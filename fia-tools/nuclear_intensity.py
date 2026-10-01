"""Select completed nucleus runs and export original nuclear marker intensity."""

import csv
import json
import logging
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import spatial_calibration as spatial
from nuclear_intensity_imagej import METRICS, ImageJEngine
from openpyxl import Workbook
from openpyxl.cell import WriteOnlyCell
from terminal_progress import CompactProgress
from intensity_run_log import IntensityJournal
from interactive_input import Cancelled, ask_choice, ask_integer, choose_indices as choose, parse_selection

RUN_PATTERN = re.compile(r'^Final_Nuclei_Mask_(\d{8}_\d{6})$')
MARKER_PATTERN = re.compile(r'^Foci_([1-9][0-9]*)_Channel_([1-9][0-9]*)$')
MASK_SUFFIX = '_nuclei_projection_StarDist_processed_processed'
INPUT_MODES = ('nd2', 'tiff-stack', 'tiff-2d')
IDENTIFIERS = [
    'Dataset', 'Dataset_path', 'Image_name', 'Source_file', 'Mask_file', 'ID_map',
    'Nuclei_run_ID', 'Intensity_run_ID', 'Particle_size_px2', 'StarDist_source',
    'Marker_folder', 'Marker_folder_path', 'Marker_channel',
    'Input_type', 'Projection', 'Z_planes', 'Width_px', 'Height_px',
    'Pixel_type',
] + spatial.CALIBRATION_COLUMNS
COUNTS = ['Nuclei_count_total', 'Border_nuclei_count', 'Non_border_nuclei_count',
          'Nuclei_measured_count', 'Failed_nuclei_count']
EXPORT_METRICS = [*METRICS, 'Area_um2']
NUCLEI_COLUMNS = IDENTIFIERS + ['Nucleus_ID'] + EXPORT_METRICS + [
    'Nucleus_mask', 'ROI', 'Marker_image', 'Numbered_image']
IMAGE_COLUMNS = IDENTIFIERS + ['Status', 'Error'] + COUNTS + [
    f'{metric}_{stat}' for metric in EXPORT_METRICS for stat in ('Mean', 'Median', 'IQR')
] + ['Marker_image', 'Numbered_image']


def visible_files(folder, extensions):
    return sorted((path for path in Path(folder).iterdir()
                   if not path.name.startswith('.') and path.is_file()
                   and path.suffix.lower() in extensions), key=lambda p: p.name)


def discover(input_path, audit=None, skipped=None):
    path = Path(input_path).expanduser().resolve()
    payload = json.loads(path.read_text(encoding='utf-8-sig'))
    folders = payload.get('paths_to_files') if isinstance(payload, dict) else None
    if not isinstance(folders, list) or not folders or not all(
            isinstance(folder, str) and folder.strip() for folder in folders):
        raise ValueError('Input JSON must contain a nonempty paths_to_files list of folders.')
    experiments, seen = [], set()
    for folder in folders:
        root = Path(folder).expanduser().resolve()
        if root in seen:
            continue
        seen.add(root)
        if root.name.startswith('.') or not root.is_dir():
            print(f'Skipping missing or hidden experiment folder: {root}')
            if skipped is not None:
                skipped.append({'dataset': str(root), 'reason': 'Missing or hidden experiment folder'})
            continue
        if audit:
            audit.register(root)
        assay = root / 'foci_assay'
        runs = sorted((p for p in assay.iterdir() if p.is_dir()
                       and RUN_PATTERN.fullmatch(p.name)), reverse=True) if assay.is_dir() else []
        raw = visible_files(root, {'.nd2', '.tif', '.tiff'})
        experiments.append({'root': root, 'raw': raw, 'runs': runs})
        if audit:
            audit.event(root, 'INPUTS | original_images=%s | mask_runs=%s | selected=from JSON',
                        len(raw), len(runs))
    return experiments


def discover_markers(experiments):
    """Use visible nonempty Foci folders as the channel catalog, never as pixel inputs."""
    catalog = {}
    for experiment in experiments:
        root = experiment['root']
        foci = root / 'foci_assay' / 'Foci'
        if not foci.is_dir():
            print(f'No marker-channel catalog: {foci}')
            continue
        for folder in sorted(foci.iterdir()):
            if folder.name.startswith('.') or not folder.is_dir():
                continue
            match = MARKER_PATTERN.fullmatch(folder.name)
            if match is None:
                print(f'Skipping unrecognized marker folder: {folder}')
                continue
            count = len(visible_files(folder, {'.tif', '.tiff'}))
            if count == 0:
                print(f'Skipping marker folder without visible TIFF images: {folder}')
                continue
            marker = catalog.setdefault(folder.name, {
                'name': folder.name, 'index': int(match[1]),
                'channel': int(match[2]), 'folders': {},
            })
            marker['folders'][root] = {'path': folder, 'image_count': count}
    return sorted(catalog.values(), key=lambda marker: (
        marker['index'], marker['channel'], marker['name']))


