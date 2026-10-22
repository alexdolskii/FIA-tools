"""Independent nuclei-only entry point using the shared FIA reporting engine."""

if __name__ == '__main__':
    from runtime_worker import launch_direct
    raise SystemExit(launch_direct('fia_nuclei_report'))

import csv
import plate_design as plate
import logging
from datetime import datetime

import collect_marker_intensity_results as collect
import marker_intensity_report as report
from assay_layout import ASSAY_DIR
from cli_arguments import parse_arguments
from table_workflow import TableJournal, TableProgress

OUTPUT_PREFIX = report.REPORT_PROFILES['nuclei']['prefix']
WORKBOOK = report.REPORT_PROFILES['nuclei']['workbook']
JOURNAL = report.REPORT_PROFILES['nuclei']['journal']


def discover_runs(root, mode, audit, progress):
    """Inspect only final-nuclei tables; unrelated channels and analyses are irrelevant."""
    assay = root / ASSAY_DIR
    if not assay.is_dir() or assay.is_symlink():
        raise collect.ValidationError(f'No regular fia_assay directory: {root}')
    candidates = sorted((path for path in assay.iterdir() if collect.NUCLEI_PATTERN.fullmatch(path.name)
                         and path.is_dir() and not path.is_symlink()), reverse=True)
    progress.phase('Finding latest valid nuclei run' if mode == 'latest' else 'Checking nuclei runs',
                   None if mode == 'latest' else len(candidates))
    selected = []
    for path in candidates:
        try:
            datetime.strptime(collect.NUCLEI_PATTERN.fullmatch(path.name)[1], '%Y%m%d_%H%M%S')
            plate.validate_snapshot(path, plate.load_design(root))
            bundle = collect.load_morphology(path, allow_empty=True)
            selected.append({'root': root, 'run': path, 'particle_size': bundle['particle_size']})
            audit.event(root, 'SOURCE_VALIDATED | %s | images=%s | non_border_nuclei=%s',
                        path, len(bundle['images']), len(bundle['nuclei']))
            progress.advance(path.name)
            if mode == 'latest':
                break
        except (OSError, ValueError, csv.Error, KeyError) as error:
            audit.event(root, 'SOURCE_REJECTED | %s | %s', path, error, level=logging.WARNING)
            progress.advance(path.name)
    return selected


def create_report(run, root, **options):
    return report.create_report(run, root, report_kind='nuclei', **options)


def main(argv=None):
    args = parse_arguments('fia_nuclei_report', argv)
    audit = TableJournal(JOURNAL, args.input)
    result = _main(args, audit)
    saved = audit.finish()
    return result if saved or result else 1


def _main(args, audit):
    skipped = []
    try:
        plate.preflight(args.input, args.template, command='fia_nuclei_report')
        roots = collect.discover_sources(args.input, audit, skipped)
        if not roots:
            raise collect.ValidationError('No existing experiment folders')
        for root in skipped:
            audit.event(None, 'EXPERIMENT_SKIPPED | missing or hidden: %s', root, level=logging.WARNING)
        print(f'Experiments from JSON: {len(roots)} (all valid paths)')
        audit.event(None, 'PARAMETERS | nuclei_runs=%s | statistics=%s | min_nuclei=%s | '
                    'template=%s | sheet=%s | plot_format=%s', args.nuclei_runs, args.stats_unit or 'disabled',
                    args.min_nuclei, args.template, args.sheet, args.plot_format)
        contexts, missing = [], []
        with TableProgress() as progress:
            for index, root in enumerate(roots, 1):
                progress.group(f'Scan {index}/{len(roots)} | {root.name}')
                with audit.stage(root, 'Nuclei source discovery', progress):
                    try:
                        bundles = discover_runs(root, args.nuclei_runs, audit, progress)
                    except (OSError, ValueError) as error:
                        audit.event(root, 'SOURCE_DISCOVERY_FAILED | %s', error, level=logging.ERROR)
                        bundles = []
                contexts.append({'root': root, 'morphology': bundles})
                if not bundles:
                    missing.append(root)
                    audit.incomplete(root, 'No completed valid nuclei morphology tables')
        selected = (collect.select_morphologies(contexts) if args.nuclei_runs == 'ask'
                    else [bundle for context in contexts for bundle in context['morphology']])
        for bundle in selected:
            audit.plan(bundle['root'], bundle['run'])
            print(f"Selected nuclei run: {bundle['run']} | -p {bundle['particle_size']:g}")
        print(f'Statistics: {args.stats_unit or "disabled (descriptive only)"}. Reports remain separate per nuclei run.')
        print(f'Plot format: {args.plot_format}. Four core morphology metrics are always plotted.')
        failures = len(skipped) + len(missing)
        with TableProgress() as progress:
            for index, bundle in enumerate(selected, 1):
                root, run = bundle['root'], bundle['run']
                progress.group(f'Report {index}/{len(selected)} | {root.name}')
                progress.message(str(run))
                try:
                    success, _ = create_report(run, root, template=args.template, stats_unit=args.stats_unit,
                                               sheet=args.sheet, manifest=args.input, min_nuclei=args.min_nuclei,
                                               audit=audit, progress=progress, plot_format=args.plot_format)
                    failures += not success
                except (OSError, ValueError) as error:
                    audit.result(root, run, 'FAILED')
                    audit.event(root, 'REPORT_FAILED | %s | %s', run, error, level=logging.ERROR)
                    progress.message(f'Cannot write nuclei report for {root}: {error}')
                    failures += 1
        audit.status = 'INCOMPLETE' if failures else 'COMPLETE'
        return 1 if failures else 0
    except (collect.Cancelled, KeyboardInterrupt, EOFError):
        audit.status = 'CANCELLED'
        audit.event(None, 'CANCELLED | nuclei report canceled by user')
        print('Nuclei report canceled. Completed reports have report_status.json with Status SUCCESS.')
        return 130
    except Exception:
        audit.status = 'FAILED'
        audit.event(None, 'RUN_ABORTED | nuclei report', level=logging.ERROR, exc_info=True)
        return 1
