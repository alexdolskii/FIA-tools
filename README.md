# FIA-tools (Foci Imaging Assay

FIA-tools is a modular, semi-interactive Python toolkit for processing confocal immunofluorescence images and quantifying nuclear foci. This toolkit is optimized for medium-throughput, batch processing of large confocal images datasets from fibroblast/ECM 3D units, streamlining extraction and quantification of nuclear staining signals.

FIA-tools complements [UMA-tools](https://github.com/alexdolskii/UMA-tools). The project was originally developed as part of the study [`Pulsed low-dose-rate radiation reduces the tumor-promotion induced by conventional chemoradiation in pancreatic cancer-associated fibroblasts`](https://pubmed.ncbi.nlm.nih.gov/41884584/) in the [Edna (Eti) Cukierman laboratory](https://www.foxchase.org/edna-cukierman).

## Protocol versions and repository branches

| Branch | Relationship to the protocol | Documentation to use |
| --- | --- | --- |
| [`main`](https://github.com/alexdolskii/FIA-tools/tree/main) | Implementation associated with the published protocol, version 1 | [Fibroblast/ECM Functional Units: A Medium-Throughput Assay and Digital Analysis Pipeline, version 1](https://www.protocols.io/view/fibroblast-ecm-functional-units-a-medium-throughpu-e6nvwqyz7vmk/v1) and the README in `main` |
| [`tech_dev`](https://github.com/alexdolskii/FIA-tools/tree/tech_dev) | Development version being prepared for an updated protocol | This README, including the changes and development notes below |

This README describes `tech_dev`. The installation and command names below apply to this branch. The revised protocol is in preparation; its version-specific link will be added when it is published.

For work following protocol version 1, use the corresponding `main` implementation. For work using `tech_dev`, record the exact Git commit together with the analysis parameters and environment. A branch name can refer to different commits over time.

For a complete guide to script usage, visit protocols.io.

# Usage
A modular, semi-interactive toolbox for quantifying nuclear foci (e.g., Ki-67, apoptotic markers) and their colocalization in 3D fibroblast/ECM unit assays. FIA-tools complements (UMA-tools)[https://github.com/alexdolskii/UMA-tools] by focusing on per-nucleus foci counts and/or colocolization between multiple foci channels, areas while preserving a reproducible, batch-friendly workflow.

Input is a directory of .nd2 or .tif/.tiff confocal images representing Z-stacks with multiple detection channels (DAPI, fibronectin). To ensure correct channel mapping, keep the same channel order across every image.
Core stack: Python + FIJI/ImageJ (headless), StarDist (2D_versatile_fluo)
Use cases: punctate nuclear foci (Ki-67), pan-nuclear stains, and multi-marker colocalization

## Technical changes relative to `main`

The following changes describe the installation, interface, and development infrastructure in `tech_dev`:

- The software can be installed as a Python package, providing four named terminal commands.
- The analysis scripts are located in `fia-tools/`, with a package entry module in `fia-tools/__init__.py`.
- A shared `environment.yaml` replaces the separate macOS and Linux environment files. The environment is named `fia_tools` and uses Python 3.10.
- `pyproject.toml` defines the package dependencies and terminal commands. The `uv.lock` file from `main` is not included in this branch.
- Log filenames in stages 1-3 use the `.log` extension.
- The repository includes automated tests, GitHub Actions workflow definitions, and example intermediate and final results in `data/`.

| Stage | Script in `main` | Terminal command in `tech_dev` |
| --- | --- | --- |
| 1. Select and prepare channels | `code/1_select_channels.py` | `select_channels` |
| 2. Generate nuclei masks | `code/2_nuclei_mask_generation.py` | `generate_nuclei_mask` |
| 3. Generate foci masks | `code/3_foci_mask_generation.py` | `generate_foci_mask` |
| 4. Quantify foci | `code/4_foci_quantification.py` | `quantify_foci` |

Additional changes accompanying the revised protocol will be documented here as they are implemented.

## Key features
Robust nuclei detection in noisy data with StarDist; watershed + particle analysis to split touching nuclei.
Flexible foci calls: user-defined thresholds per marker; supports multiple foci channels and co-localization (pairwise or multi-channel intersections).
Projection-aware preprocessing: Max Intensity Z-prrojection for nuclei; StdDev Z-projection for foci (ND2).
Transparent: extensive logging, safety prompts before overwriting, and structured output folders.

## Workflow (4 scripts)
**1) Image Pre-processing & Channel Extraction**
Interactively select channels (1 nuclei + 1..N foci). For .nd2 .tiff stacks, create Max Intensity Z-projections for nuclei and StdDev Z-projections for foci (XY). Standardize all images (resize to 1024×1024, convert to 8-bit), save into foci_assay/ with per-channel subfolders, and record calibration in image_metadata.txt (pixel size, units, dimensions).

**2) Nuclei Segmentation & Mask Generation**
Perform nuclei segmentation with StarDist (2D_versatile_fluo) after intensity normalization, then refine masks via ImageJ particle analysis and watershed to split touching objects, applying a minimum-size filter to remove debris; results are saved to timestamped nuclei-mask folders with detailed warning/error logs for QA.

**3) Foci Detection & Mask Generation**
Validate inputs by locating the latest nuclei masks, verifying foci-channel folders, and reading calibration; select the foci set to process (e.g., Foci_1_Channel_1); apply a user-defined intensity threshold to create binary foci masks, then use watershed to split touching foci; save results to timestamped Foci_Masks_* directories with detailed logs for QA.

**4) Foci Quantification (with optional colocalization)**
Label nuclei and compute area metrics (pixels, µm²), saving quick-look images with labeled IDs; for each nucleus, quantify foci count, total foci area (pixels, µm²), and % area occupied by foci, with optional colocalization via intersection masks across selected foci channels (same metrics on overlaps). Results are consolidated into a unified CSV combining single-channel and colocalization readouts for downstream stats; outputs also include nuclei masks, per-channel foci masks, optional intersection masks, QC overlays, image-level and study-level tables with per-nucleus metrics, a calibration metadata file, and detailed logs.

