#!/usr/bin/env python

if __name__ == '__main__':
    from runtime_worker import launch_direct
    raise SystemExit(launch_direct('quantify_foci'))

import itertools
import logging
import json
import hashlib
import plate_design as plate
import os
import re
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime, timedelta
from pathlib import Path

import collect_marker_intensity_results as collect
import image_exclusions as exclusions
import numpy as np
import pandas as pd
import spatial_calibration as spatial
from assay_layout import ASSAY_DIR
from table_workflow import TableJournal, TableProgress
from run_resources import write_json, utc_now
from interactive_input import CANCELLATION_EXCEPTIONS
from interactive_input import Cancelled, ask_yes_no, cancelable
from PIL import Image, ImageDraw
from skimage import io, measure
from validate_folders import validate_input_file


def validate_folders(input_json_path: str) -> dict:
    valid_folders = validate_input_file(input_json_path)
    folder_dicts = {}
    for base_folder in valid_folders:
        if not os.path.isdir(base_folder):
            logging.warning(f"Folder not found: {base_folder}")
            continue
        fia_assay_folder = os.path.join(base_folder, ASSAY_DIR)
        if not os.path.isdir(fia_assay_folder):
            logging.warning(f"No fia_assay folder in {base_folder}")
            continue
        folder_dicts[base_folder] = {
            "base_folder": base_folder,
            "fia_assay_folder": fia_assay_folder
        }
    return folder_dicts


def extract_image_key(filename: str) -> str:
    """
    Removes known prefixes/suffixes from a filename to derive a 'nuc_key'.
    """
    filename = re.sub(r"_nuclei_projection_StarDist_processed_processed",
                      "",
                      filename)
    filename = re.sub(r"^processed_", "", filename)
    filename = re.sub(r"_foci_projection", "", filename)
    filename = re.sub(r"\.tif$", "", filename)
    filename = re.sub(r"\.nd2", "", filename)
    return filename


def extract_metadata(metadata_path: str) -> dict:
    """
    Extracts pixel metadata from an image_metadata.txt file,
    if it exists.
    Returns a dict of { image_key: {"Pixel Width": float,
    "Pixel Height": float, ...}, ... }
    """
    return spatial.read_text_metadata(metadata_path)


def get_nuclei_mask_folder(fia_assay_folder: str, design=None) -> str:
    """
    Finds the newest folder containing
    nuclei masks (e.g., Final_Nuclei_Mask_YYYYMMDD_HHMMSS).
    """
    nuclei_mask_folders = [f for f in os.listdir(fia_assay_folder)
                           if f.startswith("Final_Nuclei_Mask_")]
    if not nuclei_mask_folders:
        raise FileNotFoundError(f"No 'Final_Nuclei_Mask_' "
                                f"folders in {fia_assay_folder}.")

    folder_timestamps = []
    for folder in nuclei_mask_folders:
        match = re.search(r"_(\d{8}_\d{6})", folder)
        if match:
            folder_timestamps.append((folder, match.group(1)))

    if not folder_timestamps:
        raise ValueError(f"Could not extract timestamps "
                         f"for 'Final_Nuclei_Mask_' "
                         f"folders in {fia_assay_folder}.")

    folder_timestamps.sort(key=lambda x: x[1], reverse=True)
    if design is not None:
        for folder, _ in folder_timestamps:
            path = Path(fia_assay_folder) / folder
            try:
                plate.validate_snapshot(path, design)
                collect.load_morphology(path)
                return str(path)
            except (OSError, ValueError, KeyError) as error:
                logging.warning('Skipping incompatible nuclei run %s: %s', path, error)
        raise ValueError('No completed compatible nuclei run; run generate_nuclei_mask first.')
    return os.path.join(fia_assay_folder, folder_timestamps[0][0])


