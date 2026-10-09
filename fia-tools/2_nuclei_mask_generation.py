#!/usr/bin/env python

if __name__ == '__main__':
    from runtime_worker import launch_direct
    raise SystemExit(launch_direct('generate_nuclei_mask'))

import hashlib
import json
import os
import time
from datetime import datetime, timedelta
from pathlib import Path

import fiji_config
import image_exclusions as exclusions
import imagej
import numpy as np
import spatial_calibration as spatial
from assay_layout import ASSAY_DIR
from csbdeep.utils import normalize
from interactive_input import ask_choice, ask_yes_no, cancelable
from nuclei_morphology import NucleiMorphologyExport
from nuclei_run_log import NucleiLogSession, NucleiRunLog, nuclei_log_session
from run_resources import temporary_path
from scyjava import jimport
from skimage.io import imread, imsave
from stardist_worker import ImageInferenceError, checked_normalize
from stardist_worker import IsolatedStarDist as StarDist2D
from validate_folders import validate_input_file

STARDIST_SETTINGS = {
    "model": "2D_versatile_fluo",
    "nms_thresh": 0.9,
    "prob_thresh": 0.7,
    "normalization": "csbdeep.utils.normalize (defaults)",
}
STARDIST_METADATA = "stardist_run.json"


def create_output_folder(parent, prefix):
    """Reserve a new timestamped folder without overwriting an earlier run."""
    timestamp = datetime.now()
    while True:
        folder = Path(parent) / f"{prefix}{timestamp:%Y%m%d_%H%M%S}"
        try:
            folder.mkdir(parents=True, exist_ok=False)
            return str(folder)
        except FileExistsError:
            timestamp += timedelta(seconds=1)


def write_run_metadata(folder, filename, metadata):
    """Replace metadata atomically; interrupted runs retain an incomplete status."""
    path = Path(folder) / filename
    temporary = temporary_path(path, path.with_name("." + filename + ".tmp"))
    temporary.write_text(json.dumps(metadata, indent=2) + "\n",
                         encoding="utf-8")
    temporary.replace(path)


def file_digest(path):
    """Identify file contents even when a source keeps the same filename."""
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def nuclei_source_files(nuclei_folder):
    """Use the same visible .tif inputs as the StarDist processing loop."""
    return [f for f in os.listdir(nuclei_folder)
            if not f.startswith('.') and f.endswith('.tif')
            and (Path(nuclei_folder) / f).is_file()]


def source_fingerprints(nuclei_folder, progress=None):
    files = nuclei_source_files(nuclei_folder)
    fingerprints = {}
    for index, name in enumerate(files, 1):
        if progress:
            progress.phase(f'Source checksums {index}/{len(files)}')
        fingerprints[name] = file_digest(Path(nuclei_folder) / name)
        if progress and progress.logger:
            progress.logger.info('SOURCE_CHECKSUM | file=%s | sha256=%s',
                                 Path(nuclei_folder) / name, fingerprints[name])
    return fingerprints


