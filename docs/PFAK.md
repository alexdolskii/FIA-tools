# Whole-field pFAK analysis

This additional route is designed for pFAK-Y397 in fibroblast/ECM units with a nuclei-staining channel. It measures the selected original pFAK channel over the full XY field. It does not use fibronectin, segment cells, assign pFAK objects to individual cells, or measure pFAK only inside nuclei.

## Commands and inputs

1. Prepare nuclei with `select_channels -i input_paths.json --nuclei-only` and `generate_nuclei_mask -i input_paths.json`. Existing compatible final nuclei results can be reused.
2. Run `quantify_pfak -i input_paths.json --channel 3 -t LOWER [UPPER]`, replacing the placeholders with numeric thresholds. Channel numbers start at 1. If omitted, the pFAK channel is requested once with validated input and cancellation support.
3. Put one 96-well Excel layout in each experiment's `fia_assay`.
4. Run `fia_pfak_report -i input_paths.json --min-nuclei 20`. The cutoff is an example. Add `--stats-unit image` or `well` only when the selected statistical unit fits the experimental design.

All valid experiment folders in the `paths_to_files` JSON are used. Original ND2/TIFF files must match existing final nucleus masks and Morphology_QC IDs at native XY dimensions. Metadata, counts, non-border IDs, areas and available calibration are validated. Multiseries, multitimepoint and RGB-packed originals are rejected with image-level diagnostics. Single-plane images are used unchanged as a one-slice SUM; an earlier projection method cannot be inferred or reversed.

`--nuclei-runs latest` selects the latest validated complete nuclei run in each experiment. `all` processes each valid run separately; `ask` uses the existing FIA selection interface. The preparation and nuclear-intensity routes retain their existing projections and bit-depth behavior.

## One SUM image and one positive mask

The original selected-channel planes are read without autoscaling and summed by ImageJ into a float32 projection. Values are neither normalized to display range nor converted to 8-bit intensities. The projection and mask retain the original XY dimensions and spatial calibration.

The positive mask is the inclusive interval `LOWER <= SUM <= UPPER`. The lower bound is required: the software supplies no biological pFAK threshold. An omitted upper bound or `inf` uses the largest finite float32 value. Requested thresholds and their effective float32 representations are both recorded. A finite upper bound excludes pixels above it; it does not clamp their brightness.

The binary TIFF contains 255 for positive pixels and 0 otherwise. Its geometry is used for area and connected-object measurements. Intensity values are read from the corresponding pixels of the original SUM32 projection, not from the 0/255 mask. Saved arrays are reopened and compared with the measured arrays.

No automatic threshold selection, background subtraction, smoothing, resizing, hole filling, dilation, erosion, watershed or minimum object-size filter is applied. All connected positive regions, including one-pixel regions and border-touching objects, are retained. Object connectivity is 8, so diagonally touching positive pixels belong to one object. Overlapping structures at different depths may merge in the projection. These are **pFAK-positive objects on a 2D projection**, not validated individual focal adhesions.

## Measurements

Let `M` be the positive mask, `P` the original SUM32 image, and `a` the XY area of one pixel.

| Measurement | Definition |
| --- | --- |
| Positive pixels | Number of pixels in M |
| pFAK area | Positive pixels × a |
| Coverage | 100 × positive pixels / full-field pixel count |
| RawIntDen | Sum of P inside M, accumulated in float64 |
| IntDen | RawIntDen × a, equivalent to area × mean intensity |
| Object count | Number of 8-connected components in M |
| Median object area | Median area of all components in that image |

Each individual object also has an ID, area, RawIntDen, IntDen, centroid and border-touch flag. Summed object areas and intensities are reconciled with the entire positive mask. Separate object processing does not redefine the global signal or area.

