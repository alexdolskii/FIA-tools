#!/usr/bin/env python3
"""
5_graph_representation.py

Reads a JSON file of folder paths, each containing subfolders like
Foci_count_results_YYYYMMDD_HHMMSS and Nuclei_count_results_YYYYMMDD_HHMMSS
with CSV files Combined_Summary_foci.csv / Combined_Summary_nuclei.csv.

Combines all data into two Pandas DataFrames (foci, nuclei) + computes ratios
for each folder. Then generates:

1) Foci boxplots (Count, TotalArea, AverageSize) as 3 separate subplots
2) Nuclei boxplots (Count, TotalArea, AverageSize) as 3 separate subplots
3) Ratio boxplots: CountRatio, AreaRatio

Outputs are placed in the *first* folder from the JSON file under:
foci_analysis/graphs_representation_YYYYMMDD_HHMMSS
"""

import os
import re
import argparse
import logging
import pandas as pd
import matplotlib.pyplot as plt
from datetime import datetime
from pathlib import Path

from validate_folders import validate_path_files


def find_latest_foci_nuclei_folders(foci_analysis_path: str):
    """
    Search in 'foci_analysis_path' for the newest matching:
      - Foci_count_results_YYYYMMDD_HHMMSS
      - Nuclei_count_results_YYYYMMDD_HHMMSS
    with the same timestamp.

    Return (foci_folder, nuclei_folder, timestamp_str)
    or (None, None, None) if not found.
    """
    pattern_foci = r'^Foci_count_results_(\d{8}_\d{6})$'
    pattern_nuc = r'^Nuclei_count_results_(\d{8}_\d{6})$'
    foci_dict = {}
    nuclei_dict = {}

    if not os.path.isdir(foci_analysis_path):
        return None, None, None

    for name in os.listdir(foci_analysis_path):
        full_path = os.path.join(foci_analysis_path, name)
        if not os.path.isdir(full_path):
            continue
        mf = re.match(pattern_foci, name)
        mn = re.match(pattern_nuc, name)
        if mf:
            stamp = mf.group(1)  # e.g. '20250118_191928'
            foci_dict[stamp] = full_path
        elif mn:
            stamp = mn.group(1)
            nuclei_dict[stamp] = full_path

    # Find common timestamps
    common_stamps = set(foci_dict.keys()).intersection(nuclei_dict.keys())
    if not common_stamps:
        return None, None, None

    # Pick the newest by sorting date/time
    def stamp_to_dt(s):
        return datetime.strptime(s, "%Y%m%d_%H%M%S")

    newest_stamp = max(common_stamps, key=lambda s: stamp_to_dt(s))
    return foci_dict[newest_stamp], nuclei_dict[newest_stamp], newest_stamp


def load_combined_summary(file_path: str,
                          columns=('Filename','Count','TotalArea','AverageSize')):
    """
    Loads a CSV with headers matching 'columns', e.g.:
        Filename,Count,TotalArea,AverageSize
    Returns a DataFrame or None on error.
    """
    if not os.path.isfile(file_path):
        logging.error(f"File not found: {file_path}")
        return None

    try:
        df = pd.read_csv(file_path)
    except Exception as e:
        logging.error(f"Failed to read CSV '{file_path}': {e}")
        return None

    for col in columns:
        if col not in df.columns:
            logging.error(f"Column '{col}' not found in {file_path}.")
            return None

    # Convert numeric columns
    for num_col in ['Count','TotalArea','AverageSize']:
        df[num_col] = pd.to_numeric(df[num_col], errors='coerce')
    return df


