# Info
This project was originally developed for the article "Pulsed low-dose-rate radiation reduces the tumor-promotion induced by conventional chemoradiation in pancreatic cancer-associated fibroblasts" in the  [Edna (Eti) Cukierman lab](https://www.foxchase.org/edna-cukierman). 
Its primary goal is to quantify and measure the area of double-strand break (DSB) foci in multi-level 3D confocal images of cancer-associated fibroblasts. 
The program can be adapted to analyze any type of foci/specks detected by immunofluorescence (IF) staining, utilizing a nuclei mask for segmentation.


## Features
A key feature of this program is its ability to processed a large number of images efficiently, combined with the use of an AI-based nuclei mask. 
This AI-driven approach significantly improves the accuracy of nuclear segmentation, effectively reducing issues caused by background noise—a common challenge in IF image analysis. 
As a result, the tool enhances the precision and reliability of DSB foci quantification in complex biological imaging datasets.


## Authors
- [Aleksandr Dolskii](https://github.com/alexdolskii)

- [Ekaterina Shitik](https://github.com/EkaterinShitik)


## Dependencies and Tools Used
This program utilizes the following tools:

1. **Fiji** 
    In this project, Fiji was used for **preprocessing image stacks**, including tasks such as contrast enhancement, filtering and particle analysis.

    [Fiji](https://fiji.sc/) is an open-source distribution of ImageJ with a focus on image analysis. 
    
    - Repository: [Fiji](https://github.com/fiji/fiji)  
    - License: [GPL License](https://imagej.net/licensing/)

2. **StarDist**
    In this project, the standard StarDist model was employed to generate high-quality nuclei masks from image data, significantly improving segmentation accuracy and reducing background noise issues commonly encountered in immunofluorescence (IF) image analysis.
    
    [StarDist](https://stardist.net/)

    - Repository: [StarDist](https://github.com/stardist/stardist)  
    - License: [BSD 3-Clause License](https://github.com/stardist/stardist/blob/main/LICENSE.txt)


# Detailed instructions for the first launch of FIA-tools
The program can be run on Linux or macOS machines. If you are using Windows, you will need to install WSL (Windows Subsystem for Linux).
For the code to function properly, specific package versions are required. Therefore, it is essential to work within an environment provided. 
As an example, commands for working with Miniconda are provided, but you are free to use any environment management tool that you find convenient. 
For macOS users, skip the WSL installation and work directly in the terminal.

### Step 1 (Skip for MacOs): Install WSL (Windows Subsystem for Linux)

1. Open Powerell with administrator privileges:
    Press the Windows key, type "Powerell," right-click on it, and select "Run as administrator".
2. Run the following command to install WSL:
    - `wsl --install`
3. After the installation, restart your computer.
4. Once WSL is installed, open your WSL terminal and run:
    - `sudo apt update`
    - `sudo apt upgrade -y`

### Step 2: Install Miniconda
1. You can find instructions for Lunux or MacOs [here](https://docs.anaconda.com/miniconda/install/#quick-command-line-install):


### Step 3: Clone a GitHub Repository on WSL
1. Check if Git is installed:
   - `git --version`
   - `sudo apt update`
   - `sudo apt install git`
2. Choose the Directory for Cloning
   - `cd ~`
   - `cd ..`
   - `ls`
   Or create another folder if preferred:
   - `mkdir <folder_name>`
   - `cd <folder_name>`
3. Clone the Repository:
   - `git clone https://github.com/alexdolskii/FIA-tools`
4. Verify the Result:
   - `cd FIA-tools`
   - `git branch` (You will see a list of branches, and an asterisk (`*`), confirming you are on the correct branch)
   - `git pull` (This will fetch all new changes from the remote repository in the FIA-tools)
5. Make the main script executable:
    - `chmod +x code/1_select_channels.py`
    - `chmod +x code/2_analyze_nuclei.py`
    - `chmod +x code/3_filter_imgs`
    - `chmod +x code/4_calculate_nuc_foci.py`



### Step 4: Create Environment for UMA Tools
1. Create the environment:
    - `conda env create -f fia_tools_environment_Linux_MacOs.yml -n fia_tools_environment_Linux_MacOs`
To check conda environments:
    - `conda info --envs`
To delete conda envioment:
    - `conda remove --name <environment_name> --all`

2. Activate the environment:
    - `conda activate fia_tools_environment_Linux_MacOs`


### Step 5:  Running the UMA Tools Script
1. Before running the program, you need to modify a `input_paths.json` file. This file should contain a list of folders with .nd2 images, and you can include as many folders as needed.

Additionally, before starting the program, make sure you know how many fluorescence channels you have (e.g., DAPI, Cy5) and their order in the file. You can check this by opening the image using the standard method in the GPU application FiJi (https://imagej.net/software/fiji/downloads).


2. Run the main analysis script:
    - 'python ./code/1_select_channels.py -i input_paths.json'
    - 'python ./code/2_analyze_nuclei.py -i input_paths.json'
    - 'python ./code/3_filter_imgs -i input_paths.json'
    - 'python ./code/4_calculate_nuc_foci.py -i input_paths.json'


# FIA-tool workflow description
## 1_select_channels.py
This script processes confocal image stacks (.nd2 files with Z-ctacks) to analyze nuclei and foci signal chanells. It is specifically designed for immunofluorescence (IF) experiments, allowing users to extract and project specific image channels from raw .nd2 files. The results are saved as processed images in newly created directories. The script leverages ImageJ with Bio-Formats for image manipulation and Fiji plugins for Z-projection and resizing.
### How It Works
1. Input: The user provides a JSON file containing paths to directories with .nd2 image stacks.
2. Channel Selection: The script prompts the user to specify channel numbers for nuclei and foci staining.
3. Image Processing:
    - Extracts specified channels for nuclei and foci.
    - Performs [Z-projection](https://imagej.net/imaging/z-functions):
        - Maximum Intensity (Max) for nuclei channel:
        - Standard Deviation (SD) for foci channel.
    - Resizes images to standard dimensions (X:1024; Y:1024) and converts them to 8-bit grayscale.
4. Output: Processes results are saved in a folder named foci_assay created within each input directory, organized into subfolders Nuclei and Foci.

## 2_analyse_nuclei.py
This script processes nuclei images using a machine learning-based segmentation approach powered by the pre-trained [StarDist](https://pypi.org/project/stardist/0.3.4/) model. It is specifically designed for high-precision segmentation of nuclei in 2D fluorescent microscopy images. The output is a new set of processed masks that highlight detected nuclei, saved in well-organized directories for downstream analysis.
### How It Works
1. Input: The script takes a JSON file listing directories containing .tif images of nuclei.
    - Reads .tif images from specified directories.
    - Skips files that are not 8-bit grayscale.
2. Normalization:
    - Applies contrast/intensity normalization for futher analysis
3. Segmentation:
    The StarDist model predicts labeled instances of nuclei, with adjustable thresholds:
    - nms_thresh=0.9: Controls how close detected objects can be before merging.
    - prob_thresh=0.7: Minimum confidence required for nuclei detection.
3. Output: Processed masks are saved as new .tif files in a folder named Nuclei_StarDist_mask_processed_<timestamp> created for each input directory with the naming convention *_StarDist_processed.

## 3_filter_imgs.py
This script is designed to create binary masks for nuclei and foci in microscopy images, enabling the downstream analysis of the size and area of foci within the nuclei region. It works as a follow-up to the previous step, where the StarDist standard model was used to predict nuclei for a wide range of object sizes.
After generating the binary masks, this script provides functionality to optimize the size threshold for nuclei objects, ensuring greater accuracy in subsequent analyses. Additionally, it converts images to binary format (black and white masks) to facilitate clear object segmentation and measurement.
### How It Works
1. Input: Takes directories containing the results of the previous step (nuclei segmentation from StarDist).
Requires user-specified thresholds for nuclei object size and foci intensity.
2. Processing:
    - Nuclei Masks:
        - Applies size-based thresholds to filter out unwanted nuclei objects.
        - Converts filtered nuclei masks into binary format.
    - Foci Masks:
        - Applies intensity thresholds to identify foci regions.
        - Additional watershed segmentation to separate overlapping objects.
        - Converts foci masks into binary format.
        - Associates foci with nuclei regions for future spatial analysis.
3. Output: Saves processed masks in separate directories for nuclei (Final_Nuclei_Mask) and foci (Foci_Mask), with results timestamped for traceability.

## 4_calculate_nuc_foci.py
This script represents the final step in a microscopy image analysis pipeline, where it performs detailed quantification of nuclei and foci, computing summaries for the size, count, and total area of detected objects. It uses binary masks from the previous steps and combines foci and nuclei masks to analyze the spatial distribution of foci within the nuclei region. Results are saved in detailed summaries for both individual objects (single nuclei/foci) and aggregated data per pucture (summary about objects per folder).
### How It Works
1. Input: Requires a JSON file listing paths to directories containing:
    - Binary nuclei masks (Final_Nuclei_Mask).
    - Binary foci masks (Foci_Mask).
    - Masks generated from previous pipeline steps are analyzed together.
2. Processing:
    - Nuclei Analysis:
        - Measures object count, total area, and average size for detected nuclei.
        - Outputs a summary file for each image and a combined summary for all images.
    - Foci Analysis:
        - Combines foci and nuclei masks using a logical AND operation to isolate foci within nuclei.
        - Analyzes the size and spatial distribution of foci within nuclei regions.
        - Outputs detailed and aggregated summaries.
3. Output: 
    - Creates new folders for:
Combined Masks with pictures  (Foci_in_nuclei_final_<timestamp>).
    - Creates new folder foci_analysis with:
        - Nuclei Analysis folder (Nuclei_count_results_<timestamp>).
        - Foci Analysis folder (Foci_count_results_<timestamp>).
Both folders contain <folder_name>_Combined_Summary.csv and processed_<image_name>.csv files.
    - The first file type: Each row contains information about the total number of objects (nuclei or foci) detected in the image and the total area occupied by these objects.
    - The second file type: A separate file is generated for each image, where each row provides information about a single object, including its area (Area). 
