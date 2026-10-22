#!/usr/bin/env python

if __name__ == '__main__':
    from runtime_worker import launch_direct
    raise SystemExit(launch_direct('select_channels'))

import hashlib
import json
import os
from pathlib import Path
from uuid import uuid4

import fiji_config
import imagej
import plate_design as plate
import spatial_calibration as spatial
from assay_layout import ASSAY_DIR, MARKERS_DIR
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


def select_settings(nuclei_only=False):
    """Only ambiguous TIFF projection semantics need user input."""
    print("TIFF input: 2 = multichannel Z-stack; 3 = already projected 2D image")
    return ask_integer("Input type (2-3; q = cancel): ", 2, 3)


def preparation_manifest(folder, design, files, status, sources, input_type):
    plate.save_snapshot(folder, design)
    payload = {'Status': status, 'Input_type': input_type, 'Files': files, 'Sources': sources}
    target = Path(folder) / plate.PREPARATION
    temporary = target.with_suffix('.tmp')
    temporary.write_text(json.dumps(payload, indent=2) + '\n', encoding='utf-8')
    temporary.replace(target)


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


def process_image(valid_folders: list, input_json_path=None, nuclei_only=False) -> list:
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
    designs = {Path(folder).resolve(): plate.load_design(folder) for folder in valid_folders}
    inventories = {root: plate.sources(root, design) for root, design in designs.items()}
    selection = confirm_output_folders(valid_folders)
    if selection is None:
        return []
    selected_folders, skipped_folders = selection
    if not selected_folders:
        print("No folders selected for processing.\nExisting results preserved. Nothing to do.")
        return []
    initialized = False
    tiff_type = None
    run_id = uuid4().hex
    script_digest = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    statuses = []

    total = sum(len(inventories[Path(folder).resolve()][0]) for folder in selected_folders)
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
                design = designs[Path(input_folder).resolve()]
                included, unassigned = inventories[Path(input_folder).resolve()]
                run.files = [path.name for path in included]
                for path in unassigned:
                    run.logger.info('SKIPPED_BY_DESIGN | file=%s | unannotated well', path.name)
                run.logger.info('PLATE_SCHEME | file=%s | sha256=%s', design.path, design.workbook_sha256)
                prepared = {}
                source_hashes = {p.name: plate.file_digest(p) for p in included}
                if tiff_type is None and any(p.suffix.lower() != '.nd2' for p in included):
                    tiff_type = select_settings()

                progress.logger = run.logger
                if not initialized:
                    run.logger.info("Initializing ImageJ...")
                    ij = initialize_imagej()
                    IJ = jimport('ij.IJ')
                    ZProjector = jimport('ij.plugin.ZProjector')
                    ChannelSplitter = jimport('ij.plugin.ChannelSplitter')
                    initialized = True
                run.logger.info("RUNTIME | ImageJ=%s", ij.getVersion())
                with bioformats_progress(progress):
                    # Create subfolder for Nuclei
                    nuclei_folder = os.path.join(processed_folder, "Nuclei")
                    Path(nuclei_folder).mkdir(parents=True, exist_ok=True)
                    run.logger.info("Nuclei folder: %s", nuclei_folder)

                    folders = {None: Path(nuclei_folder)}
                    if not nuclei_only:
                        for method in ('Foci', 'Nuclear intensity', 'Field signal'):
                            for marker in design.markers(method):
                                folders[marker['id']] = Path(processed_folder) / MARKERS_DIR / marker['id']
                    for folder in folders.values():
                        folder.mkdir(parents=True, exist_ok=True)
                        prepared[folder] = {}
                        preparation_manifest(folder, design, {}, 'RUNNING', source_hashes, tiff_type or 1)

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
                            file_type = 1 if file_ext == '.nd2' else tiff_type
                            well = plate.well_from_name(filename)
                            nuclei_channel = design.channel(well, 'Nuclei')
                            assignments = {row['channel']: row for row in design.wells[well]['channels']
                                           if row and row['method'] in ('Foci', 'Nuclear intensity', 'Field signal')
                                           and not nuclei_only}
                            foci_channels = list(assignments)
                            foci_folders = {number: str(folders[row['id']]) for number, row in assignments.items()}
                            run.logger.info('IMAGE_ROUTING | file=%s | well=%s | nuclei_C=%s | markers=%s',
                                            filename, well, nuclei_channel, assignments)

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
                            try:
                                design.validate_channels(well, channels)
                            except ValueError as error:
                                run.fail_image(str(error))
                                imp.close()
                                continue
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
                                run.logger.info("PROCESSING | file=%s | nuclei_projection=MAX%s | "
                                                "nuclei_output_bit_depth=8 | resize=False", filename,
                                                " | marker_projection=per method" if foci_channels else "")
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
                                prepared[Path(nuclei_folder)][Path(nuclei_out).name] = hashlib.sha256(Path(nuclei_out).read_bytes()).hexdigest()

                                nuclei_proj.close()
                                imp_nuclei.close()

                                # Prepare each supported marker using its method-specific projection.
                                for foci_channel in foci_channels:
                                    method = assignments[foci_channel]['method']
                                    projection_name = {'Foci': 'SD', 'Nuclear intensity': 'MAX', 'Field signal': 'SUM'}[method]
                                    progress.phase(f"{method} C{foci_channel} {projection_name} projection")
                                    run.logger.info("MARKER_PREPARATION | file=%s | marker=%s | original_channel=%s | method=%s | projection=%s | to_8bit=%s", filename, assignments[foci_channel]['name'], foci_channel, method, projection_name, method == 'Foci')
                                    imp.setC(foci_channel)
                                    IJ.run(imp, "Duplicate...",
                                           f"title=imp_foci duplicate channels={foci_channel}")
                                    imp_foci = IJ.getImage()

                                    zp_foci = ZProjector(imp_foci)
                                    method = assignments[foci_channel]['method']
                                    projection_method = {'Foci': ZProjector.SD_METHOD,
                                                         'Nuclear intensity': ZProjector.MAX_METHOD,
                                                         'Field signal': ZProjector.SUM_METHOD}[method]
                                    zp_foci.setMethod(projection_method)
                                    zp_foci.doProjection()
                                    foci_proj = zp_foci.getProjection()

                                    # Preserve native XY dimensions; convert segmentation input to 8-bit.
                                    if method == 'Foci':
                                        IJ.run(foci_proj, "8-bit", "")

                                    # Save to the corresponding marker folder.
                                    foci_out = os.path.join(foci_folders[foci_channel],
                                                            f"{base_name}_foci_projection.tif")
                                    spatial.imagej_calibration(foci_proj, calibration, jimport)
                                    progress.phase("Saving prepared channel")
                                    IJ.saveAs(foci_proj, "Tiff", foci_out)
                                    spatial.save_snapshot(foci_out, calibration, (height, width), file_path)
                                    run.saved(foci_out, (height, width))
                                    prepared[Path(foci_folders[foci_channel])][Path(foci_out).name] = hashlib.sha256(Path(foci_out).read_bytes()).hexdigest()

                                    foci_proj.close()
                                    imp_foci.close()

                                # Close the original
                                imp.close()

                            else:
                                run.logger.info("PROCESSING | file=%s | projection=none (2D channel extraction) | "
                                                "nuclei_output_bit_depth=8 | marker_bit_depth=per method | resize=False", filename)
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
                                prepared[Path(nuclei_folder)][Path(nuclei_out).name] = hashlib.sha256(Path(nuclei_out).read_bytes()).hexdigest()
                                imp_nuclei.close()

                                # ----- Prepare marker channels (2D TIFF) -----
                                for foci_channel in foci_channels:
                                    progress.phase(f"Extracting marker C{foci_channel}")
                                    imp_foci = splitted_channels[foci_channel - 1]
                                    if assignments[foci_channel]['method'] == 'Foci':
                                        IJ.run(imp_foci, "8-bit", "")

                                    # Save to the corresponding marker folder.
                                    foci_out = os.path.join(foci_folders[foci_channel],
                                                            f"{base_name}_foci_projection.tif")
                                    spatial.imagej_calibration(imp_foci, calibration, jimport)
                                    progress.phase("Saving prepared channel")
                                    IJ.saveAs(imp_foci, "Tiff", foci_out)
                                    spatial.save_snapshot(foci_out, calibration, (height, width), file_path)
                                    run.saved(foci_out, (height, width))
                                    prepared[Path(foci_folders[foci_channel])][Path(foci_out).name] = hashlib.sha256(Path(foci_out).read_bytes()).hexdigest()
                                    imp_foci.close()

                                # Close the original image
                                imp.close()

                            # Close all images to free memory
                            IJ.run("Close All")

                            run.finish_image()
                for folder, files in prepared.items():
                    preparation_manifest(folder, design, files,
                                         'COMPLETE' if run.completed == len(run.files) and not run.failed else 'INCOMPLETE',
                                         source_hashes, tiff_type or 1)
            statuses.append(run.status)
    return statuses


@cancelable
def select_channel_name(input_json_path: str, nuclei_only=False):
    """
    Read the experiment folders, confirm preparation and use Excel channel roles.
    TIFF projection semantics are requested when they cannot be inferred.
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
    statuses = (process_image(valid_folders, input_json_path, nuclei_only=True) if nuclei_only
                else process_image(valid_folders, input_json_path))
    if not statuses:
        return
    if all(status == "SUCCESS" for status in statuses):
        print("\nPart 1 successfully completed.")
    else:
        print("\nPart 1 finished with incomplete results. Check each folder's 1_log.log.")
