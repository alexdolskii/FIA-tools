"""Owned temporary storage and a lightweight supervisor for FIA CLI processes.

No imaging libraries are imported here. The supervisor outlives the worker's
Python/JVM shutdown, so Windows file handles are closed before cleanup begins.
"""

import json
import os
import platform
import re
import shutil
import signal
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import psutil

SCHEMA = 'fia-runtime-1'
RUN_PATTERN = re.compile(r'^\d{8}T\d{12}Z_[0-9a-f]{12}$')
COMMANDS = (
    'select_channels', 'generate_nuclei_mask', 'generate_foci_mask',
    'quantify_foci', 'quantify_nuclear_intensity',
    'fia_collect_marker_intensity_results', 'fia_marker_intensity_report',
)


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def runtime_home():
    return Path(os.environ.get('FIA_RUNTIME_HOME', Path.home() / '.fia-tools')).expanduser().resolve()


def write_json(path, data):
    path = Path(path)
    temporary = path.with_name(path.name + '.writing')
    temporary.write_text(json.dumps(data, indent=2) + '\n', encoding='utf-8')
    temporary.replace(path)


def identity(process=None):
    try:
        process = process or psutil.Process()
        return {'pid': process.pid, 'created': process.create_time()}
    except psutil.Error as error:
        return {'pid': process.pid if process else os.getpid(), 'created': None, 'error': str(error)}


def observation_supported():
    """A remapped/restricted proc filesystem cannot establish process ownership."""
    try:
        if sys.platform.startswith('linux'):
            with open('/proc/self/stat', encoding='utf-8') as handle:
                if int(handle.read().split(' ', 1)[0]) != os.getpid():
                    return False
        return identity().get('created') is not None
    except (OSError, ValueError):
        return False


def process_state(record, host=None):
    """Do not confuse a reused PID, inaccessible process, or another host with exit."""
    if not isinstance(record, dict) or not observation_supported():
        return 'UNKNOWN'
    if host and host != platform.node():
        return 'UNKNOWN'
    if record.get('created') is None:
        return 'UNKNOWN'
    try:
        process = psutil.Process(int(record['pid']))
        if abs(process.create_time() - float(record['created'])) > 0.01:
            return 'EXITED'
        if process.status() == psutil.STATUS_ZOMBIE:
            return 'EXITED'
        return 'ACTIVE'
    except psutil.NoSuchProcess:
        return 'EXITED'
    except (psutil.AccessDenied, OSError, ValueError, KeyError, TypeError):
        return 'UNKNOWN'


def current_run():
    run_id = os.environ.get('FIA_RUN_ID', '')
    directory = os.environ.get('FIA_RUN_DIR')
    if not directory or not RUN_PATTERN.fullmatch(run_id):
        return None
    return run_id, Path(directory)


def runtime_context():
    run = current_run()
    if run:
        run_id, directory = run
        return f'runtime_run_id={run_id} | registry={directory / "run.json"} | temporary_directory={os.environ.get("TMPDIR", "")}'
    return ''


def record_resource(directory, path):
    # One append per owned directory, not one entry per image or checkpoint.
    payload = json.dumps({'path': str(path), 'kind': 'temporary_directory'}) + '\n'
    with (directory / 'resources.jsonl').open('a', encoding='utf-8') as handle:
        handle.write(payload)


def create_owned_directory(path, run_id, directory):
    path = Path(path)
    try:
        path.mkdir(mode=0o700)
    except FileExistsError:
        if path.is_symlink() or not path.is_dir():
            raise OSError(f'Unsafe temporary directory: {path}')
        owner = json.loads((path / '.fia-owner.json').read_text(encoding='utf-8'))
        if owner != {'schema': SCHEMA, 'run_id': run_id}:
            raise OSError(f'Temporary directory belongs to a different run: {path}')
    else:
        # Register first: an interrupted creation remains visible to diagnostics.
        record_resource(directory, path)
        write_json(path / '.fia-owner.json', {'schema': SCHEMA, 'run_id': run_id})
    return path


