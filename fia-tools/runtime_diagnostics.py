"""Read-only inspection of FIA resources, known caches and temporary locations."""

import argparse
import json
import os
import platform
import stat
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import psutil
from assay_layout import ASSAY_DIR
from run_resources import (
    RUN_PATTERN,
    SCHEMA,
    observation_supported,
    owned_path,
    process_state,
    resource_paths,
    runtime_home,
    utc_now,
)

JOURNALS = ('1_log.log', '2_log.log', '3_nuclei_intensity.log',
            '4_collect_marker_intensity.log', '5_marker_intensity_report.log')


def redirected(path):
    info = path.lstat()
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, 'st_file_attributes', 0) &
                                            getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0x400))


def walk_metadata(root, limit, errors):
    """Bounded metadata-only walk; do not follow links/junctions or cross mounts."""
    root = Path(root)
    if not root.exists():
        return
    try:
        if redirected(root):
            errors.append(f'Skipped linked directory: {root}')
            return
        device = root.stat().st_dev
        pending, seen = [root], 0
        while pending:
            directory = pending.pop()
            try:
                with os.scandir(directory) as entries:
                    for entry in entries:
                        seen += 1
                        if seen > limit:
                            errors.append(f'Scan limit ({limit} entries) reached: {root}')
                            return
                        path = Path(entry.path)
                        try:
                            info = entry.stat(follow_symlinks=False)
                            if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
                                continue
                            # Windows DirEntry.stat() may report st_dev=0;
                            # junctions/volume mounts are excluded as reparse points above.
                            if os.name != 'nt' and info.st_dev != device:
                                errors.append(f'Skipped mount point: {path}')
                                continue
                            is_dir = stat.S_ISDIR(info.st_mode)
                            yield path, info, is_dir
                            if is_dir:
                                pending.append(path)
                        except OSError as error:
                            errors.append(f'{path}: {error}')
            except OSError as error:
                errors.append(f'{directory}: {error}')
    except OSError as error:
        errors.append(f'{root}: {error}')


def size_of(path, limit, errors):
    files, size = 0, 0
    for item, info, is_dir in walk_metadata(path, limit, errors):
        if not is_dir:
            files += 1
            size += info.st_size
    return {'files': files, 'bytes': size}


def bounded_json(path):
    if redirected(path) or path.stat().st_size > 2_000_000:
        raise ValueError(f'Linked or oversized metadata: {path}')
    return json.loads(path.read_text(encoding='utf-8'))


def inspect_run(directory, limit, errors):
    record = bounded_json(directory / 'run.json')
    if not isinstance(record, dict) or record.get('schema') != SCHEMA or record.get('run_id') != directory.name:
        raise ValueError(f'Invalid runtime record: {directory}')
    members = list(record.get('processes', []))
    worker_file = directory / 'worker.json'
    if worker_file.exists():
        members.append(bounded_json(worker_file)['process'])
    if record.get('process_status') != 'EXITED':
        members.append(record.get('supervisor', {}))
    states = [process_state(member, record.get('host')) for member in members]
    if 'ACTIVE' in states:
        status = 'ACTIVE'
    elif 'UNKNOWN' in states or record.get('process_observation_incomplete'):
        status = 'UNKNOWN'
    elif record.get('process_status') != 'EXITED':
        # An abruptly lost supervisor may not have observed every descendant.
        status = 'POSSIBLE_REMAINDER'
    else:
        status = 'FINISHED'
    paths, issues = resource_paths(directory, limit)
    errors.extend(issues)
    resources = []
    for path in paths:
        if not path.exists() and not path.is_symlink():
            continue
        verified = owned_path(path, record)
        category = status if verified else 'UNKNOWN'
        if category == 'FINISHED':
            category = 'OWNED_REMAINDER'
        resources.append({'path': str(path), 'category': category,
                          **(size_of(path, limit, errors) if verified else {})})
    return {'run_id': directory.name, 'command': record.get('command'), 'host': record.get('host'),
            'started_utc': record.get('started_utc'), 'finished_utc': record.get('finished_utc'),
            'process_status': record.get('process_status'), 'exit_code': record.get('exit_code'),
            'status': status, 'processes': members, 'resources': resources,
            'cleanup': record.get('cleanup'),
            'sampled_peak_tree_rss_bytes': record.get('sampled_peak_tree_rss_bytes'),
            'sampled_peak_system_swap_bytes': record.get('sampled_peak_system_swap_bytes')}


