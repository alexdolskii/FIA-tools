"""Compact, display-only progress for image workflows and Bio-Formats readers."""

import logging
import os
import re
import shutil
import sys
import threading
import time
from pathlib import Path

from interactive_input import CANCELLATION_EXCEPTIONS


def short_image_name(path):
    name = Path(path).name
    match = re.search(r'Well([A-Za-z]\d+)_Point[^_]+_(\d+)', name)
    if match:
        return f'{match[1]}:{match[2]}'
    return name if len(name) <= 30 else name[:13] + '...' + name[-14:]


class CompactProgress:
    """Refresh one terminal line; redirected output gets concise image summaries."""

    def __init__(self, total=None, stream=None):
        self.stream = stream if stream is not None else sys.stdout
        self.interactive = bool(self.stream.isatty()) and os.environ.get('TERM') != 'dumb'
        self.total = total
        self.completed = self.failed = self.group_completed = self.group_failed = 0
        self.group_total = 0
        self.image = None
        self.stage = ''
        self.percent = None
        self.logger = None
        self._last_render = 0.0
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread = None

    def __enter__(self):
        if self.interactive:
            self._thread = threading.Thread(target=self._tick, daemon=True)
            self._thread.start()
        return self

    def __exit__(self, exc_type, exc, traceback):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=1)
        if exc_type is not None and self.image is not None:
            status = 'CANCELLED' if issubclass(exc_type, CANCELLATION_EXCEPTIONS) else 'INTERRUPTED'
            self.message(f'{status}: {self.image} | stage={self.stage}')
        with self._lock:
            self._clear()
            self.image = None
        return False

    def _tick(self):
        while not self._stop.wait(0.2):
            self._render()

    def _clear(self):
        if self.interactive:
            self.stream.write('\r\033[2K')
            self.stream.flush()

    def message(self, message):
        with self._lock:
            self._clear()
            self.stream.write(str(message) + '\n')
            self.stream.flush()
            self._render(force=True)

    def group(self, label, total):
        with self._lock:
            self.image = None
            self.group_total = total
            self.group_completed = self.group_failed = 0
            self.message(label)

    def begin_image(self, path):
        with self._lock:
            self.image = str(path)
            self.started = time.monotonic()
            self.stage, self.percent = 'Starting', None
            if not self.interactive:
                self.message(f'Image {self.group_completed + self.group_failed + 1}/{self.group_total}: '
                             f'{short_image_name(path)}')
            self._render(force=True)

    def phase(self, name, percent=None):
        with self._lock:
            changed = self.stage != name
            self.stage, self.percent = name, percent
            if changed and self.logger:
                self.logger.info('PHASE | file=%s | %s', self.image, name)
            self._render()

    def finish_image(self, success=True):
        with self._lock:
            if self.image is None:
                return
            name = short_image_name(self.image or '')
            elapsed = time.monotonic() - self.started
            if success:
                self.completed += 1
                self.group_completed += 1
            else:
                self.failed += 1
                self.group_failed += 1
            self.stage, self.percent = ('Done' if success else 'Failed'), None
            self._render(force=True)
            if not self.interactive:
                self.message(f'{"OK" if success else "FAILED"}: {name} | '
                             f'{self._counts()} | {elapsed:.1f}s')
            self.image = None
            if self.interactive and self.group_completed + self.group_failed == self.group_total:
                self.stream.write('\n')
                self.stream.flush()

    def _counts(self):
        local = f'Images {self.group_completed}/{self.group_total}'
        if self.total is not None:
            fraction = 100 * self.completed / self.total if self.total else 0
            local += f' | Total {self.completed}/{self.total} ({fraction:.1f}%)'
        if self.failed:
            local += f' | Failed {self.failed}'
        return local

    def _render(self, force=False):
        with self._lock:
            if not self.interactive or self.image is None:
                return
            now = time.monotonic()
            if not force and now - self._last_render < 0.2:
                return
            self._last_render = now
            percent = f' {self.percent}%' if self.percent is not None else ''
            elapsed = int(now - self.started)
            line = (f'{self._counts()} | {short_image_name(self.image)} | '
                    f'{self.stage}{percent} | {elapsed // 60:02d}:{elapsed % 60:02d}')
            width = max(1, shutil.get_terminal_size((120, 24)).columns - 1)
            if len(line) > width:
                overall = f' | All {self.completed}/{self.total}' if self.total is not None else ''
                line = (f'{self.group_completed}/{self.group_total}{overall} | '
                        f'{short_image_name(self.image)} | {self.stage}{percent} | '
                        f'{elapsed // 60:02d}:{elapsed % 60:02d}')
            # Keep the stage and elapsed time visible even in narrow terminals.
            if len(line) > width:
                line = f'{self.group_completed}/{self.group_total} | {self.stage}{percent} | {elapsed}s'
            self.stream.write('\r\033[2K' + line[:width])
            self.stream.flush()

    def bioformats_event(self, message, level=logging.INFO):
        if self.logger:
            self.logger.log(level, 'Bio-Formats: %s', message)
        if level >= logging.WARNING:
            if not self.logger or not any(isinstance(h, ProgressLogHandler) for h in self.logger.handlers):
                self.message(f'{logging.getLevelName(level)}: {message}')
            return
        match = re.search(r"Parsing block .*? (\d+)%", message)
        if match and self.image is not None:
            self.phase('ND2 structure', min(99, max(0, int(match[1]))))


class ProgressLogHandler(logging.Handler):
    """Place warnings and errors above the live line without losing the message."""

    def __init__(self, progress):
        super().__init__(logging.WARNING)
        self.progress = progress

    def emit(self, record):
        self.progress.message(self.format(record))
