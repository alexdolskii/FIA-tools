"""Folder-level terminal progress and scoped journals for nuclei generation."""

import logging
import shutil
import time
import warnings
from pathlib import Path

from interactive_input import CANCELLATION_EXCEPTIONS
from terminal_progress import CompactProgress


class NucleiProgress(CompactProgress):
    """Use the shared live-line engine without per-image terminal messages."""

    def __init__(self, stream=None):
        super().__init__(stream=stream)
        self.label = ''
        self.group_skipped = 0
        self.group_started = time.monotonic()
        self.active = False

    def group(self, label, total):
        with self._lock:
            self.active = False
            self.image = None
            self.label, self.group_total = label, total
            self.group_completed = self.group_failed = self.group_skipped = 0
            self.group_started = time.monotonic()
            self.stage = 'Preparing'
            self.message(f'{label} | images: {total}')
            self.active = True
            self._render(force=True)

    def begin_image(self, path):
        with self._lock:
            self.image = str(path)
            self.started = time.monotonic()
            self.stage = 'Reading image'
            if self.logger:
                self.logger.info('IMAGE_STARTED | file=%s', self.image)
            self._render(force=True)

    def finish_image(self, success=True, skipped=False):
        with self._lock:
            if self.image is None:
                return
            if skipped:
                self.group_skipped += 1
            elif success:
                self.group_completed += 1
            else:
                self.group_failed += 1
            if self.logger:
                self.logger.info('IMAGE_FINISHED | file=%s | status=%s | elapsed=%.3fs',
                                 self.image, 'SKIPPED' if skipped else
                                 ('COMPLETE' if success else 'FAILED'),
                                 time.monotonic() - self.started)
            self.image = None
            self.stage = 'Continuing'
            self._render(force=True)

    def _counts(self):
        done = self.group_completed + self.group_failed + self.group_skipped
        percent = 100 * done / self.group_total if self.group_total else 0
        counts = f'{done}/{self.group_total} ({percent:.1f}%)'
        if self.group_failed or self.group_skipped:
            counts += f' | failed {self.group_failed} | skipped {self.group_skipped}'
        return counts

    def _render(self, force=False):
        with self._lock:
            if not self.interactive or not self.active:
                return
            now = time.monotonic()
            if not force and now - self._last_render < 0.2:
                return
            self._last_render = now
            elapsed = int(now - self.group_started)
            timer = f'{elapsed // 60:02d}:{elapsed % 60:02d}'
            line = f'{self.label} | {self._counts()} | {self.stage} | Elapsed {timer}'
            width = max(1, shutil.get_terminal_size((120, 24)).columns - 1)
            if len(line) > width:
                line = f'{self._counts()} | {self.stage} | {timer}'
            if len(line) > width:
                done = self.group_completed + self.group_failed + self.group_skipped
                prefix, suffix = f'{done}/{self.group_total} | ', f' | {timer}'
                phase_width = max(0, width - len(prefix) - len(suffix))
                line = prefix + self.stage[:phase_width] + suffix
            self.stream.write('\r\033[2K' + line[:width])
            self.stream.flush()

    def __exit__(self, exc_type, exc, traceback):
        with self._lock:
            self.active = False
        return super().__exit__(exc_type, exc, traceback)


class _TerminalWarnings(logging.Handler):
    def __init__(self, progress):
        super().__init__(logging.WARNING)
        self.progress = progress

    def emit(self, record):
        # Full tracebacks belong in the journal; retain actionable errors here.
        if not getattr(record, 'label_contrast_notice', False):
            self.progress.message(f'{record.levelname}: {record.getMessage()}')


class NucleiRunLog:
    """Own and close all handlers; never attach journals to the root logger."""

    def __init__(self, path, label, total=0, mode='w', stream=None, quiet=False):
        self.path = Path(path)
        self.label, self.total, self.mode = label, total, mode
        self.quiet = quiet
        self.progress = NucleiProgress(stream)
        self.logger = logging.Logger(f'nuclei.{self.path}', level=logging.INFO)
        self.logger.propagate = False
        self.status = 'RUNNING'
        self.details = ''
        self.show_counts = True
        self.label_contrast_notices = 0

    def __enter__(self):
        self.started = time.monotonic()
        handler = logging.FileHandler(self.path, mode=self.mode, encoding='utf-8')
        handler.setFormatter(logging.Formatter('%(asctime)s | %(levelname)s | %(message)s'))
        self.logger.addHandler(handler)
        self.logger.addHandler(_TerminalWarnings(self.progress))
        self.progress.logger = self.logger
        self.logger.info('STARTED | stage=%s | images=%s', self.label, self.total)
        self.progress.__enter__()
        if not self.quiet:
            self.progress.group(self.label, self.total)
        self._warnings = warnings.catch_warnings()
        self._warnings.__enter__()
        warnings.showwarning = self._show_warning
        return self

    def _show_warning(self, message, category, filename, lineno, file=None, line=None):
        # skimage assesses uint16 label IDs as if they were grayscale intensities.
        # Retain every notice in the file but summarize these expected mask notices.
        label_notice = (category is UserWarning
                        and self.progress.stage == 'Saving mask and calibration'
                        and str(message).endswith(' is a low contrast image'))
        if label_notice:
            self.label_contrast_notices += 1
        self.logger.warning('%s | file=%s | %s (%s:%s)', category.__name__,
                            self.progress.image or '-', message, filename, lineno,
                            extra={'label_contrast_notice': label_notice})

    def saved(self, path):
        path = Path(path)
        if path.is_file():
            self.logger.info('SAVED | path=%s | bytes=%s', path, path.stat().st_size)
        else:
            self.logger.warning('Save returned without an output file: %s', path)

    def finish(self, status, show_counts=True, **details):
        self.status = status
        self.show_counts = show_counts
        self.details = ' | '.join(f'{key}={value}' for key, value in details.items())

    def __exit__(self, exc_type, exc, traceback):
        try:
            if exc_type is not None:
                if issubclass(exc_type, CANCELLATION_EXCEPTIONS):
                    self.status = 'CANCELLED'
                    self.logger.warning('CANCELLED | file=%s | phase=%s',
                                        self.progress.image, self.progress.stage)
                else:
                    self.status = 'FAILED'
                    self.logger.error('FAILED | file=%s | phase=%s | %s',
                                      self.progress.image, self.progress.stage, exc,
                                      exc_info=(exc_type, exc, traceback))
                    self.progress.finish_image(success=False)
            elif self.status == 'RUNNING':
                self.status = 'COMPLETE'
            counts = (f' | completed={self.progress.group_completed}'
                      f' | failed={self.progress.group_failed}'
                      f' | skipped={self.progress.group_skipped}'
                      f' | total={self.progress.group_total}') if self.show_counts else ''
            summary = (f'{self.label} | {self.status}{counts}'
                       f' | elapsed={time.monotonic() - self.started:.1f}s')
            if self.details:
                summary += f' | {self.details}'
            self.logger.info('FINISHED | %s', summary)
            with self.progress._lock:
                self.progress.active = False
                self.progress.image = None
            if not self.quiet:
                if self.label_contrast_notices:
                    self.progress.message(
                        f'WARNING: {self.label_contrast_notices} low-contrast label-mask '
                        f'notice(s); details in {self.path}')
                self.progress.message(summary)
                self.progress.message(f'Log: {self.path}')
        finally:
            self._warnings.__exit__(exc_type, exc, traceback)
            self.progress.__exit__(exc_type, exc, traceback)
            self.progress.logger = None
            for handler in list(self.logger.handlers):
                self.logger.removeHandler(handler)
                handler.close()
        return False