def inspect_stardist_folder(folder, nuclei_folder, sources, progress=None):
    """Check completeness, provenance and readable masks before offering reuse."""
    candidate = {"path": str(folder), "count": 0, "legacy": False,
                 "reason": "", "usable": False}
    try:
        masks = {p.name for p in Path(folder).iterdir()
                 if not p.name.startswith('.') and p.is_file()
                 and p.suffix.lower() in (".tif", ".tiff")}
        candidate["count"] = len(masks)
        excluded = exclusions.load_exclusions(folder)
        expected = {f"{Path(name).stem}_StarDist_processed.tif": name
                    for name in sources if exclusions.image_key(name) not in excluded}
        if not expected:
            raise ValueError("No visible .tif source images found")
        active_masks = {name for name in masks if exclusions.image_key(name) not in excluded}
        missing = expected.keys() - active_masks
        extra = active_masks - expected.keys()
        if missing or extra:
            raise ValueError(f"Mask set mismatch: {len(missing)} missing, "
                             f"{len(extra)} unexpected")

        metadata_path = Path(folder) / STARDIST_METADATA
        if metadata_path.exists():
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            if not isinstance(metadata, dict):
                raise ValueError("Invalid StarDist run metadata")
            if (metadata.get("schema_version") != 1
                or metadata.get("status") not in ("complete", "complete_with_exclusions")):
                raise ValueError("StarDist run is incomplete or unrecognized")
            if metadata.get("settings") != STARDIST_SETTINGS:
                raise ValueError("StarDist settings do not match this program")
            if metadata.get("sources") != sources:
                raise ValueError("Source images have changed since this run")
            recorded_masks = metadata.get("masks", {})
            if not isinstance(recorded_masks, dict) or set(recorded_masks) != masks:
                raise ValueError("Recorded mask set does not match the folder")
            for index, name in enumerate(masks, 1):
                if progress:
                    progress.phase(f'Mask checksums {index}/{len(masks)}')
                if file_digest(Path(folder) / name) != recorded_masks[name]:
                    raise ValueError(f"Saved mask has changed: {name}")
        else:
            candidate["legacy"] = True

        for mask_name, source_name in expected.items():
            if progress:
                progress.begin_image(Path(folder) / mask_name)
                progress.phase('Checking dimensions and bit depth')
            source = imread(str(Path(nuclei_folder) / source_name))
            mask = imread(str(Path(folder) / mask_name))
            if progress and progress.logger:
                progress.logger.info(
                    'VALIDATE_IMAGE | source=%s | shape=%s | dtype=%s | '
                    'mask=%s | shape=%s | dtype=%s', source_name, source.shape,
                    source.dtype, mask_name, mask.shape, mask.dtype)
            if source.ndim != 2 or source.dtype != np.uint8:
                raise ValueError(f"Source is not 2D uint8: {source_name}")
            if (mask.ndim != 2 or mask.dtype != np.uint16
                    or mask.shape != source.shape):
                raise ValueError(f"Invalid mask type or dimensions: {mask_name}")
            if progress:
                progress.finish_image()
        candidate["usable"] = True
    except Exception as error:
        candidate["reason"] = str(error)
        if progress:
            progress.finish_image(success=False)
    if progress and progress.logger:
        progress.logger.info('REUSE_CHECK | folder=%s | usable=%s | legacy=%s | reason=%s',
                             folder, candidate['usable'], candidate['legacy'], candidate['reason'])
    return candidate


@nuclei_log_session
def discover_stardist_folders(nuclei_folder):
    """List timestamped runs, newest first, ignoring hidden entries."""
    prefix = "Nuclei_StarDist_mask_processed_"
    folders = []
    for folder in Path(nuclei_folder).parent.iterdir():
        if not folder.is_dir() or not folder.name.startswith(prefix):
            continue
        try:
            timestamp = datetime.strptime(folder.name[len(prefix):],
                                          "%Y%m%d_%H%M%S")
        except ValueError:
            continue
        folders.append((timestamp, folder))
    folders.sort(key=lambda item: item[0], reverse=True)
    if not folders:
        return []
    with NucleiRunLog(Path(nuclei_folder).parent / '2_log.log',
                      'Reuse validation', len(nuclei_source_files(nuclei_folder)), stage='reuse_check') as run:
        run.logger.info('INPUT | folder=%s | candidate_runs=%s', nuclei_folder, len(folders))
        sources = source_fingerprints(nuclei_folder, run.progress)
        candidates = []
        for index, (_, folder) in enumerate(folders, 1):
            run.progress.group(f'Run {index}/{len(folders)} | Checking masks', len(sources))
            candidates.append(inspect_stardist_folder(folder, nuclei_folder, sources, run.progress))
        run.finish('COMPLETE', show_counts=False, candidate_runs=len(candidates),
                   reusable=sum(item['usable'] for item in candidates))
    return candidates


