"""Persistent, experiment-scoped processing exclusions and portable run snapshots."""

import csv
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from assay_layout import ASSAY_DIR
from run_resources import temporary_path

MANIFEST = 'excluded_images.json'
COLUMNS = ['Image_key', 'Source_file', 'Source_sha256', 'Stage', 'Reason', 'Detail', 'Excluded_UTC']
TABLE = 'Processing_Exclusions.csv'


def image_key(value):
    """Keep full acquisition/field identity; never exclude an entire well or Seq."""
    name = str(value).replace('\\', '/').rsplit('/', 1)[-1]
    stem = Path(name).stem
    if stem.startswith('processed_') and stem.endswith('_foci_projection'):
        stem = stem.removeprefix('processed_')
    return re.sub(r'_(?:nuclei_projection(?:_StarDist_processed(?:_processed)?)?|foci_projection)$', '', stem)


def registry_path(folder):
    folder = Path(folder)
    for parent in (folder, *folder.parents):
        if parent.name == ASSAY_DIR:
            return parent / MANIFEST
    return folder / ASSAY_DIR / MANIFEST


def read_manifest(path, files=None):
    path = Path(path)
    if not path.exists():
        return {}
    content = path.read_bytes()
    if files is not None:
        files[path] = content
    payload = json.loads(content)
    if not isinstance(payload, dict) or payload.get('schema_version') != 1:
        raise ValueError(f'Invalid exclusion manifest: {path}')
    records = payload.get('images')
    if not isinstance(records, dict):
        raise ValueError(f'Invalid exclusion records: {path}')  # noqa: TRY004 - malformed file content
    for key, record in records.items():
        if (not key or not isinstance(record, dict) or record.get('Image_key') != key
                or not all(isinstance(record.get(field), str) for field in COLUMNS)
                or not record['Reason']):
            raise ValueError(f'Invalid exclusion entry {key!r}: {path}')
    return records


def load_exclusions(folder, files=None):
    """Union portable history and current experiment decisions (including old runs)."""
    records = read_manifest(Path(folder) / MANIFEST, files)
    records.update(read_manifest(registry_path(folder), files))
    return records


def write_manifest(path, records):
    path = Path(path)
    temporary = temporary_path(path, path.with_name('.' + path.name + '.tmp'))
    temporary.write_text(json.dumps({'schema_version': 1, 'images': records},
                                    indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    temporary.replace(path)


def write_snapshot(folder, records):
    folder = Path(folder)
    write_manifest(folder / MANIFEST, records)
    with (folder / TABLE).open('w', newline='', encoding='utf-8-sig') as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(records.values())


def exclude_image(assay_folder, source, reason, detail, stage='StarDist'):
    """Commit the decision before continuing; concurrent registry writers fail closed."""
    assay = Path(assay_folder)
    manifest = assay / MANIFEST
    lock = assay / '.excluded_images.lock'
    lock.mkdir()
    try:
        records = read_manifest(manifest)
        key = image_key(source)
        if key not in records:
            record = dict(zip(COLUMNS, [key, str(Path(source).resolve()),
                hashlib.sha256(Path(source).read_bytes()).hexdigest(), stage,
                str(reason), str(detail), datetime.now(timezone.utc).isoformat()]))
            records[key] = record
            write_manifest(manifest, records)
            logs = assay / 'logs'
            logs.mkdir(exist_ok=True)
            with (logs / '2_excluded_images.log').open('a', encoding='utf-8') as handle:
                handle.write(json.dumps(record, ensure_ascii=False) + '\n')
        return records
    finally:
        lock.rmdir()


def filter_bundle(bundle, records):
    """Filter validated measurement dictionaries without rewriting archived source bytes."""
    bundle['images'] = {key: row for key, row in bundle['images'].items()
                        if image_key(key) not in records}
    bundle['nuclei'] = {key: row for key, row in bundle['nuclei'].items()
                        if image_key(key[0]) not in records}
    bundle['processing_exclusions'] = records
    return bundle