def gather_data_for_folder(folder_path: str) -> (pd.DataFrame, pd.DataFrame):
    """
    For a single 'folder_path', find the newest Foci_count_results and
    Nuclei_count_results with the same timestamp. Load Combined_Summary_foci.csv
    and Combined_Summary_nuclei.csv. Return two DataFrames with columns:
       [Filename, Count, TotalArea, AverageSize, Folder]
    or (None, None) if not found or error.

    We assume the CSVs are named:
      <basename(folder)>_Combined_Summary_foci.csv
      <basename(folder)>_Combined_Summary_nuclei.csv
    under the matched subfolders.
    """
    foci_analysis_path = os.path.join(folder_path, "foci_analysis")
    if not os.path.isdir(foci_analysis_path):
        logging.warning(f"No 'foci_analysis' directory in {folder_path}. Skipping.")
        return None, None

    foci_folder, nuclei_folder, stamp = find_latest_foci_nuclei_folders(foci_analysis_path)
    if foci_folder is None or nuclei_folder is None or stamp is None:
        logging.warning(f"No matching Foci/Nuclei_count_results_* found in {folder_path}. Skipping.")
        return None, None

    # Construct CSV paths
    base_name = os.path.basename(folder_path)
    foci_csv = os.path.join(foci_folder, f"{base_name}_Combined_Summary_foci.csv")
    nuclei_csv = os.path.join(nuclei_folder, f"{base_name}_Combined_Summary_nuclei.csv")

    df_foci = load_combined_summary(foci_csv)
    df_nuc = load_combined_summary(nuclei_csv)
    if df_foci is None or df_nuc is None:
        return None, None

    # Add an extra column "Folder" or "Condition" to each row
    df_foci['Folder'] = base_name
    df_nuc['Folder'] = base_name
    return df_foci, df_nuc


def create_multi_subplot_boxplot(df: pd.DataFrame,
                                 measures=('Count','TotalArea','AverageSize'),
                                 title='Data Summary',
                                 output_path='output.png'):
    """
    Creates a figure with one subplot per measure, each subplot is a boxplot
    grouped by 'Folder'.

    - X-axis = each Folder (group)
    - Y-axis = measure value (Count, or TotalArea, or AverageSize)
    - Different y-scale for each subplot
    """
    folders = df['Folder'].unique()
    n_measures = len(measures)
    fig, axes = plt.subplots(nrows=1, ncols=n_measures,
                             figsize=(4*n_measures, 5),
                             sharex=False, sharey=False)

    if n_measures == 1:
        # If there's only one measure, axes won't be a list
        axes = [axes]

    for i, measure in enumerate(measures):
        # We create a subset with only that measure, and group by 'Folder'
        sub_data = []
        for folder in folders:
            # all rows for this folder
            vals = df.loc[df['Folder'] == folder, measure].dropna().values
            sub_data.append(vals)

        # Boxplot in axes[i]
        ax = axes[i]
        bp = ax.boxplot(sub_data, showfliers=False, patch_artist=True)
        # Overplot points
        for j, arr in enumerate(sub_data):
            xvals = [j+1]*len(arr)
            ax.scatter(xvals, arr, alpha=0.6, color='blue', edgecolor='k')

        ax.set_title(f"{measure}")
        ax.set_xticks(range(1, len(folders)+1))
        ax.set_xticklabels(folders, rotation=45, ha='right')
        ax.set_ylabel(measure)

    fig.suptitle(title)
    plt.tight_layout()
    plt.savefig(output_path, dpi=150)
    plt.close(fig)


