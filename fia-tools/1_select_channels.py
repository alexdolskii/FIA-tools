#!/usr/bin/env python

import argparse
import hashlib
import os
from pathlib import Path
from uuid import uuid4

import fiji_config
import imagej
import spatial_calibration as spatial
from channel_run_log import ChannelRunLog
from scyjava import jimport
from validate_folders import validate_input_file

# Increase memory limit for JVM
os.environ['_JAVA_OPTIONS'] = (
    "-Xmx16g "                # up to 16GB of memory
    "-XX:+IgnoreUnrecognizedVMOptions "
    "--illegal-access=warn "
    "--add-opens=java.base/java.lang=ALL-UNNAMED "
)


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
        ij = imagej.init(fiji_config.FIJI_ENDPOINT, mode='headless')
    except Exception as e:
        raise ImageJInitializationError(
            f"Failed to initialize ImageJ: {e}")
    print(f"ImageJ initialization completed. Version: {ij.getVersion()}")
    return ij


def validate_folders(input_json_path: str) -> list:
    folder_paths = validate_input_file(input_json_path)
    valid_folders = []
    for folder_path in folder_paths:
        if not os.path.exists(folder_path):
            raise ValueError(f"Folder '{folder_path}' does not exist.")
        valid_extensions = {'.nd2', '.tif', '.tiff'}
        # Skip hidden files (starting with .)
        # and macOS temp files (starting with ._)
        all_files = [f for f in os.listdir(folder_path)
                     if os.path.isfile(os.path.join(folder_path, f))
                     and not f.startswith('.') and not f.startswith('._')]
        recognized_files = [
            f for f in all_files
            if os.path.splitext(f)[1].lower() in valid_extensions
        ]
        num_files = len(recognized_files)
        file_formats = set(os.path.splitext(f)[1].lower()
                           for f in recognized_files)
        message = (', '.join(sorted(file_formats))
                   if file_formats else
                   'No .nd2 or .tif/.tiff files')
        print(f"Folder: {folder_path}, "
              f"Number of recognized files: {num_files}, "
              f"File formats: {message}")
        if num_files > 0:
            valid_folders.append(folder_path)
    return valid_folders


def select_settings():
    """Request shared channel settings once for the batch."""
    # Request file type
    print("\nSelect input file type:")
    print("1. ND2 files (multi-channel Z-stacks)")
    print("2. Multi-channel TIFF files with Z-stacks")
    print("3. 2D multi-channel TIFF files (already projections)")
    file_type = int(input("Enter choice (1-3): "))
    if file_type not in [1, 2, 3]:
        raise ValueError("Invalid file type selection (must be 1-3).")

    # Request the nuclei segmentation channel (1-based).
    nuclei_channel = int(input("Enter the nuclei segmentation "
                               "channel number (starting from 1): "))
    if nuclei_channel not in range(1, 13):
        raise ValueError("Invalid channel number for Nuclei (must be 1-12).")

    # Marker channels serve either the foci or nuclear-intensity workflow.
    print("Marker channels can be used for foci analysis or nuclear marker-intensity measurements.")
    num_foci_channels = int(input("How many marker "
                                  "channels do you want to process? "))
    if num_foci_channels < 1:
        raise ValueError("Number of marker "
                         "channels must be at least 1.")

    # Request channel numbers for each marker (1-based).
    foci_channels = []
    for i in range(num_foci_channels):
        channel = int(input(f"Enter the channel "
                            f"number for marker {i + 1} "
                            f"(starting from 1): "))
        if channel not in range(1, 13):
            raise ValueError(f"Invalid channel "
                             f"number for marker {i + 1} "
                             f"(must be 1-12).")
        foci_channels.append(channel)

    return file_type, nuclei_channel, foci_channels


def confirm_output_folders(valid_folders):
    """Confirm every existing output folder before initializing or writing anything."""
    output_folders = [Path(folder) / 'foci_assay' for folder in valid_folders]
    existing = []
    print("\nChecking output folders before analysis:")
    for folder in output_folders:
        if folder.exists():
            if not folder.is_dir():
                raise NotADirectoryError(f"Output path is not a directory: {folder}")
            existing.append(folder)
            print(f"Existing results: {folder}")
        else:
            print(f"New output folder: {folder}")

    for folder in existing:
        while True:
            response = input(
                f"The folder {folder} already exists. "
                "Do you want to overwrite existing results? (yes/no): "
            ).strip().lower()
            if response in ('yes', 'y'):
                break
            if response in ('no', 'n'):
                raise ValueError("Analysis canceled by user before processing; existing results were not changed.")
            print("Please enter yes or no.")


