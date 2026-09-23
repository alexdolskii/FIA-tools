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

- The software can be installed as a Python package, providing five named terminal commands, including an optional nuclear-intensity workflow.
- The analysis scripts are located in `fia-tools/`, with a package entry module in `fia-tools/__init__.py`.
- A shared `environment.yaml` replaces the separate macOS and Linux environment files. The environment is named `fia_tools` and uses Python 3.10.
- `pyproject.toml` defines the package dependencies and terminal commands. The `uv.lock` file from `main` is not included in this branch.
- Log filenames in stages 1-3 use the `.log` extension.
- Stage 2 can reuse validated StarDist masks when changing the minimum nucleus area and exports pixel-based nuclear morphology for each final-mask run.
- Native image width and height are preserved throughout processing and numbered QC exports; the former 1024 x 1024 resizing in stage 1 has been removed.
- `quantify_nuclear_intensity` measures original marker-channel values in existing final nucleus IDs, with explicit experiment/run selection and separate results for each mask run.
- The repository includes automated tests, GitHub Actions workflow definitions, and example intermediate and final results in `data/`.

| Stage | Script in `main` | Terminal command in `tech_dev` |
| --- | --- | --- |
| 1. Select and prepare channels | `code/1_select_channels.py` | `select_channels` |
| 2. Generate nuclei masks | `code/2_nuclei_mask_generation.py` | `generate_nuclei_mask` |
| 3. Generate foci masks | `code/3_foci_mask_generation.py` | `generate_foci_mask` |
| 4. Quantify foci | `code/4_foci_quantification.py` | `quantify_foci` |
| Optional nuclear intensity after stage 2 | Not available | `quantify_nuclear_intensity` |

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
quantify_nuclear_intensity --help
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

For foci analysis, run the four stages in order and wait for each stage to finish before starting the next. For per-nucleus marker intensity (for example, phospho-Smad2), run stages 1 and 2, then the optional nuclear-intensity command described below; foci masks are not required. Several stages ask questions in the terminal. Review their output and log files before continuing.

### Stage 1. Select and prepare image channels

```bash
select_channels -i input_paths.json
```

The command asks you to confirm processing, select the input image type, choose the nuclei channel, and specify the number and indices of the foci channels. If `foci_assay` already exists, it also asks whether to overwrite existing results in that location.

For stacks, the workflow prepares maximum-intensity projections for nuclei and standard-deviation projections for foci. Prepared channels retain the original width and height and are converted to 8-bit images for segmentation. No channel, mask or numbered QC image is resized. Z projection collapses only the Z dimension; the XY pixel grid is unchanged. They are saved under `foci_assay/Nuclei/` and `foci_assay/Foci/`; source metadata is written to `foci_assay/image_metadata.txt`.

### Stage 2. Generate nuclei masks

Run with the default minimum nucleus area:

```bash
generate_nuclei_mask -i input_paths.json
```

This stage performs StarDist segmentation and subsequent mask processing. The default minimum nucleus area is **2500 pixels²** in the processed image. Use `-p` or `--particle_size` to specify a different minimum area.

For example, the following command uses 2000 pixels²:

```bash
generate_nuclei_mask -i input_paths.json -p 2000
```

The example value is an optional setting; it is not the default. The `-p` option controls the area filter and does not set the StarDist probability threshold.

#### Repeat the area filter using existing StarDist masks

To try another minimum area, run the same command with a new `-p` value. For each input folder, the program searches for `foci_assay/Nuclei_StarDist_mask_processed_<timestamp>/` and checks mask filenames, completeness, TIFF readability, dimensions and data types. Hidden files and folders, including macOS `._*` entries, are excluded.

If reusable results exist, the program lists their paths and mask counts, newest first, and offers these choices:

- Enter a folder number, or press Enter to reuse the newest valid result.
- Enter `n` to run StarDist again for this input folder.
- Enter `a` to reuse the newest valid result for this and the remaining input folders. Inputs without reusable results still require a new StarDist run.
- Enter `q` to cancel before starting new segmentation.

New StarDist runs include `stardist_run.json`, which records the settings, source and mask SHA-256 checksums, and completion status. Incomplete runs, changed files and mismatched settings are not offered for reuse. Older folders without this metadata can be reused after structural checks and explicit confirmation that the original source images and StarDist settings have not changed; their provenance cannot be verified automatically.

When results are reused, only the ImageJ stage runs with the requested `-p`. The StarDist model is not loaded if every input uses existing results. Both increasing and decreasing `-p` are supported because processing starts from the original StarDist masks.

Every ImageJ run creates a new `Final_Nuclei_Mask_<timestamp>/` folder without overwriting previous masks. Its `nuclei_run.json` records the selected StarDist folder, minimum area in pixels², completion status, and processed/skipped filenames. A run interrupted before completion retains a `running` status. Folder timestamps identify separate runs; the `-p` value is recorded in the JSON file.

