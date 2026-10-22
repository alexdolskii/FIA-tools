"""Direct pFAK reporting from verified analysis tables; each profile stays separate."""

if __name__ == '__main__':
    from runtime_worker import launch_direct
    raise SystemExit(launch_direct('fia_pfak_report'))

import hashlib
import json
import logging
from pathlib import Path

import collect_marker_intensity_results as collect
import image_exclusions as exclusions
import marker_report_data as shared
import pfak_io as io
import pfak_report_data as measurements
from assay_layout import ASSAY_DIR
from cli_arguments import parse_arguments
from interactive_input import CANCELLATION_EXCEPTIONS, choose_indices
from marker_intensity_report import create_output, write_status, write_workbook
from marker_report_plots import LABEL_COLUMNS, PLOT_COLUMNS, render_plots
from marker_report_statistics import STAT_COLUMNS, calculate_statistics
from pfak_plots import render_object_areas
from table_workflow import TableJournal, TableProgress

JOURNAL = '4_pfak_report.log'
PREFIX = 'FIA_pFAK_Report_'
WORKBOOK = 'FIA_pFAK_Report.xlsx'


def as_table(rows, columns=()):
    return list(dict.fromkeys([*columns, *(key for row in rows for key in row)])), rows


def discover_runs(root, mode, audit, progress):
    assay = root / ASSAY_DIR
    candidates = sorted((p for p in assay.iterdir() if p.name.startswith(io.PREFIX)
                         and p.is_dir() and not p.is_symlink()), key=lambda p: p.name, reverse=True)
    valid = []
    progress.phase('Checking pFAK result tables', len(candidates))
    for path in candidates:
        try:
            data = io.load_run(path)
            valid.append({'root': root, 'path': path, 'profile': io.profile_key(data['metadata']),
                          'started': data['metadata']['Started_UTC']})
            audit.event(root, 'PFAK_RUN_VALIDATED | %s | status=%s | images=%s',
                        path, data['metadata']['Status'], len(data['images']))
        except (OSError, ValueError, KeyError) as error:
            audit.event(root, 'PFAK_RUN_REJECTED | %s | %s', path, error, level=logging.WARNING)
        progress.advance(path.name)
    ordered = sorted(valid, key=lambda row: (row['started'], row['path'].name), reverse=True)
    if mode != 'latest':
        return ordered
    selected, seen = [], set()
    for row in ordered:
        if row['profile'] not in seen:
            selected.append(row)
            seen.add(row['profile'])
    return selected


def report_tables(data, output):
    source_rows = []
    archive = output / 'Source_Tables'
    archive.mkdir()
    for index, (path, content) in enumerate(data['files'].items(), 1):
        target = archive / f'{index:03d}_{path.name}'
        target.write_bytes(content)
        source_rows.append({'Source': str(path), 'Archived_copy': str(target.relative_to(output)),
                            'SHA256': hashlib.sha256(content).hexdigest()})
    run_info = {**data['metadata'], 'Report_source': str(data['path']), 'Template': str(data['template']),
                'Template_sheet': data['template_sheet'], 'Statistics_unit': data['stats_unit'] or 'disabled',
                'Min_nuclei': data['min_nuclei'], 'Plot_format': data['plot_format'],
                'Statistics_policy': 'Image endpoints or equal-image-weight well means; no object-level tests. '
                'Two-sided Welch comparisons to the Excel bold control; Holm across all planned endpoints per block. '
                'RawIntDen and IntDen are proportional at constant pixel area; both are requested endpoints, not independent evidence.',
                'Object_area_policy': 'One median per image; pooled individual-object distribution is descriptive and object-weighted.',
                'Empty_object_policy': 'Object count, total area and intensity are zero. Median object area is undefined.',
                'Zero_denominator_policy': 'Normalized values are undefined, never zero.',
                'Condition_order_source': data['group_order_source']}
    return {
        'Images': as_table(data['images'], data['image_columns']),
        'Images_before_filter': as_table(data['calibration_images_before_filter'], data['image_columns']),
        'Objects': as_table(data['objects'], [*io.OBJECT_COLUMNS, 'Group']),
        'Image_Values': as_table(data['image_values'], ['Metric', 'Group', 'Image_name', 'Value']),
        'Well_Values': as_table(data['well_values'], ['Metric', 'Group', 'Well', 'Value']),
        'Condition_Summary': as_table(data['summary'], ['Metric', 'Group', 'N_images', 'Mean', 'Median', 'IQR']),
        'Statistics': (STAT_COLUMNS, data['statistics']),
        'Design': as_table(data['design']), 'Condition_Order': as_table(data['group_order']),
        'Excluded_Images': as_table(data['excluded_images'], shared.EXCLUDED_IMAGE_COLUMNS),
        'Image_Filter_Summary': as_table(data['image_filter_summary']),
        'Processing_Exclusions': as_table(data['processing_exclusion_rows'], exclusions.COLUMNS + ['Well', 'Group']),
        'Failed_Images': (io.FAILURE_COLUMNS, data['failures']),
        'Plot_Data': (PLOT_COLUMNS + ['Object_ID'], data['plot_data']),
        'Plot_Labels': (LABEL_COLUMNS, data['plot_labels']), 'Plot_Files': as_table(data['plots']),
        'Source_Files': as_table(source_rows),
        'Run_Info': (['Parameter', 'Value'], [
            {'Parameter': key, 'Value': json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else value}
            for key, value in run_info.items()]),
        'Warnings': (['Message'], [{'Message': note} for note in data['notes']]),
    }


