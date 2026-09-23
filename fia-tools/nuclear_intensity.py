"""Select completed nucleus runs and export original nuclear marker intensity."""

import csv
import json
import logging
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
from nuclear_intensity_imagej import METRICS, ImageJEngine
from openpyxl import Workbook
from openpyxl.cell import WriteOnlyCell

RUN_PATTERN = re.compile(r'^Final_Nuclei_Mask_(\d{8}_\d{6})$')
MASK_SUFFIX = '_nuclei_projection_StarDist_processed_processed'
INPUT_MODES = ('nd2', 'tiff-stack', 'tiff-2d')
IDENTIFIERS = [
    'Dataset', 'Dataset_path', 'Image_name', 'Source_file', 'Mask_file', 'ID_map',
    'Nuclei_run_ID', 'Intensity_run_ID', 'Particle_size_px2', 'StarDist_source',
    'Marker_channel', 'Input_type', 'Projection', 'Z_planes', 'Width_px', 'Height_px',
    'Pixel_type',
]
COUNTS = ['Nuclei_count_total', 'Border_nuclei_count', 'Non_border_nuclei_count',
          'Nuclei_measured_count', 'Failed_nuclei_count']
NUCLEI_COLUMNS = IDENTIFIERS + ['Nucleus_ID'] + list(METRICS) + [
    'Nucleus_mask', 'ROI', 'Marker_image', 'Numbered_image']
IMAGE_COLUMNS = IDENTIFIERS + ['Status', 'Error'] + COUNTS + [
    f'{metric}_{stat}' for metric in METRICS for stat in ('Mean', 'Median', 'IQR')
] + ['Marker_image', 'Numbered_image']


class Cancelled(Exception):
    """The user canceled before any measurement output was created."""


def visible_files(folder, extensions):
    return sorted((path for path in Path(folder).iterdir()
                   if not path.name.startswith('.') and path.is_file()
                   and path.suffix.lower() in extensions), key=lambda p: p.name)


def discover(input_path):
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
            continue
        assay = root / 'foci_assay'
        runs = sorted((p for p in assay.iterdir() if p.is_dir()
                       and RUN_PATTERN.fullmatch(p.name)), reverse=True) if assay.is_dir() else []
        raw = visible_files(root, {'.nd2', '.tif', '.tiff'})
        experiments.append({'root': root, 'raw': raw, 'runs': runs})
    return experiments


def parse_selection(answer, count):
    answer = answer.strip().lower()
    if answer == 'q':
        raise Cancelled()
    if answer in ('a', 'all'):
        return list(range(count))
    try:
        numbers = [int(value.strip()) for value in answer.split(',')]
    except ValueError as error:
        raise ValueError('Enter comma-separated numbers, all, or q.') from error
    if not numbers or any(number < 1 or number > count for number in numbers):
        raise ValueError(f'Choose numbers between 1 and {count}.')
    return sorted({number - 1 for number in numbers})


def choose(prompt, count):
    while True:
        try:
            return parse_selection(input(prompt), count)
        except ValueError as error:
            print(error)


def fingerprint(path):
    stat = Path(path).stat()
    return {'size_bytes': stat.st_size, 'mtime_ns': stat.st_mtime_ns}


def inspect_run(experiment, run, mode, channel, engine, cache):
    record = {'dataset': experiment['root'], 'path': run, 'pairs': [],
              'metadata': {}, 'errors': [], 'mask_count': 0}
    try:
        masks = visible_files(run, {'.tif', '.tiff'})
        record['mask_count'] = len(masks)
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
                id_map = run / 'Morphology_QC' / f'{mask.stem}_ids.tif'
                fingerprints = {str(p): fingerprint(p) for p in (source, mask, id_map)}
                engine.labels(mask, id_map, info)
                record['pairs'].append({'source': source, 'mask': mask, 'ids': id_map,
                                        'info': info, 'fingerprints': fingerprints})
            except Exception as error:  # noqa: BLE001 - isolate Java/image I/O failures
                record['errors'].append(f'{mask.name}: {error}')
    except Exception as error:  # noqa: BLE001 - isolate Java/image I/O failures
        record['errors'].append(str(error))
    record['eligible'] = bool(record['pairs']) and not record['errors']
    return record


