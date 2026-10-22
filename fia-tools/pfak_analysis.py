"""Measure whole-field pFAK-Y397 from originals and existing final nucleus IDs."""

if __name__ == '__main__':
    from runtime_worker import launch_direct
    raise SystemExit(launch_direct('quantify_pfak'))

import csv
import json
import logging
import time

import collect_marker_intensity_results as collect
import image_exclusions as exclusions
import pfak_io as io
import spatial_calibration as spatial
from assay_layout import ASSAY_DIR
from cli_arguments import parse_arguments
from interactive_input import CANCELLATION_EXCEPTIONS, ask_integer
from marker_intensity_report import create_output
from marker_report_data import WELL_PATTERN
from pfak_measurements import measure_projection, nuclear_population
from run_resources import runtime_context, utc_now
from table_workflow import TableJournal, TableProgress

JOURNAL = '3_pfak_analysis.log'


def discover_nuclei(root, mode, audit):
    assay = root / ASSAY_DIR
    candidates = sorted((p for p in assay.iterdir() if p.is_dir() and not p.is_symlink()
                         and collect.NUCLEI_PATTERN.fullmatch(p.name)), reverse=True)
    bundles = []
    for path in candidates:
        try:
            bundle = collect.load_morphology(path, allow_empty=True)
            bundle['root'] = root
            bundles.append(bundle)
            audit.event(root, 'NUCLEI_VALIDATED | %s | images=%s', path, len(bundle['images']))
            if mode == 'latest':
                break
        except (OSError, ValueError, KeyError, csv.Error) as error:
            audit.event(root, 'NUCLEI_REJECTED | %s | %s', path, error, level=logging.WARNING)
    return bundles


def image_tasks(root, bundle):
    sources = {}
    for path in sorted(root.iterdir()):
        if not path.name.startswith('.') and path.is_file() and path.suffix.lower() in ('.nd2', '.tif', '.tiff'):
            sources.setdefault(path.stem, []).append(path)
    masks = {exclusions.image_key(name): name for name in bundle['images']}
    if len(masks) != len(bundle['images']):
        raise ValueError('Ambiguous source identity in nuclei tables.')
    tasks = []
    for key in sorted(set(sources) | set(masks)):
        if key in bundle['processing_exclusions']:
            continue
        matches = sources.get(key, [])
        mask_name = masks.get(key, '')
        error = ''
        if len(matches) != 1:
            error = f'Expected one original ND2/TIFF for {key}; found {len(matches)}.'
        elif not mask_name:
            error = f'No validated final nucleus mask for {matches[0].name}; rerun nuclei preparation/segmentation.'
        tasks.append({'source': matches[0] if len(matches) == 1 else None,
                      'Image_name': matches[0].name if len(matches) == 1 else key,
                      'Mask_name': mask_name, 'error': error})
    return tasks


def validate_nuclei(ids, image, bundle, info):
    counts, areas, border = nuclear_population(ids)
    for field in collect.COUNTS:
        if counts[field] != int(image[field]):
            raise ValueError(f'Nucleus ID map disagrees with morphology: {field}')
    expected = {key[1]: row for key, row in bundle['nuclei'].items() if key[0] == image['Mask_name']}
    if set(expected) != set(areas) - border:
        raise ValueError('Nucleus ID map and non-border morphology IDs differ.')
    if any(areas[key] != int(row['Area_px2']) for key, row in expected.items()):
        raise ValueError('Nucleus ID map areas differ from morphology tables.')
    if not spatial.compatible(image, info):
        raise ValueError('Original image and nucleus-mask calibration differ.')


