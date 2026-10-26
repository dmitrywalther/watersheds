"""Bounded raster class previews shared by soil and land cover."""

from collections import defaultdict
from contextlib import ExitStack
from dataclasses import dataclass
import json
import math
from pathlib import Path
import warnings

import numpy as np
from pyproj import CRS, Transformer
from pyproj.exceptions import ProjError
import rasterio
from rasterio.errors import NotGeoreferencedWarning, RasterioError
from rasterio.features import geometry_mask, geometry_window, shapes
from rasterio.windows import Window
from shapely.errors import GEOSException
from shapely.geometry import MultiPolygon, Polygon, mapping, shape
from shapely.ops import transform as transform_geometry, unary_union

from config import is_cloud_placeholder
from services.soil import RASTER_ENV


MAX_GRID_EDGE = 512
READ_CHUNK_EDGE = 512
MAX_SOURCE_CELLS = 100_000_000
MAX_POLYGONS = 2_000
MAX_COORDINATES = 20_000
MAX_GEOJSON_BYTES = 2_000_000


@dataclass(frozen=True)
class OverlayLabels:
    """Dataset-specific wording; limits and geometry behavior remain shared."""

    map_subject: str
    map_object: str
    map_genitive: str
    layer_genitive: str
    class_genitive: str
    tab: str


class _TooComplex(Exception):
    """Retry the preview at a coarser grid, or return an explicit limit."""


def _empty(status, reason):
    return {
        'status': status,
        'geometry': {'type': 'FeatureCollection', 'features': []},
        'approximate': False,
        'sample_resolution_m': None,
        'reason': reason,
    }


def _polygon_parts(geometry):
    if geometry.is_empty:
        return []
    if geometry.geom_type == 'Polygon':
        return [geometry]
    if geometry.geom_type in ('MultiPolygon', 'GeometryCollection'):
        return [part for item in geometry.geoms for part in _polygon_parts(item)]
    return []


def _coordinate_count(geometry):
    return sum(len(part.exterior.coords) + sum(len(ring.coords) for ring in part.interiors)
               for part in _polygon_parts(geometry))