def create_report(source, root, template=None, sheet=None, stats_unit=None, min_nuclei=0,
                  plot_format='pdf', audit=None, progress=None):
    own_audit = audit is None
    audit = audit or TableJournal(JOURNAL)
    root, source = Path(root), Path(source)
    audit.register(root)
    audit.plan(root, source)
    output = create_output(root / ASSAY_DIR, PREFIX)
    stage = 'input validation'
    try:
        with audit.stage(root, 'pFAK report', progress) as logger:
            write_status(output, 'RUNNING', Source=str(source), Report_kind='pfak')
            data = io.load_run(source)
            if data['metadata']['Status'] == 'PARTIAL':
                data['notes'].append(f'PARTIAL source: {len(data["failures"])} failed images are listed separately and excluded, not represented as zeros.')
            data['notes'].extend(data['metadata'].get('Warnings', []))
            stage = 'plate-map validation'
            plate = shared.select_template(root / ASSAY_DIR, template, sheet, location_label='fia_assay folder')
            measurements.prepare(data, plate, stats_unit, sheet, min_nuclei)
            data['notes'].extend(data['plot_style']['warnings'])
            for note in data['notes']:
                logger.warning('REPORT_NOTE | %s', note)
            stage = 'statistics'
            if progress:
                progress.phase('Calculating image/well statistics')
            calculate_statistics(data)
            stage = 'plots'
            render_plots(data, output / 'Plots', progress, plot_format)
            if progress:
                progress.phase('Object-size panels', len(data['object_area_metrics']))
            render_object_areas(data, output / 'Plots', plot_format, progress)
            io.write_json(output / 'plot_style.json', data['plot_style'])
            stage = 'table export'
            if progress:
                progress.phase('Saving and verifying report tables')
            exclusions.write_snapshot(output, data['processing_exclusions'])
            tables = report_tables(data, output)
            for title, (columns, rows) in tables.items():
                collect.write_csv(output / (title + '.csv'), columns, rows)
            write_workbook(output, tables, data['plots'], WORKBOOK, data['plot_previews'])
            data['plot_previews'].clear()
            exported = {title: collect.csv_snapshot(output / (title + '.csv'), {}, columns)
                        for title, (columns, _) in tables.items()}
            collect.verify_workbook(output / WORKBOOK, {}, exported)
            for path, content in data['files'].items():
                if path.is_symlink() or path.read_bytes() != content:
                    raise ValueError(f'Input changed during pFAK report: {path}')
            if exclusions.load_exclusions(source) != data['processing_exclusions']:
                raise ValueError('Processing exclusions changed during pFAK report; rerun the report.')
            write_status(output, 'SUCCESS', Source=str(source), Source_status=data['metadata']['Status'],
                         Report_kind='pfak', Images=len(data['images']), Objects=len(data['objects']),
                         Source_failed_images=len(data['failures']), Min_nuclei=min_nuclei,
                         Statistics_unit=stats_unit, Plot_format=plot_format,
                         Include_border_nuclei=data['metadata']['Include_border_nuclei'],
                         Threshold=data['metadata']['Threshold'], Condition_order=data['groups'])
            logger.info('REPORT_VERIFIED | images=%s | objects=%s | plots=%s | output=%s',
                        len(data['images']), len(data['objects']), len(data['plots']), output)
            audit.result(root, source, 'SUCCESS', output)
            (progress.message if progress else print)(f'SUCCESS: {output}')
            return True, output
    except CANCELLATION_EXCEPTIONS:
        write_status(output, 'CANCELLED', Source=str(source), Stage=stage, Report_kind='pfak')
        audit.result(root, source, 'CANCELLED', output)
        raise
    except Exception as error:  # noqa: BLE001 - save diagnostics and allow other reports to continue
        details = ({'Error_code': error.code, 'Required_action': error.action,
                    'Plate_map_location': error.location, 'Plate_map_details': error.details}
                   if isinstance(error, shared.PlateMapError) else {})
        diagnostic = {'Stage': stage, 'Error': str(error), **details}
        write_status(output, 'FAILED', Source=str(source), Report_kind='pfak', **diagnostic)
        collect.write_workbook(output / 'Report_Diagnostics.xlsx', {'Diagnostics': (list(diagnostic), [diagnostic])})
        audit.event(root, 'REPORT_FAILED | %s | %s', stage, error, level=logging.ERROR, exc_info=True)
        audit.result(root, source, 'FAILED', output)
        return False, output
    finally:
        if own_audit:
            audit.finish()