def analyze_run(bundle, channel, limits, include_border, engine, audit, progress):
    from pfak_imagej import save_images
    root, run = bundle['root'], bundle['run']
    label = f'C{channel}_threshold_{limits["requested_lower"]:g}'
    if not limits['upper_unbounded']:
        label += f'_to_{limits["requested_upper"]:g}'
    output = create_output(root / ASSAY_DIR, io.PREFIX + label + '_')
    parameters = {
        'Schema_version': 1, 'Run_ID': output.name, 'Channel': channel,
        'Nuclei_run_ID': run.name, 'Nuclei_run': str(run),
        'Particle_size_px2': bundle['particle_size'], 'Threshold': limits,
        'Projection': 'SUM', 'Projection_bit_depth': 32,
        'Include_border_nuclei': bool(include_border),
        'Object_connectivity': 8, 'Watershed': False, 'Object_size_filter': None,
        'Background_subtraction': False, 'Intensity_scaling': False,
        'pFAK_domain': 'Full field; one inclusive threshold mask; border objects included',
        'IntDen_definition': 'RawIntDen times pixel area (um2 when calibrated, otherwise px2)',
        'Acquisition_assumption': 'Comparable imaging settings, Z sampling and complete specimen coverage are assumed, not established by this analysis.',
        'Input_inventory_policy': 'All visible original images and final masks must match one-to-one, except recorded processing exclusions.',
        'ImageJ_version': engine.version, 'BioFormats_version': engine.bioformats_version,
        'Runtime': runtime_context(),
    }
    images, objects, failures, artifacts = [], [], [], {}
    state = {**parameters, 'Status': 'RUNNING', 'Started_UTC': utc_now(),
             'Images_measured': 0, 'Images_failed': 0}
    started = time.monotonic()
    io.write_json(output / 'run_parameters.json', parameters)
    io.write_json(output / 'pfak_run.json', state)
    audit.plan(root, output)
    with audit.stage(root, 'pFAK SUM and mask measurements', progress) as logger:
        logger.info('PARAMETERS | %s', json.dumps(parameters))
        try:
            exclusions.write_snapshot(output, bundle['processing_exclusions'])
            for row in bundle['processing_exclusions'].values():
                logger.warning('PROCESSING_EXCLUSION | %s | %s', row['Image_key'], row['Reason'])
            tasks = image_tasks(root, bundle)
            progress.phase('Measuring pFAK images', len(tasks))
            for index, task in enumerate(tasks, 1):
                source, mask_name = task['source'], task['Mask_name']
                progress.image = task['Image_name']
                stage = 'matching original and final masks'
                before = time.monotonic()
                try:
                    if task['error']:
                        raise ValueError(task['error'])
                    mask = run / mask_name
                    id_map = run / 'Morphology_QC' / (mask.stem + '_ids.tif')
                    if any(p.is_symlink() or not p.is_file() for p in (source, mask, id_map)):
                        raise ValueError('Original image, mask and ID map must be regular files.')
                    hashes = {p: spatial.digest(p) for p in (source, mask, id_map)}
                    stage = 'reading original metadata and nucleus IDs'
                    mode = 'nd2' if source.suffix.lower() == '.nd2' else 'tiff-stack'
                    info = engine.inspect(source, mode, channel)
                    ids = engine.labels(mask, id_map, info)
                    validate_nuclei(ids, bundle['images'][mask_name], bundle, info)
                    logger.info('IMAGE_PARAMETERS | %s | %s', source.name, json.dumps(info))
                    stage = 'SUM projection and pFAK measurements'
                    projection = engine.projection(source, channel, info)
                    measured, items, binary, object_ids = measure_projection(
                        projection, ids, limits, info, include_border)
                    stage = 'saving and verifying images'
                    folder = output / 'Images' / f'{index:06d}'
                    saved = save_images(folder, projection, binary, object_ids, ids, info, items)
                    if any(spatial.digest(p) != digest for p, digest in hashes.items()):
                        raise ValueError('Original image or nucleus mask changed during measurement.')
                    matches = WELL_PATTERN.findall(source.name)
                    well = f'{matches[0][0].upper()}{int(matches[0][1]):02d}' if len(matches) == 1 else ''
                    identity = {'Image_name': source.name, 'Mask_name': mask_name, 'Well': well}
                    row = {**identity, **measured, 'Run_ID': output.name, 'Dataset_path': str(root),
                           'Source_file': str(source), 'Source_SHA256': hashes[source],
                           'Nuclei_run_ID': run.name, 'Mask_SHA256': hashes[mask], 'ID_map_SHA256': hashes[id_map],
                           'Channel': channel, 'Projection': 'SUM', 'Particle_size_px2': bundle['particle_size'],
                           'Threshold_lower': limits['lower'],
                           'Threshold_upper': None if limits['upper_unbounded'] else limits['upper'],
                           'Image_folder': str(folder.relative_to(output)),
                           **{key: info.get(key) for key in ('Z_planes', 'Z_step_um', 'Width_px', 'Height_px', 'Pixel_type')}}
                    images.append(row)
                    objects.extend({**identity, **item} for item in items)
                    artifacts.update({str((folder / name).relative_to(output)): digest for name, digest in saved.items()})
                    logger.info('IMAGE_SAVED | %s | objects=%s | area=%s %s | RawIntDen=%.9g | IntDen=%.9g | elapsed=%.3fs',
                                source.name, len(items), row['pFAK_Area'], row['Area_unit'],
                                row['pFAK_RawIntDen'], row['pFAK_IntDen'], time.monotonic() - before)
                except Exception as error:  # A failed field must not stop the remaining images.
                    failures.append({key: task[key] for key in ('Image_name', 'Mask_name')} | {'Stage': stage, 'Error': str(error)})
                    logger.exception('IMAGE_FAILED | %s | %s', task['Image_name'], stage)
                progress.advance(task['Image_name'])
                state.update(Images_measured=len(images), Images_failed=len(failures), Current_image=task['Image_name'])
                io.write_json(output / 'pfak_run.json', state)
            z_counts = sorted({row['Z_planes'] for row in images})
            z_steps = sorted({row['Z_step_um'] for row in images if row['Z_step_um'] is not None})
            warnings = []
            if len(z_counts) > 1:
                warnings.append(f'SUM combines different Z counts: {z_counts}; signal and background may differ with stack depth.')
            if len(z_steps) > 1:
                warnings.append(f'Z steps differ: {z_steps}; SUM values do not have identical Z sampling.')
            if any(row['Z_step_um'] is None and row['Z_planes'] > 1 for row in images):
                warnings.append('Some Z stacks lack physical Z-step metadata; comparability cannot be verified.')
            if any(not spatial.calibrated(row) for row in images):
                warnings.append('Some images lack XY calibration; their areas and IntDen use pixel units and remain separate in reports.')
            for warning in warnings:
                logger.warning('ACQUISITION_QC | %s', warning)
            state.update(Warnings=warnings, Z_counts=z_counts, Z_steps_um=z_steps,
                         Area_units=sorted({row['Area_unit'] for row in images}),
                         Table_SHA256=io.save_tables(output, images, objects, failures, parameters),
                         Image_artifact_SHA256=artifacts)
            for path, content in bundle['files'].items():
                if path.read_bytes() != content:
                    raise ValueError(f'Nuclei input changed during analysis: {path}')
            if exclusions.load_exclusions(run) != bundle['processing_exclusions']:
                raise ValueError('Processing exclusions changed during pFAK analysis; rerun the command.')
            state['Status'] = 'PARTIAL' if images and failures else ('SUCCESS' if images else 'FAILED')
            if not tasks:
                state['Error'] = 'No eligible original/mask pairs remain after processing exclusions.'
        except CANCELLATION_EXCEPTIONS:
            state['Status'] = 'CANCELLED'
            raise
        except Exception as error:
            state.update(Status='FAILED', Error=str(error))
            logger.exception('ANALYSIS_FAILED')
        finally:
            state.update(Ended_UTC=utc_now(), Elapsed_seconds=time.monotonic() - started)
            io.write_json(output / 'pfak_run.json', state)
            audit.result(root, output, state['Status'], output)
            progress.message(f'{state["Status"]}: {len(images)} measured, {len(failures)} failed | {output}')
    return state['Status'] == 'SUCCESS', output


