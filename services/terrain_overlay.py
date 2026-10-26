"""Bounded interval maps of the existing elevation and slope analysis rasters.

These are display layers, independent from the unmodified zonal statistics.
Source values are sampled without interpolation and grouped into at most 24
equal-width intervals before exact basin clipping in source CRS and WGS84.
"""

from contextlib import ExitStack
import json
import math
from pathlib import Path
import warnings

import numpy as np
from pyproj import Transformer
from pyproj.exceptions import ProjError
import rasterio
from rasterio.errors import NotGeoreferencedWarning, RasterioError
from rasterio.features import geometry_mask, geometry_window
from rasterio.windows import Window
from shapely.errors import GEOSException
from shapely.geometry import Polygon, mapping
from shapely.ops import unary_union

from config import ELEVATION_RASTER, SLOPE_RASTER, is_cloud_placeholder
from services import categorical_overlay as overlay
from services.soil import RASTER_ENV


MAX_BANDS = 24
SOURCE_LABEL = 'GMTED2010 30″'


def _empty(status, reason, unit):
    return {
        'status': status,
        'geometry': {'type': 'FeatureCollection', 'features': []},
        'approximate': False,
        'sample_resolution_m': None,
        'reason': reason,
        'unit': unit,
        'range': None,
        'bands': [],
        'source_label': SOURCE_LABEL,
    }


def _classify(values, valid):
    """Classify finite valid samples, retaining the exact sampled endpoints."""
    samples = values[valid].astype(np.float64)
    minimum, maximum = float(samples.min()), float(samples.max())
    if minimum == maximum:
        edges = np.array([minimum, maximum])
    else:
        # Weighted interpolation avoids overflowing maximum - minimum for
        # opposite-sign finite float64 endpoints.
        fractions = np.linspace(0, 1, MAX_BANDS + 1)
        edges = minimum * (1 - fractions) + maximum * fractions
        edges[0], edges[-1] = minimum, maximum
        # Adjacent float64 values cannot represent 24 distinct intervals.
        edges = np.unique(edges)
    bands = [{'code': index, 'min': float(low), 'max': float(high)}
             for index, (low, high) in enumerate(zip(edges[:-1], edges[1:]))]
    labels = np.zeros(values.shape, dtype=np.int32)
    labels[valid] = np.searchsorted(edges[1:-1], samples, side='right')
    return labels, bands, {'min': minimum, 'max': maximum}


