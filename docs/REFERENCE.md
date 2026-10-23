# FIA-tools technical reference

For installation and complete CLI options, see [README](../README.md). The authoritative current-format Excel definition and module map are in [PLATE_SCHEME.md](PLATE_SCHEME.md). Whole-field signal formulas and assumptions are in [PFAK.md](PFAK.md).

This reference describes the current development workflow. One FIA workbook beside raw images defines per-well assignments for every stage; results without its provenance require a new analysis. Preparation and measurements use separate identities for methods, marker names and original channels.

## Implementation and dependencies

Python dependencies and terminal entry points are defined in [pyproject.toml](../pyproject.toml); the Conda environment is defined in [environment.yaml](../environment.yaml). The environment selects Python 3.10 and OpenJDK 11, with pins including StarDist 0.9.1 and TensorFlow 2.18.0. Package metadata permits broader ranges for several dependencies. A first `python -m pip install .` can therefore resolve dependencies to satisfy package requirements; these files are not a fully locked environment. The README separates initial installation from a code-only reinstall with `--no-deps` in an already compatible environment.

macOS and Linux environment builds are configured in [GitHub Actions](../.github/workflows/build-env.yml). A workflow definition alone does not establish successful validation on every system. Native Windows and WSL installation have not been validated for this documentation. WSL paths must be visible to Linux, for example `/mnt/c/...`; native Windows paths in JSON require escaped backslashes or forward slashes.

All FIA commands that initialize ImageJ read the same `FIJI_ENDPOINT` from `fia-tools/fiji_config.py`. Fiji is pinned to `sc.fiji:fiji:2.14.0`, and initialization remains headless. This one setting applies to channel preparation, nuclei masks, foci masks and nuclear-intensity measurements. To use Fiji without a pinned version, replace the assignment with `FIJI_ENDPOINT = "sc.fiji:fiji"`, as shown in the source comment; this permits dependency versions to change. After changing it, reinstall the package and start a new command process. This shared setting does not change Java memory or compatibility options.

## Preparation and selection

The JSON identifies raw-image experiment folders. All selected schemes are validated before image processing. Missing, duplicate, malformed or inconsistent schemes are shown in the terminal and recorded in the command's experiment journal. Invalid input does not start an imaging engine or create a successful analysis.

Channel selection questions are removed. Nuclei preparation uses MAX and the existing 8-bit segmentation input. Foci preparation uses the existing SD projection and 8-bit conversion. Nuclear intensity preparation uses native-value MAX, and Field signal preparation uses SUM. Fiber and Ignore are recorded without FIA measurements. Actual intensity measurements reread the original images rather than prepared display/segmentation files.

ND2 is detected automatically. The user still resolves ambiguous TIFF stack/projection semantics; overwriting prepared data and selecting among compatible analysis runs are separate choices. A blank channel means absent; an existing unused channel must say Ignore. Original XY geometry is retained. Numerical routines and imaging dependencies are not changed by Excel routing.

Each prepared directory contains a manifest of its successful images and original/output checksums. Stale extra TIFF files do not enter downstream analysis. Incomplete preparation cannot be reused as a complete source.

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

The final `RUN_FINISHED` entry reports `COMPLETE`, `COMPLETE_WITH_EXCLUSIONS`, `NO_ELIGIBLE_IMAGES`, `INCOMPLETE`, `FAILED` or `CANCELLED`. Cancellation during processing or at a reuse prompt is recorded without a traceback. If a later folder fails, an already completed folder retains its completed status; folders whose remaining stages were not run report `INCOMPLETE` (or `CANCELLED` when the batch was canceled). Invalid input JSON before any input folder is identified remains a terminal-only error.

New runs no longer create `2_val_log.log` beside the input data, `2_log.log` inside a StarDist result folder, or `nuclei_log.log` inside a final-mask folder. Existing journals in those locations remain untouched as historical records, and reused StarDist folders remain unchanged. Run metadata JSON files continue to be saved with their corresponding masks.

#### Processing exclusions