def main(argv=None, engine_factory=None):
    args = parse_arguments('quantify_pfak', argv)
    audit = TableJournal(JOURNAL, args.input)
    result = 1
    engine = None
    try:
        skipped = []
        roots = collect.discover_sources(args.input, audit, skipped)
        contexts = [{'root': root, 'morphology': discover_nuclei(root, args.nuclei_runs, audit)} for root in roots]
        selected = (collect.select_morphologies(contexts) if args.nuclei_runs == 'ask'
                    else [bundle for context in contexts for bundle in context['morphology']])
        missing = [context['root'] for context in contexts if not context['morphology']]
        for root in missing:
            audit.incomplete(root, 'No completed valid nuclei run. Run select_channels and generate_nuclei_mask first.')
        if not selected:
            raise ValueError('No completed nuclei runs available for pFAK analysis.')
        channel = args.channel or ask_integer('Original pFAK channel (1-based; q = cancel): ', 1)
        print(f'Experiments from JSON: {len(roots)} | selected nuclei runs: {len(selected)} | pFAK channel: {channel}')
        print('Original-channel SUM32; inclusive threshold mask; no background subtraction or watershed.')
        if engine_factory is None:
            from pfak_imagej import ImageJEngine
            engine_factory = ImageJEngine
        audit.event(None, 'IMAGEJ_INITIALIZING')
        print('Initializing ImageJ...')
        engine = engine_factory()
        failures = len(skipped) + len(missing)
        with TableProgress() as progress:
            for index, bundle in enumerate(selected, 1):
                progress.group(f'pFAK {index}/{len(selected)} | {bundle["root"].name} | {bundle["run"].name}')
                try:
                    ok, _ = analyze_run(bundle, channel, args.threshold_settings, args.include_border_nuclei,
                                        engine, audit, progress)
                    failures += not ok
                except (OSError, ValueError) as error:
                    audit.incomplete(bundle['root'], str(error))
                    failures += 1
        audit.status = 'INCOMPLETE' if failures else 'COMPLETE'
        result = 1 if failures else 0
    except CANCELLATION_EXCEPTIONS:
        audit.status = 'CANCELLED'
        audit.event(None, 'CANCELLED | pFAK analysis')
        result = 130
    except Exception:  # noqa: BLE001 - the CLI records unexpected failures and closes its journal
        audit.status = 'FAILED'
        audit.event(None, 'RUN_ABORTED | pFAK analysis', level=logging.ERROR, exc_info=True)
    finally:
        try:
            if engine is not None and getattr(engine, 'gateway', None) is not None:
                engine.gateway.dispose()
        finally:
            saved = audit.finish()
    return result if saved or result else 1
