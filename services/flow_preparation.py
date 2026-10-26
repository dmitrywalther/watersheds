"""Prepare exact ESRI D8 accumulation without keeping a whole raster in RAM."""

from collections import deque
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

import numpy as np
import rasterio
from rasterio.windows import Window

from config import BASE_DIR, is_cloud_placeholder
from services.dem_catalog import RASTER_ENV


NODATA = np.iinfo(np.uint32).max
STRIPE_ROWS = 128
KERNEL_SOURCE = BASE_DIR / 'scripts' / 'FlowAccumulation.cs'
PREPARATION_ENV = {**RASTER_ENV, 'GDAL_CACHEMAX': 16 * 1024 * 1024, 'GDAL_NUM_THREADS': '1'}


def _notify(callback, phase, **values):
    if callback:
        callback({'phase': phase, **values})


def _compile_kernel(directory):
    compiler = Path(os.environ.get('WINDIR', r'C:\Windows')) / 'Microsoft.NET' / 'Framework64' / 'v4.0.30319' / 'csc.exe'
    if not compiler.is_file():
        raise RuntimeError('Для подготовки нужен установленный 64-битный компилятор .NET Framework csc.exe.')
    executable = directory / 'FlowAccumulation.exe'
    source_bytes = KERNEL_SOURCE.read_bytes()
    source_copy = directory / 'FlowAccumulation.cs'
    source_copy.write_bytes(source_bytes)
    result = subprocess.run(
        [str(compiler), '/nologo', '/unsafe', '/optimize+', '/platform:x64',
         '/out:' + str(executable), str(source_copy)],
        capture_output=True, text=True, timeout=120,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0,
    )
    if result.returncode:
        raise RuntimeError('Не удалось собрать вычислительный модуль: ' + result.stdout + result.stderr)
    return executable, hashlib.sha256(source_bytes).hexdigest()


def _run_kernel(directory, height, width, callback):
    executable, kernel_sha256 = _compile_kernel(directory)
    command = [str(executable), str(directory / 'directions.raw'),
               str(directory / 'counts.raw'), str(directory / 'state.raw'), str(height), str(width)]
    lines = deque(maxlen=12)
    completed = None
    with subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          text=True, encoding='utf-8', errors='replace',
                          creationflags=(subprocess.CREATE_NO_WINDOW | subprocess.BELOW_NORMAL_PRIORITY_CLASS)
                          if os.name == 'nt' else 0) as process:
        for line in process.stdout:
            lines.append(line.strip())
            try:
                progress = json.loads(line)
            except json.JSONDecodeError:
                continue
            if callback:
                callback(progress)
            if progress.get('phase') == 'complete':
                completed = progress
        if process.wait() or completed is None:
            raise ValueError('Ошибка подготовки D8: ' + '\n'.join(lines))
    return {**completed, 'kernel_sha256': kernel_sha256}


def _write_directions(source, directory, callback):
    digest = hashlib.sha256()
    valid_cells = 0
    with rasterio.Env(**PREPARATION_ENV), rasterio.open(source) as src:
        if src.count != 1 or not np.issubdtype(np.dtype(src.dtypes[0]), np.integer):
            raise ValueError('Нужен одноканальный растр целочисленных направлений D8.')
        if not src.crs or not src.crs.is_projected:
            raise ValueError('Нужна подготовленная проекция растра направлений D8.')
        height, width = src.shape
        cells = height * width
        if cells >= NODATA:
            raise ValueError('Слишком много ячеек для точного uint32 накопления.')
        # Raw direction + state + uint32 counts + worst-case uncompressed output.
        required_bytes = cells * 10 + 256 * 1024 * 1024
        if shutil.disk_usage(directory).free < required_bytes:
            raise ValueError(f'Недостаточно места для расчёта: требуется около {required_bytes / 1024**3:.1f} ГиБ.')
        profile = dict(driver='GTiff', count=1, width=width, height=height, crs=src.crs,
                       transform=src.transform, dtype='uint32', nodata=int(NODATA),
                       tiled=True, blockxsize=256, blockysize=256, compress='DEFLATE',
                       predictor=1, zlevel=4, BIGTIFF='YES', NUM_THREADS='1')
        with (directory / 'directions.raw').open('xb') as output:
            for row in range(0, height, STRIPE_ROWS):
                values = src.read(1, window=Window(0, row, width, min(STRIPE_ROWS, height-row)), masked=True)
                valid = ~np.ma.getmaskarray(values)
                if not np.all(np.isin(values.data[valid], (0, 1, 2, 4, 8, 16, 32, 64, 128))):
                    raise ValueError('Растр содержит неизвестные коды направления D8.')
                pointers = np.where(valid, values.data, 255).astype('uint8')
                raw = pointers.tobytes()
                output.write(raw)
                digest.update(raw)
                valid_cells += int(np.count_nonzero(valid))
                if row % 2048 == 0 or row + STRIPE_ROWS >= height:
                    _notify(callback, 'read_directions', rows_done=min(row+STRIPE_ROWS, height), rows=height)
        if not valid_cells:
            raise ValueError('В растре направлений D8 нет валидных ячеек.')
        return profile, valid_cells, digest.hexdigest()