@nuclei_log_session
def select_stardist_sources(nuclei_folders):
    """Collect all reuse decisions before starting any new segmentation."""
    selected = {}
    reuse_remaining = False
    legacy_remaining = False
    for nuclei_folder in nuclei_folders:
        print(f"\nChecking existing StarDist results for: {nuclei_folder}")
        candidates = discover_stardist_folders(nuclei_folder)
        with NucleiRunLog(Path(nuclei_folder).parent / '2_log.log',
                          'Mask selection', quiet=True, stage='selection') as run:
            run.logger.info('INPUT | nuclei_folder=%s', nuclei_folder)
            usable = [item for item in candidates if item["usable"]]
            for item in candidates:
                if not item["usable"]:
                    print(f"Unavailable: {item['path']} "
                          f"({item['count']} masks; {item['reason']})")
            if not usable:
                print("No reusable StarDist results. A new run is required.")
                selected[nuclei_folder] = None
                run.logger.info("STARDIST_NEW | reason=no reusable results")
                continue
            for index, item in enumerate(usable, 1):
                note = "legacy; confirmation required" if item["legacy"] else "verified"
                print(f"[{index}] {item['path']} ({item['count']} masks; {note})")

            while True:
                if reuse_remaining:
                    answer = "1"
                else:
                    answer = ask_choice(
                        "Reuse folder [number; Enter = 1], [n] run StarDist again, "
                        "[a] reuse the latest valid run for this and remaining "
                        "inputs, or [q] cancel: ",
                        {**{str(i): str(i) for i in range(1, len(usable) + 1)},
                         "n": "n", "a": "a"}, default="1",
                        error="Please choose a listed folder or n, a, q.")
                if answer == "n":
                    selected[nuclei_folder] = None
                    run.logger.info("STARDIST_NEW | reason=user requested new segmentation")
                    break
                if answer == "a":
                    reuse_remaining = True
                    answer = "1"
                chosen = usable[int(answer) - 1]
                if chosen["legacy"] and not legacy_remaining:
                    print("This older run has no provenance metadata. File names, "
                          "dimensions and readability passed validation, but "
                          "unchanged source content and settings cannot be verified.")
                    scope = "all reused legacy runs" if reuse_remaining else "this run"
                    confirmed = ask_yes_no(
                        f"Confirm unchanged source images and StarDist settings "
                        f"for {scope} [y/N; Enter = no; q = cancel]: ", default=False)
                    run.logger.info("LEGACY_REUSE_CONFIRMATION | source=%s | confirmed=%s",
                                    chosen["path"], confirmed)
                    if not confirmed:
                        reuse_remaining = False
                        print("Legacy reuse was not confirmed. Choose another option.")
                        continue
                    legacy_remaining = reuse_remaining
                selected[nuclei_folder] = chosen["path"]
                run.logger.info("STARDIST_REUSED | source=%s | masks=%s | legacy=%s",
                                chosen["path"], chosen["count"], chosen["legacy"])
                print(f"StarDist: reused ({chosen['count']} masks). Source: {chosen['path']}")
                break
    return selected


class ImageJInitializationError(Exception):
    """
    Exception raised for unsuccessful initialization of ImageJ.
    """
    pass


def initialize_imagej(progress=None):
    """
    Initialize ImageJ in headless mode.

    Returns:
        ij (imagej.ImageJ): The initialized ImageJ instance.
    """
    # Attempt to initialize ImageJ headless mode
    message = progress.message if progress is not None else print
    message("Initializing ImageJ...")
    try:
        ij = imagej.init(fiji_config.FIJI_ENDPOINT, mode='headless')
    except Exception as e:
        raise ImageJInitializationError(
            f"Failed to initialize ImageJ: {e}")
    message(f"ImageJ initialization completed. Version: {ij.getVersion()}")
    return ij


