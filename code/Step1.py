import imagej
import os
import json

from pathlib import Path
from scyjava import jimport


def validate_path_files(input_json_path: str) -> list:
    """
    Function validate_path_files read json file with the path
    to files and check if they all exist
    Args:
        input_json_path: json with paths to directories with files

    Returns:
        Collection with validated files
    """

    # Step 1: Request file with folder paths
    # input_txt_path = input("Enter the full path to the .txt file containing a list of folder paths: ")

    # Check if the file exists
    if not os.path.exists(input_json_path):
        ValueError(f"File '{input_json_path}' does not exist. Please try again.")

    # Read folder paths from file
    with open(input_json_path, 'r') as file:
        paths_dict = json.load(file)
        folder_paths = paths_dict["paths_to_files"]

    # Check existence of folders and count the number of files in each
    print(f"Found {len(folder_paths)} folders for verification.")
    valid_folders = []
    for folder_path in folder_paths:
        if os.path.exists(folder_path):
            files = [f for f in os.listdir(folder_path) if f.lower().endswith('.nd2')]
            num_files = len(files)
            file_formats = set(os.path.splitext(f)[1] for f in files)
            print(f"Folder: {folder_path}, Number of files: {num_files}, File formats: {', '.join(file_formats) if file_formats else 'no .nd2 files'}")
            if num_files > 0:
                valid_folders.append(folder_path)
        else:
            ValueError(f"Folder '{folder_path}' does not exist.")
    return valid_folders


def process_image(valid_folders: list) -> None:
    """
    Function that process all files from provided directories
    and merge the collection of images in a single one.
    Args:
        valid_folders: list of paths to directories
        with images of main interest

    Returns:
        Processed in new directory "foci_assay" that is created
        in each provided path
    """
    # Initialize ImageJ
    print("Initializing ImageJ...")
    ij = imagej.init('sc.fiji:fiji', mode='headless')
    print("ImageJ initialization completed.")

    # Import Java classes
    IJ = jimport('ij.IJ')
    ImagePlus = jimport('ij.ImagePlus')
    ZProjector = jimport('ij.plugin.ZProjector')
    Prefs = jimport('ij.Prefs')

    # Request channel numbers from user
    nuclei_channel = int(input("Enter the channel number for "
                               "nuclei staining (starting from 1): "))
    foci_channel = int(input("Enter the channel number for "
                             "foci staining (starting from 1): "))
    if (nuclei_channel not in range(1, 13) or
            foci_channel not in range(1, 13)):
        ValueError("Invalid channel number input. "
                   "Zero channel should be used as first")

    # Process images in each folder
    for input_folder in valid_folders:
        # Create a new folder 'foci_assay' for processed images
        processed_folder = os.path.join(input_folder, 'foci_assay')
        Path(processed_folder).mkdir(parents=True, exist_ok=True)
        print(f"\nProcessed images will be saved in a new folder: {processed_folder}")

        # Create output folders for images
        nuclei_folder = os.path.join(processed_folder, "Nuclei")
        foci_folder = os.path.join(processed_folder, "Foci")
        Path(nuclei_folder).mkdir(parents=True, exist_ok=True)
        Path(foci_folder).mkdir(parents=True, exist_ok=True)
        print(f"Folders Nuclei and Foci created in {processed_folder}")

        # Part 1: Image processing
        print("\nStarting Part 1: Image processing...")

        for filename in os.listdir(input_folder):
            if not filename.lower().endswith('.nd2'):
                print(f"Skipping file '{filename}', as it is not an .nd2 file.")
                continue

            file_path = os.path.join(input_folder, filename)
            print(f"\nProcessing file: {file_path}")

            # Close all windows before starting processing
            IJ.run("Close All")

            # Attempt to open the image in ImageJ using Bio-Formats
            try:
                # Use Bio-Formats to open .nd2 files
                options = {"open": "Composite"}
                imp = IJ.openImage(file_path)
                if imp is None:
                    print(f"Failed to open image: {file_path}")
                    continue
                else:
                    print(f"Image '{filename}' successfully opened.")
            except Exception as e:
                print(f"Error opening image '{filename}': {e}")
                continue

            # Get image dimensions
            width, height, channels, slices, frames = imp.getDimensions()
            print(f"Image dimensions '{filename}': width={width}, "
                  f"height={height}, channels={channels}, "
                  f"slices={slices}, frames={frames}")

            # Check if specified channels are available
            if nuclei_channel > channels or foci_channel > channels:
                print(f"Specified channels exceed available "
                      f"channels in '{filename}'. Skipping file.")
                imp.close()
                continue

            # Process nuclei channel
            print(f"Processing nuclei channel ({nuclei_channel}) in '{filename}'.")
            imp.setC(nuclei_channel)
            IJ.run(imp, "Duplicate...",
                   f"title=imp_nuclei duplicate channels={nuclei_channel}")
            imp_nuclei = IJ.getImage()

            # Perform maximum intensity Z projection for nuclei
            zp_nuclei = ZProjector(imp_nuclei)
            zp_nuclei.setMethod(ZProjector.MAX_METHOD)
            zp_nuclei.doProjection()
            nuclei_proj = zp_nuclei.getProjection()
            nuclei_proj = nuclei_proj.resize(1024, 1024, 1, "bilinear")
            IJ.run(nuclei_proj, "8-bit", "")  # Convert to grayscale
            nuclei_output_path = os.path.join(nuclei_folder,
                                              f"{os.path.splitext(filename)[0]}"
                                              f"_nuclei_projection.tif")
            IJ.saveAs(nuclei_proj, "Tiff", nuclei_output_path)
            print(f"Nuclei projection saved to '{nuclei_output_path}'.")
            nuclei_proj.close()
            imp_nuclei.close()

            # Process foci channel
            print(f"Processing foci channel ({foci_channel}) in '{filename}'.")
            imp.setC(foci_channel)
            IJ.run(imp, "Duplicate...",
                   f"title=imp_foci duplicate channels={foci_channel}")
            imp_foci = IJ.getImage()

            # Perform standard deviation intensity Z projection for foci
            zp_foci = ZProjector(imp_foci)
            zp_foci.setMethod(ZProjector.SD_METHOD)
            zp_foci.doProjection()
            foci_proj = zp_foci.getProjection()
            foci_proj = foci_proj.resize(1024, 1024, 1, "bilinear")
            IJ.run(foci_proj, "8-bit", "")  # Convert to grayscale
            foci_output_path = os.path.join(foci_folder,
                                            f"{os.path.splitext(filename)[0]}"
                                            f"_foci_projection.tif")
            IJ.saveAs(foci_proj, "Tiff", foci_output_path)
            print(f"Foci projection saved to '{foci_output_path}'")
            foci_proj.close()
            imp_foci.close()

            # Close original image
            imp.close()

            # Close all windows
            IJ.run("Close All")


def main_merge_images(input_json_path: str) -> None:
    """
    Main function that check all provided paths to files and
    merge the collection of images in a single one.
    Args:
        input_json_path: json file with all paths to directories

    Returns:
        Processed in new directory "foci_assay" that is created
        in each provided path
    """
    valid_folders = validate_path_files(input_json_path)

    # Ask user if analysis should start
    start_analysis = input("Start analyzing files in "
                           "the specified folders? (yes/no): ").strip().lower()
    if start_analysis != 'yes':
        ValueError("Analysis canceled by user")
    process_image(valid_folders)
    print("\nPart 1 successfully completed.")
