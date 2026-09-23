#!/usr/bin/env python

import argparse
import hashlib
import json
import logging
import os
from datetime import datetime, timedelta
from pathlib import Path

import imagej
import numpy as np
from csbdeep.utils import normalize
from scyjava import jimport
from skimage.io import imread, imsave
from stardist.models import StarDist2D
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
    temporary = path.with_name("." + filename + ".tmp")
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


def source_fingerprints(nuclei_folder):
    return {name: file_digest(Path(nuclei_folder) / name)
            for name in nuclei_source_files(nuclei_folder)}


def inspect_stardist_folder(folder, nuclei_folder, sources):
    """Check completeness, provenance and readable masks before offering reuse."""
    candidate = {"path": str(folder), "count": 0, "legacy": False,
                 "reason": "", "usable": False}
    try:
        masks = {p.name for p in Path(folder).iterdir()
                 if not p.name.startswith('.') and p.is_file()
                 and p.suffix.lower() in (".tif", ".tiff")}
        candidate["count"] = len(masks)
        expected = {f"{Path(name).stem}_StarDist_processed.tif": name
                    for name in sources}
        if not expected:
            raise ValueError("No visible .tif source images found")
        missing = expected.keys() - masks
        extra = masks - expected.keys()
        if missing or extra:
            raise ValueError(f"Mask set mismatch: {len(missing)} missing, "
                             f"{len(extra)} unexpected")

        metadata_path = Path(folder) / STARDIST_METADATA
        if metadata_path.exists():
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            if not isinstance(metadata, dict):
                raise ValueError("Invalid StarDist run metadata")
            if (metadata.get("schema_version") != 1
                    or metadata.get("status") != "complete"):
                raise ValueError("StarDist run is incomplete or unrecognized")
            if metadata.get("settings") != STARDIST_SETTINGS:
                raise ValueError("StarDist settings do not match this program")
            if metadata.get("sources") != sources:
                raise ValueError("Source images have changed since this run")
            recorded_masks = metadata.get("masks", {})
            if not isinstance(recorded_masks, dict) or set(recorded_masks) != masks:
                raise ValueError("Recorded mask set does not match the folder")
            for name in masks:
                if file_digest(Path(folder) / name) != recorded_masks[name]:
                    raise ValueError(f"Saved mask has changed: {name}")
        else:
            candidate["legacy"] = True

        for mask_name, source_name in expected.items():
            source = imread(str(Path(nuclei_folder) / source_name))
            mask = imread(str(Path(folder) / mask_name))
            if source.ndim != 2 or source.dtype != np.uint8:
                raise ValueError(f"Source is not 2D uint8: {source_name}")
            if (mask.ndim != 2 or mask.dtype != np.uint16
                    or mask.shape != source.shape):
                raise ValueError(f"Invalid mask type or dimensions: {mask_name}")
        candidate["usable"] = True
    except Exception as error:
        candidate["reason"] = str(error)
    return candidate


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
    sources = source_fingerprints(nuclei_folder)
    return [inspect_stardist_folder(folder, nuclei_folder, sources)
            for _, folder in folders]


