import imagej
import os
from pathlib import Path
from scyjava import jimport
from datetime import datetime

# Initialize ImageJ
print("Initializing ImageJ...")
try:
    ij = imagej.init(r"C:\Users\dolsk\Desktop\Orientation_assay_script\Fiji.app", mode='interactive')
    print("ImageJ successfully initialized.")
except Exception as e:
    print(f"Error initializing ImageJ: {e}")
    exit(1)

# Import Java classes
try:
    IJ = jimport('ij.IJ')
    Prefs = jimport('ij.Prefs')
    WindowManager = jimport('ij.WindowManager')
    print("Java classes successfully imported.")
except Exception as e:
    print(f"Error importing Java classes: {e}")
    exit(1)

# Main process
def process_folders():
    print("\n--- Start of folder processing ---")

    # Step 1: Request path to .txt file containing folder paths
    txt_file_path = input("Enter the full path to the .txt file containing the list of folder paths for processing: ").strip()

    # Check if the file exists
    if not os.path.isfile(txt_file_path):
        print(f"File '{txt_file_path}' does not exist. Please try again.")
        return

    print(f"File found: {txt_file_path}")

    # Read folder paths from file
    try:
        with open(txt_file_path, 'r', encoding='utf-8') as f:
            folder_paths = [line.strip() for line in f if line.strip()]
        print(f"Number of folder paths read: {folder_paths}")
    except Exception as e:
        print(f"Error reading file: {e}")
        return

    # Check if paths are present
    if not folder_paths:
        print("The file does not contain folder paths. Please check the file content.")
        return

    print(f"\nFound {len(folder_paths)} paths in the file.")

    # Request particle size for nuclear analysis
    particle_size_input = input("Enter particle size for 'Analyze Particles...' for nuclear images (default is 2500): ").strip()
    if particle_size_input == '':
        particle_size = 2500
    else:
        try:
            particle_size = float(particle_size_input)
        except ValueError:
            print("Invalid input. Using default particle size of 2500.")
            particle_size = 2500

    # Request threshold value for foci analysis
    foci_threshold_input = input("Enter threshold value for 'Foci' images (default is 150): ").strip()
    if foci_threshold_input == '':
        foci_threshold = 150
    else:
        try:
            foci_threshold = float(foci_threshold_input)
        except ValueError:
            print("Invalid input. Using default threshold value of 150.")
            foci_threshold = 150

    # Check folders and prepare information
    print("\nChecking folders and files...")

    valid_folders = []

    for idx, base_folder in enumerate(folder_paths, start=1):
        print(f"\nChecking base folder {idx}: {base_folder}")
        if not os.path.exists(base_folder):
            print(f"Folder '{base_folder}' does not exist. Skipping this folder.")
            continue

        # Check for 'foci_assay' subfolder
        foci_assay_folder = os.path.join(base_folder, 'foci_assay')
        if not os.path.exists(foci_assay_folder):
            print(f"Subfolder 'foci_assay' not found in folder '{base_folder}'. Skipping this folder.")
            continue

        # Check for 'Foci' subfolder inside 'foci_assay'
        foci_folder = os.path.join(foci_assay_folder, 'Foci')
        if not os.path.exists(foci_folder):
            print(f"Subfolder 'Foci' not found in folder '{foci_assay_folder}'. Skipping this folder.")
            continue

        # Find the latest folder 'Nuclei_StarDist_mask_processed_<timestamp>' inside 'foci_assay'
        processed_folders = []
        for name in os.listdir(foci_assay_folder):
            if name.startswith('Nuclei_StarDist_mask_processed_'):
                try:
                    timestamp_str = name.replace('Nuclei_StarDist_mask_processed_', '')
                    timestamp = datetime.strptime(timestamp_str, '%Y%m%d_%H%M%S')
                    processed_folders.append((timestamp, os.path.join(foci_assay_folder, name)))
                except ValueError:
                    print(f"Invalid folder name format: {name}. Skipping this folder.")
                    continue

        if not processed_folders:
            print(f"No folders found starting with 'Nuclei_StarDist_mask_processed_' in '{foci_assay_folder}'. Skipping this folder.")
            continue

        # Select the latest folder
        latest_processed_folder = max(processed_folders, key=lambda x: x[0])[1]
        print(f"Found latest folder 'Nuclei_StarDist_mask_processed_': {latest_processed_folder}")

        # Check for files in 'Foci'
        foci_files = [f for f in os.listdir(foci_folder) if f.lower().endswith('.tif')]
        if not foci_files:
            print(f"No files with '.tif' extension found in folder 'Foci'. Skipping this folder.")
            continue

        # Check for files in the latest folder 'Nuclei_StarDist_mask_processed_<timestamp>'
        nuclei_files = [f for f in os.listdir(latest_processed_folder) if f.lower().endswith('.tif')]
        if not nuclei_files:
            print(f"No files with '.tif' extension found in folder '{latest_processed_folder}'. Skipping this folder.")
            continue

        # Information about found files
        print(f"\n--- File information in folder '{foci_assay_folder}' ---")
        print(f"Number of files found in 'Foci': {len(foci_files)}. Data types: {set(os.path.splitext(f)[-1] for f in foci_files)}")
        print(f"Number of files found in 'Nuclei_StarDist_mask_processed_': {len(nuclei_files)}. Data types: {set(os.path.splitext(f)[-1] for f in nuclei_files)}")

        # Add folder to the list of valid folders for processing
        valid_folders.append({
            'base_folder': base_folder,
            'foci_assay_folder': foci_assay_folder,
            'foci_folder': foci_folder,
            'foci_files': foci_files,
            'nuclei_folder': latest_processed_folder,
            'nuclei_files': nuclei_files,
            'particle_size': particle_size,
            'foci_threshold': foci_threshold
        })

    # Check if there are valid folders to process
    if not valid_folders:
        print("\nNo folders found with necessary files for processing.")
        return

    print("\nFound 'Foci' and 'Nuclei_StarDist_mask_processed_' folders ready for analysis.")

    # Request to start processing
    start_processing = input("\nStart processing the found folders? (yes/no): ").strip().lower()
    if start_processing not in ('yes', 'y'):
        print("File processing canceled by user.")
        return

    # Process files in valid folders
    for folder_info in valid_folders:
        process_images_in_folder(folder_info)

    print("\n--- All processing tasks completed ---")


