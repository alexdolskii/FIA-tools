# FIA plate scheme and routing contract

This is the current `tech_dev` input contract. Use the two distributed workbooks in `templates/`: `FIA_96_well_Template.xlsx` is blank; `FIA_96_well_Golden_Standard.xlsx` demonstrates all supported assignments and layouts. Start new analyses; previous grid-only or Order/Group workbooks and results without scheme provenance are not current inputs.

## One authoritative workbook per experiment

Place exactly one visible `FIA*.xlsx` beside the original ND2/TIFF files, not inside `fia_assay`. UMA workbooks are ignored. Every experiment in the JSON is validated before an imaging engine starts. Reports can use an explicit `--template`, which applies to every selected experiment. An invalid or ambiguous scheme is an input error, not an invitation to guess a channel.

Image filenames must contain exactly one well identifier, `WellA01` through `WellH12` (unpadded numbers also work). If a Point well token is present it must agree. Source channel numbers are one-based, refer to the original image, and never change when channels are ignored.

## Workbook coordinates

| Input | Sheet and range | Meaning |
| --- | --- | --- |
| Conditions | `Plate Map!B2:M9` | Physical rows A–H and columns 1–12 |
| Comparison blocks | Direct solid cell fill | Same RGB fill identifies the same block |
| Controls | Bold condition text | Independent of wording and horizontal position |
| Panel placement and order | `Plate Map!B15:M21` | One horizontal colored strip per block; conditions left to right |
| Channel roles | `Channels!C6:N37` | Four vertically stacked cells C1–C4 for each well |
| Requested intersections | `Channels!C44:N51` | Semicolon-separated Foci combinations per well |

The headers identify physical well columns. Merged row labels are permitted outside the input grids. Do not merge input cells, use formulas as annotations, or use conditional formatting to encode roles. Guide, Reference and dropdown examples are documentation, not additional inputs.

A block appears once in PLOT LAYOUT and lists every condition in that block exactly once. Its names, colors and bold settings match the physical plate map. Side-by-side strips need at least one empty column between them. Strips can appear on different rows: their row/column positions define the panel grid. A strip itself is horizontal; stacked blocks are separate strips, not one condition per vertical cell.

Within one comparison block, a condition label must consistently identify the same role. Statistical reports require an unambiguous bold control and treatment. Descriptive reports preserve the shared palette rules and warn when no unique control is marked. The label “Control” alone never assigns that role.

## Channel assignments

Use `Method: marker name`, with a freely chosen nonempty marker name.

| Method | What FIA prepares / measures |
| --- | --- |
| Nuclei | One channel per annotated well; existing MAX / 8-bit segmentation preparation |
| Foci | Existing SD / 8-bit preparation and Foci thresholding; nucleus-local counts, area and requested intersections |
| Nuclear intensity | Native-value MAX preparation; quantitative measurements reread the assigned original channel |
| Field signal | SUM preparation; quantitative measurements reread the original channel and use SUM32 |
| Fiber | Recorded only; no FIA fiber measurement |
| Ignore | An existing unused physical channel; recorded but not prepared or measured |
| Blank | An absent trailing channel; not an unused existing channel |

All annotated wells need exactly one Nuclei channel. Unannotated wells have no channel/intersection entries. Existing channels must be contiguous from C1: use Ignore for unused existing channels and leave only absent trailing channels blank. The number of declared channels is checked against the source image.

Each channel has one method. Multiple Foci, Nuclear intensity or Field signal markers can occupy different channels. A given method/name must not occur twice within a well. The same biological marker may be in C2 in one well and C3 in another. FIA identifies it by the exact normalized method/name, not by channel number. Names are case-sensitive. The same name used with different methods creates separate identities.

An internal portable folder ID combines the method, a readable name fragment and a hash. Full names and actual source channels are preserved in the scheme and tables. Renaming a marker changes its identity; it does not silently rename old measurements.

## Explicit intersections

Examples: `C2+C4`, `C1+C3; C1+C2+C3`. Any pair or triple of existing Foci channels is valid. Repeated terms, nonexistent/ignored channels, non-Foci methods and fewer than two distinct channels are rejected. Equivalent channel orders are canonicalized.

A triple requests only the common triple intersection. It does not request three pairs. A blank cell requests no intersections but retains individual Foci measurements. Across wells, equivalent marker combinations retain the same meaning even if physical channels change. Intersection TIFF names are short hashes; `Requested_Intersections.csv` maps them to original channels, marker IDs, image and well.