def select_markers(experiments):
    catalog = discover_markers(experiments)
    if not catalog:
        raise ValueError('No nonempty Foci_<index>_Channel_<channel> folders were found. '
                         'Prepare marker channels with stage 1 first.')
    print('\nAvailable markers (Channel_N selects channel N in the original image):')
    for index, marker in enumerate(catalog, 1):
        print(f"{index}. {marker['name']} | original channel {marker['channel']}")
        for experiment in experiments:
            root = experiment['root']
            folder = marker['folders'].get(root)
            availability = f"{folder['image_count']} TIFF images" if folder else 'unavailable or empty'
            print(f'   {root}: {availability}')
    if len(catalog) == 1:
        print(f"Marker: {catalog[0]['name']} | original channel {catalog[0]['channel']} — selected automatically")
        return catalog
    return [catalog[index] for index in choose(
        'Select marker numbers (e.g. 1,3), all, or q: ', len(catalog))]


def fingerprint(path):
    stat = Path(path).stat()
    return {'size_bytes': stat.st_size, 'mtime_ns': stat.st_mtime_ns}


def inspect_run(experiment, run, mode, marker, engine, cache, progress=None):
    channel = marker['channel']
    record = {'dataset': experiment['root'], 'path': run, 'pairs': [],
              'metadata': {}, 'errors': [], 'mask_count': 0, 'marker': marker}
    try:
        masks = visible_files(run, {'.tif', '.tiff'})
        record['mask_count'] = len(masks)
        if progress:
            progress.group(f"Validating {experiment['root'].name} | {marker['name']} | {run.name}", len(masks))
        datetime.strptime(RUN_PATTERN.fullmatch(run.name)[1], '%Y%m%d_%H%M%S').replace(tzinfo=timezone.utc)
        metadata_path = run / 'nuclei_run.json'
        record['metadata_fingerprint'] = fingerprint(metadata_path)
        metadata = json.loads(metadata_path.read_text(encoding='utf-8'))
        record['metadata'] = metadata
        if not isinstance(metadata, dict):
            record['metadata'] = {}
            raise TypeError('Invalid nuclei_run.json object.')
        if (metadata.get('schema_version') != 1 or metadata.get('status') != 'complete'
                or metadata.get('morphology_status') != 'complete'
                or metadata.get('skipped_files')):
            raise ValueError('Nuclei/morphology run is not complete. Rerun stage 2 '
                             '(existing StarDist masks may be reused).')
        area = metadata.get('particle_size_pixels_squared')
        if not isinstance(area, (int, float)) or isinstance(area, bool) or not np.isfinite(area) or area < 0:
            raise ValueError('Missing or invalid particle-size metadata.')
        processed = metadata.get('processed_files')
        if not isinstance(processed, list) or not processed or not all(
                isinstance(name, str) and Path(name).name == name
                and not name.startswith('.') for name in processed):
            raise ValueError('Missing or invalid processed_files manifest.')
        expected = {Path(name).stem + '_processed.tif' for name in processed}
        if len(expected) != len(processed) or {p.name for p in masks} != expected:
            raise ValueError('Final-mask files do not match the completed run manifest.')
        extensions = {'.nd2'} if mode == 'nd2' else {'.tif', '.tiff'}
        sources = {}
        for raw in experiment['raw']:
            if raw.suffix.lower() in extensions:
                sources.setdefault(raw.stem, []).append(raw)
        for mask in masks:
            if progress:
                progress.begin_image(mask.name)
                progress.phase("Validating source and masks")
            try:
                if not mask.stem.endswith(MASK_SUFFIX):
                    raise ValueError('Unrecognized final-mask filename suffix.')
                matches = sources.get(mask.stem.removesuffix(MASK_SUFFIX), [])
                if len(matches) != 1:
                    raise ValueError(f'Expected one original source, found {len(matches)}.')
                source = matches[0]
                source_stat = fingerprint(source)
                key = (str(source), mode, channel, source_stat['size_bytes'], source_stat['mtime_ns'])
                if key not in cache:
                    cache[key] = engine.inspect(source, mode, channel)
                info = cache[key]
                if progress and progress.logger:
                    progress.logger.info('VALIDATED_SOURCE | file=%s | channel=%s | %s', source, channel, info)
                id_map = run / 'Morphology_QC' / f'{mask.stem}_ids.tif'
                fingerprints = {str(p): fingerprint(p) for p in (source, mask, id_map)}
                engine.labels(mask, id_map, info)
                record['pairs'].append({'source': source, 'mask': mask, 'ids': id_map,
                                        'info': info, 'fingerprints': fingerprints})
                if progress:
                    progress.finish_image()
            except Exception as error:  # noqa: BLE001 - isolate Java/image I/O failures
                record['errors'].append(f'{mask.name}: {error}')
                if progress:
                    progress.finish_image(False)
    except Exception as error:  # noqa: BLE001 - isolate Java/image I/O failures
        record['errors'].append(str(error))
    record['eligible'] = bool(record['pairs']) and not record['errors']
    return record


