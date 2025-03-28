#!/usr/bin/env python3

import os
import re
import json
import logging
import numpy as np
import pandas as pd
from datetime import datetime, timedelta
from pathlib import Path
from skimage import io, measure
import matplotlib.pyplot as plt
import itertools
import time
import csv

def validate_path_files(input_json_path: str) -> dict:
    """
    Reads a JSON file describing paths in the form:
      { "paths_to_files": ["/folder1", "/folder2", ...] }
    Returns a dict of valid folders:
      { base_folder: {"base_folder": base_folder, "foci_assay_folder": ...}, ... }
    """
    if not os.path.exists(input_json_path):
        raise FileNotFoundError(f"JSON not found: {input_json_path}")

    with open(input_json_path, 'r') as f:
        data = json.load(f)

    folder_dicts = {}
    for base_folder in data["paths_to_files"]:
        if not os.path.isdir(base_folder):
            logging.warning(f"Folder not found: {base_folder}")
            continue

        foci_assay_folder = os.path.join(base_folder, "foci_assay")
        if not os.path.isdir(foci_assay_folder):
            logging.warning(f"No foci_assay folder in {base_folder}")
            continue

        folder_dicts[base_folder] = {
            "base_folder": base_folder,
            "foci_assay_folder": foci_assay_folder
        }
    return folder_dicts

def gather_paths_and_channels(base_folder: str):
    """
    1) Finds the newest nuclei folder,
    2) Collects all *.tif files (nuclei),
    3) Finds the newest foci folders by channel,
    4) Builds channels_dict[nuc_key][channel_name] = path_to_foci_file
    """
    foci_assay_folder = os.path.join(base_folder, "foci_assay")
    nuclei_mask_folder = get_latest_nuclei_mask_folder(foci_assay_folder)

    nuclei_files = []
    for f in os.listdir(nuclei_mask_folder):
        if f.endswith(".tif"):
            nuclei_files.append(os.path.join(nuclei_mask_folder, f))

    latest_foci = get_latest_foci_folders(foci_assay_folder)
    channels_dict = {}

    for channel_name, foci_folder_path in latest_foci.items():
        foci_files = [fn for fn in os.listdir(foci_folder_path) if fn.endswith(".tif")]
        for tif_file in foci_files:
            foci_key = extract_image_key(tif_file)
            if foci_key not in channels_dict:
                channels_dict[foci_key] = {}
            channels_dict[foci_key][channel_name] = os.path.join(foci_folder_path, tif_file)

    return nuclei_files, channels_dict

def extract_image_key(filename: str) -> str:
    """
    Removes known prefixes/suffixes from a filename to derive a 'nuc_key'.
    """
    filename = re.sub(r"_nuclei_projection_StarDist_processed_processed", "", filename)
    filename = re.sub(r"^processed_", "", filename)
    filename = re.sub(r"_foci_projection", "", filename)
    filename = re.sub(r"\.tif$", "", filename)
    filename = re.sub(r"\.nd2", "", filename)
    return filename

def extract_metadata(metadata_path: str) -> dict:
    """
    Extracts pixel metadata from an image_metadata.txt file, if it exists.
    Returns a dict of { image_key: {"Pixel Width": float, "Pixel Height": float, ...}, ... }
    """
    metadata = {}
    if not os.path.exists(metadata_path):
        return metadata

    with open(metadata_path, "r") as file:
        content = file.read()

    blocks = content.split("Image Name: ")[1:]
    for block in blocks:
        image_name = block.split("\n")[0].strip()
        image_key = os.path.splitext(image_name)[0]

        px_w = re.search(r"Pixel Width: (\d+\.\d+)", block)
        px_h = re.search(r"Pixel Height: (\d+\.\d+)", block)
        px_d = re.search(r"Pixel Depth: (\d+\.\d+)", block)
        unit = re.search(r"Unit: (\w+)", block)

        if px_w and px_h and px_d and unit:
            metadata[image_key] = {
                "Pixel Width": float(px_w.group(1)),
                "Pixel Height": float(px_h.group(1)),
                "Pixel Depth": float(px_d.group(1)),
                "Unit": unit.group(1)
            }
    return metadata

