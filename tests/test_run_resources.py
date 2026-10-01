"""Real child processes verify ownership, exit ordering and retained crash evidence."""

import json
import os
import platform
import subprocess
import sys
from pathlib import Path

import psutil
import pytest
import run_resources as runtime
import runtime_diagnostics as diagnostics

SCRIPTS = Path(runtime.__file__).parent
NATIVE_PROCESS_INFO = runtime.observation_supported()


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    home = tmp_path / 'runtime home with spaces'
    output = tmp_path / 'experiment with spaces' / 'fia_assay'
    output.mkdir(parents=True)
    monkeypatch.setenv('FIA_RUNTIME_HOME', str(home))
    monkeypatch.delenv('FIA_RUN_ID', raising=False)
    monkeypatch.delenv('FIA_RUN_DIR', raising=False)
    monkeypatch.setenv('PYTHONPATH', str(SCRIPTS) + os.pathsep + os.environ.get('PYTHONPATH', ''))
    # Some execution containers remap PIDs without remounting /proc. Exercise
    # real file/process-exit behavior there with an explicit observer test double;
    # process-tree and PID identity tests below require a native process table.
    if not NATIVE_PROCESS_INFO:
        monkeypatch.setattr(runtime, 'observation_supported', lambda: True)
        monkeypatch.setattr(runtime, 'identity', lambda process=None: {'pid': os.getpid(), 'created': 1.0})
        monkeypatch.setattr(runtime, '_observe', lambda child, record: None)
        monkeypatch.setattr(runtime, 'process_state', lambda record, host=None: 'EXITED')
        monkeypatch.setattr(diagnostics, 'process_state', lambda record, host=None: 'EXITED')
    worker = tmp_path / 'test_worker.py'
    worker.write_text('''
import json, os, sys, tempfile, time
from pathlib import Path
from run_resources import temporary_path, write_json, identity
output = Path(sys.argv[2])
mode = sys.argv[3]
directory = Path(os.environ['FIA_RUN_DIR'])
write_json(directory / 'worker.json', {'process': identity()})
with tempfile.NamedTemporaryFile(delete=False) as handle:
    handle.write(b'temporary data')
    default_temp = handle.name
staging = temporary_path(output / 'result.csv')
staging.write_text('Nucleus_ID,Intensity\\n1,65535\\n')
write_json(output / ('evidence_' + os.environ['FIA_RUN_ID'] + '.json'), {
    'default_temp': default_temp, 'staging': str(staging),
    'TMPDIR': os.environ['TMPDIR'], 'TEMP': os.environ['TEMP'], 'TMP': os.environ['TMP']})
if mode == 'crash':
    os._exit(1)
if mode == 'slow':
    time.sleep(1.5)
if mode == 'descendant':
    import subprocess
    child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])
    write_json(output / 'descendant.json', identity(__import__('psutil').Process(child.pid)))
    time.sleep(1.5)
staging.replace(output / 'result.csv')
sys.exit(130 if mode == 'cancel' else 0)
''', encoding='utf-8')
    return home, output, worker


@pytest.mark.parametrize('mode,code', [('success', 0), ('cancel', 130)])
def test_cleanup_after_process_exit_preserves_outputs_and_unowned_files(isolated, mode, code):
    home, output, worker = isolated
    unrelated = output / 'someone_else.tmp'
    unrelated.write_text('keep')
    assert runtime.run_command('fia_collect_marker_intensity_results', [str(output), mode], worker) == code
    record_path, = (home / 'runs').glob('*/run.json')
    record = json.loads(record_path.read_text())
    assert record['process_status'] == 'EXITED' and record['exit_code'] == code
    assert record['cleanup']['status'] == 'CLEANED'
    assert not list((home / 'tmp').iterdir())
    assert not list(output.glob('.fia_tmp_*'))
    evidence, = output.glob('evidence_*.json')
    values = json.loads(evidence.read_text())
    assert Path(values['default_temp']).parent == Path(values['TMPDIR'])
    assert values['TMPDIR'] == values['TEMP'] == values['TMP']
    assert Path(values['staging']).parent.parent == output
    assert (output / 'result.csv').read_text() == 'Nucleus_ID,Intensity\n1,65535\n'
    assert unrelated.read_text() == 'keep'


def test_crash_leaves_owned_evidence_and_diagnostics_does_not_delete(isolated):
    home, output, worker = isolated
    assert runtime.run_command('fia_collect_marker_intensity_results', [str(output), 'crash'], worker) == 1
    directory, = (home / 'runs').iterdir()
    errors = []
    report = diagnostics.inspect_run(directory, 1000, errors)
    assert report['status'] == 'FINISHED'
    assert report['cleanup']['status'] == 'RETAINED'
    assert len(report['resources']) == 2
    assert all(item['category'] == 'OWNED_REMAINDER' for item in report['resources'])
    assert all(Path(item['path']).is_dir() for item in report['resources'])
    assert not errors


