#!/usr/bin/env python3
import imagej
import os
from pathlib import Path
from scyjava import jimport
from datetime import datetime
import re

import argparse
from validate_folders import validate_path_files


# Function to extract the common part of the filename
def extract_common_part(foci_filename):
    """
    Extracts the common part of the filename before the '_foci_projection.tif'.

    Example:
        'processed_CRT no gem cs1_001_foci_projection.tif' -> 'CRT no gem cs1_001'
    """
    match = re.match(r'processed_(.+?)_foci_projection\.tif', foci_filename)
    if match:
        return match.group(1)
    else:
        return None


def calculate_nuc_foci(folder: dict):
    # Initialize ImageJ in interactive mode
    print("Initializing ImageJ...")
    ij = imagej.init('sc.fiji:fiji', mode='headless')
    print("ImageJ initialization completed.")

    # Import Java classes
    IJ = jimport('ij.IJ')
    Prefs = jimport('ij.Prefs')
    WindowManager = jimport('ij.WindowManager')
    ImageCalculator = jimport('ij.plugin.ImageCalculator')

    # Get current date and time for folder naming
    now = datetime.now()
    timestamp = now.strftime("%Y%m%d_%H%M%S")

    # Prepare paths for saving results
    results_folder = os.path.join(folder['base_folder'], 'foci_analysis', f'Nuclei_count_results_{timestamp}')
    Path(results_folder).mkdir(parents=True, exist_ok=True)

    # Prepare folder for Foci_count_results
    foci_results_folder = os.path.join(folder['base_folder'], 'foci_analysis', f'Foci_count_results_{timestamp}')
    Path(foci_results_folder).mkdir(parents=True, exist_ok=True)

    final_nuclei_mask_folder = folder['final_nuclei_mask_folder']
    foci_masks_folder = folder['foci_masks_folder']
    star_dist_files = folder['star_dist_files']
    foci_projection_files = folder['foci_projection_files']
    foci_assay_folder = folder['foci_assay_folder']
    # Initialize lists for collecting summaries
    nuclei_summaries = []
    foci_summaries = []
    nuclei_summary_headers = None  # To store headers from the first summary
    foci_summary_headers = None  # To store headers from the first foci summary

    # ----------------------------
    # Section 1: Processing Nuclei
    # ----------------------------
    print("\n--- Processing Nuclei Masks ---")

    for filename in star_dist_files:
        file_path = os.path.join(final_nuclei_mask_folder, filename)

        if not os.path.isfile(file_path):
            print(f"'{file_path}' is not a file. Skipping.")
            continue
        print(f"\nProcessing file: {file_path}")
        # Close all open images before processing
        IJ.run("Close All")
        # Open image
        imp = IJ.openImage(file_path)
        if imp is None:
            print(f"Failed to open image: {file_path}")
            continue
        else:
            print(f"Image '{filename}' successfully opened.")

        # Analyze Particles with fixed size=0-Infinity
        IJ.run(imp, "Analyze Particles...", "size=0-Infinity pixel show=Masks display summarize")

        # Find mask image created by Analyze Particles
        mask_imp = IJ.getImage()
        if mask_imp is not None:
            print("Mask created by Analyze Particles found (not saving).")
            # Mask_imp is available for further work
        else:
            print("Mask created by Analyze Particles not found.")

        # Save results for each nucleus
        if WindowManager.getWindow("Results") is not None:
            results_filename = f"{os.path.splitext(filename)[0]}_Each_nucleus.csv"
            results_file_path = os.path.join(results_folder, results_filename)
            IJ.selectWindow("Results")
            IJ.saveAs("Results", results_file_path)
            print(f"Per nucleus results saved to '{results_file_path}'.")
            IJ.run("Close")  # Close results window
        else:
            print("'Results' window not found. Skipping saving results table.")

        # Get data from summary table
        summary_window = WindowManager.getWindow("Summary")
        if summary_window is not None:
            summary_text_panel = summary_window.getTextPanel()
            summary_text = summary_text_panel.getText()
            # Convert Java string to Python string
            summary_text_py = str(summary_text)
            lines = summary_text_py.strip().split('\n')
            if len(lines) >= 2:
                # First line is header
                # Second line is data
                if not nuclei_summary_headers:
                    nuclei_summary_headers = "Filename," + lines[0].replace('\t', ',')
                summary_line = f"{filename}," + lines[1].replace('\t', ',')
                nuclei_summaries.append(summary_line)
            else:
                print("No data in summary table for this image.")
            # Close summary window
            summary_window.close()
        else:
            print("'Summary' window not found for this image.")
        # Close original image
        imp.close()
        # Ensure all images are closed
        IJ.run("Close All")

    # Save combined summary for nuclei
    if nuclei_summaries:
        combined_summary_file = os.path.join(results_folder, f"{os.path.basename(folder['base_folder'])}_Combined_Summary_nuclei.csv")
        with open(combined_summary_file, 'w', encoding='utf-8') as summary_file:
            # Write headers
            summary_file.write(nuclei_summary_headers + '\n')
            # Write data
            for line in nuclei_summaries:
                summary_file.write(line + '\n')
        print(f"Combined summary for nuclei saved to '{combined_summary_file}'.")
    else:
        print(f"No nuclei summary data to save in folder '{folder['base_folder']}'.")

    # ----------------------------
    # Section 2: Processing Foci
    # ----------------------------
    print("\n--- Processing Foci Masks ---")

    # Prepare folder for saving combined foci masks inside 'foci_assay'
    foci_in_nuclei_final_folder = os.path.join(foci_assay_folder, f'Foci_in_nuclei_final_{timestamp}')
    Path(foci_in_nuclei_final_folder).mkdir(parents=True, exist_ok=True)
    print(f"Created folder for combined foci masks: '{foci_in_nuclei_final_folder}'")

    for foci_filename in foci_projection_files:
        foci_file_path = os.path.join(foci_masks_folder, foci_filename)

        if not os.path.isfile(foci_file_path):
            print(f"'{foci_file_path}' is not a file. Skipping.")
            continue

        print(f"\nProcessing foci file: {foci_file_path}")

        # Extract common part from foci filename
        match = re.match(r'processed_(.+?)_foci_projection\.tif', foci_filename)
        if match is None:
            print(f"Could not extract common part from filename '{foci_filename}'. Skipping.")
            continue
        common_part = match.group(1)

        # Find corresponding nuclei file
        corresponding_nuclei_filename = f"processed_{common_part}_nuclei_projection_StarDist_processed.tif"
        corresponding_nuclei_file_path = os.path.join(final_nuclei_mask_folder, corresponding_nuclei_filename)

        if not os.path.isfile(corresponding_nuclei_file_path):
            print(f"Corresponding nuclei file '{corresponding_nuclei_filename}' not found. Skipping.")
            continue

        print(f"Found corresponding nuclei file: {corresponding_nuclei_file_path}")

        # Close all open images before processing
        IJ.run("Close All")

        # Open both foci and nuclei images
        imp_foci = IJ.openImage(foci_file_path)
        if imp_foci is None:
            print(f"Failed to open foci image: {foci_file_path}. Skipping.")
            continue
        else:
            print(f"Foci image '{foci_filename}' successfully opened.")

        imp_nuclei = IJ.openImage(corresponding_nuclei_file_path)
        if imp_nuclei is None:
            print(f"Failed to open nuclei image: {corresponding_nuclei_file_path}. Skipping.")
            imp_foci.close()
            continue
        else:
            print(f"Nuclei image '{corresponding_nuclei_filename}' successfully opened.")

        # Perform Image Calculator to add masks
        # Direct reference to the opened images
        imp1 = imp_foci  # Reference to foci image
        imp2 = imp_nuclei  # Reference to nuclei image

        if imp1 is None or imp2 is None:
            print(f"One of the images '{foci_filename}' or '{corresponding_nuclei_filename}' is not open. Skipping.")
            imp_foci.close()
            imp_nuclei.close()
            continue

        # Create a new mask by adding foci and nuclei masks
        ic = ImageCalculator()
        imp3 = ic.run(imp1, imp2, "AND create")
        if imp3 is None:
            print(f"Failed to create combined mask for '{foci_filename}'. Skipping.")
            imp_foci.close()
            imp_nuclei.close()
            continue

        # Show the new mask
        imp3.show()
        print(f"Combined mask created for '{foci_filename}'.")

        # Ensure imp3 is the active image
        active_imp = WindowManager.getCurrentImage()
        if active_imp != imp3:
            print("Failed to set the combined mask as the active image.")
            imp3.close()
            imp_foci.close()
            imp_nuclei.close()
            continue
        else:
            print("Combined mask is the active image.")

        # Prepare filename for the new mask
        result_mask_filename = f"Result of {foci_filename}"
        result_mask_file_path = os.path.join(foci_in_nuclei_final_folder, result_mask_filename)

        # Save the new mask
        IJ.saveAs("Tiff", result_mask_file_path)
        print(f"Combined mask saved to '{result_mask_file_path}'.")

        # Analyze Particles on the new mask
        IJ.run("Analyze Particles...", "size=0-Infinity pixel display summarize")

        # Save analysis results
        if WindowManager.getWindow("Results") is not None:
            foci_analysis_filename = f"{os.path.splitext(foci_filename)[0]}_foci_analysis.csv"
            foci_analysis_file_path = os.path.join(foci_results_folder, foci_analysis_filename)
            IJ.selectWindow("Results")
            IJ.saveAs("Results", foci_analysis_file_path)
            print(f"Foci analysis results saved to '{foci_analysis_file_path}'.")
            IJ.run("Close")  # Close results window
        else:
            print("'Results' window not found during foci analysis.")

        # Get data from summary table for foci analysis
        summary_window = WindowManager.getWindow("Summary")
        if summary_window is not None:
            summary_text_panel = summary_window.getTextPanel()
            summary_text = summary_text_panel.getText()
            # Convert Java string to Python string
            summary_text_py = str(summary_text)
            lines = summary_text_py.strip().split('\n')
            if len(lines) >= 2:
                # First line is header
                # Second line is data
                if not foci_summary_headers:
                    foci_summary_headers = "Filename," + lines[0].replace('\t', ',')
                summary_line = f"{result_mask_filename}," + lines[1].replace('\t', ',')
                foci_summaries.append(summary_line)
            else:
                print("No data in summary table for foci analysis of this image.")
            # Close summary window
            summary_window.close()
        else:
            print("'Summary' window not found during foci analysis.")

        # Close all images
        imp_foci.close()
        imp_nuclei.close()
        imp3.close()

        # Ensure all images are closed
        IJ.run("Close All")

    # Save combined summary for foci
    if foci_summaries:
        combined_foci_summary_file = os.path.join(foci_results_folder, f"{os.path.basename(folder['base_folder'])}_Combined_Summary_foci.csv")
        with open(combined_foci_summary_file, 'w', encoding='utf-8') as summary_file:
            # Write headers
            summary_file.write(foci_summary_headers + '\n')
            # Write data
            for line in foci_summaries:
                summary_file.write(line + '\n')
        print(f"Combined summary for foci saved to '{combined_foci_summary_file}'.")
    else:
        print(f"No foci summary data to save in folder '{folder['base_folder']}'.")


def main_summarize_res(input_json_path: str) -> None:
    folders = validate_path_files(input_json_path, step=4)

    # Ask if processing should start
    start_processing = input("\nDo you want to start processing files? (yes/no): ").strip().lower()
    if start_processing not in ('yes', 'y'):
        raise ValueError("File processing canceled by user.")

    # Part 2: Process images in folders
    print("\nStarting image processing...")
    for path in folders.keys():
        calculate_nuc_foci(folders[path])
    print("\nImage processing completed.")


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('-i',
                        '--input',
                        type=str,
                        help="JSON file with all paths of directories",
                        required=True)

    args = parser.parse_args()
    main_summarize_res(args.input)
