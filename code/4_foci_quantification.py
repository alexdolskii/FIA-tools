#!/usr/bin/env python3

import argparse
import csv
import itertools
import logging
import os
import re
import shutil
import time
from datetime import datetime, timedelta
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from skimage import io, measure
from validate_folders import validate_input_file


def validate_folders(input_json_path: str) -> dict:
    folder_paths = validate_input_file(input_json_path)
    folder_dicts = {}
    for base_folder in folder_paths:
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


def get_latest_nuclei_mask_folder(foci_assay_folder: str) -> str:
    """
    Finds the newest folder containing nuclei
    masks (e.g., Final_Nuclei_Mask_YYYYMMDD_HHMMSS).
    """
    nuclei_folder = os.path.join(foci_assay_folder, "Nuclei_masks")
    nuclei_mask_folders = [f for f in os.listdir(nuclei_folder)
                           if f.startswith("Final_Nuclei_Mask_")]
    if not nuclei_mask_folders:
        raise FileNotFoundError(f"No 'Final_Nuclei_Mask_' "
                                f"olders in {nuclei_folder}.")
    folder_timestamps = []
    for folder in nuclei_mask_folders:
        match = re.search(r"_(\d{8}_\d{6})", folder)
        if match:
            folder_timestamps.append((folder, match.group(1)))
    if not folder_timestamps:
        raise ValueError(f"Could not extract "
                         f"timestamps for 'Final_Nuclei_Mask_' "
                         f"folders in {foci_assay_folder}.")
    folder_timestamps.sort(key=lambda x: x[1], reverse=True)
    latest_folder = folder_timestamps[0][0]
    path2lat_folder = os.path.join(nuclei_folder, latest_folder)
    return path2lat_folder


def get_latest_foci_folders(foci_assay_folder: str) -> dict:
    """
    Finds the newest folders with foci masks
    for each channel (e.g., Foci_1_Channel_1_YYYYMMDD_HHMMSS).
    Returns a dict {channel_name: folder_path}.
    """
    foci_masks_folder = os.path.join(foci_assay_folder,
                                     "Foci_Masks")
    if not os.path.exists(foci_masks_folder):
        raise FileNotFoundError(f"Foci_Masks folder "
                                f"does not exist in {foci_assay_folder}.")
    foci_folders = [f for f in os.listdir(foci_masks_folder)
                    if f.startswith("Foci_")]
    if not foci_folders:
        raise FileNotFoundError(f"No 'Foci_' "
                                f"folders in {foci_masks_folder}.")
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
        raise ValueError(f"Could not extract "
                         f"timestamps for foci folders "
                         f"in {foci_masks_folder}.")
    paths = {channel:
             os.path.join(foci_masks_folder, folder)
             for channel, (folder, _) in latest_foci_folders.items()}
    return paths


def gather_paths_and_channels(base_folder: str):
    """
    1) Finds the newest nuclei folder,
    2) Collects all *.tif files (nuclei),
    3) Finds the newest foci folders by channel,
    4) Builds channels_dict[nuc_key][channel_name] = path_to_foci_file
    """
    foci_assay_folder = os.path.join(base_folder,
                                     "foci_assay")
    nuclei_mask_folder = get_latest_nuclei_mask_folder(foci_assay_folder)

    nuclei_files = []
    for f in os.listdir(nuclei_mask_folder):
        if f.endswith(".tif"):
            nuclei_files.append(os.path.join(nuclei_mask_folder, f))
    latest_foci = get_latest_foci_folders(foci_assay_folder)
    channels_dict = {}
    for channel_name, foci_folder_path in latest_foci.items():
        foci_files = [fn for fn
                      in os.listdir(foci_folder_path)
                      if fn.endswith(".tif")]
        for tif_file in foci_files:
            foci_key = extract_image_key(tif_file)
            if foci_key not in channels_dict:
                channels_dict[foci_key] = {}
            channels_dict[foci_key][channel_name] = os.path.join(foci_folder_path,
                                                                 tif_file)
    return nuclei_files, channels_dict


def extract_image_key(filename: str) -> str:
    """
    Removes known prefixes/suffixes from a
    filename to derive a 'nuc_key'.
    """
    filename = re.sub(r"_nuclei_projection_StarDist_processed_processed",
                      "", filename)
    filename = re.sub(r"^processed_", "", filename)
    filename = re.sub(r"_foci_projection", "", filename)
    filename = re.sub(r"\.tif$", "", filename)
    filename = re.sub(r"\.nd2", "", filename)
    return filename


def extract_metadata(metadata_path: str) -> dict:
    """
    Extracts pixel metadata from an image_metadata.txt file,
    if it exists.
    Returns a dict of
    { image_key: {"Pixel Width": float, "Pixel Height": float, ...}, ... }
    """
    metadata = {}
    if not os.path.exists(metadata_path):
        raise ValueError("There is no metadata file")
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