def _export_and_validate(directory, profile, valid_cells, callback):
    height, width = profile['height'], profile['width']
    counts = np.memmap(directory / 'counts.raw', mode='r', dtype='<u4', shape=(height, width))
    directions = np.memmap(directory / 'directions.raw', mode='r', dtype='uint8', shape=(height, width))
    staging = directory / 'accumulation.tif'
    maximum = 0
    try:
        with rasterio.Env(**PREPARATION_ENV), rasterio.open(staging, 'w', **profile) as dst:
            for row in range(0, height, STRIPE_ROWS):
                stop = min(row + STRIPE_ROWS, height)
                valid = directions[row:stop] != 255
                inclusive = counts[row:stop]
                if np.any(valid & ((inclusive < 1) | (inclusive > valid_cells))):
                    raise ValueError('Вычислительный модуль вернул некорректное накопление.')
                values = np.full(inclusive.shape, NODATA, dtype='uint32')
                np.subtract(inclusive, np.uint32(1), out=values, where=valid)
                if np.any(valid):
                    maximum = max(maximum, int(values[valid].max()))
                dst.write(values, 1, window=Window(0, row, width, stop-row))
                if row % 2048 == 0 or stop == height:
                    _notify(callback, 'write_geotiff', rows_done=stop, rows=height)
            dst.update_tags(flow_dir_encoding='esri', flow_acc_units='cells', includes_self='false',
                            algorithm='exact disk-backed topological D8 accumulation',
                            valid_cells=str(valid_cells))
        # Re-read every output pixel and compare to exact counts and mask.
        with rasterio.Env(**PREPARATION_ENV), rasterio.open(staging) as dst:
            if dst.shape != (height, width) or dst.crs != profile['crs'] or dst.transform != profile['transform']:
                raise ValueError('Сетка выходного накопления не совпадает с направлениями.')
            for row in range(0, height, STRIPE_ROWS):
                stop = min(row + STRIPE_ROWS, height)
                valid = directions[row:stop] != 255
                actual = dst.read(1, window=Window(0, row, width, stop-row))
                expected = np.full(actual.shape, NODATA, dtype='uint32')
                np.subtract(counts[row:stop], np.uint32(1), out=expected, where=valid)
                if not np.array_equal(actual, expected):
                    raise ValueError('Проверка записанного растра накопления не пройдена.')
                if row % 4096 == 0 or stop == height:
                    _notify(callback, 'validate_geotiff', rows_done=stop, rows=height)
    finally:
        # Windows cannot remove the scratch directory while a mapping is open.
        counts._mmap.close()
        directions._mmap.close()
    return staging, maximum


def prepare_flow_accumulation(source, destination, *, on_progress=None):
    source, destination = Path(source).resolve(), Path(destination).resolve()
    manifest = destination.with_suffix(destination.suffix + '.preparation.json')
    if destination.exists() or manifest.exists():
        raise FileExistsError(f'Результат уже существует: {destination}')
    if is_cloud_placeholder(source):
        raise ValueError('Сначала скачайте растр направлений из облака.')
    source_stat = source.stat()
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.flow-preparation-', dir=destination.parent) as temp:
        directory = Path(temp)
        profile, valid_cells, digest = _write_directions(source, directory, on_progress)
        result = _run_kernel(directory, profile['height'], profile['width'], on_progress)
        if result['valid'] != valid_cells or result['processed'] != valid_cells:
            raise ValueError('Не все валидные ячейки обработаны; возможен цикл стока.')
        staging, maximum = _export_and_validate(directory, profile, valid_cells, on_progress)
        current_stat = source.stat()
        if (source_stat.st_size, source_stat.st_mtime_ns) != (current_stat.st_size, current_stat.st_mtime_ns):
            raise ValueError('Исходный растр изменился во время расчёта.')
        report = {
            'source': str(source), 'output': str(destination),
            'created_at': datetime.now(timezone.utc).isoformat(),
            'source_size': source_stat.st_size, 'source_mtime_ns': source_stat.st_mtime_ns,
            'direction_sha256': digest, 'kernel_sha256': result['kernel_sha256'],
            'shape': [profile['height'], profile['width']], 'crs': str(profile['crs']),
            'transform': list(profile['transform']), 'dtype': 'uint32', 'nodata': int(NODATA),
            'flow_dir_encoding': 'esri', 'units': 'cells', 'includes_self': False,
            'valid_cells': valid_cells, 'processed_cells': result['processed'],
            'max_accumulation': maximum, 'all_output_pixels_validated': True,
        }
        staged_manifest = directory / 'preparation.json'
        staged_manifest.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        # Hard-link publication is atomic and fails if a destination exists.
        # Both staging files are on the destination volume (NTFS on Windows).
        os.link(staged_manifest, manifest)
        try:
            os.link(staging, destination)
        except BaseException:
            manifest.unlink()
            raise
        _notify(on_progress, 'published', output=str(destination), valid_cells=valid_cells)
        return report
