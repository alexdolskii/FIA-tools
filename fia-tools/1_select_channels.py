#!/usr/bin/env python
from assay_layout import ASSAY_DIR, MARKERS_DIR

import argparse
import hashlib
import os
from pathlib import Path
from uuid import uuid4

import fiji_config
import imagej
import spatial_calibration as spatial
from bioformats_progress import bioformats_progress
from channel_run_log import ChannelRunLog
from interactive_input import Cancelled, ask_integer, ask_yes_no, cancelable
from scyjava import jimport
from terminal_progress import CompactProgress
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
    file_type = ask_integer("Enter choice (1-3; q = cancel): ", 1, 3)

    # Request the nuclei segmentation channel (1-based).
    nuclei_channel = ask_integer("Enter the nuclei segmentation "
                                 "channel number (1-12; q = cancel): ", 1, 12)

    # Marker channels serve either the foci or nuclear-intensity workflow.
    print("Marker channels can be used for foci analysis or nuclear marker-intensity measurements.")
    num_foci_channels = ask_integer("How many marker channels do you want to "
                                    "process? (at least 1; q = cancel): ", 1)

    # Request channel numbers for each marker (1-based).
    foci_channels = []
    for i in range(num_foci_channels):
        channel = ask_integer(f"Enter the channel number for marker {i + 1} "
                              "(1-12; q = cancel): ", 1, 12)
        foci_channels.append(channel)

    return file_type, nuclei_channel, foci_channels