def select_input_mode(experiments, mode=None):
    if mode is not None:
        if mode not in INPUT_MODES:
            raise ValueError('Invalid input type.')
        print(f'Input type: {mode} — supplied by --input-type')
        return mode
    raw = [path for experiment in experiments for path in experiment['raw']]
    if raw and all(Path(path).suffix.lower() == '.nd2' for path in raw):
        print('Input type: ND2 — detected automatically')
        return 'nd2'
    print('Input type: 1 = ND2 Z-stack; 2 = multichannel TIFF Z-stack; '
          '3 = 2D multichannel TIFF (assumed MAX projection).')
    return INPUT_MODES[ask_integer('Input type (1-3; q = cancel): ', 1, 3) - 1]


def choose_run_policy(records):
    eligible = [record for record in records if record['eligible']]
    if not eligible:
        raise ValueError('No completed compatible runs. See validation reasons above.')
    groups = {}
    for record in eligible:
        key = (record['dataset'], record['marker']['name'])
        groups[key] = groups.get(key, 0) + 1
    if all(count == 1 for count in groups.values()):
        print('Mask selection: one candidate per available experiment/marker — selected automatically.')
        return 'latest'
    print('\nUse masks: 1 = latest compatible per experiment and marker [Enter]; '
          '2 = all compatible; 3 = select manually; q = cancel.')
    return ask_choice('Mask-run selection (1-3; Enter = 1; q = cancel): ',
                      {'1': 'latest', '2': 'all', '3': 'manual'}, default='1')


def select_runs(records, policy=None):
    eligible = [record for record in records if record['eligible']]
    policy = policy or choose_run_policy(records)
    if policy == 'latest':
        latest = {}
        for record in eligible:
            key = (record['dataset'], record['marker']['name'])
            if key not in latest or record['path'].name > latest[key]['path'].name:
                latest[key] = record
        return list(latest.values())
    if policy == 'all':
        return eligible
    for index, record in enumerate(eligible, 1):
        print(f"{index}. {record['dataset']} / {record['marker']['name']} / {record['path'].name} "
              f"(-p {record['metadata'].get('particle_size_pixels_squared')}; compatibility not yet checked)")
    return [eligible[index] for index in choose(
        'Select run numbers (e.g. 1,3), all, or q: ', len(eligible))]


def candidate_inventory(experiments, markers, audit, skipped):
    """Read small completion manifests before asking which expensive checks to run."""
    candidates, rejected = [], []
    for experiment in experiments:
        root = experiment['root']
        for choice in markers:
            folder = choice['folders'].get(root)
            reason = None
            if folder is None:
                reason = 'Selected marker folder is missing or has no visible TIFF images'
            elif not experiment['raw']:
                reason = 'No original images found'
            elif not experiment['runs']:
                reason = 'No final-mask runs found'
            if reason:
                skipped.append({'dataset': str(root), 'marker': choice['name'],
                                'channel': choice['channel'], 'reason': reason})
                audit.incomplete(root, f"{choice['name']}: {reason}")
                continue
            marker = {'name': choice['name'], 'channel': choice['channel'], 'folder': folder['path']}
            for run in experiment['runs']:
                record = {'dataset': root, 'path': run, 'marker': marker,
                          'metadata': {}, 'errors': [], 'eligible': False}
                try:
                    record['metadata_fingerprint'] = fingerprint(run / 'nuclei_run.json')
                    metadata = json.loads((run / 'nuclei_run.json').read_text(encoding='utf-8'))
                    if (not isinstance(metadata, dict) or metadata.get('schema_version') != 1
                            or metadata.get('status') != 'complete'
                            or metadata.get('morphology_status') != 'complete'
                            or metadata.get('skipped_files')):
                        raise ValueError('Nuclei/morphology run is not complete. Rerun stage 2.')
                    record.update(metadata=metadata, eligible=True)
                    candidates.append(record)
                    audit.event(root, 'CANDIDATE | marker=%s | nuclei_run=%s | particle_size_px2=%s',
                                marker['name'], run, metadata.get('particle_size_pixels_squared'))
                except Exception as error:
                    record['errors'].append(str(error))
                    rejected.append(record)
                    audit.event(root, 'UNAVAILABLE | %s | %s', run, error, level=logging.WARNING)
            count = sum(r['dataset'] == root and r['marker']['name'] == marker['name'] for r in candidates)
            print(f"{root.name} | {marker['name']} | candidate mask runs: {count}")
            if not count:
                reason = 'No completed candidate mask runs'
                skipped.append({'dataset': str(root), 'marker': marker['name'],
                                'channel': marker['channel'], 'reason': reason})
                audit.incomplete(root, f"{marker['name']}: {reason}")
    return candidates, rejected


