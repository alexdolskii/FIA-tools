#!/usr/bin/env python3
import argparse
import logging
import os
from datetime import datetime

import imagej
from scyjava import jimport
from validate_folders import validate_input_file


def validate_folders(input_json_path: str) -> dict:
    valid_folders = validate_input_file(input_json_path)
    # checking 'Foci' and the latest
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
        foci_assay_folder = os.path.join(folder, 'foci_assay')
        if not os.path.exists(foci_assay_folder):
            logging.error(f"Subfolder 'foci_assay' "
                          f"not found in folder '{folder}'. "
                          f"Skipping this folder.")
            continue
        else:
            result[folder]["foci_assay_folder"] = foci_assay_folder

        # Check for 'Foci' subfolder
        foci_folder = os.path.join(foci_assay_folder,
                                   'Foci')
        if not os.path.exists(foci_folder):
            logging.error(f"Subfolder 'Foci' not found "
                          f"in folder '{foci_assay_folder}'. "
                          f"Skipping this folder.")
        else:
            result[folder]["foci_folder"] = foci_folder

        # Look for the latest 'Nuclei_StarDist_mask_processed_<timestamp>'
        processed_folders = []
        for name in os.listdir(foci_assay_folder):
            if name.startswith('Nuclei_StarDist_mask_processed_'):
                timestamp_str = name.replace('Nuclei_StarDist_mask_processed_',
                                             '')
                timestamp = datetime.strptime(timestamp_str,
                                              '%Y%m%d_%H%M%S')
                processed_folders.append((timestamp,
                                          os.path.join(foci_assay_folder,
                                                       name)))

        if len(processed_folders) == 0:
            logging.error(f"No folders found "
                          f"starting with 'Nuclei_StarDist_mask_processed_' "
                          f"in '{foci_assay_folder}'. Skipping.")
        else:
            # Select the latest folder
            latest_processed_folder = max(processed_folders,
                                          key=lambda x: x[0])[1]
            print(f"Found the latest folder "
                  f"'Nuclei_StarDist_mask_processed_': "
                  f"{latest_processed_folder}")
            result[folder]["nuclei_folder"] = latest_processed_folder

        # Check for files in 'Foci'
        if "foci_folder" in result[folder]:
            foci_files = [f for f in os.listdir(result[folder]["foci_folder"])
                          if f.lower().endswith('.tif')]
            if len(foci_files) == 0:
                logging.error("No '.tif' files found in folder 'Foci'.")
            else:
                result[folder]["foci_files"] = foci_files

        # Check for files in the latest
        # 'Nuclei_StarDist_mask_processed_<timestamp>'
        if "nuclei_folder" in result[folder]:
            nuclei_files = [f for f in
                            os.listdir(result[folder]["nuclei_folder"])
                            if f.lower().endswith('.tif')]
            if len(nuclei_files) == 0:
                logging.error(f"No '.tif' files found in folder "
                              f"'{result[folder]['nuclei_folder']}'.")
            else:
                result[folder]["nuclei_files"] = nuclei_files

        # Print information about found files
        if "foci_folder" in result[folder]:
            foci_files = result[folder].get("foci_files", [])
            print(f"\n--- File information in folder "
                  f"'{foci_assay_folder}' ---")
            print(f"Number of files in 'Foci': {len(foci_files)}. "
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
    if not os.path.exists(metadata_path):
        logging.warning(f"Metadata file not found: {metadata_path}")
        return {}

    metadata_dict = {}
    current_name = None
    current_data = {}

    with open(metadata_path, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if line.startswith("Image Name:"):
                # If we have a previous entry, store
                # it before starting a new one
                if current_name and current_data:
                    base_key = os.path.splitext(current_name)[0]
                    metadata_dict[base_key] = current_data

                # Start a new entry
                current_name = line.replace("Image Name:", "").strip()
                current_data = {}
            elif line.startswith("Pixel Width:"):
                val = line.replace("Pixel Width:", "").strip()
                current_data['pixel_width'] = float(val)
            elif line.startswith("Pixel Height:"):
                val = line.replace("Pixel Height:", "").strip()
                current_data['pixel_height'] = float(val)
            elif line.startswith("Pixel Depth:"):
                val = line.replace("Pixel Depth:", "").strip()
                current_data['pixel_depth'] = float(val)
            elif line.startswith("Unit:"):
                val = line.replace("Unit:", "").strip()
                current_data['unit'] = val

        # Store the last entry if present
        if current_name and current_data:
            base_key = os.path.splitext(current_name)[0]
            metadata_dict[base_key] = current_data

    return metadata_dict


def find_metadata_for_file(filename: str, metadata_dict: dict) -> dict:
    """
    Attempt to find the calibration data in 'metadata_dict'
    for a given filename (e.g. 'image_1_nuclei_projection.tif').
    """
    for base_key, cal_data in metadata_dict.items():
        if base_key in filename:
            return cal_data
    return None


def filter_foci(folder: dict,
                chosen_subfolder: str,
                foci_threshold: int) -> None:
    """
    Filters machine-learning results for Foci
    images in one specific subfolder.

    Args:
        folder: dictionary containing at least:
                - 'foci_assay_folder'
                - 'foci_folder'
        chosen_subfolder: name of the subfolder to analyze
        (e.g. "Foci_1_Channel_1")
        foci_threshold: threshold value for foci analysis
    """
    # Extract the relevant paths
    foci_folder = folder['foci_folder']
    foci_assay_folder = folder['foci_assay_folder']

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
        if f.lower().endswith((".tif", ".tiff"))
    ]
    if not foci_files:
        print(f"  - No TIF/TIFF files found in "
              f"{subfolder_path}. Nothing to do.\n")
        return

    # Initialize ImageJ once we know there's something to process
    print("  - Initializing ImageJ...")
    ij = imagej.init('sc.fiji:fiji',
                     mode='headless')
    print(f"  - ImageJ initialization completed. "
          f"Version: {ij.getVersion()}")

    # Import Java classes
    IJ = jimport('ij.IJ')
    WindowManager = jimport('ij.WindowManager')

    # Read metadata from image_metadata.txt
    metadata_path = os.path.join(foci_assay_folder,
                                 "image_metadata.txt")
    metadata_dict = parse_metadata_file(metadata_path)

    # Create (or reuse) a "Foci_Masks" folder in the assay folder
    foci_masks_base = os.path.join(foci_assay_folder, "Foci_Masks")
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

        # Retrieve calibration info (if any) from metadata
        cal_data = find_metadata_for_file(filename, metadata_dict)
        if cal_data:
            pxw = cal_data.get('pixel_width', 0.2071602)
            pxh = cal_data.get('pixel_height', 0.2071602)
            pxd = cal_data.get('pixel_depth', 0.5)
            unit = cal_data.get('unit', 'micron')
        else:
            logging.warning(f"No matching metadata found for '{filename}'. "
                            f"Using defaults.")
            pxw, pxh, pxd, unit = 0.2071602, 0.2071602, 0.5, 'micron'

        # Set calibration in ImageJ
        IJ.run(imp, "Properties...",
               f"channels=1 slices=1 frames=1 "
               f"pixel_width={pxw} pixel_height={pxh} voxel_depth={pxd}")

        # Optionally set the units
        calibration = imp.getCalibration()
        calibration.setXUnit(unit)
        calibration.setYUnit(unit)
        calibration.setZUnit(unit)

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
        IJ.saveAs(imp_mask, "Tiff", output_path)

        # Close images
        imp.close()
        imp_mask.close()

    print(f"  - Results saved to: {foci_mask_folder}\n")


def main_filter_foci(input_json_path: str, foci_threshold: int) -> None:
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

    # Step=3 ensures we have 'foci_assay_folder', 'foci_folder', etc.
    folders = validate_folders(input_json_path)
    folder_keys = list(folders.keys())
    if not folder_keys:
        raise ValueError("No valid folders found in JSON. Exiting.")

    # --- Gather all possible subfolder names from each 'foci_folder' ---
    all_subfolders = set()
    for key in folder_keys:
        foci_folder = folders[key]['foci_folder']
        if os.path.isdir(foci_folder):
            for d in os.listdir(foci_folder):
                subfolder_full = os.path.join(foci_folder, d)
                if os.path.isdir(subfolder_full):
                    all_subfolders.add(d)

    if not all_subfolders:
        print("No subfolders found in any Foci folder. Exiting.")
        return

    # Convert to a sorted list for consistent display
    all_subfolders_list = sorted(list(all_subfolders))

    # --- Ask user once: which subfolder to analyze? ---
    print("\nSubfolders found across all Foci folders:")
    for i, sb in enumerate(all_subfolders_list, start=1):
        print(f"  {i}) {sb}")

    choice = input("Select subfolder to analyze (enter a number): ").strip()
    try:
        choice_idx = int(choice) - 1
        if choice_idx < 0 or choice_idx >= len(all_subfolders_list):
            raise IndexError
    except (ValueError, IndexError):
        raise ValueError("Invalid choice. Please run again.")

    chosen_subfolder = all_subfolders_list[choice_idx]

    # --- Confirm user wants to proceed ---
    start_processing = input(f"\nYou selected '{chosen_subfolder}'. "
                             f"Proceed? (yes/no): ").strip().lower()
    if start_processing in ('no', 'n'):
        raise ValueError("Analysis canceled by user.")
    elif start_processing not in ('yes', 'y', 'no', 'n'):
        raise ValueError("Incorrect input. Please enter yes/no.")

    # --- Process that subfolder in each valid folder ---
    for key in folder_keys:
        folder_dict = folders[key]
        print(f"\nAnalyzing folder '{key}': {folder_dict['foci_folder']}")
        filter_foci(folder_dict, chosen_subfolder, foci_threshold)

    print("\n--- All processing tasks completed ---")


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('-i',
                        '--input',
                        type=str,
                        help="JSON file with all paths of directories",
                        required=True)
    parser.add_argument('-f',
                        '--foci_threshold',
                        type=int,
                        help="Threshold value for foci analysis. "
                             "Default is 150",
                        default=150)
    args = parser.parse_args()
    main_filter_foci(args.input, args.foci_threshold)