def main_graphs_representation(input_json_path: str):
    """
    Main function:

    1) Reads a JSON with multiple folder paths.
    2) Merges all data from all folders into two DataFrames: fociDF, nucleiDF.
    3) Computes "folder-based ratio" => single ratio per folder:
        CountRatio = sum(Count_foci) / sum(Count_nuc)
        AreaRatio = sum(TotalArea_foci) / sum(TotalArea_nuc)
    4) Creates a new folder "graphs_representation_YYYYMMDD_HHMMSS" inside the
       *first* folder's "foci_analysis".
    5) Generates:
        - Foci: boxplot figure (3 subplots: Count, TotalArea, AverageSize)
        - Nuclei: boxplot figure (3 subplots)
        - Ratios: boxplot figure (2 subplots: CountRatio, AreaRatio)
          For each "Folder" we have exactly 1 data point for each ratio.
    """

    logging.basicConfig(level=logging.WARNING,
                        format='%(asctime)s - %(levelname)s - %(message)s')

    # STEP 1: Validate the JSON paths
    valid_folders = validate_path_files(input_json_path, step=1)
    if not valid_folders:
        logging.error("No valid folders found. Exiting.")
        return

    # Ask user if we want to generate graphs
    start_proc = input("\nGenerate graphs for all data in the JSON? (yes/no): ").strip().lower()
    if start_proc in ('no','n'):
        logging.warning("Graph generation canceled.")
        return
    elif start_proc not in ('yes','y'):
        raise ValueError("Incorrect input. Please enter yes/no")

    # STEP 2: Gather data from each folder
    all_foci = []
    all_nuclei = []
    for folder_path in valid_folders:
        df_foci, df_nuc = gather_data_for_folder(folder_path)
        if df_foci is not None and df_nuc is not None:
            all_foci.append(df_foci)
            all_nuclei.append(df_nuc)

    if not all_foci or not all_nuclei:
        logging.error("No data frames were successfully loaded. Exiting.")
        return

    # Combine all into single DataFrame
    df_foci_all = pd.concat(all_foci, ignore_index=True)
    df_nuclei_all = pd.concat(all_nuclei, ignore_index=True)

    # STEP 3: For the Ratio, we compute ONE ratio per folder (summing)
    # For each folder, sum Count_foci, sum TotalArea_foci,
    # and sum Count_nuc, sum TotalArea_nuc, then compute ratio
    ratio_rows = []
    for folder_name in df_foci_all['Folder'].unique():
        # sum foci
        sub_foci = df_foci_all.loc[df_foci_all['Folder'] == folder_name]
        foci_count_sum = sub_foci['Count'].sum()
        foci_area_sum = sub_foci['TotalArea'].sum()

        # sum nuclei
        sub_nuc = df_nuclei_all.loc[df_nuclei_all['Folder'] == folder_name]
        nuc_count_sum = sub_nuc['Count'].sum()
        nuc_area_sum = sub_nuc['TotalArea'].sum()

        # compute ratio
        count_ratio = foci_count_sum / nuc_count_sum if nuc_count_sum != 0 else None
        area_ratio = foci_area_sum / nuc_area_sum if nuc_area_sum != 0 else None

        ratio_rows.append({
            'Folder': folder_name,
            'CountRatio': count_ratio,
            'AreaRatio': area_ratio
        })

    df_ratio = pd.DataFrame(ratio_rows)

    # STEP 4: Create the final output folder inside the FIRST folder
    first_folder = valid_folders[0]
    foci_analysis_path = os.path.join(first_folder, "foci_analysis")
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    graphs_folder_name = f"graphs_representation_{timestamp}"
    graphs_folder = os.path.join(foci_analysis_path, graphs_folder_name)
    os.makedirs(graphs_folder, exist_ok=True)

    print(f"\nA single folder for graphs has been created in:\n{graphs_folder}")

    # STEP 5: Generate the 3 figures

    # (1) Foci
    foci_fig_path = os.path.join(graphs_folder, "foci_boxplots.png")
    create_multi_subplot_boxplot(df_foci_all,
                                 measures=('Count','TotalArea','AverageSize'),
                                 title='Foci Data (All Folders)',
                                 output_path=foci_fig_path)
    print(f"Saved Foci boxplots to {foci_fig_path}")

    # (2) Nuclei
    nuc_fig_path = os.path.join(graphs_folder, "nuclei_boxplots.png")
    create_multi_subplot_boxplot(df_nuclei_all,
                                 measures=('Count','TotalArea','AverageSize'),
                                 title='Nuclei Data (All Folders)',
                                 output_path=nuc_fig_path)
    print(f"Saved Nuclei boxplots to {nuc_fig_path}")

    # (3) Ratio figure: 2 subplots => CountRatio, AreaRatio
    #    We do something similar with create_multi_subplot_boxplot,
    #    but we'll pass our ratio DF and 2 columns. Each folder is a single value => box with 1 data point
    ratio_fig_path = os.path.join(graphs_folder, "ratio_boxplots.png")

    # We'll reuse the same method (create_multi_subplot_boxplot) but with 2 measures
    # and a simpler approach. Each row is one point => the boxplot won't have whiskers if there's 1 row.
    create_multi_subplot_boxplot(df_ratio,
                                 measures=('CountRatio', 'AreaRatio'),
                                 title='Foci/Nuclei Ratios (All Folders)',
                                 output_path=ratio_fig_path)
    print(f"Saved ratio boxplots to {ratio_fig_path}")

    print("\nGraph generation completed.")


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('-i','--input',
                        type=str,
                        help="JSON file with paths_to_files",
                        required=True)
    args = parser.parse_args()

    main_graphs_representation(args.input)