def get_latest_foci_folders(fia_assay_folder: str, design=None) -> dict:
    """
    Finds the newest folders with foci masks for
    each channel (e.g., Foci_1_Channel_1_YYYYMMDD_HHMMSS).
    Returns a dict {channel_name: folder_path}.
    """
    if design is not None:
        result = {}
        base = Path(fia_assay_folder) / 'Foci_Masks'
        for folder in sorted(base.iterdir(), reverse=True) if base.exists() else []:
            if not folder.is_dir() or folder.name.startswith('.'):
                continue
            try:
                meta = json.loads((folder / 'foci_run.json').read_text(encoding='utf-8'))
                marker = meta['Marker_ID']
                if marker in result or meta['Status'] != 'COMPLETE':
                    continue
                plate.validate_snapshot(folder, design, 'Foci', marker)
                prepared = Path(fia_assay_folder) / 'markers' / marker
                plate.preparation_files(prepared)
                for name, checksum in meta['Inputs'].items():
                    if plate.file_digest(prepared / name) != checksum:
                        raise ValueError('Prepared marker changed after masking')
                for name, checksum in meta['Masks'].items():
                    if plate.file_digest(folder / name) != checksum:
                        raise ValueError('Foci mask changed after analysis')
                result[marker] = str(folder)
            except (OSError, ValueError, KeyError) as error:
                logging.warning('Skipping incompatible foci run %s: %s', folder, error)
        return result
    foci_masks_folder = os.path.join(fia_assay_folder,
                                     "Foci_Masks")
    if not os.path.exists(foci_masks_folder):
        raise FileNotFoundError(f"Foci_Masks folder "
                                f"does not exist in {fia_assay_folder}.")

    foci_folders = [f for f in os.listdir(foci_masks_folder)
                    if f.startswith("Foci_")]
    if not foci_folders:
        raise FileNotFoundError(f"No 'Foci_' folders in "
                                f"{foci_masks_folder}.")

    latest_foci_folders = {}
    for folder in foci_folders:
        match = re.search(r"_(\d{8}_\d{6})", folder)
        if not match:
            continue
        channel_match = re.search(r"(Foci_\d+_Channel_\d+)",
                                  folder)
        if not channel_match:
            continue

        channel_name = channel_match.group(1)
        timestamp = match.group(1)

        if channel_name in latest_foci_folders:
            # keep the folder with the latest timestamp
            if timestamp > latest_foci_folders[channel_name][1]:
                latest_foci_folders[channel_name] = (folder,
                                                     timestamp)
        else:
            latest_foci_folders[channel_name] = (folder,
                                                 timestamp)

    if not latest_foci_folders:
        raise ValueError(f"Could not extract timestamps "
                         f"for foci folders in {foci_masks_folder}.")

    return {
        channel: os.path.join(foci_masks_folder, folder)
        for channel, (folder, _) in latest_foci_folders.items()
    }


def save_labeled_image(image, output_path, title, labels=None):
    """
    Save a native-size numbered preview or an unchanged 16-bit TIFF.
    PNG previews use display scaling only. A labels dictionary maps each
    nucleus ID to (centroid_y, centroid_x); labels never alter geometry.
    """
    if labels is not None or Path(output_path).suffix.lower() == ".png":
        # Display scaling changes brightness only, never the pixel grid.
        pixels = np.asarray(image, dtype=float)
        low, high = float(np.min(pixels)), float(np.max(pixels))
        display = ((pixels - low) * 255 / (high - low)
                   if high > low else np.zeros_like(pixels))
        preview = Image.fromarray(display.astype(np.uint8)).convert("RGB")
        draw = ImageDraw.Draw(preview)
        for lbl, (cy, cx) in (labels or {}).items():
            draw.text((float(cx), float(cy)), str(lbl), fill="red", anchor="mm")
        preview.save(output_path)
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
    return os.path.join(results_folder,
                        f"{nuc_key}_labeled_nuclei.png")


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
                         image_key) -> list:
    """
    Counts how many labeled objects in 'foci_mask'
    fall inside each labeled nucleus in 'nuclei_mask'.
    Returns a list of dicts with metrics:
    Foci Count, total area, relative area, etc.
    """
    if nuclei_mask.shape != foci_mask.shape:
        raise ValueError("Nuclei mask and foci "
                         "mask must have the same shape.")

    nuclei_props = measure.regionprops(nuclei_mask)
    labeled_foci = measure.label(foci_mask)

    results = []
    for prop in nuclei_props:
        nuc_label = prop.label
        nuc_area_px = prop.area
        nuc_area_micron = nuc_area_px * pixel_area if pixel_area is not None else None

        nucleus_bool = (nuclei_mask == nuc_label)
        masked_foci = labeled_foci * nucleus_bool
        unique_foci = np.unique(masked_foci)
        unique_foci = unique_foci[unique_foci != 0]
        foci_count = len(unique_foci)

        total_foci_px = 0
        for flab in unique_foci:
            total_foci_px += np.sum(masked_foci == flab)
        total_foci_micron = total_foci_px * pixel_area if pixel_area is not None else None
        rel_area = 0.0
        if nuc_area_px > 0:
            rel_area = (total_foci_px / nuc_area_px) * 100.0

        results.append({
            "Image Key": image_key,  # Ensure this column is always present
            "Nucleus": nuc_label,    # Ensure this column is always present
            "Foci Count": foci_count,
            "Nucleus Area (pixels)": nuc_area_px,
            "Nucleus Area (micron²)": nuc_area_micron,
            "Total Foci Area (pixels)": total_foci_px,
            "Total Foci Area (micron²)": total_foci_micron,
            "Relative Foci Area (%)": round(rel_area, 2),
        })

    # Ensure the DataFrame is not empty and
    # contains the required columns
    if not results:
        results.append({
            "Image Key": image_key,
            "Nucleus": 0,
            "Foci Count": 0,
            "Nucleus Area (pixels)": 0,
            "Nucleus Area (micron²)": 0 if pixel_area is not None else None,
            "Total Foci Area (pixels)": 0,
            "Total Foci Area (micron²)": 0 if pixel_area is not None else None,
            "Relative Foci Area (%)": 0.0,
        })
    return results