@pytest.mark.skipif(not NATIVE_PROCESS_INFO, reason='Executor PID namespace differs from /proc')
def test_living_descendant_prevents_cleanup(isolated):
    home, output, worker = isolated
    try:
        assert runtime.run_command('fia_collect_marker_intensity_results', [str(output), 'descendant'], worker) == 0
        directory, = (home / 'runs').iterdir()
        report = diagnostics.inspect_run(directory, 1000, [])
        assert report['status'] == 'ACTIVE'
        assert report['cleanup']['status'] == 'RETAINED'
        assert all(Path(item['path']).exists() for item in report['resources'])
    finally:
        path = output / 'descendant.json'
        if path.exists():
            child = psutil.Process(json.loads(path.read_text())['pid'])
            child.terminate()
            try:
                child.wait(timeout=3)
            except psutil.TimeoutExpired:
                child.kill()


@pytest.mark.skipif(not NATIVE_PROCESS_INFO, reason='Executor PID namespace differs from /proc')
def test_pid_reuse_and_other_host_are_not_mistaken_for_active_run():
    value = runtime.identity()
    assert runtime.process_state(value, platform.node()) == 'ACTIVE'
    assert runtime.process_state({**value, 'created': value['created'] - 100}, platform.node()) == 'EXITED'
    assert runtime.process_state(value, 'another-computer') == 'UNKNOWN'


def test_cleanup_refuses_unowned_or_linked_paths(isolated):
    home, output, worker = isolated
    runtime.run_command('fia_collect_marker_intensity_results', [str(output), 'crash'], worker)
    directory, = (home / 'runs').iterdir()
    record = json.loads((directory / 'run.json').read_text())
    victim = output / 'valuable_results'
    victim.mkdir()
    (victim / 'image.tif').write_bytes(b'original')
    runtime.record_resource(directory, victim)
    link = output / 'different_parent' / ('.fia_tmp_' + record['run_id'])
    link.parent.mkdir()
    try:
        link.symlink_to(victim, target_is_directory=True)
    except OSError:
        pass  # Native Windows may lack symlink privileges; unowned path still covered.
    else:
        runtime.record_resource(directory, link)
    result = runtime.cleanup_resources(directory, record)
    assert result['status'] == 'INCOMPLETE'
    assert (victim / 'image.tif').read_bytes() == b'original'


