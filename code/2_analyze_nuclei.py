#!/usr/bin/env python3

import argparse
import os
from datetime import datetime
from pathlib import Path

import numpy as np
from csbdeep.utils import normalize
from skimage.io import imread, imsave
from stardist.models import StarDist2D

from validate_folders import validate_path_files


def find_nuclei(nuclei_folders: list) -> None:
    """
    The function to find nuclei using machine
    learning approach from stardist. For the analysis
    2D_versatile_fluo is used
    Args:
        nuclei_folders: list of folders that contain 2D
        images with nuclei to analyze

    Returns:
        The new folder that starts with Nuclei_StarDist_mask_processed
        in each provided directory
    """
    # Load pre-trained Versatile (fluorescent nuclei) model
    model = StarDist2D.from_pretrained('2D_versatile_fluo')

    # Process images in each Nuclei folder
    for nuclei_folder in nuclei_folders:
        # Get current date and time in format YYYYMMDD_HHMMSS
        current_datetime = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_folder_name = (f"Nuclei_StarDist_"
                              f"mask_processed_{current_datetime}")
        output_folder = os.path.join(os.path.dirname(nuclei_folder),
                                     output_folder_name)
        Path(output_folder).mkdir(parents=True, exist_ok=True)

        # Get list of files with .tif extension
        image_files = [f for f in os.listdir(nuclei_folder)
                       if f.endswith('.tif')]

        # Check if there are any images in the folder
        if not image_files:
            print(f"No .tif images found in folder "
                  f"'{nuclei_folder}'. Skipping folder.")
            continue

        # Process each image in the folder
        for image_file in image_files:
            image_path = os.path.join(nuclei_folder, image_file)
            image = imread(image_path)

            # Check if the image is 8-bit grayscale
            if image.dtype != np.uint8:
                print(f"Image '{image_file}' "
                      f"is not 8-bit grayscale. Skipping file.")
                continue

            # Normalize the image
            image = normalize(image)

            # Apply model with specified thresholds
            labels, details = model.predict_instances(
                image, nms_thresh=0.9, prob_thresh=0.7
            )

            # Form new file name with _StarDist_processed suffix
            base_name, ext = os.path.splitext(image_file)
            new_file_name = f"{base_name}_StarDist_processed{ext}"
            output_path = os.path.join(output_folder, new_file_name)
            imsave(output_path, labels.astype(np.uint16))

        print(f"Image processing completed in folder '{nuclei_folder}'.")


def main_analyze_nuclei(input_json_path: str) -> None:
    """
    Main function to analyze nuclei
    Args:
        input_json_path: the path to json file
        that contains of directories to analyze

    Returns:
        The new folder that starts with Nuclei_StarDist_mask_processed
        in each provided directory
    """
    nuclei_folders = validate_path_files(input_json_path, 2)

    # Ask user if analysis should start
    start_analysis = input("Start analyzing "
                           "Nuclei folders? (yes/no): ").strip().lower()
    if start_analysis in ('no', 'n'):
        raise ValueError("Analysis canceled by user")
    elif start_analysis not in ('yes', 'y', 'no', 'n'):
        raise ValueError("Incorrect input. Please enter yes/no")
    find_nuclei(nuclei_folders)
    print("Analysis of all Nuclei folders completed.")


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('-i',
                        '--input',
                        type=str,
                        help="JSON file with all paths of directories",
                        required=True)
    args = parser.parse_args()
    main_analyze_nuclei(args.input)