def managed_runs(home, limit, errors):
    runs = []
    parent = home / 'runs'
    if parent.exists():
        for index, directory in enumerate(parent.iterdir()):
            if index >= limit:
                errors.append(f'Run count limit reached: {parent}')
                break
            if not RUN_PATTERN.fullmatch(directory.name):
                continue
            try:
                if redirected(directory) or not directory.is_dir():
                    continue
                runs.append(inspect_run(directory, limit, errors))
            except (OSError, ValueError, KeyError, TypeError) as error:
                errors.append(f'{directory}: {error}')
                runs.append({'run_id': directory.name, 'status': 'UNKNOWN', 'resources': []})
    return runs


def cache_locations():
    home = Path.home()
    # scyjava 1.10 uses these Java paths; jgo's standalone override is also shown.
    locations = [home / '.jgo', home / '.m2' / 'repository',
                 Path(os.environ.get('KERAS_HOME', home / '.keras')) / 'models',
                 home / '.matplotlib', Path(os.environ.get('XDG_CACHE_HOME', home / '.cache')) / 'matplotlib']
    locations += [Path(os.environ[key]) for key in ('JGO_CACHE_DIR', 'MPLCONFIGDIR') if os.environ.get(key)]
    return list(dict.fromkeys(path.expanduser().absolute() for path in locations))


def system_temporaries(limit, errors):
    locations = [Path(tempfile.gettempdir())]
    locations += [Path(os.environ[key]).expanduser() for key in ('TMPDIR', 'TEMP', 'TMP') if os.environ.get(key)]
    if os.name != 'nt':
        locations += [Path('/tmp'), Path('/var/tmp')]
    items, checked = [], set()
    for folder in locations:
        folder = folder.resolve()
        if folder in checked or not folder.is_dir():
            continue
        checked.add(folder)
        try:
            with os.scandir(folder) as entries:
                for count, entry in enumerate(entries, 1):
                    if count > limit:
                        errors.append(f'System temp entry limit reached: {folder}')
                        break
                    if not entry.name.startswith('openpyxl.') or not entry.is_file(follow_symlinks=False):
                        continue
                    info = entry.stat(follow_symlinks=False)
                    items.append({'path': entry.path, 'bytes': info.st_size,
                                  'modified_utc': datetime.fromtimestamp(info.st_mtime, timezone.utc).isoformat(),
                                  'category': 'UNATTRIBUTED',
                                  'reason': 'May belong to another Python program; age does not prove abandonment'})
        except OSError as error:
            errors.append(f'{folder}: {error}')
    return {'checked_locations': sorted(map(str, checked)), 'files': items}


def experiment_diagnostics(root, limit, errors, owned):
    assay = root / ASSAY_DIR
    candidates = []
    for path, info, is_dir in walk_metadata(assay, limit, errors):
        if any(parent in owned for parent in (path, *path.parents)):
            continue
        match = (path.name.startswith(('.fia_tmp_', '.fia_marker_collection_')) if is_dir else
                 path.name.endswith(('.tmp', '.partial.xlsx', '.partial.json', '.tmp.xlsx')))
        if match:
            candidates.append({'path': str(path), 'bytes': None if is_dir else info.st_size,
                               'category': 'POSSIBLE_REMAINDER'})
    journals = []
    for filename in JOURNALS:
        path = assay / filename
        if not path.exists():
            continue
        try:
            if redirected(path):
                continue
            with path.open('rb') as handle:
                handle.seek(max(0, path.stat().st_size - 65536))
                tail = handle.read(65536).decode('utf-8', errors='replace')
            lines = [line for line in tail.splitlines() if any(
                word in line for word in ('RUN_FINISHED', 'FINISHED', 'RUN_STATUS', 'status=', 'STATUS |'))]
            journals.append({'path': str(path), 'last_status_line': lines[-1] if lines else None,
                             'note': 'No final line is evidence of an incomplete log, not proof of a dead process'})
        except OSError as error:
            errors.append(f'{path}: {error}')
    return {'root': str(root), 'assay_exists': assay.is_dir(), 'candidates': candidates, 'journals': journals}