`quantify_foci` selects the newest final nuclei-mask folder. Check that the new run completed successfully, then rerun quantification to use the new area filter; existing result tables are not updated automatically.

#### Nuclear morphology spreadsheet

Each new `Final_Nuclei_Mask_<timestamp>/` folder also contains `Nuclei_Morphology.xlsx`. Export is automatic with the same command, including when existing StarDist masks are reused. Install the updated package with `python -m pip install .` to include the new `openpyxl` dependency.

The workbook contains three sheets:

- **Nuclei**: one row per non-border object in the final mask after the requested `-p` filter. Nuclei touching any image edge are excluded from this sheet and its CSV copy. Columns include `Area_px2`, `Perimeter_px`, `Circularity`, `Aspect_ratio`, `Solidity`, `Major_axis_px`, `Minor_axis_px`, `Feret_max_px`, `Feret_min_px`, `Equivalent_diameter_px`, `Roundness`, `Eccentricity`, `Orientation_deg`, centroid coordinates and `Touches_border` (always false for exported rows).
- **Images**: `Nuclei_count_total` (all final-mask nuclei), `Border_nuclei_count` (excluded nuclei) and `Non_border_nuclei_count` (`Nuclei_count_total - Border_nuclei_count`), plus completion status and errors. Per-image arithmetic means (`Mean`), medians and interquartile ranges (IQR) of the size and shape metrics use **only non-border nuclei**. Orientation is not summarized because it is an axial angle. If no non-border nuclei remain, their count is `0` and all summary statistics are blank, even if border nuclei are present. A failed measurement has blank counts and an error. `Nuclei_count_total` replaces the previous `Nuclei_count` column.
- **Run_Info**: selected StarDist folder, `-p`, ImageJ version, units, measurement definitions and export status. The same status is recorded separately as `morphology_status` in `nuclei_run.json`.

UTF-8 CSV copies are saved as `Nuclei_Morphology.csv`, `Nuclei_Images.csv` and `Nuclei_Run_Info.csv`.

Measurements use ImageJ ParticleAnalyzer on a duplicate of the final binary mask, without additional watershed or size filtering. Objects touching any of the four image edges are counted separately and excluded from all per-nucleus morphology tables and summary metrics in both Excel and CSV. This export filter does not alter the binary masks or the stage 4 analysis. Area is the number of object pixels (`Area_px2`); lengths are in processed-image pixels. No intensity, texture, physical calibration or 3D volume is measured. Shapes refer to the 2D processed images at native XY dimensions. Older outputs may have been resized to 1024 x 1024; do not mix their pixel-based areas or lengths with new native-size results.

Circularity uses the ImageJ definition `min(1, 4*pi*Area/Perimeter^2)`. Aspect ratio is major/minor fitted-ellipse axis length; roundness is `4*Area/(pi*Major_axis^2)`; solidity compares area with convex-hull area. Feret values describe maximum and minimum caliper diameters. Equivalent diameter is `sqrt(4*Area/pi)` and eccentricity is `sqrt(1-(Minor_axis/Major_axis)^2)`. These pixel-boundary measurements can differ from other software definitions. Undefined values are left blank.

Rows include dataset and image identifiers, well (when recognizable as `WellA2`, `WellA02`, etc.), run ID, minimum area and StarDist source. `Condition` and `Biological_replicate` columns are omitted because these assignments have not been supplied at this stage. When comparing conditions, use independent biological replicates as the experimental units; nuclei within one image are not independent biological replicates.

The `Morphology_QC/` subfolder contains a 16-bit `*_ids.tif` label map and a `*_ids.png` preview with red nucleus numbers for each successfully measured image. QC images retain all final-mask nuclei, including the excluded border objects. `Nucleus_ID` matches the label-map pixel value and is local to an image and run; IDs are not renumbered after border exclusion, so table IDs can have gaps. These IDs are not guaranteed to match StarDist labels or `quantify_foci` IDs. The subfolder keeps QC label TIFFs separate from the binary masks consumed by stage 4. Final masks remain unchanged.

If an image cannot be measured or its QC files cannot be saved, its `Images` row records the error and the morphology export is marked `incomplete`; other images continue processing. Check export status before using the tables. An interrupted run can retain `running` status and lack a completed workbook.

### Optional branch after stage 2: nuclear marker intensity

Use this branch for a diffuse nuclear marker such as phospho-Smad2. It measures intensity inside each existing final nucleus, without generating foci, rerunning StarDist or adding another segmentation/size filter.

```bash
quantify_nuclear_intensity -i input_paths.json -c 2
```