def select_stardist_sources(nuclei_folders):
    """Collect all reuse decisions before starting any new segmentation."""
    selected = {}
    reuse_remaining = False
    legacy_remaining = False
    for nuclei_folder in nuclei_folders:
        print(f"\nChecking existing StarDist results for: {nuclei_folder}")
        candidates = discover_stardist_folders(nuclei_folder)
        usable = [item for item in candidates if item["usable"]]
        for item in candidates:
            if not item["usable"]:
                print(f"Unavailable: {item['path']} "
                      f"({item['count']} masks; {item['reason']})")
        if not usable:
            print("No reusable StarDist results. A new run is required.")
            selected[nuclei_folder] = None
            continue
        for index, item in enumerate(usable, 1):
            note = "legacy; confirmation required" if item["legacy"] else "verified"
            print(f"[{index}] {item['path']} ({item['count']} masks; {note})")

        while True:
            if reuse_remaining:
                answer = "1"
            else:
                answer = input(
                    "Reuse folder [number; Enter = 1], [n] run StarDist again, "
                    "[a] reuse the latest valid run for this and remaining "
                    "inputs, or [q] cancel: "
                ).strip().lower() or "1"
            if answer == "q":
                raise ValueError("Analysis canceled by user.")
            if answer == "n":
                selected[nuclei_folder] = None
                break
            if answer == "a":
                reuse_remaining = True
                answer = "1"
            if not answer.isdecimal() or not 1 <= int(answer) <= len(usable):
                print("Please choose a listed folder or n, a, q.")
                continue
            chosen = usable[int(answer) - 1]
            if chosen["legacy"] and not legacy_remaining:
                print("This older run has no provenance metadata. File names, "
                      "dimensions and readability passed validation, but "
                      "unchanged source content and settings cannot be verified.")
                scope = "all reused legacy runs" if reuse_remaining else "this run"
                confirmed = input(
                    f"Confirm unchanged source images and StarDist settings "
                    f"for {scope} [y/N]: "
                ).strip().lower()
                if confirmed not in ("y", "yes"):
                    reuse_remaining = False
                    print("Legacy reuse was not confirmed. Choose another option.")
                    continue
                legacy_remaining = reuse_remaining
            selected[nuclei_folder] = chosen["path"]
            print(f"Reusing StarDist masks: {chosen['path']}")
            break
    return selected


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
        ij = imagej.init('sc.fiji:fiji', mode='headless')
    except Exception as e:
        raise ImageJInitializationError(
            f"Failed to initialize ImageJ: {e}")
    print(f"ImageJ initialization completed. Version: {ij.getVersion()}")
    return ij


def validate_folders(input_json_path: str) -> list:
    valid_folders = validate_input_file(input_json_path)
    nuclei_folders = []
    for folder in valid_folders:
        # Set up logging
        file_handler = logging.FileHandler(os.path.join(folder,
                                                        '2_val_log.log'),
                                           mode='w')
        file_handler.setLevel(logging.WARNING)
        file_handler.setFormatter(
            logging.Formatter('%(asctime)s - '
                              '%(levelname)s - '
                              '%(message)s'))
        logging.getLogger('').addHandler(file_handler)

        nuclei_folder = os.path.join(folder,
                                     'foci_assay',
                                     'Nuclei')
        if os.path.exists(nuclei_folder):
            files = [f for f in os.listdir(nuclei_folder)
                     if not f.startswith('.')]
            file_formats = set(os.path.splitext(f)[1] for f in files)
            print(f"Nuclei folder found: {nuclei_folder}, "
                  f"File types: {', '.join(file_formats)}")
            nuclei_folders.append(nuclei_folder)
        else:
            logging.error(f"Nuclei folder not found "
                          f"in '{folder}/foci_assay'.")
    return nuclei_folders


