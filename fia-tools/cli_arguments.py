"""Parse analysis options before creating run storage or importing imaging libraries."""

import argparse


def parse_arguments(command, argv=None):
    descriptions = {
        'quantify_nuclear_intensity': 'Read Nuclear intensity assignments from Excel, measure original intensities in existing nucleus IDs and automatically collect the current results.',
        'fia_collect_marker_intensity_results': 'Optionally rebuild collected nuclear morphology and marker-intensity spreadsheets from saved measurements; no image analysis.',
        'fia_marker_intensity_report': 'Report nuclear marker intensity on the assigned wells; no image processing.',
        'fia_nuclei_report': 'Report nucleus counts and morphology directly from completed nuclei runs.',
        'quantify_field_signal': 'Measure whole-field field signal SUM intensity, area and objects using existing final nucleus IDs.',
        'fia_field_signal_report': 'Report field signal image measurements and object-size distributions; no image processing.',
    }
    parser = argparse.ArgumentParser(prog=command, description=descriptions.get(command))
    parser.add_argument('-i', '--input', required=True, help='JSON with paths_to_files experiment folders')
    if command in ('generate_foci_mask', 'quantify_nuclear_intensity', 'quantify_field_signal'):
        parser.add_argument('--marker', action='append', help='Exact marker name or ID from Excel; repeat to select several; default: all assigned to this method')
    if command == 'select_channels':
        parser.add_argument('--nuclei-only', action='store_true',
                            help='Prepare only the nuclei channel assigned by Excel in each well')
    elif command == 'generate_nuclei_mask':
        parser.add_argument('-p', '--particle_size', type=int, default=2500)
    elif command == 'generate_foci_mask':
        parser.add_argument('-f', '--foci_threshold', type=int, default=150)
    elif command == 'quantify_foci':
        parser.add_argument('-j', '--jobs', type=int, default=4)
    elif command == 'quantify_nuclear_intensity':
        parser.add_argument('--input-type', choices=('nd2', 'tiff-stack', 'tiff-2d'),
                            help='Input format; auto-detected for ND2, prompted for TIFF/mixed inputs when omitted')
    elif command == 'quantify_field_signal':
        parser.add_argument('-t', '--threshold', nargs='+', type=float, required=True, metavar='LIMIT',
                            help='Inclusive LOWER [UPPER] in raw SUM32 units; no default field signal threshold; inf means no upper limit')
        parser.add_argument('--include-border-nuclei', action='store_true',
                            help='Include all visible nucleus IDs and their visible areas in both normalization denominators')
        parser.add_argument('--nuclei-runs', choices=('latest', 'all', 'ask'), default='latest',
                            help='Latest valid completed nucleus run per experiment by default; each run stays separate')
    elif command in ('fia_marker_intensity_report', 'fia_nuclei_report', 'fia_field_signal_report'):
        parser.add_argument('--stats-unit', choices=('image', 'well') if command == 'fia_field_signal_report' else ('nucleus', 'well'),
                            help='Omit for descriptive results without tests' +
                            ('; individual field signal objects are never test replicates' if command == 'fia_field_signal_report' else ''))
        parser.add_argument('--plot-format', choices=('pdf', 'png', 'both'), default='pdf',
                            help='Standalone plot files: pdf (default), png, or both; Excel always includes plots')
        parser.add_argument('--min-nuclei', type=int, default=0,
                            help='Minimum non-border nuclei per image (inclusive); omit or use 0 to disable filtering')
        location = 'raw-image experiment folder'
        parser.add_argument('--template', help=f'96-well plate-map XLSX; otherwise discover it inside each {location}')
        parser.add_argument('--sheet', help='Plate-map worksheet name; fixed input sheet: Plate Map')
        if command == 'fia_field_signal_report':
            parser.add_argument('--runs', choices=('latest', 'all', 'ask'), default='latest',
                                help='Latest valid result per marker/threshold/nucleus-run/border-policy profile; never pool profiles')
        elif command == 'fia_nuclei_report':
            parser.add_argument('--nuclei-runs', choices=('latest', 'all', 'ask'), default='latest',
                                help='Default: latest valid completed nuclei run per experiment; keep runs separate')
        else:
            parser.add_argument('--all-experiments', action='store_true',
                                help='Compatibility option: all valid manifest folders are always used')
            parser.add_argument('--collections', choices=('ask', 'latest', 'all'), default='latest',
                                help='Default: latest completed collection per experiment; ask enables manual selection')
            parser.add_argument('--markers', help='Comma-separated exact marker names/IDs or all; default: all assigned Nuclear intensity markers')
    args = parser.parse_args(argv)
    if command in ('fia_marker_intensity_report', 'fia_nuclei_report', 'fia_field_signal_report') and args.min_nuclei < 0:
        parser.error('--min-nuclei must be a nonnegative integer')
    if command == 'quantify_field_signal':
        from pfak_measurements import threshold_settings
        try:
            args.threshold_settings = threshold_settings(args.threshold)
        except (ValueError, OverflowError) as error:
            parser.error(str(error))
    return args
