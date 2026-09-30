"""Compact display, diagnostic preservation and native Java logger restoration."""

import io
import logging
import os
from pathlib import Path

import pytest
from bioformats_progress import bioformats_progress
from terminal_progress import CompactProgress, ProgressLogHandler


class Terminal(io.StringIO):
    def isatty(self):
        return True


def test_redirected_output_has_no_parsing_flood_and_keeps_warnings(tmp_path):
    stream = io.StringIO()
    log = logging.Logger('test_progress', logging.INFO)
    handler = logging.FileHandler(tmp_path / 'details.log')
    log.addHandler(handler)
    with CompactProgress(2, stream) as progress:
        progress.logger = log
        log.addHandler(ProgressLogHandler(progress))
        progress.group('Folder 1/1', 2)
        progress.begin_image('first.nd2')
        for percent in range(100):
            progress.bioformats_event(f"Parsing block 'ImageDataSeq' {percent}%")
        progress.bioformats_event('Reader warning', logging.WARNING)
        assert progress.completed == 0
        progress.finish_image()
        progress.begin_image('second.nd2')
        progress.bioformats_event('Reader error', logging.ERROR)
        progress.finish_image(False)
    handler.close()
    output = stream.getvalue()
    assert '\r' not in output and '\033' not in output
    assert 'Parsing block' not in output
    assert output.count('Reader warning') == output.count('Reader error') == 1
    assert 'Total 1/2 (50.0%)' in output and 'Failed 1' in output
    assert '100.0%' not in output
    details = (tmp_path / 'details.log').read_text()
    assert details.count('Parsing block') == 100
    assert 'Reader warning' in details and 'Reader error' in details


def test_terminal_refreshes_one_line_and_separates_warnings(monkeypatch):
    monkeypatch.setenv('TERM', 'xterm')
    monkeypatch.setenv('COLUMNS', '160')
    stream = Terminal()
    progress = CompactProgress(2, stream)
    progress.group('Folder', 2)
    progress.begin_image('long__WellC04_PointC04_0001_ChannelDAPI.nd2')
    before = stream.getvalue().count('\n')
    for percent in range(100):
        progress.bioformats_event(f"Parsing block 'ImageDataSeq' {percent}%")
    progress._render(force=True)
    assert stream.getvalue().count('\n') == before
    assert 'C04:0001' in stream.getvalue() and 'ND2 structure 99%' in stream.getvalue()
    assert 'ND2 structure 100%' not in stream.getvalue()
    assert stream.getvalue().count('\033[2K') < 10  # Fast events are throttled.
    progress.bioformats_event('Test warning', logging.WARNING)
    assert '\r\033[2KWARNING: Test warning\n' in stream.getvalue()
    progress.phase('Saving spreadsheets')
    assert progress.completed == 0
    progress.finish_image()
    assert progress.completed == 1
    progress.begin_image('second.nd2')
    progress.finish_image()
    assert stream.getvalue().endswith('\n')
    assert 'Total 2/2 (100.0%)' in stream.getvalue()


def test_narrow_terminal_and_cancellation(monkeypatch):
    monkeypatch.setenv('TERM', 'xterm')
    monkeypatch.setenv('COLUMNS', '42')
    stream = Terminal()
    progress = CompactProgress(1, stream)
    with pytest.raises(KeyboardInterrupt), progress:
        progress.group('Folder', 1)
        progress.begin_image('very_long_source_filename.nd2')
        progress.phase('ND2 structure', 42)
        progress._render(force=True)
        last = stream.getvalue().rsplit('\033[2K', 1)[1]
        assert len(last) <= 41 and 'ND2 structure 42%' in last
        raise KeyboardInterrupt()
    assert 'CANCELLED' in stream.getvalue()
    assert progress.completed == 0 and progress.image is None
    assert not progress._thread.is_alive()


@pytest.fixture
def java_logging():
    jar = os.environ.get('FIA_BIOFORMATS_JAR')
    if not jar or not Path(jar).is_file():
        pytest.skip('Set FIA_BIOFORMATS_JAR for native logger tests')
    jpype = pytest.importorskip('jpype')
    if not jpype.isJVMStarted():
        jpype.startJVM('-Djava.awt.headless=true', classpath=[jar])
    else:
        jpype.addClassPath(jar)
    return jpype.JClass


@pytest.mark.parametrize('exception', [None, OSError, KeyboardInterrupt])
def test_native_bioformats_events_restore_logger_on_every_exit(java_logging, exception, capfd):
    factory = java_logging('org.slf4j.LoggerFactory')
    parent = factory.getLogger('loci.formats')
    child = factory.getLogger('loci.formats.in.ND2Reader')
    snapshot = (parent.getLevel(), parent.isAdditive(), list(parent.iteratorForAppenders()))
    stream = io.StringIO()
    progress = CompactProgress(1, stream)
    progress.group('Folder', 1)
    progress.begin_image('image.nd2')
    def run():
        with bioformats_progress(progress):
            child.info("Parsing block 'ImageDataSeq' 76%")
            child.warn('Preserved native warning')
            child.error('Preserved native error', java_logging('java.lang.RuntimeException')('Reader details'))
            if exception:
                raise exception()
    if exception:
        with pytest.raises(exception):
            run()
    else:
        run()
    assert (progress.stage, progress.percent) == ('ND2 structure', 76)
    assert stream.getvalue().count('Preserved native warning') == 1
    assert stream.getvalue().count('Preserved native error') == 1
    assert 'RuntimeException: Reader details' in stream.getvalue()
    captured = capfd.readouterr()
    assert 'Parsing block' not in captured.out + captured.err
    assert 'Preserved native' not in captured.out + captured.err
    assert snapshot == (parent.getLevel(), parent.isAdditive(), list(parent.iteratorForAppenders()))


def test_unsupported_java_logging_keeps_normal_output(monkeypatch):
    jpype = pytest.importorskip('jpype')
    monkeypatch.setattr(jpype, 'isJVMStarted', lambda: True)
    def unavailable(name):
        raise TypeError('Unsupported logging backend')
    monkeypatch.setattr(jpype, 'JClass', unavailable)
    stream = io.StringIO()
    progress = CompactProgress(stream=stream)
    for _ in range(2):
        with bioformats_progress(progress):
            pass
    assert stream.getvalue().count('retaining normal output') == 1
