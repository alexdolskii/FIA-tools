#!/usr/bin/env python

if __name__ == '__main__':
    from runtime_worker import launch_direct
    raise SystemExit(launch_direct('generate_foci_mask'))

from assay_layout import ASSAY_DIR, MARKERS_DIR
import logging
import os
from datetime import datetime
from pathlib import Path

import fiji_config
import imagej
import spatial_calibration as spatial
from interactive_input import Cancelled, ask_integer, ask_yes_no, cancelable
from scyjava import jimport
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
                foci_threshold: int) -> None:
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
    foci_folder = folder['foci_folder']
    fia_assay_folder = folder['fia_assay_folder']

    if chosen_subfolder.startswith('.'):
        logging.warning(f"Skipping hidden foci folder: {chosen_subfolder}")
        return

    # Build the path to the chosen subfolder
    subfolder_path = os.path.join(foci_folder, chosen_subfolder)

    # If the chosen subfolder does not exist in this folder, skip
    if not os.path.isdir(subfolder_path):
        print(f"  - Subfolder '{chosen_subfolder}' "
              f"not found in {foci_folder}. Skipping.\n")
        return

    # Collect TIF/TIFF files within the chosen subfolder
    foci_files = [
        f for f in os.listdir(subfolder_path)
        if not f.startswith('.')
        and f.lower().endswith((".tif", ".tiff"))
    ]
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
    os.makedirs(foci_mask_folder, exist_ok=True)

    # Setup logging to file in the result subfolder
    file_handler = logging.FileHandler(os.path.join(foci_mask_folder,
                                                    'foci_log.log'),
                                       mode='w')
    file_handler.setLevel(logging.WARNING)
    file_handler.setFormatter(logging.Formatter('%(asctime)s - '
                                                '%(levelname)s - '
                                                '%(message)s'))
    logging.getLogger('').addHandler(file_handler)

    print(f"  - Processing {len(foci_files)} file(s) in "
          f"'{chosen_subfolder}'...")

    # Process each TIF file
    for filename in foci_files:
        file_path = os.path.join(subfolder_path, filename)
        print(f"    -> {filename}")
        IJ.run("Close All")  # Close images before starting

        # Open image
        imp = IJ.openImage(file_path)
        if imp is None:
            logging.error(f"Failed to open image: {file_path}")
            continue

        # Convert image to 8-bit
        IJ.run(imp, "8-bit", "")

        # Prefer the exact projection snapshot; never invent a microscopy scale.
        shape = (int(imp.getHeight()), int(imp.getWidth()))
        calibration = spatial.projection_calibration(file_path, shape)
        if not spatial.calibrated(calibration):
            logging.warning("No valid physical calibration for '%s'; using pixel units.", filename)
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
                logging.error(f"Failed to get mask for image: {file_path}")
                imp.close()
                continue

        # Save processed image
        output_path = os.path.join(foci_mask_folder, f"processed_{filename}")
        spatial.imagej_calibration(imp_mask, calibration, jimport)
        IJ.saveAs(imp_mask, "Tiff", output_path)
        spatial.save_snapshot(output_path, calibration, shape)

        # Close images
        imp.close()
        imp_mask.close()

    print(f"  - Results saved to: {foci_mask_folder}\n")


@cancelable
def main_filter_foci(input_json_path: str, foci_threshold: int):
    """
    Main entry point: validate & process machine-learning results for Foci.
    We prompt once for a subfolder to analyze, then apply that choice to all
    valid folders from the JSON.
    """
    # Setting up logging
    logging.basicConfig(level=logging.WARNING,
                        format='%(asctime)s - %(levelname)s - %(message)s')

    if not isinstance(foci_threshold, int):
        raise ValueError('Foci threshold must be an integer!')

    # Step=3 ensures we have 'fia_assay_folder', 'foci_folder', etc.
    folders = validate_folders(input_json_path)
    folder_keys = [key for key, info in folders.items() if 'foci_folder' in info]
    if not folder_keys:
        raise ValueError("No valid folders found in JSON. Exiting.")

    # --- Gather all possible subfolder names from each 'foci_folder' ---
    all_subfolders = set()
    for key in folder_keys:
        foci_folder = folders[key]['foci_folder']
        if os.path.isdir(foci_folder):
            for d in os.listdir(foci_folder):
                subfolder_full = os.path.join(foci_folder, d)
                if not d.startswith('.') and os.path.isdir(subfolder_full):
                    all_subfolders.add(d)

    if not all_subfolders:
        print("No channel subfolders found in any markers folder. Exiting.")
        return

    # Convert to a sorted list for consistent display
    all_subfolders_list = sorted(list(all_subfolders))

    # --- Ask user once: which subfolder to analyze? ---
    print("\nChannel subfolders found across all markers folders:")
    for i, sb in enumerate(all_subfolders_list, start=1):
        print(f"  {i}) {sb}")

    choice_idx = ask_integer(
        f"Select subfolder to analyze (1-{len(all_subfolders_list)}; q = cancel): ",
        1, len(all_subfolders_list)) - 1

    chosen_subfolder = all_subfolders_list[choice_idx]

    # --- Confirm user wants to proceed ---
    if not ask_yes_no(f"\nYou selected '{chosen_subfolder}'. "
                      "Proceed? (yes/no; q = cancel): "):
        raise Cancelled()

    # --- Process that subfolder in each valid folder ---
    for key in folder_keys:
        folder_dict = folders[key]
        print(f"\nAnalyzing folder '{key}': {folder_dict['foci_folder']}")
        filter_foci(folder_dict, chosen_subfolder, foci_threshold)

    print("\n--- All processing tasks completed ---")