def get_latest_nuclei_mask_folder(foci_assay_folder: str) -> str:
    """
    Finds the newest folder containing nuclei masks (e.g., Final_Nuclei_Mask_YYYYMMDD_HHMMSS).
    """
    nuclei_mask_folders = [f for f in os.listdir(foci_assay_folder) if f.startswith("Final_Nuclei_Mask_")]
    if not nuclei_mask_folders:
        raise FileNotFoundError(f"No 'Final_Nuclei_Mask_' folders in {foci_assay_folder}.")

    folder_timestamps = []
    for folder in nuclei_mask_folders:
        match = re.search(r"_(\d{8}_\d{6})", folder)
        if match:
            folder_timestamps.append((folder, match.group(1)))

    if not folder_timestamps:
        raise ValueError(f"Could not extract timestamps for 'Final_Nuclei_Mask_' folders in {foci_assay_folder}.")

    folder_timestamps.sort(key=lambda x: x[1], reverse=True)
    latest_folder = folder_timestamps[0][0]
    return os.path.join(foci_assay_folder, latest_folder)

def get_latest_foci_folders(foci_assay_folder: str) -> dict:
    """
    Finds the newest folders with foci masks for each channel (e.g., Foci_1_Channel_1_YYYYMMDD_HHMMSS).
    Returns a dict {channel_name: folder_path}.
    """
    foci_masks_folder = os.path.join(foci_assay_folder, "Foci_Masks")
    if not os.path.exists(foci_masks_folder):
        raise FileNotFoundError(f"Foci_Masks folder does not exist in {foci_assay_folder}.")

    foci_folders = [f for f in os.listdir(foci_masks_folder) if f.startswith("Foci_")]
    if not foci_folders:
        raise FileNotFoundError(f"No 'Foci_' folders in {foci_masks_folder}.")

    latest_foci_folders = {}
    for folder in foci_folders:
        match = re.search(r"_(\d{8}_\d{6})", folder)
        if not match:
            continue
        channel_match = re.search(r"(Foci_\d+_Channel_\d+)", folder)
        if not channel_match:
            continue

        channel_name = channel_match.group(1)
        timestamp = match.group(1)

        if channel_name in latest_foci_folders:
            # keep the folder with the latest timestamp
            if timestamp > latest_foci_folders[channel_name][1]:
                latest_foci_folders[channel_name] = (folder, timestamp)
        else:
            latest_foci_folders[channel_name] = (folder, timestamp)

    if not latest_foci_folders:
        raise ValueError(f"Could not extract timestamps for foci folders in {foci_masks_folder}.")

    return {
        channel: os.path.join(foci_masks_folder, folder)
        for channel, (folder, _) in latest_foci_folders.items()
    }

def save_labeled_image(image, output_path, title, labels=None):
    """
    Saves an image (TIFF/PNG) with labeled nuclei.
    If labels is None, it just saves the image as 16-bit TIFF.
    If labels is a dict {label: (centroid_y, centroid_x)}, it draws those labels in the image.
    """
    if labels:
        fig, ax = plt.subplots()
        ax.imshow(image, cmap='gray')
        for lbl, (cy, cx) in labels.items():
            ax.text(cx, cy, str(lbl), color='red', fontsize=8, ha='center', va='center')
        ax.set_title(title)
        ax.axis('off')
        plt.savefig(output_path, bbox_inches='tight', pad_inches=0, dpi=150)
        plt.close()
    else:
        io.imsave(output_path, image.astype(np.uint16))
    logging.info(f"Saved image {title} to {output_path}.")

