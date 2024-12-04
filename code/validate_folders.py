import json
import os

def validate_path_files(input_json_path: str, step: int) -> list:
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
            elif step == 2:
                valid_folders.append(folder_path)
        else:
            raise ValueError(f"Folder '{folder_path}' does not exist.")
    if step == 1:
        result = valid_folders
    elif step == 2:
        print(f"Found {len(valid_folders)} folders for verification.")

        # Search for Nuclei folder in each folder and determine file types
        nuclei_folders = []
        for folder in valid_folders:
            nuclei_folder = os.path.join(folder, 'foci_assay', 'Nuclei')
            if os.path.exists(nuclei_folder):
                files = os.listdir(nuclei_folder)
                file_formats = set(os.path.splitext(f)[1] for f in files)
                print(f"Nuclei folder found: {nuclei_folder}, File types: {', '.join(file_formats)}")
                nuclei_folders.append(nuclei_folder)
            else:
                raise ValueError(f"Nuclei folder not found in '{folder}/foci_assay'.")
        result = nuclei_folders
    return result
