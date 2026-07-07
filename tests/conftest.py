"""
Shared pytest configuration for FIA-tools tests.

Sets up:
- Stubs for heavy JVM/GPU dependencies (imagej, scyjava, stardist, csbdeep)
  before any script module is imported.
- sys.path so that fia-tools/ scripts are importable.
- Loads each numerically-named script module via importlib and registers it
  in sys.modules under a clean alias so tests can reference it.
- Common session/function-scoped fixtures.
"""

import importlib.util
import json
import os
import sys
from unittest.mock import MagicMock

import pytest

# ── 1. Stub heavy dependencies ─────────────────────────────────────────────────
# Must happen BEFORE any script module is loaded so that the `import imagej`
# etc. statements inside those scripts resolve to our mocks.
for _name in (
    "imagej",
    "scyjava",
    "stardist",
    "stardist.models",
    "csbdeep",
    "csbdeep.utils",
):
    if _name not in sys.modules:
        sys.modules[_name] = MagicMock()

# ── 2. Extend sys.path ─────────────────────────────────────────────────────────
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SCRIPTS_DIR = os.path.join(_ROOT, "fia-tools")
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

# ── 3. Load numerically-named script modules ───────────────────────────────────
def _load_script(alias: str, filename: str):
    """Load a script by file path and register it in sys.modules under *alias*."""
    path = os.path.join(_SCRIPTS_DIR, filename)
    spec = importlib.util.spec_from_file_location(alias, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[alias] = mod
    spec.loader.exec_module(mod)
    return mod


select_channels_mod = _load_script("select_channels", "1_select_channels.py")
nuclei_mask_mod = _load_script("nuclei_mask_generation", "2_nuclei_mask_generation.py")
foci_mask_mod = _load_script("foci_mask_generation", "3_foci_mask_generation.py")
foci_quant_mod = _load_script("foci_quantification", "4_foci_quantification.py")

# ── 4. Fixtures ────────────────────────────────────────────────────────────────
DATA_DIR = os.path.join(_ROOT, "data")


@pytest.fixture(scope="session")
def data_dir() -> str:
    """Absolute path to the workspace *data/* folder (contains foci_assay/)."""
    return DATA_DIR


@pytest.fixture
def test_input_json(tmp_path) -> str:
    """
    A temporary input_paths.json whose *paths_to_files* list contains the
    absolute path to the workspace *data/* folder.
    """
    json_path = tmp_path / "input_paths.json"
    json_path.write_text(json.dumps({"paths_to_files": [DATA_DIR]}))
    return str(json_path)
