"""Create a lossless COG, verify every native pixel, then publish without overwriting."""

from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import tempfile
import time
import xml.etree.ElementTree as ET

import numpy as np
import rasterio
from rasterio.enums import MaskFlags
from rasterio.shutil import copy as raster_copy
from rasterio.windows import Window

from config import is_cloud_placeholder


BLOCK_SIZE = 512
DISK_RESERVE_BYTES = 512 * 1024 * 1024
COG_ENV = {
    'GDAL_CACHEMAX': 64 * 1024 * 1024,
    'GDAL_NUM_THREADS': '2',
    'GDAL_PAM_ENABLED': 'NO',
    # TRUE still probes .ovr explicitly and can hydrate cloud placeholders.
    # External masks are attached explicitly through a temporary VRT below.
    'GDAL_DISABLE_READDIR_ON_OPEN': 'EMPTY_DIR',
    'GDAL_TIFF_INTERNAL_MASK': True,
}


def _notify(callback, phase, **values):
    if callback:
        callback({'phase': phase, **values})


def _copy_cog(source, staging, options):
    raster_copy(str(source), str(staging), driver='COG', strict=True, **options)


def _staged_disk_budget(width, height, itemsize):
    """Budget tiled data plus a full byte per mask pixel, even for nodata masks.

    The COG CreateCopy path writes the output directly and uses scratch for
    generated overviews, not a separate uncompressed native image:
    https://gdal.org/en/stable/drivers/raster/cog.html
    Tile padding, 1% compression/table overhead and a fixed reserve deliberately
    overestimate lossless ZSTD/DEFLATE storage without assuming compressibility.
    """
    def padded_bytes(level_width, level_height):
        tiles = math.ceil(level_width / BLOCK_SIZE) * math.ceil(level_height / BLOCK_SIZE)
        return tiles * BLOCK_SIZE * BLOCK_SIZE * (itemsize + 1)

    native = padded_bytes(width, height)
    overviews = 0
    while max(width, height) > BLOCK_SIZE:
        width, height = math.ceil(width / 2), math.ceil(height / 2)
        overviews += padded_bytes(width, height)
    return {
        'native_padded_bytes': native,
        'overview_padded_bytes': overviews,
        'native_required_bytes': native + math.ceil(native / 100) + DISK_RESERVE_BYTES,
    }


def _staged_final_required_bytes(budget, measured_native_bytes):
    # The native stage already occupies disk when free space is rechecked.
    # Reserve twice its measured size for the final compressed base, and raw
    # padded overviews twice (temporary pyramid plus final output). This remains
    # conservative even when categorical overview compression is much worse
    # than native compression. Both passes use identical native tile options.
    overviews = budget['overview_padded_bytes']
    return (2 * measured_native_bytes
            + 2 * (overviews + math.ceil(overviews / 100)) + DISK_RESERVE_BYTES)


def _check_disk_budget(directory, required_bytes, phase, checks):
    free_bytes = shutil.disk_usage(directory).free
    checks.append({'phase': phase, 'required_bytes': required_bytes, 'free_bytes': free_bytes})
    if free_bytes < required_bytes:
        raise ValueError(f'Недостаточно места ({phase}): нужно около {required_bytes / 1024**3:.1f} ГиБ, '
                         f'свободно {free_bytes / 1024**3:.1f} ГиБ.')


def _same_nodata(left, right):
    return left == right or (left is not None and right is not None
                             and math.isnan(left) and math.isnan(right))


def _semantic_metadata(dataset):
    return {
        'scales': list(dataset.scales), 'offsets': list(dataset.offsets),
        'units': list(dataset.units), 'descriptions': list(dataset.descriptions),
        'color_interpretation': [value.name for value in dataset.colorinterp],
        'dataset_tags': dataset.tags(), 'band_tags': dataset.tags(1),
    }