Conventions & notes
A single run can cover many folders/conditions defined in one JSON manifest.
Keep fluorescence channel order consistent across images within a run.
Thresholds and minimum object sizes materially affect sensitivity—tune once, then batch.
For new cell types or stain characteristics, consider training a custom StarDist model for best accuracy.

## Installation

Install [Git](https://git-scm.com/downloads) and a Conda distribution such as [Miniforge](https://github.com/conda-forge/miniforge). Use a terminal with Conda available.

The environment specifies Python 3.10 and OpenJDK 11. Its remaining dependencies are defined in [`environment.yaml`](https://github.com/alexdolskii/FIA-tools/blob/tech_dev/environment.yaml) and [`pyproject.toml`](https://github.com/alexdolskii/FIA-tools/blob/tech_dev/pyproject.toml). macOS and Linux environment builds are configured in GitHub Actions; this configuration alone does not establish successful validation on every system. Native Windows installation has not been validated for this README.

### 1. Download the correct branch

Create a separate checkout for the development version:

```bash
git clone --branch tech_dev --single-branch https://github.com/alexdolskii/FIA-tools.git FIA-tools-tech_dev
cd FIA-tools-tech_dev
```

The explicit branch selection is required because the repository's default branch is `main`.

### 2. Create the environment and install the package

If an environment named `fia_tools` does not already exist:

```bash
conda env create -f environment.yaml
conda activate fia_tools
python -m pip install .
```

If that name is already used for a previous installation, create a separate environment instead:

```bash
conda env create -n fia_tools_tech_dev -f environment.yaml
conda activate fia_tools_tech_dev
python -m pip install .
```

Run `python -m pip install .` from the repository root. It installs the package into the active Python environment and creates the terminal commands. Package installation may resolve or update dependencies to satisfy `pyproject.toml`.

### 3. Check command availability

```bash
select_channels --help
generate_nuclei_mask --help
generate_foci_mask --help
quantify_foci --help
```

These commands check that the entry points are available. Image processing requires the appropriate input data and dependencies. ImageJ initialization and the first StarDist model load may require internet access for downloads.

In each new terminal session, activate the environment used for installation. You do not need to reinstall the package for each analysis. After updating the source checkout, reinstall the package to use those source changes.

## Prepare the input manifest

Create or edit `input_paths.json`. It must contain the key `paths_to_files` and a list of existing folders containing source images:

```json
{
  "paths_to_files": [
    "/Volumes/ExampleDrive/Experiment_01/Condition_A",
    "/Volumes/ExampleDrive/Experiment_01/Condition_B"
  ]
}
```

Replace the example paths with your own. Despite the key name, each entry refers to a folder. Include only folders that should be processed in this batch. JSON does not support comments or trailing commas.

- Supported source formats are `.nd2`, `.tif`, and `.tiff`.
- Stage 1 distinguishes ND2 stacks, multichannel TIFF stacks, and already projected 2D multichannel TIFF images.
- Keep the channel order consistent across the images included in one batch.
- Channel numbers start at **1**. Identify the nuclei channel and each foci marker channel before starting.
- Use absolute paths for input folders. When running under WSL, use paths visible inside WSL, such as `/mnt/c/...`.
- Pass the same manifest to each stage. Run commands from the repository root when using the relative filename `input_paths.json`, or provide its absolute path in quotes.

## Run the analysis

Run the four stages in order and wait for each stage to finish before starting the next. Several stages ask questions in the terminal. Review their output and log files before continuing.

### Stage 1. Select and prepare image channels

```bash
select_channels -i input_paths.json
```

The command asks you to confirm processing, select the input image type, choose the nuclei channel, and specify the number and indices of the foci channels. If `foci_assay` already exists, it also asks whether to overwrite existing results in that location.

For stacks, the workflow prepares maximum-intensity projections for nuclei and standard-deviation projections for foci. Prepared channels are resized to 1024 x 1024 pixels and converted to 8-bit images. They are saved under `foci_assay/Nuclei/` and `foci_assay/Foci/`; source metadata is written to `foci_assay/image_metadata.txt`.

### Stage 2. Generate nuclei masks

Run with the default minimum nucleus area:

```bash
generate_nuclei_mask -i input_paths.json
```

This stage performs StarDist segmentation and subsequent mask processing. The default minimum nucleus area is **2500 pixels** in the processed image. Use `-p` or `--particle_size` to specify a different minimum area.

For example, the following command uses 2000 pixels:

```bash
generate_nuclei_mask -i input_paths.json -p 2000
```

The example value is an optional setting; it is not the default. The `-p` option controls the area filter and does not set the StarDist probability threshold.

### Stage 3. Generate foci masks

Run with the default intensity threshold:

```bash
generate_foci_mask -i input_paths.json
```

The command lists available foci-channel folders, asks you to select one, and requests confirmation. One invocation processes that selected channel folder across the input folders. **Repeat this stage for every foci channel needed for the final analysis.**

The default lower intensity threshold is **150** on the processed 8-bit image. Use `-f` or `--foci_threshold` to specify another threshold. For example:

```bash
generate_foci_mask -i input_paths.json -f 100
```

Here, 100 is an example threshold. Choose the threshold for the selected marker and record the value used. It is an intensity threshold, not a minimum foci area.

### Stage 4. Quantify foci and optional channel overlap

The package provides the following entry point:

```bash
quantify_foci -i input_paths.json
```

It asks whether to start processing and whether to perform colocalization analysis. Generate masks for all required channels before starting this stage. Colocalization is selected interactively; the current command does not expose a separate colocalization flag.

```bash
python fia-tools/4_foci_quantification.py -i input_paths.json
```

### Parameter reference

| Command | Option | Meaning | Default |
| --- | --- | --- | --- |
| All four commands | `-i`, `--input` | Path to the input JSON manifest | Required |
| `generate_nuclei_mask` | `-p`, `--particle_size` | Minimum nucleus area in processed-image pixels | `2500` |
| `generate_foci_mask` | `-f`, `--foci_threshold` | Lower foci intensity threshold on processed 8-bit images | `150` |
| `quantify_foci` | `-j`, `--jobs` | Number of worker processes | `4` |
| All four commands | `-h`, `--help` | Show command-line options | Not applicable |

The defaults document the implementation. Parameter selection should follow the experiment and the applicable protocol.

## Results and logs

Each input folder has its own analysis outputs. The following paths are relative to that input folder; `<timestamp>` represents the date and time generated by a stage.

| Location | Contents |
| --- | --- |
| `foci_assay/Nuclei/` | Prepared nuclei-channel images |
| `foci_assay/Foci/Foci_<index>_Channel_<channel>/` | Prepared images for each foci channel |
| `foci_assay/image_metadata.txt` | Source-image dimensions and calibration metadata |
| `foci_assay/Nuclei_StarDist_mask_processed_<timestamp>/` | Initial nuclei masks |
| `foci_assay/Final_Nuclei_Mask_<timestamp>/` | Processed nuclei masks |
| `foci_assay/Foci_Masks/Foci_<index>_Channel_<channel>_<timestamp>/` | Processed masks for a selected foci channel |
| `foci_analysis/Results_<timestamp>/` | Final table, numbered nuclei images, and optional intersection masks |

The final table is `all_results_with_coloc_universal.csv`. Its filename is also used when colocalization is disabled. It contains per-nucleus rows across the processed images in one input folder, with channel-specific measurements. Numbered nuclei images are saved as PNG files; intersection masks are saved as TIFF files when requested. Separate study-wide summary tables are not automatically generated by the current final stage.

Stage-specific logs include `1_log.log`, `2_val_log.log`, `2_log.log`, `nuclei_log.log`, `3_val_log.log`, `foci_log.log`, and `4_log.log`. Validation logs are written in the input folder; processing logs are saved with their corresponding stage outputs. Logs record warnings and errors, and the final stage also logs progress.

Nuclei-mask, foci-mask, and final-analysis directories include timestamps. The prepared-channel folders and metadata file use fixed paths, and some log files are overwritten on rerun. Later stages select the latest matching mask folders. Preserve the outputs needed for a previous analysis before repeating preparation or switching between analyses in the same input folder.


# Dependencies and Tools Used

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

# Contributors

- [Aleksandr Dolskii](aleksandr.dolskii@fccc.edu)

- [Ekaterina Shitik](mailto:shitik.ekaterina@gmail.com) 

Enjoy your use 💫
