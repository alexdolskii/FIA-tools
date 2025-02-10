#!/usr/bin/env python3

import argparse
import logging
import os
from pathlib import Path

# Увеличиваем память для JVM
os.environ['_JAVA_OPTIONS'] = (
    "-Xmx16g "                # До 16 ГБ оперативной памяти
    "-XX:+IgnoreUnrecognizedVMOptions "  
    "--illegal-access=warn "
    "--add-opens=java.base/java.lang=ALL-UNNAMED "
)

import imagej
from scyjava import jimport
from validate_folders import validate_path_files


def process_image(valid_folders: list) -> None:
    """
    Process all files from the provided directories (.nd2 or .tif/.tiff)
    according to user-selected nuclei and foci channels.

    - For ND2 files (3D stacks):
        * Nuclei -> Max Intensity Z-projection
        * Foci   -> Standard Deviation Z-projection for each specified channel
    - For TIF/TIFF (already 2D multi-channel):
        * Nuclei -> ChannelSplitter channel for user input
        * Foci   -> ChannelSplitter channel for each specified channel

    In addition, the script creates a text file (image_metadata.txt)
    in the 'foci_assay' folder, listing image calibration properties
    and dimension info for each processed image.
    """

    # Initialize ImageJ
    print("Initializing ImageJ...")
    ij = imagej.init('sc.fiji:fiji', mode='headless')
    print(f"ImageJ initialization completed. Version: {ij.getVersion()}")

    # Import Java classes
    IJ = jimport('ij.IJ')
    ZProjector = jimport('ij.plugin.ZProjector')
    ChannelSplitter = jimport('ij.plugin.ChannelSplitter')

    # Request channel number for Nuclei (1-based)
    nuclei_channel = int(input("Enter the channel number for nuclei staining (starting from 1): "))
    if nuclei_channel not in range(1, 13):
        raise ValueError("Invalid channel number for Nuclei (must be 1–12).")

    # Request the number of Foci channels to process
    num_foci_channels = int(input("How many Foci channels do you want to process? "))
    if num_foci_channels < 1:
        raise ValueError("Number of Foci channels must be at least 1.")

    # Request channel numbers for each Foci (1-based)
    foci_channels = []
    for i in range(num_foci_channels):
        channel = int(input(f"Enter the channel number for Foci {i + 1} (starting from 1): "))
        if channel not in range(1, 13):
            raise ValueError(f"Invalid channel number for Foci {i + 1} (must be 1–12).")
        foci_channels.append(channel)

    # Process images in each folder
    for input_folder in valid_folders:
        # Create a new folder 'foci_assay' for processed images
        processed_folder = os.path.join(input_folder, 'foci_assay')
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
        log_file = os.path.join(processed_folder, '1_log.txt')
        file_handler = logging.FileHandler(log_file, mode='w')
        file_handler.setLevel(logging.WARNING)
        file_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
        logging.getLogger('').addHandler(file_handler)

        # Create subfolder for Nuclei
        nuclei_folder = os.path.join(processed_folder, "Nuclei")
        Path(nuclei_folder).mkdir(parents=True, exist_ok=True)
        print(f"Subfolder 'Nuclei' created in {processed_folder}")

        # Create subfolders for each Foci channel
        foci_folders = {}
        for i, channel in enumerate(foci_channels):
            folder_name = os.path.join(processed_folder, "Foci", f"Foci_{i + 1}_Channel_{channel}")
            Path(folder_name).mkdir(parents=True, exist_ok=True)
            foci_folders[channel] = folder_name
            print(f"Subfolder 'Foci_{i + 1}_Channel_{channel}' created in {processed_folder}")

        # Create or open the metadata file in append mode
        metadata_file_path = os.path.join(processed_folder, 'image_metadata.txt')
        metadata_file = open(metadata_file_path, mode='w', encoding='utf-8')
        metadata_file.write("Image Metadata:\n")
        metadata_file.write("================\n")

        # Part 1: Image processing
        print("\nStarting Part 1: Image processing...")

        # Valid file extensions
        valid_exts = ('.nd2', '.tif', '.tiff')

        for filename in os.listdir(input_folder):
            # Skip hidden files and files starting with "._"
            if filename.startswith('.') or filename.startswith('._'):
                logging.warning(f"Skipping hidden or dot-underscore file: {filename}")
                continue

            # Check file extension
            file_ext = filename.lower()
            if not file_ext.endswith(valid_exts):
                # If file is not ND2 nor TIF/TIFF, skip
                logging.error(f"Skipping '{filename}' (unsupported format).")
                continue

            file_path = os.path.join(input_folder, filename)
            print(f"\nProcessing file: {file_path}")

            # Close any images left open
            IJ.run("Close All")

            # Open the image
            imp = IJ.openImage(file_path)
            if imp is None:
                logging.warning(f"Failed to open image: {file_path}. "
                                f"Check Bio-Formats or file integrity.")
                continue

            # Gather dimension info
            width, height, channels, slices, frames = imp.getDimensions()
            print(f"Image dimensions for '{filename}': "
                  f"W={width}, H={height}, C={channels}, Z={slices}, T={frames}")

            # ---------------------------------------------------
            # WRITE METADATA TO THE TEXT FILE
            # ---------------------------------------------------
            # Retrieve calibration info
            cal = imp.getCalibration()
            pixel_width = cal.pixelWidth
            pixel_height = cal.pixelHeight
            pixel_depth = cal.pixelDepth
            unit = cal.getUnit()  # e.g. "micron"

            # Write an entry for this image to the metadata file
            metadata_file.write(f"Image Name: {filename}\n")
            metadata_file.write(f"  Pixel Width: {pixel_width}\n")
            metadata_file.write(f"  Pixel Height: {pixel_height}\n")
            metadata_file.write(f"  Pixel Depth: {pixel_depth}\n")
            metadata_file.write(f"  Unit: {unit}\n")
            metadata_file.write(f"  Channels: {channels}\n")
            metadata_file.write(f"  Slices: {slices}\n")
            metadata_file.write(f"  Frames: {frames}\n\n")
            metadata_file.flush()  # Ensure immediate write

            # For ND2 files, we assume a multi-Z or multi-channel stack
            if file_ext.endswith('.nd2'):
                # Check if channels exist
                if nuclei_channel > channels or any(foci_channel > channels for foci_channel in foci_channels):
                    logging.error(f"Specified channels exceed available ({channels}) in '{filename}'.")
                    imp.close()
                    continue

                # ----- Process NUCLEI (ND2): Max Z-projection -----
                print(f"Processing nuclei channel {nuclei_channel} in ND2 file.")
                imp.setC(nuclei_channel)
                IJ.run(imp, "Duplicate...", f"title=imp_nuclei duplicate channels={nuclei_channel}")
                imp_nuclei = IJ.getImage()

                zp_nuclei = ZProjector(imp_nuclei)
                zp_nuclei.setMethod(ZProjector.MAX_METHOD)
                zp_nuclei.doProjection()
                nuclei_proj = zp_nuclei.getProjection()

                # Resize & convert to 8-bit
                nuclei_proj = nuclei_proj.resize(1024, 1024, 1, "bilinear")
                IJ.run(nuclei_proj, "8-bit", "")

                # Save
                base_name = os.path.splitext(filename)[0]
                nuclei_out = os.path.join(nuclei_folder, f"{base_name}_nuclei_projection.tif")
                IJ.saveAs(nuclei_proj, "Tiff", nuclei_out)
                print(f"Nuclei (Max Z) saved to '{nuclei_out}'")

                nuclei_proj.close()
                imp_nuclei.close()

                # ----- Process FOCI (ND2): SD Z-projection for each channel -----
                for foci_channel in foci_channels:
                    print(f"Processing foci channel {foci_channel} in ND2 file.")
                    imp.setC(foci_channel)
                    IJ.run(imp, "Duplicate...", f"title=imp_foci duplicate channels={foci_channel}")
                    imp_foci = IJ.getImage()

                    zp_foci = ZProjector(imp_foci)
                    zp_foci.setMethod(ZProjector.SD_METHOD)
                    zp_foci.doProjection()
                    foci_proj = zp_foci.getProjection()

                    # Resize & convert
                    foci_proj = foci_proj.resize(1024, 1024, 1, "bilinear")
                    IJ.run(foci_proj, "8-bit", "")

                    # Save to the corresponding Foci folder
                    foci_out = os.path.join(foci_folders[foci_channel], f"{base_name}_foci_projection.tif")
                    IJ.saveAs(foci_proj, "Tiff", foci_out)
                    print(f"Foci (SD Z) saved to '{foci_out}'")

                    foci_proj.close()
                    imp_foci.close()

                # Close the original
                imp.close()

            else:
                # For TIF/TIFF, we assume 2D multi-channel images
                # The user wants to skip Z-projection; just separate channels
                # using ChannelSplitter.split()
                print(f"Processing TIF/TIFF file (assumed 2D multi-channel).")

                # Split channels
                splitted_channels = ChannelSplitter.split(imp)
                total_split_channels = len(splitted_channels)
                print(f"Total channels in TIF: {total_split_channels}")

                # Check channel availability
                if nuclei_channel > total_split_channels or any(foci_channel > total_split_channels for foci_channel in foci_channels):
                    logging.error(f"Requested channels exceed total split channels ({total_split_channels}). Skipping.")
                    imp.close()
                    continue

                # ----- Process NUCLEI (TIF) -----
                print(f"Extracting nuclei channel {nuclei_channel} from TIF.")
                imp_nuclei = splitted_channels[nuclei_channel - 1]
                imp_nuclei = imp_nuclei.resize(1024, 1024, 1, "bilinear")
                IJ.run(imp_nuclei, "8-bit", "")

                base_name = os.path.splitext(filename)[0]
                nuclei_out = os.path.join(nuclei_folder, f"{base_name}_nuclei_projection.tif")
                IJ.saveAs(imp_nuclei, "Tiff", nuclei_out)
                print(f"Nuclei channel saved to '{nuclei_out}'.")
                imp_nuclei.close()

                # ----- Process FOCI (TIF) -----
                for foci_channel in foci_channels:
                    print(f"Extracting foci channel {foci_channel} from TIF.")
                    imp_foci = splitted_channels[foci_channel - 1]
                    imp_foci = imp_foci.resize(1024, 1024, 1, "bilinear")
                    IJ.run(imp_foci, "8-bit", "")

                    # Save to the corresponding Foci folder
                    foci_out = os.path.join(foci_folders[foci_channel], f"{base_name}_foci_projection.tif")
                    IJ.saveAs(imp_foci, "Tiff", foci_out)
                    print(f"Foci channel saved to '{foci_out}'.")
                    imp_foci.close()

                # Close the original image
                imp.close()

            # Close all images to free memory
            IJ.run("Close All")

        # Close the metadata file after all files in this folder are processed
        metadata_file.close()


def select_channel_name(input_json_path: str) -> None:
    """
    Reads the JSON file to get valid folders, then prompts the user
    for channel numbers, and processes ND2/TIF images.
    """
    # Set up logging
    logging.basicConfig(level=logging.WARNING,
                        format='%(asctime)s - %(levelname)s - %(message)s')

    # Validate input directories from the JSON file
    valid_folders = validate_path_files(input_json_path, 1)

    # Confirm whether the user wants to start analysis
    start_analysis = input("Start analyzing files in the specified folders? (yes/no): ").strip().lower()
    if start_analysis in ('no', 'n'):
        raise ValueError("Analysis canceled by user.")
    elif start_analysis not in ('yes', 'y', 'no', 'n'):
        raise ValueError("Incorrect input. Please enter yes/no")

    # Process images
    process_image(valid_folders)
    print("\nPart 1 successfully completed.")


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('-i',
                        '--input',
                        type=str,
                        help="JSON file with all paths of directories",
                        required=True)
    args = parser.parse_args()
    select_channel_name(args.input)