def find_nuclei(nuclei_folders: list) -> list:
    """
    The function to find nuclei using machine
    learning approach from stardist. For the analysis
    2D_versatile_fluo is used.

    Args:
        nuclei_folders: list of folders that contain 2D
        images with nuclei to analyze.

    Returns:
        List of paths to folders with processed masks.
    """
    # Load pre-trained Versatile (fluorescent nuclei) model
    model = StarDist2D.from_pretrained(STARDIST_SETTINGS["model"])

    processed_folders = []

    # Process images in each Nuclei folder
    for nuclei_folder in nuclei_folders:
        output_folder = create_output_folder(
            os.path.dirname(nuclei_folder), "Nuclei_StarDist_mask_processed_")
        processed_folders.append(output_folder)

        # Setting up logging
        file_handler = logging.FileHandler(os.path.join(output_folder,
                                                        '2_log.log'),
                                           mode='w')
        file_handler.setLevel(logging.WARNING)
        file_handler.setFormatter(logging.Formatter('%(asctime)s - '
                                                    '%(levelname)s - '
                                                    '%(message)s'))
        logging.getLogger('').addHandler(file_handler)

        # Get list of files with .tif extension
        image_files = nuclei_source_files(nuclei_folder)
        run_metadata = {
            "schema_version": 1,
            "status": "running",
            "source_folder": str(Path(nuclei_folder).resolve()),
            "settings": dict(STARDIST_SETTINGS),
            "sources": source_fingerprints(nuclei_folder),
            "masks": {},
        }
        write_run_metadata(output_folder, STARDIST_METADATA, run_metadata)

        # Check if there are any images in the folder
        if not image_files:
            logging.error(f"No .tif images found in folder "
                          f"'{nuclei_folder}'. Skipping folder.")
            run_metadata["status"] = "incomplete"
            write_run_metadata(output_folder, STARDIST_METADATA, run_metadata)
            continue

        # Process each image in the folder
        for image_file in image_files:
            image_path = os.path.join(nuclei_folder, image_file)
            image = imread(image_path)

            # Check if the image is 8-bit grayscale
            if image.dtype != np.uint8:
                logging.error(f"Image '{image_file}' is "
                              f"not 8-bit grayscale. "
                              f"Skipping file.")
                continue

            # Normalize the image
            image = normalize(image)

            # Apply model with specified thresholds
            labels, details = model.predict_instances(
                image,
                nms_thresh=STARDIST_SETTINGS["nms_thresh"],
                prob_thresh=STARDIST_SETTINGS["prob_thresh"])

            # Form new file name with _StarDist_processed suffix
            base_name, ext = os.path.splitext(image_file)
            new_file_name = f"{base_name}_StarDist_processed{ext}"
            output_path = os.path.join(output_folder, new_file_name)
            imsave(output_path, labels.astype(np.uint16))
            run_metadata["masks"][new_file_name] = file_digest(output_path)

        complete = (
            len(run_metadata["masks"]) == len(image_files)
            and run_metadata["sources"] == source_fingerprints(nuclei_folder))
        run_metadata["status"] = "complete" if complete else "incomplete"
        write_run_metadata(output_folder, STARDIST_METADATA, run_metadata)
        print(f"Image processing completed in folder '{nuclei_folder}'.")

    return processed_folders