`-c`/`--channel` is the **1-based marker channel in the original multichannel image**, not the nuclei channel or a prepared `Foci` folder number. If omitted, the program asks for it. The command reads originals directly from the experiment folders in `paths_to_files`; it never uses the prepared 8-bit/standard-deviation foci images for intensity measurement.

The terminal prompts proceed as follows:

1. List existing experiment folders, original-image counts and final-mask run counts. Choose one number, several comma-separated numbers (for example `1,3`), or `all`.
2. Select ND2 Z-stack, multichannel TIFF Z-stack, or 2D multichannel TIFF. For either stack format, the selected marker channel is projected with **ImageJ MAX over all Z planes**. A 2D TIFF is assumed to be an existing MAX projection and is only split by channel. You can supply `--input-type nd2`, `--input-type tiff-stack` or `--input-type tiff-2d` to skip this prompt. The same channel and input mode apply to this batch; run a separate batch for different acquisition layouts.
3. Inspect the listed `Final_Nuclei_Mask_<timestamp>` runs: full path/timestamp, `-p`, mask count, compatible source/mask pair count and validation errors. Choose the latest **completed compatible** run per selected experiment, all compatible runs, or a manual selection of one/several/all listed compatible runs. A newer incomplete run is never selected automatically. Experiments without compatible runs are reported and have no results.
4. The program prints the selected runs and starts analysis. Enter `q` at a selection prompt to cancel before results are created.

A usable mask run needs completed `nuclei_run.json` and morphology status, all recorded final masks, and matching `Morphology_QC/*_ids.tif` maps. Source pairing uses the exact original filename stem after removing the known final-mask suffix; missing or ambiguous matches block that run. Hidden files and directories, including `.DS_Store` and `._*`, are excluded from discovery and counts. Old runs without ID maps must pass through stage 2 again; validated StarDist masks can be reused.

**Native dimensions are required.** Original marker images, final masks and ID maps must have identical width and height. A mismatch blocks the run; the program never resizes either side. For old resized masks, rerun stage 1 and stage 2 at native size, and regenerate subsequent foci results if needed. Reassess `-p`: it is an area in pixels², so values selected for 1024 x 1024 inputs are not directly transferable to differently sized native images. Review StarDist segmentation at the native pixel scale as well. Original image files are never overwritten.

Bio-Formats reads raw channel planes; ImageJ performs MAX projection, ROI construction and measurements. Supported numerical inputs are 8-/16-bit signed or unsigned grayscale channels and 32-bit floating-point channels. The saved marker projection is **unnormalized float32**; integer values from supported input types are preserved exactly. There is no intensity threshold, background subtraction, brightness normalization or texture measurement. Zero-valued pixels inside each nucleus ROI are included. RGB images, multiple series/fields within one file, multiple timepoints, nonfinite channel values, or a Z-stack selected as 2D are rejected with an explanation. Export each field/timepoint as a separate multichannel file first. Input metadata must identify channel and Z axes correctly; the program does not infer them from TIFF page count.

Each selected experiment/mask-run pair creates its own `foci_assay/Nuclear_Intensity_<timestamp>/`, preserving earlier runs. Separate mask runs of the same image are never pooled. The folder contains:

- `Nuclear_Intensity.xlsx`, with **Nuclei**, **Images** and **Run_Info** sheets, plus UTF-8 CSV copies `Nuclear_Intensity.csv`, `Nuclear_Intensity_Images.csv` and `Nuclear_Intensity_Run_Info.csv`. CSV fields containing commas are quoted.
- `image_0001/`, `image_0002/`, etc., linked to original filenames in the tables. Each contains `marker.tif`, native-size full-frame binary `nucleus_<ID>_mask.tif` files, corresponding ImageJ `nucleus_<ID>.roi` files, and `numbered_nuclei.png` with eligible nucleus outlines/IDs. Display contrast is adjusted only for the PNG preview; saved marker values remain unchanged.
- `intensity_run.json` with run status/settings/provenance, and `intensity.log` with per-image progress and errors. A separate `Nuclear_Intensity_Batch_<timestamp>/batch.json` beside the input manifest records validation, selected runs and output paths without pooling measurements.

The **Nuclei** sheet contains one row per non-border nucleus and these measurements:

| Column | Definition |
| --- | --- |
| `Area_px2` | Number of native pixels in the nucleus ROI |
| `Marker_Mean` | Mean raw marker intensity within the ROI |
| `Marker_Median` | ImageJ median raw intensity within the ROI |
| `Marker_StdDev` | ImageJ sample standard deviation of ROI pixel intensities |
| `Marker_Min`, `Marker_Max` | Minimum and maximum ROI pixel intensities |
| `Marker_RawIntDen` | Sum of raw marker pixel values in the ROI |