def validate_candidates(experiments, candidates, mode, policy, engine, audit, skipped):
    records, chosen, cache, ready = [], [], {}, set()
    lookup = {experiment['root']: experiment for experiment in experiments}
    with CompactProgress() as progress:
        engine.progress = progress
        try:
            for candidate in candidates:
                root, marker = candidate['dataset'], candidate['marker']
                key = (root, marker['name'])
                if policy == 'latest' and key in ready:
                    audit.event(root, 'NOT_CHECKED | %s | a newer compatible run was selected', candidate['path'])
                    continue
                with audit.stage(root, f"Validation | {marker['name']} | {candidate['path'].name}", progress) as logger:
                    metadata_error = None
                    try:
                        if fingerprint(candidate['path'] / 'nuclei_run.json') != candidate['metadata_fingerprint']:
                            metadata_error = 'Nucleus run metadata changed after selection.'
                    except OSError as error:
                        metadata_error = str(error)
                    if metadata_error:
                        record = {**candidate, 'eligible': False, 'pairs': [], 'mask_count': 0,
                                  'errors': [metadata_error]}
                    else:
                        record = inspect_run(lookup[root], candidate['path'], mode, marker, engine, cache, progress)
                    records.append(record)
                    summary = (f"{record['path']} | {marker['name']} | original channel {marker['channel']} "
                               f"| -p {record['metadata'].get('particle_size_pixels_squared', '?')} "
                               f"| masks: {record['mask_count']} | compatible pairs: {len(record['pairs'])} "
                               f"| {'READY' if record['eligible'] else 'UNAVAILABLE'}")
                    progress.message(summary)
                    logger.info(summary)
                    for error in record['errors']:
                        logger.warning('VALIDATION | %s', error)
                if record['eligible']:
                    chosen.append(record)
                    ready.add(key)
                elif policy == 'manual':
                    reason = f"Selected mask run is incompatible: {record['path']}"
                    skipped.append({'dataset': str(root), 'marker': marker['name'],
                                    'channel': marker['channel'], 'reason': reason})
                    audit.incomplete(root, reason)
            for root, name in {(r['dataset'], r['marker']['name']) for r in candidates} - ready:
                reason = 'No compatible mask run found'
                skipped.append({'dataset': str(root), 'marker': name, 'reason': reason})
                audit.incomplete(root, f'{name}: {reason}')
        finally:
            engine.progress = None
    return chosen, records


def new_output(parent, prefix):
    stamp = datetime.now().astimezone()
    while True:
        path = Path(parent) / (prefix + stamp.strftime('%Y%m%d_%H%M%S'))
        try:
            path.mkdir()
            return path
        except FileExistsError:
            stamp += timedelta(seconds=1)


def write_json(path, content):
    temporary = Path(str(path) + '.tmp')
    temporary.write_text(json.dumps(content, indent=2, default=str) + '\n', encoding='utf-8')
    temporary.replace(path)


def save_batch_journal(journal, progress=None):
    """Checkpoint the full batch in every created result, with a local fallback for I/O failures."""
    message = progress.message if progress else print
    paths = [Path(item['output']) / 'batch.json' for item in journal['runs'] if item['output']]
    errors = []
    for path in paths:
        try:
            write_json(path, journal)
        except OSError as error:
            errors.append({'path': str(path), 'error': str(error)})
    if errors or not paths or journal.get('fallback_journal'):
        try:
            if not journal.get('fallback_journal'):
                logs = Path.home() / '.fia-tools' / 'logs'
                logs.mkdir(parents=True, exist_ok=True)
                folder = new_output(logs, 'Nuclear_Intensity_Batch_')
                fallback = folder / 'batch.json'
                write_json(fallback, {**journal, 'fallback_journal': str(fallback), 'journal_copy_errors': errors})
                journal['fallback_journal'] = str(fallback)
                message(f'Fallback batch journal: {fallback}')
            else:
                write_json(Path(journal['fallback_journal']), {**journal, 'journal_copy_errors': errors})
        except OSError as error:
            message(f'Could not preserve the fallback batch journal: {error}')
            return False
    for error in errors:
        message(f"Could not update batch journal {error['path']}: {error['error']}")
    return bool(paths) and not errors