## Independent populations, parameters and statistics

- Common nucleus counts and morphology use all annotated, successfully processed wells and belong to `fia_nuclei_report`.
- Each marker method selects only wells assigned that method/name. Staining is not inferred from condition names or from files left in a marker folder.
- Thresholds belong to a method/marker analysis invocation. Use `--marker NAME` separately for different Foci or Field signal thresholds. Nuclear intensity has no intensity threshold.
- Nuclear QC values remain in marker tables. Marker reports do not duplicate the common nuclear graphs or nuclear statistical family.
- `NOT_APPLICABLE` means a marker was not assigned to that well. Its measurements are blank. A measured zero remains zero. `MISSING` means the marker was assigned but its required measurements are unavailable.
- `--min-nuclei` uses the inclusive non-border count criterion after assignment selection and persistent image exclusions.
- Reports stay separate by experiment and marker; Field signal also separates threshold/nucleus-run/border-policy profiles. Holm families are independent by marker and comparison block. Comparisons absent by design are not planned tests; planned comparisons without enough retained observations remain untested members of the family.
- A control absent from a marker's staining subset is not replaced by a control from another subset. The comparison is not applicable.

No new segmentation, watershed policy, intensity scaling, biological replicate definition or statistical test is introduced by routing through Excel.

## Provenance and reruns

Each stage records `plate_design.json` and the exact workbook bytes as `plate_design.xlsx`. Preparation also records source/output checksums and completion state. Downstream stages use manifest files, not every TIFF found in a directory.

Channel and physical-channel-count changes invalidate affected measurements. Nucleus-channel changes invalidate nucleus runs and dependent marker analyses. Marker assignment changes invalidate that method/marker. Editing condition labels, control roles, blocks or PLOT LAYOUT allows report regeneration from unchanged compatible measurements. Changing requested intersections requires rerunning Foci quantification, not resegmenting nuclei.

Runs with missing or incompatible provenance, changed prepared sources, missing masks or incomplete state are not silently treated as successful measurements. Compatible complete candidates can be selected; rejection reasons appear in journals. The persistent StarDist exclusion registry continues to apply throughout the workflow.

Keep the scheme fixed while a command runs. The command uses the validated snapshot; subsequent commands compare their current scheme with that snapshot.

## Code navigation

| Module | Responsibility |
| --- | --- |
| `plate_design.py` | Workbook parser, validation, marker identity, per-well routing, scope hashes and preparation manifests |
| `runtime_worker.py` / `cli_arguments.py` | All-experiment preflight and options without manual channel assignment |
| `1_select_channels.py` | Per-image channel preparation by method |
| `2_nuclei_mask_generation.py` | Prepared-nucleus manifest, compatible segmentation reuse and final masks |
| `3_foci_mask_generation.py` / `4_foci_quantification.py` | Per-marker Foci thresholds, exact requested intersections and provenance |
| `nuclear_intensity.py` | Per-image original-channel lookup and existing ImageJ measurements |
| `collect_marker_intensity_results.py` | Exact marker subsets, N/A values and merged QC tables |
| `marker_report_data.py` / `marker_report_statistics.py` | Assignment-aware populations, layout metadata and separate statistical families |
| `marker_report_plots.py` / `pfak_plots.py` | Panel placement, condition order and shared visual encoding |
| `nuclei_report.py` | Common counts and morphology |
| `marker_intensity_report.py` | Separate nuclear-intensity reports |
| `pfak_*.py` | Generic Field signal implementation; private module names retained to avoid unnecessary refactoring |

Public Field signal commands are `quantify_field_signal` and `fia_field_signal_report`. The earlier pFAK-specific entry points and `--channel` are removed. This route still supports pFAK-Y397; its name comes from Excel.

## Golden-standard checks

The example has 96 annotated wells. Expected assigned-well counts are: Nuclei 96; Foci Marker 1/2/3: 42/39/27; Nuclear intensity Marker 4/7: 18/3; Field signal Marker 5/8: 18/3; Fiber: 48. Nuclear channels vary across the example, including C2–C4. Row G requests triples without their pairs. Its four blocks use different condition orders in a two-by-two layout.

Automated mixed-staining tests are in `tests/test_plate_design_workflow.py`; native original-channel MAX/SUM checks are in `tests/test_plate_native_channels.py`. Tests verify routing, blank versus Ignore, stale/missing provenance, source-channel differences, N/A versus true zero, independent report populations, explicit intersections and statistical families. They do not establish biological segmentation accuracy on new microscopy data.