def process_image(valid_folders: list, input_json_path=None) -> list:
    """
    Process all files from the provided directories (.nd2 or .tif/.tiff)
    according to user-selected nuclei segmentation and marker channels.

    Three types of input files are supported:
    1. ND2 files (multi-channel Z-stacks)
        * Nuclei -> Max Intensity Z-projection
        * Markers -> Standard Deviation Z-projection for each specified channel
    2. Multi-channel TIFF files with Z-stacks (similar to ND2 structure)
        * Same processing as ND2 files
    3. 2D multi-channel TIFF files (already projections)
        * Nuclei -> ChannelSplitter channel for user input
        * Markers -> ChannelSplitter channel for each specified channel

    Prepared marker images support foci analysis. Nuclear-intensity measurements
    use the selected channels in the original images, not these prepared pixels.

    Creates a text file (image_metadata.txt) in the 'foci_assay' folder,
    listing image calibration properties and dimension
    info for each processed image.
    """

    if not valid_folders:
        raise ValueError("No supported input images were found; analysis was not started.")
    confirm_output_folders(valid_folders)
    settings = None
    run_id = uuid4().hex
    script_digest = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    statuses = []

    # Process images in each folder
    for input_folder in valid_folders:
        # Create a new folder 'foci_assay' for processed images
        processed_folder = os.path.join(input_folder,
                                        'foci_assay')
        Path(processed_folder).mkdir(parents=True, exist_ok=True)
        print(f"\nProcessed images will be saved in: {processed_folder}")

        with ChannelRunLog(input_folder, input_json_path, run_id, script_digest) as run:
            run.inventory()
            if settings is None:
                run.logger.info("Initializing ImageJ...")
                ij = initialize_imagej()
                IJ = jimport('ij.IJ')
                ZProjector = jimport('ij.plugin.ZProjector')
                ChannelSplitter = jimport('ij.plugin.ChannelSplitter')
                settings = select_settings()
            file_type, nuclei_channel, foci_channels = settings
            run.logger.info("RUNTIME | ImageJ=%s", ij.getVersion())
            run.logger.info("SETTINGS | input_type=%s | nuclei_channel=%d | marker_channels=%s",
                            {1: 'ND2 Z-stack', 2: 'TIFF Z-stack', 3: '2D TIFF'}[file_type],
                            nuclei_channel, foci_channels)
            # Create subfolder for Nuclei
            nuclei_folder = os.path.join(processed_folder, "Nuclei")
            Path(nuclei_folder).mkdir(parents=True, exist_ok=True)
            print(f"Subfolder 'Nuclei' created in {processed_folder}")

            # Retain Foci folder names for compatibility with both workflows.
            foci_folders = {}
            for i, channel in enumerate(foci_channels):
                folder_name = os.path.join(processed_folder,
                                           "Foci",
                                           f"Foci_{i + 1}_Channel_{channel}")
                Path(folder_name).mkdir(parents=True, exist_ok=True)
                foci_folders[channel] = folder_name
                print(f"Subfolder "
                      f"'Foci_{i + 1}_Channel_{channel}' "
                      f"created in {processed_folder}")

            # Record source metadata separately from the run journal
            metadata_file_path = os.path.join(processed_folder,
                                              'image_metadata.txt')
            with open(metadata_file_path, mode="w", encoding="utf-8") as metadata_file:
                metadata_file.write("Image Metadata:\n")
                metadata_file.write("================\n")

                # Part 1: Image processing
                print("\nStarting Part 1: Image processing...")

                for filename in run.files:
                    file_ext = os.path.splitext(filename)[1].lower()
                    run.start_image(filename)
                    file_path = os.path.join(input_folder, filename)
                    print(f"\nProcessing file: {file_path}")

                    # Close any images left open
                    IJ.run("Close All")

                    # Open the image
                    imp = IJ.openImage(file_path)
                    if imp is None:
                        run.fail_image("Failed to open image. Check Bio-Formats or file integrity.")
                        continue

                    # Gather dimension info
                    width, height, channels, slices, frames = imp.getDimensions()
                    run.logger.info("DIMENSIONS | file=%s | W=%d | H=%d | C=%d | Z=%d | T=%d",
                                    filename, width, height, channels, slices, frames)
                    print(f"Image dimensions for '{filename}': "
                          f"W={width}, "
                          f"H={height}, "
                          f"C={channels}, "
                          f"Z={slices}, "
                          f"T={frames}")

                    # ---------------------------------------------------
                    # WRITE METADATA TO THE TEXT FILE
                    # ---------------------------------------------------
                    # Retrieve calibration info
                    original_calibration = imp.getCalibration().copy()
                    try:
                        calibration, original_shape = spatial.read_source(file_path)
                        if original_shape != (height, width):
                            calibration = spatial.uncalibrated('Metadata and opened image dimensions differ.')
                    except Exception as error:
                        calibration = spatial.uncalibrated(f'Cannot read source calibration: {error}')
                    spatial.imagej_calibration(imp, calibration, jimport)
                    pixel_width = calibration['Pixel_size_X_um'] or 1.0
                    pixel_height = calibration['Pixel_size_Y_um'] or 1.0
                    z_cal = spatial.normalize(original_calibration.pixelDepth, original_calibration.pixelDepth, original_calibration.getZUnit())
                    pixel_depth = z_cal["Pixel_size_X_um"] if spatial.calibrated(z_cal) else original_calibration.pixelDepth
                    z_unit = "um" if spatial.calibrated(z_cal) else original_calibration.getZUnit()
                    unit = 'um' if spatial.calibrated(calibration) else 'pixel'
                    if spatial.calibrated(calibration):
                        run.logger.info("CALIBRATION | file=%s | status=calibrated | "
                                        "pixel_X_um=%s | pixel_Y_um=%s", filename, pixel_width, pixel_height)
                    else:
                        run.logger.warning("CALIBRATION | file=%s | status=uncalibrated | units=pixel | reason=%s",
                                           filename, calibration['Calibration_note'])

                    # Write an entry for this image to the metadata file
                    metadata_file.write(f"Image Name: {filename}\n")
                    metadata_file.write(f"  Width: {width}\n")
                    metadata_file.write(f"  Height: {height}\n")
                    metadata_file.write("  XY processing: native dimensions; no resizing\n")
                    metadata_file.write(f"  Pixel Width: {pixel_width}\n")
                    metadata_file.write(f"  Pixel Height: {pixel_height}\n")
                    metadata_file.write(f"  Pixel Depth: {pixel_depth}\n")
                    metadata_file.write(f"  Z Unit: {z_unit}\n")
                    metadata_file.write(f"  Unit: {unit}\n")
                    metadata_file.write(f"  Channels: {channels}\n")
                    metadata_file.write(f"  Slices: {slices}\n")
                    metadata_file.write(f"  Frames: {frames}\n\n")
                    metadata_file.flush()  # Ensure immediate write

                    # For ND2 files or Z-stack TIFFs (file types 1 and 2)
                    if (file_ext == '.nd2' or (file_ext in ('.tif', '.tiff')
                                               and file_type in (1, 2))):
                        run.logger.info("PROCESSING | file=%s | nuclei_projection=MAX | marker_projection=SD | "
                                        "output_bit_depth=8 | resize=False", filename)
                        # Check if channels exist
                        if (nuclei_channel > channels
                                or any(foci_channel > channels
                                       for foci_channel in foci_channels)):
                            run.fail_image(f"Specified channels exceed available ({channels}).")
                            imp.close()
                            continue

                        # ----- Process NUCLEI: Max Z-projection -----
                        print(f"Processing nuclei channel "
                              f"{nuclei_channel} as Max Z-projection.")
                        imp.setC(nuclei_channel)
                        IJ.run(imp, "Duplicate...",
                               f"title=imp_nuclei duplicate channels={nuclei_channel}")
                        imp_nuclei = IJ.getImage()

                        zp_nuclei = ZProjector(imp_nuclei)
                        zp_nuclei.setMethod(ZProjector.MAX_METHOD)
                        zp_nuclei.doProjection()
                        nuclei_proj = zp_nuclei.getProjection()

                        # Preserve native XY dimensions; convert segmentation input to 8-bit.
                        IJ.run(nuclei_proj, "8-bit", "")

                        # Save
                        base_name = os.path.splitext(filename)[0]
                        nuclei_out = os.path.join(nuclei_folder,
                                                  f"{base_name}_nuclei_projection.tif")
                        spatial.imagej_calibration(nuclei_proj, calibration, jimport)
                        IJ.saveAs(nuclei_proj, "Tiff", nuclei_out)
                        spatial.save_snapshot(nuclei_out, calibration, (height, width), file_path)
                        run.saved(nuclei_out, (height, width))
                        print(f"Nuclei (Max Z) saved to '{nuclei_out}'")

                        nuclei_proj.close()
                        imp_nuclei.close()

                        # Prepare marker channels as SD Z-projections.
                        for foci_channel in foci_channels:
                            print(f"Processing marker channel "
                                  f"{foci_channel} as SD Z-projection.")
                            imp.setC(foci_channel)
                            IJ.run(imp, "Duplicate...",
                                   f"title=imp_foci duplicate channels={foci_channel}")
                            imp_foci = IJ.getImage()

                            zp_foci = ZProjector(imp_foci)
                            zp_foci.setMethod(ZProjector.SD_METHOD)
                            zp_foci.doProjection()
                            foci_proj = zp_foci.getProjection()

                            # Preserve native XY dimensions; convert segmentation input to 8-bit.
                            IJ.run(foci_proj, "8-bit", "")

                            # Save to the corresponding marker folder.
                            foci_out = os.path.join(foci_folders[foci_channel],
                                                    f"{base_name}_foci_projection.tif")
                            spatial.imagej_calibration(foci_proj, calibration, jimport)
                            IJ.saveAs(foci_proj, "Tiff", foci_out)
                            spatial.save_snapshot(foci_out, calibration, (height, width), file_path)
                            run.saved(foci_out, (height, width))
                            print(f"Marker (SD Z) saved to '{foci_out}'")

                            foci_proj.close()
                            imp_foci.close()

                        # Close the original
                        imp.close()

                    else:
                        run.logger.info("PROCESSING | file=%s | projection=none (2D channel extraction) | "
                                        "output_bit_depth=8 | resize=False", filename)
                        # For 2D multi-channel TIFF files (file type 3)
                        print("Processing as 2D multi-channel TIFF file.")

                        # Split channels
                        splitted_channels = ChannelSplitter.split(imp)
                        total_split_channels = len(splitted_channels)
                        print(f"Total channels in TIFF: {total_split_channels}")

                        # Check channel availability
                        if (nuclei_channel > total_split_channels
                                or any(foci_channel > total_split_channels
                                       for foci_channel in foci_channels)):
                            run.fail_image(f"Requested channels exceed total split channels ({total_split_channels}).")
                            imp.close()
                            continue

                        # ----- Process NUCLEI (2D TIFF) -----
                        print(f"Extracting nuclei channel "
                              f"{nuclei_channel} from 2D TIFF.")
                        imp_nuclei = splitted_channels[nuclei_channel - 1]
                        IJ.run(imp_nuclei, "8-bit", "")

                        base_name = os.path.splitext(filename)[0]
                        nuclei_out = os.path.join(nuclei_folder,
                                                  f"{base_name}_nuclei_projection.tif")
                        spatial.imagej_calibration(imp_nuclei, calibration, jimport)
                        IJ.saveAs(imp_nuclei, "Tiff", nuclei_out)
                        spatial.save_snapshot(nuclei_out, calibration, (height, width), file_path)
                        run.saved(nuclei_out, (height, width))
                        print(f"Nuclei channel saved to '{nuclei_out}'.")
                        imp_nuclei.close()

                        # ----- Prepare marker channels (2D TIFF) -----
                        for foci_channel in foci_channels:
                            print(f"Extracting marker channel "
                                  f"{foci_channel} from 2D TIFF.")
                            imp_foci = splitted_channels[foci_channel - 1]
                            IJ.run(imp_foci, "8-bit", "")

                            # Save to the corresponding marker folder.
                            foci_out = os.path.join(foci_folders[foci_channel],
                                                    f"{base_name}_foci_projection.tif")
                            spatial.imagej_calibration(imp_foci, calibration, jimport)
                            IJ.saveAs(imp_foci, "Tiff", foci_out)
                            spatial.save_snapshot(foci_out, calibration, (height, width), file_path)
                            run.saved(foci_out, (height, width))
                            print(f"Marker channel saved to '{foci_out}'.")
                            imp_foci.close()

                        # Close the original image
                        imp.close()

                    # Close all images to free memory
                    IJ.run("Close All")

                    run.finish_image()
        statuses.append(run.status)
    return statuses


def select_channel_name(input_json_path: str) -> None:
    """
    Reads the JSON file to get valid folders, then prompts the user
    for file type and channel numbers, and processes images accordingly.
    """
    # Validate input directories from the JSON file
    valid_folders = validate_folders(input_json_path)

    # Confirm whether the user wants to start analysis
    start_analysis = input("Start analyzing "
                           "files in the specified folders? "
                           "(yes/no): ").strip().lower()
    if start_analysis in ('no', 'n'):
        raise ValueError("Analysis canceled by user.")
    elif start_analysis not in ('yes', 'y', 'no', 'n'):
        raise ValueError("Incorrect input. Please enter yes/no")

    # Process images
    statuses = process_image(valid_folders, input_json_path)
    if all(status == "SUCCESS" for status in statuses):
        print("\nPart 1 successfully completed.")
    else:
        print("\nPart 1 finished with incomplete results. Check each folder's 1_log.log.")


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('-i',
                        '--input',
                        type=str,
                        help="JSON file with all paths of directories",
                        required=True)
    args = parser.parse_args()
    select_channel_name(args.input)
