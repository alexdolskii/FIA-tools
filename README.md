# FIA-tools (Foci Imaging Assay)
This toolkit is optimized for high-throughput, batch processing of large confocal datasets from fibroblast/ECM 3D units, streamlining extraction and quantification of nuclear staining signals.

For a complete guide to script usage, visit protocols.io.

# Aplication
The project was oroginally developed as a part of the research **Pulsed low-dose-rate radiation reduces the tumor-promotion induced by conventional chemoradiation in pancreatic cancer-associated fibroblasts** in the  [Edna (Eti) Cukierman lab](https://www.foxchase.org/edna-cukierman). 

A modular, semi-interactive toolbox for quantifying nuclear foci (e.g., Ki-67, apoptotic markers) and their colocalization in 3D fibroblast/ECM “unit” assays. FIA-tools complements UMA-tools by focusing on per-nucleus foci counts, areas, and overlap metrics while preserving a reproducible, batch-friendly workflow.

Input formats: .nd2 3D stacks and .tif/.tiff 2D images
Core stack: Python + FIJI/ImageJ (headless), StarDist (2D_versatile_fluo)
Scope: multiple folders / multiple images per run via a JSON manifest
Use cases: punctate nuclear foci (Ki-67), pan-nuclear stains, and multi-marker colocalization

Key features
Robust nuclei detection in noisy data with StarDist; optional watershed + particle analysis to split touching nuclei.
Flexible foci calls: user-defined thresholds per marker; supports multiple foci channels and co-localization (pairwise or multi-channel intersections).
Projection-aware preprocessing: MIP for nuclei; StdDev Z-projection for foci (ND2), or direct channel split (2D TIFF).
Reproducible batch runs: standardized resizing (1024×1024), 8-bit conversion, metadata capture, timestamped outputs.
Scalable: parallelized quantification for multi-image datasets.
Transparent: extensive logging, safety prompts before overwriting, and structured output folders.

Workflow (4 scripts)
1) Image Pre-processing & Channel Extraction
Select channels interactively: 1 nuclei channel + 1..N foci channels.
ND2 stacks:
Nuclei → Max-Intensity Z-Projection (XY)
Foci → StdDev Z-Projection (XY) to emphasize puncta
2D TIFF: direct channel split (no Z-projection)
Standardize & organize: resize to 1024×1024, convert to 8-bit, write to foci_assay/ with subfolders per channel.
Record calibration: writes image_metadata.txt (pixel size, units, dimensions).
2) Nuclei Segmentation & Mask Generation
StarDist (2D_versatile_fluo) for nuclei masks; intensity normalization before inference.
Refinement: ImageJ particle analysis + watershed; minimum size filter to drop debris.
Output: timestamped nuclei mask folders; logs of warnings/errors for QA.
3) Foci Detection & Mask Generation
Validate inputs: finds latest nuclei masks; verifies foci channel folders; reads calibration.
Choose which foci set to process (e.g., Foci_1_Channel_1).
Thresholding: apply user-defined intensity threshold → binary foci masks, then watershed to split touching foci.
Output: timestamped Foci_Masks_* folders + detailed logs.
4) Foci Quantification (with optional colocalization)
Label nuclei and compute area metrics (pixels, µm²); save quick-look images with labeled IDs.
Per-nucleus foci stats: count, total foci area (pixels, µm²), and % nucleus area occupied by foci.
Colocalization (optional): build intersection masks across selected foci channels; compute the same metrics for overlapped regions.
Parallelized: speeds up large batches via multi-core processing.
Output: unified CSV (Pandas) merging single-channel and colocalization results; ready for downstream statistics.
Outputs
Masks: nuclei masks; per-channel foci masks; optional intersection masks.
QC images: labeled nuclei overlays for rapid visual validation.
Tables: image-level and consolidated CSVs with per-nucleus metrics; metadata file for calibration.
Logs: detailed processing history and warnings.

Conventions & notes
A single run can cover many folders/conditions defined in one JSON manifest.
Keep fluorescence channel order consistent across images within a run.
Thresholds and minimum object sizes materially affect sensitivity—tune once, then batch.
For new cell types or stain characteristics, consider training a custom StarDist model for best accuracy.

## Installation 
To download and install *git* please visit [Git Download page](https://git-scm.com/downloads).

To download and install *conda* please visit [Miniforge github](https://github.com/conda-forge/miniforge)

To install the package please follow these steps:

```bash
git clone https://github.com/alexdolskii/FIA-tools.git
cd FIA-tools
conda env create -f <linux_|mac_>environment.yaml
conda activate fia-tools
```

## Usage

The main input for all of the programms is `input_paths.json`. Therefore, previously it have to be modified. This file should contain a list of folders with `.nd2` images. The file can contain as many folders as needed.

Further, to implement analysis run all of the programs one after another:

#### 1_select_channels.py

```bash
chmod +x code/1_select_channels.py
# Run command
code/1_select_channels.py -i input_paths.json
```
#### 2_nuclei_mask_generation

```bash
chmod +x code/2_nuclei_mask_generation.py
# Run command
code/2_nuclei_mask_generation.py -i input_paths.json
```

To customize the thresholds for nuclei please use other *particle_size* parameter

```bash
code/2_nuclei_mask_generation.py -i input_paths.json -p 2000
```

#### 3_foci_mask_generation.py

```bash
chmod +x code/3_foci_mask_generation.py
# Run command
code/3_foci_mask_generation.py -i input_paths.json
```

To customize the thresholds for foci please use other *foci_threshold* parameter

```bash
code/3_foci_mask_generation.py  -i input_paths.json -f 100
```

#### 4_foci_quantification.py

```bash
chmod +x code/4_foci_quantification.py
code/4_foci_quantification.py -i input_paths.json
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

- [Aleksandr Dolskii](aleksandr.dolskii@fccc.edu)

- [Ekaterina Shitik](mailto:shitik.ekaterina@gmail.com) 

Enjoy your use 💫