def active_processes(runs):
    result, checked = [], set()
    for run in runs:
        for member in run.get('processes', []):
            key = (member.get('pid'), member.get('created'))
            if key in checked:
                continue
            checked.add(key)
            if process_state(member, run.get('host')) != 'ACTIVE':
                continue
            try:
                process = psutil.Process(member['pid'])
                result.append({**member, 'run_id': run['run_id'], 'name': process.name(),
                               'rss_bytes': process.memory_info().rss})
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
    return result


def scan(roots=(), limit=100000, progress=print):
    errors, home = [], runtime_home()
    if not observation_supported():
        errors.append('Process inspection unavailable in this environment; process ownership is uncertain')
    progress('Check 1/5 | Managed FIA runs and temporary folders')
    runs = managed_runs(home, limit, errors)
    owned = {Path(item['path']) for run in runs for item in run['resources']}
    unregistered = []
    temp_parent = home / 'tmp'
    if temp_parent.is_dir():
        for count, path in enumerate(temp_parent.iterdir(), 1):
            if count > limit:
                errors.append(f'Temporary folder count limit reached: {temp_parent}')
                break
            if path not in owned:
                unregistered.append({'path': str(path), 'category': 'UNKNOWN'})
    progress('Check 2/5 | Known system temporary locations')
    system_temp = system_temporaries(limit, errors)
    progress('Check 3/5 | Persistent library caches')
    caches = [{'path': str(path), 'category': 'PERSISTENT_CACHE', **size_of(path, limit, errors)}
              for path in cache_locations() if path.exists()]
    progress('Check 4/5 | Experiment folders and current journals')
    experiments = []
    for index, root in enumerate(roots, 1):
        progress(f'Experiment {index}/{len(roots)} | {root.name}')
        experiments.append(experiment_diagnostics(root, limit, errors, owned))
    progress('Check 5/5 | Processes, memory, swap and disk space')
    disks = []
    for path in dict.fromkeys([Path.home(), home, *roots]):
        try:
            existing = path
            while not existing.exists() and existing != existing.parent:
                existing = existing.parent
            if path in roots and not path.is_dir():
                errors.append(f'Experiment folder unavailable: {path}')
            disks.append({'path': str(path), 'measured_at': str(existing),
                          **psutil.disk_usage(str(existing))._asdict()})
        except OSError as error:
            errors.append(f'Disk usage {path}: {error}')
    return {'schema': SCHEMA, 'generated_utc': utc_now(), 'host': platform.node(),
            'platform': platform.platform(), 'runtime_home': str(home), 'entry_limit_per_location': limit,
            'status': 'PARTIAL' if errors else 'COMPLETE', 'errors': errors,
            'managed_runs': runs, 'unregistered_temporary_folders': unregistered,
            'system_temporaries': system_temp, 'caches': caches, 'experiments': experiments,
            'active_managed_processes': active_processes(runs),
            'memory': psutil.virtual_memory()._asdict(), 'swap': psutil.swap_memory()._asdict(), 'disks': disks,
            'scope': 'Known FIA locations and current user temp roots only; no full-system scan or deletion. '
                     'No image/workbook contents read. No historic peak inferred from current memory. '
                     'Unregistered Python/Java processes are not attributed to FIA.'}


