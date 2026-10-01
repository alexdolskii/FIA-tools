"""Parse analysis options before creating run storage or importing imaging libraries."""

import argparse


def parse_arguments(command, argv=None):
    descriptions = {
        'quantify_nuclear_intensity': 'Select prepared marker channels and measure original intensities in existing nucleus IDs.',
        'fia_collect_marker_intensity_results': 'Collect nuclear morphology and marker-intensity spreadsheets; no image analysis.',
        'fia_marker_intensity_report': 'Report collected nuclear morphology and marker intensity; no image processing.',
    }
    parser = argparse.ArgumentParser(prog=command, description=descriptions.get(command))
    parser.add_argument('-i', '--input', required=True, help='JSON with paths_to_files experiment folders')
    if command == 'generate_nuclei_mask':
        parser.add_argument('-p', '--particle_size', type=int, default=2500)
    elif command == 'generate_foci_mask':
        parser.add_argument('-f', '--foci_threshold', type=int, default=150)
    elif command == 'quantify_foci':
        parser.add_argument('-j', '--jobs', type=int, default=4)
    elif command == 'quantify_nuclear_intensity':
        parser.add_argument('--input-type', choices=('nd2', 'tiff-stack', 'tiff-2d'),
                            help='Input format; auto-detected for ND2, prompted for TIFF/mixed inputs when omitted')
    elif command == 'fia_marker_intensity_report':
        parser.add_argument('--stats-unit', choices=('nucleus', 'well'), help='Omit for descriptive results without tests')
        parser.add_argument('--min-nuclei', type=int, default=0,
                            help='Minimum non-border nuclei per image (inclusive); omit or use 0 to disable filtering')
        parser.add_argument('--template', help='96-well plate-map XLSX; otherwise discover it inside each collection folder')
        parser.add_argument('--sheet', help='Plate-map worksheet name; default: first sheet')
        parser.add_argument('--all-experiments', action='store_true',
                            help='Compatibility option: all valid manifest folders are always used')
        parser.add_argument('--collections', choices=('ask', 'latest', 'all'), default='latest',
                            help='Default: latest completed collection per experiment; ask enables manual selection')
        parser.add_argument('--markers', help='Comma-separated marker folders, all, or none; default: one batch selection (automatic for one marker)')
    args = parser.parse_args(argv)
    if command == 'fia_marker_intensity_report' and args.min_nuclei < 0:
        parser.error('--min-nuclei must be a nonnegative integer')
    return args