def temporary_path(destination, legacy=None):
    """Unique staging file on the destination filesystem; never move final outputs."""
    destination = Path(destination)
    run = current_run()
    if run is None:
        return Path(legacy) if legacy is not None else destination.with_name('.' + destination.name + '.tmp')
    run_id, directory = run
    area = create_owned_directory(destination.parent.resolve() / ('.fia_tmp_' + run_id), run_id, directory)
    return area / (uuid4().hex + '_' + destination.name)


@contextmanager
def temporary_directory(parent, prefix='.fia_marker_collection_'):
    run = current_run()
    if run:
        run_id, directory = run
        parent = create_owned_directory(Path(parent).resolve() / ('.fia_tmp_' + run_id), run_id, directory)
    with tempfile.TemporaryDirectory(prefix=prefix, dir=parent) as folder:
        yield folder


def resource_paths(directory, limit=100000):
    path = Path(directory) / 'resources.jsonl'
    if not path.exists():
        return [], []
    paths, errors = [], []
    with path.open(encoding='utf-8') as handle:
        for index, line in enumerate(handle, 1):
            if index > limit:
                errors.append(f'Resource registry entry limit reached: {path}')
                break
            try:
                value = json.loads(line)
                if value['kind'] != 'temporary_directory':
                    raise ValueError('unknown resource kind')
                item = Path(value['path'])
                if not item.is_absolute():
                    raise ValueError('relative resource path')
                if item not in paths:
                    paths.append(item)
            except (ValueError, KeyError, TypeError) as error:
                errors.append(f'{path}:{index}: {error}')
    return paths, errors


def owned_path(path, record):
    """Validate both the namespace and owner marker before inspecting or deleting."""
    run_id = record.get('run_id', '')
    if not RUN_PATTERN.fullmatch(run_id):
        return False
    if path.name not in (run_id, '.fia_tmp_' + run_id):
        return False
    if path.is_symlink() or path.resolve() != path or not path.is_dir():
        return False
    try:
        marker = path / '.fia-owner.json'
        return not marker.is_symlink() and json.loads(marker.read_text(encoding='utf-8')) == {
            'schema': SCHEMA, 'run_id': run_id}
    except (OSError, ValueError):
        return False


def cleanup_resources(directory, record):
    paths, errors = resource_paths(directory)
    removed = []
    for path in paths:
        if not path.exists() and not path.is_symlink():
            continue
        if not owned_path(path, record):
            errors.append(f'Ownership could not be verified: {path}')
            continue
        try:
            shutil.rmtree(path)
            removed.append(str(path))
        except OSError as error:
            errors.append(f'{path}: {error}')
    return {'status': 'INCOMPLETE' if errors else 'CLEANED', 'removed': removed, 'errors': errors}


def child_environment(record, directory, temp):
    env = os.environ.copy()
    env.update(FIA_RUN_ID=record['run_id'], FIA_RUN_DIR=str(directory))
    env.update({key: str(temp) for key in ('TMPDIR', 'TEMP', 'TMP')})
    # java.io.tmpdir is configured through scyjava before JVM startup in the worker.
    # Preserve Java heap options, models, numerical settings and persistent caches.
    return env


def event(directory, message):
    with (directory / 'runtime.log').open('a', encoding='utf-8') as handle:
        handle.write(f'{utc_now()} | {message}\n')


def _observe(child, record):
    """Small process-tree sample. Summed RSS includes shared pages more than once."""
    if record.get('observation_unavailable'):
        return
    try:
        processes = [psutil.Process(child.pid)]
        processes += processes[0].children(recursive=True)
    except psutil.NoSuchProcess:
        return
    except psutil.AccessDenied:
        record['process_observation_incomplete'] = True
        return
    observed = {item['pid']: item for item in record['processes']}
    rss = 0
    for process in processes:
        try:
            item = identity(process)
            observed[item['pid']] = item
            rss += process.memory_info().rss
        except psutil.NoSuchProcess:
            continue
        except psutil.AccessDenied:
            record['process_observation_incomplete'] = True
    record['processes'] = list(observed.values())
    record['sampled_peak_tree_rss_bytes'] = max(record.get('sampled_peak_tree_rss_bytes', 0), rss)
    try:
        record['sampled_peak_system_swap_bytes'] = max(
            record.get('sampled_peak_system_swap_bytes', 0), psutil.swap_memory().used)
    except (OSError, psutil.Error):
        record['swap_observation_unavailable'] = True


