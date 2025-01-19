import json
import logging
import os
from datetime import datetime


def validate_path_files(input_json_path: str,
                        step: int) -> list:
    """
    Validates paths specified in a JSON file for existence, 
    then checks for certain file types depending on the analysis step.

    Args:
        input_json_path (str): Path to a JSON file containing folder paths under the key "paths_to_files".
        step (int): Indicates which step of analysis we are at (1, 2, 3, or 4).

    Returns:
        list (or dict): A list of valid folders or a dictionary of folder information, 
                        depending on the step.

    Raises:
        ValueError: If the JSON file does not exist, if folder paths are missing, 
                    if folders do not exist, or if the step is invalid.
    """

    # Check if the input JSON file exists
    if not os.path.exists(input_json_path):
        raise ValueError(f"File '{input_json_path}' does not exist. "
                         f"Please provide a valid path.")

    # Read folder paths from the JSON file
    with open(input_json_path, 'r') as file:
        paths_dict = json.load(file)
        folder_paths = paths_dict.get("paths_to_files", [])

        # If file paths start with C:\, convert them to WSL paths if needed
        if folder_paths and folder_paths[0].startswith('C:\\'):
            folder_paths = [
                path.strip()
                    .replace('C:\\', '/mnt/c/')
                    .replace('\\', '/')
                for path in folder_paths
            ]

    if len(folder_paths) == 0:
        raise ValueError("The JSON file does not contain any folder paths. "
                         "Please check the file content under key 'paths_to_files'.")

    print(f"Found {len(folder_paths)} folders for verification.")

    # This list (or dict) will store valid folder information
    valid_folders = []

    for folder_path in folder_paths:
        if not os.path.exists(folder_path):
            raise ValueError(f"Folder '{folder_path}' does not exist.")

        if step == 1:
            # In step 1, we want to look for .nd2 or .tif/.tiff files
            valid_extensions = {'.nd2', '.tif', '.tiff'}

            # Gather files in the folder
            all_files = [f for f in os.listdir(folder_path)
                         if os.path.isfile(os.path.join(folder_path, f))]

            # Filter only recognized image types
            recognized_files = [
                f for f in all_files
                if os.path.splitext(f)[1].lower() in valid_extensions
            ]
            num_files = len(recognized_files)

            # Create a set of file formats found
            file_formats = set(os.path.splitext(f)[1].lower()
                               for f in recognized_files)
            message = ', '.join(sorted(file_formats)) if file_formats else 'No .nd2 or .tif/.tiff files'

            print(f"Folder: {folder_path}, Number of recognized files: {num_files}, File formats: {message}")

            # Keep the folder only if it has at least one recognized file
            if num_files > 0:
                valid_folders.append(folder_path)

        else:
            # For steps other than 1, no specific file check is performed here
            valid_folders.append(folder_path)

    # After collecting valid_folders in step 1,
    # steps 2, 3, and 4 perform more detailed checks or set up logs
    if step == 1:
        # Return the list of valid folders
        result = valid_folders

    elif step == 2:
        # In step 2, we look for the "Nuclei" subfolder inside each valid folder's "foci_assay"
        nuclei_folders = []
        for folder in valid_folders:
            # Set up logging
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
                logging.error(f"Nuclei folder not found in '{folder}/foci_assay'.")

        result = nuclei_folders

    elif step == 3:
        # In step 3, we gather details for the 'foci_assay' folder, 
        # checking 'Foci' and the latest 'Nuclei_StarDist_mask_processed_<timestamp>' subfolder
        result = {}
        for folder in valid_folders:
            # Set up logging
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
                logging.error(f"Subfolder 'foci_assay' not found in folder '{folder}'. Skipping this folder.")
                continue
            else:
                result[folder]["foci_assay_folder"] = foci_assay_folder

            # Check for 'Foci' subfolder
            foci_folder = os.path.join(foci_assay_folder, 'Foci')
            if not os.path.exists(foci_folder):
                logging.error(f"Subfolder 'Foci' not found in folder '{foci_assay_folder}'. Skipping this folder.")
            else:
                result[folder]["foci_folder"] = foci_folder

            # Look for the latest 'Nuclei_StarDist_mask_processed_<timestamp>'
            processed_folders = []
            for name in os.listdir(foci_assay_folder):
                if name.startswith('Nuclei_StarDist_mask_processed_'):
                    timestamp_str = name.replace('Nuclei_StarDist_mask_processed_', '')
                    timestamp = datetime.strptime(timestamp_str, '%Y%m%d_%H%M%S')
                    processed_folders.append((timestamp, os.path.join(foci_assay_folder, name)))

            if len(processed_folders) == 0:
                logging.error(f"No folders found starting with 'Nuclei_StarDist_mask_processed_' in '{foci_assay_folder}'. Skipping.")
            else:
                # Select the latest folder
                latest_processed_folder = max(processed_folders, key=lambda x: x[0])[1]
                print(f"Found the latest folder 'Nuclei_StarDist_mask_processed_': {latest_processed_folder}")
                result[folder]["nuclei_folder"] = latest_processed_folder

            # Check for files in 'Foci'
            if "foci_folder" in result[folder]:
                foci_files = [f for f in os.listdir(result[folder]["foci_folder"])
                              if f.lower().endswith('.tif')]
                if len(foci_files) == 0:
                    logging.error("No '.tif' files found in folder 'Foci'.")
                else:
                    result[folder]["foci_files"] = foci_files

            # Check for files in the latest 'Nuclei_StarDist_mask_processed_<timestamp>'
            if "nuclei_folder" in result[folder]:
                nuclei_files = [f for f in os.listdir(result[folder]["nuclei_folder"])
                                if f.lower().endswith('.tif')]
                if len(nuclei_files) == 0:
                    logging.error(f"No '.tif' files found in folder '{result[folder]['nuclei_folder']}'.")
                else:
                    result[folder]["nuclei_files"] = nuclei_files

            # Print information about found files
            if "foci_folder" in result[folder]:
                foci_files = result[folder].get("foci_files", [])
                print(f"\n--- File information in folder '{foci_assay_folder}' ---")
                print(f"Number of files in 'Foci': {len(foci_files)}. "
                      f"Data types: {set(os.path.splitext(f)[-1] for f in foci_files)}")

            if "nuclei_folder" in result[folder]:
                nuclei_files = result[folder].get("nuclei_files", [])
                print(f"Number of files in 'Nuclei_StarDist_mask_processed_': {len(nuclei_files)}. "
                      f"Data types: {set(os.path.splitext(f)[-1] for f in nuclei_files)}")

        result = result

    elif step == 4:
        # In step 4, we search for 'Final_Nuclei_Mask_YYYYMMDD_HHMMSS' and 'Foci_Mask_YYYYMMDD_HHMMSS'
        result = {}
        for folder in valid_folders:
            # Set up logging
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
                logging.error(f"Subdirectory 'foci_assay' not found in folder '{folder}'. Skipping.")
                continue
            else:
                result[folder]['foci_assay_folder'] = foci_assay_folder

            # Look for 'Final_Nuclei_Mask_YYYYMMDD_HHMMSS'
            final_nuclei_folders = []
            for name in os.listdir(foci_assay_folder):
                if name.startswith('Final_Nuclei_Mask_'):
                    date_str = name.replace('Final_Nuclei_Mask_', '')
                    folder_datetime = datetime.strptime(date_str, '%Y%m%d_%H%M%S')
                    final_nuclei_folders.append((folder_datetime, os.path.join(foci_assay_folder, name)))

            if len(final_nuclei_folders) == 0:
                logging.error(f"No folders 'Final_Nuclei_Mask_YYYYMMDD_HHMMSS' found in '{foci_assay_folder}'.")
            else:
                # Take the latest
                lat_fin_nuc_mask_dir = max(final_nuclei_folders, key=lambda x: x[0])[1]
                print(f"Selected the latest 'Final_Nuclei_Mask' folder: {lat_fin_nuc_mask_dir}")
                result[folder]['final_nuclei_mask_folder'] = lat_fin_nuc_mask_dir

                # Count files ending with '_StarDist_processed.tif'
                star_dist_proc_files = [
                    f for f in os.listdir(lat_fin_nuc_mask_dir)
                    if f.endswith('_StarDist_processed.tif')
                ]
                star_dist_count = len(star_dist_proc_files)
                print(f"Number of '_StarDist_processed.tif' files in '{lat_fin_nuc_mask_dir}': {star_dist_count}")
                result[folder]['star_dist_files'] = star_dist_proc_files
                result[folder]['star_dist_count'] = star_dist_count

            # Look for 'Foci_Mask_YYYYMMDD_HHMMSS'
            foci_masks_folders = []
            for name in os.listdir(foci_assay_folder):
                if name.startswith('Foci_Mask_'):
                    date_str = name.replace('Foci_Mask_', '')
                    folder_datetime = datetime.strptime(date_str, '%Y%m%d_%H%M%S')
                    foci_masks_folders.append((folder_datetime, os.path.join(foci_assay_folder, name)))

            if len(foci_masks_folders) == 0:
                logging.error(f"No folders 'Foci_Mask_YYYYMMDD_HHMMSS' found in '{foci_assay_folder}'.")
            else:
                # Take the latest
                latest_foci_masks_folder = max(foci_masks_folders, key=lambda x: x[0])[1]
                print(f"Selected the latest 'Foci_Mask' folder: {latest_foci_masks_folder}")
                result[folder]['foci_masks_folder'] = latest_foci_masks_folder

                # Count files ending with '_foci_projection.tif'
                foci_projection_files = [
                    f for f in os.listdir(latest_foci_masks_folder)
                    if f.endswith('_foci_projection.tif')
                ]
                foci_projection_count = len(foci_projection_files)
                print(f"Number of '_foci_projection.tif' files in '{latest_foci_masks_folder}': {foci_projection_count}")
                result[folder]['foci_projection_files'] = foci_projection_files
                result[folder]['foci_projection_count'] = foci_projection_count
                print(f"  Found '_StarDist_processed.tif' files: {star_dist_count}")
                print(f"  Found '_foci_projection.tif' files: {foci_projection_count}")

        result = result

    else:
        raise ValueError('Invalid step. Please choose a step number from 1 to 4.')

    return result