StarDist inference runs in a separate persistent Python worker with the same model, normalization and prediction thresholds. Native failures such as `ClipperLib::clipperException` terminate that worker only. FIA records the current image as excluded and restarts the worker for the next eligible image. A model initialization failure stops the run instead of blacklisting healthy inputs. Saving/logging failures also stop the run. No dependency update or image bit-depth change is involved.

Before inference, FIA verifies 2D uint8 input and finite normalized values. A nonconstant image whose default normalization percentiles (3 and 99.8) coincide is excluded before division can amplify rare pixels. A truly constant image remains eligible; a valid image with no detected nuclei is not a processing failure. Ordinary images use exactly the existing normalization and StarDist thresholds.

Each experiment owns `fia_assay/excluded_images.json`. The append-only journal `fia_assay/logs/2_excluded_images.log` records the full image identity, source path and SHA-256, UTC time, stage, reason and error detail. This journal is cumulative across runs; the usual `2_log.log` still rotates normally. Exclusions match the full acquisition/field basename, not the well or Seq number, and apply only within that experiment. Repeating a command or replacing a source file does not silently reinstate an excluded image. Original input files and historical result folders are not deleted or modified.

New StarDist runs record `complete_with_exclusions` when every input is accounted for by a saved mask or explicit exclusion. Only saved eligible masks proceed to ImageJ. Completed final-mask runs retain the existing `complete` metadata contract for their accepted images and record exclusions separately. The command journal reports `COMPLETE_WITH_EXCLUSIONS`; a folder with no eligible images reports `NO_ELIGIBLE_IMAGES` and does not create artificial final masks. Unexpected missing masks still invalidate reuse.

Intensity and foci processing consult the registry before reading excluded images. Collection and reporting also filter measurements from older runs against current exclusions. Each result carries a portable `excluded_images.json` snapshot and `Processing_Exclusions.csv`; morphology, intensity, collection and report workbooks include a `Processing_Exclusions` sheet. The collection hashes its exclusion snapshot. Archived source spreadsheets retain their original bytes; their excluded measurements never enter the combined analytical tables. A report copied away from its experiment uses its collection's snapshot, without requiring the original image drive. A disconnected old archive cannot discover exclusions made after that archive was created.

Processing exclusions are applied before count plots, intensity/morphology summaries, statistics and `--min-nuclei`. They are not zero-count observations and do not appear on diagnostic count plots. `Image_Filter_Summary` has a separate `Processing_excluded` column; `Images_total` describes successfully processed images before the minimum-nuclei filter. The report retains template conditions with no usable images as `n=0`. Invalid exclusion manifests stop analysis rather than silently reintroducing images.

#### Repeat the area filter using existing StarDist masks

To try another minimum area, run the same command with a new `-p` value. For each input folder, the program searches for `fia_assay/Nuclei_StarDist_mask_processed_<timestamp>/` and checks mask filenames, completeness, TIFF readability, dimensions and data types. Hidden files and folders, including macOS `._*` entries, are excluded.

If reusable results exist, the program lists their paths and mask counts, newest first, and offers these choices:

- Enter a folder number, or press Enter to reuse the newest valid result.
- Enter `n` to run StarDist again for this input folder.
- Enter `a` to reuse the newest valid result for this and the remaining input folders. Inputs without reusable results still require a new StarDist run.
- Enter `q` to cancel before starting new segmentation.

New StarDist runs include `stardist_run.json`, which records the settings, source and mask SHA-256 checksums, and completion status. Incomplete runs, changed files and mismatched settings are not offered for reuse. Every reused run must also have a compatible current-format plate-scheme snapshot. Runs without scheme provenance are rejected.

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
- Mixed datasets have separate rows and tests for physical and pixel size cohorts. Area plots also remain separate (`Area_um2` and `Area_px2`), shown in the common nuclear report. Units are never pooled in one comparison or axis. Well means are computed separately within each cohort. Count and raw intensity populations remain unchanged.
- `Calibration_Summary.csv` and its workbook sheet list retained images, wells with retained images and non-border nuclei per condition and calibration cohort. Plot captions and statistical tables include contributing image counts. Each plotted metric has one complete overview containing all its panels.

## Independent analysis routes