def confirm_output_folders(valid_folders):
    """Return selected/skipped folders, or None for cancellation, before any writes."""
    output_folders = [Path(folder) / ASSAY_DIR for folder in valid_folders]
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

    selected, skipped = [], []
    for index, (input_folder, folder) in enumerate(zip(valid_folders, output_folders), 1):
        if folder not in existing:
            selected.append(input_folder)
            continue
        try:
            overwrite = ask_yes_no(
                f"\n[{index}/{len(valid_folders)}] {folder}\n"
                "Existing results found. Do you want to overwrite prepared-channel results? "
                "(yes/no; q = cancel all): "
            )
        except Cancelled:
            print("Analysis canceled. Existing results preserved; no processing started.")
            return None
        if overwrite:
            selected.append(input_folder)
            print("SELECTED: Prepared-channel results will be overwritten.")
        else:
            skipped.append(input_folder)
            print("SKIPPED: Existing results preserved.")
    return selected, skipped


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

    Creates a text file (image_metadata.txt) in the 'fia_assay' folder,
    listing image calibration properties and dimension
    info for each processed image.
    """

    if not valid_folders:
        raise ValueError("No supported input images were found; analysis was not started.")
    selection = confirm_output_folders(valid_folders)
    if selection is None:
        return []
    selected_folders, skipped_folders = selection
    if not selected_folders:
        print("No folders selected for processing.\nExisting results preserved. Nothing to do.")
        return []
    settings = None
    run_id = uuid4().hex
    script_digest = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    statuses = []

    total = sum(1 for folder in selected_folders for path in Path(folder).iterdir()
                if path.is_file() and not path.name.startswith('.')
                and path.suffix.lower() in ('.nd2', '.tif', '.tiff'))
    print(f"\nReady: {len(selected_folders)} folder(s), {total} images. "
          f"Skipped: {len(skipped_folders)} folder(s).")
    with CompactProgress(total) as progress:
        # Process images in each folder
        for folder_index, input_folder in enumerate(selected_folders, 1):
            # Create a new folder 'fia_assay' for processed images
            processed_folder = os.path.join(input_folder,
                                            ASSAY_DIR)
            Path(processed_folder).mkdir(parents=True, exist_ok=True)
            print(f"\nProcessed images will be saved in: {processed_folder}")

            with ChannelRunLog(input_folder, input_json_path, run_id, script_digest, progress) as run:
                run.inventory()
                progress.logger = run.logger
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
                with bioformats_progress(progress):
                    # Create subfolder for Nuclei
                    nuclei_folder = os.path.join(processed_folder, "Nuclei")
                    Path(nuclei_folder).mkdir(parents=True, exist_ok=True)
                    run.logger.info("Nuclei folder: %s", nuclei_folder)

                    # Channel identifiers are shared by both analysis workflows.
                    foci_folders = {}
                    for i, channel in enumerate(foci_channels):
                        folder_name = os.path.join(processed_folder,
                                                   MARKERS_DIR,
                                                   f"Foci_{i + 1}_Channel_{channel}")
                        Path(folder_name).mkdir(parents=True, exist_ok=True)
                        foci_folders[channel] = folder_name
                        run.logger.info("Marker channel %d folder: %s", channel, folder_name)

                    # Record source metadata separately from the run journal
                    metadata_file_path = os.path.join(processed_folder,
                                                      'image_metadata.txt')
                    with open(metadata_file_path, mode="w", encoding="utf-8") as metadata_file:
                        metadata_file.write("Image Metadata:\n")
                        metadata_file.write("================\n")

                        # Part 1: Image processing
                        progress.group(f"Folder {folder_index}/{len(selected_folders)}: {input_folder}", len(run.files))

                        for filename in run.files:
                            file_ext = os.path.splitext(filename)[1].lower()
                            run.start_image(filename)
                            file_path = os.path.join(input_folder, filename)
                            progress.phase("Opening source")

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
                            # ---------------------------------------------------
                            # WRITE METADATA TO THE TEXT FILE
                            # ---------------------------------------------------
                            progress.phase("Reading calibration")
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
                                progress.phase(f"Nuclei C{nuclei_channel} MAX projection")
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
                                progress.phase("Saving prepared channel")
                                IJ.saveAs(nuclei_proj, "Tiff", nuclei_out)
                                spatial.save_snapshot(nuclei_out, calibration, (height, width), file_path)
                                run.saved(nuclei_out, (height, width))

                                nuclei_proj.close()
                                imp_nuclei.close()

                                # Prepare marker channels as SD Z-projections.
                                for foci_channel in foci_channels:
                                    progress.phase(f"Marker C{foci_channel} SD projection")
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
                                    progress.phase("Saving prepared channel")
                                    IJ.saveAs(foci_proj, "Tiff", foci_out)
                                    spatial.save_snapshot(foci_out, calibration, (height, width), file_path)
                                    run.saved(foci_out, (height, width))

                                    foci_proj.close()
                                    imp_foci.close()

                                # Close the original
                                imp.close()

                            else:
                                run.logger.info("PROCESSING | file=%s | projection=none (2D channel extraction) | "
                                                "output_bit_depth=8 | resize=False", filename)
                                # For 2D multi-channel TIFF files (file type 3)
                                progress.phase("Extracting 2D channels")

                                # Split channels
                                splitted_channels = ChannelSplitter.split(imp)
                                total_split_channels = len(splitted_channels)
                                run.logger.info("Split channels: %d", total_split_channels)

                                # Check channel availability
                                if (nuclei_channel > total_split_channels
                                        or any(foci_channel > total_split_channels
                                               for foci_channel in foci_channels)):
                                    run.fail_image(f"Requested channels exceed total split channels ({total_split_channels}).")
                                    imp.close()
                                    continue

                                # ----- Process NUCLEI (2D TIFF) -----
                                progress.phase(f"Extracting nuclei C{nuclei_channel}")
                                imp_nuclei = splitted_channels[nuclei_channel - 1]
                                IJ.run(imp_nuclei, "8-bit", "")

                                base_name = os.path.splitext(filename)[0]
                                nuclei_out = os.path.join(nuclei_folder,
                                                          f"{base_name}_nuclei_projection.tif")
                                spatial.imagej_calibration(imp_nuclei, calibration, jimport)
                                progress.phase("Saving prepared channel")
                                IJ.saveAs(imp_nuclei, "Tiff", nuclei_out)
                                spatial.save_snapshot(nuclei_out, calibration, (height, width), file_path)
                                run.saved(nuclei_out, (height, width))
                                imp_nuclei.close()

                                # ----- Prepare marker channels (2D TIFF) -----
                                for foci_channel in foci_channels:
                                    progress.phase(f"Extracting marker C{foci_channel}")
                                    imp_foci = splitted_channels[foci_channel - 1]
                                    IJ.run(imp_foci, "8-bit", "")

                                    # Save to the corresponding marker folder.
                                    foci_out = os.path.join(foci_folders[foci_channel],
                                                            f"{base_name}_foci_projection.tif")
                                    spatial.imagej_calibration(imp_foci, calibration, jimport)
                                    progress.phase("Saving prepared channel")
                                    IJ.saveAs(imp_foci, "Tiff", foci_out)
                                    spatial.save_snapshot(foci_out, calibration, (height, width), file_path)
                                    run.saved(foci_out, (height, width))
                                    imp_foci.close()

                                # Close the original image
                                imp.close()

                            # Close all images to free memory
                            IJ.run("Close All")

                            run.finish_image()
            statuses.append(run.status)
    return statuses


@cancelable
def select_channel_name(input_json_path: str):
    """
    Reads the JSON file to get valid folders, then prompts the user
    for file type and channel numbers, and processes images accordingly.
    """
    # Validate input directories from the JSON file
    valid_folders = validate_folders(input_json_path)

    # Confirm whether the user wants to start analysis
    try:
        start_analysis = ask_yes_no("Start analyzing files in the specified folders? "
                                    "(yes/no; q = cancel all): ")
    except Cancelled:
        start_analysis = False
    if not start_analysis:
        print("Analysis canceled. Existing results preserved; no processing started.")
        return

    # Process images
    statuses = process_image(valid_folders, input_json_path)
    if not statuses:
        return
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
    raise SystemExit(select_channel_name(args.input))
