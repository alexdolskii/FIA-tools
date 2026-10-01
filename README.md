# FIA-tools

FIA-tools processes multichannel confocal images to measure nuclear morphology, nuclear marker intensity, and nuclear foci with optional colocalization. It uses Fiji/ImageJ and StarDist, with shared channel preparation and nuclei segmentation followed by two analysis routes.

**This README describes `tech_dev`, the development version for an updated protocol.** The published [protocol version 1](https://www.protocols.io/view/fibroblast-ecm-functional-units-a-medium-throughpu-e6nvwqyz7vmk/v1) accompanies the [`main` branch](https://github.com/alexdolskii/FIA-tools/tree/main). The updated protocol is in preparation. Record the exact Git commit, environment and analysis parameters; a branch name can change over time.

[Changes](#changes-from-the-original-version) · [Installation](#installation-and-updating) · [Inputs](#prepare-the-inputs) · [Run](#run-the-analysis) · [Parameters](#command-line-parameters) · [Results and help](#results-documentation-and-help)

## Changes from the original version

Compared with the original implementation associated with protocol version 1:

- Seven analysis commands and `fia_diagnostics` are installed together in one Conda environment.
- Native image width and height are preserved throughout preparation, masks and QC exports; the earlier 1024 × 1024 resizing is removed.
- Nuclei generation exports morphology tables and numbered QC images. Validated StarDist masks can be reused when changing the minimum nucleus area.
- Nuclear marker intensity can be measured directly from the original images within existing final nucleus IDs, independently of foci detection.
- A collector and a 96-well report combine morphology and marker-intensity tables, produce overview plots, and optionally compare conditions using nucleus- or well-based statistics.
- Available spatial calibration is retained for physical size measurements. Raw pixel measurements remain available, and calibrated and uncalibrated size populations remain separate in reports.
- Shared outputs use `fia_assay`, prepared marker channels use `fia_assay/markers`, and the preparation/nuclei/intensity/collection/report commands have current journals with archived earlier runs and compact terminal progress.

Start a fresh analysis for this directory layout. The programs do not search or migrate the former `foci_assay` and `Foci` directories. The [technical reference](docs/REFERENCE.md) describes implementation changes and detailed behavior.

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

Create `input_paths.json` with the folders containing **original images**, not result folders:

```json
{
  "paths_to_files": [
    "/path/to/Experiment_01",
    "/path/to/Experiment_02"
  ]
}
```

- Use absolute paths and the same JSON for every step. Include only the experiments intended for this batch.
- Supported inputs are `.nd2`, `.tif` and `.tiff`: multichannel Z-stacks or already projected 2D multichannel TIFFs. Keep channel order consistent across a batch; channel numbers start at **1**.
- Run the examples from the checkout containing `input_paths.json`, or give `-i` an absolute path in quotes. In Windows JSON paths, use forward slashes or escaped backslashes. WSL uses paths such as `/mnt/c/Experiments/Plate_01`.
- For the plate-based report, image names must contain recognizable well identifiers, for example `WellA02`. See [plate-map conventions](docs/REFERENCE.md#plate-map-convention).

**Before generating a report**, put one 96-well Excel layout (`.xlsx`, any filename) inside each selected `fia_assay/FIA_Marker_Intensity_Combined_Results_<timestamp>/` folder, or supply `--template`. That folder is created by the collector. The plate grid has column numbers **1–12 in B1:M1**, row letters **A–H in A2:A9**, and condition names in **B2:M9**. For statistics, direct solid fill colors define comparison blocks and exactly one condition per color is **bold** to mark the control. The report requires the layout even when statistics are disabled. An explicit `--template` applies to every selected collection; otherwise each collection supplies its own layout.

## Run the analysis

Wait for each command to finish and review its status before continuing. The first two commands are shared by both routes. Thresholds below are defaults or labeled examples; choose and record values appropriate to the experiment.

### Shared preparation

```bash
select_channels -i input_paths.json
generate_nuclei_mask -i input_paths.json
```

The first command asks for input type and nuclei/marker channels. The second uses a minimum nucleus area of **2500 px²** by default. For example, `generate_nuclei_mask -i input_paths.json -p 200` uses 200 px² instead. Repeating the second command can reuse validated StarDist masks and create a new final-mask run.

Prepared channels are 8-bit images for segmentation. **Nuclear marker intensity is measured from originals**, using MAX projection for Z-stacks; it is not measured from the prepared standard-deviation marker projections. See [projection, pixel-type and measurement rules](docs/REFERENCE.md#nuclear-marker-intensity).

### Route A: nuclear marker intensity and reports

```bash
quantify_nuclear_intensity -i input_paths.json
fia_collect_marker_intensity_results -i input_paths.json
```

These commands use all valid experiment folders in the JSON. They select markers and nucleus runs, retaining separate results for each selected experiment/run combination. Prepared marker folders such as `Foci_1_Channel_2` identify **original channel 2**; the first number is the preparation order.

After collection, add the Excel layout described above. Example report with nucleus-based tests and exclusion of images containing fewer than 20 non-border nuclei:

```bash
fia_marker_intensity_report -i input_paths.json --stats-unit nucleus --min-nuclei 20
```

The default report selects the latest completed collection per experiment. Use `--collections all` to report every completed collection, or `--collections ask` to choose. Use `--markers all` to select all available markers without the marker prompt, or `--markers none` for morphology only.

Plots are saved as **PDF only by default**. Add `--plot-format png` for PNG only or `--plot-format both` for both formats. Excel includes all plots in every mode; PDF-only runs create no standalone PNG previews.

Omit `--stats-unit` for descriptive tables and plots without tests. Choose `--stats-unit well` for tests on equal-image-weight well means. Nucleus/image tests do not account for dependence within a well, and neither mode establishes independent biological replication. Tests use Welch comparisons with Holm correction; details are in the [statistics reference](docs/REFERENCE.md#plots-and-statistical-units).

The collector and report read spreadsheets; they do not rerun image analysis. They handle nuclear morphology and marker intensity, not foci-count or colocalization tables.

### Route B: foci and colocalization

```bash
generate_foci_mask -i input_paths.json
quantify_foci -i input_paths.json
```

Mask generation asks for one prepared marker channel and uses an intensity threshold of **150** on its 8-bit images by default. Set another threshold with `-f`, for example `generate_foci_mask -i input_paths.json -f 100`. **Repeat mask generation for each required foci channel before quantification.** Quantification asks whether to include colocalization and uses the latest matching mask folders. Details: [foci analysis](docs/REFERENCE.md#foci-analysis).

### Interactive choices

| Command | Choices after launch |
| --- | --- |
| `select_channels` | Confirm processing and per-folder overwrite/skip; input type; nuclei channel; number and indices of marker channels. |
| `generate_nuclei_mask` | When reusable masks exist: choose a StarDist run, rerun segmentation, or reuse newest valid results for remaining folders. |
| `quantify_nuclear_intensity` | Markers if several exist; input type if not supplied or auto-detected as ND2; latest compatible, all compatible or manually selected mask runs. A single candidate run per experiment/marker is selected automatically. |
| `fia_collect_marker_intensity_results` | Nuclei runs when several exist; markers or morphology only (`none`). A single valid nuclei run per experiment is selected automatically; available markers are chosen at a prompt. |
| `fia_marker_intensity_report` | Markers when needed unless `--markers` is supplied; collection selection only with `--collections ask`. |
| `generate_foci_mask` | One marker-channel folder and confirmation. |
| `quantify_foci` | Start confirmation and whether to calculate colocalization. |

Invalid interactive answers repeat the question. Enter accepts a default only when advertised. Use `q`, Ctrl+C or end-of-input to cancel; invalid command-line arguments instead produce an argument error. [Selection and cancellation details](docs/REFERENCE.md#interactive-input-and-terminal-progress).

## Command-line parameters

This table covers the seven analysis commands and diagnostics. Options are case-sensitive; use `-i`, not `-I`. Each command also supports `--help`.

| Command | Option | Meaning and accepted values | Default when omitted |
| --- | --- | --- | --- |
| Analysis commands | `-i`, `--input` | Path to the JSON containing `paths_to_files`. | Required |
| All commands | `-h`, `--help` | Show command usage and exit. | — |
| `generate_nuclei_mask` | `-p`, `--particle_size` | Integer minimum nucleus area in **px²**; not a StarDist probability threshold. | `2500` |
| `generate_foci_mask` | `-f`, `--foci_threshold` | Integer lower intensity threshold on prepared **8-bit** marker images; not a foci-area threshold. | `150` |
| `quantify_foci` | `-j`, `--jobs` | Positive integer number of worker processes. | `4` |
| `quantify_nuclear_intensity` | `--input-type` | `nd2`, `tiff-stack` or `tiff-2d`; one mode applies to the batch. | Automatic for all-ND2 inputs; otherwise prompted |
| `fia_marker_intensity_report` | `--stats-unit` | `nucleus` or `well`. Count tests use images in nucleus mode and wells in well mode. | Descriptive results, no tests |
| `fia_marker_intensity_report` | `--min-nuclei` | Nonnegative integer minimum **non-border nuclei per image**, inclusive. Images below it are excluded from all report metrics. `0` disables this filter. | `0` |
| `fia_marker_intensity_report` | `--plot-format` | `pdf`, `png` or `both` for standalone plots. Excel always includes plots. | `pdf` |
| `fia_marker_intensity_report` | `--template` | Path to the plate-map `.xlsx`; the same workbook applies to every selected collection. | Discover one layout inside each collection |
| `fia_marker_intensity_report` | `--sheet` | Name of the plate-map worksheet. | First worksheet |
| `fia_marker_intensity_report` | `--collections` | `latest`, `all` or `ask`. | `latest` completed per experiment |
| `fia_marker_intensity_report` | `--markers` | Exact folder names separated by commas, `all`, or `none`. Example: `Foci_1_Channel_2,Foci_2_Channel_3`. | One batch selection; automatic for one marker |
| `fia_marker_intensity_report` | `--all-experiments` | Still accepted for compatibility; has no effect because all valid JSON experiment paths are always used. Unnecessary in new commands. | All valid JSON paths |
| `fia_diagnostics` | `-i`, `--input` | Also inspect the experiment folders and journals listed in this JSON. | Inspect managed runs, known temporary locations, caches and system resources |
| `fia_diagnostics` | `--max-entries` | Positive integer limit on directory entries inspected per location; reaching it is reported as a partial scan. | `100000` |

`select_channels` and `fia_collect_marker_intensity_results` have no additional command-line parameters. Channel choices, StarDist reuse and colocalization are interactive, as listed above.

### Temporary files and diagnostics

Every analysis command creates a temporary directory for its run under `~/.fia-tools/tmp/`. The same mechanism applies to direct execution of the analysis scripts. Temporary files used to publish results safely stay on the result disk in owned `.fia_tmp_<run_id>` directories. After a successful process exit or normal cancellation, the supervisor cleans these directories once the observed worker processes have exited. Failures, uncertain process ownership or still-running descendants retain their resources for review. Image-processing settings and bit depths are unchanged.

Inspect the computer and, optionally, the experiment folders:

```bash
fia_diagnostics
fia_diagnostics -i input_paths.json
```

Diagnostics reports temporary resources, persistent caches, active managed processes, available RAM, swap and disk space. It deletes no files and stops no processes. The current journal is `~/.fia-tools/fia_diagnostics.log`; with `-i`, a copy is written to each existing `fia_assay/fia_diagnostics.log`. Previous journals move to the adjacent `logs` folder. Runtime records and cleanup outcomes are under `~/.fia-tools/runs/<run_id>/`.

Set `FIA_RUNTIME_HOME` to an absolute writable directory to change the common runtime location; use the same setting for analysis and diagnostics. Permanent Fiji/StarDist caches keep their existing locations. See [resource ownership, cleanup and platform limitations](docs/REFERENCE.md#temporary-resources-and-system-diagnostics).

## Results, documentation and help

Paths below are relative to each original experiment folder. Results from different experiments remain separate.

| Location | Main contents |
| --- | --- |
| `fia_assay/Nuclei/`, `fia_assay/markers/` | Prepared nuclei and marker channels |
| `fia_assay/Final_Nuclei_Mask_<timestamp>/` | Final masks, morphology Excel/CSV tables and numbered QC images |
| `fia_assay/Nuclear_Intensity_<marker>_<timestamp>/` | Nuclear-intensity tables, marker images, ROIs and QC |
| `fia_assay/FIA_Marker_Intensity_Combined_Results_<timestamp>/` | Collected and combined spreadsheets; place the Excel layout here |
| `fia_assay/FIA_Marker_Intensity_Report_<timestamp>/` | Report workbooks/CSV, overview plots (PDF by default; optional PNG or both) and completion metadata |
| `fia_assay/Foci_Masks/` | Foci masks by channel and run |
| `foci_analysis/Results_<timestamp>/` | Foci/colocalization results, including `all_results_with_coloc_universal.csv` |

The five preparation/nuclei/intensity/collection/report journals are `1_log.log`, `2_log.log`, `3_nuclei_intensity.log`, `4_collect_marker_intensity.log` and `5_marker_intensity_report.log` in `fia_assay`; earlier journals are archived in `fia_assay/logs/`. Foci-mask and foci-quantification logs have separate locations described in the [full output and journal reference](docs/REFERENCE.md#output-files-and-journals). Review completion status before using results; report success is recorded in `report_status.json`.

- **Detailed software behavior:** [docs/REFERENCE.md](docs/REFERENCE.md) covers methods, formulas, calibration, tables, statistics, plots, repeat runs, dependencies and troubleshooting.
- **Scientific protocol:** [published version 1](https://www.protocols.io/view/fibroblast-ecm-functional-units-a-medium-throughpu-e6nvwqyz7vmk/v1), for `main`. An updated protocol link will be added when published and associated with a specific code revision.
- **Problems:** check [troubleshooting](docs/REFERENCE.md#troubleshooting), then open a [GitHub issue](https://github.com/alexdolskii/FIA-tools/issues) with the command, Git commit, operating system and relevant log entries.
- **License:** [MIT](LICENSE). Fiji and StarDist licensing is linked in the [tool acknowledgments](docs/REFERENCE.md#underlying-tools).

FIA-tools complements [UMA-tools](https://github.com/alexdolskii/UMA-tools) and was developed in the [Edna (Eti) Cukierman laboratory](https://www.foxchase.org/edna-cukierman) as part of [Pulsed low-dose-rate radiation reduces the tumor-promotion induced by conventional chemoradiation in pancreatic cancer-associated fibroblasts](https://pubmed.ncbi.nlm.nih.gov/41884584/). For scientific use, cite the applicable protocol and study and record the code revision used.

Contributors: [Aleksandr Dolskii](mailto:aleksandr.dolskii@fccc.edu) and [Ekaterina Shitik](mailto:shitik.ekaterina@gmail.com).
