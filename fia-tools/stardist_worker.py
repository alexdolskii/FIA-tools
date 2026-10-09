"""Isolate native StarDist crashes; keep one model alive between successful images."""

import contextlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np


class ImageInferenceError(RuntimeError):
    """An image failed; its caller must persist an exclusion before continuing."""


def checked_normalize(image, normalize):
    if image.ndim != 2 or image.dtype != np.uint8:
        raise ImageInferenceError(f'Expected 2D uint8; shape={image.shape}, dtype={image.dtype}')
    low, high = np.percentile(image, (3, 99.8))
    if high == low and image.max() != image.min():
        raise ImageInferenceError(
            f'Degenerate normalization: p3=p99.8={low:g}, min={image.min()}, max={image.max()}')
    normalized = normalize(image)
    if not np.isfinite(normalized).all():
        raise ImageInferenceError('Normalization produced NaN or infinity')
    return normalized


class IsolatedStarDist:
    def __init__(self, name, command=None):
        self.name = name
        self.command = command or [sys.executable, str(Path(__file__).resolve()), name]
        self.temporary = tempfile.TemporaryDirectory(prefix='fia-stardist-')
        self.folder = Path(self.temporary.name)
        self.process = None
        self.log = (self.folder / 'worker.log').open('w+b')

    @classmethod
    def from_pretrained(cls, name):
        model = cls(name)
        try:
            model.start()
        except BaseException:
            model.close()
            raise
        return model

    def diagnostic(self, offset=0):
        self.log.flush()
        with (self.folder / 'worker.log').open('rb') as handle:
            handle.seek(offset)
            return handle.read()[-8000:].decode('utf-8', errors='replace').strip()

    def start(self):
        self.process = subprocess.Popen(self.command, stdin=subprocess.PIPE,
                                        stdout=subprocess.PIPE, stderr=self.log,
                                        text=True, bufsize=1)
        line = self.process.stdout.readline()
        if not line or json.loads(line).get('status') != 'ready':
            self.stop()
            raise RuntimeError('StarDist model initialization failed: ' + self.diagnostic())

    def stop(self):
        if self.process is None:
            return
        process, self.process = self.process, None
        if process.stdin:
            try:
                process.stdin.close()
            except BrokenPipeError:
                pass
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        process.stdout.close()

    def predict_instances(self, image, **settings):
        if self.process is None:
            self.start()
        source, result = self.folder / 'input.npy', self.folder / 'labels.npy'
        result.unlink(missing_ok=True)
        np.save(source, image, allow_pickle=False)
        offset = os.fstat(self.log.fileno()).st_size
        try:
            self.process.stdin.write(json.dumps({'input': str(source), 'output': str(result), **settings}) + '\n')
            self.process.stdin.flush()
            line = self.process.stdout.readline()
            if not line:
                code = self.process.wait()
                raise ImageInferenceError(f'StarDist worker exited with code {code}: {self.diagnostic(offset)}')
            message = json.loads(line)
            if message.get('status') == 'fatal':
                raise RuntimeError('StarDist worker I/O failed: ' + message.get('error', 'Unknown error'))
            if message.get('status') != 'ok':
                raise ImageInferenceError(message.get('error', 'Invalid StarDist response'))
            labels = np.load(result, allow_pickle=False)
            if (labels.shape != image.shape or labels.dtype.kind not in 'ui'
                    or labels.min() < 0 or labels.max() > np.iinfo(np.uint16).max):
                raise ImageInferenceError('Invalid label mask shape, type or label range')
            return labels, {}
        except (BrokenPipeError, EOFError) as error:
            self.stop()
            raise ImageInferenceError('StarDist worker disconnected: ' + self.diagnostic(offset)) from error
        except ImageInferenceError:
            self.stop()
            raise

    def close(self):
        self.stop()
        self.log.close()
        self.temporary.cleanup()


def main():
    if os.name == 'posix':
        import resource
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    # Reserve the original stdout for the protocol, including native C/C++ writes.
    protocol = os.fdopen(os.dup(sys.stdout.fileno()), 'w', buffering=1)
    os.dup2(sys.stderr.fileno(), sys.stdout.fileno())
    with contextlib.redirect_stdout(sys.stderr):
        from stardist.models import StarDist2D
        model = StarDist2D.from_pretrained(sys.argv[1])
        protocol.write(json.dumps({'status': 'ready'}) + '\n')
        for line in sys.stdin:
            request = json.loads(line)
            phase = 'loading input'
            try:
                image = np.load(request['input'], allow_pickle=False)
                phase = 'inference'
                labels, _ = model.predict_instances(
                    image,
                    nms_thresh=request['nms_thresh'], prob_thresh=request['prob_thresh'])
                phase = 'saving mask'
                np.save(request['output'], labels, allow_pickle=False)
                message = {'status': 'ok'}
            except Exception as error:  # noqa: BLE001 - report image/library failures across the worker boundary
                message = {'status': 'error' if phase == 'inference' else 'fatal',
                           'error': f'{phase}: {type(error).__name__}: {error}'}
            protocol.write(json.dumps(message) + '\n')


if __name__ == '__main__':
    main()