def finish_batch_journal(journal, status, error=None):
    journal.update(status=status, finished_utc=datetime.now(timezone.utc).isoformat())
    if error is not None:
        journal['error'] = str(error)
    saved = save_batch_journal(journal)
    if saved:
        print('Batch journal saved as batch.json in each intensity result folder.')
    return saved


def save_tables(output, nuclei, images, info):
    """Save quoted UTF-8 CSV and literal-string Excel cells, including empty tables."""
    workbook = Workbook(write_only=True)
    tables = (
        ('Nuclei', 'Nuclear_Intensity.csv', NUCLEI_COLUMNS, nuclei),
        ('Images', 'Nuclear_Intensity_Images.csv', IMAGE_COLUMNS, images),
        ('Run_Info', 'Nuclear_Intensity_Run_Info.csv', ['Parameter', 'Value'],
         [{'Parameter': key, 'Value': json.dumps(value, default=str) if isinstance(value, (dict, list))
           else value} for key, value in info.items()]),
    )
    for sheet_name, filename, columns, rows in tables:
        temporary = output / (filename + '.tmp')
        with temporary.open('w', encoding='utf-8-sig', newline='') as handle:
            writer = csv.DictWriter(handle, fieldnames=columns, extrasaction='ignore')
            writer.writeheader()
            writer.writerows(rows)
        temporary.replace(output / filename)
        sheet = workbook.create_sheet(sheet_name)
        sheet.freeze_panes = 'A2'
        sheet.append(columns)
        for row in rows:
            cells = []
            for column in columns:
                value = row.get(column)
                cell = WriteOnlyCell(sheet, value=value)
                if isinstance(value, str):
                    cell.data_type = 's'
                cells.append(cell)
            sheet.append(cells)
    temporary = output / 'Nuclear_Intensity.tmp.xlsx'
    workbook.save(temporary)
    temporary.replace(output / 'Nuclear_Intensity.xlsx')


def summarize(rows):
    summary = {}
    for metric in EXPORT_METRICS:
        values = [row[metric] for row in rows if row.get(metric) is not None]
        summary[f'{metric}_Mean'] = float(np.mean(values)) if values else None
        summary[f'{metric}_Median'] = float(np.median(values)) if values else None
        summary[f'{metric}_IQR'] = float(np.percentile(values, 75) - np.percentile(values, 25)) if values else None
    return summary


def analyze_run(record, output, mode, engine, batch_path, progress=None, audit=None):
    owns_audit = audit is None
    audit = audit or IntensityJournal()
    if owns_audit:
        audit.register(record['dataset'])
        audit.plan([record])
    status = 'failed'
    try:
        with audit.stage(record['dataset'],
                         f"Measuring | {record['marker']['name']} | {record['path'].name}", progress) as logger:
            status = _analyze_run(record, output, mode, engine, batch_path, progress, logger)
        return status
    except (Cancelled, KeyboardInterrupt, EOFError):
        status = 'interrupted'
        raise
    finally:
        if owns_audit:
            audit.result(record, status, output)
            audit.status = 'canceled' if status == 'interrupted' else status
            audit.finish()