def test_simultaneous_runs_have_different_namespaces(isolated):
    home, output, worker = isolated
    script = ('from run_resources import run_command; import sys; '
              'sys.exit(run_command("fia_collect_marker_intensity_results", '
              '[sys.argv[1], "slow"], sys.argv[2]))')
    if not NATIVE_PROCESS_INFO:
        script = ('import run_resources as r; '
                  'r.observation_supported=lambda: True; '
                  'r.identity=lambda: {"pid": 999999, "created": 1.0}; '
                  'r._observe=lambda child, record: None; ' + script)
    children = [subprocess.Popen([sys.executable, '-c', script, str(output), str(worker)],
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                for _ in range(2)]
    for child in children:
        out, err = child.communicate(timeout=20)
        assert child.returncode == 0, out + err
    records = [json.loads(path.read_text()) for path in (home / 'runs').glob('*/run.json')]
    assert len(records) == 2 and len({item['run_id'] for item in records}) == 2
    assert all(item['cleanup']['status'] == 'CLEANED' for item in records)
    evidence = [json.loads(path.read_text()) for path in output.glob('evidence_*.json')]
    assert len({item['TMPDIR'] for item in evidence}) == 2


def test_registry_failure_keeps_supervising_worker_and_retains_files(isolated, monkeypatch):
    home, output, worker = isolated
    write = runtime.write_json
    calls = []
    def fail_checkpoints(path, value):
        if path.name == 'run.json':
            calls.append(path)
            if len(calls) > 1:
                raise OSError('disk unavailable')
        return write(path, value)
    monkeypatch.setattr(runtime, 'write_json', fail_checkpoints)
    assert runtime.run_command('fia_collect_marker_intensity_results', [str(output), 'slow'], worker) == 1
    assert (output / 'result.csv').exists()  # Worker completed before the supervisor returned.
    assert list((home / 'tmp').iterdir()) and list(output.glob('.fia_tmp_*'))


def test_excel_uses_managed_python_temp_directory(isolated):
    home, output, worker = isolated
    worker.write_text('''
import json, os, sys
from pathlib import Path
from openpyxl import Workbook
book = Workbook(write_only=True)
sheet = book.create_sheet()
sheet.append(['test', 65535])
Path(sys.argv[2], 'excel_path.json').write_text(json.dumps({'path': sheet._writer.out}))
book.save(Path(sys.argv[2], 'test.xlsx'))
''')
    assert runtime.run_command('fia_collect_marker_intensity_results', [str(output)], worker) == 0
    path = Path(json.loads((output / 'excel_path.json').read_text())['path'])
    assert home / 'tmp' in path.parents and not path.exists()
    assert (output / 'test.xlsx').exists()


def test_diagnostics_is_lightweight_and_scan_limits_are_explicit(tmp_path):
    completed = subprocess.run([sys.executable, '-c',
        ('import runtime_diagnostics,sys; '
         'assert not any(m in sys.modules for m in ("imagej","scyjava","tensorflow","stardist","numpy","openpyxl"))')],
        capture_output=True, text=True, check=False,
        env={**os.environ, 'PYTHONPATH': str(SCRIPTS) + os.pathsep + os.environ.get('PYTHONPATH', '')})
    assert completed.returncode == 0, completed.stderr
    for index in range(5):
        (tmp_path / f'{index}.tmp').write_text('keep')
    errors = []
    assert diagnostics.size_of(tmp_path, 2, errors)['files'] == 2
    assert 'Scan limit' in errors[0]
    assert len(list(tmp_path.iterdir())) == 5


def test_diagnostics_rotates_log_without_changing_previous_contents(tmp_path):
    report = {'status': 'COMPLETE', 'managed_runs': [], 'experiments': [],
              'system_temporaries': {'files': []}, 'unregistered_temporary_folders': [],
              'active_managed_processes': [], 'caches': [], 'memory': {'total': 2, 'available': 1},
              'swap': {'used': 0}, 'disks': [], 'errors': []}
    log = diagnostics.save_log(tmp_path, report)
    prior = log.read_bytes()
    diagnostics.save_log(tmp_path, report)
    archived, = (tmp_path / 'logs').iterdir()
    assert archived.read_bytes() == prior


def test_canonical_cli_parses_invalid_arguments_without_starting_imaging(isolated):
    process = subprocess.run([sys.executable, str(SCRIPTS / '1_select_channels.py'), '-I', 'x.json'],
                             capture_output=True, text=True, timeout=20, check=False)
    assert process.returncode == 2
    assert 'usage:' in process.stderr and 'scyjava' not in process.stderr
    assert 'Initializing ImageJ' not in process.stdout


def test_java_temp_option_keeps_heap_and_endpoint(monkeypatch, tmp_path):
    import fiji_config
    import scyjava
    monkeypatch.setenv('FIA_RUN_ID', 'test')
    monkeypatch.setenv('TMPDIR', str(tmp_path / 'space in path'))
    old_heap = os.environ.get('_JAVA_OPTIONS')
    scyjava.config.add_option.reset_mock()
    fiji_config.configure_runtime()
    scyjava.config.add_option.assert_called_once_with('-Djava.io.tmpdir=' + str(tmp_path / 'space in path'))
    assert os.environ.get('_JAVA_OPTIONS') == old_heap
    assert fiji_config.FIJI_ENDPOINT == 'sc.fiji:fiji:2.14.0'


@pytest.mark.parametrize('state', ['ACTIVE', 'UNKNOWN'])
def test_live_or_uncertain_tree_keeps_temporary_files(isolated, monkeypatch, state):
    home, output, worker = isolated
    monkeypatch.setattr(runtime, '_observe', lambda child, record:
                        record.update(processes=[{'pid': child.pid, 'created': 1.0}]))
    monkeypatch.setattr(runtime, 'process_state', lambda member, host=None: state)
    assert runtime.run_command('fia_collect_marker_intensity_results', [str(output), 'success'], worker) == 0
    directory, = (home / 'runs').iterdir()
    assert json.loads((directory / 'run.json').read_text())['cleanup']['status'] == 'RETAINED'
    assert list((home / 'tmp').iterdir()) and list(output.glob('.fia_tmp_*'))


def test_process_identity_contract_with_reused_pid_and_denied_access(monkeypatch):
    from unittest.mock import Mock
    process = Mock()
    process.create_time.return_value = 1234.0
    process.status.return_value = psutil.STATUS_RUNNING
    monkeypatch.setattr(runtime, 'observation_supported', lambda: True)
    factory = Mock(return_value=process)
    monkeypatch.setattr(psutil, 'Process', factory)
    assert runtime.process_state({'pid': 5, 'created': 1234.0}) == 'ACTIVE'
    assert runtime.process_state({'pid': 5, 'created': 999.0}) == 'EXITED'
    assert runtime.process_state({'pid': 5, 'created': None}) == 'UNKNOWN'
    factory.side_effect = psutil.AccessDenied(5)
    assert runtime.process_state({'pid': 5, 'created': 1234.0}) == 'UNKNOWN'