def save_labeled_image(image,
                       output_path,
                       title,
                       labels=None):
    """
    Saves an image (TIFF/PNG) with labeled nuclei.
    If labels is None, it just saves the image
    as 16-bit TIFF.
    If labels is a dict {label: (centroid_y, centroid_x)},
    it draws those labels in the image.
    """
    if labels:
        fig, ax = plt.subplots()
        ax.imshow(image, cmap='gray')
        for lbl, (cy, cx) in labels.items():
            ax.text(cx, cy, str(lbl),
                    color='red',
                    fontsize=8,
                    ha='center',
                    va='center')
        ax.set_title(title)
        ax.axis('off')
        plt.savefig(output_path,
                    bbox_inches='tight',
                    pad_inches=0,
                    dpi=150)
        plt.close()
    else:
        io.imsave(output_path, image.astype(np.uint16))
    logging.info(f"Saved image {title} to {output_path}.")


def labeled_nuclei_path(nuc_file_path: str,
                        results_folder: str) -> str:
    """
    Generates a path for the labeled nuclei image file.
    """
    nuc_filename = os.path.basename(nuc_file_path)
    nuc_key = extract_image_key(nuc_filename)
    path = os.path.join(results_folder, f"{nuc_key}_labeled_nuclei.png")
    return path