def _sample_grid(src, external_mask, window, stride, excluded_codes):
    """Nearest source pixels, using only small native reads, even on large TIFFs.

    No interpolated category values, overviews, full-resolution arrays, or
    unbounded source reads are used. Partial edge cells sample the last source
    pixel and are subsequently clipped to the exact raster footprint.
    """
    height = math.ceil(window.height / stride)
    width = math.ceil(window.width / stride)
    rows = np.minimum((np.arange(height) * stride + stride // 2).astype(np.int64), int(window.height) - 1)
    cols = np.minimum((np.arange(width) * stride + stride // 2).astype(np.int64), int(window.width) - 1)
    rows += int(window.row_off)
    cols += int(window.col_off)
    values = np.empty((height, width), dtype=src.dtypes[0])
    valid = np.empty((height, width), dtype=bool)
    row_groups = rows // READ_CHUNK_EDGE
    col_groups = cols // READ_CHUNK_EDGE
    for row_group in np.unique(row_groups):
        out_rows = np.flatnonzero(row_groups == row_group)
        source_rows = rows[out_rows]
        top, bottom = int(source_rows[0]), int(source_rows[-1]) + 1
        for col_group in np.unique(col_groups):
            out_cols = np.flatnonzero(col_groups == col_group)
            source_cols = cols[out_cols]
            left, right = int(source_cols[0]), int(source_cols[-1]) + 1
            native_window = Window(left, top, right - left, bottom - top)
            selection = np.ix_(source_rows - top, source_cols - left)
            samples = src.read(1, window=native_window)[selection]
            included = src.read_masks(1, window=native_window)[selection] != 0
            if src.nodata is not None:
                included &= samples != src.nodata
            for code in excluded_codes:
                included &= samples != code
            if external_mask is not None:
                included &= external_mask.read(1, window=native_window)[selection] != 0
            target = np.ix_(out_rows, out_cols)
            values[target] = samples
            valid[target] = included
    transform = src.window_transform(window) * src.transform.scale(stride, stride)
    return values, valid, transform


def _vectorize(values, valid, transform, native_basin, geographic_basin, projector, class_names):
    valid &= geometry_mask([mapping(native_basin)], out_shape=valid.shape,
                           transform=transform, all_touched=True, invert=True)
    if not valid.any():
        return []
    # GDAL polygonization does not preserve uint32 values directly. Compact
    # int32 labels retain even unknown codes above 2**31 without conversion loss.
    codes, compact = np.unique(values[valid], return_inverse=True)
    labels = np.zeros(values.shape, dtype=np.int32)
    labels[valid] = compact + 1
    by_code = defaultdict(list)
    polygon_count = coordinate_count = 0
    for raw_geometry, label in shapes(labels, mask=valid, transform=transform, connectivity=4):
        native = shape(raw_geometry)
        polygon_count += 1
        coordinate_count += _coordinate_count(native)
        if polygon_count > MAX_POLYGONS or coordinate_count > MAX_COORDINATES:
            raise _TooComplex
        native = native.intersection(native_basin)
        if native.is_empty:
            continue
        # Clipping in both CRSs also prevents straight transformed segments from
        # protruding beyond the exact GeoJSON basin shown by the web map.
        geographic = transform_geometry(projector.transform, native).intersection(geographic_basin)
        by_code[int(codes[int(label) - 1])].extend(_polygon_parts(geographic))

    features = []
    polygon_count = coordinate_count = 0
    for code in sorted(by_code):
        parts = _polygon_parts(unary_union(by_code[code]))
        if not parts:
            continue
        geometry = parts[0] if len(parts) == 1 else MultiPolygon(parts)
        polygon_count += len(parts)
        coordinate_count += _coordinate_count(geometry)
        if polygon_count > MAX_POLYGONS or coordinate_count > MAX_COORDINATES:
            raise _TooComplex
        features.append({
            'type': 'Feature', 'geometry': mapping(geometry),
            'properties': {'code': code, 'name': class_names.get(code, f'Неизвестный класс (код {code})')},
        })
    collection = {'type': 'FeatureCollection', 'features': features}
    if len(json.dumps(collection, ensure_ascii=False, separators=(',', ':')).encode('utf-8')) > MAX_GEOJSON_BYTES:
        raise _TooComplex
    return features


def _resolution_m(src, stride):
    crs = CRS(src.crs)
    if not crs.is_projected or not crs.axis_info:
        return None
    # Geographic degrees are deliberately not presented as metres. Projected
    # units, including feet, have an explicit conversion in pyproj's CRS axes.
    factor = crs.axis_info[0].unit_conversion_factor
    if factor is None or not math.isfinite(factor):
        return None
    spacing = max(math.hypot(src.transform.a, src.transform.d),
                  math.hypot(src.transform.b, src.transform.e))
    return round(float(spacing * stride * factor), 6)


def build_categorical_map(watershed_gdf, raster_path, class_names, *, labels, excluded_codes=()):
    """Return a clipped WGS84 preview without changing precise class shares.

    Coarse previews use nearest-neighbour samples for both classes and validity;
    small classes or NoData gaps may disappear. The response marks this clearly.
    Native previews retain whole class-cell footprints, clipped to the basin;
    edge classes can therefore differ from centre-based statistics.
    """
    path = Path(raster_path)
    try:
        if not path.is_file():
            return _empty('unavailable', f'Файл {labels.map_genitive} не найден.')
        if is_cloud_placeholder(path):
            return _empty('unavailable', f'{labels.map_subject} хранится только в облаке. Скачайте файл на диск.')
        mask_path = path.with_name(path.name + '.msk')
        if mask_path.is_file() and is_cloud_placeholder(mask_path):
            return _empty('unavailable', f'Маска {labels.map_genitive} хранится только в облаке. Скачайте её на диск.')
        if watershed_gdf.crs is None:
            return _empty('unavailable', 'Не задана система координат водосбора.')
        with rasterio.Env(**RASTER_ENV), ExitStack() as stack:
            src = stack.enter_context(rasterio.open(path))
            if src.crs is None or src.count != 1 or not np.issubdtype(np.dtype(src.dtypes[0]), np.integer):
                return _empty('unavailable', f'{labels.map_subject} должна содержать один канал целочисленных классов и систему координат.')
            geometries = [geometry for geometry in watershed_gdf.geometry
                          if geometry is not None and not geometry.is_empty]
            if not geometries:
                return _empty('no_data', 'Геометрия водосбора пуста.')
            if any(geometry.geom_type not in ('Polygon', 'MultiPolygon') or not geometry.is_valid
                   or not np.all(np.isfinite(geometry.bounds)) for geometry in geometries):
                return _empty('unavailable', f'Не удалось использовать геометрию водосбора для слоя {labels.class_genitive}.')
            native_basin = unary_union(watershed_gdf.to_crs(src.crs).geometry.dropna())
            geographic_basin = unary_union(watershed_gdf.to_crs('EPSG:4326').geometry.dropna())
            extent = geometry_window(src, [mapping(native_basin)], boundless=True)
            left, top = max(0, int(extent.col_off)), max(0, int(extent.row_off))
            right = min(src.width, int(extent.col_off + extent.width))
            bottom = min(src.height, int(extent.row_off + extent.height))
            if right <= left or bottom <= top:
                return _empty('no_data', f'Водосбор находится вне покрытия {labels.map_genitive}.')
            window = Window(left, top, right - left, bottom - top)
            if window.width * window.height > MAX_SOURCE_CELLS:
                return _empty('unavailable', f'Размер области водосбора превышает лимит подготовки {labels.layer_genitive}.')
            raster_footprint = Polygon([src.transform * point for point in
                                        [(0, 0), (src.width, 0), (src.width, src.height), (0, src.height)]])
            native_basin = native_basin.intersection(raster_footprint)
            if native_basin.is_empty:
                return _empty('no_data', f'Водосбор находится вне покрытия {labels.map_genitive}.')

            external_mask = None
            if mask_path.is_file():
                with warnings.catch_warnings():
                    warnings.simplefilter('ignore', NotGeoreferencedWarning)
                    external_mask = stack.enter_context(rasterio.open(mask_path))
                if (external_mask.count != 1 or external_mask.shape != src.shape
                        or external_mask.dtypes != ('uint8',)
                        or external_mask.tags().get('INTERNAL_MASK_FLAGS_1') not in ('0', '2')):
                    return _empty('unavailable', f'Не поддерживается формат внешней маски {labels.map_genitive}.')

            projector = Transformer.from_crs(src.crs, 'EPSG:4326', always_xy=True)
            stride = max(1, math.ceil(max(window.width, window.height) / MAX_GRID_EDGE))
            while True:
                values, valid, transform = _sample_grid(src, external_mask, window, stride, excluded_codes)
                try:
                    features = _vectorize(values, valid, transform, native_basin, geographic_basin, projector, class_names)
                    break
                except _TooComplex:
                    if values.size == 1:
                        return _empty('unavailable', f'Геометрия {labels.layer_genitive} слишком сложна для отображения на карте.')
                    stride *= 2
            if not features:
                if stride > 1:
                    result = _empty('no_data', f'На обзорной сетке не найдено классов {labels.class_genitive}: мелкие участки данных могут быть пропущены. '
                                    f'Точные доли приведены во вкладке «{labels.tab}».')
                    result.update(approximate=True, sample_resolution_m=_resolution_m(src, stride))
                    return result
                return _empty('no_data', f'Внутри водосбора нет данных {labels.map_genitive} для отображения.')
            approximate = stride > 1
            return {
                'status': 'ok',
                'geometry': {'type': 'FeatureCollection', 'features': features},
                'approximate': approximate,
                'sample_resolution_m': _resolution_m(src, stride),
                'reason': ('Слой обобщён по ближайшим ячейкам: мелкие классы и пропуски данных могут быть не видны. '
                           f'Точные доли приведены во вкладке «{labels.tab}».') if approximate else None,
            }
    except (RasterioError, OSError, ValueError, OverflowError, ProjError, GEOSException):
        return _empty('unavailable', f'Не удалось прочитать {labels.map_object} или преобразовать геометрию водосбора.')