def process_nuclei(valid_folders: list,
                   particle_size: int) -> None:
    """
    Process all files from the provided directories (.tif)
    for the Nuclei channel using ImageJ.

    Args:
        valid_folders: list of folders containing 2D images.
        particle_size: minimum size of nuclei to analyze.
    """
    # Initialize ImageJ
    ij = initialize_imagej()  # noqa: F841

    # Import Java classes
    IJ = jimport('ij.IJ')
    WindowManager = jimport('ij.WindowManager')

    # Process images in each folder
    for input_folder in valid_folders:
        # Keep every area-filter run separate, including same-second reruns.
        processed_folder = create_output_folder(
            os.path.dirname(input_folder), "Final_Nuclei_Mask_")
        print(f"\nProcessed images will be saved in: {processed_folder}")
        run_metadata = {
            "schema_version": 1,
            "status": "running",
            "stardist_folder": str(Path(input_folder).resolve()),
            "particle_size_pixels_squared": particle_size,
            "processed_files": [],
            "skipped_files": [],
        }
        write_run_metadata(processed_folder, "nuclei_run.json", run_metadata)

        # Set up logging
        log_file = os.path.join(processed_folder, 'nuclei_log.log')
        file_handler = logging.FileHandler(log_file, mode='w')
        file_handler.setLevel(logging.WARNING)
        file_handler.setFormatter(logging.Formatter('%(asctime)s - '
                                                    '%(levelname)s - '
                                                    '%(message)s'))
        logging.getLogger('').addHandler(file_handler)

        # Valid file extensions
        valid_exts = ('.tif', '.tiff')

        for filename in os.listdir(input_folder):
            # Skip hidden files and files starting with "._"
            if filename.startswith('.') or filename.startswith('._'):
                logging.warning(f"Skipping hidden "
                                f"or dot-underscore file: "
                                f"{filename}")
                continue

            if filename == STARDIST_METADATA:
                continue

            # Check file extension
            file_ext = filename.lower()
            if not file_ext.endswith(valid_exts):
                # If file is not TIF/TIFF, skip
                logging.error(f"Skipping '{filename}' (unsupported format).")
                continue

            file_path = os.path.join(input_folder, filename)
            print(f"\nProcessing file: {file_path}")

            # Close any images left open
            IJ.run("Close All")

            # Open the image
            imp = IJ.openImage(file_path)
            if imp is None:
                logging.warning(f"Failed to open image: "
                                f"{file_path}. "
                                f"Check Bio-Formats or file integrity.")
                run_metadata["skipped_files"].append(filename)
                continue

            # Convert image to 8-bit
            IJ.run(imp, "8-bit", "")

            # Threshold
            IJ.setThreshold(imp, 1, 255)
            IJ.run(imp, "Convert to Mask", "")
            IJ.run(imp, "Watershed", "")

            # Analyze particles with specified particle size
            IJ.run(imp, "Analyze Particles...",
                   f"size={particle_size}-Infinity pixel show=Masks")

            # Get processed image
            mask_title = 'Mask of ' + filename
            imp_mask = WindowManager.getImage(mask_title)
            if imp_mask is None:
                imp_mask = WindowManager.getCurrentImage()
                if imp_mask is None:
                    logging.error(f"Failed to get mask for image: {file_path}")
                    imp.close()
                    run_metadata["skipped_files"].append(filename)
                    continue

            # Save processed image
            base_name = os.path.splitext(filename)[0]
            output_path = os.path.join(processed_folder,
                                       f"{base_name}_processed.tif")
            IJ.saveAs(imp_mask, "Tiff", output_path)
            run_metadata["processed_files"].append(filename)
            print(f"Processed image saved: {output_path}")

            # Close images
            imp.close()
            imp_mask.close()

        # Close all images to free memory
        IJ.run("Close All")
        run_metadata["status"] = (
            "complete" if run_metadata["processed_files"]
            and not run_metadata["skipped_files"] else "incomplete")
        write_run_metadata(processed_folder, "nuclei_run.json", run_metadata)
        print(f"ImageJ run status: {run_metadata['status']}; "
              f"processed: {len(run_metadata['processed_files'])}; "
              f"skipped: {len(run_metadata['skipped_files'])}; "
              f"minimum area: {particle_size} pixels^2.")


def main(input_json_path: str,
         particle_size: int) -> None:
    """
    Main function to analyze and process nuclei.
    """
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
            metadata = json.loads((Path(output_folder) / STARDIST_METADATA)
                                  .read_text(encoding="utf-8"))
            if metadata["status"] != "complete":
                raise ValueError(f"Incomplete StarDist results: {output_folder}. "
                                 "ImageJ processing was not started.")
            selected[nuclei_folder] = output_folder
    processed_folders = [selected[folder] for folder in nuclei_folders]
    print("Step 1 completed: Nuclei masks ready.")

    # Step 2: Process nuclei using ImageJ
    print("Starting Step 2: Processing nuclei with ImageJ...")
    process_nuclei(processed_folders, particle_size)
    print("Step 2 completed: Nuclei processing finished.")


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('-i',
                        '--input',
                        type=str,
                        help="JSON file with all paths of directories",
                        required=True)
    parser.add_argument('-p',
                        '--particle_size',
                        type=int,
                        help="Minimum size of nuclei to analyze (in pixels). "
                             "Default is 2500",
                        default=2500)
    args = parser.parse_args()
    main(args.input, args.particle_size)