def _verify_native(source, output, callback):
    source_hash, output_hash = hashlib.sha256(), hashlib.sha256()
    source_mask_hash, output_mask_hash = hashlib.sha256(), hashlib.sha256()
    valid_cells = 0
    with rasterio.open(source) as src, rasterio.open(output) as dst:
        if (src.shape != dst.shape or src.count != dst.count or src.dtypes != dst.dtypes
                or src.crs != dst.crs or src.transform != dst.transform
                or not _same_nodata(src.nodata, dst.nodata)):
            raise ValueError('Сетка, тип данных или NoData изменились при конвертации COG.')
        metadata = _semantic_metadata(src)
        if metadata != _semantic_metadata(dst):
            raise ValueError('Метаданные значений или описания растра изменились при конвертации COG.')
        structure = dst.tags(ns='IMAGE_STRUCTURE')
        if structure.get('LAYOUT') != 'COG' or not dst.is_tiled:
            raise ValueError('Выходной файл не имеет структуры COG.')
        if any(shape != (BLOCK_SIZE, BLOCK_SIZE) for shape in dst.block_shapes):
            raise ValueError('COG записан с неожиданным размером блоков.')
        overviews = dst.overviews(1)
        if max(src.shape) > BLOCK_SIZE and not overviews:
            raise ValueError('В COG отсутствуют внутренние обзорные уровни.')
        if Path(str(output) + '.ovr').exists() or Path(str(output) + '.msk').exists():
            raise ValueError('Обзорные уровни и маска должны храниться внутри COG.')
        # Tile order bounds memory independently of raster width and avoids
        # repeatedly decoding an entire wide row of compressed blocks.
        for row in range(0, src.height, BLOCK_SIZE):
            for col in range(0, src.width, BLOCK_SIZE):
                window = Window(col, row, min(BLOCK_SIZE, src.width-col),
                                min(BLOCK_SIZE, src.height-row))
                expected = src.read(1, window=window)
                actual = dst.read(1, window=window)
                expected_bytes = expected.tobytes(order='C')
                actual_bytes = actual.tobytes(order='C')
                if expected_bytes != actual_bytes:
                    raise ValueError(f'Значения COG отличаются от оригинала: {window}.')
                source_hash.update(expected_bytes)
                output_hash.update(actual_bytes)
                expected_mask = src.read_masks(1, window=window)
                actual_mask = dst.read_masks(1, window=window)
                if not np.array_equal(expected_mask, actual_mask):
                    raise ValueError(f'Маска COG отличается от оригинала: {window}.')
                source_mask_hash.update(expected_mask.tobytes(order='C'))
                output_mask_hash.update(actual_mask.tobytes(order='C'))
                valid_cells += int(np.count_nonzero(expected_mask))
            _notify(callback, 'validate_native', rows_done=min(row+BLOCK_SIZE, src.height),
                    rows=src.height, output=str(output))
        return {
            'shape': list(src.shape), 'count': src.count, 'dtype': src.dtypes[0],
            'nodata': src.nodata if src.nodata is None or math.isfinite(src.nodata) else str(src.nodata),
            'crs': str(src.crs), 'transform': list(src.transform),
            'valid_cells': valid_cells, 'total_cells': src.width * src.height,
            'all_native_pixels_validated': True, 'all_native_masks_validated': True,
            'semantic_metadata_validated': True, 'semantic_metadata': metadata,
            'source_native_sha256': source_hash.hexdigest(),
            'output_native_sha256': output_hash.hexdigest(),
            'source_mask_sha256': source_mask_hash.hexdigest(),
            'output_mask_sha256': output_mask_hash.hexdigest(),
            'hash_order': '512x512 tiles in row-major order; C-order native bytes within each tile',
            'image_structure': structure, 'block_shapes': [list(shape) for shape in dst.block_shapes],
            'overviews': overviews,
            'structural_checks': ['GDAL LAYOUT=COG', 'tiled 512x512',
                                  'internal overviews when raster exceeds block size',
                                  'no external overview or mask files'],
            'formal_cog_validator_run': False,
        }


def _file_signature(path):
    info = path.stat()
    return info.st_size, info.st_mtime_ns


def _source_file_signatures(source):
    # File stats do not read placeholder contents. Do not use Dataset.files:
    # GDAL's file-list discovery can open a neighboring overview raster.
    candidates = [source] + [Path(str(source) + suffix)
                             for suffix in ('.msk', '.ovr', '.aux.xml', '.vat.dbf')]
    return {str(path): _file_signature(path) for path in candidates if path.is_file()}