| Route | Eligible images | Output and statistical scope |
| --- | --- | --- |
| Common nuclei | All annotated wells after processing exclusions | `fia_nuclei_report`: counts and four core morphology overviews; all 12 morphology metrics in tables |
| Foci | Wells assigned the requested Foci marker | Per-marker threshold masks; nucleus-local counts/areas; only explicit intersections from Excel |
| Nuclear intensity | Wells assigned the requested Nuclear intensity marker | Original MAX values in existing non-border nucleus IDs; one report per marker |
| Field signal | Wells assigned the requested Field signal marker | Original SUM32 threshold mask, integrated intensities, area and objects; separate marker/threshold/nucleus-run/border-policy profiles |

### Nuclear intensity and collection

`quantify_nuclear_intensity` uses the existing original-value reader. Supported integer values are represented exactly in float32; this is not an 8-bit rescaling. MAX uses all Z planes, while 2D sources are used directly. True zero pixels remain in each ROI. Nuclei are neither resegmented nor relabeled. Source dimensions, channel count, mask IDs, border classification and run metadata are checked.

The measurement tables retain mean, median, standard deviation, minimum, maximum and RawIntDen. RawIntDen is the sum of original values inside the nucleus. The main marker plot is integrated density. Channel numbers are recorded per image; a method/name marker identity stays constant across wells.

The collector combines morphology with each marker's exact assigned subset. It verifies nucleus IDs, image populations, areas, calibration, run identity, completion and CSV/workbook agreement. Archives preserve the source table bytes. Missing assigned measurements are MISSING; unassigned wells are NOT_APPLICABLE with blank values. Neither is converted to zero.

`quantify_nuclear_intensity` automatically calls this collection stage using the exact completed outputs selected in its invocation. There is no second mask-run prompt or scan for newer/older intensity results. Each experiment/nucleus-run combination is collected independently, using only its selected markers. An incomplete requested marker blocks that combination; an invalid table fails its collection. Other combinations continue. Successful measurement exports remain intact if collection fails, and the overall command returns nonzero. `batch.json` separately records `measurement_status`, `collection_status` and `collections` (source mask run, markers, exact measurement inputs, status and output path). The measurement journal remains `3_nuclei_intensity.log`; the collection journal remains `4_collect_marker_intensity.log`, archived once per invocation.

The standalone `fia_collect_marker_intensity_results -i input_paths.json` is an optional table rebuild/recovery command. It retains interactive nucleus-run selection and latest-compatible intensity selection for all assigned markers, with explicit missing values when a marker has no usable result. The report remains a separate command so plot, filtering and statistics settings can be changed without repeating image measurements.

A separate marker report is created for each collection/marker. Common nuclear graphs and nuclear statistics are produced only by `fia_nuclei_report`. Images and Nuclei tables in marker reports retain nuclear QC. Thresholds/settings from different Field signal profiles are never pooled.

### Foci

`generate_foci_mask --marker NAME -f VALUE` uses the existing inclusive 8-bit threshold and watershed. The default is 150; values must be 0–255. Use separate invocations for different marker thresholds.

`quantify_foci` uses compatible complete nucleus and Foci masks. It processes only wells with Foci assignments. Missing required masks or wrong XY dimensions fail the affected run rather than creating partial zero measurements. A blank intersection cell means individual-marker results only. An explicit triple does not add pairs.

The output is under `fia_assay/foci_analysis/Results_<timestamp>`. `Marker_Assignments.csv` resolves marker IDs to names, wells and source channels. `Requested_Intersections.csv` maps short TIFF filenames to image, well, original channels and marker combinations. The wide per-nucleus result table retains blanks for unassigned markers and explicit availability fields. A run metadata file records SUCCESS/FAILED/CANCELLED, mask checksums and timing. This update does not introduce a new Foci hypothesis-testing report.

## Reporting and statistics

PLOT LAYOUT supplies panel positions and left-to-right condition order. Conditions absent after filtering retain their position. A condition without this marker is labeled as unassigned, not interpreted as a measured zero. Palette assignment uses the full annotation before filtering.

`--min-nuclei` is an inclusive minimum non-border nucleus count per image; zero disables filtering. Processing exclusions are applied first, then marker assignments, then the minimum count filter. These steps precede aggregation and testing. Excluded rows and reasons remain available as QC tables.

