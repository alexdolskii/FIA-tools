#!/usr/bin/env python

if __name__ == '__main__':
    from runtime_worker import launch_direct
    raise SystemExit(launch_direct('generate_foci_mask'))

import hashlib
import json
import logging
import os
from datetime import datetime, timedelta
from pathlib import Path

import fiji_config
import image_exclusions as exclusions
import imagej
import plate_design as plate
import spatial_calibration as spatial
from assay_layout import ASSAY_DIR, MARKERS_DIR
from interactive_input import (
    CANCELLATION_EXCEPTIONS,
)
from scyjava import jimport
from table_workflow import TableJournal, TableProgress
from validate_folders import validate_input_file


class ImageJInitializationError(Exception):
    """
    Exception raised for unsuccessful initialization of ImageJ.
    """
    pass


def initialize_imagej():
    """
    Initialize ImageJ in headless mode.

    Returns:
        ij (imagej.ImageJ): The initialized ImageJ instance.
    """
    # Attempt to initialize ImageJ headless mode
    print("Initializing ImageJ...")
    try:
        ij = imagej.init(fiji_config.FIJI_ENDPOINT, mode='headless')
    except Exception as e:
        raise ImageJInitializationError(
            f"Failed to initialize ImageJ: {e}")
    print(f"ImageJ initialization completed. Version: {ij.getVersion()}")
    return ij


def validate_folders(input_json_path: str) -> dict:
    valid_folders = validate_input_file(input_json_path)
    # checking 'markers' and the latest
    # 'Nuclei_StarDist_mask_processed_<timestamp>' subfolder
    result = {}
    for folder in valid_folders:
        # Set up logging
        file_handler = logging.FileHandler(os.path.join(folder,
                                                        '3_val_log.log'),
                                           mode='w')
        file_handler.setLevel(logging.WARNING)
        file_handler.setFormatter(
            logging.Formatter('%(asctime)s '
                              '- %(levelname)s '
                              '- %(message)s'))
        logging.getLogger('').addHandler(file_handler)

        result[folder] = {}
        fia_assay_folder = os.path.join(folder, ASSAY_DIR)
        if not os.path.exists(fia_assay_folder):
            logging.error(f"Subfolder 'fia_assay' "
                          f"not found in folder '{folder}'. "
                          f"Skipping this folder.")
            continue
        else:
            result[folder]["fia_assay_folder"] = fia_assay_folder

        # Check for 'markers' subfolder
        foci_folder = os.path.join(fia_assay_folder,
                                   MARKERS_DIR)
        if not os.path.exists(foci_folder):
            logging.error(f"Subfolder 'markers' not found "
                          f"in folder '{fia_assay_folder}'. "
                          f"Skipping this folder.")
        else:
            result[folder]["foci_folder"] = foci_folder

        # Look for the latest 'Nuclei_StarDist_mask_processed_<timestamp>'
        processed_folders = []
        for name in os.listdir(fia_assay_folder):
            if name.startswith('Nuclei_StarDist_mask_processed_'):
                timestamp_str = name.replace('Nuclei_StarDist_mask_processed_',
                                             '')
                timestamp = datetime.strptime(timestamp_str,
                                              '%Y%m%d_%H%M%S')
                processed_folders.append((timestamp,
                                          os.path.join(fia_assay_folder,
                                                       name)))

        if len(processed_folders) == 0:
            logging.error(f"No folders found "
                          f"starting with 'Nuclei_StarDist_mask_processed_' "
                          f"in '{fia_assay_folder}'. Skipping.")
        else:
            # Select the latest folder
            latest_processed_folder = max(processed_folders,
                                          key=lambda x: x[0])[1]
            print(f"Found the latest folder "
                  f"'Nuclei_StarDist_mask_processed_': "
                  f"{latest_processed_folder}")
            result[folder]["nuclei_folder"] = latest_processed_folder

        # Check for files in 'markers'
        if "foci_folder" in result[folder]:
            marker_root = Path(result[folder]["foci_folder"])
            foci_files = [str(path.relative_to(marker_root))
                          for channel in sorted(marker_root.iterdir())
                          if not channel.name.startswith('.') and channel.is_dir()
                          for path in sorted(channel.iterdir())
                          if not path.name.startswith('.') and path.is_file()
                          and path.suffix.lower() in ('.tif', '.tiff')]
            if len(foci_files) == 0:
                logging.error("No TIFF files found in marker-channel subfolders of '%s'.", marker_root)
            else:
                result[folder]["foci_files"] = foci_files

        # Check for files in the latest
        # 'Nuclei_StarDist_mask_processed_<timestamp>'
        if "nuclei_folder" in result[folder]:
            nuclei_files = [f for f in
                            os.listdir(result[folder]["nuclei_folder"])
                            if not f.startswith('.')
                            and f.lower().endswith('.tif')]
            if len(nuclei_files) == 0:
                logging.error(f"No '.tif' files found in folder "
                              f"'{result[folder]['nuclei_folder']}'.")
            else:
                result[folder]["nuclei_files"] = nuclei_files

        # Print information about found files
        if "foci_folder" in result[folder]:
            foci_files = result[folder].get("foci_files", [])
            print(f"\n--- File information in folder "
                  f"'{fia_assay_folder}' ---")
            print(f"Number of files in 'markers': {len(foci_files)}. "
                  f"Data types: "
                  f"{set(os.path.splitext(f)[-1] for f in foci_files)}")

        if "nuclei_folder" in result[folder]:
            nuclei_files = result[folder].get("nuclei_files", [])
            print(f"Number of files in "
                  f"'Nuclei_StarDist_mask_processed_': {len(nuclei_files)}. "
                  f"Data types: "
                  f"{set(os.path.splitext(f)[-1] for f in nuclei_files)}")
    return result


