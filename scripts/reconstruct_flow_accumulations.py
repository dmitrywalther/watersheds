"""Recover MERIT/ALOS accumulation GeoTIFFs from verified COGs without touching cloud data."""

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import time

for name in ('PROJ_LIB', 'PROJ_DATA'):
    os.environ.pop(name, None)
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import numpy as np
import rasterio
from rasterio.enums import MaskFlags
from rasterio.windows import Window

from config import DEM_DIR, DEM_FILES, is_cloud_placeholder
from services.cog_preparation import BLOCK_SIZE, COG_ENV
from verify_cog_integrity import HASH_ORDER, signature, write_report


TARGET_IDS = ('MERIT_3sec', 'ALOS_local')
DESTINATION_ROOT = DEM_DIR / 'recovered_originals'
REPORT_PATH = ROOT / 'artifacts' / 'cog-restore' / 'reconstructed.json'
OPTIONS = {'driver': 'GTiff', 'tiled': True, 'blockxsize': 256, 'blockysize': 256,
           'compress': 'DEFLATE', 'predictor': 1, 'zlevel': 4, 'BIGTIFF': 'YES', 'NUM_THREADS': '1'}


def timestamp():
    return datetime.now(timezone.utc).isoformat()


def emit(phase, **fields):
    print(json.dumps({'phase': phase, **fields}, ensure_ascii=True), flush=True)


def windows(height, width):
    for row in range(0, height, BLOCK_SIZE):
        for col in range(0, width, BLOCK_SIZE):
            yield Window(col, row, min(BLOCK_SIZE, width-col), min(BLOCK_SIZE, height-row))


