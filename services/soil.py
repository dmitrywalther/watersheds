"""Native-grid soil class counts using bounded reads and pixel-centre coverage."""

from collections import Counter
from contextlib import ExitStack
from pathlib import Path
import warnings

import numpy as np
import rasterio
from pyproj.exceptions import ProjError
from rasterio.errors import NotGeoreferencedWarning, RasterioError
from rasterio.features import geometry_mask, geometry_window
from rasterio.windows import Window
from shapely.errors import GEOSException
from shapely.geometry import mapping

from config import SOIL_RASTER, is_cloud_placeholder
from data.soil.soil_categories import SOIL_CLASSES


CHUNK_SIZE = 512
MAX_WINDOW_CELLS = 100_000_000
RASTER_ENV = {
    'GDAL_DISABLE_READDIR_ON_OPEN': 'EMPTY_DIR',
    'GDAL_PAM_ENABLED': 'NO',
    'GDAL_CACHEMAX': 64 * 1024 * 1024,
}


def _empty(status, reason, total_cells=0):
    return {
        'status': status, 'categories': [],
        'coverage_percent': 0.0 if total_cells else None,
        'valid_cells': 0, 'total_cells': total_cells, 'reason': reason,
    }


def analyze_soil(watershed_gdf, raster_path=None):
    """Count soil codes, including unknown codes, without resampling the raster.

    Shares are among valid cells. Coverage uses all watershed pixel centres on
    the soil grid, including centres outside the raster. This is a raster-cell
    share, not a geodesic area measurement. Overlapping polygons are rasterized
    together and therefore never double-counted.
    """
    path = Path(raster_path) if raster_path is not None else SOIL_RASTER
    try:
        if not path.is_file():
            return _empty('unavailable', 'Файл почвенной карты не найден.')
        if is_cloud_placeholder(path):
            return _empty('unavailable', 'Почвенная карта хранится только в облаке. Скачайте файл на диск.')
        external_mask_path = path.with_name(path.name + '.msk')
        if external_mask_path.is_file() and is_cloud_placeholder(external_mask_path):
            return _empty('unavailable', 'Маска почвенной карты хранится только в облаке. Скачайте её на диск.')
        if watershed_gdf.crs is None:
            return _empty('unavailable', 'Не задана система координат водосбора.')

        with rasterio.Env(**RASTER_ENV), ExitStack() as stack:
            src = stack.enter_context(rasterio.open(path))
            if src.crs is None or src.count != 1 or not np.issubdtype(np.dtype(src.dtypes[0]), np.integer):
                return _empty('unavailable', 'Почвенная карта должна содержать один канал целочисленных классов и систему координат.')
            geometries = watershed_gdf.to_crs(src.crs).geometry
            geometries = [geometry for geometry in geometries if geometry is not None and not geometry.is_empty]
            if not geometries:
                return _empty('no_data', 'Геометрия водосбора пуста.')
            if any(geometry.geom_type not in ('Polygon', 'MultiPolygon') or not geometry.is_valid
                   or not np.all(np.isfinite(geometry.bounds)) for geometry in geometries):
                return _empty('unavailable', 'Не удалось использовать геометрию водосбора для анализа почв.')
            shapes = [mapping(geometry) for geometry in geometries]
            extent = geometry_window(src, shapes, boundless=True)
            if extent.width * extent.height > MAX_WINDOW_CELLS:
                return _empty('unavailable', 'Размер области водосбора превышает лимит точного анализа почв.')

            external_mask = None
            if external_mask_path.is_file():
                # EMPTY_DIR avoids discovering cloud overviews. A separate
                # validity mask must instead be read explicitly at native size.
                with warnings.catch_warnings():
                    warnings.simplefilter('ignore', NotGeoreferencedWarning)
                    external_mask = stack.enter_context(rasterio.open(external_mask_path))
                if (external_mask.count != 1 or external_mask.shape != src.shape
                        or external_mask.dtypes != ('uint8',)
                        or external_mask.tags().get('INTERNAL_MASK_FLAGS_1') not in ('0', '2')):
                    return _empty('unavailable', 'Не поддерживается формат внешней маски почвенной карты.')

            counts = Counter()
            total_cells = 0
            col_start, row_start = int(extent.col_off), int(extent.row_off)
            col_stop = col_start + int(extent.width)
            row_stop = row_start + int(extent.height)
            for row in range(row_start, row_stop, CHUNK_SIZE):
                for col in range(col_start, col_stop, CHUNK_SIZE):
                    window = Window(col, row, min(CHUNK_SIZE, col_stop - col), min(CHUNK_SIZE, row_stop - row))
                    inside = geometry_mask(shapes, out_shape=(int(window.height), int(window.width)),
                                           transform=src.window_transform(window), invert=True,
                                           all_touched=False)
                    total_cells += int(np.count_nonzero(inside))
                    left, top = max(col, 0), max(row, 0)
                    right, bottom = min(col + int(window.width), src.width), min(row + int(window.height), src.height)
                    if right <= left or bottom <= top:
                        continue
                    included = inside[top - row:bottom - row, left - col:right - col]
                    if not included.any():
                        continue
                    native_window = Window(left, top, right - left, bottom - top)
                    values = src.read(1, window=native_window)
                    valid = included & (src.read_masks(1, window=native_window) != 0)
                    # An explicit mask can supersede GDAL's NoData mask, so
                    # enforce both independently.
                    if src.nodata is not None:
                        valid &= values != src.nodata
                    if external_mask is not None:
                        valid &= external_mask.read(1, window=native_window) != 0
                    codes, frequencies = np.unique(values[valid], return_counts=True)
                    counts.update({int(code): int(frequency) for code, frequency in zip(codes, frequencies)})

            if not total_cells:
                return _empty('no_data', 'В водосборе нет центров ячеек почвенной карты: недостаточно данных для этого масштаба.')
            valid_cells = sum(counts.values())
            if not valid_cells:
                return _empty('no_data', 'Внутри водосбора нет данных почвенной карты.', total_cells)
            return {
                'status': 'ok',
                'categories': [
                    {'code': code, 'name': SOIL_CLASSES.get(code, f'Неизвестный класс (код {code})'),
                     'percent': round(count / valid_cells * 100, 2)}
                    for code, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))
                ],
                'coverage_percent': round(valid_cells / total_cells * 100, 2),
                'valid_cells': valid_cells, 'total_cells': total_cells, 'reason': None,
            }
    except (RasterioError, OSError, ValueError, OverflowError, ProjError, GEOSException):
        return _empty('unavailable', 'Не удалось прочитать почвенную карту или преобразовать геометрию водосбора.')
