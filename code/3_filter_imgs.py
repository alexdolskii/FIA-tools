#!/usr/bin/env python3
import argparse
import logging
import os
from datetime import datetime

import imagej
from scyjava import jimport
from validate_folders import validate_path_files


def parse_metadata_file(metadata_path: str) -> dict:
    """
    Reads 'image_metadata.txt' and returns a dictionary
    keyed by the base image name (e.g., "image_1") with
    a dictionary of calibration info.

    Expects file lines like:

        Image Metadata:
        ================
        Image Name: image_1.tif
          Pixel Width: 0.2405002405002405
          Pixel Height: 0.2405002405002405
          Pixel Depth: 1.0
          Unit: micron
          Channels: 3
          Slices: 1
          Frames: 1

        Image Name: image_1.tif
          Pixel Width: ...
          ...
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
                # If we have a previous entry, store it before starting a new one
                if current_name and current_data:
                    base_key = os.path.splitext(current_name)[0]
                    metadata_dict[base_key] = current_data

                # Start a new entry
                # e.g. "Image Name: image_1.tif" -> current_name = "image_1.tif"
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
            # You can parse channels, slices, frames if needed, but not crucial
            # for setting the calibration in ImageJ:
            # elif line.startswith("Channels:"):
            #    ...
            # elif line.startswith("Slices:"):
            #    ...
            # elif line.startswith("Frames:"):
            #    ...

        # Store the last entry if present
        if current_name and current_data:
            base_key = os.path.splitext(current_name)[0]
            metadata_dict[base_key] = current_data

    return metadata_dict


def find_metadata_for_file(filename: str, metadata_dict: dict) -> dict:
    """
    Attempt to find the calibration data in 'metadata_dict'
    for a given filename (e.g. 'image_1_nuclei_projection.tif').

    We look for a key in metadata_dict (e.g. 'image_1') that
    is contained in 'filename'. If found, return that entry.

    If no match, return None.
    """
    for base_key, cal_data in metadata_dict.items():
        # If 'image_1' is in 'image_1_nuclei_projection.tif', we match
        if base_key in filename:
            return cal_data
    return None


def filter_in_folder(folder: dict,
                     particle_size: int,
                     foci_threshold: int) -> None:
    """
    The function to filter the results of machine
    learning processing of images.
    Args:
        folder: the path to the folder that contain output of
                main_analyze_nuclei function
        particle_size: the threshold for size of nuclei
        foci_threshold: threshold value for foci analysis
    Returns:
        Two directories: 'Final_Nuclei_Mask' and 'Foci_Mask'
    """
    # Initialize ImageJ
    print("Initializing ImageJ...")
    ij = imagej.init('sc.fiji:fiji', mode='headless')
    print(f"ImageJ initialization completed. Version: {ij.getVersion()}")

    # Import Java classes
    IJ = jimport('ij.IJ')
    WindowManager = jimport('ij.WindowManager')
    print("Java classes successfully imported.")

    # Paths
    foci_folder = folder['foci_folder']
    nuclei_folder = folder['nuclei_folder']
    foci_files = folder['foci_files']
    nuclei_files = folder['nuclei_files']
    foci_assay_folder = folder['foci_assay_folder']

    # Read metadata from the text file located in 'foci_assay_folder'
    metadata_path = os.path.join(foci_assay_folder, "image_metadata.txt")
    metadata_dict = parse_metadata_file(metadata_path)

    # Create folders for saving results
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    foci_mask_folder = os.path.join(foci_assay_folder, f"Foci_Mask_{timestamp}")
    nuclei_mask_folder = os.path.join(foci_assay_folder, f"Final_Nuclei_Mask_{timestamp}")
    os.makedirs(foci_mask_folder, exist_ok=True)
    os.makedirs(nuclei_mask_folder, exist_ok=True)

    # Setting up logging for Foci
    file_handler = logging.FileHandler(os.path.join(foci_mask_folder, '3_foci_log.txt'), mode='w')
    file_handler.setLevel(logging.WARNING)
    file_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
    logging.getLogger('').addHandler(file_handler)

    # -------- Process images in 'Foci' folder --------
    for filename in foci_files:
        file_path = os.path.join(foci_folder, filename)
        print(f"\nProcessing Foci file: {file_path}")
        # Close all images before starting processing
        IJ.run("Close All")

        # Open image
        imp = IJ.openImage(file_path)
        if imp is None:
            logging.error(f"Failed to open image: {file_path}")
            continue

        # Convert image to 8-bit
        IJ.run(imp, "8-bit", "")

        # Try to retrieve calibration info from the metadata
        cal_data = find_metadata_for_file(filename, metadata_dict)
        if cal_data:
            pxw = cal_data.get('pixel_width', 0.2071602)
            pxh = cal_data.get('pixel_height', 0.2071602)
            pxd = cal_data.get('pixel_depth', 0.5)
            unit = cal_data.get('unit', 'micron')
        else:
            logging.warning(f"No matching metadata found for '{filename}'. Using default calibration.")
            pxw, pxh, pxd, unit = 0.2071602, 0.2071602, 0.5, 'micron'

        # Set calibration in ImageJ
        # Alternatively, you can also do imp.setCalibration(...) in Java directly.
        IJ.run(imp, "Properties...",
               f"channels=1 slices=1 frames=1 "
               f"pixel_width={pxw} pixel_height={pxh} voxel_depth={pxd}")

        # Optionally set unit if needed. 'Properties...' macro does not allow a 'unit=' param,
        # but we can do it directly in the calibration object:
        calibration = imp.getCalibration()
        calibration.setXUnit(unit)
        calibration.setYUnit(unit)
        calibration.setZUnit(unit)

        # Set threshold for Foci
        IJ.setThreshold(imp, foci_threshold, 255)
        IJ.run(imp, "Convert to Mask", "")
        IJ.run(imp, "Watershed", "")

        # Analyze particles
        IJ.run(imp, "Analyze Particles...", "size=0-Infinity pixel show=Masks")

        # Get processed image
        mask_title = 'Mask of ' + filename
        imp_mask = WindowManager.getImage(mask_title)
        if imp_mask is None:
            # If title differs, try to find the last opened image
            imp_mask = WindowManager.getCurrentImage()
            if imp_mask is None:
                logging.error(f"Failed to get mask for image: {file_path}")
                imp.close()
                continue

        # Save processed image
        output_path = os.path.join(foci_mask_folder, f"processed_{filename}")
        IJ.saveAs(imp_mask, "Tiff", output_path)
        print(f"Processed image saved: {output_path}")

        # Close images
        imp.close()
        imp_mask.close()

    # Setting up logging for Nuclei
    file_handler = logging.FileHandler(os.path.join(nuclei_mask_folder, '3_nuc_log.txt'), mode='w')
    file_handler.setLevel(logging.WARNING)
    file_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
    logging.getLogger('').addHandler(file_handler)

    # -------- Process images in the latest 'Nuclei' folder --------
    for filename in nuclei_files:
        file_path = os.path.join(nuclei_folder, filename)
        print(f"\nProcessing Nuclei file: {file_path}")
        # Close all images before starting processing
        IJ.run("Close All")

        # Open image
        imp = IJ.openImage(file_path)
        if imp is None:
            logging.error(f"Failed to open image: {file_path}")
            continue

        # Convert image to 8-bit
        IJ.run(imp, "8-bit", "")

        # Retrieve calibration from metadata
        cal_data = find_metadata_for_file(filename, metadata_dict)
        if cal_data:
            pxw = cal_data.get('pixel_width', 0.2071602)
            pxh = cal_data.get('pixel_height', 0.2071602)
            pxd = cal_data.get('pixel_depth', 0.5)
            unit = cal_data.get('unit', 'micron')
        else:
            logging.warning(f"No matching metadata found for '{filename}'. Using default calibration.")
            pxw, pxh, pxd, unit = 0.2071602, 0.2071602, 0.5, 'micron'

        # Set calibration in ImageJ
        IJ.run(imp, "Properties...",
               f"channels=1 slices=1 frames=1 "
               f"pixel_width={pxw} pixel_height={pxh} voxel_depth={pxd}")

        # Optionally set unit if needed
        calibration = imp.getCalibration()
        calibration.setXUnit(unit)
        calibration.setYUnit(unit)
        calibration.setZUnit(unit)

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
                continue

        # Save processed image
        output_path = os.path.join(nuclei_mask_folder, f"processed_{filename}")
        IJ.saveAs(imp_mask, "Tiff", output_path)
        print(f"Processed image saved: {output_path}")

        # Close images
        imp.close()
        imp_mask.close()


def main_filter_imgs(input_json_path: str,
                     particle_size: int,
                     foci_threshold: int) -> None:
    """
    Main entry point to validate & process machine-learning results.
    Uses filter_in_folder() to handle each valid folder.

    Args:
        input_json_path: JSON file with all paths of directories
        particle_size: threshold for nuclei size
        foci_threshold: threshold for foci intensity

    Returns:
        Two directories in each folder: 'Final_Nuclei_Mask' and 'Foci_Mask'
    """
    # Setting up logging
    logging.basicConfig(level=logging.WARNING,
                        format='%(asctime)s - %(levelname)s - %(message)s')

    if not isinstance(particle_size, int):
        raise ValueError('Particle size must be an integer!')

    if not isinstance(foci_threshold, int):
        raise ValueError('Foci threshold must be an integer!')

    # Step=3 ensures we have 'foci_assay_folder', 'foci_folder', 'nuclei_folder', etc.
    folders = validate_path_files(input_json_path, step=3)

    # Request to start processing
    start_processing = input("\nStart processing the found folders? (yes/no): ").strip().lower()
    if start_processing in ('no', 'n'):
        raise ValueError("Analysis canceled by user.")
    elif start_processing not in ('yes', 'y', 'no', 'n'):
        raise ValueError("Incorrect input. Please enter yes/no.")

    # Process files in valid folders
    for path in folders.keys():
        filter_in_folder(folders[path], particle_size, foci_threshold)

    print("\n--- All processing tasks completed ---")


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
                        help="The threshold for size of nuclei. Default is 500",
                        default=2500)
    parser.add_argument('-f',
                        '--foci_threshold',
                        type=int,
                        help="Threshold value for foci analysis. Default is 150",
                        default=150)
    args = parser.parse_args()
    main_filter_imgs(args.input, args.particle_size, args.foci_threshold)