@nuclei_log_session
def validate_folders(input_json_path: str) -> list:
    valid_folders = validate_input_file(input_json_path)
    nuclei_folders = []
    for folder in valid_folders:
        if not Path(folder).is_dir():
            raise FileNotFoundError(f'Input folder does not exist: {folder}')
        with NucleiRunLog(Path(folder) / ASSAY_DIR / '2_log.log',
                          'Input validation', quiet=True, stage='validation') as run:
            run.logger.info('INPUT | json=%s | folder=%s', input_json_path, folder)
            nuclei_folder = os.path.join(folder, ASSAY_DIR, 'Nuclei')
            if os.path.exists(nuclei_folder):
                files = [f for f in os.listdir(nuclei_folder) if not f.startswith('.')]
                file_formats = set(os.path.splitext(f)[1] for f in files)
                count = len(nuclei_source_files(nuclei_folder))
                message = (f"Nuclei folder found: {nuclei_folder}, "
                           f"File types: {', '.join(sorted(file_formats))}; images: {count}")
                print(message)
                run.logger.info(message)
                nuclei_folders.append(nuclei_folder)
                run.finish('COMPLETE', show_counts=False, images=count)
            else:
                run.logger.error("Nuclei folder not found in '%s/fia_assay'.", folder)
                run.finish('INCOMPLETE', show_counts=False)
    return nuclei_folders


@nuclei_log_session
def find_nuclei(nuclei_folders: list) -> list:
    """Generate masks, isolating inference crashes and recording durable exclusions."""
    model = None
    processed_folders = []
    try:
        for folder_index, nuclei_folder in enumerate(nuclei_folders, 1):
            image_files = nuclei_source_files(nuclei_folder)
            assay = Path(nuclei_folder).parent
            excluded = exclusions.load_exclusions(nuclei_folder)
            with NucleiRunLog(assay / '2_log.log',
                              f'Folder {folder_index}/{len(nuclei_folders)} | StarDist',
                              len(image_files), stage='stardist') as run:
                output_folder = create_output_folder(assay, "Nuclei_StarDist_mask_processed_")
                processed_folders.append(output_folder)
                progress, logger = run.progress, run.logger
                progress.message(f'Input: {nuclei_folder}')
                progress.message(f'Output: {output_folder}')
                logger.info('INPUT | folder=%s | output=%s', nuclei_folder, output_folder)
                logger.info('PARAMETERS | %s', json.dumps(STARDIST_SETTINGS, sort_keys=True))
                run_metadata = {
                    "schema_version": 1, "status": "running",
                    "source_folder": str(Path(nuclei_folder).resolve()),
                    "settings": dict(STARDIST_SETTINGS),
                    "sources": source_fingerprints(nuclei_folder, progress),
                    "masks": {}, "excluded_files": [],
                }
                write_run_metadata(output_folder, STARDIST_METADATA, run_metadata)
                exclusions.write_snapshot(output_folder, excluded)
                run.saved(Path(output_folder) / STARDIST_METADATA)
                for image_file in image_files:
                    image_path = os.path.join(nuclei_folder, image_file)
                    progress.begin_image(image_path)
                    if exclusions.image_key(image_file) in excluded:
                        logger.info('IMAGE_EXCLUDED | file=%s | previous=%s', image_path,
                                       excluded[exclusions.image_key(image_file)])
                        run_metadata['excluded_files'].append(image_file)
                        progress.finish_image(skipped=True)
                        continue
                    reason = 'INVALID_STARDIST_INPUT'
                    try:
                        image = imread(image_path)
                        logger.info('INPUT_IMAGE | file=%s | shape=%s | dtype=%s | bits=%s',
                                    image_file, image.shape, image.dtype, image.dtype.itemsize * 8)
                        progress.phase('Normalizing')
                        image = checked_normalize(image, normalize)
                    except (ValueError, OSError, ImageInferenceError) as error:
                        failure = error
                    else:
                        # Initialization failures stop the run, rather than excluding healthy images.
                        if model is None:
                            progress.phase('Loading StarDist model')
                            progress.message('Loading StarDist model...')
                            model = StarDist2D.from_pretrained(STARDIST_SETTINGS['model'])
                            logger.info('MODEL_READY | model=%s', STARDIST_SETTINGS['model'])
                            progress.message('StarDist model ready.')
                        reason = 'STARDIST_INFERENCE_FAILED'
                        try:
                            progress.phase('Predicting nuclei')
                            labels, _ = model.predict_instances(
                                image, nms_thresh=STARDIST_SETTINGS['nms_thresh'],
                                prob_thresh=STARDIST_SETTINGS['prob_thresh'])
                            failure = None
                        except ImageInferenceError as error:
                            failure = error
                    if failure is not None:
                        excluded = exclusions.exclude_image(assay, image_path, reason, failure)
                        exclusions.write_snapshot(output_folder, excluded)
                        run_metadata['excluded_files'].append(image_file)
                        write_run_metadata(output_folder, STARDIST_METADATA, run_metadata)
                        logger.error('IMAGE_EXCLUDED | file=%s | reason=%s | detail=%s', image_path, reason, failure,
                                     extra={'file_only': True})
                        progress.message(f'Excluded: {image_file} | {reason}. See logs/2_excluded_images.log')
                        progress.finish_image(skipped=True)
                        continue
                    progress.phase('Saving mask and calibration')
                    base_name, ext = os.path.splitext(image_file)
                    new_file_name = f"{base_name}_StarDist_processed{ext}"
                    output_path = os.path.join(output_folder, new_file_name)
                    imsave(output_path, labels.astype(np.uint16))
                    logger.info('OUTPUT_IMAGE | file=%s | shape=%s | dtype=uint16 | bits=16',
                                new_file_name, labels.shape)
                    run.saved(output_path)
                    calibration = spatial.projection_calibration(image_path, labels.shape, allow_bioformats=False)
                    logger.info('CALIBRATION | file=%s | %s', image_file, calibration)
                    if calibration is not None:
                        spatial.save_snapshot(output_path, calibration, labels.shape)
                        run.saved(Path(output_folder) / spatial.MANIFEST)
                    run_metadata['masks'][new_file_name] = file_digest(output_path)
                    write_run_metadata(output_folder, STARDIST_METADATA, run_metadata)
                    progress.finish_image()
                complete = (bool(image_files) and
                            len(run_metadata['masks']) + len(run_metadata['excluded_files']) == len(image_files)
                            and run_metadata['sources'] == source_fingerprints(nuclei_folder, progress))
                status = ('complete_with_exclusions' if run_metadata['excluded_files'] else 'complete') if complete else 'incomplete'
                run_metadata['status'] = status
                exclusions.write_snapshot(output_folder, excluded)
                write_run_metadata(output_folder, STARDIST_METADATA, run_metadata)
                run.saved(Path(output_folder) / STARDIST_METADATA)
                run.finish(status.upper(), masks=len(run_metadata['masks']), excluded=len(run_metadata['excluded_files']))
    finally:
        if model is not None:
            model.close()
    return processed_folders


