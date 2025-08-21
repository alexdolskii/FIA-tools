#!/usr/bin/env python3

import argparse
import logging
import os
from datetime import datetime
from pathlib import Path

import imagej
import numpy as np
from csbdeep.utils import normalize
from scyjava import jimport
from skimage.io import imread, imsave
from stardist.models import StarDist2D
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
                                                        '2_val_log.txt'),
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
            files = os.listdir(nuclei_folder)
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
    model = StarDist2D.from_pretrained('2D_versatile_fluo')

    processed_folders = []

    # Process images in each Nuclei folder
    for nuclei_folder in nuclei_folders:
        # Get current date and time in format YYYYMMDD_HHMMSS
        current_datetime = (datetime.now()
                            .strftime("%Y%m%d_%H%M%S"))
        output_folder_name = (f"Nuclei_StarDist_mask_"
                              f"processed_{current_datetime}")
        output_folder = os.path.join(os.path.dirname(nuclei_folder),
                                     output_folder_name)
        Path(output_folder).mkdir(parents=True, exist_ok=True)
        processed_folders.append(output_folder)

        # Setting up logging
        file_handler = logging.FileHandler(os.path.join(output_folder,
                                                        '2_log.txt'),
                                           mode='w')
        file_handler.setLevel(logging.WARNING)
        file_handler.setFormatter(logging.Formatter('%(asctime)s - '
                                                    '%(levelname)s - '
                                                    '%(message)s'))
        logging.getLogger('').addHandler(file_handler)

        # Get list of files with .tif extension
        image_files = [f for f in os.listdir(nuclei_folder)
                       if f.endswith('.tif')]

        # Check if there are any images in the folder
        if not image_files:
            logging.error(f"No .tif images found in folder "
                          f"'{nuclei_folder}'. Skipping folder.")
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
            labels, details = model.predict_instances(image,
                                                      nms_thresh=0.9,
                                                      prob_thresh=0.7)

            # Form new file name with _StarDist_processed suffix
            base_name, ext = os.path.splitext(image_file)
            new_file_name = f"{base_name}_StarDist_processed{ext}"
            output_path = os.path.join(output_folder, new_file_name)
            imsave(output_path, labels.astype(np.uint16))

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
    ij = initialize_imagej()

    # Import Java classes
    IJ = jimport('ij.IJ')
    WindowManager = jimport('ij.WindowManager')

    # Process images in each folder
    for input_folder in valid_folders:
        # Generate timestamp for the folder name
        current_datetime = datetime.now().strftime("%Y%m%d_%H%M%S")

        # Create a new folder 'Final_Nuclei_Mask_<timestamp>'
        # in the same directory as input_folder
        processed_folder = os.path.join(os.path.dirname(input_folder),
                                        f'Final_Nuclei_Mask_'
                                        f'{current_datetime}')

        if os.path.exists(processed_folder):
            response = input(
                f"The folder {processed_folder} already exists. "
                "Do you want to overwrite existing results? (yes/no): "
            ).strip().lower()
            if response == 'no':
                raise ValueError("Analysis canceled by user.")
        Path(processed_folder).mkdir(parents=True, exist_ok=True)
        print(f"\nProcessed images will be saved in: {processed_folder}")

        # Set up logging
        log_file = os.path.join(processed_folder, 'nuclei_log.txt')
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
                    continue

            # Save processed image
            base_name = os.path.splitext(filename)[0]
            output_path = os.path.join(processed_folder,
                                       f"{base_name}_processed.tif")
            IJ.saveAs(imp_mask, "Tiff", output_path)
            print(f"Processed image saved: {output_path}")

            # Close images
            imp.close()
            imp_mask.close()

        # Close all images to free memory
        IJ.run("Close All")


def main(input_json_path: str,
         particle_size: int) -> None:
    """
    Main function to analyze and process nuclei.
    """
    # Step 1: Analyze nuclei using StarDist
    print("Starting Step 1: Analyzing nuclei with StarDist...")
    nuclei_folders = validate_folders(input_json_path)
    processed_folders = find_nuclei(nuclei_folders)
    print("Step 1 completed: Nuclei masks created.")

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