# Main per-nucleus logic
def process_nuclei_image(nuc_file_path: str,
                         foci_channels_info: dict,
                         metadata: dict,
                         results_folder: str,
                         perform_colocalization: bool,
                         min_subset_size=2,
                         max_subset_size=None, requested_intersections=None) -> list:
    """
    Processes ONE nucleus mask + all corresponding foci channels.

    Args:
      nuc_file_path, foci_channels_info, metadata,
      results_folder, perform_colocalization
      min_subset_size, max_subset_size define
      which channel subsets we intersect
    Returns:
      A list of dictionaries with final results
      for each nucleus.
    """
    nuc_filename = os.path.basename(nuc_file_path)
    if nuc_filename.startswith('.'):
        logging.warning(f"Skipping hidden nuclei file: {nuc_file_path}")
        return []

    nuc_key = extract_image_key(nuc_filename)
    logging.info(f"Started nuclei processing: {nuc_filename}")

    # Load nucleus
    nuclei_mask = io.imread(nuc_file_path)
    nuclei_labels = measure.label(nuclei_mask)
    nuclei_props = measure.regionprops(nuclei_labels)

    # Save labeled nucleus image
    labels_dict = {prop.label: prop.centroid
                   for prop in nuclei_props}
    labeled_path = labeled_nuclei_path(nuc_file_path,
                                       results_folder)
    save_labeled_image(nuclei_labels,
                       labeled_path,
                       title=f"Nuclei {nuc_key}",
                       labels=labels_dict)

    # Use a saved mask calibration or explicit, dimension-checked preparation metadata.
    calibration = spatial.snapshot_calibration(nuc_file_path, nuclei_mask.shape)
    if calibration is None:
        item = metadata.get(nuc_key, {})
        expected = (item.get('Height'), item.get('Width'))
        if expected == nuclei_mask.shape and item.get('XY processing') == 'native dimensions; no resizing':
            calibration = spatial.normalize(item.get('Pixel Width'), item.get('Pixel Height'),
                                            item.get('Unit'), 'Native-size preparation metadata')
        else:
            calibration = spatial.uncalibrated('No verified native-size calibration for this mask.')
    pixel_area_micron = spatial.area_um2(1, calibration)
    if pixel_area_micron is None:
        logging.warning('No verified calibration for %s; physical areas are blank, pixel results retained.', nuc_key)

    # Load all foci channel masks
    channel_names = sorted(foci_channels_info.keys())
    channel_masks = {}
    for ch_name in channel_names:
        path_ch = foci_channels_info[ch_name]
        if os.path.basename(path_ch).startswith('.'):
            logging.warning(f"Skipping hidden foci file: {path_ch}")
            continue

        if not os.path.exists(path_ch):
            if requested_intersections is not None:
                raise ValueError(f'Required Foci mask is missing: {path_ch}')
            logging.warning(f"Foci file not found: {path_ch}")
            continue
        foci_mask = io.imread(path_ch)
        if foci_mask.shape != nuclei_mask.shape:
            if requested_intersections is not None:
                raise ValueError(f'Required Foci mask dimensions differ from nuclei: {path_ch}')
            logging.warning(f"Foci shape {foci_mask.shape} "
                            f"!= Nuclei shape {nuclei_mask.shape}. "
                            f"Skipping {path_ch}")
            continue
        channel_masks[ch_name] = foci_mask

    if not channel_masks:
        logging.warning(f"No valid foci channels "
                        f"for nucleus {nuc_key}")
        return []

    # Single-channel measurements
    all_dfs = []
    for ch_name, f_mask in channel_masks.items():
        res = count_foci_in_nuclei(nuclei_labels,
                                   f_mask,
                                   pixel_area_micron,
                                   nuc_key)
        df_res = pd.DataFrame(res)
        # rename columns
        df_res.rename(columns={
            "Foci Count":
                f"Foci Count ({ch_name})",
            "Total Foci Area (pixels)":
                f"Total Foci Area (pixels) ({ch_name})",
            "Total Foci Area (micron²)":
                f"Total Foci Area (micron²) ({ch_name})",
            "Relative Foci Area (%)":
                f"Relative Foci Area (%) ({ch_name})"
        }, inplace=True)
        all_dfs.append(df_res)

    # Merge single channels
    df_single = all_dfs[0]
    for df_next in all_dfs[1:]:
        # we allow duplicates of "Nucleus Area (pixels)"
        # but they are the same => no conflict yet
        df_next = df_next.drop(columns=['Nucleus Area (pixels)', 'Nucleus Area (micron²)'])
        df_single = df_single.merge(df_next,
                                    on=["Image Key", "Nucleus"],
                                    how="outer")

    for field, value in spatial.columns(calibration).items():
        df_single[field] = value

    # If user doesn't want colocalization => return single
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
    subsets = (requested_intersections if requested_intersections is not None else
               [subset for r in range(min_subset_size, max_subset_size + 1)
                for subset in itertools.combinations(ch_name_list, r)])
    for subset in subsets:
        if any(ch not in channel_masks for ch in subset):
            raise ValueError(f'Requested intersection lacks a valid mask: {subset}')
        subset_str = "+".join(subset)
        # Build labeled intersection
        masks_to_intersect = [channel_masks[ch] for ch in subset]
        intersection_mask = build_intersection_mask(*masks_to_intersect)

        # Convert to black-objects, white-background:
        #   objects = 0, background = 255
        # This is a binary representation, losing distinct labels
        # but satisfying the user's request
        # for black object / white background
        inverted_mask = (np.where(intersection_mask > 0, 0, 255)
                         .astype(np.uint8))

        # Save new mask
        intersection_name = f"intersection_{plate.digest([nuc_key, sorted(subset)])[:24]}.tif"
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
                                                    on=["Image Key",
                                                        "Nucleus"],
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


