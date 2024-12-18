# FIA-tools

**FIA-tools** quantifies and measures the area of double-strand break (DSB) foci in multi-level 3D confocal images of cancer-associated fibroblasts. It used [ImageJ](https://github.com/imagej) to process images and count elements and pretrained models from [StarDist](https://github.com/stardist/stardist) to recognize cell nuclei.
The program can be adapted to analyze any foci/specks detected by immunofluorescence (IF) staining, utilizing a nuclei mask for segmentation.

The project is developed as a part of the research **Pulsed low-dose-rate radiation reduces the tumor-promotion induced by conventional chemoradiation in pancreatic cancer-associated fibroblasts** in the  [Edna (Eti) Cukierman lab](https://www.foxchase.org/edna-cukierman). 

## Installation 
To download and install *git* please visit [Git Download page](https://git-scm.com/downloads).

To download and install *conda* please visit [Miniforge github](https://github.com/conda-forge/miniforge)

To install the package please follow these steps:

```bash
git clone https://github.com/alexdolskii/FIA-tools.git
cd FIA-tools
conda env create -f environment.yaml
conda activate fia-tools
```

## Usage

The main input for all of the programms is `input_paths.json`. Therefore, previously it have to be modified. This file should contain a list of folders with `.nd2` images. The file can contain as many folders as needed.

Further, to implement analysis run all of the programs one after another:

#### 1_select_channels.py
Make the script executable
```bash
chmod +x code/1_select_channels.py
```
Run command
```bash
code/1_select_channels.py -i input_paths.json
```
#### 2_analyse_nuclei.py

Make the script executable
```bash
chmod +x code/2_analyse_nuclei.py
```

Run command
```bash
code/2_analyse_nuclei.py -i input_paths.json
```

#### 3_filter_imgs.py

Make the script executable

```bash
chmod +x code/3_filter_imgs.py
```

Run command

```bash
code/3_filter_imgs.py -i input_paths.json
```

To customize the thresholds for nuclei and foci please use other arguments

```bash
code/3_filter_imgs.py -i input_paths.json -p 2000 -f 100
```

#### 4_calculate_nuc_foci.py
Make the script executable

```bash
chmod +x code/4_calculate_nuc_foci.py
```
Run command
```bash
code/4_calculate_nuc_foci.py  -i input_paths.json
```

To delve into more details of program usage please visit [FIA_tools](FIA_tools.ipynb) notebook 

## References

This program utilizes the following tools:

1. **Fiji** 
    This project used Fiji for preprocessing into preprocess image stacks as contrast enhancement, filtering, and particle analysis.

    [Fiji](https://fiji.sc/) is an open-source distribution of ImageJ focusing on image analysis. 
    
    - Repository: [Fiji](https://github.com/fiji/fiji)  
    - License: [GPL License](https://imagej.net/licensing/)

2. **StarDist**
    In this project, the standard StarDist model was employed to generate high-quality nuclei masks from image data, significantly improving segmentation accuracy and reducing background noise issues commonly encountered in immunofluorescence (IF) image analysis.
    
    [StarDist](https://stardist.net/)

    - Repository: [StarDist](https://github.com/stardist/stardist)  
    - License: [BSD 3-Clause License](https://github.com/stardist/stardist/blob/main/LICENSE.txt)

## Contributors

- Aleksandr Dolskii

- [Ekaterina Shitik](mailto:shitik.ekaterina@gmail.com) 

Enjoy your use 💫
