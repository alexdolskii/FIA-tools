# FIA-tools

FIA-tools analyzes nuclei and independently assigned markers in multichannel confocal images. The `tech_dev` workflow uses **one FIA Excel scheme beside each experiment's raw images**. The scheme defines the condition, comparison block, control, per-well channel assignments, requested Foci intersections and plot layout.

This development version extends the [original protocol](https://www.protocols.io/view/fibroblast-ecm-functional-units-a-medium-throughpu-e6nvwqyz7vmk/v1), associated with `main`. Record the exact commit (`git rev-parse HEAD`), environment and analysis parameters.

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

### Updating an existing installation

In your `tech_dev` checkout, with its environment active:

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

Use absolute paths to raw-image folders and the same JSON for all commands. Each image name must identify exactly one well, for example `WellA02`; a `PointA02` token, if present, must agree. Supported sources are ND2 and multichannel TIFF. Hidden/macOS sidecar files are ignored. ND2 input is detected automatically; ambiguous TIFF projection semantics may require a prompt or `--input-type`.

Place exactly one **`FIA*.xlsx`** in each raw-image folder before starting. Do not put both the example and template in an experiment. `UMA*.xlsx` files are independent and ignored. Every selected experiment is checked before the imaging engine starts.

- [Blank template](templates/FIA_96_well_Template.xlsx)
- [Golden standard with worked examples](templates/FIA_96_well_Golden_Standard.xlsx)
- [Exact scheme, validation and provenance contract](docs/PLATE_SCHEME.md)

On **Plate Map**, annotate conditions in B2:M9. Direct solid fill defines a comparison block; bold defines its control, independent of the condition name. Draw each block as one horizontal strip in **PLOT LAYOUT**, B15:M21: arrange panels side by side, vertically or in a grid. Within a strip, conditions appear left to right. Use the same labels, fills and bold settings as above.

On **Channels**, the four cells for a well are original C1–C4. Each existing channel must contain `Method: marker name` or `Ignore`. Use exactly one `Nuclei` assignment per annotated well. A blank channel cell means an absent trailing channel; all cells are blank for an unused well. `Fiber` is recorded but excluded from FIA measurements. Marker names are freely editable after the colon.

In **Requested intersections**, enter only the desired Foci combinations, for example `C2+C4; C2+C3+C4`. `+` means the shared mask intersection. A triple does not implicitly request its pairs. Every participating channel must be assigned `Foci` in that well.

## Run the analysis

Prepare the assigned channels and nuclei once:

```bash
select_channels -i input_paths.json
generate_nuclei_mask -i input_paths.json -p 200
fia_nuclei_report -i input_paths.json --stats-unit well --min-nuclei 20
```

`-p 200` is an example minimum nucleus area in pixels squared, not a universal recommendation. The common report contains nucleus counts and four core morphology graphs (area, aspect ratio, circularity, solidity); all 12 morphology metrics remain in tables. For only this route, `select_channels --nuclei-only` prepares only the Excel-assigned nuclei channels.

Run any applicable marker route independently:

```bash
# Foci: an 8-bit threshold on the existing prepared projection.
generate_foci_mask -i input_paths.json --marker "Marker 1" -f 150
# Repeat masking with appropriate thresholds for other Foci markers.
quantify_foci -i input_paths.json -j 4

# Nuclear intensity: original MAX values, no intensity threshold.
quantify_nuclear_intensity -i input_paths.json
fia_collect_marker_intensity_results -i input_paths.json
fia_marker_intensity_report -i input_paths.json --stats-unit nucleus --min-nuclei 20

# Field signal: one raw SUM32 threshold interval, e.g. for pFAK-Y397.
quantify_field_signal -i input_paths.json --marker "pFAK-Y397" -t 100000 300000
fia_field_signal_report -i input_paths.json --stats-unit well --min-nuclei 20
```

Use marker names actually entered in your scheme. Threshold values above are examples. Foci and Field signal thresholds have different units and are not interchangeable. `--marker` restricts a method to a named marker; it never changes its source channels. Without it, the method processes all its assigned markers using that invocation's settings. To use different thresholds within a method, run it separately for each marker. Nuclear intensity has no threshold parameter.

Field signal generalizes the previously pFAK-specific commands. It retains SUM32, an inclusive threshold mask, RawIntDen, IntDen, positive area, connected-object counts and sizes, and separate normalizations by nucleus count and nuclear area. No watershed or background subtraction is added. See [field-signal measurement details](docs/PFAK.md).

The marker-intensity report creates a separate report per marker and collection. The field-signal report keeps each marker/threshold/nucleus-run/border-policy profile separate. General nuclear graphs and statistics belong to `fia_nuclei_report`; nuclear QC measurements remain in marker tables. Missing staining is `NOT_APPLICABLE`, never a measured zero. `--min-nuclei` applies after assignment-based selection and existing processing exclusions.

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

All generated analyses remain under each experiment's `fia_assay`. Each new run preserves earlier outputs. `plate_design.json` and `plate_design.xlsx` record the scheme used; per-image tables record original channels separately from marker identity. Reordering plot panels allows report regeneration; changing channel assignments requires regeneration of affected measurements.

- [Scheme and code navigation](docs/PLATE_SCHEME.md)
- [Technical reference](docs/REFERENCE.md)
- [Field signal / pFAK](docs/PFAK.md)

The repository's annotated golden standard is a routing/layout test fixture, not experimental data. The Excel file is a required input for all analysis routes.
