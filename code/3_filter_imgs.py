#!/usr/bin/env python3
import argparse
import os
from datetime import datetime

import imagej
from scyjava import jimport
from validate_folders import validate_path_files


def filter_in_folder(folder: dict,
                     particle_size: int,
                     foci_threshold: int) -> None:
    """
    The function to filter the results of machine
    learning processing of images.
    Args:
        folder: the path to the folder that contain output of
        main_analyze_nuclei function
        particle_size: the threshold for size of nuclei
        foci_threshold: threshold value for foci analysis
    Returns:
        Two directories: 'Final_Nuclei_Mask' and 'Foci_Mask'

    """
    # Initialize ImageJ
    print("Initializing ImageJ...")

    ij = imagej.init('sc.fiji:fiji', mode='headless')
    print("ImageJ successfully initialized.")

    # Import Java classes
    IJ = jimport('ij.IJ')
    WindowManager = jimport('ij.WindowManager')
    print("Java classes successfully imported.")

    foci_folder = folder['foci_folder']
    nuclei_folder = folder['nuclei_folder']
    foci_files = folder['foci_files']
    nuclei_files = folder['nuclei_files']

    # Create folders for saving results
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    foci_mask_folder = os.path.join(folder['foci_assay_folder'],
                                    f"Foci_Mask_{timestamp}")
    nuclei_mask_folder = os.path.join(folder['foci_assay_folder'],
                                      f"Final_Nuclei_Mask_{timestamp}")
    os.makedirs(foci_mask_folder, exist_ok=True)
    os.makedirs(nuclei_mask_folder, exist_ok=True)

    # Process images in 'Foci' folder
    for filename in foci_files:
        file_path = os.path.join(foci_folder, filename)
        print(f"\nProcessing Foci file: {file_path}")
        # Close all images before starting processing
        IJ.run("Close All")

        # Open image
        imp = IJ.openImage(file_path)
        if imp is None:
            print(f"Failed to open image: {file_path}")
            continue

        # Convert image to 8-bit
        IJ.run(imp, "8-bit", "")
        # Set calibration
        calibration = imp.getCalibration()
        calibration.setXUnit("micron")
        calibration.setYUnit("micron")
        calibration.setZUnit("micron")
        IJ.run(imp, "Properties...", "channels=1 "
                                     "slices=1 frames=1 "
                                     "pixel_width=0.2071602 "
                                     "pixel_height=0.2071602 "
                                     "voxel_depth=0.5")
        # Set threshold for Foci
        IJ.setThreshold(imp, foci_threshold, 255)
        IJ.run(imp, "Convert to Mask", "")
        IJ.run(imp, "Watershed", "")

        # Analyze particles
        IJ.run(imp, "Analyze Particles...",
               "size=0-Infinity pixel show=Masks")

        # Get processed image
        mask_title = 'Mask of ' + filename
        imp_mask = WindowManager.getImage(mask_title)
        if imp_mask is None:
            # If title differs, try to find the last opened image
            imp_mask = WindowManager.getCurrentImage()
            if imp_mask is None:
                print(f"Failed to get mask for image: {file_path}")
                continue

        # Save processed image
        output_path = os.path.join(foci_mask_folder,
                                   f"processed_{filename}")
        IJ.saveAs(imp_mask, "Tiff", output_path)
        print(f"Processed image saved: {output_path}")

        # Close images
        imp.close()
        imp_mask.close()

    # Process images in the latest 'Nuclei' folder
    for filename in nuclei_files:
        file_path = os.path.join(nuclei_folder, filename)
        print(f"\nProcessing Nuclei file: {file_path}")
        # Close all images before starting processing
        IJ.run("Close All")

        # Open image
        imp = IJ.openImage(file_path)
        if imp is None:
            print(f"Failed to open image: {file_path}")
            continue

        # Convert image to 8-bit
        IJ.run(imp, "8-bit", "")
        # Set calibration
        calibration = imp.getCalibration()
        calibration.setXUnit("micron")
        calibration.setYUnit("micron")
        calibration.setZUnit("micron")
        IJ.run(imp, "Properties...", "channels=1 "
                                     "slices=1 "
                                     "frames=1 "
                                     "pixel_width=0.2071602 "
                                     "pixel_height=0.2071602 "
                                     "voxel_depth=0.5")
        # Set threshold
        IJ.setThreshold(imp, 1, 255)
        IJ.run(imp, "Convert to Mask", "")
        IJ.run(imp, "Watershed", "")

        # Analyze particles with specified particle size
        IJ.run(imp, "Analyze Particles...",
               f"size={particle_size}-Infinity pixel show=Masks")

        # Get processed image
        mask_title = 'Mask of ' + filename
        imp_mask = WindowManager.getImage(mask_title)
        if imp_mask is None:
            # If title differs, try to find the last opened image
            imp_mask = WindowManager.getCurrentImage()
            if imp_mask is None:
                print(f"Failed to get mask for image: {file_path}")
                continue

        # Save processed image
        output_path = os.path.join(nuclei_mask_folder,
                                   f"processed_{filename}")
        IJ.saveAs(imp_mask, "Tiff", output_path)
        print(f"Processed image saved: {output_path}")

        # Close images
        imp.close()
        imp_mask.close()


def main_filter_imgs(input_json_path: str,
                     particle_size: int,
                     foci_threshold: int) -> None:
    """
    The function to validate the output of the results
    from main_analyze_nuclei from 2_analyze_nuclei.py
    and filter the results of machine
    learning processing of images.
    Please use this function next to main_analyze_nuclei from
    2_analyze_nuclei.py

    Args:
        input_json_path: input json file with all paths
        to processed files
        particle_size: the threshold for size of nuclei
        foci_threshold: threshold value for foci analysis

    Returns:
        Two directories: 'Final_Nuclei_Mask' and 'Foci_Mask'
    """
    if not isinstance(particle_size, int):
        raise ValueError('Particle size must be integer!')

    if not isinstance(foci_threshold, int):
        raise ValueError('Foci threshold must be integer!')

    folders = validate_path_files(input_json_path, step=3)

    # Request to start processing
    start_processing = input("\nStart processing the "
                             "found folders? (yes/no): ").strip().lower()
    if start_processing not in ('yes', 'y'):
        raise ValueError("File processing canceled by user.")

    # Process files in valid folders
    for path in folders.keys():
        filter_in_folder(folders[path], particle_size, foci_threshold)

    print("\n--- All processing tasks completed ---")


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('-i',
                        '--input',
                        type=str,
                        help="JSON file with all paths of directories",
                        required=True)
    parser.add_argument('-p',
                        '--particle_size',
                        type=int,
                        help="The threshold for size of nuclei. "
                             "Default is 2500",
                        default=2500)
    parser.add_argument('-f',
                        '--foci_threshold',
                        type=int,
                        help="Threshold value for foci analysis. "
                             "Default is 150",
                        default=150)
    args = parser.parse_args()
    main_filter_imgs(args.input, args.particle_size, args.foci_threshold)
