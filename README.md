# FIA-tools

FIA-tools analyzes nuclei and independently assigned markers in multichannel confocal images. The Excel-based workflow uses **one FIA Excel scheme beside each experiment's raw images**. The scheme defines the condition, comparison block, control, per-well channel assignments, requested Foci intersections and plot layout.

**Version for these instructions:** `tech_dev`, including the Excel-based workflow introduced in [PR #4](https://github.com/alexdolskii/FIA-tools/pull/4). Install or update `tech_dev` using the commands below.

This development version extends the [original protocol](https://www.protocols.io/view/fibroblast-ecm-functional-units-a-medium-throughpu-e6nvwqyz7vmk/v1), associated with `main`. Record the exact commit (`git rev-parse HEAD`), environment and analysis parameters.

[Changes](#changes-from-the-original-version) · [Installation](#installation-and-updating) · [Inputs](#prepare-the-inputs) · [Plate-scheme skill](#plate-scheme-review-and-preparation-skill) · [Run](#run-the-analysis) · [Parameters](#command-line-parameters) · [Results](#results-and-documentation)

## Changes from the original version

- Native image dimensions and physical calibration are preserved.
- Nuclear segmentation exports counts, all 12 morphology measurements and numbered QC images. Compatible StarDist masks can be reused when changing the area filter.
- `fia_nuclei_report` provides the common nuclear analysis for **all annotated wells**, irrespective of other staining.
- Independent marker methods are **Foci**, **Nuclear intensity** and **Field signal**. A marker can use different original channels in different wells; a physical channel can contain different markers in different wells.
- Channel roles come exclusively from Excel. The CLI no longer asks which channels are nuclei or markers, or whether all channel combinations should be intersected.
- Reports use `PLOT LAYOUT` for panel positions and condition order. Statistics remain separate by experiment, method, marker, analysis profile and comparison block.
- Results are stored in `fia_assay`; prepared marker images are in `fia_assay/markers`. Shared journals retain archived previous runs. Failed-image exclusions propagate to downstream analyses.

**Start a fresh analysis with the new scheme.** Old results without scheme provenance cannot be used as current runs. There is no automatic conversion of old plate maps, folder names or channel-based marker identities. Updating code does not update imaging dependencies or change the numerical segmentation/intensity algorithms.

## Installation and updating

Install [Git](https://git-scm.com/downloads) and a Conda distribution such as [Miniforge](https://github.com/conda-forge/miniforge). The supplied environment uses Python 3.10 and OpenJDK 11. Requirements are maintained in [environment.yaml](environment.yaml) and [pyproject.toml](pyproject.toml).

macOS and Linux environment builds are configured in CI; this is not a guarantee for every machine. Native Windows and WSL installation have not been validated for this documentation. When using WSL, install and run in its Linux environment and use Linux-visible paths such as `/mnt/c/...`. See [platform and dependency details](docs/REFERENCE.md#implementation-and-dependencies).

### First installation

Run in a terminal with Conda available:

```bash
git clone --branch tech_dev --single-branch https://github.com/alexdolskii/FIA-tools.git FIA-tools-tech_dev
cd FIA-tools-tech_dev
conda env create -f environment.yaml
conda activate fia_tools
python -m pip install .
select_channels --help
```

If `fia_tools` already belongs to another installation, use a separate environment: replace the creation and activation commands above with:

```bash
conda env create -n fia_tools_tech_dev -f environment.yaml
conda activate fia_tools_tech_dev
```

Then run `python -m pip install .` in that environment. The initial package installation resolves any additional requirements. First ImageJ initialization and StarDist model loading may require internet downloads; `--help` checks command availability, not full image-processing readiness.

### Switching an existing installation to the Excel workflow

Run these commands inside your existing FIA-tools checkout. Use the environment into which you previously installed FIA:

```bash
conda activate fia_tools
git fetch origin tech_dev:refs/remotes/origin/tech_dev
git switch tech_dev
git pull --ff-only origin tech_dev
python -m pip install --no-deps .
git log -1 --oneline
quantify_field_signal --help
```

This also works after testing the PR branch or a detached commit: it switches to `tech_dev`. `--help` confirms that the installed command exists; `git log` identifies the checked-out revision. The package must be reinstalled after updating the checkout.

### Subsequent updates

In the same checkout and branch, with its environment active:

```bash
git pull --ff-only
python -m pip install --no-deps .
```

This reinstalls the updated code without changing dependencies. It assumes the environment already satisfies the current requirements; if requirements have changed, create a separate environment using the first-installation instructions. Do not repeatedly recreate the environment for ordinary analyses. In a new terminal, activate the environment you installed into. Use `git rev-parse HEAD` to record the code revision.

When updating an existing environment to the version with managed temporary files, install its one new dependency with `python -m pip install "psutil>=5.9,<8"`, then reinstall FIA using the command above. This does not require updating StarDist, TensorFlow or Fiji.

## Prepare the inputs

```json
{
  "paths_to_files": [
    "/path/to/Experiment_01",
    "/path/to/Experiment_02"
  ]
}
```

Save this as `input_paths.json`. Use absolute paths to raw-image folders and the same JSON for all commands. All valid experiment folders listed in it are used; no extra experiment selection is needed. Each image name must identify exactly one well, for example `WellA02`; a `PointA02` token, if present, must agree. Supported sources are ND2 and multichannel TIFF. Hidden/macOS sidecar files are ignored. ND2 input is detected automatically; TIFF stack/projection semantics may require a prompt. Only `quantify_nuclear_intensity` also accepts `--input-type`.

Place exactly one **`FIA*.xlsx`** in each raw-image folder before starting, for example `/path/to/Experiment_01/FIA_Plate_01.xlsx`, beside its ND2/TIFF files, **not inside `fia_assay`**. Start from the blank template and fill it for the actual plate. Do not put both the example and template in an experiment. `UMA*.xlsx` files are independent and ignored. Every selected experiment is checked before the imaging engine starts.

- [Blank template](templates/FIA_96_well_Template.xlsx)
- [Golden standard with worked examples](templates/FIA_96_well_Golden_Standard.xlsx)
- [Exact scheme, validation and provenance contract](docs/PLATE_SCHEME.md)

On **Plate Map**, annotate conditions in B2:M9. Direct solid fill defines a comparison block; bold defines its control, independent of the condition name. Draw each block as one horizontal strip in **PLOT LAYOUT**, B15:M21: arrange panels side by side, vertically or in a grid. Within a strip, conditions appear left to right. Use the same labels, fills and bold settings as above.

On **Channels**, the four cells for a well are original C1–C4. Each existing channel must contain `Method: marker name` or `Ignore`. Use exactly one `Nuclei` assignment per annotated well. A blank channel cell means an absent trailing channel; all cells are blank for an unused well. `Fiber` is recorded but excluded from FIA measurements. Marker names are freely editable after the colon.

For example, a three-channel well could contain `Nuclei: DAPI`, `Ignore`, `Field signal: pFAK-Y397`, then a blank C4. Another well in the same condition may have different staining and channel assignments. The method text before the colon selects the analysis route; the name after it is the exact marker name used by `--marker`. Do not renumber channels when ignoring one.

In **Requested intersections**, enter only the desired Foci combinations, for example `C2+C4; C2+C3+C4`. `+` means the shared mask intersection. A triple does not implicitly request its pairs. Every participating channel must be assigned `Foci` in that well.

## Plate-scheme review and preparation skill

[`check-plate-schemes`](skills/check-plate-schemes/SKILL.md) is a reusable ChatGPT/Codex skill for **96-well experiments**. The repository includes its complete instructions, reference rules, blank Excel template and read-only validator in [`skills/check-plate-schemes/`](skills/check-plate-schemes/).

It accepts Excel, screenshots, photographs, PDFs, handwritten schemes and text descriptions, using only the materials needed for the task. It supports three modes:

- **Check a completed scheme**, including one that may already be correct. Review all 96 positions, conditions, blocks, controls, C1–C4 assignments, marker methods, explicit Foci intersections and plot layout. Report errors and uncertainties separately; if no errors are found, leave the file unchanged and say so.
- **Correct or convert an existing scheme**, discussing every proposed change before applying it.
- **Create a scheme from a description**, clarifying missing information and producing one agreed `.xlsx` per experiment.

Unreadable or missing information is queried rather than guessed. An image-only review cannot verify the internal structure of an Excel workbook. Structure, software compatibility and agreement with raw images are separate checks.

**Format compatibility:** the bundled template and validator use the **Plate Map / Channels / PLOT LAYOUT** contract from [PR #4](https://github.com/alexdolskii/FIA-tools/pull/4), originally verified at commit `ff10a8d3c2c8985c05580544389bff82b53a39ca` and included in `tech_dev`. The common format is also intended for future UMA support, which must be verified in the intended UMA version. Older Order / Group templates use a different format and require an agreed conversion.

If the personal skill is installed, select it in ChatGPT/Codex and attach the scheme, for example:

> Use $check-plate-schemes. Check this completed scheme for correctness. Do not change it; list any errors and questions first.

When working from this repository, ask Codex to read `skills/check-plate-schemes/SKILL.md` and its referenced resources. Keep the entire skill directory together. `pip install .` installs FIA; it does not install this personal skill in ChatGPT.

For a local structural check of a **completed new-format workbook**, run from the repository root with `openpyxl` available (included in the FIA environment):

```bash
python skills/check-plate-schemes/scripts/validate_scheme.py /path/to/FIA_experiment.xlsx
```

Add `--require-controls` when checking a scheme intended for statistical comparisons. The validator reads without modifying the workbook, prints JSON diagnostics and exits with code 2 on validation failure. It checks the recorded schema, not the validity of the biological experiment or compatibility with an unverified installed consumer. The bundled blank template must be completed before it can pass this check.

## Run the analysis

Activate your installed environment in each new terminal with `conda activate fia_tools` (or your chosen environment name). Run the examples from the directory containing `input_paths.json`, or replace `input_paths.json` with its absolute path. The commands are installed executables; do not run the numbered Python source files directly. Use lowercase **`-i`**, not `-I`.

### 1. Prepare channels and nucleus masks once

```bash
select_channels -i input_paths.json
generate_nuclei_mask -i input_paths.json -p 200
```

The first command reads each well's assignments from Excel and prepares `Nuclei` and the supported marker channels in `fia_assay`. It no longer asks you to assign channel roles. The second command segments nuclei and creates a completed `Final_Nuclei_Mask_<timestamp>` run shared by the later routes. `-p 200` is an example minimum nucleus area in pixels squared; choose an appropriate value for your images. The default without `-p` is 2500 px².

For a **nuclei-only analysis**, replace the first command with:

```bash
select_channels -i input_paths.json --nuclei-only
```

Then run `generate_nuclei_mask` and the common report below. No marker measurement or collection command is needed for that route. For a mixed-staining workflow, use the default preparation once and then run whichever marker routes you need.

### 2. Create the common nuclei report

```bash
fia_nuclei_report -i input_paths.json --stats-unit well --min-nuclei 20
```

This report uses **all annotated, successfully processed wells**, regardless of marker staining. It contains nucleus counts and four core morphology graphs (area, aspect ratio, circularity, solidity); all 12 morphology metrics remain in tables. `--min-nuclei 20` retains images with at least 20 non-border nuclei. It filters report observations, not the saved nucleus masks.

The example requests statistics using wells as the statistical unit. Omit `--stats-unit well` for a descriptive report without hypothesis tests. Choose the statistical unit from the experimental design, not simply from the number of nuclei available. PDF is the default standalone graph format; add `--plot-format png` or `--plot-format both` if needed.

### 3A. Foci: masks, measurements and explicit intersections

Use this route only for wells assigned `Foci: ...`. Supply the exact marker name from Excel. For two markers needing different thresholds, for example:

```bash
generate_foci_mask -i input_paths.json --marker "Marker 1" -f 150
generate_foci_mask -i input_paths.json --marker "Marker 2" -f 120
quantify_foci -i input_paths.json -j 4
```

Create masks for **every Foci marker assigned in the selected experiments before running `quantify_foci`**. That command has no `--marker` option: it uses the latest compatible completed masks for all assigned Foci markers and the latest compatible completed nucleus run. If all Foci markers should share one threshold, omit `--marker` from `generate_foci_mask` instead.

`-f` is a threshold on the prepared 8-bit Foci projection (0–255). The values above are examples. `-j 4` uses four parallel workers. Requested pair/triple intersections come only from Excel; leave the intersection cell blank when none is needed. This route saves measurement tables and requested intersection masks under `fia_assay/foci_analysis`; it does not introduce a new Foci statistical plotting command. `--min-nuclei` is a report option, not an option to `quantify_foci`.

### 3B. Nuclear intensity: measure, collect, report

Use this route only for wells assigned `Nuclear intensity: ...`:

```bash
quantify_nuclear_intensity -i input_paths.json
fia_collect_marker_intensity_results -i input_paths.json
fia_marker_intensity_report -i input_paths.json --stats-unit nucleus --min-nuclei 20
```

The first command rereads the assigned **original channel** and measures MAX-projection values inside existing nucleus IDs; it has no intensity threshold. The collector combines compatible measurements and nuclear QC tables. The report then creates separate results for each marker and collection. `--stats-unit nucleus` reproduces the nucleus-level analysis; use `well` if that is the chosen statistical unit, or omit the option for descriptive results.

The default is all assigned Nuclear intensity markers. To measure only one, add `--marker "pSMAD2"` to `quantify_nuclear_intensity`; to select it in the report, use the distinct plural option `--markers "pSMAD2"`. Use the exact name in your scheme. Other assigned but unmeasured markers are missing measurements, not zeros. For a known TIFF input type, add `--input-type tiff-stack` or `--input-type tiff-2d` to the measurement command.

### 3C. Field signal: thresholded SUM measurements and report

Use this route only for wells assigned `Field signal: ...`, for example `Field signal: pFAK-Y397`:

```bash
quantify_field_signal -i input_paths.json --marker "pFAK-Y397" -t 100000 300000
fia_field_signal_report -i input_paths.json --stats-unit well --min-nuclei 20
```

`-t LOWER UPPER` is an inclusive interval in raw SUM32 units; `-t LOWER` has no upper limit. There is no default threshold. These example numbers must be replaced with values chosen for the marker and acquisition. They are not interchangeable with the 8-bit Foci threshold. Run `quantify_field_signal` separately with `--marker` for markers needing different thresholds. There is no collection command between Field signal measurement and its report.

By default, both nuclear normalization denominators exclude border nuclei. Add `--include-border-nuclei` to the measurement command to include their visible counts and areas. pFAK/other Field signal is measured over the whole field in either case. The report's `--min-nuclei` criterion still uses non-border nuclei. Different thresholds, nucleus runs and border policies remain separate analysis profiles; the report defaults to the latest completed result **per profile**, not one pooled result.

Field signal generalizes the previously pFAK-specific commands. It retains SUM32, an inclusive threshold mask, RawIntDen, IntDen, positive area, connected-object counts and sizes, and separate normalizations by nucleus count and nuclear area. No watershed or background subtraction is added. See [field-signal measurement details](docs/PFAK.md).

**Routes 3A–3C are independent:** run any subset, in any order, after common preparation. You do not need to repeat `select_channels` or nucleus segmentation for each marker. Marker routes select wells by their method/name assignments, even when those wells share a condition with other staining. General nuclear graphs and statistics belong to `fia_nuclei_report`; nuclear QC measurements remain in marker tables. Missing staining is `NOT_APPLICABLE`, never a measured zero. Report filtering applies after assignment-based selection and existing processing exclusions.

### Prompts and repeat runs

Channel roles and Foci intersections no longer need terminal selection. Prompts may still ask to confirm processing/overwriting prepared channels, distinguish TIFF stacks from existing projections, reuse compatible StarDist results, or choose among compatible result runs. The commands below document run-selection options where available; use `ask` for manual selection.

| What changed? | What to rerun |
| --- | --- |
| `--min-nuclei`, graph format, statistical unit, condition labels or `PLOT LAYOUT` | The relevant report only, using compatible existing measurements. |
| Foci threshold | `generate_foci_mask` for the affected marker, then `quantify_foci`. |
| Requested Foci intersections | `quantify_foci`; no nucleus resegmentation is needed. |
| Field signal threshold or border-nucleus normalization | `quantify_field_signal` for the affected marker, then `fia_field_signal_report`. |
| Nucleus area filter (`-p`) | `generate_nuclei_mask` (compatible StarDist masks may be reused), then affected measurements, collection where applicable, and reports. |
| Channel assignments or marker identity | Regenerate affected prepared inputs and dependent analyses; old measurements are not silently relabeled. |

## Command-line parameters

All analysis commands require `-i/--input`. `-I` is not an alias. Use `COMMAND --help` for complete syntax.

| Command | Additional parameters |
| --- | --- |
| `select_channels` | `--nuclei-only`: prepare only assigned nuclei; default: all supported assigned channels. |
| `generate_nuclei_mask` | `-p/--particle_size`: minimum nucleus area, default 2500 px². Compatible StarDist results can be selected for reuse. |
| `generate_foci_mask` | `-f/--foci_threshold`: 0–255, default 150. `--marker NAME`: optional repeatable restriction; default all Foci markers. |
| `quantify_foci` | `-j/--jobs`: parallel workers, default 4. Intersections come from Excel. |
| `quantify_nuclear_intensity` | `--marker NAME`; `--input-type nd2\|tiff-stack\|tiff-2d`. Mask-run selection remains independent of channel assignment. |
| `fia_collect_marker_intensity_results` | Nucleus-run selection when several valid runs exist; latest compatible intensity for each assigned marker. |
| `fia_nuclei_report` | `--nuclei-runs latest\|all\|ask`, default latest. Shared report options below. |
| `fia_marker_intensity_report` | `--collections latest\|all\|ask`, default latest; `--markers` comma-separated exact names/IDs or `all`, default all. |
| `quantify_field_signal` | Required `-t/--threshold LOWER [UPPER]`; optional `--marker NAME`; `--include-border-nuclei`; `--nuclei-runs latest\|all\|ask`, default latest. |
| `fia_field_signal_report` | `--runs latest\|all\|ask`, default latest per analysis profile. Shared report options below. |

Shared report options:

- `--stats-unit nucleus|well` for nuclei/nuclear intensity; `image|well` for Field signal. Omit for descriptive reports. Individual Field signal objects are never statistical replicates.
- `--min-nuclei N`: inclusive minimum non-border nucleus count per image; default 0 disables this filter.
- `--plot-format pdf|png|both`: default PDF; Excel always embeds raster previews. PDF-only runs do not create standalone PNG plot files.
- `--template PATH`: explicit current-format scheme; otherwise one `FIA*.xlsx` is discovered beside raw images. An explicit path applies to every selected experiment, so omit it for different per-experiment schemes.
- `--sheet`: only `Plate Map` is accepted by the current fixed-sheet format.

Statistics use two-sided Welch comparisons against the bold control and Holm correction within each method/marker/block. Comparisons absent by design are distinguished from planned comparisons with insufficient observations after filtering. Imaging settings and biological independence are not inferred from the scheme.

## Temporary files and diagnostics

Each managed run uses `~/.fia-tools/runs/<run-id>`. `FIA_RUNTIME_HOME` sets another base directory; runs use its runs subdirectory. Successful runs clean their temporary working data; failed/interrupted runs retain diagnostics. Use `fia_diagnostics --help` to inspect runs and remaining resources. This does not scan or delete unrelated system caches.

## Results and documentation

All generated analyses remain under each experiment's `fia_assay`. Timestamped measurement/report runs preserve earlier outputs; channel preparation can overwrite its prepared images after confirmation. `plate_design.json` and `plate_design.xlsx` record the scheme used; per-image tables record original channels separately from marker identity. Reordering plot panels allows report regeneration; changing channel assignments requires regeneration of affected measurements.

| Result | Location inside `fia_assay` |
| --- | --- |
| Common nuclei report | `FIA_Nuclei_Report_<timestamp>` |
| Foci measurements and intersections | `foci_analysis/Results_<timestamp>` |
| Collected nuclear intensity tables | `FIA_Marker_Intensity_Combined_Results_<timestamp>` |
| Nuclear intensity reports | `FIA_Marker_Intensity_Report_<timestamp>` |
| Field signal measurements | Folders beginning `FIA_Field_Signal_Results_` |
| Field signal reports | `FIA_Field_Signal_Report_<timestamp>` |

The terminal prints the output and log paths for each experiment. Check its final status and journal before treating a folder as a completed analysis.

- [Scheme and code navigation](docs/PLATE_SCHEME.md)
- [Technical reference](docs/REFERENCE.md)
- [Field signal / pFAK](docs/PFAK.md)

The repository's annotated golden standard is a routing/layout test fixture, not experimental data. The Excel file is a required input for all analysis routes.