def parse_metadata_file(metadata_path: str) -> dict:
    """
    Reads 'image_metadata.txt' and returns a dictionary
    keyed by the base image name (e.g., "image_1") with
    a dictionary of calibration info.
    """
    return {key: {'pixel_width': row.get('Pixel Width'),
                  'pixel_height': row.get('Pixel Height'),
                  'pixel_depth': row.get('Pixel Depth'), 'unit': row.get('Unit')}
            for key, row in spatial.read_text_metadata(metadata_path).items()}


def find_metadata_for_file(filename: str, metadata_dict: dict) -> dict:
    """
    Attempt to find the calibration data in 'metadata_dict'
    for a given filename (e.g. 'image_1_nuclei_projection.tif').
    """
    stem = os.path.splitext(filename)[0]
    for suffix in ('_nuclei_projection_StarDist_processed_processed',
                   '_nuclei_projection_StarDist_processed', '_nuclei_projection', '_foci_projection'):
        if stem.endswith(suffix):
            stem = stem[:-len(suffix)]
            break
    return metadata_dict.get(stem)


def filter_foci(folder: dict,
                chosen_subfolder: str,
                foci_threshold: int, design=None, logger=None, progress=None) -> None:
    """
    Filters machine-learning results for Foci
    images in one specific subfolder.

    Args:
        folder: dictionary containing at least:
                - 'fia_assay_folder'
                - 'foci_folder'
        chosen_subfolder: name of the subfolder to analyze
        (e.g. "Foci_1_Channel_1")
        foci_threshold: threshold value for foci analysis
    """
    # Extract the relevant paths
    logger = logger or logging.getLogger(__name__)
    foci_folder = folder['foci_folder']
    fia_assay_folder = folder['fia_assay_folder']

    if chosen_subfolder.startswith('.'):
        logging.warning(f"Skipping hidden foci folder: {chosen_subfolder}")
        return

    # Build the path to the chosen subfolder
    subfolder_path = os.path.join(foci_folder, chosen_subfolder)

    # If the chosen subfolder does not exist in this folder, skip
    if not os.path.isdir(subfolder_path):
        if design:
            raise ValueError(f'Missing prepared Foci marker {chosen_subfolder}; run select_channels first')
        print(f"  - Subfolder '{chosen_subfolder}' "
              f"not found in {foci_folder}. Skipping.\n")
        return

    # Collect TIF/TIFF files within the chosen subfolder
    foci_files = plate.preparation_files(subfolder_path) if design else [
        f for f in os.listdir(subfolder_path) if not f.startswith('.') and f.lower().endswith(('.tif', '.tiff'))]
    if design:
        foci_files = [f for f in foci_files if design.assigned(plate.well_from_name(f), 'Foci', chosen_subfolder)]
    excluded = exclusions.load_exclusions(fia_assay_folder)
    omitted = [name for name in foci_files if exclusions.image_key(name) in excluded]
    foci_files = [name for name in foci_files if exclusions.image_key(name) not in excluded]
    if omitted:
        print(f'  - Processing exclusions: {len(omitted)} image(s). See logs/2_excluded_images.log')
    if not foci_files:
        print(f"  - No TIF/TIFF files found in "
              f"{subfolder_path}. Nothing to do.\n")
        return

    # Initialize ImageJ
    ij = initialize_imagej()  # noqa: F841

    # Import Java classes
    IJ = jimport('ij.IJ')
    WindowManager = jimport('ij.WindowManager')

    # Create (or reuse) a "Foci_Masks" folder in the assay folder
    foci_masks_base = os.path.join(fia_assay_folder, "Foci_Masks")
    os.makedirs(foci_masks_base, exist_ok=True)

    # Create a timestamped subfolder for the chosen subfolder
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    result_subfolder_name = f"{chosen_subfolder}_{timestamp}"
    foci_mask_folder = os.path.join(foci_masks_base,
                                    result_subfolder_name)
    stamp = datetime.now()
    while Path(foci_mask_folder).exists():
        stamp += timedelta(seconds=1)
        foci_mask_folder = str(Path(foci_masks_base) / f"{chosen_subfolder}_{stamp:%Y%m%d_%H%M%S}")
    os.makedirs(foci_mask_folder)
    state = {'Schema_version': 1, 'Status': 'RUNNING', 'Marker_ID': chosen_subfolder,
             'Method': 'Foci', 'Threshold': foci_threshold, 'Masks': {},
             'Inputs': {f: hashlib.sha256((Path(subfolder_path) / f).read_bytes()).hexdigest() for f in foci_files}}
    state_path = Path(foci_mask_folder) / 'foci_run.json'
    state_path.write_text(json.dumps(state, indent=2) + '\n', encoding='utf-8')
    if design:
        plate.save_snapshot(foci_mask_folder, design)
    exclusions.write_snapshot(foci_mask_folder, excluded)

    logger.info('MASK_RUN_STARTED | marker=%s | threshold=%s | images=%s | output=%s',
                chosen_subfolder, foci_threshold, len(foci_files), foci_mask_folder)
    if progress:
        progress.phase('Thresholding Foci', len(foci_files))

    try:
        # Process each TIF file
        for filename in foci_files:
            file_path = os.path.join(subfolder_path, filename)
            logger.info('IMAGE_STARTED | %s', filename)
            IJ.run("Close All")  # Close images before starting

            # Open image
            imp = IJ.openImage(file_path)
            if imp is None:
                logger.error(f"Failed to open image: {file_path}")
                continue

            # Convert image to 8-bit
            IJ.run(imp, "8-bit", "")

            # Prefer the exact projection snapshot; never invent a microscopy scale.
            shape = (int(imp.getHeight()), int(imp.getWidth()))
            calibration = spatial.projection_calibration(file_path, shape)
            if not spatial.calibrated(calibration):
                logger.warning("No valid physical calibration for '%s'; using pixel units.", filename)
            spatial.imagej_calibration(imp, calibration, jimport)

            # Threshold & convert to mask
            IJ.setThreshold(imp, foci_threshold, 255)
            IJ.run(imp, "Convert to Mask", "")
            IJ.run(imp, "Watershed", "")

            # Analyze particles
            IJ.run(imp, "Analyze Particles...", "size=0-Infinity pixel show=Masks")

            # Retrieve the new mask image
            mask_title = 'Mask of ' + filename
            imp_mask = WindowManager.getImage(mask_title)
            if imp_mask is None:
                imp_mask = WindowManager.getCurrentImage()
                if imp_mask is None:
                    logger.error(f"Failed to get mask for image: {file_path}")
                    imp.close()
                    continue

            # Save processed image
            output_path = os.path.join(foci_mask_folder, f"processed_{filename}")
            spatial.imagej_calibration(imp_mask, calibration, jimport)
            IJ.saveAs(imp_mask, "Tiff", output_path)
            spatial.save_snapshot(output_path, calibration, shape)
            state['Masks'][Path(output_path).name] = hashlib.sha256(Path(output_path).read_bytes()).hexdigest()
            state_path.write_text(json.dumps(state, indent=2) + '\n', encoding='utf-8')

            # Close images
            imp.close()
            imp_mask.close()
            if progress:
                progress.advance(filename)

    except BaseException as error:
        state.update(Status='FAILED', Error=str(error))
        state_path.write_text(json.dumps(state, indent=2) + '\n', encoding='utf-8')
        raise
    state['Status'] = 'COMPLETE' if len(state['Masks']) == len(foci_files) else 'INCOMPLETE'
    state_path.write_text(json.dumps(state, indent=2) + '\n', encoding='utf-8')
    print(f"  - {state['Status']}: {foci_mask_folder}\n")
    return Path(foci_mask_folder)