# Gathering folders
def gather_paths_and_channels(base_folder: str, design=None):
    """
    1) Finds the newest nuclei folder,
    2) Collects all *.tif files (nuclei),
    3) Finds the newest foci folders by channel,
    4) Builds channels_dict[nuc_key][channel_name] = path_to_foci_file
    """
    fia_assay_folder = os.path.join(base_folder,
                                     ASSAY_DIR)
    nuclei_mask_folder = get_nuclei_mask_folder(fia_assay_folder, design)
    if design:
        plate.validate_snapshot(nuclei_mask_folder, design)
    excluded = exclusions.load_exclusions(nuclei_mask_folder)
    excluded.update(exclusions.load_exclusions(fia_assay_folder))

    nuclei_files = []
    for f in os.listdir(nuclei_mask_folder):
        if not f.startswith('.') and f.endswith(".tif") and exclusions.image_key(f) not in excluded:
            if design is None or design.assigned(plate.well_from_name(f), 'Foci'):
                nuclei_files.append(os.path.join(nuclei_mask_folder, f))

    latest_foci = get_latest_foci_folders(fia_assay_folder, design)
    channels_dict = {}

    for channel_name, foci_folder_path in latest_foci.items():
        declared = (set(json.loads((Path(foci_folder_path) / 'foci_run.json').read_text())['Masks'])
                    if design else None)
        foci_files = [fn for fn in
                      os.listdir(foci_folder_path)
                      if not fn.startswith('.') and fn.endswith(".tif")]
        for tif_file in foci_files:
            if declared is not None and tif_file not in declared:
                continue
            if exclusions.image_key(tif_file) in excluded:
                continue
            if design and not design.assigned(plate.well_from_name(tif_file), 'Foci', channel_name):
                continue
            foci_key = extract_image_key(tif_file)
            if foci_key not in channels_dict:
                channels_dict[foci_key] = {}
            channels_dict[foci_key][channel_name] = os.path.join(foci_folder_path,
                                                                 tif_file)

    return nuclei_files, channels_dict