def labeled_nuclei_path(nuc_file_path: str, results_folder: str) -> str:
    """
    Generates a path for the labeled nuclei image file.
    """
    nuc_filename = os.path.basename(nuc_file_path)
    nuc_key = extract_image_key(nuc_filename)
    return os.path.join(results_folder, f"{nuc_key}_labeled_nuclei.png")

def print_progress_bar(progress, prefix="", suffix="", length=50):
    """
    Prints a progress bar to the console.
    """
    filled_length = int(length * progress // 100)
    bar = "=" * filled_length + ">" + " " * (length - filled_length)
    print(f"\r{prefix} [{bar}] {progress:.2f}% {suffix}", end="", flush=True)
    if progress >= 100:
        print()

def count_foci_in_nuclei(nuclei_mask, foci_mask, pixel_area, image_key, temp_folder):
    """
    СЧЁТ ЧЕРЕЗ 2D-ГИСТОГРАММУ. 
    Считает, сколько объектов в foci_mask попадает в каждое ядро в nuclei_mask.
    Использует один проход по пикселям (двумерная гистограмма) вместо цикла по каждому ядру.
    Сохраняет промежуточные результаты в CSV (как и раньше).
    """
    if nuclei_mask.shape != foci_mask.shape:
        raise ValueError("Nuclei mask and foci mask must have the same shape.")

    # Nuclei and foci lableing
    labeled_nuc = measure.label(nuclei_mask)
    labeled_foc = measure.label(foci_mask)
    nuc_props = measure.regionprops(labeled_nuc)
    total_nuclei = len(nuc_props)

    # For quick access to nuclear area (px)
    nuc_area_dict = {prop.label: prop.area for prop in nuc_props}

    # Preparing a 2D histogram
    arr_nuc = labeled_nuc.ravel()
    arr_foc = labeled_foc.ravel()
    max_nuc_label = labeled_nuc.max()
    max_foc_label = labeled_foc.max()

    # hist_2d[i, j] = number of pixels where nucleus i intersects with focus j
    hist_2d = np.zeros((max_nuc_label + 1, max_foc_label + 1), dtype=int)
    
    # One pass through all pixels
    for i in range(len(arr_nuc)):
        nuc_val = arr_nuc[i]
        foc_val = arr_foc[i]
        # 0 is background; labels are only relevant when > 0
        if nuc_val != 0 and foc_val != 0:
            hist_2d[nuc_val, foc_val] += 1

    results = []
    temp_results_file = os.path.join(temp_folder, f"{image_key}_temp_results.csv")

    #Let’s save the intermediate results to a temp CSV (as in the original code)
    with open(temp_results_file, "w", newline="") as temp_file:
        writer = csv.writer(temp_file)
        writer.writerow([
            "Image Key","Nucleus","Foci Count","Nucleus Area (pixels)",
            "Nucleus Area (micron²)","Total Foci Area (pixels)",
            "Total Foci Area (micron²)","Relative Foci Area (%)"
        ])

        for idx, prop in enumerate(nuc_props):
            nuc_label = prop.label
            nuc_area_px = nuc_area_dict[nuc_label]
            nuc_area_micron = nuc_area_px * pixel_area

            # From the histogram, extract all intersections for the given nucleus
            overlap_per_foc = hist_2d[nuc_label, :]
            # Number of foci — how many j > 0 have an intersection > 0
            foci_count = np.count_nonzero(overlap_per_foc)
            # Total area of foci within the nucleus
            total_foci_px = np.sum(overlap_per_foc)
            total_foci_micron = total_foci_px * pixel_area

            rel_area = 0.0
            if nuc_area_px > 0:
                rel_area = (total_foci_px / nuc_area_px) * 100.0

            # Displaying progress
            progress = (idx + 1) / total_nuclei * 100
            print_progress_bar(
                progress, 
                prefix=f"Image {image_key}:", 
                suffix=f"Nucleus {nuc_label} ({idx + 1}/{total_nuclei})"
            )

            writer.writerow([
                image_key, nuc_label, foci_count, nuc_area_px,
                nuc_area_micron, total_foci_px, total_foci_micron,
                f"{rel_area:.2f}"
            ])

    # Reading the temporary file back into a list of dicts
    with open(temp_results_file, "r") as temp_file:
        reader = csv.DictReader(temp_file)
        for row in reader:
            results.append(row)

    # Deleting the temporary CSV
    os.remove(temp_results_file)

    return results

def process_nuclei_image(nuc_file_path: str,
                         foci_channels_info: dict,
                         metadata: dict,
                         results_folder: str,
                         perform_colocalization: bool) -> list:
    """
    Processes ONE nucleus mask + all corresponding foci channels.
    Uses temporary files to reduce memory usage.
    """
    try:
        nuc_filename = os.path.basename(nuc_file_path)
        nuc_key = extract_image_key(nuc_filename)
        logging.info(f"Started nuclei processing: {nuc_filename}")

        if nuc_key not in metadata:
            logging.warning(f"No metadata for {nuc_key}. Skipping.")
            return []

        # Create a temporary folder for intermediate results
        temp_folder = os.path.join(results_folder, "temp")
        Path(temp_folder).mkdir(parents=True, exist_ok=True)

        # Load nucleus
        nuclei_mask = io.imread(nuc_file_path)
        nuclei_labels = measure.label(nuclei_mask)
        nuclei_props = measure.regionprops(nuclei_labels)

        # Save labeled nucleus image (for visual check)
        labels_dict = {prop.label: prop.centroid for prop in nuclei_props}
        labeled_path = labeled_nuclei_path(nuc_file_path, results_folder)
        save_labeled_image(nuclei_labels, labeled_path, title=f"Nuclei {nuc_key}", labels=labels_dict)

        # Pixel area
        px_width = metadata[nuc_key]["Pixel Width"]
        px_height = metadata[nuc_key]["Pixel Height"]
        pixel_area_micron = px_width * px_height

        # Load all foci channel masks
        channel_names = sorted(foci_channels_info.keys())
        channel_masks = {}
        for ch_name in channel_names:
            path_ch = foci_channels_info[ch_name]
            if not os.path.exists(path_ch):
                logging.warning(f"Foci file not found: {path_ch}")
                continue
            foci_mask = io.imread(path_ch)
            if foci_mask.shape != nuclei_mask.shape:
                logging.warning(f"Foci shape {foci_mask.shape} != Nuclei shape {nuclei_mask.shape}. Skipping {path_ch}")
                continue
            channel_masks[ch_name] = foci_mask

        if not channel_masks:
            logging.warning(f"No valid foci channels for nucleus {nuc_key}")
            return []

        # Collecting results for each channel (without colocalization between channels)
        all_dfs = []
        for ch_name, f_mask in channel_masks.items():
            # Using the new fast function based on a 2D histogram
            res = count_foci_in_nuclei(nuclei_mask, f_mask, pixel_area_micron, nuc_key, temp_folder)
            df_res = pd.DataFrame(res)
            #Renaming columns for the given channel
            df_res.rename(columns={
                "Foci Count": f"Foci Count ({ch_name})",
                "Total Foci Area (pixels)": f"Total Foci Area (pixels) ({ch_name})",
                "Total Foci Area (micron²)": f"Total Foci Area (micron²) ({ch_name})",
                "Relative Foci Area (%)": f"Relative Foci Area (%) ({ch_name})"
            }, inplace=True)
            all_dfs.append(df_res)

        # Merging results across channels
        df_single = all_dfs[0]
        for df_next in all_dfs[1:]:
            df_single = df_single.merge(df_next, on=["Image Key", "Nucleus"], how="outer")

        # If colocalization is not needed, simply return the result
        if not perform_colocalization:
            return df_single.to_dict("records")

        # Colocalization logic can be added here if needed.
        # В данной версии оставлено как в исходном коде "return df_single".
        return df_single.to_dict("records")

    except Exception as e:
        logging.error(f"Error processing {nuc_file_path}: {e}")
        return []

def main_summarize_res(input_json_path: str) -> None:
    """
    Main function:
      1) Reads JSON with folder paths,
      2) Asks user if we should start any processing,
      3) Asks user if we should perform colocalization or skip it,
      4) For each folder, gathers nuclei/foci, loads metadata, runs analysis, saves results.
    """
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
    folder_dicts = validate_path_files(input_json_path)
    if not folder_dicts:
        raise ValueError("No valid folders found in JSON.")

    proceed_processing = input("Start processing? (yes/no): ").strip().lower()
    if proceed_processing not in ("yes", "y"):
        logging.info("Processing canceled by user.")
        return

    co_loc_answer = input("Do you want to perform colocalization analysis? (yes/no): ").strip().lower()
    perform_colocalization = (co_loc_answer in ("yes", "y"))

    for base_folder, info in folder_dicts.items():
        foci_assay_folder = info["foci_assay_folder"]
        now_str = datetime.now().strftime("%Y%m%d_%H%M%S")
        results_folder = os.path.join(base_folder, "foci_analysis", f"Results_{now_str}")
        Path(results_folder).mkdir(parents=True, exist_ok=True)

        fh = logging.FileHandler(os.path.join(results_folder, "log.txt"), mode='w')
        fh.setLevel(logging.INFO)
        logging.getLogger('').addHandler(fh)

        metadata_path = os.path.join(foci_assay_folder, "image_metadata.txt")
        metadata = extract_metadata(metadata_path)

        try:
            nuclei_files, channels_dict = gather_paths_and_channels(base_folder)
        except Exception as e:
            logging.error(f"Error gathering paths: {e}")
            continue

        if not nuclei_files:
            logging.warning(f"No nuclei files in {base_folder}")
            continue

        all_results = []
        total_images = len(nuclei_files)
        start_time = time.time()

        for idx, nuc_file_path in enumerate(nuclei_files):
            nuc_filename = os.path.basename(nuc_file_path)
            nuc_key = extract_image_key(nuc_filename)

            if nuc_key not in channels_dict:
                logging.warning(f"No foci channels found for {nuc_key}")
                continue

            foci_channels_info = channels_dict[nuc_key]
            partial_res = process_nuclei_image(
                nuc_file_path,
                foci_channels_info,
                metadata,
                results_folder,
                perform_colocalization
            )
            all_results.extend(partial_res)

            elapsed = time.time() - start_time
            avg_time_per_task = elapsed / (idx + 1)
            tasks_left = total_images - (idx + 1)
            eta_seconds = avg_time_per_task * tasks_left
            finish_time_est = datetime.now() + timedelta(seconds=eta_seconds)

            logging.info(
                f"Image {idx + 1}/{total_images} completed. "
                f"Elapsed {elapsed:.1f}s, avg {avg_time_per_task:.2f}s per task. "
                f"Remaining {tasks_left} tasks, ETA ~ {finish_time_est.strftime('%H:%M:%S')}."
            )

        if not all_results:
            logging.warning("No data after processing. Skipping CSV save.")
            logging.getLogger('').removeHandler(fh)
            fh.close()
            continue

        df_results = pd.DataFrame(all_results)
        df_results = df_results.sort_values(by=["Image Key", "Nucleus"])
        output_csv = os.path.join(results_folder, "all_results_with_coloc_universal.csv")
        df_results.to_csv(output_csv, index=False)
        logging.info(f"Saved result: {output_csv}")

        logging.getLogger('').removeHandler(fh)
        fh.close()

    logging.info("All processing completed.")

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Foci/nuclei analysis with optional universal colocalization and black-objects/white-bg masks."
    )
    parser.add_argument("-i", "--input", required=True, help="JSON file with folder paths.")
    args = parser.parse_args()

    main_summarize_res(args.input)
