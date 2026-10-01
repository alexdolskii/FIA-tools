"""One rotating text journal per experiment for the full intensity command."""

from assay_layout import ASSAY_DIR

from run_resources import runtime_context

import logging
import re
import time
import warnings
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from interactive_input import CANCELLATION_EXCEPTIONS


class _FileHandler(logging.FileHandler):
    def __init__(self, path, journal):
        super().__init__(path, mode='a', encoding='utf-8')
        self.journal = journal

    def handleError(self, record):
        self.journal.write_failed = True
        print(f'Could not append to intensity journal: {self.baseFilename}')


class _ReaderProgressFilter(logging.Filter):
    def filter(self, record):
        return (record.levelno >= logging.WARNING or
                re.match(r'Bio-Formats: Parsing block .*? \d+%', record.getMessage()) is None)


class _Console(logging.Handler):
    def __init__(self, progress):
        super().__init__(logging.WARNING)
        self.progress = progress

    def emit(self, record):
        message = self.progress.message if self.progress else print
        message(f'{record.levelname}: {record.getMessage()}')


class IntensityJournal:
    """Stage handlers are scoped; journals rotate only at first registration."""

    filename = '3_nuclei_intensity.log'

    def __init__(self, input_path=None):
        self.input_path = str(Path(input_path).resolve()) if input_path else 'not supplied'
        self.run_id = f'{datetime.now(timezone.utc):%Y%m%d_%H%M%S_%f}_{uuid4().hex[:8]}'
        self.folders = {}
        self.status = 'failed'
        self.write_failed = False

    def _handler(self, path):
        handler = _FileHandler(path, self)
        handler.addFilter(_ReaderProgressFilter())
        formatter = logging.Formatter('%(asctime)sZ | %(levelname)s | %(message)s')
        formatter.converter = time.gmtime
        handler.setFormatter(formatter)
        return handler

    def register(self, root):
        root = Path(root).resolve()
        if root in self.folders:
            return
        path = root / ASSAY_DIR / self.filename
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            archive = path.parent / 'logs'
            archive.mkdir(exist_ok=True)
            stamp = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
            destination = archive / f'{path.stem}_{stamp:%Y%m%d_%H%M%S_%f}.log'
            if destination.exists():
                destination = archive / f'{destination.stem}_{uuid4().hex}.log'
            path.rename(destination)
        self.folders[root] = {'path': path, 'started': time.monotonic(),
                              'incomplete': False, 'runs': {}}
        self.event(root, 'RUN_STARTED | run_id=%s | input_folder=%s | input_json=%s',
                   self.run_id, root, self.input_path)

        if runtime_context():
            self.event(root, "TEMPORARY_RESOURCES | %s", runtime_context())

    @contextmanager
    def logger(self, root=None, progress=None):
        logger = logging.Logger(f'intensity.{self.run_id}', logging.INFO)
        logger.propagate = False
        records = self.folders.values() if root is None else [self.folders[Path(root).resolve()]]
        try:
            for record in records:
                try:
                    logger.addHandler(self._handler(record['path']))
                except OSError as error:
                    self.write_failed = True
                    print(f"Could not write intensity journal {record['path']}: {error}")
            logger.addHandler(_Console(progress))
            yield logger
        finally:
            for handler in list(logger.handlers):
                logger.removeHandler(handler)
                handler.close()

    def event(self, root, message, *args, level=logging.INFO, exc_info=None):
        with self.logger(root) as logger:
            logger.log(level, message, *args, exc_info=exc_info)

    def incomplete(self, root, reason):
        self.folders[Path(root).resolve()]['incomplete'] = True
        self.event(root, 'NOT_PROCESSED | %s', reason, level=logging.WARNING)

    @contextmanager
    def stage(self, root, name, progress=None):
        started = time.monotonic()
        with self.logger(root, progress) as logger:
            previous = progress.logger if progress else None
            if progress:
                progress.logger = logger
            logger.info('STAGE_STARTED | %s', name)
            try:
                with warnings.catch_warnings():
                    def showwarning(message, category, filename, lineno, file=None, line=None):
                        logger.warning('%s | file=%s | %s (%s:%s)', category.__name__,
                                       progress.image if progress else '-', message, filename, lineno)
                    warnings.showwarning = showwarning
                    yield logger
            except CANCELLATION_EXCEPTIONS:
                logger.warning('CANCELLED | stage=%s | file=%s', name,
                               progress.image if progress else '-')
                raise
            except Exception as error:
                logger.exception('STAGE_FAILED | stage=%s | %s', name, error)
                raise
            finally:
                logger.info('STAGE_FINISHED | %s | elapsed=%.3fs', name, time.monotonic() - started)
                if progress:
                    progress.logger = previous

    @staticmethod
    def key(record):
        return (record['marker']['name'], str(record['path']))

    def plan(self, records):
        for record in records:
            self.folders[Path(record['dataset']).resolve()]['runs'][self.key(record)] = 'pending'
            self.event(record['dataset'], 'SELECTED | marker=%s | channel=%s | nuclei_run=%s | images=%s',
                       record['marker']['name'], record['marker']['channel'], record['path'], len(record['pairs']))

    def result(self, record, status, output=None):
        self.folders[Path(record['dataset']).resolve()]['runs'][self.key(record)] = status
        self.event(record['dataset'], 'RESULT | marker=%s | nuclei_run=%s | status=%s | output=%s',
                   record['marker']['name'], record['path'], status, output)

    def finish(self):
        for root, record in self.folders.items():
            outcomes = list(record['runs'].values())
            if outcomes and all(status == 'complete' for status in outcomes) and not record['incomplete']:
                status = 'COMPLETE'
            elif self.status == 'canceled' and (not outcomes or any(s in ('pending', 'interrupted') for s in outcomes)):
                status = 'CANCELLED'
            elif self.status == 'failed' or (outcomes and all(s == 'failed' for s in outcomes)):
                status = 'FAILED'
            elif not outcomes and not record['incomplete'] and self.status == 'complete':
                status = 'NOT_SELECTED'
            else:
                status = 'INCOMPLETE'
            if self.write_failed and status == 'COMPLETE':
                status = 'INCOMPLETE'
            self.event(root, 'RUN_FINISHED | run_id=%s | status=%s | completed=%s | '
                       'selected=%s | elapsed=%.3fs | results=%s', self.run_id, status,
                       outcomes.count('complete'), len(outcomes), time.monotonic() - record['started'], record['runs'])
            print(f"Folder status: {status}. Log: {record['path']}")
        return not self.write_failed
