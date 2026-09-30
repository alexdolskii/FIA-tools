"""Per-folder audit logs for channel preparation, independent of the root logger."""

import logging
import platform
import time
from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from uuid import uuid4

import tifffile


class ChannelRunLog:
    """Keep a readable current log, archive previous runs, and count outcomes."""

    def __init__(self, folder, input_json, run_id, script_digest):
        self.folder = Path(folder)
        self.output = self.folder / "foci_assay"
        self.input_json = str(Path(input_json).resolve()) if input_json else "not supplied"
        self.run_id = run_id
        self.script_digest = script_digest
        self.logger = logging.Logger(f"select_channels.{run_id}.{uuid4().hex}", logging.INFO)
        self.logger.propagate = False
        self.started = time.monotonic()
        self.files = []
        self.completed = 0
        self.failed = 0
        self.attempted = 0
        self.output_files = 0
        self.current_image = None
        self.status = "RUNNING"

    def __enter__(self):
        log_path = self.output / "1_log.log"
        if log_path.exists():
            archive = self.output / "logs"
            archive.mkdir(exist_ok=True)
            stamp = datetime.fromtimestamp(log_path.stat().st_mtime, timezone.utc)
            archived = archive / f"1_log_{stamp:%Y%m%d_%H%M%S_%f}.log"
            if archived.exists():
                archived = archive / f"{archived.stem}_{uuid4().hex}.log"
            log_path.rename(archived)
        handler = logging.FileHandler(log_path, mode="w", encoding="utf-8")
        formatter = logging.Formatter("%(asctime)sZ - %(levelname)s - %(message)s")
        formatter.converter = time.gmtime
        handler.setFormatter(formatter)
        self.logger.addHandler(handler)
        console = logging.StreamHandler()
        console.setLevel(logging.WARNING)
        console.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))
        self.logger.addHandler(console)
        self.logger.info("STARTED | run_id=%s | input_folder=%s | input_json=%s | output_folder=%s",
                         self.run_id, self.folder.resolve(), self.input_json, self.output.resolve())
        try:
            package_version = version("fia-tools")
        except PackageNotFoundError:
            package_version = "source checkout (not installed)"
        self.logger.info("SOFTWARE | fia-tools=%s | Python=%s | script_SHA256=%s",
                         package_version, platform.python_version(), self.script_digest)
        return self

    def inventory(self):
        hidden = unsupported = directories = 0
        for path in self.folder.iterdir():
            if not path.is_file():
                directories += 1
            elif path.name.startswith("."):
                hidden += 1
            elif path.suffix.lower() in (".nd2", ".tif", ".tiff"):
                self.files.append(path.name)
            else:
                unsupported += 1
        self.logger.info("INPUTS | images=%d | ignored_hidden_files=%d | "
                         "ignored_unsupported_files=%d | ignored_directories=%d",
                         len(self.files), hidden, unsupported, directories)

    def start_image(self, filename):
        self.current_image = filename
        self.image_started = time.monotonic()
        self.attempted += 1
        self.logger.info("IMAGE_STARTED | file=%s", filename)

    def fail_image(self, reason):
        self.failed += 1
        self.logger.error("IMAGE_FAILED | file=%s | reason=%s | elapsed_s=%.3f",
                          self.current_image, reason, time.monotonic() - self.image_started)
        self.current_image = None

    def finish_image(self):
        self.completed += 1
        self.logger.info("IMAGE_COMPLETED | file=%s | elapsed_s=%.3f",
                         self.current_image, time.monotonic() - self.image_started)
        self.current_image = None

    def saved(self, path, shape):
        """Confirm a readable TIFF header and native XY size without loading pixels."""
        with tifffile.TiffFile(path) as tiff:
            saved_shape = (tiff.pages[0].imagelength, tiff.pages[0].imagewidth)
        if saved_shape != tuple(shape):
            raise ValueError(f"Saved image dimensions {saved_shape} do not match {tuple(shape)}: {path}")
        self.output_files += 1
        self.logger.info("OUTPUT_SAVED | file=%s | W=%d | H=%d | native_XY_preserved=True | "
                         "calibration_snapshot=%s", path, shape[1], shape[0],
                         Path(path).parent / "spatial_calibration.json")

    def __exit__(self, exc_type, exc_value, traceback):
        try:
            if exc_type is not None:
                if self.current_image is not None:
                    self.fail_image(str(exc_value) or exc_type.__name__)
                self.status = "CANCELLED" if issubclass(exc_type, KeyboardInterrupt) else "FAILED"
                self.logger.error("RUN_ABORTED", exc_info=(exc_type, exc_value, traceback))
            elif not self.files:
                self.status = "NO_INPUT"
            elif self.failed:
                self.status = "PARTIAL" if self.completed else "FAILED"
            else:
                self.status = "SUCCESS"
            self.logger.info("FINISHED | status=%s | input_images=%d | attempted=%d | completed=%d | "
                             "failed=%d | not_attempted=%d | output_files=%d | elapsed_s=%.3f",
                             self.status, len(self.files), self.attempted, self.completed,
                             self.failed, len(self.files) - self.attempted, self.output_files,
                             time.monotonic() - self.started)
            print(f"Folder status: {self.status}. Log: {self.output / '1_log.log'}")
        finally:
            for handler in self.logger.handlers[:]:
                self.logger.removeHandler(handler)
                handler.close()
        return False