def _analyze_run(record, output, mode, engine, batch_path, progress, logger):
    channel = record['marker']['channel']
    marker_identity = {'Marker_folder': record['marker']['name'],
                       'Marker_folder_path': str(record['marker']['folder']),
                       'Marker_channel': channel}
    nuclei, images = [], []
    info = {
        'Schema_version': 3, 'Status': 'running', 'Started_UTC': datetime.now(timezone.utc).isoformat(),
        'Nuclei_run': str(record['path']), 'Intensity_run': str(output),
        'Particle_size_px2': record['metadata']['particle_size_pixels_squared'],
        'StarDist_source': record['metadata'].get('stardist_folder', ''),
        **marker_identity, 'Input_type': mode,
        'Channel_selection': 'Channel_N parsed from the selected marker folder; no manual channel override',
        'Projection': 'Already projected (assumed MAX)' if mode == 'tiff-2d' else 'MAX over all Z planes',
        'ImageJ_version': engine.version, 'BioFormats_version': engine.bioformats_version,
        'Batch_journal': str(batch_path),
        'Intensity_policy': 'Original values; no normalization, background subtraction or intensity threshold; zeros included',
        'Geometry': 'Native XY pixels; no resizing; Area_px2 is ROI pixel count; Area_um2 uses per-image physical XY calibration',
        'Spatial_calibration_schema': 1,
        'Border_policy': 'Exclude all edge-touching IDs from measurements; count separately',
        'Summary_population': 'Equal weight per non-border nucleus, within each image and mask run',
        'Marker_StdDev': 'ImageJ sample standard deviation of pixels inside the nucleus ROI',
        'Marker_RawIntDen': 'Sum of raw marker pixel values inside the nucleus ROI',
        'Summary_statistics': 'Arithmetic mean, median, IQR across nuclei; blank for an empty population',
        'Error_policy': 'A failed image exports no nucleus rows; counts unknown before ID-map validation are blank',
        'Nucleus_ID': 'Original Morphology_QC ID; never renumbered; local to image and mask run',
        'Source_fingerprints': [pair['fingerprints'] for pair in record['pairs']],
    }
    write_json(output / 'intensity_run.json', info)
    logger.info('PARAMETERS | %s', {key: value for key, value in info.items() if key != 'Source_fingerprints'})
    if progress:
        progress.group(f"Measuring {record['dataset'].name} | {record['marker']['name']} | {record['path'].name}",
                       len(record['pairs']))
    for index, pair in enumerate(record['pairs'], 1):
        identifiers = {
            'Dataset': record['dataset'].name, 'Dataset_path': str(record['dataset']),
            'Image_name': pair['source'].name, 'Source_file': str(pair['source']),
            'Mask_file': str(pair['mask']), 'ID_map': str(pair['ids']),
            'Nuclei_run_ID': record['path'].name, 'Intensity_run_ID': output.name,
            'Particle_size_px2': info['Particle_size_px2'], 'StarDist_source': info['StarDist_source'],
            **marker_identity, 'Input_type': mode, 'Projection': info['Projection'],
            **(spatial.columns(pair['info']) if pair['info'].get('Calibration_status') else spatial.uncalibrated()),
            **{key: pair['info'][key] for key in ('Width_px', 'Height_px', 'Z_planes', 'Pixel_type')},
        }
        marker = None
        counts = dict.fromkeys(COUNTS)
        image_folder = output / f'image_{index:04d}'
        if progress:
            progress.begin_image(pair['source'].name)
            progress.phase('Checking input files')
        image_started = time.monotonic()
        logger.info('IMAGE_PARAMETERS | file=%s | %s', pair['source'], pair['info'])
        logger.info('IMAGE_STARTED | file=%s | marker=%s | nuclei_run=%s',
                    pair['source'], marker_identity['Marker_folder'], record['path'])
        try:
            if fingerprint(record['path'] / 'nuclei_run.json') != record['metadata_fingerprint']:
                raise ValueError('Nucleus run metadata changed after selection.')
            for path, previous in pair['fingerprints'].items():
                if fingerprint(path) != previous:
                    raise ValueError(f'Input changed after selection: {path}')
            labels = engine.labels(pair['mask'], pair['ids'], pair['info'])
            all_ids, border, included = engine.populations(labels)
            counts.update(Nuclei_count_total=len(all_ids), Border_nuclei_count=len(border),
                          Non_border_nuclei_count=len(included), Nuclei_measured_count=0,
                          Failed_nuclei_count=len(included))
            marker = engine.marker(pair['source'], mode, channel, pair['info'])
            rows, counts = engine.measure(marker, labels, image_folder)
            for row in rows:
                row['Area_um2'] = spatial.area_um2(row['Area_px2'], identifiers)
            # Recheck file metadata before accepting any image-level result.
            for path, previous in pair['fingerprints'].items():
                if fingerprint(path) != previous:
                    raise ValueError(f'Input changed during measurement: {path}')
            common_paths = {'Marker_image': f'{image_folder.name}/marker.tif',
                            'Numbered_image': f'{image_folder.name}/numbered_nuclei.png'}
            nuclei.extend({**identifiers, **row, **common_paths,
                           'Nucleus_mask': f"{image_folder.name}/{row['Nucleus_mask']}",
                           'ROI': f"{image_folder.name}/{row['ROI']}"} for row in rows)
            images.append({**identifiers, 'Status': 'complete', 'Error': '',
                           **counts, **summarize(rows), **common_paths})
            logger.info('Complete: %s; %s', pair['source'], counts)
        except Exception as error:
            if counts['Non_border_nuclei_count'] is not None:
                counts['Nuclei_measured_count'] = 0
                counts['Failed_nuclei_count'] = counts['Non_border_nuclei_count']
            images.append({**identifiers, 'Status': 'failed', 'Error': str(error), **counts})
            if image_folder.exists():
                write_json(image_folder / 'FAILED.json', {'error': str(error), 'measurements_accepted': False})
            logger.exception('Failed: %s', pair['source'])
            if not progress:
                print(f"Failed: {pair['source'].name}: {error}")
        finally:
            if marker is not None:
                marker.close()
        # Preserve completed image rows if a later image is interrupted.
        if progress:
            progress.phase('Saving spreadsheets')
        save_tables(output, nuclei, images, info)
        logger.info('TABLES_SAVED | folder=%s | image_status=%s | image_outputs=%s',
                    output, images[-1]['Status'], image_folder)
        logger.info('IMAGE_FINISHED | file=%s | status=%s | elapsed=%.3fs',
                    pair['source'], images[-1]['Status'], time.monotonic() - image_started)
        if progress:
            progress.finish_image(images[-1]['Status'] == 'complete')
    info['Status'] = 'complete' if images and all(row['Status'] == 'complete' for row in images) else 'incomplete'
    info['Finished_UTC'] = datetime.now(timezone.utc).isoformat()
    save_tables(output, nuclei, images, info)
    write_json(output / 'intensity_run.json', info)
    return info['Status']