@nuclei_log_session
def process_nuclei(valid_folders: list, particle_size: int) -> bool:
    """Run the existing ImageJ/morphology pipeline, reporting stage-level progress."""
    ij = None
    all_complete = bool(valid_folders)
    for folder_index, input_folder in enumerate(valid_folders, 1):
        excluded = exclusions.load_exclusions(input_folder)
        entries = [name for name in os.listdir(input_folder)
                   if exclusions.image_key(name) not in excluded]
        image_files = [name for name in entries
                       if not name.startswith('.') and name.lower().endswith(('.tif', '.tiff'))]
        with NucleiRunLog(Path(input_folder).parent / '2_log.log',
                          f'Folder {folder_index}/{len(valid_folders)} | ImageJ',
                          len(image_files), stage='imagej') as run:
            processed_folder = create_output_folder(
                os.path.dirname(input_folder), "Final_Nuclei_Mask_")
            progress, logger = run.progress, run.logger
            progress.message(f'Input: {input_folder}')
            progress.message(f'Output: {processed_folder}')
            logger.info('INPUT | folder=%s | output=%s', input_folder, processed_folder)
            logger.info('PARAMETERS | particle_size_pixels_squared=%s | threshold=1..255 | '
                        'conversion=8-bit | watershed=True | Fiji=%s',
                        particle_size, fiji_config.FIJI_ENDPOINT)
            exclusions.write_snapshot(processed_folder, excluded)
            logger.info('PROCESSING_EXCLUSIONS | count=%s | manifest=%s', len(excluded), exclusions.MANIFEST)
            if not image_files:
                run.finish('NO_ELIGIBLE_IMAGES', excluded=len(excluded))
                continue
            if ij is None:
                progress.phase('Initializing ImageJ')
                ij = initialize_imagej(progress=progress)
                IJ = jimport('ij.IJ')
                WindowManager = jimport('ij.WindowManager')
            logger.info('IMAGEJ_READY | version=%s', IJ.getVersion())
            morphology = NucleiMorphologyExport(
                processed_folder, input_folder, particle_size, IJ.getVersion())
            run_metadata = {
                "schema_version": 1,
                "status": "running",
                "stardist_folder": str(Path(input_folder).resolve()),
                "particle_size_pixels_squared": particle_size,
                "morphology_status": "running",
                "processed_files": [],
                "skipped_files": [],
                "excluded_images": list(excluded),
            }
            write_run_metadata(processed_folder, "nuclei_run.json", run_metadata)
            run.saved(Path(processed_folder) / 'nuclei_run.json')
            ignored = 0
            for filename in entries:
                if filename.startswith('.') or filename in (
                        STARDIST_METADATA, spatial.MANIFEST, exclusions.MANIFEST, exclusions.TABLE, '2_log.log'):
                    logger.info('IGNORED | file=%s | hidden or auxiliary file', filename)
                    ignored += 1
                    continue
                if not filename.lower().endswith(('.tif', '.tiff')):
                    logger.error("Skipping '%s' (unsupported format).", filename)
                    ignored += 1
                    continue
                file_path = os.path.join(input_folder, filename)
                progress.begin_image(file_path)
                IJ.run("Close All")
                imp = IJ.openImage(file_path)
                if imp is None:
                    logger.warning('Failed to open image: %s. Check Bio-Formats or file integrity.', file_path)
                    run_metadata["skipped_files"].append(filename)
                    morphology.record_failure(filename, "Failed to open the StarDist mask.")
                    progress.finish_image(skipped=True)
                    continue
                try:
                    logger.info('INPUT_IMAGE | file=%s | width=%s | height=%s | bits=%s',
                                filename, imp.getWidth(), imp.getHeight(), imp.getBitDepth())
                    progress.phase('Converting and thresholding')
                    IJ.run(imp, "8-bit", "")
                    IJ.setThreshold(imp, 1, 255)
                    IJ.run(imp, "Convert to Mask", "")
                    progress.phase('Watershed')
                    IJ.run(imp, "Watershed", "")
                    progress.phase('Filtering nuclei by area')
                    IJ.run(imp, "Analyze Particles...",
                           f"size={particle_size}-Infinity pixel show=Masks")
                    mask_title = 'Mask of ' + filename
                    imp_mask = WindowManager.getImage(mask_title)
                    if imp_mask is None:
                        imp_mask = WindowManager.getCurrentImage()
                    if imp_mask is None:
                        logger.error('Failed to get mask for image: %s', file_path)
                        run_metadata["skipped_files"].append(filename)
                        morphology.record_failure(filename, "ImageJ did not return a final mask.")
                        progress.finish_image(skipped=True)
                        continue
                    try:
                        progress.phase('Saving final mask')
                        base_name = os.path.splitext(filename)[0]
                        output_path = os.path.join(processed_folder, f"{base_name}_processed.tif")
                        IJ.saveAs(imp_mask, "Tiff", output_path)
                        run_metadata["processed_files"].append(filename)
                        logger.info('OUTPUT_IMAGE | file=%s | width=%s | height=%s | bits=%s',
                                    output_path, imp_mask.getWidth(), imp_mask.getHeight(), imp_mask.getBitDepth())
                        run.saved(output_path)
                        success = True
                        progress.phase('Measuring morphology and saving QC')
                        try:
                            morphology.add_image(imp_mask, filename, Path(output_path).name)
                            # add_image can update calibration of the final TIFF.
                            run.saved(output_path)
                            qc_stem = Path(output_path).stem
                            for suffix in ('_ids.tif', '_ids.png'):
                                run.saved(Path(processed_folder) / 'Morphology_QC' / (qc_stem + suffix))
                            logger.info('MORPHOLOGY | file=%s | status=complete', filename)
                        except Exception as error:
                            success = False
                            morphology.record_failure(filename, error, Path(output_path).name)
                            logger.exception("Morphology export failed for '%s': %s", filename, error)
                        progress.finish_image(success=success)
                    finally:
                        imp_mask.close()
                finally:
                    imp.close()

            IJ.run("Close All")
            run_metadata["status"] = (
                "complete" if run_metadata["processed_files"]
                and not run_metadata["skipped_files"] else "incomplete")
            progress.phase('Saving morphology tables')
            run_metadata["morphology_status"] = morphology.save()
            for name in ('Nuclei_Morphology.xlsx', 'Nuclei_Morphology.csv',
                         'Nuclei_Images.csv', 'Nuclei_Run_Info.csv'):
                run.saved(Path(processed_folder) / name)
            write_run_metadata(processed_folder, "nuclei_run.json", run_metadata)
            run.saved(Path(processed_folder) / 'nuclei_run.json')
            complete = (run_metadata['status'] == 'complete'
                        and run_metadata['morphology_status'] == 'complete')
            all_complete = all_complete and complete
            run.finish(('COMPLETE_WITH_EXCLUSIONS' if excluded else 'COMPLETE') if complete else 'INCOMPLETE',
                       masks=run_metadata['status'], morphology=run_metadata['morphology_status'],
                       saved_masks=len(run_metadata['processed_files']), ignored=ignored,
                       minimum_area_px2=particle_size)
            progress.message(f"Morphology table: {processed_folder}/Nuclei_Morphology.xlsx; "
                             f"status: {run_metadata['morphology_status']}.")
    return all_complete