def run_command(command, argv, worker_path=None):
    """Run one analysis command; cleanup only after its whole observed tree exits."""
    if command not in COMMANDS:
        raise ValueError(f'Unknown FIA command: {command}')
    home = runtime_home()
    run_id = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ_') + uuid4().hex[:12]
    directory = home / 'runs' / run_id
    temp = home / 'tmp' / run_id
    try:
        directory.mkdir(parents=True, mode=0o700)
        temp.parent.mkdir(parents=True, exist_ok=True)
        observable = observation_supported()
        record = {'schema': SCHEMA, 'run_id': run_id, 'command': command, 'arguments': list(argv),
                  'cwd': str(Path.cwd()), 'host': platform.node(), 'started_utc': utc_now(),
                  'supervisor': identity() if observable else {'pid': os.getpid(), 'created': None},
                  'processes': [], 'process_observation_incomplete': not observable,
                  'observation_unavailable': not observable,
                  'process_status': 'STARTING', 'cleanup': {'status': 'PENDING'}}
        create_owned_directory(temp, run_id, directory)
        write_json(directory / 'run.json', record)
        event(directory, f'STARTED | command={command} | temporary_directory={temp}')
    except (OSError, ValueError) as error:
        print(f'Cannot prepare FIA temporary storage: {error}', file=sys.stderr)
        return 1
    print(f'Run resources: {directory}', flush=True)
    if not observable:
        print('Process inspection is unavailable; temporary resources will be retained for review.', flush=True)
    worker = worker_path or Path(__file__).with_name('runtime_worker.py')
    child = None
    terminated = False
    journal_failed = False

    def terminate(signum, frame):
        nonlocal terminated
        terminated = True
        if child is not None and child.poll() is None:
            child.send_signal(signum)

    previous_term = signal.signal(signal.SIGTERM, terminate)
    try:
        child = subprocess.Popen([sys.executable, str(worker), command, *argv],
                                 env=child_environment(record, directory, temp))
        record['process_status'] = 'RUNNING'
        while True:
            try:
                _observe(child, record)
                try:
                    write_json(directory / 'run.json', record)
                except OSError as error:
                    if not journal_failed:
                        print(f'Runtime journal unavailable; temporary files will be retained: {error}', file=sys.stderr)
                    journal_failed = True
                code = child.wait(timeout=1)
                break
            except subprocess.TimeoutExpired:
                continue
            except KeyboardInterrupt:
                # Terminal Ctrl+C is delivered to both processes in their shared
                # foreground group. Keep supervising the worker's own cancellation.
                terminated = True
                continue
    except OSError as error:
        print(f'FIA runtime error: {error}. Resources retained: {directory}', file=sys.stderr)
        # A worker may still be alive if a registry write failed. Never delete its files.
        record.update(process_status='SUPERVISION_ERROR', error=str(error))
        try:
            write_json(directory / 'run.json', record)
        except OSError:
            pass
        return 1
    finally:
        signal.signal(signal.SIGTERM, previous_term)

    states = [process_state(item, record['host']) for item in record['processes']]
    record.update(process_status='EXITED', exit_code=code, finished_utc=utc_now(), interrupted=terminated)
    # Nonzero failures retain useful evidence; diagnostics never deletes old runs.
    if (code in (0, 130) and not journal_failed and all(state == 'EXITED' for state in states)
            and not record['process_observation_incomplete']):
        try:
            record['cleanup'] = cleanup_resources(directory, record)
        except OSError as error:
            record['cleanup'] = {'status': 'INCOMPLETE', 'errors': [str(error)]}
    else:
        record['cleanup'] = {'status': 'RETAINED', 'reason': 'Failure, live descendant or uncertain process state'}
    try:
        write_json(directory / 'run.json', record)
        event(directory, f'PROCESS_EXITED | exit_code={code} | cleanup={record["cleanup"]["status"]}')
    except OSError as error:
        print(f'Could not save runtime completion: {error}', file=sys.stderr)
        return code or 1
    print(f'Temporary files: {record["cleanup"]["status"]}. Runtime log: {directory / "runtime.log"}', flush=True)
    return (128 - code if code < 0 else code) or (1 if journal_failed else 0)