def human_size(size):
    return f'{size / (1024 ** 3):.2f} GiB'


def summary(report):
    resources = [item for run in report['managed_runs'] for item in run['resources']]
    remaining = [item for item in resources if item['category'] == 'OWNED_REMAINDER']
    candidates = sum(len(item['candidates']) for item in report['experiments'])
    candidates += sum(item['category'] not in ('OWNED_REMAINDER', 'ACTIVE') for item in resources)
    candidates += len(report['system_temporaries']['files']) + len(report['unregistered_temporary_folders'])
    lines = [f'Diagnostics: {report["status"]}',
             f'Managed runs: {len(report["managed_runs"])} | active processes: {len(report["active_managed_processes"])}',
             (f'Confirmed owned remainders: {len(remaining)} folder(s), '
              f'{human_size(sum(item.get("bytes", 0) for item in remaining))}'),
             f'Possible/unattributed files or folders: {candidates}',
             f'Persistent caches: {human_size(sum(item["bytes"] for item in report["caches"]))}',
             (f'RAM available: {human_size(report["memory"]["available"])} / '
              f'{human_size(report["memory"]["total"])} | system swap used: {human_size(report["swap"]["used"])}')]
    for disk in report['disks']:
        lines.append(f'Disk free: {human_size(disk["free"])} | {disk["path"]}')
    for error in report['errors']:
        lines.append(f'WARNING: {error}')
    return '\n'.join(lines)


def save_log(parent, report):
    parent.mkdir(parents=True, exist_ok=True)
    current = parent / 'fia_diagnostics.log'
    if current.exists():
        if redirected(current):
            raise OSError(f'Refusing to replace linked diagnostic log: {current}')
        archive = parent / 'logs'
        archive.mkdir(exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S_%f')
        current.rename(archive / f'fia_diagnostics_{stamp}_{uuid4().hex[:8]}.log')
    text = summary(report) + '\n\nDetailed diagnostic snapshot (JSON):\n'
    current.write_text(text + json.dumps(report, indent=2) + '\n', encoding='utf-8')
    return current


def main(argv=None):
    parser = argparse.ArgumentParser(description='Inspect FIA temporary files, processes and disk/memory usage; no cleanup.')
    parser.add_argument('-i', '--input', help='Optional JSON with paths_to_files experiment folders')
    parser.add_argument('--max-entries', type=int, default=100000,
                        help='Maximum directory entries per inspected location (default: 100000); partial scans are reported')
    args = parser.parse_args(argv)
    if args.max_entries <= 0:
        parser.error('--max-entries must be a positive integer')
    roots = []
    if args.input:
        try:
            path = Path(args.input)
            if path.name.startswith('._'):
                raise ValueError('AppleDouble sidecar is not an input JSON')
            values = json.loads(path.read_text(encoding='utf-8'))['paths_to_files']
            if not isinstance(values, list) or any(not isinstance(value, str) or not value for value in values):
                raise ValueError('paths_to_files must be a list of nonempty paths')
            roots = list(dict.fromkeys(Path(value).expanduser().resolve() for value in values))
        except (OSError, ValueError, KeyError, TypeError) as error:
            parser.error(str(error))
    try:
        report = scan(roots, args.max_entries)
        print(summary(report))
        destinations = [runtime_home()]
        destinations.extend(root / ASSAY_DIR for root in roots if (root / ASSAY_DIR).is_dir())
        saved = True
        for destination in destinations:
            try:
                print(f'Diagnostics log: {save_log(destination, report)}')
            except OSError as error:
                saved = False
                print(f'Could not save diagnostic log: {error}', file=sys.stderr)
        return 0 if saved and report['status'] == 'COMPLETE' else 1
    except (KeyboardInterrupt, EOFError):
        print('Diagnostics canceled. No temporary files were removed.')
        return 130
    except OSError as error:
        print(f'Diagnostics failed: {error}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