@cancelable
def main(input_json_path: str,
         particle_size: int):
    """
    Main function to analyze and process nuclei.
    """
    with NucleiLogSession(input_json_path, particle_size, require_imagej=True):
        started = time.monotonic()
        # Step 1: Reuse validated masks or analyze nuclei using StarDist.
        print("Starting Step 1: Preparing StarDist nuclei masks...")
        nuclei_folders = validate_folders(input_json_path)
        if not nuclei_folders:
            print("No valid nuclei folders found. Nothing to process.")
            return
        selected = select_stardist_sources(nuclei_folders)
        new_inputs = [folder for folder in nuclei_folders if selected[folder] is None]
        if new_inputs:
            new_outputs = find_nuclei(new_inputs)
            for nuclei_folder, output_folder in zip(new_inputs, new_outputs):
                with NucleiRunLog(Path(nuclei_folder).parent / '2_log.log',
                                  'StarDist readiness', quiet=True, stage='stardist_check') as run:
                    metadata = json.loads((Path(output_folder) / STARDIST_METADATA)
                                          .read_text(encoding="utf-8"))
                    if metadata["status"] not in ("complete", "complete_with_exclusions"):
                        raise ValueError(f"Incomplete StarDist results: {output_folder}. "
                                         "ImageJ processing was not started.")
                    selected[nuclei_folder] = output_folder
        processed_folders = []
        for folder in nuclei_folders:
            excluded = exclusions.load_exclusions(selected[folder])
            if any(exclusions.image_key(name) not in excluded for name in nuclei_source_files(selected[folder])):
                processed_folders.append(selected[folder])
            else:
                with NucleiRunLog(Path(folder).parent / '2_log.log', 'ImageJ readiness',
                                  quiet=True, stage='imagej') as run:
                    run.finish('NO_ELIGIBLE_IMAGES', excluded=len(excluded))
        if not processed_folders:
            print('No eligible images remain. See logs/2_excluded_images.log; no final masks were generated.')
            return
        print("Step 1 completed: Nuclei masks ready.")

        # Step 2: Process nuclei using ImageJ
        print("Starting Step 2: Processing nuclei with ImageJ...")
        complete = process_nuclei(processed_folders, particle_size)
        status = "INCOMPLETE; check the folder logs" if complete is False else "finished"
        print(f"Step 2: Nuclei processing {status}. Total elapsed: {time.monotonic() - started:.1f}s.")