For calibrated images, `a = Pixel_size_X_um × Pixel_size_Y_um`, area is in µm² and IntDen in intensity units × µm². With no reliable XY calibration, `a = 1`, area is in px² and IntDen equals RawIntDen numerically. Physical-area and pixel-area endpoints are separated in plots and tests. Both pixel and physical object areas remain in tables. RawIntDen and IntDen are proportional at constant pixel area; they are retained at the user's request and are not independent confirmations of an effect.

## Nuclear normalization and edges

Both intensities and the pFAK-positive area are exported unnormalized, divided by the selected nucleus count, and divided by the selected summed nuclear area. The area-to-area result is a ratio, not a percentage or an overlap measure, and can exceed one. There is no combined count-times-area denominator.

By default only non-border nucleus IDs are selected. `--include-border-nuclei` selects all visible IDs and their visible areas. Both denominators always use the same ID population. Existing masks are not resegmented or renumbered. The full-field pFAK mask is unchanged by this option.

The tables preserve total, border, non-border and normalization nucleus counts; total visible nuclear area; normalization nuclear area; mean selected nuclear area; and the border fraction. Including border nuclei uses their truncated visible areas. Excluding border nuclei while measuring pFAK over the whole field creates an operational field-level ratio: pFAK from cells with border/outside nuclei can remain in the numerator. It must not be interpreted as a direct measurement of the same individually identified cells.

`--min-nuclei` always uses the number of non-border nuclei, independently of normalization policy. Equality passes. It filters images consistently across pFAK endpoints and object distributions in the report, not during measurement. The diagnostic count plot retains all eligible input images and marks below-threshold images with a red edge.

Zero positive objects is a valid result: count, area and integrated intensities equal zero. Median object area is undefined. A zero selected nucleus count/area makes the corresponding normalized value undefined, even when the numerator is zero. Missing/failed images are not zero measurements.

## SUM assumptions and quality review

Comparable acquisition settings and complete coverage of the sample along Z are assumptions, not conclusions established by this software. SUM accumulates background as well as signal. The threshold excludes pixels outside the chosen interval; it does not subtract background contributions within retained pixels. Fixed thresholds must be shared across compared conditions; choosing them to maximize a group difference can bias the result.

Z-plane counts, available Z steps, source pixel type, XY sizes, projection extrema and thresholds are recorded. Different Z counts or steps produce warnings, as do missing calibration data. No division by Z count is applied. Changing the Z step, stack coverage, exposure, illumination or detector settings can change the meaning of a SUM comparison. No normalization to total FAK is possible without a separate total-FAK measurement.

Review the SUM32 image, binary mask, numbered object overlay and tabular border fraction before interpreting results. Overlay contrast is display-only. Biological segmentation accuracy and acquisition comparability require validation on representative experimental images; synthetic correctness tests do not establish either.

## Reports and statistics

Reports discover the plate layout directly in `fia_assay`, or use `--template` / `--sheet`. Existing FIA Order/Group, comparison-block and bold-control rules apply. Condition fill colors, grey image points and technical-well shapes are assigned before filtering and stay stable. Descriptive reports can omit a uniquely identified control; statistical reports use the strict shared validation.

Each main endpoint has one overview containing all comparison blocks. Mean intensity has no separate plot. The object-area overview has two panels per block: image medians with image points, and pooled object-area distributions. Pooled distributions are descriptive and weight images according to their object counts. They never supply statistical replicates. Empty conditions keep their positions and n=0. For fewer than five objects or constant values, the shared distribution renderer uses box/range/line displays instead of fabricating a violin density.

With one area-unit cohort there are 13 overview files, plus the all-images count diagnostic when `--min-nuclei` is positive. Mixing calibrated and uncalibrated inputs adds separate area-dependent overviews; those observations are never pooled in an area-dependent test. Both intensity representations and both normalizations are deliberately retained even where mathematically related.

Statistics are optional. `image` compares image endpoints; `well` compares equal-image-weight means within each well. The object-size endpoint is the per-image median, not individual objects. Tests are two-sided Welch comparisons against the designated control; Holm correction includes all planned image endpoints and treatments within each comparison block, including both requested intensity representations. Unavailable tests remain in the planned family. Images and technical wells are not automatically independent biological replicates.