def _source_view(source, external_mask, directory):
    """Expose a downloaded external mask without discovering any sidecars."""
    if external_mask is None:
        return source
    with rasterio.open(source) as src, rasterio.open(external_mask) as mask:
        intrinsic_flags = src.mask_flag_enums[0]
        if (not intrinsic_flags or any(flag not in (MaskFlags.all_valid, MaskFlags.nodata)
                                       for flag in intrinsic_flags)):
            raise ValueError('Одновременно найдены внутренняя и внешняя маски; их приоритет неоднозначен.')
        flags = mask.tags().get('INTERNAL_MASK_FLAGS_1')
        if (mask.count != 1 or mask.shape != src.shape or mask.dtypes != ('uint8',)
                or flags not in ('0', '2')):
            raise ValueError('Не поддерживается структура внешней маски .msk; маска не будет отброшена.')
        width, height = src.width, src.height
    view = directory / 'source-with-mask.vrt'
    raster_copy(str(source), str(view), driver='VRT', strict=True)
    tree = ET.parse(view)
    root = tree.getroot()
    for existing in list(root.findall('MaskBand')) + list(root.findall('./VRTRasterBand/MaskBand')):
        parent = root if existing in list(root) else root.find('VRTRasterBand')
        parent.remove(existing)
    band = ET.SubElement(ET.SubElement(root, 'MaskBand'), 'VRTRasterBand', dataType='Byte')
    simple = ET.SubElement(band, 'SimpleSource')
    ET.SubElement(simple, 'SourceFilename', relativeToVRT='0').text = str(external_mask)
    ET.SubElement(simple, 'SourceBand').text = '1'
    for name in ('SrcRect', 'DstRect'):
        ET.SubElement(simple, name, xOff='0', yOff='0', xSize=str(width), ySize=str(height))
    tree.write(view, encoding='utf-8', xml_declaration=True)
    return view