# Parallel driver
def parallel_processing(nuclei_files: list,
                        channels_dict: dict,
                        metadata: dict,
                        results_folder: str,
                        perform_colocalization: bool,
                        max_workers: int, design=None, progress=None, logger=None) -> pd.DataFrame:
    """gi
    Runs parallel processing of the given list of nuclei_files.
    Gathers all results into a single DataFrame.
    Logs approximate time to finish after each completed task.
    """

    all_results = []
    total_tasks = len(nuclei_files)
    start_time = time.time()

    logging.info(f"Total tasks (nuclei files): {total_tasks}. "
                 f"Starting pool with max_workers={max_workers}.")

    tasks = []
    for nuc_file_path in nuclei_files:
        nuc_filename = os.path.basename(nuc_file_path)
        nuc_key = extract_image_key(nuc_filename)

        if nuc_key not in channels_dict:
            if design:
                raise ValueError(f"Missing required Foci masks for {nuc_key}")
            logging.warning(f"No foci channels found for {nuc_key}")
            continue

        foci_channels_info = channels_dict[nuc_key]
        intersections = None
        if design:
            well = plate.well_from_name(nuc_filename)
            item = design.wells[well]
            expected = {row['id'] for row in item['channels'] if row and row['method'] == 'Foci'}
            if set(foci_channels_info) != expected:
                raise ValueError(f'{well}: required Foci masks missing; rerun generate_foci_mask for each assigned marker')
            intersections = [tuple(sorted(item['channels'][c-1]['id'] for c in combo))
                             for combo in item['intersections']]
        tasks.append((nuc_file_path, foci_channels_info, intersections))

    if progress:
        progress.phase('Measuring Foci and requested intersections', len(tasks))
    with ProcessPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(process_nuclei_image, path, channels, metadata, results_folder,
                                   perform_colocalization, requested_intersections=combinations): path
                   for path, channels, combinations in tasks}

        for i, fut in enumerate(as_completed(futures), start=1):
            partial_res = fut.result()  # list of dict
            all_results.extend(partial_res)
            if progress:
                progress.advance(futures[fut])
            if logger:
                logger.info('IMAGE_COMPLETE | %s | rows=%s', futures[fut], len(partial_res))

            elapsed = time.time() - start_time
            avg_time_per_task = elapsed / i
            tasks_left = len(futures) - i
            eta_seconds = avg_time_per_task * tasks_left
            finish_time_est = datetime.now() + timedelta(seconds=eta_seconds)

            logging.info(
                f"Task {i}/{len(futures)} completed. "
                f"Elapsed {elapsed:.1f}s, "
                f"avg {avg_time_per_task:.2f}s per task. "
                f"Remaining {tasks_left} tasks, "
                f"ETA ~ {finish_time_est.strftime('%H:%M:%S')}."
            )

    df = pd.DataFrame(all_results)
    return df