def select_runs(records):
    eligible = [record for record in records if record['eligible']]
    if not eligible:
        raise ValueError('No completed compatible runs. See validation reasons above.')
    print('\nChoose mask runs: 1 = latest compatible per experiment; '
          '2 = all compatible; 3 = manual; q = cancel.')
    while True:
        answer = input('Mask-run selection: ').strip().lower()
        if answer == 'q':
            raise Cancelled()
        if answer == '1':
            latest = {}
            for record in eligible:
                root = record['dataset']
                if root not in latest or record['path'].name > latest[root]['path'].name:
                    latest[root] = record
            return list(latest.values())
        if answer == '2':
            return eligible
        if answer == '3':
            for index, record in enumerate(eligible, 1):
                print(f"{index}. {record['dataset']} / {record['path'].name} "
                      f"(-p {record['metadata'].get('particle_size_pixels_squared')})")
            return [eligible[index] for index in choose(
                'Select run numbers (e.g. 1,3), all, or q: ', len(eligible))]
        print('Enter 1, 2, 3, or q.')


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
    for metric in METRICS:
        values = [row[metric] for row in rows]
        summary[f'{metric}_Mean'] = float(np.mean(values)) if values else None
        summary[f'{metric}_Median'] = float(np.median(values)) if values else None
        summary[f'{metric}_IQR'] = float(np.percentile(values, 75) - np.percentile(values, 25)) if values else None
    return summary


