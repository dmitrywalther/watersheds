"""Re-read every native pixel and mask of active ZSTD COGs; never open originals."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import sys
import time

for key in ('PROJ_LIB', 'PROJ_DATA'):
    os.environ.pop(key, None)
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import numpy as np
import rasterio
from rasterio.crs import CRS
from rasterio.windows import Window

from config import DEM_DIR, DEM_FILES
from services.cog_preparation import BLOCK_SIZE, COG_ENV


HASH_ORDER = '512x512 tiles in row-major order; C-order native bytes within each tile'


def timestamp():
    return datetime.now(timezone.utc).isoformat()


def emit(event):
    print(json.dumps(event, ensure_ascii=True, allow_nan=False), flush=True)


def signature(path):
    info = path.stat()
    return {'size_bytes': info.st_size, 'mtime_ns': info.st_mtime_ns,
            'ctime_ns': info.st_ctime_ns, 'file_id': info.st_ino,
            'file_attributes': getattr(info, 'st_file_attributes', None)}


def saved_nodata(value):
    return value if value is None or math.isfinite(value) else str(value)


def active_files():
    allowed_root = (DEM_DIR / 'cog').resolve()
    entries = []
    for source_id, spec in DEM_FILES.items():
        for role in ('flow_dir', 'flow_acc'):
            path = Path(spec[role]).resolve()
            # Refuse a configuration fallback to an original raster, before opening it.
            if not path.is_relative_to(allowed_root) or path.parent.name != 'zstd':
                raise ValueError(f'Active raster is not an approved ZSTD COG path: {source_id}/{role}')
            entries.append({'source_id': source_id, 'role': role, 'path': str(path)})
    if len(entries) != 18 or len({entry['path'] for entry in entries}) != 18:
        raise ValueError('Expected exactly 18 distinct active COGs for nine sources.')
    return entries


def verify_file(entry):
    started = time.perf_counter()
    path = Path(entry['path'])
    manifest_path = path.with_suffix(path.suffix + '.cog.json')
    result = {**entry, 'manifest': str(manifest_path), 'started_utc': timestamp(), 'passed': False}
    result['stat_before'] = signature(path)
    result['manifest_stat_before'] = signature(manifest_path)
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    if Path(manifest['output']).resolve() != path:
        raise ValueError(f'Manifest output does not identify {path}')
    if manifest.get('hash_order') != HASH_ORDER:
        raise ValueError(f'Unsupported manifest hash order: {path}')
    if result['stat_before']['size_bytes'] != manifest['output_size_bytes']:
        raise ValueError(f'COG size differs from its conversion manifest: {path}')
    for flag in ('all_native_pixels_validated', 'all_native_masks_validated'):
        if manifest.get(flag) is not True:
            raise ValueError(f'Conversion manifest lacks {flag}: {path}')
    for kind in ('native', 'mask'):
        source_digest = manifest[f'source_{kind}_sha256']
        output_digest = manifest[f'output_{kind}_sha256']
        if source_digest != output_digest or len(output_digest) != 64:
            raise ValueError(f'Manifest source/output {kind} digests differ: {path}')
        int(output_digest, 16)
    native_digest, mask_digest = hashlib.sha256(), hashlib.sha256()
    valid_cells = 0
    checked_cells = 0
    with rasterio.Env(**{**COG_ENV, 'GDAL_DISABLE_READDIR_ON_OPEN': 'EMPTY_DIR'}), rasterio.open(path) as dataset:
        metadata = {'shape': list(dataset.shape), 'count': dataset.count, 'dtype': dataset.dtypes[0],
                    'nodata': saved_nodata(dataset.nodata), 'transform': list(dataset.transform)}
        for key, value in metadata.items():
            if value != manifest[key]:
                raise ValueError(f'COG metadata differs at {key}: {path}')
        if dataset.crs != CRS.from_user_input(manifest['crs']):
            raise ValueError(f'COG CRS differs from its conversion manifest: {path}')
        structure = dataset.tags(ns='IMAGE_STRUCTURE')
        if structure.get('LAYOUT') != 'COG' or structure.get('COMPRESSION') != 'ZSTD':
            raise ValueError(f'Active raster must remain a ZSTD COG: {path}')
        if dataset.count != 1 or dataset.block_shapes != [(BLOCK_SIZE, BLOCK_SIZE)]:
            raise ValueError(f'Unexpected COG band count or block shape: {path}')
        result['metadata'] = {**metadata, 'crs': str(dataset.crs), 'image_structure': structure,
                              'overviews': dataset.overviews(1)}
        last_progress = time.perf_counter()
        for row in range(0, dataset.height, BLOCK_SIZE):
            for col in range(0, dataset.width, BLOCK_SIZE):
                window = Window(col, row, min(BLOCK_SIZE, dataset.width-col),
                                min(BLOCK_SIZE, dataset.height-row))
                values = dataset.read(1, window=window)
                mask = dataset.read_masks(1, window=window)
                native_digest.update(values.tobytes(order='C'))
                mask_digest.update(mask.tobytes(order='C'))
                checked_cells += values.size
                valid_cells += int(np.count_nonzero(mask))
            if time.perf_counter() - last_progress >= 10 or row + BLOCK_SIZE >= dataset.height:
                emit({'phase': 'hashing', 'source_id': entry['source_id'], 'role': entry['role'],
                      'rows_done': min(row + BLOCK_SIZE, dataset.height), 'rows': dataset.height})
                last_progress = time.perf_counter()
    result.update(native_sha256=native_digest.hexdigest(), mask_sha256=mask_digest.hexdigest(),
                  expected_native_sha256=manifest['output_native_sha256'],
                  expected_mask_sha256=manifest['output_mask_sha256'],
                  checked_cells=checked_cells, valid_cells=valid_cells,
                  stat_after=signature(path), manifest_stat_after=signature(manifest_path),
                  elapsed_seconds=time.perf_counter()-started, finished_utc=timestamp())
    result['checks'] = {
        'native_hash_matches_source_and_output': result['native_sha256'] == manifest['output_native_sha256'],
        'mask_hash_matches_source_and_output': result['mask_sha256'] == manifest['output_mask_sha256'],
        'all_cells_read': checked_cells == manifest['total_cells'],
        'valid_cell_count_matches': valid_cells == manifest['valid_cells'],
        'cog_stat_unchanged': result['stat_before'] == result['stat_after'],
        'manifest_stat_unchanged': result['manifest_stat_before'] == result['manifest_stat_after'],
        'metadata_grid_size_match': True,
    }
    result['passed'] = all(result['checks'].values())
    return result


def write_report(path, report):
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + '\n',
                         encoding='utf-8')
    os.replace(temporary, path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path,
                        default=ROOT / 'artifacts' / 'cog-cleanup' / 'cog_integrity.json')
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(f'Preserve the existing integrity report: {args.output}')
    entries = active_files()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    report = {'started_utc': timestamp(), 'all_passed': False, 'complete': False,
              'scope': 'Fresh full-resolution SHA256 scan of the 18 active ZSTD COGs and their masks. Original TIFFs are never opened, modified, or deleted.',
              'hash_order': HASH_ORDER, 'total_files': len(entries), 'completed_files': 0,
              'rasterio_version': rasterio.__version__, 'gdal_version': rasterio.__gdal_version__,
              'gdal_env': {**COG_ENV, 'GDAL_DISABLE_READDIR_ON_OPEN': 'EMPTY_DIR'}, 'files': []}
    write_report(args.output, report)
    for index, entry in enumerate(entries, start=1):
        emit({'phase': 'file_started', 'index': index, 'total': len(entries), **entry})
        try:
            result = verify_file(entry)
        except Exception as exc:
            result = {**entry, 'passed': False, 'error': str(exc), 'finished_utc': timestamp()}
        report['files'].append(result)
        report['completed_files'] = index
        write_report(args.output, report)
        emit({'phase': 'file_complete', 'index': index, 'total': len(entries),
              'source_id': entry['source_id'], 'role': entry['role'], 'passed': result['passed'],
              'elapsed_seconds': result.get('elapsed_seconds'), 'error': result.get('error')})
    report['complete'] = True
    report['all_passed'] = all(item['passed'] for item in report['files'])
    report['finished_utc'] = timestamp()
    report['checked_bytes'] = sum(item.get('stat_before', {}).get('size_bytes', 0) for item in report['files'])
    write_report(args.output, report)
    emit({'phase': 'complete', 'all_passed': report['all_passed'],
          'completed_files': len(report['files']), 'report': str(args.output)})
    if not report['all_passed']:
        raise SystemExit('Fresh COG integrity verification failed.')


if __name__ == '__main__':
    main()