def reconstruct(source_id):
    started = time.perf_counter()
    source = Path(DEM_FILES[source_id]['flow_acc']).resolve()
    if not source.is_relative_to((DEM_DIR / 'cog').resolve()) or source.parent.name != 'zstd':
        raise ValueError('Recovery must read an active local ZSTD COG, never an original TIFF.')
    if is_cloud_placeholder(source):
        raise ValueError('The source COG must be fully local.')
    cog_manifest_path = source.with_suffix('.tif.cog.json')
    cog_manifest = json.loads(cog_manifest_path.read_text(encoding='utf-8'))
    destination = DESTINATION_ROOT / source.name
    if destination.exists():
        raise FileExistsError(f'Preserve existing reconstruction: {destination}')
    original_preparation_path = DEM_DIR / (source.name + '.preparation.json')
    preparation_bytes = original_preparation_path.read_bytes()
    preparation = json.loads(preparation_bytes)
    if cog_manifest['hash_order'] != HASH_ORDER:
        raise ValueError('Unexpected COG verification hash order.')
    if Path(cog_manifest['output']).resolve() != source:
        raise ValueError('COG manifest output path mismatch.')
    before = signature(source)
    manifest_before = signature(cog_manifest_path)
    preparation_before = signature(original_preparation_path)
    if before['size_bytes'] != cog_manifest['output_size_bytes']:
        raise ValueError('COG size differs from its manifest.')
    for kind in ('native', 'mask'):
        if cog_manifest[f'source_{kind}_sha256'] != cog_manifest[f'output_{kind}_sha256']:
            raise ValueError('COG manifest does not attest preservation of original values and masks.')
    DESTINATION_ROOT.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.reconstruction-', dir=DESTINATION_ROOT) as temporary:
        staging = Path(temporary) / source.name
        environment = {**COG_ENV, 'GDAL_DISABLE_READDIR_ON_OPEN': 'EMPTY_DIR', 'GDAL_NUM_THREADS': '1'}
        with rasterio.Env(**environment), rasterio.open(source) as src:
            if (src.count != 1 or src.dtypes != ('uint32',) or src.nodata != 4294967295
                    or src.mask_flag_enums != ([MaskFlags.nodata],)):
                raise ValueError('Recovery expects uint32 accumulation with a NoData-derived mask.')
            profile = {**OPTIONS, 'count': 1, 'dtype': src.dtypes[0], 'nodata': src.nodata,
                       'width': src.width, 'height': src.height, 'crs': src.crs, 'transform': src.transform}
            metadata = {'shape': list(src.shape), 'dtype': src.dtypes[0], 'nodata': int(src.nodata),
                        'crs': str(src.crs), 'transform': list(src.transform)}
            for key, value in metadata.items():
                if value != preparation[key] or value != cog_manifest[key]:
                    raise ValueError(f'Original preparation/COG metadata mismatch: {key}')
            required_space = src.width * src.height * 4 + 128 * 1024 * 1024
            if shutil.disk_usage(DESTINATION_ROOT).free < required_space:
                raise ValueError('Insufficient scratch space for lossless reconstruction.')
            emit('reconstruction_started', source_id=source_id, output=str(destination))
            last_progress = time.perf_counter()
            with rasterio.open(staging, 'w', **profile) as dst:
                for window in windows(src.height, src.width):
                    dst.write(src.read(1, window=window), 1, window=window)
                    if time.perf_counter()-last_progress >= 10:
                        emit('writing', source_id=source_id, row=int(window.row_off), rows=src.height)
                        last_progress = time.perf_counter()
                dst.update_tags(**src.tags())
            writing_seconds = time.perf_counter()-started
            native_digest, mask_digest = hashlib.sha256(), hashlib.sha256()
            valid_cells = 0
            total_cells = 0
            maximum = 0
            with rasterio.open(staging) as dst:
                if (src.shape != dst.shape or src.crs != dst.crs or src.transform != dst.transform
                        or src.dtypes != dst.dtypes or src.nodata != dst.nodata or dst.count != 1
                        or dst.block_shapes != [(256, 256)] or dst.overviews(1)):
                    raise ValueError('Reconstructed GeoTIFF metadata or layout mismatch.')
                last_progress = time.perf_counter()
                for window in windows(src.height, src.width):
                    expected = src.read(1, window=window)
                    actual = dst.read(1, window=window)
                    expected_mask = src.read_masks(1, window=window)
                    actual_mask = dst.read_masks(1, window=window)
                    if expected.tobytes() != actual.tobytes() or expected_mask.tobytes() != actual_mask.tobytes():
                        raise ValueError(f'Reconstructed native values or masks differ at {window}.')
                    native_digest.update(actual.tobytes(order='C'))
                    mask_digest.update(actual_mask.tobytes(order='C'))
                    total_cells += actual.size
                    valid_cells += int(np.count_nonzero(actual_mask))
                    valid = actual_mask != 0
                    if np.any(valid):
                        maximum = max(maximum, int(actual[valid].max()))
                    if time.perf_counter()-last_progress >= 10:
                        emit('validating', source_id=source_id, row=int(window.row_off), rows=src.height)
                        last_progress = time.perf_counter()
                image_structure = dst.tags(ns='IMAGE_STRUCTURE')
        after = signature(source)
        if (before != after or manifest_before != signature(cog_manifest_path)
                or preparation_before != signature(original_preparation_path)):
            raise ValueError('Source COG or provenance changed during reconstruction.')
        checks = {
            'every_native_pixel_and_mask_compared': True,
            'native_sha_matches_cog_manifest': native_digest.hexdigest() == cog_manifest['output_native_sha256'],
            'mask_sha_matches_cog_manifest': mask_digest.hexdigest() == cog_manifest['output_mask_sha256'],
            'valid_cells_match_preparation': valid_cells == preparation['valid_cells'] == cog_manifest['valid_cells'],
            'maximum_matches_preparation': maximum == preparation['max_accumulation'],
            'total_cells_match_manifest': total_cells == cog_manifest['total_cells'],
            'source_cog_and_provenance_unchanged': True,
            'metadata_grid_crs_dtype_nodata_preserved': True,
            'compression_matches_original_writer': image_structure.get('COMPRESSION') == 'DEFLATE'
                and image_structure.get('PREDICTOR', '1') == '1',
        }
        if not all(checks.values()):
            raise ValueError(f'Reconstruction validation failed: {checks}')
        # Same-volume publication cannot overwrite an existing destination.
        os.link(staging, destination)
        report = {'source_id': source_id, 'source_cog': str(source), 'output': str(destination),
                  'created_utc': timestamp(), 'passed': True, 'checks': checks,
                  'byte_identical_tiff_container_claimed': False,
                  'output_size_bytes': destination.stat().st_size,
                  'original_container_size_bytes': cog_manifest['source_size_bytes'],
                  'native_sha256': native_digest.hexdigest(), 'mask_sha256': mask_digest.hexdigest(),
                  'expected_native_sha256': cog_manifest['output_native_sha256'],
                  'expected_mask_sha256': cog_manifest['output_mask_sha256'], 'hash_order': HASH_ORDER,
                  'valid_cells': valid_cells, 'total_cells': total_cells, 'max_accumulation': maximum,
                  'metadata': metadata, 'creation_options': OPTIONS, 'image_structure': image_structure,
                  'source_stat_before': before, 'source_stat_after': after,
                  'output_stat': signature(destination), 'writing_seconds': writing_seconds,
                  'elapsed_seconds': time.perf_counter()-started,
                  'provenance': {'cog_manifest': str(cog_manifest_path),
                                 'original_preparation_manifest': str(original_preparation_path),
                                 'original_preparation_manifest_sha256': hashlib.sha256(preparation_bytes).hexdigest(),
                                 'original_preparation': preparation,
                                 'creation_parameters_source': str(ROOT / 'services' / 'flow_preparation.py')}}
    emit('reconstruction_complete', source_id=source_id, passed=True,
         output=str(destination), bytes=report['output_size_bytes'])
    return report


def main():
    if REPORT_PATH.exists():
        raise FileExistsError(f'Preserve existing reconstruction report: {REPORT_PATH}')
    for source_id in TARGET_IDS:
        if (DESTINATION_ROOT / Path(DEM_FILES[source_id]['flow_acc']).name).exists():
            raise FileExistsError(f'Preserve existing reconstruction for {source_id}')
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    report = {'started_utc': timestamp(), 'complete': False, 'all_passed': False, 'files': [],
              'scope': 'Two locally generated FlowAcc rasters recovered from verified active COGs; cloud files and application config untouched. Values/masks exact; TIFF container identity not claimed.',
              'rasterio_version': rasterio.__version__, 'gdal_version': rasterio.__gdal_version__}
    write_report(REPORT_PATH, report)
    for source_id in TARGET_IDS:
        report['files'].append(reconstruct(source_id))
        write_report(REPORT_PATH, report)
    report.update(complete=True, all_passed=all(item['passed'] for item in report['files']),
                  finished_utc=timestamp())
    write_report(REPORT_PATH, report)
    emit('complete', all_passed=report['all_passed'], report=str(REPORT_PATH))


if __name__ == '__main__':
    main()