def analyze_run(record, output, mode, channel, engine, batch_path):
    nuclei, images = [], []
    info = {
        'Schema_version': 1, 'Status': 'running', 'Started_UTC': datetime.now(timezone.utc).isoformat(),
        'Nuclei_run': str(record['path']), 'Intensity_run': str(output),
        'Particle_size_px2': record['metadata']['particle_size_pixels_squared'],
        'StarDist_source': record['metadata'].get('stardist_folder', ''),
        'Marker_channel': channel, 'Input_type': mode,
        'Projection': 'Already projected (assumed MAX)' if mode == 'tiff-2d' else 'MAX over all Z planes',
        'ImageJ_version': engine.version, 'BioFormats_version': engine.bioformats_version,
        'Batch_journal': str(batch_path),
        'Intensity_policy': 'Original values; no normalization, background subtraction or intensity threshold; zeros included',
        'Geometry': 'Native XY pixels; no resizing; Area_px2 is ROI pixel count',
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
    logger = logging.getLogger(f'nuclear_intensity.{output}')
    logger.setLevel(logging.INFO)
    logger.propagate = False
    handler = logging.FileHandler(output / 'intensity.log', encoding='utf-8')
    handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'))
    logger.addHandler(handler)
    try:
        for index, pair in enumerate(record['pairs'], 1):
            identifiers = {
                'Dataset': record['dataset'].name, 'Dataset_path': str(record['dataset']),
                'Image_name': pair['source'].name, 'Source_file': str(pair['source']),
                'Mask_file': str(pair['mask']), 'ID_map': str(pair['ids']),
                'Nuclei_run_ID': record['path'].name, 'Intensity_run_ID': output.name,
                'Particle_size_px2': info['Particle_size_px2'], 'StarDist_source': info['StarDist_source'],
                'Marker_channel': channel, 'Input_type': mode, 'Projection': info['Projection'],
                **{key: pair['info'][key] for key in ('Width_px', 'Height_px', 'Z_planes', 'Pixel_type')},
            }
            marker = None
            counts = dict.fromkeys(COUNTS)
            image_folder = output / f'image_{index:04d}'
            print(f"Measuring {pair['source'].name} with {record['path'].name}...")
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
                print(f"Failed: {pair['source'].name}: {error}")
            finally:
                if marker is not None:
                    marker.close()
            # Preserve completed image rows if a later image is interrupted.
            save_tables(output, nuclei, images, info)
        info['Status'] = 'complete' if images and all(row['Status'] == 'complete' for row in images) else 'incomplete'
        info['Finished_UTC'] = datetime.now(timezone.utc).isoformat()
        save_tables(output, nuclei, images, info)
        write_json(output / 'intensity_run.json', info)
        return info['Status']
    finally:
        logger.removeHandler(handler)
        handler.close()


def main(input_path, channel=None, mode=None):
    """Interactive planning precedes output creation; failures return a nonzero status."""
    try:
        experiments = discover(input_path)
        if not experiments:
            raise ValueError('No existing visible experiment folders were found.')
        for index, experiment in enumerate(experiments, 1):
            print(f"{index}. {experiment['root']} | original images: {len(experiment['raw'])} "
                  f"| final-mask runs: {len(experiment['runs'])}")
        selected = [experiments[index] for index in choose(
            'Select experiment numbers (e.g. 1,3), all, or q: ', len(experiments))]
        if mode is None:
            print('Input type: 1 = ND2 Z-stack; 2 = multichannel TIFF Z-stack; '
                  '3 = 2D multichannel TIFF (assumed MAX projection).')
            while mode is None:
                answer = input('Input type (1-3, q to cancel): ').strip().lower()
                if answer == 'q':
                    raise Cancelled()
                if answer in ('1', '2', '3'):
                    mode = INPUT_MODES[int(answer) - 1]
        if mode not in INPUT_MODES:
            raise ValueError('Invalid input type.')
        if channel is None:
            while channel is None:
                answer = input('Marker channel (starting from 1, q to cancel): ').strip().lower()
                if answer == 'q':
                    raise Cancelled()
                try:
                    if int(answer) > 0:
                        channel = int(answer)
                except ValueError:
                    pass
        if channel < 1:
            raise ValueError('Marker channel must be positive (1-based).')
        print('Initializing ImageJ and validating candidate mask runs...')
        engine = ImageJEngine()
        records, cache = [], {}
        for experiment in selected:
            if not experiment['runs']:
                print(f"No final-mask runs: {experiment['root']}")
            for run in experiment['runs']:
                record = inspect_run(experiment, run, mode, channel, engine, cache)
                records.append(record)
                print(f"{run} | -p {record['metadata'].get('particle_size_pixels_squared', '?')} "
                      f"| masks: {record['mask_count']} | compatible pairs: {len(record['pairs'])} "
                      f"| {'READY' if record['eligible'] else 'UNAVAILABLE'}")
                for error in record['errors']:
                    print(f'  {error}')
        runs = select_runs(records)
        print(f"\nSelected {len(runs)} mask runs, {sum(len(r['pairs']) for r in runs)} image/run pairs; "
              f"marker channel {channel}, input type {mode}. Each run gets separate results.")
        for record in runs:
            print(f"  {record['path']}")
        batch = new_output(Path(input_path).expanduser().resolve().parent, 'Nuclear_Intensity_Batch_')
        journal_path = batch / 'batch.json'
        journal = {'status': 'running', 'input_manifest': str(Path(input_path).resolve()),
                   'channel': channel, 'input_type': mode,
                   'validation': [{'run': str(r['path']), 'eligible': r['eligible'], 'errors': r['errors']}
                                  for r in records],
                   'runs': [{'source': str(r['path']), 'status': 'pending', 'output': None} for r in runs]}
        write_json(journal_path, journal)
        for record, item in zip(runs, journal['runs']):
            output = new_output(record['path'].parent, 'Nuclear_Intensity_')
            item.update(status='running', output=str(output))
            write_json(journal_path, journal)
            try:
                item['status'] = analyze_run(record, output, mode, channel, engine, journal_path)
            except Exception as error:  # noqa: BLE001 - isolate Java/image I/O failures
                item.update(status='failed', error=str(error))
                failed_path = output / 'intensity_run.json'
                try:
                    failed_info = json.loads(failed_path.read_text(encoding='utf-8')) if failed_path.exists() else {}
                    failed_info.update(Status='failed', Error=str(error))
                    write_json(failed_path, failed_info)
                except OSError:
                    pass  # The batch journal still records failures on an unwritable output volume.
                print(f'Run failed: {output}: {error}')
            write_json(journal_path, journal)
            print(f"Results: {output} ({item['status']})")
        journal['status'] = 'complete' if all(r['status'] == 'complete' for r in journal['runs']) else 'incomplete'
        write_json(journal_path, journal)
        print(f'Batch journal: {journal_path}')
        return 0 if journal['status'] == 'complete' else 1
    except (Cancelled, KeyboardInterrupt, EOFError):
        print('Canceled. Any interrupted output remains marked running; it is not a completed result.')
        return 130
    except Exception as error:  # noqa: BLE001 - isolate Java/image I/O failures
        print(f'Nuclear intensity analysis could not complete: {error}')
        return 1
