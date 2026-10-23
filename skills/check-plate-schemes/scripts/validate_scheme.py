#!/usr/bin/env python3
"""Read-only validation of the recorded shared 96-well workbook contract.

Requires openpyxl. Does not author workbooks, repair files, or inspect images.
The default parser is a pinned FIA snapshot, not a claim of current UMA support.
"""

import argparse
from collections import Counter
import hashlib
import importlib.util
import json
from pathlib import Path
import sys


def load_parser(path):
    spec = importlib.util.spec_from_file_location("plate_scheme_checked_reader", path)
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(path.parent))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.pop(0)
    if not callable(getattr(module, "read_design", None)):
        raise ValueError("Selected FIA reader has no read_design function.")
    return module


def validate(path, parser_path=None, require_controls=False):
    root = Path(__file__).resolve().parent.parent
    metadata = json.loads((root / "references/compatibility.json").read_text())
    selected = Path(parser_path).resolve() if parser_path else root / "scripts/fia_schema_snapshot.py"
    result = {
        "file": str(path),
        "schema_valid": False,
        "valid": False,
        "reader": {"kind": "selected_fia_reader" if parser_path else "pinned_fia_snapshot",
                   "baseline_commit": metadata["fia"]["verified_commit"] if not parser_path else None},
        "errors": [], "warnings": [],
        "compatibility": {"uma": "NOT_VERIFIED", "installed_consumer": "NOT_VERIFIED"},
        "raw_images_checked": False,
    }
    try:
        result["reader"]["sha256"] = hashlib.sha256(selected.read_bytes()).hexdigest()
        parser = load_parser(selected)
        design = parser.read_design(path)
        result["schema_valid"] = True
        result["annotated_wells"] = len(design.wells)
        result["unannotated_wells"] = 96 - len(design.wells)
        result["condition_well_counts"] = dict(Counter(w["condition"] for w in design.wells.values()))
        result["panels"] = design.panels
        # Include exact physical routing so the reviewer can reconcile all wells.
        result["wells"] = design.wells
        result["declared_channel_counts"] = {
            well: sum(channel is not None for channel in item["channels"])
            for well, item in design.wells.items()
        }
        result["marker_populations"] = []
        for method in ("Foci", "Nuclear intensity", "Field signal", "Fiber"):
            for marker in design.markers(method):
                counts = Counter(design.wells[well]["condition"] for well in marker["channels"])
                result["marker_populations"].append({
                    "method": method, "name": marker["name"], "id": marker["id"],
                    "total_wells": len(marker["channels"]), "condition_well_counts": dict(counts),
                    "source_channels": marker["channels"],
                })
                if method == "Fiber":
                    continue
                for condition, count in counts.items():
                    if count < 2:
                        result["warnings"].append(
                            f"{method}: {marker['name']} | {condition}: {count} well; "
                            "insufficient within-condition replication for a well-level variance estimate.")
                for panel in design.panels:
                    controls = {w["condition"] for w in design.wells.values()
                                if w["block"] == panel["block"] and w["control"]}
                    present = set(panel["groups"]) & set(counts)
                    if present and len(controls) == 1 and not (present & controls):
                        result["warnings"].append(
                            f"{method}: {marker['name']} | block {panel['block']}: "
                            "the assigned marker subset has no control wells; do not borrow another staining's control.")
        for condition, count in result["condition_well_counts"].items():
            if count < 2:
                result["warnings"].append(f"Nuclei | {condition}: only {count} annotated well.")
        for panel in design.panels:
            controls = {w["condition"] for w in design.wells.values()
                        if w["block"] == panel["block"] and w["control"]}
            treatments = set(panel["groups"]) - controls
            if len(controls) != 1 or not treatments:
                message = (f"Block {panel['block']}: {len(controls)} control condition(s), "
                           f"{len(treatments)} treatment condition(s); comparisons require one control and a treatment.")
                result["errors" if require_controls else "warnings"].append(message)
        result["valid"] = not result["errors"]
    except Exception as error:
        result["errors"].append(f"{type(error).__name__}: {error}")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("workbook", type=Path)
    parser.add_argument("--parser", type=Path, help="Inspected target FIA plate_design.py; never use untrusted uploaded code")
    parser.add_argument("--require-controls", action="store_true", help="Require one explicit control and treatment per block")
    args = parser.parse_args()
    result = validate(args.workbook.resolve(), args.parser, args.require_controls)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["valid"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