def _build_terrain_map(watershed_gdf, raster_path, *, kind):
    unit = 'm' if kind == 'elevation' else 'degrees'
    subject = 'высот' if kind == 'elevation' else 'уклонов'
    path = Path(raster_path)
    try:
        if not path.is_file():
            return _empty('unavailable', f'Файл растра {subject} не найден.', unit)
        if is_cloud_placeholder(path):
            return _empty('unavailable', f'Растр {subject} хранится только в облаке. Скачайте файл на диск.', unit)
        mask_path = path.with_name(path.name + '.msk')
        if mask_path.is_file() and is_cloud_placeholder(mask_path):
            return _empty('unavailable', f'Маска растра {subject} хранится только в облаке. Скачайте её на диск.', unit)
        if watershed_gdf.crs is None:
            return _empty('unavailable', 'Не задана система координат водосбора.', unit)
        with rasterio.Env(**RASTER_ENV), ExitStack() as stack:
            src = stack.enter_context(rasterio.open(path))
            if src.crs is None or src.count != 1 or np.dtype(src.dtypes[0]).kind not in 'iuf':
                return _empty('unavailable', f'Растр {subject} должен содержать один числовой канал и систему координат.', unit)
            geometries = [geometry for geometry in watershed_gdf.geometry
                          if geometry is not None and not geometry.is_empty]
            if not geometries:
                return _empty('no_data', 'Геометрия водосбора пуста.', unit)
            if any(geometry.geom_type not in ('Polygon', 'MultiPolygon') or not geometry.is_valid
                   or not np.all(np.isfinite(geometry.bounds)) for geometry in geometries):
                return _empty('unavailable', f'Не удалось использовать геометрию водосбора для слоя {subject}.', unit)
            native_basin = unary_union(watershed_gdf.to_crs(src.crs).geometry.dropna())
            geographic_basin = unary_union(watershed_gdf.to_crs('EPSG:4326').geometry.dropna())
            extent = geometry_window(src, [mapping(native_basin)], boundless=True)
            left, top = max(0, int(extent.col_off)), max(0, int(extent.row_off))
            right = min(src.width, int(extent.col_off + extent.width))
            bottom = min(src.height, int(extent.row_off + extent.height))
            if right <= left or bottom <= top:
                return _empty('no_data', f'Водосбор находится вне покрытия растра {subject}.', unit)
            window = Window(left, top, right - left, bottom - top)
            if window.width * window.height > overlay.MAX_SOURCE_CELLS:
                return _empty('unavailable', f'Размер области водосбора превышает лимит подготовки слоя {subject}.', unit)
            footprint = Polygon([src.transform * point for point in
                                 [(0, 0), (src.width, 0), (src.width, src.height), (0, src.height)]])
            native_basin = native_basin.intersection(footprint)
            if native_basin.is_empty:
                return _empty('no_data', f'Водосбор находится вне покрытия растра {subject}.', unit)

            external_mask = None
            if mask_path.is_file():
                with warnings.catch_warnings():
                    warnings.simplefilter('ignore', NotGeoreferencedWarning)
                    external_mask = stack.enter_context(rasterio.open(mask_path))
                if (external_mask.count != 1 or external_mask.shape != src.shape
                        or external_mask.dtypes != ('uint8',)
                        or external_mask.tags().get('INTERNAL_MASK_FLAGS_1') not in ('0', '2')):
                    return _empty('unavailable', f'Не поддерживается формат внешней маски растра {subject}.', unit)

            projector = Transformer.from_crs(src.crs, 'EPSG:4326', always_xy=True)
            stride = max(1, math.ceil(max(window.width, window.height) / overlay.MAX_GRID_EDGE))
            while True:
                values, valid, transform = overlay._sample_grid(src, external_mask, window, stride, ())
                valid &= np.isfinite(values)
                if kind == 'slope':
                    valid &= (values >= 0) & (values <= 90)
                valid &= geometry_mask([mapping(native_basin)], out_shape=valid.shape,
                                       transform=transform, all_touched=True, invert=True)
                if not valid.any():
                    features = []
                    break
                labels, bands, value_range = _classify(values, valid)
                try:
                    features = overlay._vectorize(labels, valid, transform, native_basin,
                                                  geographic_basin, projector, {})
                    for feature in features:
                        feature['properties'] = dict(bands[feature['properties']['code']])
                    collection = {'type': 'FeatureCollection', 'features': features}
                    encoded = json.dumps(collection, ensure_ascii=False, allow_nan=False, separators=(',', ':'))
                    if len(encoded.encode('utf-8')) > overlay.MAX_GEOJSON_BYTES:
                        raise overlay._TooComplex
                    break
                except overlay._TooComplex:
                    if values.size == 1:
                        return _empty('unavailable', f'Геометрия слоя {subject} слишком сложна для отображения на карте.', unit)
                    stride *= 2
            if not features:
                result = _empty('no_data', f'Внутри водосбора нет данных {subject} для отображения.', unit)
                if stride > 1:
                    result.update(
                        approximate=True,
                        sample_resolution_m=overlay._resolution_m(src, stride),
                        reason=f'На обзорной сетке не найдено значений {subject}: мелкие участки данных могут быть пропущены.',
                    )
                return result
            approximate = stride > 1
            reason = ('Цвета показывают интервалы значений в пределах водосбора. '
                      'Диапазон слоя рассчитан по отображаемым ячейкам и может отличаться от сводной статистики.')
            if approximate:
                reason += ' Слой обобщён по ближайшим ячейкам: мелкие детали и пропуски данных могут быть не видны.'
            return {
                'status': 'ok',
                'geometry': collection,
                'approximate': approximate,
                'sample_resolution_m': overlay._resolution_m(src, stride),
                'reason': reason,
                'unit': unit,
                'range': value_range,
                'bands': bands,
                'source_label': SOURCE_LABEL,
            }
    except (RasterioError, OSError, ValueError, OverflowError, ProjError, GEOSException):
        return _empty('unavailable', f'Не удалось прочитать растр {subject} или преобразовать геометрию водосбора.', unit)


def build_elevation_map(watershed_gdf, raster_path=None):
    return _build_terrain_map(watershed_gdf, ELEVATION_RASTER if raster_path is None else raster_path,
                              kind='elevation')


def build_slope_map(watershed_gdf, raster_path=None):
    return _build_terrain_map(watershed_gdf, SLOPE_RASTER if raster_path is None else raster_path,
                              kind='slope')
