import json
import os


def validate_input_file(input_json_path: str) -> list:
    """
    Validates paths specified in a JSON file for existence,
    then checks for certain file types depending on the analysis step.

    Args:
        input_json_path (str): Path to a JSON file containing
        folder paths under the key "paths_to_files".

    Returns:
        list (or dict): A list of valid folders or a dictionary
                        of folder information,
                        depending on the step.
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
                         "Please check the file content under "
                         "key 'paths_to_files'.")

    print(f"Found {len(folder_paths)} folders for verification.")
    return folder_paths