def main(argv=None):
    args = parse_arguments('fia_pfak_report', argv)
    audit = TableJournal(JOURNAL, args.input)
    result = 1
    try:
        skipped = []
        roots = collect.discover_sources(args.input, audit, skipped)
        selected, missing = [], []
        with TableProgress() as progress:
            for index, root in enumerate(roots, 1):
                progress.group(f'Scan {index}/{len(roots)} | {root.name}')
                with audit.stage(root, 'pFAK source discovery', progress):
                    runs = discover_runs(root, args.runs, audit, progress)
                selected.extend(runs)
                if not runs:
                    missing.append(root)
                    audit.incomplete(root, 'No valid pFAK analysis results. Run quantify_pfak first.')
        if not selected:
            raise ValueError('No valid pFAK analysis results found.')
        for index, item in enumerate(selected, 1):
            print(f'{index}. {item["path"]}')
        if args.runs == 'ask':
            selected = [selected[i] for i in choose_indices('Select pFAK runs (numbers, all, or q): ', len(selected))]
        print(f'Selected {len(selected)} separate pFAK reports. Statistics: {args.stats_unit or "disabled"}.')
        failures = len(skipped) + len(missing)
        with TableProgress() as progress:
            for index, item in enumerate(selected, 1):
                progress.group(f'Report {index}/{len(selected)} | {item["root"].name}')
                ok, _ = create_report(item['path'], item['root'], args.template, args.sheet,
                                      args.stats_unit, args.min_nuclei, args.plot_format, audit, progress)
                failures += not ok
        audit.status = 'INCOMPLETE' if failures else 'COMPLETE'
        result = 1 if failures else 0
    except CANCELLATION_EXCEPTIONS:
        audit.status = 'CANCELLED'
        audit.event(None, 'CANCELLED | pFAK report')
        result = 130
    except Exception:  # noqa: BLE001 - the CLI records unexpected failures and closes its journal
        audit.status = 'FAILED'
        audit.event(None, 'RUN_ABORTED | pFAK report', level=logging.ERROR, exc_info=True)
    finally:
        saved = audit.finish()
    return result if saved or result else 1
