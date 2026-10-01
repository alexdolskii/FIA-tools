# FIA-tools technical reference

This reference describes the code in the same repository checkout as this file. See the [README](../README.md) for installation, the two analysis routes and the [complete command-line parameter reference](../README.md#command-line-parameters).

The current documentation applies to `tech_dev`. The published [protocol version 1](https://www.protocols.io/view/fibroblast-ecm-functional-units-a-medium-throughpu-e6nvwqyz7vmk/v1) accompanies `main`; an updated protocol is in preparation. Record the exact Git commit, environment and parameters when reporting an analysis. Use the future version-specific protocol for experimental design and quality-control guidance, and this reference for software behavior.

- [Implementation and dependencies](#implementation-and-dependencies)
- [Shared preparation and nuclei measurements](#shared-preparation-and-nuclei-measurements)
- [Nuclear marker intensity](#nuclear-marker-intensity)
- [Collecting spreadsheets](#collecting-spreadsheets)
- [Statistical reports](#statistical-reports)
- [Foci analysis](#foci-analysis)
- [Output files and journals](#output-files-and-journals)
- [Troubleshooting](#troubleshooting)
- [Underlying tools](#underlying-tools)

## Implementation and dependencies

Python dependencies and terminal entry points are defined in [pyproject.toml](../pyproject.toml); the Conda environment is defined in [environment.yaml](../environment.yaml). The environment selects Python 3.10 and OpenJDK 11, with pins including StarDist 0.9.1 and TensorFlow 2.18.0. Package metadata permits broader ranges for several dependencies. A first `python -m pip install .` can therefore resolve dependencies to satisfy package requirements; these files are not a fully locked environment. The README separates initial installation from a code-only reinstall with `--no-deps` in an already compatible environment.

macOS and Linux environment builds are configured in [GitHub Actions](../.github/workflows/build-env.yml). A workflow definition alone does not establish successful validation on every system. Native Windows and WSL installation have not been validated for this documentation. WSL paths must be visible to Linux, for example `/mnt/c/...`; native Windows paths in JSON require escaped backslashes or forward slashes.

All FIA commands that initialize ImageJ read the same `FIJI_ENDPOINT` from `fia-tools/fiji_config.py`. Fiji is pinned to `sc.fiji:fiji:2.14.0`, and initialization remains headless. This one setting applies to channel preparation, nuclei masks, foci masks and nuclear-intensity measurements. To use Fiji without a pinned version, replace the assignment with `FIJI_ENDPOINT = "sc.fiji:fiji"`, as shown in the source comment; this permits dependency versions to change. After changing it, reinstall the package and start a new command process. This shared setting does not change Java memory or compatibility options.

### Development changes relative to the original implementation

The following changes describe the installation, interface, and development infrastructure in `tech_dev`:

- The software can be installed as a Python package, providing seven named terminal commands, including an optional nuclear-intensity workflow, spreadsheet collector and statistical report.
- The analysis scripts are located in `fia-tools/`, with a package entry module in `fia-tools/__init__.py`.
- A shared `environment.yaml` replaces the separate macOS and Linux environment files. The environment is named `fia_tools` and uses Python 3.10.
- `pyproject.toml` defines the package dependencies and terminal commands. The `uv.lock` file from `main` is not included in this branch.
- Log filenames in stages 1-3 use the `.log` extension.
- Stage 2 can reuse validated StarDist masks when changing the minimum nucleus area and exports nuclear morphology with pixel and available physical-unit measurements for each final-mask run.
- Native image width and height are preserved throughout processing and numbered QC exports; the former 1024 x 1024 resizing in stage 1 has been removed.
- `quantify_nuclear_intensity` measures original marker-channel values in existing final nucleus IDs, with explicit experiment/marker/run selection and separate results for each combination.
- `fia_collect_marker_intensity_results` collects nuclear morphology and marker-intensity spreadsheets into a separate folder inside `fia_assay`, with a combined per-nucleus table and per-image summaries.
- `fia_marker_intensity_report` compares collected morphology and marker intensity using a 96-well plate map, with nucleus-level violin plots, panels by plate-map color block and optional nucleus- or well-based statistics.
- The repository includes automated tests, GitHub Actions workflow definitions, and example intermediate and final results in `data/`.

| Stage | Script in `main` | Terminal command in `tech_dev` |
| --- | --- | --- |
| 1. Select and prepare channels | `code/1_select_channels.py` | `select_channels` |
| 2. Generate nuclei masks | `code/2_nuclei_mask_generation.py` | `generate_nuclei_mask` |
| 3. Generate foci masks | `code/3_foci_mask_generation.py` | `generate_foci_mask` |
| 4. Quantify foci | `code/4_foci_quantification.py` | `quantify_foci` |
| Optional nuclear intensity after stage 2 | Not available | `quantify_nuclear_intensity` |
| Collect morphology and marker-intensity tables | Not available | `fia_collect_marker_intensity_results` |
| Report nuclear morphology and marker intensity | Not available | `fia_marker_intensity_report` |

The README summarizes the current user-facing changes. Implementation details and data contracts are documented below.

## Shared preparation and nuclei measurements

All commands use `fia_assay` as the shared assay directory and `fia_assay/markers` for prepared marker channels. The names are defined centrally in `fia-tools/assay_layout.py`. Start a fresh analysis from `select_channels`; the programs do not search, migrate or fall back to `foci_assay` or its `Foci` folder. JSON paths still point to the original experiment folders. Existing results at old paths are left untouched.

References to older run metadata below do not enable discovery or migration of the old directory layout.

### Interactive input and terminal progress

All seven commands share validated interactive input. An invalid symbol, format or out-of-range selection prints a short explanation and repeats the same question without discarding earlier answers. Confirmations accept only `yes`/`y` and `no`/`n`; a typo never silently disables colocalization. Enter uses a default only when the prompt advertises one (newest reusable StarDist run, latest compatible intensity masks, or no for legacy-run confirmation). Every prompt accepts `q` to cancel; Ctrl+C and end-of-input also cancel without a traceback. Cancellation during a channel-preparation run is recorded as `CANCELLED` in `1_log.log`; an interrupted image is counted separately from failed images. Nuclei generation records cancellation during processing or a reuse prompt in `fia_assay/2_log.log`. Nuclear intensity records cancellation from its selection prompts onward in `fia_assay/3_nuclei_intensity.log`. Other command logs record cancellation when already open. Existing intensity/report completion markers continue to distinguish interrupted outputs from completed results. Invalid command-line options (for example, `-p abc`) still produce argparse usage errors instead of interactive retries, so automated launches remain noninteractive.

`select_channels` and `quantify_nuclear_intensity` use a compact live terminal line for the current image, processing stage, elapsed time and successful-image counters (per folder/run and across the selected batch). An image advances the success counter only after its outputs have been saved; failures are counted separately. Intensity compatibility checks have their own progress after choosing the mask-selection policy. Long filenames are shortened for display; full names remain in the journals.

Bio-Formats ND2 block messages update the stage percentage when available instead of printing a new line for every block. **This percentage describes the ND2 structure scan, not the entire image analysis**, and may stop at 99% before processing advances to the next stage. Projection and saving stages show their names and elapsed time without inventing percentages. Warnings and errors appear on separate lines. In `1_log.log` and `3_nuclei_intensity.log`, routine `Parsing block ... N%` messages are omitted; phase changes, image parameters, saved outputs, timestamps, elapsed time, warnings and errors are retained. Warning/error messages are retained even if they contain a block percentage. This file-only filter does not affect terminal progress. Redirecting stdout to a file produces concise image start/completion lines without terminal control codes. If the Java logging backend cannot be connected, the command warns once and retains normal Bio-Formats output. These display changes do not alter image processing or scientific measurements.

### Channel preparation

```bash
select_channels -i input_paths.json
```

The command first asks you to confirm processing and checks output folders for every input folder included in the analysis. It lists new and existing `fia_assay` locations and requests overwrite confirmation for each existing location **before initializing ImageJ or changing any results**. Answer `yes`/`y` to process that folder with overwrite, `no`/`n` to skip only that folder and continue to the next question, or `q` to cancel the entire batch before any writes, including folders already approved. New output locations are included automatically. Skipped folders retain all existing results and logs unchanged and do not enter the progress counters. After all decisions, the command prints selected/skipped folder counts and the number of selected input images. If every folder is skipped, or you decline the initial processing question, it exits normally without a traceback or a misleading analysis-success message. Only when folders remain selected does it initialize ImageJ and ask once for the input image type, nuclei segmentation channel, and number and indices of the marker channels. These markers can be used for foci analysis or nuclear marker-intensity measurements. There are no further overwrite prompts between folders. Confirmation retains the existing behavior of overwriting prepared-channel outputs and metadata; it does not delete the entire `fia_assay` directory or its other analysis runs.

For stacks, the workflow prepares maximum-intensity projections for nuclei and standard-deviation projections for marker channels. Prepared channels retain the original width and height and are converted to 8-bit images for segmentation. No channel, mask or numbered QC image is resized. Z projection collapses only the Z dimension; the XY pixel grid is unchanged. They are saved under `fia_assay/Nuclei/` and `fia_assay/markers/`; source metadata is written to `fia_assay/image_metadata.txt`. Each channel is stored in `markers/Foci_<index>_Channel_<channel>/` with `*_foci_projection.tif` filenames; they identify marker channels and do not imply that foci have already been detected. Nuclear-intensity measurements use the original images, with MAX projection for stacks, rather than the prepared SD/8-bit images.

Each processed input folder has a readable `fia_assay/1_log.log`, including successful runs. It records the input folder/JSON, software versions, shared channel choices, actual projection methods, image dimensions, brief calibration information, saved output paths, per-image outcomes, counts and elapsed time. Hidden files (including macOS `._*`) are counted separately as ignored files, not failed images. Saved TIFF headers are checked for the original XY dimensions. Calibration details and file checksums remain in `spatial_calibration.json` for downstream programs; the log does not replace that file.

The final log status is `SUCCESS` only when all discovered input images finish successfully; `PARTIAL` means some images failed, and `FAILED` means none succeeded or a processing exception stopped the run. Processing exceptions include a traceback; `q`, Ctrl+C and end-of-input record `CANCELLED` without a traceback. `NO_INPUT` identifies a folder with no supported files at processing time. A batch without any supported input images is rejected. If some images are skipped because they cannot be opened or lack a requested channel, the terminal reports incomplete results instead of successful completion. These existing per-image skips still allow the remaining images to be processed.

Before a confirmed rerun, the previous journal is moved to `fia_assay/logs/1_log_<timestamp>.log`; `1_log.log` then describes the current run only. Timestamps are UTC. Each folder has its own journal, and handlers are closed on completion or failure. The first folder's journal also captures ImageJ initialization and channel-selection errors. Input validation and cancellation before creating an output folder remain terminal-only events.

### Nuclei segmentation and morphology

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

#### Terminal progress and journals

In an interactive terminal, mask validation, StarDist and ImageJ display a single updating line with the current phase, image count and elapsed time. StarDist and ImageJ have separate counters; StarDist folder counts include only inputs requiring new segmentation. Reused masks are announced as `StarDist: reused`. The timer continues during model loading, inference and spreadsheet export; percentages count finished image attempts, not progress inside one image. Failed and skipped images are counted separately. Redirected output contains folder summaries instead of a success line for each image.

Folder summaries report mask and morphology status separately, output paths, elapsed time and the journal path. An incomplete morphology export makes the overall result incomplete even if the masks were saved. Unexpected warnings and errors retain the affected filename in the terminal; tracebacks and per-image details are saved in the journal. Expected low-contrast warnings emitted when saving uint16 label masks are retained individually in the journal and summarized once per folder in the terminal.

Each input folder has one current journal, `fia_assay/2_log.log`, covering validation, existing-mask checks and selection, StarDist segmentation or `STARDIST_REUSED`, ImageJ processing, morphology export and the overall outcome. It records settings, dimensions and bit depth, exact source/output paths, saved files, per-image outcomes, elapsed time, warnings and errors. A shared run ID identifies the same command invocation across folders.

At the start of a new command invocation, the previous journal is moved to `fia_assay/logs/2_log_<timestamp>.log`, alongside the first program's archived journals. Rotation happens once per input folder; all later stages append to the same current file. Timestamps use UTC, and existing archive filenames are protected against collisions. File handlers are closed after each stage, including on failure or cancellation.

The final `RUN_FINISHED` entry reports `COMPLETE`, `INCOMPLETE`, `FAILED` or `CANCELLED`. Cancellation during processing or at a reuse prompt is recorded without a traceback. If a later folder fails, an already completed folder retains `COMPLETE`; folders whose remaining stages were not run report `INCOMPLETE` (or `CANCELLED` when the batch was canceled). Invalid input JSON before any input folder is identified remains a terminal-only error.

New runs no longer create `2_val_log.log` beside the input data, `2_log.log` inside a StarDist result folder, or `nuclei_log.log` inside a final-mask folder. Existing journals in those locations remain untouched as historical records, and reused StarDist folders remain unchanged. Run metadata JSON files continue to be saved with their corresponding masks.

#### Repeat the area filter using existing StarDist masks

To try another minimum area, run the same command with a new `-p` value. For each input folder, the program searches for `fia_assay/Nuclei_StarDist_mask_processed_<timestamp>/` and checks mask filenames, completeness, TIFF readability, dimensions and data types. Hidden files and folders, including macOS `._*` entries, are excluded.

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

Each new `Final_Nuclei_Mask_<timestamp>/` folder also contains `Nuclei_Morphology.xlsx`. Export is automatic with the same command, including when existing StarDist masks are reused.

The workbook contains three sheets:

- **Nuclei**: one row per non-border object in the final mask after the requested `-p` filter. Nuclei touching any image edge are excluded from this sheet and its CSV copy. Columns include `Area_px2`, `Perimeter_px`, `Circularity`, `Aspect_ratio`, `Solidity`, `Major_axis_px`, `Minor_axis_px`, `Feret_max_px`, `Feret_min_px`, `Equivalent_diameter_px`, `Roundness`, `Eccentricity`, `Orientation_deg`, centroid coordinates and `Touches_border` (always false for exported rows).
- **Images**: `Nuclei_count_total` (all final-mask nuclei), `Border_nuclei_count` (excluded nuclei) and `Non_border_nuclei_count` (`Nuclei_count_total - Border_nuclei_count`), plus completion status and errors. Per-image arithmetic means (`Mean`), medians and interquartile ranges (IQR) of the size and shape metrics use **only non-border nuclei**. Orientation is not summarized because it is an axial angle. If no non-border nuclei remain, their count is `0` and all summary statistics are blank, even if border nuclei are present. A failed measurement has blank counts and an error. `Nuclei_count_total` replaces the previous `Nuclei_count` column.
- **Run_Info**: selected StarDist folder, `-p`, ImageJ version, units, measurement definitions and export status. The same status is recorded separately as `morphology_status` in `nuclei_run.json`.

UTF-8 CSV copies are saved as `Nuclei_Morphology.csv`, `Nuclei_Images.csv` and `Nuclei_Run_Info.csv`.

Measurements use ImageJ ParticleAnalyzer on a duplicate of the final binary mask, without additional watershed or size filtering. Objects touching any of the four image edges are counted separately and excluded from all per-nucleus morphology tables and summary metrics in both Excel and CSV. This export filter does not alter the binary masks or the stage 4 analysis. Raw area is the number of object pixels (`Area_px2`); raw lengths are in processed-image pixels. Physical size columns are added when calibration is available, as described below. Intensity, texture and 3D volume are not measured by this morphology stage. Shapes refer to the 2D processed images at native XY dimensions. Older outputs may have been resized to 1024 x 1024; do not mix their pixel-based areas or lengths with new native-size results.

Circularity uses the ImageJ definition `min(1, 4*pi*Area/Perimeter^2)`. Aspect ratio is major/minor fitted-ellipse axis length; roundness is `4*Area/(pi*Major_axis^2)`; solidity compares area with convex-hull area. Feret values describe maximum and minimum caliper diameters. Equivalent diameter is `sqrt(4*Area/pi)` and eccentricity is `sqrt(1-(Minor_axis/Major_axis)^2)`. These pixel-boundary measurements can differ from other software definitions. Undefined values are left blank.

Rows include dataset and image identifiers, well (when recognizable as `WellA2`, `WellA02`, etc.), run ID, minimum area and StarDist source. `Condition` and `Biological_replicate` columns are omitted because these assignments have not been supplied at this stage. When comparing conditions, use independent biological replicates as the experimental units; nuclei within one image are not independent biological replicates.

The `Morphology_QC/` subfolder contains a 16-bit `*_ids.tif` label map and a `*_ids.png` preview with red nucleus numbers for each successfully measured image. QC images retain all final-mask nuclei, including the excluded border objects. `Nucleus_ID` matches the label-map pixel value and is local to an image and run; IDs are not renumbered after border exclusion, so table IDs can have gaps. These IDs are not guaranteed to match StarDist labels or `quantify_foci` IDs. The subfolder keeps QC label TIFFs separate from the binary masks consumed by stage 4. Final-mask pixels remain unchanged; available spatial calibration is attached to their TIFF metadata.

If an image cannot be measured or its QC files cannot be saved, its `Images` row records the error and the morphology export is marked `incomplete`; other images continue processing. Check export status before using the tables. An interrupted run can retain `running` status and lack a completed workbook.

### Spatial calibration and physical units

`select_channels` reads physical XY pixel sizes from original ND2 (Bio-Formats OME metadata) or TIFF (OME or explicit ImageJ calibration) files. Units are normalized to `um`. Generic TIFF print-resolution/DPI tags and ImageJ defaults of one pixel are not microscopy calibration. Missing, nonpositive, nonfinite or unsupported values leave the image uncalibrated; no default physical pixel size is invented. Z spacing is not required for 2D measurements.

Prepared projections and new StarDist/final-mask folders include `spatial_calibration.json` snapshots bound to the exact output TIFF by SHA-256 and XY dimensions. Available XY calibration is also attached to prepared/final/QC and marker TIFF outputs. Image arrays keep their native dimensions. Reusing an older StarDist folder can recover calibration from native-size preparation metadata, or from a unique original with matching XY dimensions. A scale from a differently sized original is never applied to an old resized mask. Regenerate stages 1 and 2 for such masks.

New morphology tables retain `Area_px2` and the original pixel/shape columns. They add `Area_um2`, `Perimeter_um`, `Major_axis_um`, `Minor_axis_um`, `Feret_max_um`, `Feret_min_um`, `Equivalent_diameter_um`, physical centroids, and calibrated shape columns. `Nuclei_Images.csv` and the Images sheet include physical per-image means, medians and IQRs over non-border nuclei. Each image/nucleus row records `Pixel_size_X_um`, `Pixel_size_Y_um`, `Calibration_status`, `Calibration_source` and `Calibration_note`. Physical fields are blank when no validated scale is available; raw pixel fields remain populated. Area is `Area_px2 * Pixel_size_X_um * Pixel_size_Y_um`. Square-pixel lengths scale directly; anisotropic pixels use ImageJ calibrated ROI perimeter/Feret and an affine transform of the ImageJ fitted ellipse, without resampling. The report uses calibrated shape descriptors where available. Counts and dimensionless units are unchanged.

Marker-intensity exports also include per-image calibration and `Area_um2`. `Marker_RawIntDen` remains the sum of original pixel values inside each non-border nucleus: it is not converted to square micrometers or normalized by pixel size. The `-p` nucleus-area threshold continues to mean **pixels²**. No physical-area threshold option is introduced in this update.

The collector preserves calibration in the archived spreadsheets and checks that physical areas agree with raw pixel areas. Conflicting morphology/intensity scales are rejected. If a new calibrated intensity run is paired with older morphology lacking calibration, regenerate nucleus measurements using the existing StarDist results and measure intensity for that new nucleus run; the collector will not guess missing geometry.

The report chooses units per image **before aggregation, statistics and plotting**:

- Calibrated images contribute only to `um2`/`um` size endpoints, displayed as **µm²/µm** on graphs. Their pixel values are retained in tables but never plotted or tested as pixel-size endpoints.
- Uncalibrated images contribute only to separate `px2`/`px` size endpoints.
- Mixed datasets have separate rows and tests for physical and pixel size cohorts. Area plots also remain separate (`Area_um2` and `Area_px2`), each requiring a significant adjusted comparison. Units are never pooled in one comparison or axis. Well means are computed separately within each cohort. Count and raw intensity populations remain unchanged.
- `Calibration_Summary.csv` and its workbook sheet list retained images, wells with retained images and non-border nuclei per condition and calibration cohort. Plot captions and statistical tables include contributing image counts. Each plotted metric has one complete overview containing all its panels.

Older collected spreadsheets remain readable in pixel units because the report does not reopen original images. To obtain physical units for an existing experiment, rerun `generate_nuclei_mask` with the desired **same pixel threshold** and reuse validated StarDist masks; then rerun `quantify_nuclear_intensity`, `fia_collect_marker_intensity_results`, and `fia_marker_intensity_report`. These runs create new result folders. A new `select_channels` run is necessary only if native prepared images or usable source calibration are unavailable, or if earlier images were resized. No archived spreadsheet is rewritten in place.

## Nuclear marker intensity

Use this branch for a diffuse nuclear marker such as phospho-Smad2. It measures intensity inside each existing final nucleus, without generating foci, rerunning StarDist or adding another segmentation/size filter.

```bash
quantify_nuclear_intensity -i input_paths.json
```

Select markers from the existing `fia_assay/markers/Foci_<index>_Channel_<channel>/` folders. **`Channel_N` always selects channel N in the original multichannel image**; the `Foci` index is only the order assigned during preparation. For example, choosing `Foci_1_Channel_2` measures original channel 2. There is no separate channel prompt or `-c`/`--channel` option; previous commands containing that option must be updated. The command reads originals directly from the experiment folders in `paths_to_files`; it never uses the prepared 8-bit/standard-deviation marker images for intensity measurement.

The command uses **all experiment folders listed in the JSON**; there is no second experiment-selection prompt. The startup sequence is:

1. List the experiment folders, original-image counts and final-mask run counts. Missing/hidden folders are reported and recorded as skipped; they prevent an overall successful batch status.
2. List the available marker folders and their TIFF counts. A single marker is selected automatically; with several markers, choose numbers or `all`. Hidden, empty and nonstandard folders are excluded. A selected marker unavailable in an experiment is recorded as unprocessed, with no substitution, and makes the batch incomplete.
3. If all discovered original images are ND2, select `nd2` automatically. TIFF or mixed-format inputs still ask for the input type. You can explicitly supply `--input-type nd2`, `--input-type tiff-stack` or `--input-type tiff-2d`; the program never silently overrides it. The same mode applies to the batch. For stacks, the selected marker channel is projected with **ImageJ MAX over all Z planes**. A 2D TIFF is assumed to be an existing MAX projection and is split by channel. Keep the channel mapping consistent within each experiment. Across experiments, marker folders are matched by full name; biological marker names are not inferred from channel numbers.
4. Read the small completion manifests before loading ImageJ. If each available experiment/marker has only one completed candidate, select it automatically for validation. Otherwise ask once: **Enter/1** = latest compatible, **2** = all compatible, **3** = manual selection, **q** = cancel. Manual selection lists candidate paths and `-p` values; compatibility is still checked afterward. Equal `-p` values do not make different mask runs duplicates.
5. Initialize ImageJ and validate the chosen candidates. For **latest**, check from newest to oldest until a compatible run is found for each experiment/marker; older runs are then left unchecked. For **all**, check every completed candidate. For **manual**, check only the chosen candidates; an incompatible manual selection is reported as unprocessed and is never replaced silently. Every measured run must still pass all source/mask/ID-map checks. Changed run metadata after the choice is rejected.
6. Print the final combinations and number of image measurements, then start analysis without an additional confirmation. Each combination retains separate outputs. Enter `q` at an available prompt, or use Ctrl+C, to cancel.

For two ND2 experiments with one marker and two completed candidate mask runs each, only the mask-choice question remains. Latest selects one compatible run per experiment; all can select both, doubling the number of image measurements. Missing requested data or the absence of compatible masks makes the batch incomplete (nonzero exit status), even when other experiments finish successfully. Runs deliberately omitted in manual selection are recorded as not selected.

A usable mask run needs completed `nuclei_run.json` and morphology status, all recorded final masks, and matching `Morphology_QC/*_ids.tif` maps. Source pairing uses the exact original filename stem after removing the known final-mask suffix; missing or ambiguous matches block that run. Hidden files and directories, including `.DS_Store` and `._*`, are excluded from discovery and counts. Old runs without ID maps must pass through stage 2 again; validated StarDist masks can be reused.

**Native dimensions are required.** Original marker images, final masks and ID maps must have identical width and height. A mismatch blocks the run; the program never resizes either side. For old resized masks, rerun stage 1 and stage 2 at native size, and regenerate subsequent foci results if needed. Reassess `-p`: it is an area in pixels², so values selected for 1024 x 1024 inputs are not directly transferable to differently sized native images. Review StarDist segmentation at the native pixel scale as well. Original image files are never overwritten.

Bio-Formats reads raw channel planes; ImageJ performs MAX projection, ROI construction and measurements. Supported numerical inputs are 8-/16-bit signed or unsigned grayscale channels and 32-bit floating-point channels. The saved marker projection is **unnormalized float32**; integer values from supported input types are preserved exactly. There is no intensity threshold, background subtraction, brightness normalization or texture measurement. Zero-valued pixels inside each nucleus ROI are included. RGB images, multiple series/fields within one file, multiple timepoints, nonfinite channel values, or a Z-stack selected as 2D are rejected with an explanation. Export each field/timepoint as a separate multichannel file first. Input metadata must identify channel and Z axes correctly; the program does not infer them from TIFF page count.

Each selected experiment/marker/mask-run combination creates its own `fia_assay/Nuclear_Intensity_<marker-folder>_<timestamp>/`, preserving earlier runs. For example, selecting `Foci_1_Channel_2` creates `Nuclear_Intensity_Foci_1_Channel_2_20260923_210000/`. Different markers and different mask runs of the same image are never pooled. The prepared TIFFs in `markers` provide the marker catalog and discovery counts only; their pixels are never read for intensity analysis. The folder contains:

- `Nuclear_Intensity.xlsx`, with **Nuclei**, **Images** and **Run_Info** sheets, plus UTF-8 CSV copies `Nuclear_Intensity.csv`, `Nuclear_Intensity_Images.csv` and `Nuclear_Intensity_Run_Info.csv`. CSV fields containing commas are quoted.
- `image_0001/`, `image_0002/`, etc., linked to original filenames in the tables. Each contains `marker.tif`, native-size full-frame binary `nucleus_<ID>_mask.tif` files, corresponding ImageJ `nucleus_<ID>.roi` files, and `numbered_nuclei.png` with eligible nucleus outlines/IDs. Display contrast is adjusted only for the PNG preview; saved marker values remain unchanged.
- `intensity_run.json` with run status, settings and provenance.
- `batch.json`, a copy of the full batch journal in each created intensity result folder. It records performed validation, the mask-selection policy, skipped experiments/markers, selected runs, output paths and statuses without pooling measurements. A shared run ID links it to the text journals. Copies are updated throughout the batch and finalized after success, failure or cancellation; `Batch_journal` in run metadata/CSV/Excel points to the saved journal. No batch folder is created beside the input JSON during normal execution. If result folders cannot be created or journal copies cannot be written, a fallback is retained under `~/.fia-tools/logs/Nuclear_Intensity_Batch_<timestamp>/batch.json` and its path is printed. A run starting without a writable result journal references that fallback when available. A final journal-copy failure returns a nonzero exit status even if measurements completed. Cancellation before measurement creates no result folder or `batch.json`; the text journal records the cancellation. Existing batch folders from earlier versions are not moved or deleted.

The complete text journal is `fia_assay/3_nuclei_intensity.log` in each experiment. It starts during input discovery and includes selections, ImageJ initialization, validation, source dimensions/pixel types, projection and measurement settings, per-image outcomes, saved result locations, elapsed time, warnings, errors and final folder status. Different markers and mask runs append to the same journal with explicit identifiers. Selection cancellation and initialization failures are recorded even when no measurement outputs exist.

On the next command invocation, the previous text journal is moved once to `fia_assay/logs/3_nuclei_intensity_<timestamp>.log`. Log timestamps are UTC; filename collisions preserve both archives. Stage handlers are closed on every exit, and each experiment keeps its own measurement diagnostics. Completed folders retain `COMPLETE` when a later folder is interrupted; unfinished folders record `CANCELLED`, `FAILED` or `INCOMPLETE` as appropriate. A folder omitted entirely by manual mask selection is labeled `NOT_SELECTED`. A journal write failure is reported and prevents a successful command exit.

New result folders no longer contain `intensity.log`. Existing result logs and older results are preserved. The structured `intensity_run.json` and `batch.json` files remain alongside the results with their existing completion and fallback behavior. An unreadable/invalid input JSON before any experiment is identified is reported in the terminal.

The **Nuclei** sheet contains one row per non-border nucleus and these measurements:

| Column | Definition |
| --- | --- |
| `Area_px2` | Number of native pixels in the nucleus ROI |
| `Marker_Mean` | Mean raw marker intensity within the ROI |
| `Marker_Median` | ImageJ median raw intensity within the ROI |
| `Marker_StdDev` | ImageJ sample standard deviation of ROI pixel intensities |
| `Marker_Min`, `Marker_Max` | Minimum and maximum ROI pixel intensities |
| `Marker_RawIntDen` | Sum of raw marker pixel values in the ROI |

`Nucleus_ID` is copied from the existing morphology ID map, including gaps after border exclusion. It is local to an image and nucleus-mask run. All table rows include `Marker_folder`, `Marker_folder_path`, `Marker_channel`, source/mask/ID-map paths, dataset, input mode/projection, Z-plane count, XY dimensions, source pixel type, run IDs, `-p` and StarDist source. `Condition` and `Biological_replicate` are omitted.

The **Images** sheet reports `Nuclei_count_total`, `Border_nuclei_count`, `Non_border_nuclei_count` (total minus border), `Nuclei_measured_count`, `Failed_nuclei_count`, status and error. Any nucleus touching an image edge is excluded from all per-nucleus intensity tables and summary metrics, and receives no individual mask/ROI in this branch. Original final masks and morphology QC remain unchanged. Each measurement has per-image `Mean`, `Median` and `IQR` summary columns (for example `Marker_Mean_Mean`), with equal weight per eligible nucleus. Empty populations have zero counts and blank statistics. A failed image exports no nucleus rows or statistics: counts remain blank if its ID map could not be validated; otherwise all its eligible nuclei are counted as failed/unaccepted. Partial image files are marked `FAILED.json` and must not be used as completed measurements.

The **Run_Info** sheet documents units, policies and ImageJ/Bio-Formats versions. File size and modification time fingerprints are checked before and after measurement to detect input changes during the run; these are not a registration check or proof that an old mask came from an unchanged original. Keep source images aligned and unchanged when reusing masks. Tables are checkpointed after each image. An interrupted run remains `running`; image failures produce an `incomplete` run and a nonzero command exit status. Inspect status/errors before using results.

## Collecting spreadsheets

```bash
fia_collect_marker_intensity_results -i input_paths.json
```

This optional command collects **marker intensity**, not foci counts or colocalization results. It reads existing spreadsheets and run metadata; it does not initialize ImageJ or StarDist, read image pixels, resize images, or repeat measurements. It uses the same `paths_to_files` manifest.

1. Use all existing visible experiment folders from the JSON, without repeating experiment selection. Missing input folders are reported and prevent an overall successful command exit.
2. If each available experiment has one valid completed final-nuclei run, select it automatically. Otherwise choose **Enter/1** for the latest valid run per experiment, **2** for all valid runs, or **3** for manual selection. Each selected experiment/nuclei-run pair gets a separate collection; different `-p` runs are never pooled.
3. Select one marker, several, or `all`. Choose `none` for morphology only. Markers use their full folder names, such as `Foci_1_Channel_2`; biological names are not inferred. Enter `q` at a selection prompt to cancel.

For each selected marker, the collector selects the latest valid completed intensity run that explicitly references the selected nuclei run. Incomplete or malformed candidates are skipped with a recorded reason. If a selected completed intensity run conflicts with morphology in dataset, image set, nucleus IDs, areas or counts, collection fails for that nuclei run instead of silently choosing older measurements. Legacy intensity runs without marker-folder metadata are labeled `Channel_N` and are kept separate from named marker folders. Hidden files/folders, including macOS `._*`, are ignored.

Each collection is saved inside the experiment’s analysis folder:

`fia_assay/FIA_Marker_Intensity_Combined_Results_<timestamp>/`

Previous collections and source results are preserved. The new folder contains **only spreadsheets**, with no masks, images, ROIs, JSON files, or logs:

| Files | Contents |
| --- | --- |
| `Nuclei_Morphology.xlsx`, `Nuclei_Morphology.csv`, **`Nuclei_Images.csv`**, `Nuclei_Run_Info.csv` | Unmodified copies from the selected final-nuclei run |
| `Nuclear_Intensity_<marker>.xlsx`, `Nuclear_Intensity_<marker>.csv`, `Nuclear_Intensity_Images_<marker>.csv`, `Nuclear_Intensity_Run_Info_<marker>.csv` | Unmodified spreadsheet contents from each selected intensity run; filenames identify the marker, for example `Nuclear_Intensity_Foci_1_Channel_2.xlsx` |
| `FIA_Marker_Intensity_Combined.xlsx` | Combined **Nuclei**, **Images**, and **Collection_Info** sheets |
| `FIA_Marker_Intensity_Nuclei.csv`, `FIA_Marker_Intensity_Images.csv` | CSV copies of the combined measurement sheets |

The combined **Nuclei** sheet has one row per non-border nucleus, with morphology and separate columns for each marker's six intensity measurements. Matching uses the recorded dataset, nuclei run, mask basename and `Nucleus_ID`; `Area_px2` must agree and is retained once. `Image_name` identifies the original TIFF/ND2 where resolvable; `Morphology_Image_name` preserves the prepared image name. If original identity cannot be resolved for morphology-only data, the original-name fields remain blank and the reason is recorded.

The combined **Images** sheet preserves per-image morphology counts and summaries and adds per-marker intensity summaries. `Nuclei_Images.csv` is also copied separately as requested. Border nuclei remain excluded from per-nucleus rows and summary statistics; their counts remain available. No folder-wide averaging, condition assignments, biological-replicate assignments or statistical tests are added.

Missing intensity is explicitly marked `MISSING`, with blank measurement cells rather than zeros; the collection status is `SUCCESS_WITH_MISSING_INTENSITY`. **Collection_Info** records selected and rejected runs, settings, source paths, source SHA-256 checksums and collection status. Copied spreadsheets are byte-for-byte unchanged; their paths still refer to the original analysis folders. Long `Run_Info` text may already be truncated at Excel's 32,767-character cell limit; the copied CSV retains its full value.

The collector checks agreement between each source workbook and its CSV copies, then checks that source tables/metadata have not changed during collection. Results are prepared in a temporary folder and only the expected spreadsheets are moved, with the combined workbook published last as the completion indicator. Hidden files, including macOS `._*` companions, are never selected for transfer; companions that disappear automatically during rollback do not interrupt cleanup. A validation conflict creates a new folder containing only `Collection_Report.xlsx`, and the command returns a nonzero status; other selected nuclei runs continue. File-operation failures are reported as `IO_FAILED`, separately from `VALIDATION_FAILED`. Review missing-data and diagnostic statuses before downstream analysis.

The full command journal is `fia_assay/4_collect_marker_intensity.log` in each experiment. It opens during input discovery and records source validation, choices, parameters, image/nucleus counts, missing markers, saved outputs, elapsed time, warnings, errors and cancellation. Multiple selected nuclei runs append to one journal. On the next invocation, the previous journal moves once to `fia_assay/logs/4_collect_marker_intensity_<timestamp>.log`; timestamp collisions preserve both archives. Journals use UTC. An unavailable requested experiment or an experiment without valid morphology makes the command return nonzero; deliberately unselected runs remain unselected. Missing marker intensity retains the existing `SUCCESS_WITH_MISSING_INTENSITY` behavior.

## Statistical reports

After collection, place your 96-well plate-map workbook inside each selected `FIA_Marker_Intensity_Combined_Results_<timestamp>/` folder, or supply its path with `--template`. The filename is arbitrary. The program identifies a plate map by its grid, excluding analytical workbooks, hidden files and Excel lock files (`~$*`). If several plate maps are present, use `--template` explicitly.

If the Excel experiment layout is absent, the terminal explicitly displays `MISSING_PLATE_MAP`, the selected collection folder (or missing explicit `--template` path), and instructions to add the workbook and rerun. `INVALID_PLATE_MAP` identifies unrecognized/unreadable layouts and records the filename and rejection reason; `AMBIGUOUS_PLATE_MAP` lists multiple valid layouts and requests an explicit choice with `--template`. These expected input problems are recorded without a traceback in `5_marker_intensity_report.log`, with matching error codes, locations and required actions in `report_status.json` and `Report_Diagnostics.xlsx`. The affected report remains `FAILED`, calculations do not start, and other selected collections continue.

```bash
fia_marker_intensity_report -i input_paths.json --stats-unit nucleus
```

For well-based statistics:

```bash
fia_marker_intensity_report -i input_paths.json --stats-unit well
```

Omit `--stats-unit` for plots and descriptive tables without hypothesis tests. The report reads spreadsheets only: it does not start ImageJ, read image pixels, repeat segmentation or change upstream results. Archived collector spreadsheets suffice even if the original image-drive paths are no longer accessible.

All valid experiment paths in the JSON are analyzed automatically. Collections are discovered only inside `fia_assay`. The default is the **latest completed collection per experiment**, with its full path and nuclei run printed before processing. A separate report is created for **each selected collection**, preserving different nuclei runs and particle-size settings. Use `--collections all` for every completed collection, or `--collections ask` for interactive selection (the menu accepts `latest`, `all`, `manual`, or their numeric aliases).

Markers are selected **once for the entire batch, before any reports are generated**. A single marker is selected automatically; with multiple markers, choose one, several, all, or `none` for morphology only. The common catalog shows availability by collection. The choice is applied by exact folder name: a marker absent from a collection is skipped there with a note in the console, report log and Overview; if none of the selected markers is present, that collection receives a morphology-only report. A marker explicitly recorded as `MISSING` retains blank measurements and its planned tests. No other marker or channel is substituted. A legacy `Channel_N` result is excluded within a collection when a named `Foci_<index>_Channel_N` result exists. For example, `Foci_1_Channel_2` is used instead of `Channel_2`; these populations are never concatenated. Select at most one marker folder per original channel **within each collection**; an ambiguous interactive selection is requested again before processing. The folder name is the marker identifier; no biological name is inferred.

For a run without selection prompts:

```bash
fia_marker_intensity_report -i input_paths.json \
  --markers Foci_1_Channel_2 \
  --stats-unit nucleus --template "/path/to/plate_map.xlsx"
```

`--markers` accepts comma-separated folder names, `all`, or `none`, and skips the common marker prompt. Unknown or duplicate names and conflicting same-channel selections are rejected before processing. `--all-experiments` remains accepted for compatibility; all valid JSON paths are now used without that flag. An explicit `--template` applies to every selected collection; otherwise each collection supplies its own plate map. The manifest uses the same `paths_to_files` key as the preceding FIA commands.

### Plate-map convention

- The first worksheet, or the worksheet named by `--sheet`, contains columns **1–12 in B1:M1**, rows **A–H in A2:A9**, and condition names in **B2:M9**.
- Each imaged well must have a literal condition name. `A2` and `A02` normalize to `A02`; the report checks agreement with `Well...` and, where present, `Point...` filename tokens.
- For statistics, a **direct solid cell fill** defines a comparison block. Exactly one condition per color must be **bold**, defining the control; every other condition of that color is compared with it. Wells of the same condition must have consistent fill and bold status. Conditional formatting, formulas and merged cells cannot define the comparison grid.
- Unimaged annotated wells remain in the design with blank measurements. Groups without usable observations are retained, with a reason when a test cannot be performed. Biological replicates are not inferred.

### Plots and statistical units

Image-count filtering is **disabled by default**. Use optional **`--min-nuclei N`** to retain images with at least `N` non-border nuclei; `N` must be a nonnegative integer. Omitting the option or using `--min-nuclei 0` keeps all validated images, including zero-count images. `--min-nuclei 1` reproduces the previous zero-image exclusion, including border-only images. `--min-nuclei 20` excludes counts 0–19 and retains 20 or more:

```bash
fia_marker_intensity_report -i input_paths.json --stats-unit nucleus --min-nuclei 20
```

This image-level filter runs after input validation and plate annotation, before aggregation, statistics and plotting, and applies to every selected marker and morphology metric, removing all nuclei of each excluded image from the report. `Excluded_Images.csv` and the matching Excel sheet identify each excluded source image, mask, well, condition, total/border/non-border counts, `Min_nuclei` threshold and reason. The table is empty when filtering is disabled. `Image_Filter_Summary.csv` and its Excel sheet report original, excluded and retained image counts per condition, plus wells with input images and wells retained. The effective threshold and filter policy are saved in `Run_Info`, `report_status.json` and plot captions. The original images, collector tables and archived inputs remain unchanged. A low nucleus count is an optional exclusion criterion, not proof of poor image quality; count summaries describe only images retained by the chosen threshold.

Intensity and morphology use **violin plots with a small inner boxplot, without individual nucleus dots**. Every usable non-border nucleus contributes to the distribution, without subsampling. Violin contours use Scott smoothing, have equal maximum widths and stop at the observed minimum and maximum. Boxes show the median and IQR; whiskers extend to the most extreme observations within 1.5 IQR. A thin range line retains extremes without outlier dots. With fewer than five nuclei, only the box/range is drawn; a constant or single value is shown as a horizontal line. An empty group keeps its labeled position with `n=0`. Nuclei-count graphs remain boxplots with every retained image shown as a point.

All Y axes remain **linear**, with the same limits across panels of a given metric. Panels follow the existing plate-map color blocks. Shared whole-word condition prefixes move into figure/panel titles, leaving shorter wrapped condition labels; full names remain unchanged in all data and statistics tables. `Plot_Labels` records the exact display mapping. Labels show usable nuclei or images and contributing wells after filtering. Each plotted metric has **one overview containing all its panels**, exported as **PDF by default**. Use `--plot-format pdf`, `--plot-format png` or `--plot-format both` to choose standalone files. The layout remains in up to two columns with as many rows as needed; figure height grows without shrinking panels or splitting into `__Page_02.png` files. Base filenames are preserved, for example `Nuclei_count.pdf` and `Foci_1_Channel_2_Integrated_density.png`. Excel always embeds complete overview raster previews. In PDF-only mode these previews are generated in memory and stored inside the workbook; no standalone PNG files are created. Separate `__Block_01`, `__Block_02`, etc. PNG/PDF files are no longer generated; every block remains inside the overview. Descriptive reports also use consistent direct fills when available; otherwise they retain an unsplit view. Changing the export selection does not split or recalculate statistical families. Existing report folders are not modified or cleaned up; these rules apply to newly generated reports.

Figures use **Arial**, falling back to Liberation Sans or DejaVu Sans when unavailable. Main titles are 20 pt, panel headings 16 pt, conditions/axes/significance labels 14 pt, and sample sizes/captions 12 pt. Condition names wrap to the available font width; sample sizes appear separately below them. Stable panel letters identify the blocks within each overview. The overview has one shared Y-axis title. The marker identifier remains in the subtitle, and the full nuclei run ID moves to the footer. A short caption states the population, active image-count filter and test unit; the full methods remain in `Overview` and `Plot_Info/Caption`. Selected PNG exports and embedded Excel previews use **300 dpi**; PDF exports are **vector PDF**. `Plot_Info` records the primary exported `File`, the available `PNG_file` and `PDF_file` paths (blank for formats not selected), the font and the short display caption. The existing panel grouping and complete overview content are preserved.

| Measurement | Plot observations | `--stats-unit nucleus` | `--stats-unit well` |
| --- | --- | --- | --- |
| Non-border nuclei count | One point = non-border nuclei in one image | Images: the explicit exception to nucleus-level tests | One mean count per image per well |
| `Marker_RawIntDen` per selected marker | Violin from all usable non-border nuclei, in both statistics modes; no individual dots | Individual nuclei | One mean per well |
| Morphology metrics | Violin from all usable non-border nuclei for Area, Aspect ratio, Circularity and Solidity only; each requires at least one tested control comparison with `P_Holm < 0.05` | Individual nuclei; all morphology metrics tested | One mean per well; all morphology metrics tested |
| Other intensity metrics | No additional plots | Individual nuclei | One mean per well |

For morphology and intensity in well mode, calculate the mean across usable nuclei **within each retained image**, then the mean of those image means **within each well**. Retained images receive equal weight within a well; wells receive equal weight in the test. These well means do not replace individual nuclei on plots. Images excluded by `--min-nuclei` contribute to no metric. When filtering is disabled, a retained zero-nucleus image contributes zero to count analysis but no value to morphology/intensity means. A well or condition with no usable observations for a metric stays in the design with blank measurements, `n=0` and unavailable tests; it is never replaced by a zero-valued observation. Missing marker measurements stay blank, while measured zero intensity in a retained nucleus remains valid. Welch tests and the full planned Holm families are recomputed after filtering; adjusted p-values can change even for metrics whose observations did not change.

The tested metrics are:

- `Non_border_nuclei_count` = `Nuclei_count_total - Border_nuclei_count`.
- Morphology: `Area_um2`/`Area_px2`, `Perimeter_um`/`Perimeter_px`, `Circularity`, `Aspect_ratio`, `Solidity`, `Major_axis_um`/`Major_axis_px`, `Minor_axis_um`/`Minor_axis_px`, `Feret_max_um`/`Feret_max_px`, `Feret_min_um`/`Feret_min_px`, `Equivalent_diameter_um`/`Equivalent_diameter_px`, `Roundness`, `Eccentricity`. Physical and pixel alternatives use disjoint image populations.
- Each selected marker: `Marker_Mean`, `Marker_Median`, `Marker_StdDev`, `Marker_Min`, `Marker_Max`, `Marker_RawIntDen`.

Existing image-summary `Median` and `IQR` columns remain descriptive, without separate hypothesis tests. Border nuclei are excluded from nucleus-level tables and all morphology/intensity summaries and tests. Image tables preserve total and border counts for traceability. Intensity stays raw and unnormalized: integrated density sums original marker-channel pixel values within the nucleus, without background subtraction. Raw `Area_px2` remains in the exported data; size summaries, tests and plots use physical measurements when available, as described below.

Tests are **two-sided Welch t-tests** against the control. **Holm correction** covers all planned treatment-versus-control comparisons across count, morphology and all selected markers **within each color block**, including comparisons that cannot be tested. With one marker, two treatments and one size-unit cohort, the family contains `(1 + 12 + 6) × 2 = 38` comparisons. With mixed calibration, each size metric has separate physical and pixel cohorts; both sets of planned comparisons enter the same Holm family, including unavailable tests. No nucleus contributes to both unit cohorts of a size metric. Outputs include raw/adjusted p-values, sample sizes, contributing images/wells/nuclei, treatment-minus-control differences and nominal 95% Welch confidence intervals. Confidence intervals are not adjusted for multiplicity. Plot brackets show **only significance symbols based on Holm-adjusted p-values**: `ns` for p ≥ 0.05, `*` for p < 0.05, `**` for p < 0.01, `***` for p < 0.001 and `****` for p < 0.0001. Exact raw and adjusted p-values remain in the tables. Unavailable comparisons retain `Not tested`, never `ns`.

Tests need at least two usable observations per condition in the selected unit. **One well per condition gives no well-based p-values**, while retaining plots and descriptive results. Both groups constant also gives `NOT_TESTED`. Nucleus/image tests are exploratory: observations within a well are dependent, and Welch/Holm do not model that clustering. Wells within a plate are not automatically independent biological replicates. Separate experiments and mask runs are never pooled.

After statistics, morphology plots are limited to **Area, Aspect ratio, Circularity and Solidity**. Each requires at least one successful treatment-versus-control test of that same metric with **Holm-adjusted p < 0.05**. Calibrated `Area_um2` and uncalibrated `Area_px2` remain separate endpoints and plots, each evaluated independently. Raw p-values, differences between means, or significance of a related omitted metric do not trigger a plot. The selected `nucleus` or `well` mode determines significance; the resulting graph always represents the distribution of individual non-border nuclei. Each qualifying parameter includes all conditions and all its planned comparisons across its panels, including nonsignificant and unavailable comparisons. Count and integrated-density plots remain unconditional. Without `--stats-unit`, or if no eligible morphology comparison qualifies, no additional morphology plots are created. **All 12 morphology parameters remain in the data, summary and comparison tables and the full Holm family**, regardless of plotting eligibility or significance. The tests and adjusted p-values are unchanged by plot selection; the planned family also includes separate size-unit cohorts when calibration is mixed. No additional mean-intensity plot is generated.

### Report outputs and validation

Each report is saved in a new `fia_assay/FIA_Marker_Intensity_Report_<timestamp>/` folder. Previous reports and collector folders are preserved.

| Output | Contents |
| --- | --- |
| `FIA_Marker_Intensity_Report.xlsx` | Overview, Morphology_By_Condition, Morphology_Comparisons, Statistics, Summary, Nuclei, Images, Excluded_Images, Image_Filter_Summary, Calibration_Summary, Image_Values, Well_Values, Plate_Map, Plot_Data, Plot_Labels, Plot_Info, Run_Info, Source_Files and embedded Plots |
| `Nuclei_Morphology_Summary.xlsx` | Separate morphology workbook: Morphology_By_Condition, Morphology_Comparisons and Run_Info; no plots |
| `Excluded_Images.csv`, `Image_Filter_Summary.csv` | Excluded image identities, counts, threshold and reasons; before/after image counts per condition (also in the main Excel workbook) |
| Corresponding `.csv` tables | Data, aggregation values, statistics and provenance |
| `Plots/` | Count boxplots, integrated-density violins and eligible significant morphology violins; one complete overview per plotted metric in PDF (default), PNG or both; the main workbook always embeds raster previews; no separate block files |
| `Inputs/` | Byte-preserved input spreadsheets, plate map and input manifest |
| `report_status.json` | Selected settings and completion status; the text journal is kept at experiment level |

The full report command journal is `fia_assay/5_marker_intensity_report.log`. It includes discovery and batch selection before any report exists, source/template verification, selected settings (including `--stats-unit`, `--min-nuclei` and `--plot-format`), retained/excluded images, statistics/plot counts, saved files, elapsed time, warnings, errors and cancellation. All reports for one experiment append to this journal. A later invocation archives it once as `fia_assay/logs/5_marker_intensity_report_<timestamp>.log`. New reports no longer contain a separate `report.log`; existing logs and `report_status.json` completion rules are preserved. A journal write failure prevents a successful exit. A folder that finished successfully keeps `COMPLETE` if a later folder is canceled; unfinished folders record cancellation/failure, and intentional manual omissions record `NOT_SELECTED`. If the input JSON cannot be read before identifying experiments, the error is reported in the terminal.

Both collection and reporting show one updating terminal line with the current collection/report, phase and elapsed time. Within countable phases, progress shows completed/total operations and a percentage: source runs, archived files, image/nucleus records, image/metric combinations, or plots. Filenames identify table records; these commands never reanalyze image pixels. A plot counts as finished only after all selected standalone formats are saved and its Excel preview is ready. Excel writing and final table verification show their phase and elapsed time without an invented percentage. These are phase percentages, not estimates of total runtime. Redirected output contains phase summaries without control codes or a separate line for every image. Warnings/errors remain visible; full details go to the journals.

`Plot_Data` retains each usable observation once per plotted metric. In `Plot_Info`, `Points` counts observations represented in the complete overview; `Rendered_points` counts individual observation dots (zero for nucleus-level plots). `View` is `overview`, including a single-block report, and `Panels` lists every included block. `Plot_format` is recorded in `Run_Info` and `report_status.json`. Plot progress counts completed overviews, advancing after every selected format is saved and the Excel preview is ready.

`Morphology_By_Condition` has one row for each morphology metric and size-unit cohort, including circularity, area, aspect ratio, solidity, roundness and eccentricity. Conditions appear side by side, with numeric `N`, `Mean`, sample `SD`, `Median` and `IQR` columns. In `nucleus` mode, these describe individual non-border nuclei. In `well` mode, they describe the same equal-image-weight well means used in the tests. `N` counts usable observations for that metric in the stated `Observation_unit`; sample SD is blank when fewer than two observations are available. Empty conditions retain `N=0` and blank descriptive statistics. Without `--stats-unit`, the table describes nuclei and the comparison sheet contains headers only.

`Morphology_Comparisons` contains the morphology rows from the existing `Statistics` table: treatment-versus-control means, differences, nominal 95% confidence intervals, raw and Holm-adjusted p-values, sample sizes and explicit reasons for unavailable tests. It preserves the original Holm family across count, all morphology metrics and selected markers; exporting this table does not recalculate or narrow the correction. These two morphology sheets are also included in the main report, with matching `Morphology_By_Condition.csv` and `Morphology_Comparisons.csv`. The additional workbook uses the same completion status as the main report. Significant morphology graphs are saved in the main report and `Plots/`; the separate morphology workbook retains its tables-only layout.

Validation checks archived source SHA-256 hashes, workbook/CSV agreement, nucleus identities, areas, counts, run/marker identities and image summaries. Exported workbook/CSV agreement and unchanged inputs are verified before **`SUCCESS`** is written to `report_status.json`. Incomplete collector folders are skipped during discovery. If a selected completed collection is inconsistent, its report fails rather than silently falling back to older data. A failed report has `FAILED` status and `Report_Diagnostics.xlsx`; incomplete outputs must not be used. Other selected collections continue. The command returns nonzero if any selected report fails, a requested experiment has no completed collections, or an input experiment folder is missing/hidden.

## Foci analysis

Prepared marker channels use standard-deviation projections for stacks. Thresholding and watershed create foci masks for each selected channel. Quantification labels nuclei, measures foci count and area inside each nucleus, and optionally measures intersections between selected channels. The final table combines these per-nucleus readouts within each input folder; separate experiments are not pooled.

### Generate foci masks

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

### Quantify foci and channel overlap

The package provides the following entry point:

```bash
quantify_foci -i input_paths.json
```

It asks whether to start processing and whether to perform colocalization analysis. Generate masks for all required channels before starting this stage. Colocalization is selected interactively; the current command does not expose a separate colocalization flag.

```bash
python fia-tools/4_foci_quantification.py -i input_paths.json
```

## Temporary resources and system diagnostics

All seven installed analysis commands, and direct execution of their script files, use a lightweight supervisor. Argument validation and `--help` run before a worker or temporary directory is created. The analysis runs in a child Python process with the same interpreter, arguments, working directory and terminal streams. A fresh temporary directory is selected before importing processing libraries. `TMPDIR`, `TEMP` and `TMP` are set only in the worker environment; `fiji_config` supplies `-Djava.io.tmpdir=<directory>` before ImageJ starts. Existing heap limits, Fiji endpoint, StarDist model, image types and numerical settings are preserved. Imported Python functions called directly by another application do not automatically start this CLI lifecycle.

The default runtime root is `~/.fia-tools` (`~` means the current user's home directory on each OS). An absolute writable `FIA_RUNTIME_HOME` can select another location. For example, in a macOS/Linux shell use `export FIA_RUNTIME_HOME="/path/to/FIA-runtime"`; in PowerShell use `$env:FIA_RUNTIME_HOME = "D:\FIA-runtime"`. Use the same setting for later diagnostics. The supervisor does not silently substitute another location if it cannot create its run storage. This setting does not relocate existing caches or search previous custom runtime roots.

| Location | Purpose and lifetime |
| --- | --- |
| `<runtime root>/tmp/<run_id>/` | Python/library temporary files for one command invocation; Java uses this directory too |
| `<result directory>/.fia_tmp_<run_id>/` | Registered staging directory on the destination filesystem, for atomic metadata/CSV/XLSX replacement or collection publication |
| `<runtime root>/runs/<run_id>/run.json` | Command, arguments, host, timestamps, process identities, exit code, sampled resource peaks and cleanup outcome |
| `<runtime root>/runs/<run_id>/worker.json` | Worker identity recorded before processing imports |
| `<runtime root>/runs/<run_id>/resources.jsonl` | Registry of owned temporary directories, including those on external result disks |
| `<runtime root>/runs/<run_id>/runtime.log` | Runtime start, process exit and cleanup outcome; retained after temporary data are removed |

Run IDs combine a UTC timestamp and random suffix. Each owned directory has a `.fia-owner.json` marker. Distinct runs use distinct directories, including simultaneous runs. This isolates temporary storage; it does **not** make simultaneous writes to the same analysis outputs safe. Per-experiment stage journals include a `TEMPORARY_RESOURCES` line linking their own stage run ID to the common runtime record.

Cleanup waits until the worker has actually exited, including its Python/JVM shutdown. The supervisor samples the process tree and checks PID **and process creation time**, not PID alone. After exit code `0` or a normal cancellation with code `130`, it removes only registered directories with the expected run name, verified owner marker and canonical path, and only if the observed processes have exited. It leaves final results, permanent caches and other programs' temporary files alone. A failure/nonzero exit, a still-running descendant, incomplete process observation or a registry problem retains temporary resources. A failed cleanup is recorded as `INCOMPLETE`. These runtime states describe process/resource handling, not scientific completeness: existing stage journals and report status files remain authoritative for analysis outcomes.

Process inspection uses `psutil>=5.9,<8`, declared in both dependency manifests. macOS, Windows and Linux APIs are used through psutil; restricted permissions or an inconsistent process namespace produce an explicit unknown/partial state rather than permission to delete. On Windows, locked files can cause incomplete cleanup; the supervisor reports the error and leaves them for inspection. A killed supervisor cannot finish its journal, and a rapidly detached descendant may escape sampling. Such interrupted records remain possible remainders, not proof that cleanup is safe.

The supervisor samples process-tree RSS and system swap approximately once per second while waiting. The recorded peaks are sampled observations: short spikes can be missed, summed RSS can count shared pages more than once, and system swap belongs to the whole computer. They do not identify the cause of a freeze or reconstruct resource use from an earlier unmonitored run. OS swap is not redirected into FIA temporary storage.

`fia_diagnostics` runs without importing ImageJ, scyjava, TensorFlow, StarDist, NumPy or openpyxl. It inspects:

- The selected runtime root and its registered temporary directories, including result-disk staging areas.
- Known current-user temporary roots for top-level `openpyxl.*` files. These files remain `UNATTRIBUTED`: another Python application may own them.
- Existing Fiji/jgo, Maven, StarDist model and Matplotlib cache locations, including relevant cache environment overrides where known. These are persistent caches, not disposable analysis remainders. Custom locations not represented by these settings may be outside the scan.
- With `-i`, metadata of files below each `fia_assay`, recognizable old `.tmp`/`.partial` files and collection staging directories, plus the tails of current stage journals. Original images and workbook contents are not read. Missing experiment folders are reported.
- Registered process identities and their current RSS, RAM availability, system swap and free space on the runtime/result disks. Unregistered Python/Java processes are not attributed to FIA.

Resource categories are `ACTIVE`, `OWNED_REMAINDER` (verified directory belonging to an exited, observed run), `POSSIBLE_REMAINDER`, `UNKNOWN`, `UNATTRIBUTED` and `PERSISTENT_CACHE`. Age alone never establishes ownership or safe deletion. Missing final journal lines indicate an incomplete journal, not necessarily a dead process. No diagnostic option deletes files, stops processes or removes caches.

The scan uses five terminal stages and per-experiment progress. Directory traversal does not follow symlinks/junctions or cross nested mount points. `--max-entries` defaults to `100000` per inspected location; a reached limit, access failure or unavailable process inspection appears in the journal and marks the scan `PARTIAL`. This is a bounded check of known locations, not an exhaustive scan of every system file or every plugin cache. Scan sizes are logical file sizes, not guaranteed reclaimable disk space.

The current diagnostic journal is `<runtime root>/fia_diagnostics.log`, with previous copies archived under `<runtime root>/logs/`. When `-i` is supplied, the same diagnostic snapshot is also saved in each **existing** `fia_assay/fia_diagnostics.log`, with earlier versions in `fia_assay/logs/`. Thus these copies describe the complete requested diagnostic batch. Journals contain a readable summary followed by a detailed JSON snapshot. The exit code is `0` for a complete saved scan, `1` for an incomplete scan or save failure, `2` for invalid CLI/input JSON, and `130` for cancellation. Finding remainders does not itself make a scan incomplete.

Permanent caches are deliberately reused in their existing locations; changing their paths would require downloading or migrating dependencies/models and could discard user configuration. The runtime manager neither relocates nor deletes them. Old temporary files created before this mechanism have no reliable run registration and remain candidates for review.

## Output files and journals

Each input folder has its own analysis outputs. The following paths are relative to that input folder; `<timestamp>` represents the date and time generated by a stage.

| Location | Contents |
| --- | --- |
| `fia_assay/Nuclei/` | Prepared nuclei-channel images |
| `fia_assay/markers/Foci_<index>_Channel_<channel>/` | Prepared images for each marker channel (shared catalog for both workflows) |
| `fia_assay/image_metadata.txt` | Source-image dimensions and calibration metadata |
| `fia_assay/1_log.log` | Current channel-preparation run: settings, per-image outcomes and final status |
| `fia_assay/logs/1_log_<timestamp>.log` | Previous channel-preparation journals preserved on rerun |
| `fia_assay/2_log.log` | Current nuclei-generation run, from validation and mask selection through StarDist/ImageJ and morphology |
| `fia_assay/logs/2_log_<timestamp>.log` | Previous nuclei-generation journals preserved on rerun |
| `fia_assay/3_nuclei_intensity.log` | Current nuclear-intensity command, including selection, validation and all marker/mask combinations |
| `fia_assay/logs/3_nuclei_intensity_<timestamp>.log` | Previous nuclear-intensity journals preserved on rerun |
| `fia_assay/4_collect_marker_intensity.log` | Current marker-collection command, including selection, checks and every selected nuclei run |
| `fia_assay/logs/4_collect_marker_intensity_<timestamp>.log` | Archived marker-collection journals |
| `fia_assay/5_marker_intensity_report.log` | Current report command, including discovery, filters, statistics, plots and saving |
| `fia_assay/logs/5_marker_intensity_report_<timestamp>.log` | Archived report journals |
| `fia_assay/Nuclei_StarDist_mask_processed_<timestamp>/` | Initial nuclei masks |
| `fia_assay/Final_Nuclei_Mask_<timestamp>/` | Processed nuclei masks, morphology workbook/CSV tables and run metadata |
| `fia_assay/Final_Nuclei_Mask_<timestamp>/Morphology_QC/` | Per-image nucleus ID label maps and numbered PNG previews |
| `fia_assay/Foci_Masks/Foci_<index>_Channel_<channel>_<timestamp>/` | Processed masks for a selected foci channel |
| `fia_assay/Nuclear_Intensity_<marker-folder>_<timestamp>/` | Separate marker-intensity workbook/CSV, native marker images, per-nucleus masks/ROIs and QC |
| `fia_assay/FIA_Marker_Intensity_Combined_Results_<timestamp>/` | Copied morphology/intensity spreadsheets and combined per-nucleus/per-image tables for one selected nuclei run; diagnostic workbook only if collection fails |
| `fia_assay/FIA_Marker_Intensity_Report_<timestamp>/` | Separate marker-intensity/morphology report with statistics, panelled violin/count plots, input snapshots and completion status |
| `foci_analysis/Results_<timestamp>/` | Final table, numbered nuclei images, and optional intersection masks |

The final table is `all_results_with_coloc_universal.csv`. Its filename is also used when colocalization is disabled. It contains per-nucleus rows across the processed images in one input folder, with channel-specific measurements. Numbered nuclei images are saved as PNG files; intersection masks are saved as TIFF files when requested. Separate study-wide summary tables are not automatically generated by the current final stage.

Channel preparation, nuclei generation, nuclear intensity, collection and reporting keep their current journals in `fia_assay` at the fixed paths listed above, with earlier runs archived in `fia_assay/logs/`. All five record successful events, warnings, errors and final statuses. Other stage-specific logs include `3_val_log.log` in the input folder, and `foci_log.log` and `4_log.log` alongside their corresponding outputs; the final stage also logs progress. Older `2_val_log.log` and per-result nuclei journals are retained, but are no longer written by new nuclei-generation runs.

Nuclei-mask, foci-mask, and final-analysis directories include timestamps. The prepared-channel folders and metadata file use fixed paths, and some log files are overwritten on rerun. The existing foci quantification stage selects the latest matching mask folders; nuclear intensity uses all manifest experiments and the marker/mask-selection policy described above. Preserve the outputs needed for a previous analysis before repeating preparation or switching between analyses in the same input folder.

## Troubleshooting

| Symptom | Action |
| --- | --- |
| Terminal command is unavailable after installation or updating | Activate the environment used for installation. Run `python -m pip install --no-deps .` from the updated checkout only when its requirements are already satisfied, then check the command with `--help`. See [installation](../README.md#installation-and-updating). |
| First ImageJ or StarDist initialization needs downloads | Allow network access for Fiji/Maven dependencies and the StarDist model. Inspect the current command journal for the initialization failure; successful `--help` alone does not validate these downloads. |
| Nuclei or marker folders are not found after the directory-name change | Start a fresh analysis with `select_channels`, using original experiment folders in the JSON. New commands use `fia_assay/Nuclei` and `fia_assay/markers`; they do not migrate or search `foci_assay/Foci`. |
| A source image, mask or ID map has different XY dimensions | Recreate preparation and nuclei results at native dimensions. The program does not resize images to make them compatible. Reassess the pixel-area threshold for the native scale. |
| A collector candidate is rejected | Read the validation reason in `4_collect_marker_intensity.log` and any `Collection_Report.xlsx`. Ensure morphology and intensity refer to the same completed nuclei run and image/ID population. |
| `MISSING_PLATE_MAP` | Add the Excel experiment layout to the selected collection folder, or supply `--template`, and rerun the report. The error lists the expected location. |
| `INVALID_PLATE_MAP` or `AMBIGUOUS_PLATE_MAP` | Correct the workbook/grid or choose one workbook explicitly with `--template`; use `--sheet` for a different worksheet. See [plate-map conventions](#plate-map-convention). |
| A condition has no observations or a test is unavailable | Inspect the plate map, `Excluded_Images`, `Image_Filter_Summary`, missing-marker records and the reason in `Statistics`. Missing observations remain blank; they are not replaced with zero. |
| A run is incomplete, failed or cancelled | Read its current journal and completion metadata before using the outputs. Completed image files alone do not establish successful completion of the whole run. |

When reporting a problem in [GitHub Issues](https://github.com/alexdolskii/FIA-tools/issues), include the command, exact Git commit, operating system, active environment, relevant journal entries and a minimal reproducible example. Keep image/channel identifiers sufficient to match the error to its input.

## Underlying tools

- [Fiji/ImageJ](https://fiji.sc/) provides image import, projections, mask processing and ROI measurements. [Fiji source](https://github.com/fiji/fiji); [licensing information](https://imagej.net/licensing/).
- [StarDist](https://stardist.net/) provides nuclei segmentation with the pretrained `2D_versatile_fluo` model. [StarDist source](https://github.com/stardist/stardist); [license](https://github.com/stardist/stardist/blob/main/LICENSE.txt).

FIA-tools uses its own [MIT license](../LICENSE). Project contributors and publication information are listed in the [README](../README.md#results-documentation-and-help).