For nuclei and nuclear intensity, optional tests use individual nuclei or technical-well summaries. Count tests use images when nucleus mode is requested. Field signal uses images or equal-image-weight well summaries; its individual objects never become inferential replicates. Tests are two-sided Welch comparisons against the bold control. Holm correction is separate per marker and comparison block; common nuclear outcomes have their own family. A comparison absent by design is NOT_APPLICABLE. A planned comparison with insufficient retained observations stays NOT_TESTED in the planned family.

PDF is the default standalone format; PNG or both are optional. Excel includes raster plot previews regardless of standalone format. Original measurements, observation populations and unit conversions are not changed by presentation options. QC PNG images from segmentation/measurement are independent of plot export format.

### Condition colors and image markers

All report styles are centralized in `fia-tools/plot_palette.py`. Colors are the user-selected Wada sRGB values (Elwyn digital reconstruction), not a claimed original numbered historical combination. The same fill applies to a condition's violin body and inner boxplot, or its image-count boxplot. Dark outer outlines retain visible boundaries; inner violin boxes use a contrasting outline. Each median uses whichever of white or `#111314` gives greater contrast against the fill. Violin fills are opaque so exported colors match the recorded HEX values. Condition labels and axis positions identify groups independently of color.

| Role in a block with an explicit control | Fill | HEX |
| --- | --- | --- |
| Control | Grayish Lavender A | `#B5B1D8` |
| First treatment | Dusky Green | `#004F46` |
| Second treatment | Orange | `#F37420` |
| Third treatment | Deep Indigo | `#051230` |
| Fourth treatment | Dull Blue Violet | `#80719E` |
| Fifth treatment | Ivory Buff | `#EBD3A2` |
| Sixth treatment | Violet | `#4F4086` |
| Seventh treatment | Verditter Blue | `#6FB5A8` |

Only explicit bold plate-map roles determine the control; names such as `Control` do not. Colors restart in each comparison block and follow existing condition order, skipping the control when assigning treatment colors. Control position is unchanged. All annotated conditions count, including those without images. Nine or more conditions use lavender for the control and evenly spaced Dusky Green tints from `#004F46` to `#BFD3D1` for the others, mixing up to 75% white in sRGB. Tints are categorical labels, not effect sizes, doses or significance. Similar shades must be read with the direct condition labels; the historical palette is not an accessibility certification.

In a descriptive report without an unambiguous control, issue a warning and assign colors categorically: Dusky Green first, ordinary Grayish Lavender A second, then the remaining listed colors. With nine or more conditions, retain ordinary lavender second and use the green tint sequence for the remaining conditions. No condition is labeled as a control in this case. Statistical reports keep the existing strict plate-map validation.

On count graphs one point represents one image. Fill is `#D0D0D0`; normal edge is `#111314`. Technical wells within each condition use circle, square, triangle up, triangle down, diamond, filled plus, filled X, triangle left, triangle right, hexagon, pentagon and star, followed by numeric markers 13, 14, etc. Shapes restart within each condition, ordered by the annotated well IDs, including wells with no images. The same shape in two conditions does not identify the same physical well. Original well IDs remain in the tables and JSON. No individual nucleus dots are added to intensity/morphology violins.

When `--min-nuclei` is positive, `Nuclei_count_all` displays the pre-filter counts with a red `#D62728` edge when the non-border count is strictly below the threshold. The grey fill and well shape remain. Equality is retained (for 20: 0–19 red, 20 retained). This diagnostic has no comparisons or additional statistical family. `Nuclei_count` and every intensity/morphology plot continue to use retained data. Only the count overview is duplicated; there is no fixed 14-plot set. Omitting the threshold or using 0 creates just the normal count plot. If no images survive, the diagnostic still shows why they were excluded while analytical groups retain `n=0`.

Count views share Y limits derived from all validated image counts and the existing retained-data comparisons. Their point offsets are fixed by image identity. Colors, well shapes and condition positions are assigned before filtering and reused for every marker and metric. Points are opaque and above medians. Two or more image observations receive a boxplot plus every point; one receives its point alone; zero retains the condition label. Original 1.5-IQR whiskers and nucleus density rules are preserved; no automatic outlier dots are added.

