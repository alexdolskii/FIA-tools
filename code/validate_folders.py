import json
import os
import logging
from datetime import datetime


def validate_path_files(input_json_path: str,
                        step: int) -> list:
    """
    Function validate_path_files read json file with the path
    to files and check if they all exist
    Args:
        input_json_path: json with paths to directories with files
        step: corresponds to the step of analysis

    Returns:
        Collection with validated files
    """
    # Check if the file exists
    if not os.path.exists(input_json_path):
        raise ValueError(f"File '{input_json_path}' does not exist. "
                         f"Please try again.")

    # Read folder paths from file
    with open(input_json_path, 'r') as file:
        paths_dict = json.load(file)
        folder_paths = paths_dict["paths_to_files"]
        if folder_paths[0].startswith('C:\\'):
            folder_paths = [path.strip().replace('C:\\', '/mnt/c/')
                            .replace('\\', '/') for path in folder_paths]

    if len(folder_paths) == 0:
        raise ValueError("The file does not contain folder paths. "
                         "Please check the file content.")

    # Check existence of folders and count the number of files in each
    print(f"Found {len(folder_paths)} folders for verification.")
    valid_folders = []
    for folder_path in folder_paths:
        if os.path.exists(folder_path):
            if step == 1:
                files = [f for f in os.listdir(folder_path)
                         if f.lower().endswith('.nd2')]
                num_files = len(files)
                file_formats = set(os.path.splitext(f)[1] for f in files)
                message = (', '.join(file_formats)
                           if file_formats
                           else 'no .nd2 files')
                print(f"Folder: {folder_path}, "
                      f"Number of files: {num_files}, "
                      f"File formats: "
                      f"{message}")
                if num_files > 0:
                    valid_folders.append(folder_path)
            else:
                valid_folders.append(folder_path)
        else:
            raise ValueError(f"Folder '{folder_path}' does not exist.")
    if step == 1:
        result = valid_folders
    elif step == 2:
        # Search for Nuclei folder in each folder and determine file types
        nuclei_folders = []
        for folder in valid_folders:
            # Setting up logging
            file_handler = logging.FileHandler(os.path.join(folder,
                                                            '2_val_log.txt'),
                                               mode='w')
            file_handler.setLevel(logging.WARNING)
            file_handler.setFormatter(
                logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))

            logging.getLogger('').addHandler(file_handler)

            nuclei_folder = os.path.join(folder, 'foci_assay', 'Nuclei')
            if os.path.exists(nuclei_folder):
                files = os.listdir(nuclei_folder)
                file_formats = set(os.path.splitext(f)[1] for f in files)
                print(f"Nuclei folder found: {nuclei_folder}, "
                      f"File types: {', '.join(file_formats)}")
                nuclei_folders.append(nuclei_folder)
            else:
                logging.error(f"Nuclei folder not "
                              f"found in '{folder}/foci_assay'.")
        result = nuclei_folders
    elif step == 3:
        result = {}
        for folder in valid_folders:
            # Setting up logging
            file_handler = logging.FileHandler(os.path.join(folder,
                                                            '3_val_log.txt'),
                                               mode='w')
            file_handler.setLevel(logging.WARNING)
            file_handler.setFormatter(
                logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))

            logging.getLogger('').addHandler(file_handler)

            result[folder] = {}
            foci_assay_folder = os.path.join(folder, 'foci_assay')
            if not os.path.exists(foci_assay_folder):
                logging.error(f"Subfolder 'foci_assay' not found "
                              f"in folder '{folder}'. Skipping this folder.")
                continue
            else:
                result[folder]["foci_assay_folder"] = foci_assay_folder

            # Check for 'Foci' subfolder inside 'foci_assay'
            foci_folder = os.path.join(foci_assay_folder, 'Foci')
            if not os.path.exists(foci_folder):
                logging.error(f"Subfolder 'Foci' not found "
                              f"in folder '{foci_assay_folder}'. "
                              f"Skipping this folder.")
            else:
                result[folder]["foci_folder"] = foci_folder

            # Find the latest folder 'Nuclei_StarDist_mask_processed_<timestamp>' inside 'foci_assay'
            processed_folders = []
            for name in os.listdir(foci_assay_folder):
                if name.startswith('Nuclei_StarDist_mask_processed_'):
                    timestamp_str = name.replace('Nuclei_StarDist_mask_processed_', '')
                    timestamp = datetime.strptime(timestamp_str, '%Y%m%d_%H%M%S')
                    processed_folders.append((timestamp, os.path.join(foci_assay_folder, name)))

            if len(processed_folders) == 0:
                raise ValueError(f"No folders found starting with "
                           f"'Nuclei_StarDist_mask_processed_' "
                           f"in '{foci_assay_folder}'. Skipping this folder.")
            else:
                # Select the latest folder
                latest_processed_folder = max(processed_folders, key=lambda x: x[0])[1]
                print(f"Found latest folder "
                      f"'Nuclei_StarDist_mask_processed_': {latest_processed_folder}")
                result[folder]["nuclei_folder"] = latest_processed_folder

            # Check for files in 'Foci'
            foci_files = [f for f in os.listdir(foci_folder)
                          if f.lower().endswith('.tif')]
            if len(foci_files) == 0:
                logging.error(f"No files with '.tif' "
                              f"extension found in folder 'Foci'. Skipping this folder.")
            else:
                result[folder]["foci_files"] = foci_files

            # Check for files in the latest folder 'Nuclei_StarDist_mask_processed_<timestamp>'
            nuclei_files = [f for f in os.listdir(latest_processed_folder)
                            if f.lower().endswith('.tif')]
            if len(nuclei_files) == 0:
                logging.error(f"No files with '.tif' extension "
                              f"found in folder '{latest_processed_folder}'. "
                              f"Skipping this folder.")
            else:
                result[folder]["nuclei_files"] = nuclei_files

            # Information about found files
            print(f"\n--- File information in folder '{foci_assay_folder}' ---")
            print(
                f"Number of files found in 'Foci': {len(foci_files)}. "
                f"Data types: {set(os.path.splitext(f)[-1] for f in foci_files)}")
            print(
                f"Number of files found in 'Nuclei_StarDist_mask_processed_': {len(nuclei_files)}. "
                f"Data types: {set(os.path.splitext(f)[-1] for f in nuclei_files)}")
    elif step == 4:
        result = {}
        for folder in valid_folders:
            # Setting up logging
            file_handler = logging.FileHandler(os.path.join(folder,
                                                            '4_val_log.txt'),
                                               mode='w')
            file_handler.setLevel(logging.WARNING)
            file_handler.setFormatter(
                logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))

            logging.getLogger('').addHandler(file_handler)

            result[folder] = {}
            result[folder]['base_folder'] = folder
            # Check for 'foci_assay' subdirectory
            foci_assay_folder = os.path.join(folder, 'foci_assay')
            if not os.path.exists(foci_assay_folder):
                logging.error(f"Subdirectory 'foci_assay' not found "
                              f"in folder '{folder}'. Skipping this folder.")
                continue
            else:
                result[folder]['foci_assay_folder'] = foci_assay_folder
            # Find the dir 'Final_Nuclei_Mask_YYYYMMDD_HHMMSS'
            final_nuclei_folders = []
            for name in os.listdir(foci_assay_folder):
                if name.startswith('Final_Nuclei_Mask_'):
                    date_str = name.replace('Final_Nuclei_Mask_', '')
                    folder_datetime = datetime.strptime(date_str, '%Y%m%d_%H%M%S')
                    final_nuclei_folders.append((folder_datetime, os.path.join(foci_assay_folder, name)))

            if len(final_nuclei_folders) == 0:
                logging.error(f"No folders 'Final_Nuclei_Mask_YYYYMMDD_HHMMSS' "
                              f"found in '{foci_assay_folder}'. Skipping this folder.")
            else:
                # Find the latest Final_Nuclei_Mask
                lat_fin_nuc_mask_dir = (max(final_nuclei_folders,
                                            key=lambda x: x[0])[1])
                print(f"Selected the latest "
                      f"'Final_Nuclei_Mask' folder: {lat_fin_nuc_mask_dir}")
                result[folder]['final_nuclei_mask_folder'] = lat_fin_nuc_mask_dir

                # Count the number of files that finished
                # on '_StarDist_processed.tif'
                star_dist_processed_files = [f for f in os.listdir(lat_fin_nuc_mask_dir) if
                                             f.endswith('_StarDist_processed.tif')]
                star_dist_count = len(star_dist_processed_files)
                print(
                    f"Number of '_StarDist_processed.tif' files in '{lat_fin_nuc_mask_dir}': {star_dist_count}")
                result[folder]['star_dist_files'] = star_dist_processed_files
                result[folder]['star_dist_count'] = star_dist_count

            # Search for the latest 'Foci_Mask_YYYYMMDD_HHMMSS'
            foci_masks_folders = []
            for name in os.listdir(foci_assay_folder):
                if name.startswith('Foci_Mask_'):
                    date_str = name.replace('Foci_Mask_', '')
                    folder_datetime = datetime.strptime(date_str, '%Y%m%d_%H%M%S')
                    foci_masks_folders.append((folder_datetime, os.path.join(foci_assay_folder, name)))

            if len(foci_masks_folders) == 0:
                logging_error(f"No folders 'Foci_Mask_YYYYMMDD_HHMMSS' "
                              f"found in '{foci_assay_folder}'. Skipping this folder.")

            else:
                # Chose the latest Foci_Mask
                latest_foci_masks_folder = max(foci_masks_folders, key=lambda x: x[0])[1]
                print(f"Selected the latest 'Foci_Mask' "
                      f"folder: {latest_foci_masks_folder}")
                result[folder]['foci_masks_folder'] = latest_foci_masks_folder

                # Count the number of files that ended as '_foci_projection.tif'
                foci_projection_files = [f for f in os.listdir(latest_foci_masks_folder) if
                                         f.endswith('_foci_projection.tif')]
                foci_projection_count = len(foci_projection_files)
                print(f"Number of '_foci_projection.tif' files in "
                      f"'{latest_foci_masks_folder}': {foci_projection_count}")
                result[folder]['foci_projection_files'] = foci_projection_files
                result[folder]['foci_projection_count'] = foci_projection_count
                print(f"  Found '_StarDist_processed.tif' files: {star_dist_count}")
                print(f"  Found '_foci_projection.tif' files: {foci_projection_count}")

            # Check if there are folders for processing
            if len(result) == 0:
                raise ValueError('There are no files to process')
    else:
        raise ValueError('Please choose the particular step from 1 to 4')
    return result
