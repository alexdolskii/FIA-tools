# Info
This project was originally developed for the article "Pulsed low-dose-rate radiation reduces the tumor-promotion induced by conventional chemoradiation in pancreatic cancer-associated fibroblasts" in the Edna (Eti) Cukierman lab (https://www.foxchase.org/edna-cukierman). 
Its primary goal is to quantify and measure the area of double-strand break (DSB) foci in multi-level 3D confocal images of cancer-associated fibroblasts. 
The program can be adapted to analyze any type of foci detected by immunofluorescence (IF) staining, utilizing a nuclei mask for segmentation.


# Features
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
    - wsl --install
3. After the installation, restart your computer.
4. Once WSL is installed, open your WSL terminal and run:
    - sudo apt update
    - sudo apt upgrade -y

### Step 2: Install Miniconda
1. You can find instructions for Lunux or MacOs:
    https://docs.anaconda.com/miniconda/install/#quick-command-line-install


### Step 3: Clone a GitHub Repository on WSL
1. Check if Git is installed:
   - git --version
   - sudo apt update
   - sudo apt install git
2. Choose the Directory for Cloning
   - cd ~
   - cd ..
   - ls
   Or create another folder if preferred:
   - mkdir <folder_name>
   - cd <folder_name>
3. Clone the Repository:
   - git clone https://github.com/alexdolskii/FIA-tools
4. Verify the Result:
   - cd FIA-tools
   - git branch (You will see a list of branches, and an asterisk (`*`), confirming you are on the correct branch)
   - git pull (This will fetch all new changes from the remote repository in the `FIA-tools`)
5. Make the main script executable:
    - chmod +x code/1_select_channels.py
    - chmod +x code/2_analyze_nuclei.py
    - chmod +x code/3_filter_imgs
    - chmod +x code/4_calculate_nuc_foci.py


### Step 4: Create Environment for UMA Tools
1. Create the environment:
    - conda env create -f fia_tools_environment_Linux_MacOs.yml -n fia_tools_environment_Linux_MacOs
    To check conda environments:
    - conda info --envs
    To delete conda envioment:
    - conda remove --name <environment_name> --all

2. Activate the environment:
    - conda activate fia_tools_environment_Linux_MacOs


### Step 5:  Running the UMA Tools Script
1. Before running the program, you need to modify a `input_paths.json` file. This file should contain a list of folders with .nd2 images, and you can include as many folders as needed.

Additionally, before starting the program, make sure you know how many fluorescence channels you have (e.g., DAPI, Cy5) and their order in the file. You can check this by opening the image using the standard method in the GPU application FiJi (https://imagej.net/software/fiji/downloads).


2. Run the main analysis script:
   - python ./code/1_select_channels.py -i input_paths.json
   - python ./code/2_analyze_nuclei.py -i input_paths.json
   - python ./code/3_filter_imgs -i input_paths.json
   - python ./code/4_calculate_nuc_foci.py -i input_paths.json