Captions distinguish the minimum nucleus area (`-p`, px², when recorded in source metadata) from the minimum non-border nuclei per image (`--min-nuclei`). Image-count filtering is not FN coverage, and a red outline does not establish poor image quality. The styling and diagnostic count overview do not alter original measurements, analytical populations, aggregation, statistical units, comparisons or Holm families. All export formats and Excel use the same styles. Report schema 5 adds the diagnostic rows and style metadata described above.

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

## Temporary resources and system diagnostics

All eight installed analysis commands, and direct execution of their script files, use a lightweight supervisor. Argument validation and `--help` run before a worker or temporary directory is created. The analysis runs in a child Python process with the same interpreter, arguments, working directory and terminal streams. A fresh temporary directory is selected before importing processing libraries. `TMPDIR`, `TEMP` and `TMP` are set only in the worker environment; `fiji_config` supplies `-Djava.io.tmpdir=<directory>` before ImageJ starts. Existing heap limits, Fiji endpoint, StarDist model, image types and numerical settings are preserved. Imported Python functions called directly by another application do not automatically start this CLI lifecycle.

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

All analyses are below each raw experiment's `fia_assay`. Prepared sources are `Nuclei/` and `markers/<method-name-id>/`. Timestamped nucleus, intensity, collection and report directories preserve earlier runs. Current stages save `plate_design.json` and the exact `plate_design.xlsx` snapshot.

| Command | Current experiment journal |
| --- | --- |
| select_channels | 1_log.log |
| generate_nuclei_mask | 2_log.log |
| generate_foci_mask | 3_foci_mask.log |
| quantify_foci | 4_foci_quantification.log |
| quantify_nuclear_intensity | 3_nuclei_intensity.log |
| fia_collect_marker_intensity_results | 4_collect_marker_intensity.log |
| fia_marker_intensity_report | 5_marker_intensity_report.log |
| fia_nuclei_report | 3_nuclei_report.log |
| quantify_field_signal | 3_field_signal.log |
| fia_field_signal_report | 4_field_signal_report.log |

Previous command journals move to `fia_assay/logs`. They record phases, parameters, image identities and dimensions, saves, elapsed time, warnings, errors and final status. The cumulative `logs/2_excluded_images.log` remains distinct from rotating journals.

General nucleus reports use `FIA_Nuclei_Report_<timestamp>`; marker collections use `FIA_Marker_Intensity_Combined_Results_<timestamp>`; nuclear marker reports use `FIA_Marker_Intensity_Report_<timestamp>`. Field signal measurements and reports use `FIA_Field_Signal_Results_*` and `FIA_Field_Signal_Report_*`. Never infer a biological marker from a channel suffix.

## Troubleshooting

- Missing Excel: place exactly one completed current-format FIA workbook beside raw images. The blank template must be annotated before use.
- Channel-count mismatch: Ignore means an existing unused channel; blank means an absent trailing channel.
- Changed assignment or source: regenerate affected preparation/measurements. Reordering panels alone allows report regeneration.
- No observations: check method/name assignment, persistent processing exclusions and the minimum-nuclei filter before interpreting empty graphs.
- Incomplete run: inspect its current stage journal and failure table; absence of a measurement is not zero signal.
- Java/StarDist initialization problems: record the environment and runtime log. Updating FIA code with --no-deps does not update imaging dependencies.

## Underlying tools

- [Fiji/ImageJ](https://fiji.sc/) provides image import, projections, mask processing and ROI measurements. [Fiji source](https://github.com/fiji/fiji); [licensing information](https://imagej.net/licensing/).
- [StarDist](https://stardist.net/) provides nuclei segmentation with the pretrained `2D_versatile_fluo` model. [StarDist source](https://github.com/stardist/stardist); [license](https://github.com/stardist/stardist/blob/main/LICENSE.txt).

FIA-tools uses its own [MIT license](../LICENSE). Project contributors and publication information are listed in the [README](../README.md#results-documentation-and-help).