def process_images_in_folder(folder_info):
    # Removed 'global ij' declaration as 'ij' is not modified within the function

    foci_folder = folder_info['foci_folder']
    nuclei_folder = folder_info['nuclei_folder']
    foci_files = folder_info['foci_files']
    nuclei_files = folder_info['nuclei_files']
    particle_size = folder_info['particle_size']
    foci_threshold = folder_info['foci_threshold']

    print(f"\n--- Processing images in folder: {folder_info['base_folder']} ---")

    # Create folders for saving results
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    foci_mask_folder = os.path.join(folder_info['foci_assay_folder'], f"Foci_Mask_{timestamp}")
    nuclei_mask_folder = os.path.join(folder_info['foci_assay_folder'], f"Final_Nuclei_Mask_{timestamp}")
    os.makedirs(foci_mask_folder, exist_ok=True)
    os.makedirs(nuclei_mask_folder, exist_ok=True)

    # Process images in 'Foci' folder
    for filename in foci_files:
        file_path = os.path.join(foci_folder, filename)
        print(f"\nProcessing Foci file: {file_path}")
        try:
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
            IJ.run(imp, "Properties...", "channels=1 slices=1 frames=1 pixel_width=0.2071602 pixel_height=0.2071602 voxel_depth=0.5")
            
            # Set threshold for Foci
            IJ.setThreshold(imp, foci_threshold, 255)
            IJ.run(imp, "Convert to Mask", "")
            IJ.run(imp, "Watershed", "")

            # Analyze particles
            IJ.run(imp, "Analyze Particles...", "size=0-Infinity pixel show=Masks")

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
            output_path = os.path.join(foci_mask_folder, f"processed_{filename}")
            IJ.saveAs(imp_mask, "Tiff", output_path)
            print(f"Processed image saved: {output_path}")

            # Close images
            imp.close()
            imp_mask.close()

        except Exception as e:
            print(f"Error processing file '{file_path}': {e}")

    # Process images in the latest 'Nuclei' folder
    for filename in nuclei_files:
        file_path = os.path.join(nuclei_folder, filename)
        print(f"\nProcessing Nuclei file: {file_path}")
        try:
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
            IJ.run(imp, "Properties...", "channels=1 slices=1 frames=1 pixel_width=0.2071602 pixel_height=0.2071602 voxel_depth=0.5")
            
            # Set threshold
            IJ.setThreshold(imp, 1, 255)
            IJ.run(imp, "Convert to Mask", "")
            IJ.run(imp, "Watershed", "")

            # Analyze particles with specified particle size
            IJ.run(imp, "Analyze Particles...", f"size={particle_size}-Infinity pixel show=Masks")

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
            output_path = os.path.join(nuclei_mask_folder, f"processed_{filename}")
            IJ.saveAs(imp_mask, "Tiff", output_path)
            print(f"Processed image saved: {output_path}")

            # Close images
            imp.close()
            imp_mask.close()

        except Exception as e:
            print(f"Error processing file '{file_path}': {e}")


# -------------------- Run the main function --------------------

if __name__ == "__main__":
    try:
        process_folders()
    except Exception as e:
        print(f"Error during program execution: {e}")

    print("\n--- All processing tasks completed ---")
