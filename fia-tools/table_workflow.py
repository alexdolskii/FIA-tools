"""Per-experiment journals and display-only progress for spreadsheet workflows."""

from assay_layout import ASSAY_DIR

import logging
import shutil
import time
import warnings
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from interactive_input import CANCELLATION_EXCEPTIONS
from terminal_progress import CompactProgress, short_image_name


class TableProgress(CompactProgress):
    """Count real operations within a phase, without estimating overall runtime."""

    def __init__(self, stream=None):
        super().__init__(stream=stream)
        self.label = ''
        self.started = time.monotonic()
        self.done = 0
        self.phase_total = None
        self.phase_started = self.started

    def group(self, label, total=None):
        with self._lock:
            self.label = label
            self.stage = ''
            self.image = None
            self.message(label)

    def phase(self, name, total=None):
        with self._lock:
            if self.stage and self.logger:
                self.logger.info('PHASE_FINISHED | %s | operations=%s/%s | elapsed=%.3fs',
                                 self.stage, self.done, self.phase_total, time.monotonic() - self.phase_started)
            self.stage, self.done, self.phase_total = name, 0, total
            self.image = None
            self.phase_started = time.monotonic()
            if self.logger:
                self.logger.info('PHASE_STARTED | %s | total=%s', name, total)
            if not self.interactive:
                self.message(name)
            self._render(force=True)

    def advance(self, item=None):
        with self._lock:
            self.done += 1
            self.image = str(item) if item is not None else None
            self._render()

    def _render(self, force=False):
        with self._lock:
            if not self.interactive or not self.stage or self._stop.is_set():
                return
            now = time.monotonic()
            if not force and now - self._last_render < 0.2:
                return
            self._last_render = now
            count = (f' {self.done}/{self.phase_total} ({100 * self.done / self.phase_total:.0f}%)'
                     if self.phase_total else '')
            item = f' | {short_image_name(self.image)}' if self.image else ''
            elapsed = int(now - self.started)
            line = f'{self.label} | {self.stage}{count}{item} | {elapsed // 60:02d}:{elapsed % 60:02d}'
            width = max(1, shutil.get_terminal_size((120, 24)).columns - 1)
            if len(line) > width:
                line = f'{self.stage}{count}{item} | {elapsed // 60:02d}:{elapsed % 60:02d}'
            self.stream.write('\r\033[2K' + line[:width])
            self.stream.flush()


class _FileHandler(logging.FileHandler):
    def __init__(self, path, journal):
        super().__init__(path, mode='a', encoding='utf-8')
        self.journal = journal

    def handleError(self, record):
        self.journal.write_failed = True
        self.journal.message(f'Could not append to journal: {self.baseFilename}')


class _Console(logging.Handler):
    def __init__(self, journal):
        super().__init__(logging.WARNING)
        self.journal = journal

    def emit(self, record):
        self.journal.message(f'{record.levelname}: {record.getMessage()}')


