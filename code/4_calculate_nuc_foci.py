#!/usr/bin/env python3
import argparse
import os
import re
from datetime import datetime
from pathlib import Path

import imagej
from scyjava import jimport
from validate_folders import validate_path_files


def compute_summary_from_rt(rt):
    """
    Compute a summary from a ResultsTable.
    We compute:
    - count: number of detected objects
    - total_area: sum of the "Area" column
    - mean_area: average area per object
    (renamed as AverageSize in the summary output)

    Returns a string: "count,total_area,mean_area"
    """
    count = rt.size()
    if count == 0:
        return "0,0,0"
    area_index = rt.getColumnIndex("Area")

    total_area = 0.0
    for i in range(count):
        if area_index != -1:
            total_area += rt.getValue("Area", i)

    mean_area = (total_area / count) if count > 0 else 0
    return f"{count},{total_area},{mean_area}"


def analyze_particles(imp,
                      min_size=0.0,
                      max_size=1e9,
                      min_circ=0.0,
                      max_circ=1.0):
    """
    Analyze particles on the given ImagePlus 'imp' using ParticleAnalyzer.
    We measure AREA and MEAN.
    %Area is no longer computed or stored.
    """
    ParticleAnalyzer = jimport('ij.plugin.filter.ParticleAnalyzer')
    ResultsTable = jimport('ij.measure.ResultsTable')
    Measurements = jimport('ij.measure.Measurements')

    # Include AREA and MEAN
    measurements = Measurements.AREA | Measurements.MEAN
    options = ParticleAnalyzer.SHOW_NONE  # no GUI displays

    rt = ResultsTable()
    pa = ParticleAnalyzer(options,
                          measurements,
                          rt,
                          min_size,
                          max_size,
                          min_circ,
                          max_circ)
    success = pa.analyze(imp)
    if not success:
        raise ValueError("Particle analysis failed.")

    return rt