def main(input_path, mode=None):
    audit = IntensityJournal(input_path)
    result = _main(input_path, mode, audit)
    saved = audit.finish()
    return result if saved or result else 1


def _main(input_path, mode, audit):
    """Plan every JSON experiment before creating measurement outputs."""
    journal = None
    engine = None
    skipped_experiments = []
    try:
        experiments = discover(input_path, audit=audit, skipped=skipped_experiments)
        if not experiments:
            raise ValueError('No existing visible experiment folders were found.')
        for item in skipped_experiments:
            audit.event(None, 'SKIPPED_EXPERIMENT | %s | %s', item['dataset'], item['reason'], level=logging.WARNING)
        print(f"Input: {len(experiments)} folders from JSON | "
              f"{sum(len(e['raw']) for e in experiments)} original images — all folders selected")
        for experiment in experiments:
            print(f"{experiment['root']} | original images: {len(experiment['raw'])} "
                  f"| final-mask runs: {len(experiment['runs'])}")
        with audit.stage(None, 'Marker selection'):
            markers = select_markers(experiments)
        for experiment in experiments:
            for marker in markers:
                audit.event(experiment['root'], 'MARKER_SELECTED | %s | original_channel=%s | available=%s',
                            marker['name'], marker['channel'], experiment['root'] in marker['folders'])
        with audit.stage(None, 'Input type selection'):
            supplied_mode = mode
            mode = select_input_mode(experiments, mode)
            audit.event(None, 'INPUT_TYPE | %s | selection=%s', mode,
                        'command line' if supplied_mode is not None else ('automatic' if mode == 'nd2' and
                        all(Path(p).suffix.lower() == '.nd2' for e in experiments for p in e['raw']) else 'prompt'))
        skipped = []
        candidates, rejected = candidate_inventory(experiments, markers, audit, skipped)
        with audit.stage(None, 'Mask selection'):
            policy = choose_run_policy(candidates)
            requested = select_runs(candidates, policy) if policy == 'manual' else candidates
            audit.event(None, 'MASK_SELECTION_POLICY | %s', policy)
            selected_keys = {(r['dataset'], r['path'], r['marker']['name']) for r in requested}
            for candidate in candidates:
                if (candidate['dataset'], candidate['path'], candidate['marker']['name']) not in selected_keys:
                    audit.event(candidate['dataset'], 'NOT_SELECTED | marker=%s | nuclei_run=%s | manual selection',
                                candidate['marker']['name'], candidate['path'])
        print('Initializing ImageJ and validating selected candidate mask runs...')
        with audit.stage(None, 'ImageJ initialization') as logger:
            engine = ImageJEngine()
            logger.info('IMAGEJ_READY | ImageJ=%s | BioFormats=%s', engine.version, engine.bioformats_version)
        runs, records = validate_candidates(experiments, requested, mode, policy, engine, audit, skipped)
        records = rejected + records
        if not runs:
            raise ValueError('No completed compatible runs. See validation reasons above.')
        print(f"\nSelected {len(runs)} experiment/marker/mask-run combinations, "
              f"{sum(len(r['pairs']) for r in runs)} image measurements; input type {mode}. "
              'Each combination gets separate results.')
        for record in runs:
            print(f"  {record['path']} | {record['marker']['name']} "
                  f"| original channel {record['marker']['channel']}")
        audit.plan(runs)
        journal = {'status': 'running', 'run_id': audit.run_id,
                   'input_manifest': str(Path(input_path).resolve()),
                   'started_utc': datetime.now(timezone.utc).isoformat(),
                   'input_type': mode, 'mask_selection_policy': policy,
                   'skipped_markers': skipped, 'skipped_experiments': skipped_experiments,
                   'selected_markers': [{'folder': marker['name'], 'channel': marker['channel']} for marker in markers],
                   'validation': [{'run': str(r['path']), 'marker': r['marker']['name'],
                                   'channel': r['marker']['channel'], 'eligible': r['eligible'], 'errors': r['errors']}
                                  for r in records],
                   'runs': [{'source': str(r['path']), 'marker': r['marker']['name'],
                             'marker_folder': str(r['marker']['folder']), 'channel': r['marker']['channel'],
                             'status': 'pending', 'output': None} for r in runs]}
        with CompactProgress(sum(len(record['pairs']) for record in runs)) as progress:
            engine.progress = progress
            for record, item in zip(runs, journal['runs']):
                output = None
                try:
                    with audit.stage(record['dataset'], 'Preparing intensity outputs', progress) as logger:
                        output = new_output(record['path'].parent, f"Nuclear_Intensity_{record['marker']['name']}_")
                        logger.info('OUTPUT_FOLDER | %s', output)
                        item.update(status='running', output=str(output))
                        journal_path = output / 'batch.json'
                        saved = save_batch_journal(journal, progress)
                        if not saved and journal.get('fallback_journal'):
                            journal_path = Path(journal['fallback_journal'])
                            logger.warning('BATCH_JOURNAL_FALLBACK | %s', journal_path)
                    item['status'] = analyze_run(record, output, mode, engine, journal_path, progress, audit)
                except (Cancelled, KeyboardInterrupt, EOFError):
                    item['status'] = 'interrupted'
                    raise
                except Exception as error:  # noqa: BLE001 - isolate Java/image I/O failures
                    progress.finish_image(False)
                    item.update(status='failed', error=str(error))
                    if output is not None:
                        failed_path = output / 'intensity_run.json'
                        try:
                            failed_info = json.loads(failed_path.read_text(encoding='utf-8')) if failed_path.exists() else {}
                            failed_info.update(Status='failed', Error=str(error))
                            write_json(failed_path, failed_info)
                        except OSError:
                            pass  # The batch journal records failures even when the output volume is unavailable.
                    audit.event(record['dataset'], 'RUN_FAILED | marker=%s | nuclei_run=%s | %s',
                                record['marker']['name'], record['path'], error, level=logging.ERROR)
                finally:
                    audit.result(record, item['status'], output)
                    save_batch_journal(journal, progress)
                if output is not None:
                    progress.message(f"Results: {output} ({item['status']})")
        engine.progress = None
        status = ('complete' if not skipped and not skipped_experiments
                  and all(r['status'] == 'complete' for r in journal['runs']) else 'incomplete')
        saved = finish_batch_journal(journal, status)
        audit.status = status
        if not saved:
            audit.event(None, 'BATCH_JOURNAL_INCOMPLETE | some result copies could not be saved', level=logging.ERROR)
            for record in runs:
                audit.folders[record['dataset']]['incomplete'] = True
        if skipped or skipped_experiments:
            print(f'Batch incomplete: {len(skipped_experiments)} experiment folder(s) and '
                  f'{len(skipped)} requested marker/mask selection(s) could not be processed. See journals.')
        return 0 if status == 'complete' and saved else 1
    except (Cancelled, KeyboardInterrupt, EOFError):
        audit.status = 'canceled'
        if journal is not None:
            finish_batch_journal(journal, 'canceled')
        audit.event(None, 'CANCELLED | analysis canceled by user')
        print('Canceled. Any interrupted output remains marked running; it is not a completed result.')
        return 130
    except Exception as error:  # noqa: BLE001 - isolate Java/image I/O failures
        audit.status = 'failed'
        if journal is not None:
            finish_batch_journal(journal, 'failed', error)
        audit.event(None, 'RUN_ABORTED | %s', error, level=logging.ERROR)
        return 1
    finally:
        if engine is not None:
            engine.progress = None