def print_progress_bar(progress,
                       prefix="",
                       suffix="",
                       length=50):
    """
    Prints a progress bar to the console.
    """
    filled_length = int(length * progress // 100)
    bar = "=" * filled_length + ">" + " " * (length - filled_length)
    print(f"\r{prefix} [{bar}] "
          f"{progress:.2f}% {suffix}", end="", flush=True)
    if progress >= 100:
        print()


# Intersection logic
def build_intersection_mask(*masks):
    """
    Creates a labeled mask representing the
    intersection of all input labeled masks.
    Each distinct overlap region among them
    gets a unique label.
    """
    if len(masks) < 2:
        raise ValueError("The function "
                         "requires at least 2 masks.")

    shape_set = {m.shape for m in masks}
    if len(shape_set) != 1:
        raise ValueError("All masks must have the "
                         "same shape for intersection.")

    intersection_mask = np.zeros_like(masks[0],
                                      dtype=np.uint16)
    label_counter = 1

    # gather unique labels from each mask
    label_lists = []
    for m in masks:
        lbls = np.unique(m)
        lbls = lbls[lbls != 0]
        label_lists.append(lbls)
    # use itertools.product to combine possible
    # labels from each mask
    for combo in itertools.product(*label_lists):
        overlap_bool = None
        for mask_index, lbl_val in enumerate(combo):
            m = masks[mask_index]
            coords = np.argwhere(m == lbl_val)
            if coords.size == 0:
                overlap_bool = None
                break
            if overlap_bool is None:
                overlap_bool = np.zeros(m.shape, dtype=bool)
                overlap_bool[coords[:, 0], coords[:, 1]] = True
            else:
                temp_bool = np.zeros(m.shape, dtype=bool)
                temp_bool[coords[:, 0], coords[:, 1]] = True
                overlap_bool &= temp_bool
            if overlap_bool is not None and not np.any(overlap_bool):
                overlap_bool = None
                break
        if overlap_bool is not None and np.any(overlap_bool):
            intersection_mask[overlap_bool] = label_counter
            label_counter += 1
    return intersection_mask


def count_foci_in_nuclei(nuclei_mask,
                         foci_mask,
                         pixel_area,
                         image_key,
                         temp_folder):
    """
    COUNT THROUGH 2D-HISTOGRAM.
    Counts how many objects in the foci_mask fall
    into each nucleus in the nuclei_mask.
    Uses a single pass over the pixels (2D histogram)
    instead of iterating over each nucleus.
    Saves intermediate results in CSV (as before).
    """
    if nuclei_mask.shape != foci_mask.shape:
        raise ValueError("Nuclei mask and foci "
                         "mask must have the same shape.")
    labeled_nuc = measure.label(nuclei_mask)
    labeled_foc = measure.label(foci_mask)
    nuc_props = measure.regionprops(labeled_nuc)
    total_nuclei = len(nuc_props)
    nuc_area_dict = {prop.label: prop.area for prop in nuc_props}
    arr_nuc = labeled_nuc.ravel()
    arr_foc = labeled_foc.ravel()
    max_nuc_label = labeled_nuc.max()
    max_foc_label = labeled_foc.max()
    hist_2d = np.zeros((max_nuc_label + 1, max_foc_label + 1), dtype=int)
    for i in range(len(arr_nuc)):
        nuc_val = arr_nuc[i]
        foc_val = arr_foc[i]
        if nuc_val != 0 and foc_val != 0:
            hist_2d[nuc_val, foc_val] += 1
    results = []
    temp_results_file = os.path.join(temp_folder,
                                     f"{image_key}_temp_results.csv")
    with open(temp_results_file, "w", newline="") as temp_file:
        writer = csv.writer(temp_file)
        writer.writerow([
            "Image Key", "Nucleus", "Foci Count", "Nucleus Area (pixels)",
            "Nucleus Area (micron²)", "Total Foci Area (pixels)",
            "Total Foci Area (micron²)", "Relative Foci Area (%)"
        ])
        for idx, prop in enumerate(nuc_props):
            nuc_label = prop.label
            nuc_area_px = nuc_area_dict[nuc_label]
            nuc_area_micron = nuc_area_px * pixel_area
            overlap_per_foc = hist_2d[nuc_label, :]
            foci_count = np.count_nonzero(overlap_per_foc)
            total_foci_px = np.sum(overlap_per_foc)
            total_foci_micron = total_foci_px * pixel_area
            rel_area = 0.0
            if nuc_area_px > 0:
                rel_area = (total_foci_px / nuc_area_px) * 100.0
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
    with open(temp_results_file, "r") as temp_file:
        reader = csv.DictReader(temp_file)
        for row in reader:
            results.append(row)
    shutil.rmtree(temp_folder)
    return results


def process_nuclei_image(nuc_file_path: str,
                         foci_channels_info: dict,
                         metadata: dict,
                         results_folder: str,
                         perform_colocalization: bool,
                         min_subset_size=2,
                         max_subset_size=None) -> list:
    """
    Processes ONE nucleus mask + all corresponding
    foci channels.
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
        save_labeled_image(nuclei_labels,
                           labeled_path,
                           title=f"Nuclei {nuc_key}",
                           labels=labels_dict)
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
                logging.warning(f"Foci shape {foci_mask.shape} "
                                f"!= Nuclei shape {nuclei_mask.shape}. "
                                f"Skipping {path_ch}")
                continue
            channel_masks[ch_name] = foci_mask
        if not channel_masks:
            logging.warning(f"No valid foci channels for nucleus {nuc_key}")
            return []
        # Collecting results for each channel
        # (without colocalization between channels)
        all_dfs = []
        for ch_name, f_mask in channel_masks.items():
            # Using the new fast function based on a 2D histogram
            res = count_foci_in_nuclei(nuclei_mask,
                                       f_mask,
                                       pixel_area_micron,
                                       nuc_key,
                                       temp_folder)
            df_res = pd.DataFrame(res)
            df_res.rename(columns={
                "Foci Count": f"Foci Count ({ch_name})",
                "Total Foci Area (pixels)": f"Total Foci Area "
                                            f"(pixels) ({ch_name})",
                "Total Foci Area (micron²)": f"Total Foci Area "
                                             f"(micron²) ({ch_name})",
                "Relative Foci Area (%)": f"Relative Foci Area "
                                          f"(%) ({ch_name})"
            }, inplace=True)
            all_dfs.append(df_res)
        # Merging results across channels
        df_single = all_dfs[0]
        for df_next in all_dfs[1:]:
            df_single = df_single.merge(df_next,
                                        on=["Image Key", "Nucleus"],
                                        how="outer")
        # If colocalization is not needed, simply return the result
        if not perform_colocalization:
            return df_single.to_dict("records")
        # Build intersection masks for subsets
        n_channels = len(channel_masks)
        if (max_subset_size is None
                or max_subset_size > n_channels):
            max_subset_size = n_channels

        ch_name_list = list(channel_masks.keys())
        ch_name_list.sort()

        df_intersections = []
        for r in range(min_subset_size, max_subset_size + 1):
            for subset in itertools.combinations(ch_name_list, r):
                subset_str = "+".join(subset)
                # Build labeled intersection
                masks_to_intersect = [channel_masks[ch] for ch in subset]
                intersection_mask = build_intersection_mask(*masks_to_intersect)
                # Convert to black-objects, white-background:
                #   objects = 0, background = 255
                # This is a binary representation, losing distinct labels
                # but satisfying the user's request for black
                # object / white background
                inverted_mask = (np.where(intersection_mask > 0, 0, 255)
                                 .astype(np.uint8))
                # Save new mask
                intersection_name = (f"intersection_"
                                     f"{nuc_key}_{subset_str}.tif")
                save_path = os.path.join(results_folder,
                                         intersection_name)
                io.imsave(save_path, inverted_mask)
                logging.info(f"Saved intersection mask "
                             f"(black objects/white bg): {save_path}")
                # We still measure the labeled intersection (for stats),
                # so let's measure intersection_mask as is
                inter_res = count_foci_in_nuclei(nuclei_labels,
                                                 intersection_mask,
                                                 pixel_area_micron,
                                                 nuc_key)
                df_temp = pd.DataFrame(inter_res)
                # For intersection-based results,
                # we don't want repeated nucleus-area columns
                # because merges cause duplicates.
                # So let's drop them before renaming:
                # 'Nucleus Area (pixels)' and 'Nucleus Area (micron²)'
                # are redundant
                # we only keep them from the single-ch data
                if "Nucleus Area (pixels)" in df_temp.columns:
                    df_temp.drop(columns=["Nucleus Area (pixels)"],
                                 inplace=True,
                                 errors="ignore")
                if "Nucleus Area (micron²)" in df_temp.columns:
                    df_temp.drop(columns=["Nucleus Area (micron²)"],
                                 inplace=True,
                                 errors="ignore")
                # rename foci columns
                df_temp.rename(columns={
                    "Foci Count":
                        f"Foci Count ({subset_str})",
                    "Total Foci Area (pixels)":
                        f"Total Foci Area (pixels) ({subset_str})",
                    "Total Foci Area (micron²)":
                        f"Total Foci Area (micron²) ({subset_str})",
                    "Relative Foci Area (%)":
                        f"Relative Foci Area (%) ({subset_str})"
                }, inplace=True)
                df_intersections.append(df_temp)

        if df_intersections:
            df_inter_merged = df_intersections[0]
            for df_next in df_intersections[1:]:
                df_inter_merged = df_inter_merged.merge(df_next,
                                                        on=["Image Key", "Nucleus"],
                                                        how="outer")
        else:
            df_inter_merged = pd.DataFrame()

        if not df_inter_merged.empty:
            df_final = df_single.merge(df_inter_merged,
                                       on=["Image Key", "Nucleus"],
                                       how="outer")
        else:
            df_final = df_single
        return df_final.to_dict("records")
    except Exception as e:
        logging.error(f"Error processing {nuc_file_path}: {e}")
        return []


def main_summarize_res(input_json_path: str) -> None:
    """
    Main function:
      1) Reads JSON with folder paths,
      2) Asks user if we should start any processing,
      3) Asks user if we should perform
      colocalization or skip it,
      4) For each folder, gathers nuclei/foci,
      loads metadata, runs analysis, saves results.
    """
    logging.basicConfig(level=logging.INFO,
                        format='%(asctime)s - %(levelname)s - %(message)s')
    folder_dicts = validate_folders(input_json_path)
    if not folder_dicts:
        raise ValueError("No valid folders found in JSON.")
    proceed_processing = input("Start processing? (yes/no): ").strip().lower()
    if proceed_processing not in ("yes", "y"):
        raise ValueError("Processing canceled by user.")
    co_loc_answer = input("Do you want to perform "
                          "colocalization analysis? "
                          "(yes/no): ").strip().lower()
    perform_colocalization = (co_loc_answer in ("yes", "y"))

    for base_folder, info in folder_dicts.items():
        foci_assay_folder = info["foci_assay_folder"]
        now_str = datetime.now().strftime("%Y%m%d_%H%M%S")
        results_folder = os.path.join(base_folder,
                                      "foci_analysis",
                                      f"Results_{now_str}")
        Path(results_folder).mkdir(parents=True, exist_ok=True)

        fh = logging.FileHandler(os.path.join(results_folder,
                                              "4_log.log"), mode='w')
        fh.setLevel(logging.INFO)
        logging.getLogger('').addHandler(fh)

        metadata_path = os.path.join(foci_assay_folder,
                                     "selected_channels",
                                     "image_metadata.txt")
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
                f"Elapsed {elapsed:.1f}s, "
                f"avg {avg_time_per_task:.2f}s per task. "
                f"Remaining {tasks_left} tasks, "
                f"ETA ~ {finish_time_est.strftime('%H:%M:%S')}."
            )
        if not all_results:
            logging.warning("No data after processing. Skipping CSV save.")
            logging.getLogger('').removeHandler(fh)
            fh.close()
            continue
        df_results = pd.DataFrame(all_results)
        df_results = df_results.sort_values(by=["Image Key", "Nucleus"])
        output_csv = os.path.join(results_folder,
                                  "all_results_with_coloc_universal.csv")
        df_results.to_csv(output_csv, index=False)
        logging.info(f"Saved result: {output_csv}")
        logging.getLogger('').removeHandler(fh)
        fh.close()
    logging.info("All processing completed.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Foci/nuclei analysis with optional "
                    "universal colocalization and "
                    "black-objects/white-bg masks."
    )
    parser.add_argument("-i",
                        "--input",
                        required=True,
                        help="JSON file with folder paths.")
    args = parser.parse_args()
    main_summarize_res(args.input)