def calculate_nuc_foci(folder: dict) -> None:
    """
    Process nuclei and foci images in headless mode:
    - Analyze nuclei masks and foci masks using ParticleAnalyzer
    - %Area is removed from the calculations
    - MeanArea is renamed to AverageSize in the summary output
    """

    print("Initializing ImageJ...")
    ij = imagej.init('sc.fiji:fiji', mode='headless')
    print(f"ImageJ initialization completed. Version: {ij.getVersion()}")

    IJ = jimport('ij.IJ')
    ImageCalculator = jimport('ij.plugin.ImageCalculator')

    now = datetime.now()
    timestamp = now.strftime("%Y%m%d_%H%M%S")

    results_folder = os.path.join(folder['base_folder'],
                                  'foci_analysis',
                                  f'Nuclei_count_results_{timestamp}')
    Path(results_folder).mkdir(parents=True, exist_ok=True)

    foci_results_folder = os.path.join(folder['base_folder'],
                                       'foci_analysis',
                                       f'Foci_count_results_{timestamp}')
    Path(foci_results_folder).mkdir(parents=True, exist_ok=True)

    final_nuclei_mask_folder = folder['final_nuclei_mask_folder']
    foci_masks_folder = folder['foci_masks_folder']
    star_dist_files = folder['star_dist_files']
    foci_projection_files = folder['foci_projection_files']
    foci_assay_folder = folder['foci_assay_folder']

    nuclei_summaries = []
    foci_summaries = []
    nuclei_summary_headers = "Filename,Count,TotalArea,AverageSize"
    foci_summary_headers = "Filename,Count,TotalArea,AverageSize"

    # ----------------------------
    # Process Nuclei
    # ----------------------------
    print("\n--- Processing Nuclei Masks ---")

    for filename in star_dist_files:
        file_path = os.path.join(final_nuclei_mask_folder,
                                 filename)

        if not os.path.isfile(file_path):
            print(f"'{file_path}' is not a file. Skipping.")
            continue

        print(f"\nProcessing file: {file_path}")
        IJ.run("Close All")

        imp = IJ.openImage(file_path)
        if imp is None:
            print(f"Failed to open image: {file_path}")
            continue

        rt = analyze_particles(imp)
        if rt is None or rt.size() == 0:
            print("No nuclei detected.")
        else:
            # Save per-nucleus results
            results_filename = (f"{os.path.splitext(filename)[0]}"
                                f"_Each_nucleus.csv")
            results_file_path = os.path.join(results_folder, results_filename)
            rt.save(results_file_path)
            print(f"Per nucleus results saved to '{results_file_path}'.")

            # Compute summary
            summary_line = compute_summary_from_rt(rt)
            summary_line = f"{filename},{summary_line}"
            nuclei_summaries.append(summary_line)

        imp.close()
        IJ.run("Close All")

    # Save combined summary for nuclei
    if nuclei_summaries:
        name_file = (f"{os.path.basename(folder['base_folder'])}_"
                     f"Combined_Summary_nuclei.csv")
        combined_summary_file = os.path.join(results_folder,
                                             name_file)
        with open(combined_summary_file,
                  'w', encoding='utf-8') as sf:
            sf.write(nuclei_summary_headers + '\n')
            for line in nuclei_summaries:
                sf.write(line + '\n')
        print(f"Combined summary for nuclei "
              f"saved to '{combined_summary_file}'.")
    else:
        print(f"No nuclei summary data to "
              f"save in folder '{folder['base_folder']}'.")

    # ----------------------------
    # Process Foci
    # ----------------------------
    print("\n--- Processing Foci Masks ---")
    name_file = f'Foci_in_nuclei_final_{timestamp}'
    foci_in_nuclei_final_folder = os.path.join(foci_assay_folder,
                                               name_file)
    Path(foci_in_nuclei_final_folder).mkdir(parents=True, exist_ok=True)
    print(f"Created folder for "
          f"combined foci masks: '{foci_in_nuclei_final_folder}'")

    for foci_filename in foci_projection_files:
        foci_file_path = os.path.join(foci_masks_folder,
                                      foci_filename)

        if not os.path.isfile(foci_file_path):
            print(f"'{foci_file_path}' is not a file. Skipping.")
            continue

        print(f"\nProcessing foci file: {foci_file_path}")

        match = re.match(r'processed_(.+?)_foci_projection\.tif',
                         foci_filename)
        if match is None:
            print(f"Could not extract common part "
                  f"from filename '{foci_filename}'. Skipping.")
            continue
        common_part = match.group(1)

        corr_nuclei_filename = (f"processed_{common_part}_nuclei_"
                                f"projection_StarDist_processed.tif")
        corr_nuclei_file_path = os.path.join(final_nuclei_mask_folder,
                                             corr_nuclei_filename)

        if not os.path.isfile(corr_nuclei_file_path):
            print(f"Corresponding nuclei file "
                  f"'{corr_nuclei_filename}' not found. Skipping.")
            continue

        print(f"Found corresponding nuclei file: {corr_nuclei_file_path}")

        IJ.run("Close All")

        imp_foci = IJ.openImage(foci_file_path)
        if imp_foci is None:
            print(f"Failed to open foci image: "
                  f"{foci_file_path}. Skipping.")
            continue

        imp_nuclei = IJ.openImage(corr_nuclei_file_path)
        if imp_nuclei is None:
            print(f"Failed to open nuclei image: "
                  f"{corr_nuclei_file_path}. Skipping.")
            imp_foci.close()
            continue

        ic = ImageCalculator()
        imp3 = ic.run(imp_foci, imp_nuclei, "AND create")
        if imp3 is None:
            print(f"Failed to create combined mask "
                  f"for '{foci_filename}'. Skipping.")
            imp_foci.close()
            imp_nuclei.close()
            continue

        # Save combined mask
        result_mask_filename = f"Result_of_{foci_filename}"
        result_mask_file_path = os.path.join(foci_in_nuclei_final_folder,
                                             result_mask_filename)
        IJ.saveAs(imp3, "Tiff", result_mask_file_path)
        print(f"Combined mask saved to '{result_mask_file_path}'.")

        # Analyze foci
        rt_foci = analyze_particles(imp3)
        if rt_foci is None or rt_foci.size() == 0:
            print("No foci detected.")
        else:
            foci_analysis_filename = (f"{os.path.splitext(foci_filename)[0]}"
                                      f"_foci_analysis.csv")
            foci_analysis_file_path = os.path.join(foci_results_folder,
                                                   foci_analysis_filename)
            rt_foci.save(foci_analysis_file_path)
            print(f"Foci analysis results "
                  f"saved to '{foci_analysis_file_path}'.")

            summary_line = compute_summary_from_rt(rt_foci)
            summary_line = f"{result_mask_filename},{summary_line}"
            foci_summaries.append(summary_line)

        imp_foci.close()
        imp_nuclei.close()
        imp3.close()
        IJ.run("Close All")

    # Save combined summary for foci
    if foci_summaries:
        name_file = (f"{os.path.basename(folder['base_folder'])}"
                     f"_Combined_Summary_foci.csv")
        combined_foci_summary_file = os.path.join(foci_results_folder,
                                                  name_file)
        with open(combined_foci_summary_file,
                  'w', encoding='utf-8') as sf:
            sf.write(foci_summary_headers + '\n')
            for line in foci_summaries:
                sf.write(line + '\n')
        print(f"Combined summary for foci "
              f"saved to '{combined_foci_summary_file}'.")
    else:
        print(f"No foci summary data to "
              f"save in folder '{folder['base_folder']}'.")


def main_summarize_res(input_json_path: str) -> None:
    """
    Main function:
    - Validate folders using validate_folders.validate_path_files
    - Prompt user to start processing
    - Run processing for each path
    """
    folders = validate_path_files(input_json_path, step=4)

    start_processing = input("\nDo you want to start processing "
                             "files? (yes/no): ").strip().lower()
    if start_processing not in ('yes', 'y'):
        raise ValueError("File processing canceled by user.")

    print("\nStarting image processing...")
    for path in folders.keys():
        calculate_nuc_foci(folders[path])
    print("\nImage processing completed.")


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('-i',
                        '--input',
                        type=str,
                        help="JSON file with directory paths",
                        required=True)
    args = parser.parse_args()
    main_summarize_res(args.input)