`Nucleus_ID` is copied from the existing morphology ID map, including gaps after border exclusion. It is local to an image and nucleus-mask run. All table rows include source/mask/ID-map paths, dataset, channel, input mode/projection, Z-plane count, XY dimensions, source pixel type, run IDs, `-p` and StarDist source. `Condition` and `Biological_replicate` are omitted.

The **Images** sheet reports `Nuclei_count_total`, `Border_nuclei_count`, `Non_border_nuclei_count` (total minus border), `Nuclei_measured_count`, `Failed_nuclei_count`, status and error. Any nucleus touching an image edge is excluded from all per-nucleus intensity tables and summary metrics, and receives no individual mask/ROI in this branch. Original final masks and morphology QC remain unchanged. Each measurement has per-image `Mean`, `Median` and `IQR` summary columns (for example `Marker_Mean_Mean`), with equal weight per eligible nucleus. Empty populations have zero counts and blank statistics. A failed image exports no nucleus rows or statistics: counts remain blank if its ID map could not be validated; otherwise all its eligible nuclei are counted as failed/unaccepted. Partial image files are marked `FAILED.json` and must not be used as completed measurements.

The **Run_Info** sheet documents units, policies and ImageJ/Bio-Formats versions. File size and modification time fingerprints are checked before and after measurement to detect input changes during the run; these are not a registration check or proof that an old mask came from an unchanged original. Keep source images aligned and unchanged when reusing masks. Tables are checkpointed after each image. An interrupted run remains `running`; image failures produce an `incomplete` run and a nonzero command exit status. Inspect status/errors before using results.

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
| All five commands | `-i`, `--input` | Path to the input JSON manifest | Required |
| `generate_nuclei_mask` | `-p`, `--particle_size` | Minimum nucleus area in processed-image pixels | `2500` |
| `generate_foci_mask` | `-f`, `--foci_threshold` | Lower foci intensity threshold on processed 8-bit images | `150` |
| `quantify_foci` | `-j`, `--jobs` | Number of worker processes | `4` |
| `quantify_nuclear_intensity` | `-c`, `--channel` | Original marker channel, starting at 1 | Interactive |
| `quantify_nuclear_intensity` | `--input-type` | `nd2`, `tiff-stack`, or `tiff-2d` | Interactive |
| All five commands | `-h`, `--help` | Show command-line options | Not applicable |

The defaults document the implementation. Parameter selection should follow the experiment and the applicable protocol.

## Results and logs

Each input folder has its own analysis outputs. The following paths are relative to that input folder; `<timestamp>` represents the date and time generated by a stage.

| Location | Contents |
| --- | --- |
| `foci_assay/Nuclei/` | Prepared nuclei-channel images |
| `foci_assay/Foci/Foci_<index>_Channel_<channel>/` | Prepared images for each foci channel |
| `foci_assay/image_metadata.txt` | Source-image dimensions and calibration metadata |
| `foci_assay/Nuclei_StarDist_mask_processed_<timestamp>/` | Initial nuclei masks |
| `foci_assay/Final_Nuclei_Mask_<timestamp>/` | Processed nuclei masks, morphology workbook/CSV tables and run metadata |
| `foci_assay/Final_Nuclei_Mask_<timestamp>/Morphology_QC/` | Per-image nucleus ID label maps and numbered PNG previews |
| `foci_assay/Foci_Masks/Foci_<index>_Channel_<channel>_<timestamp>/` | Processed masks for a selected foci channel |
| `foci_assay/Nuclear_Intensity_<timestamp>/` | Separate marker-intensity workbook/CSV, native marker images, per-nucleus masks/ROIs and QC |
| `foci_analysis/Results_<timestamp>/` | Final table, numbered nuclei images, and optional intersection masks |

The final table is `all_results_with_coloc_universal.csv`. Its filename is also used when colocalization is disabled. It contains per-nucleus rows across the processed images in one input folder, with channel-specific measurements. Numbered nuclei images are saved as PNG files; intersection masks are saved as TIFF files when requested. Separate study-wide summary tables are not automatically generated by the current final stage.

Stage-specific logs include `1_log.log`, `2_val_log.log`, `2_log.log`, `nuclei_log.log`, `3_val_log.log`, `foci_log.log`, and `4_log.log`. Validation logs are written in the input folder; processing logs are saved with their corresponding stage outputs. Logs record warnings and errors, and the final stage also logs progress.

Nuclei-mask, foci-mask, and final-analysis directories include timestamps. The prepared-channel folders and metadata file use fixed paths, and some log files are overwritten on rerun. The existing foci quantification stage selects the latest matching mask folders; nuclear intensity instead uses the explicit experiment/run selection described above. Preserve the outputs needed for a previous analysis before repeating preparation or switching between analyses in the same input folder.


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