def main_summarize_res(input_json_path: str, njobs=4):
    """Quantify assigned markers and explicitly requested intersections."""
    audit = TableJournal('4_foci_quantification.log', input_json_path)
    failures = 0
    try:
        if njobs < 1:
            raise ValueError('Worker count must be positive')
        designs = plate.preflight(input_json_path, command='quantify_foci')
        selected = [(root, design) for root, design in designs.items() if design.markers('Foci')]
        if not selected:
            print('No Foci assignments; nothing to quantify.')
        for root, _ in selected:
            audit.register(root)
        with TableProgress() as progress:
            for root, design in selected:
                output = None
                state = {'Schema_version': 1, 'Status': 'RUNNING', 'Started_UTC': utc_now()}
                try:
                    with audit.stage(root, 'Foci quantification', progress) as logger:
                        progress.group(f'Foci | {root.name}')
                        nuclei_files, channels = gather_paths_and_channels(str(root), design)
                        if not nuclei_files:
                            logger.info('NO_IMAGES | No eligible Foci images')
                            continue
                        base = root / ASSAY_DIR / 'foci_analysis'
                        stamp = datetime.now()
                        output = base / f'Results_{stamp:%Y%m%d_%H%M%S}'
                        while output.exists():
                            stamp += timedelta(seconds=1)
                            output = base / f'Results_{stamp:%Y%m%d_%H%M%S}'
                        output.mkdir(parents=True)
                        plate.save_snapshot(output, design)
                        nucleus_run = Path(nuclei_files[0]).parent
                        state.update(Nuclei_run=str(nucleus_run), Markers=design.markers('Foci'),
                                     Input_masks={str(path): plate.file_digest(path) for path in nuclei_files},
                                     Foci_masks={path: plate.file_digest(path) for rows in channels.values() for path in rows.values()})
                        write_json(output / 'foci_quantification_run.json', state)
                        excluded = exclusions.load_exclusions(nucleus_run)
                        excluded.update(exclusions.load_exclusions(root / ASSAY_DIR))
                        exclusions.write_snapshot(output, excluded)
                        metadata = extract_metadata(str(root / ASSAY_DIR / 'image_metadata.txt'))
                        df = parallel_processing(nuclei_files, channels, metadata, str(output), True,
                                                 njobs, design, progress, logger)
                        if df.empty:
                            raise ValueError('No Foci measurements returned for the planned images')
                        df['Well'] = df['Image Key'].map(plate.well_from_name)
                        for marker in design.markers('Foci'):
                            mid = marker['id']
                            df[mid + '_Availability'] = df['Well'].map(
                                lambda w: 'AVAILABLE' if design.assigned(w, 'Foci', mid) else 'NOT_APPLICABLE')
                            df[mid + '_Source_channel'] = df['Well'].map(
                                lambda w: design.channel(w, 'Foci', mid) if design.assigned(w, 'Foci', mid) else None)
                        df.sort_values(['Image Key', 'Nucleus']).to_csv(output / 'all_results_with_coloc_universal.csv', index=False)
                        pd.DataFrame([{'Marker_ID': m['id'], 'Marker_name': m['name'], 'Well': w, 'Original_channel': c}
                                      for m in design.markers('Foci') for w, c in m['channels'].items()]).to_csv(
                                          output / 'Marker_Assignments.csv', index=False)
                        intersections = []
                        for path in nuclei_files:
                            key = extract_image_key(Path(path).name)
                            well = plate.well_from_name(path)
                            for combo in design.wells[well]['intersections']:
                                mids = sorted(design.wells[well]['channels'][c-1]['id'] for c in combo)
                                intersections.append({'Image_key': key, 'Well': well,
                                    'Original_channels': '+'.join('C'+str(c) for c in combo), 'Marker_IDs': '+'.join(mids),
                                    'Mask': f"intersection_{plate.digest([key, mids])[:24]}.tif"})
                        pd.DataFrame(intersections, columns=['Image_key', 'Well', 'Original_channels', 'Marker_IDs', 'Mask']).to_csv(
                            output / 'Requested_Intersections.csv', index=False)
                        state.update(Status='SUCCESS', Images=len(nuclei_files), Rows=len(df))
                        audit.result(root, output, 'SUCCESS', output)
                        progress.message(f'SUCCESS: {output}')
                except CANCELLATION_EXCEPTIONS:
                    state.update(Status='CANCELLED')
                    raise
                except Exception as error:
                    failures += 1
                    state.update(Status='FAILED', Error=str(error))
                    audit.incomplete(root, str(error))
                finally:
                    if output is not None:
                        state['Ended_UTC'] = utc_now()
                        write_json(output / 'foci_quantification_run.json', state)
        audit.status = 'INCOMPLETE' if failures else 'COMPLETE'
        return 1 if failures else 0
    except CANCELLATION_EXCEPTIONS:
        audit.status = 'CANCELLED'
        return 130
    except Exception as error:
        audit.status = 'FAILED'
        print(f'Foci quantification failed: {error}')
        return 1
    finally:
        audit.finish()