PDF is the default standalone format; `--plot-format png` or `both` changes it. Excel embeds every plot, including the paired object-area plots. PDF-only reports do not create standalone PNG plot previews. Image-analysis QC PNGs are separate diagnostic outputs.

## Runs, exclusions and provenance

Analysis directories are named `fia_assay/FIA_pFAK_Results_C<channel>_threshold_<limits>_<timestamp>`. Every execution creates a new directory. It contains:

- `Images.csv`, `Objects.csv`, `Failed_Images.csv`, `Run_Info.csv` and `pFAK_Measurements.xlsx`;
- `pfak_run.json`, `run_parameters.json` and processing-exclusion snapshots;
- per-image `pFAK_SUM32.tif`, `pFAK_mask.tif`, `pFAK_objects.tif`, `nuclei_ids.tif` and `objects_QC.png` under numbered `Images` subfolders.

The current analysis journal is `fia_assay/3_pfak_analysis.log`. Prior invocations are archived in `fia_assay/logs`. Logs record stages, inputs, image parameters, saves, elapsed times, warnings and errors. Temporary resources use the existing FIA supervisor and diagnostics.

Individual failures are logged and listed separately while remaining images continue. A run with successful and failed images is `PARTIAL` and the command returns nonzero. Reports can use its verified successful measurements, explicitly preserve the failure table and warn about the partial source. RUNNING, CANCELLED and FAILED runs are not report inputs. Existing persistent FIA processing exclusions are applied before analysis and when reporting older results. An analysis-specific I/O failure does not permanently prohibit that original from unrelated workflows or a later retry.

`fia_pfak_report --runs latest` selects the latest valid run for each distinct channel, effective threshold interval, nucleus run and border-normalization policy. Different profiles are never pooled or silently replaced by another threshold. `--runs all` reports every valid run; `ask` enables manual selection. Each report is written to a new `FIA_pFAK_Report_<timestamp>` folder, with `4_pfak_report.log` as its current experiment journal.

Reports work from checksum-verified measurement tables without re-opening raw images or starting ImageJ. They preserve image/object tables, per-image/per-well values, condition summaries, planned comparisons, plate design and order, exclusions/failures, plot data/style and archived input tables. Source file/mask/ID hashes, output image hashes and table hashes are recorded. Missing Excel layouts produce explicit diagnostics with the required action.

## Code navigation

| Module | Responsibility |
| --- | --- |
| `fia-tools/pfak_analysis.py` | Batch selection, original/mask matching, per-image isolation, provenance and journals |
| `fia-tools/pfak_imagej.py` | Original-channel SUM via shared ImageJ reader; lossless image and QC exports |
| `fia-tools/pfak_measurements.py` | Inclusive mask, connected regions, both integrated intensities and nuclear normalization |
| `fia-tools/pfak_io.py` | Table schema, checksums, portable report loading and profile identity |
| `fia-tools/pfak_report_data.py` | Image endpoints, calibration populations, filtering and equal-image-weight well summaries |
| `fia-tools/pfak_plots.py` | Paired object-area overview |
| `fia-tools/pfak_report.py` | Direct report discovery, Excel/CSV/PDF export and diagnostics |
| `tests/test_pfak.py` | Numerical contracts, exclusions, normalization and report integration |

Shared modules retain the nuclei-only and marker routes: `nuclear_intensity_imagej.py` still defaults to MAX; reporting extensions are selected only by the pFAK profile. No StarDist, TensorFlow, Fiji or environment dependency upgrade is required.

Scientific motivation: [Luong and Cukierman, BMC Cancer (2022)](https://doi.org/10.1186/s12885-022-10330-y) measured threshold-selected total pFAK intensity normalized to nuclei counts. SUM projection, the explicit border option, two intensity representations and projected-object analysis are choices of this FIA implementation, not claims that the paper validated these exact extensions.