def main_filter_foci(input_json_path: str, foci_threshold: int, markers=None):
    audit = TableJournal('3_foci_mask.log', input_json_path)
    failures = 0
    try:
        if not isinstance(foci_threshold, int) or not 0 <= foci_threshold <= 255:
            raise ValueError('Foci threshold must be an integer between 0 and 255')
        designs = plate.preflight(input_json_path, command='generate_foci_mask')
        selected = plate.selected_markers(designs.values(), 'Foci', markers)
        if not selected:
            print('No Foci assignments; nothing to process.')
        with TableProgress() as progress:
            for root, design in designs.items():
                applicable = [m for m in selected if any(design.assigned(w, 'Foci', m['id']) for w in design.wells)]
                if not applicable:
                    continue
                audit.register(root)
                info = {'fia_assay_folder': str(root / ASSAY_DIR), 'foci_folder': str(root / ASSAY_DIR / MARKERS_DIR)}
                for marker in applicable:
                    try:
                        with audit.stage(root, marker['name'], progress) as logger:
                            progress.group(f"Foci | {root.name} | {marker['name']} | threshold={foci_threshold}")
                            output = filter_foci(info, marker['id'], foci_threshold, design, logger, progress)
                            if output is not None:
                                complete = json.loads((output / 'foci_run.json').read_text())['Status'] == 'COMPLETE'
                                audit.result(root, output, 'SUCCESS' if complete else 'INCOMPLETE', output)
                                failures += not complete
                    except CANCELLATION_EXCEPTIONS:
                        raise
                    except Exception as error:  # noqa: BLE001 - persist batch failure and continue safely
                        failures += 1
                        audit.incomplete(root, str(error))
        audit.status = 'INCOMPLETE' if failures else 'COMPLETE'
        return 1 if failures else 0
    except CANCELLATION_EXCEPTIONS:
        audit.status = 'CANCELLED'
        return 130
    except Exception as error:  # noqa: BLE001 - persist batch failure and continue safely
        print(f'Foci masking failed: {error}')
        audit.status = 'FAILED'
        return 1
    finally:
        audit.finish()