class TableJournal:
    """Rotate once per CLI invocation; preserve early failures and cancellations."""

    def __init__(self, filename, input_path=None):
        self.filename = filename
        self.input_path = str(Path(input_path).resolve()) if input_path else 'not supplied'
        self.run_id = f'{datetime.now(timezone.utc):%Y%m%d_%H%M%S_%f}_{uuid4().hex[:8]}'
        self.folders = {}
        self.status = 'FAILED'
        self.write_failed = False
        self.progress = None

    def message(self, message):
        (self.progress.message if self.progress else print)(message)

    def register(self, root):
        root = Path(root).resolve()
        if root in self.folders:
            return
        path = root / ASSAY_DIR / self.filename
        record = {'path': path, 'handler': None, 'started': time.monotonic(),
                  'runs': {}, 'incomplete': False}
        self.folders[root] = record
        try:
            if path.parent.is_symlink():
                raise OSError(f'Linked fia_assay directory is not accepted: {path.parent}')
            path.parent.mkdir(exist_ok=True)
            if path.exists():
                if path.is_symlink() or not path.is_file():
                    raise OSError(f'Existing journal is not a regular file: {path}')
                archive = path.parent / 'logs'
                if archive.is_symlink():
                    raise OSError(f'Linked log archive is not accepted: {archive}')
                archive.mkdir(exist_ok=True)
                stamp = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
                target = archive / f'{path.stem}_{stamp:%Y%m%d_%H%M%S_%f}.log'
                if target.exists():
                    target = target.with_name(f'{target.stem}_{uuid4().hex}.log')
                path.rename(target)
            handler = _FileHandler(path, self)
            formatter = logging.Formatter('%(asctime)sZ | %(levelname)s | %(message)s')
            formatter.converter = time.gmtime
            handler.setFormatter(formatter)
            record['handler'] = handler
        except OSError as error:
            self.write_failed = True
            self.message(f'Could not open journal {path}: {error}')
        self.event(root, 'RUN_STARTED | run_id=%s | input_folder=%s | input_json=%s',
                   self.run_id, root, self.input_path)

    def logger(self, root=None):
        logger = logging.Logger(f'table_workflow.{self.run_id}', logging.INFO)
        logger.propagate = False
        records = self.folders.values() if root is None else [self.folders[Path(root).resolve()]]
        for record in records:
            if record['handler']:
                logger.addHandler(record['handler'])
        logger.addHandler(_Console(self))
        return logger

    def event(self, root, message, *args, level=logging.INFO, exc_info=None):
        self.logger(root).log(level, message, *args, exc_info=exc_info)

    def incomplete(self, root, reason):
        self.folders[Path(root).resolve()]['incomplete'] = True
        self.event(root, 'NOT_PROCESSED | %s', reason, level=logging.WARNING)

    def plan(self, root, key):
        self.folders[Path(root).resolve()]['runs'][str(key)] = 'PENDING'
        self.event(root, 'SELECTED | %s', key)

    def result(self, root, key, status, output=None):
        self.folders[Path(root).resolve()]['runs'][str(key)] = status
        self.event(root, 'RESULT | source=%s | status=%s | output=%s', key, status, output)

    @contextmanager
    def stage(self, root, name, progress=None):
        started = time.monotonic()
        logger = self.logger(root)
        previous_progress, previous_logger = self.progress, progress.logger if progress else None
        self.progress = progress
        if progress:
            progress.logger = logger
        logger.info('STAGE_STARTED | %s', name)
        status = 'COMPLETE'
        try:
            with warnings.catch_warnings():
                def showwarning(message, category, filename, lineno, file=None, line=None):
                    logger.warning('%s | %s (%s:%s)', category.__name__, message, filename, lineno)
                warnings.showwarning = showwarning
                yield logger
        except CANCELLATION_EXCEPTIONS:
            status = 'CANCELLED'
            logger.warning('CANCELLED | stage=%s', name)
            raise
        except Exception:
            status = 'FAILED'
            logger.exception('STAGE_FAILED | %s', name)
            raise
        finally:
            if progress and progress.stage:
                logger.info('PHASE_FINISHED | %s | operations=%s/%s | elapsed=%.3fs',
                            progress.stage, progress.done, progress.phase_total,
                            time.monotonic() - progress.phase_started)
            logger.info('STAGE_FINISHED | %s | status=%s | elapsed=%.3fs',
                        name, status, time.monotonic() - started)
            if progress:
                progress.logger = previous_logger
                progress.image, progress.stage = None, ''
            self.progress = previous_progress

    def finish(self):
        try:
            for root, record in self.folders.items():
                outcomes = list(record['runs'].values())
                if outcomes and all(s.startswith('SUCCESS') for s in outcomes) and not record['incomplete']:
                    status = 'COMPLETE'
                elif self.status == 'CANCELLED' and (not outcomes or any(s in ('PENDING', 'CANCELLED') for s in outcomes)):
                    status = 'CANCELLED'
                elif self.status == 'FAILED' or (outcomes and all(s == 'FAILED' for s in outcomes)):
                    status = 'FAILED'
                elif not outcomes and not record['incomplete']:
                    status = 'NOT_SELECTED'
                else:
                    status = 'INCOMPLETE'
                if self.write_failed and status == 'COMPLETE':
                    status = 'INCOMPLETE'
                self.event(root, 'RUN_FINISHED | run_id=%s | status=%s | elapsed=%.3fs | results=%s',
                           self.run_id, status, time.monotonic() - record['started'], record['runs'])
                self.message(f'Folder status: {status}. Log: {record["path"]}')
        finally:
            for record in self.folders.values():
                if record['handler']:
                    try:
                        record['handler'].close()
                    except OSError as error:
                        self.write_failed = True
                        self.message(f'Could not close journal {record["path"]}: {error}')
        return not self.write_failed
