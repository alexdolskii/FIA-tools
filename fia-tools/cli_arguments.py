"""Parse analysis options before creating run storage or importing imaging libraries."""

import argparse


def parse_arguments(command, argv=None):
    descriptions = {
        'quantify_nuclear_intensity': 'Select prepared marker channels and measure original intensities in existing nucleus IDs.',
        'fia_collect_marker_intensity_results': 'Collect nuclear morphology and marker-intensity spreadsheets; no image analysis.',
        'fia_marker_intensity_report': 'Report collected nuclear morphology and marker intensity; no image processing.',
        'fia_nuclei_report': 'Report nucleus counts and morphology directly from completed nuclei runs.',
        'quantify_pfak': 'Measure whole-field pFAK SUM intensity, area and objects using existing final nucleus IDs.',
        'fia_pfak_report': 'Report pFAK image measurements and object-size distributions; no image processing.',
    }
    parser = argparse.ArgumentParser(prog=command, description=descriptions.get(command))
    parser.add_argument('-i', '--input', required=True, help='JSON with paths_to_files experiment folders')
    if command == 'select_channels':
        parser.add_argument('--nuclei-only', action='store_true',
                            help='Prepare only the selected nuclei channel; do not request or prepare marker channels')
    elif command == 'generate_nuclei_mask':
        parser.add_argument('-p', '--particle_size', type=int, default=2500)
    elif command == 'generate_foci_mask':
        parser.add_argument('-f', '--foci_threshold', type=int, default=150)
    elif command == 'quantify_foci':
        parser.add_argument('-j', '--jobs', type=int, default=4)
    elif command == 'quantify_nuclear_intensity':
        parser.add_argument('--input-type', choices=('nd2', 'tiff-stack', 'tiff-2d'),
                            help='Input format; auto-detected for ND2, prompted for TIFF/mixed inputs when omitted')
    elif command == 'quantify_pfak':
        parser.add_argument('--channel', type=int, help='1-based pFAK channel in originals; prompted once if omitted')
        parser.add_argument('-t', '--threshold', nargs='+', type=float, required=True, metavar='LIMIT',
                            help='Inclusive LOWER [UPPER] in raw SUM32 units; no default pFAK threshold; inf means no upper limit')
        parser.add_argument('--include-border-nuclei', action='store_true',
                            help='Include all visible nucleus IDs and their visible areas in both normalization denominators')
        parser.add_argument('--nuclei-runs', choices=('latest', 'all', 'ask'), default='latest',
                            help='Latest valid completed nucleus run per experiment by default; each run stays separate')
    elif command in ('fia_marker_intensity_report', 'fia_nuclei_report', 'fia_pfak_report'):
        parser.add_argument('--stats-unit', choices=('image', 'well') if command == 'fia_pfak_report' else ('nucleus', 'well'),
                            help='Omit for descriptive results without tests' +
                            ('; individual pFAK objects are never test replicates' if command == 'fia_pfak_report' else ''))
        parser.add_argument('--plot-format', choices=('pdf', 'png', 'both'), default='pdf',
                            help='Standalone plot files: pdf (default), png, or both; Excel always includes plots')
        parser.add_argument('--min-nuclei', type=int, default=0,
                            help='Minimum non-border nuclei per image (inclusive); omit or use 0 to disable filtering')
        location = 'fia_assay' if command in ('fia_nuclei_report', 'fia_pfak_report') else 'collection folder'
        parser.add_argument('--template', help=f'96-well plate-map XLSX; otherwise discover it inside each {location}')
        parser.add_argument('--sheet', help='Plate-map worksheet name; default: first sheet')
        if command == 'fia_pfak_report':
            parser.add_argument('--runs', choices=('latest', 'all', 'ask'), default='latest',
                                help='Latest valid result per threshold/channel/nucleus-run/border-policy profile; never pool profiles')
        elif command == 'fia_nuclei_report':
            parser.add_argument('--nuclei-runs', choices=('latest', 'all', 'ask'), default='latest',
                                help='Default: latest valid completed nuclei run per experiment; keep runs separate')
        else:
            parser.add_argument('--all-experiments', action='store_true',
                                help='Compatibility option: all valid manifest folders are always used')
            parser.add_argument('--collections', choices=('ask', 'latest', 'all'), default='latest',
                                help='Default: latest completed collection per experiment; ask enables manual selection')
            parser.add_argument('--markers', help='Comma-separated marker folders, all, or none; default: one batch selection (automatic for one marker)')
    args = parser.parse_args(argv)
    if command in ('fia_marker_intensity_report', 'fia_nuclei_report', 'fia_pfak_report') and args.min_nuclei < 0:
        parser.error('--min-nuclei must be a nonnegative integer')
    if command == 'quantify_pfak':
        if args.channel is not None and args.channel < 1:
            parser.error('--channel must be a positive integer')
        from pfak_measurements import threshold_settings
        try:
            args.threshold_settings = threshold_settings(args.threshold)
        except (ValueError, OverflowError) as error:
            parser.error(str(error))
    return args