def prepare_cog(source, destination, *, compression='DEFLATE', on_progress=None, staged=False):
    """Preserve the native grid exactly; nearest overviews are for viewing only.

    The source is opened read-only. Publication uses same-volume hard links to
    fail atomically if an output appears meanwhile. Original files are retained.
    staged=True first creates an unpublished compressed native-only copy and
    rechecks capacity using its actual size before generating the final pyramid.
    """
    source, destination = Path(source).resolve(), Path(destination).resolve()
    manifest = destination.with_suffix(destination.suffix + '.cog.json')
    if destination.exists() or manifest.exists():
        raise FileExistsError(f'Результат уже существует: {destination}')
    compression = compression.upper()
    if compression not in ('DEFLATE', 'ZSTD'):
        raise ValueError('Поддерживаются сжатия DEFLATE и ZSTD.')
    if is_cloud_placeholder(source):
        raise ValueError('Сначала скачайте исходный растр из облака.')
    external_mask = Path(str(source) + '.msk')
    if external_mask.is_file():
        if is_cloud_placeholder(external_mask):
            raise ValueError('Сначала скачайте внешнюю маску .msk из облака; игнорировать её нельзя.')
    else:
        external_mask = None
    source_signature = _file_signature(source)
    source_files = _source_file_signatures(source)
    started = time.perf_counter()
    destination.parent.mkdir(parents=True, exist_ok=True)
    disk_checks = []
    with rasterio.Env(**COG_ENV), rasterio.open(source) as src:
        if src.count != 1 or not src.crs:
            raise ValueError('Нужен одноканальный растр с пространственной привязкой.')
        dtype = np.dtype(src.dtypes[0])
        if dtype.kind not in ('u', 'i', 'f'):
            raise ValueError('Не поддерживается тип данных исходного растра.')
        # The default path retains its conservative, compression-independent guard.
        disk_budget = _staged_disk_budget(src.width, src.height, dtype.itemsize) if staged else None
        required_bytes = (disk_budget['native_required_bytes'] if staged
                          else 3 * src.width * src.height * dtype.itemsize + DISK_RESERVE_BYTES)
        _check_disk_budget(destination.parent, required_bytes,
                           'compressed_native' if staged else 'direct', disk_checks)
        options = {
            'COMPRESS': compression, 'LEVEL': 6 if compression == 'DEFLATE' else 9,
            'BLOCKSIZE': BLOCK_SIZE, 'NUM_THREADS': '2', 'BIGTIFF': 'YES',
            'PREDICTOR': 'FLOATING_POINT' if dtype.kind == 'f' else 'STANDARD',
            'OVERVIEWS': 'IGNORE_EXISTING', 'RESAMPLING': 'NEAREST',
        }
    with tempfile.TemporaryDirectory(prefix='.cog-preparation-', dir=destination.parent) as temp:
        directory = Path(temp)
        staging = directory / destination.name
        with rasterio.Env(**COG_ENV, CPL_TMPDIR=str(directory)):
            source_view = _source_view(source, external_mask, directory)
            mode = 'compressed_native_staging' if staged else 'direct'
            _notify(on_progress, 'conversion_started', source=str(source), output=str(destination),
                    compression=compression, creation_options=options, preparation_mode=mode,
                    disk_budget_checks=disk_checks)
            conversion_started = time.perf_counter()
            native_stage_seconds = 0.0
            native_stage_size_bytes = None
            final_source = source_view
            if staged:
                native_directory = directory / 'native-stage'
                native_directory.mkdir()
                native_stage = native_directory / 'compressed-native.tif'
                native_options = {**options, 'OVERVIEWS': 'NONE'}
                _notify(on_progress, 'native_stage_started', output=str(destination),
                        creation_options=native_options)
                native_started = time.perf_counter()
                _copy_cog(source_view, native_stage, native_options)
                native_stage_seconds = time.perf_counter() - native_started
                native_stage_size_bytes = native_stage.stat().st_size
                final_required = _staged_final_required_bytes(disk_budget, native_stage_size_bytes)
                _check_disk_budget(directory, final_required, 'final_with_overviews', disk_checks)
                _notify(on_progress, 'native_stage_complete', output=str(destination),
                        bytes=native_stage_size_bytes, seconds=native_stage_seconds,
                        disk_budget_checks=disk_checks)
                final_source = native_stage
            _copy_cog(final_source, staging, options)
            conversion_seconds = time.perf_counter() - conversion_started
            _notify(on_progress, 'conversion_complete', output=str(destination),
                    seconds=conversion_seconds, bytes=staging.stat().st_size)
            validation_started = time.perf_counter()
            validation = _verify_native(source_view, staging, on_progress)
            validation_seconds = time.perf_counter() - validation_started
        if (source_signature != _file_signature(source)
                or source_files != _source_file_signatures(source)):
            raise ValueError('Исходный растр или его вспомогательные файлы изменились во время конвертации.')
        option_bytes = json.dumps(options, sort_keys=True, separators=(',', ':')).encode('utf-8')
        report = {
            'source': str(source), 'output': str(destination),
            'created_at': datetime.now(timezone.utc).isoformat(),
            'source_size_bytes': source_signature[0], 'source_mtime_ns': source_signature[1],
            'source_sidecar_policy': 'EMPTY_DIR; external .msk attached explicitly via VRT when present',
            'external_mask': str(external_mask) if external_mask is not None else None,
            'source_files': {path: {'size_bytes': signature[0], 'mtime_ns': signature[1]}
                             for path, signature in source_files.items()},
            'output_size_bytes': staging.stat().st_size,
            'creation_options': options, 'creation_options_sha256': hashlib.sha256(option_bytes).hexdigest(),
            'rasterio_version': rasterio.__version__, 'gdal_version': rasterio.__gdal_version__,
            'preparation_mode': mode, 'disk_budget_checks': disk_checks,
            'staged_disk_budget': disk_budget,
            'native_stage_size_bytes': native_stage_size_bytes, 'native_stage_seconds': native_stage_seconds,
            'conversion_seconds': conversion_seconds, 'validation_seconds': validation_seconds,
            'elapsed_seconds': time.perf_counter() - started,
            **validation,
        }
        staged_manifest = directory / 'cog.json'
        staged_manifest.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + '\n',
                                   encoding='utf-8')
        os.link(staged_manifest, manifest)
        try:
            os.link(staging, destination)
        except BaseException:
            manifest.unlink()
            raise
        _notify(on_progress, 'published', output=str(destination), manifest=str(manifest),
                bytes=report['output_size_bytes'], elapsed_seconds=report['elapsed_seconds'])
        return report
