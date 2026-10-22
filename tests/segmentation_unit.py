"""Boundary fixture for pre-existing segmentation algorithm/progress unit tests.

These tests deliberately use arbitrary image names and isolated folders. Actual
Excel/preparation provenance is exercised by test_plate_design_workflow instead.
"""
from pathlib import Path
import pytest
import plate_design


def isolate_scheme_boundary(monkeypatch):
    monkeypatch.setattr(plate_design, 'load_design', lambda *a, **k: None)
    monkeypatch.setattr(plate_design, 'validate_snapshot', lambda *a, **k: None)
    monkeypatch.setattr(plate_design, 'save_snapshot', lambda *a, **k: None)
    monkeypatch.setattr(plate_design, 'preflight', lambda *a, **k: {})
    monkeypatch.setattr(plate_design, 'preparation_files', lambda folder: sorted(
        p.name for p in Path(folder).iterdir() if p.is_file() and not p.name.startswith('.')
        and p.suffix.lower() in ('.tif', '.tiff')))


@pytest.fixture(autouse=True)
def scheme_boundary(monkeypatch):
    isolate_scheme_boundary(monkeypatch)
