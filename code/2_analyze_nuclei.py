import os

from pathlib import Path
from stardist.models import StarDist2D
from stardist import random_label_cmap
from stardist.plot import render_label
from skimage.io import imread, imsave
from csbdeep.utils import normalize
import numpy as np
from datetime import datetime

from validate_folders import validate_path_files

# # Check if the file exists
# if not os.path.exists(input_json_path):
#     raise ValueError(f"File '{input_json_path}' does not exist. Please try again.")
#
# # Read folder paths from file and convert paths for WSL
# with open(input_json_path, 'r') as file:
#     paths_dict = json.load(file)
#     folder_paths = paths_dict["paths_to_files"]
#     if folder_paths[0].startswith('C:\\'):
#         folder_paths = [path.strip().replace('C:\\', '/mnt/c/')
#                         .replace('\\', '/') for path in folder_paths]
#
# # Check existence of folders
# valid_folders = []
# for folder_path in folder_paths:
#     if os.path.exists(folder_path):
#         valid_folders.append(folder_path)
#     else:
#         print(f"Folder '{folder_path}' not found.")
#
# print(f"Found {len(valid_folders)} folders for verification.")
#
# # Search for Nuclei folder in each folder and determine file types
# nuclei_folders = []
# for folder in valid_folders:
#     nuclei_folder = os.path.join(folder, 'foci_assay', 'Nuclei')
#     if os.path.exists(nuclei_folder):
#         files = os.listdir(nuclei_folder)
#         file_formats = set(os.path.splitext(f)[1] for f in files)
#         print(f"Nuclei folder found: {nuclei_folder}, File types: {', '.join(file_formats)}")
#         nuclei_folders.append(nuclei_folder)
#     else:
#         print(f"Nuclei folder not found in '{folder}/foci_assay'.")


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
        output_folder_name = f"Nuclei_StarDist_mask_processed_{current_datetime}"
        output_folder = os.path.join(os.path.dirname(nuclei_folder), output_folder_name)
        Path(output_folder).mkdir(parents=True, exist_ok=True)

        # Get list of files with .tif extension
        image_files = [f for f in os.listdir(nuclei_folder) if f.endswith('.tif')]

        # Check if there are any images in the folder
        if not image_files:
            print(f"No .tif images found in folder '{nuclei_folder}'. Skipping folder.")
            continue

        # Process each image in the folder
        for image_file in image_files:
            image_path = os.path.join(nuclei_folder, image_file)
            image = imread(image_path)

            # Check if the image is 8-bit grayscale
            if image.dtype != np.uint8:
                print(f"Image '{image_file}' is not 8-bit grayscale. Skipping file.")
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
    start_analysis = input("Start analyzing Nuclei folders? (yes/no): ").strip().lower()
    if start_analysis != 'yes':
        raise ValueError("Analysis canceled by user.")
    find_nuclei(nuclei_folders)
    print("Analysis of all Nuclei folders completed.")
