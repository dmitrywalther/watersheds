"""Cached, approximate valid-data footprints for the hydrological raster pairs."""

import hashlib
import json
import math
import os
import tempfile
import threading

import numpy as np
import rasterio
from affine import Affine
from pyproj import Transformer
from rasterio.enums import Resampling
from rasterio.features import shapes
from shapely.affinity import translate
from shapely.geometry import box, mapping, shape
from shapely.ops import transform as transform_geometry

from config import DEM_DIR
from services.dem_catalog import get_dem_config


CACHE_DIR = DEM_DIR / 'coverage_cache'
MAX_SAMPLE_DIMENSION = 1024
ALGORITHM_VERSION = 1
_LOCKS = {}
_LOCKS_GUARD = threading.Lock()
_NOTE = (
    'Приблизительная граница участков, где есть направления и накопление стока. '
    'Мелкие пробелы могут не отображаться. Для расчёта весь водосбор должен '
    'помещаться в данных; точка внутри границы не гарантирует успешный расчёт.'
)


def _fingerprint(source_id, dem):
    files = []
    for key in ('flow_dir', 'flow_acc'):
        path = dem[key].resolve()
        stat = path.stat()
        files.append([str(path), stat.st_size, stat.st_mtime_ns])
        # A separate GDAL validity mask affects the footprint as much as NoData.
        mask = path.with_name(path.name + '.msk')
        if mask.is_file():
            stat = mask.stat()
            files.append([str(mask), stat.st_size, stat.st_mtime_ns])
    payload = [ALGORITHM_VERSION, MAX_SAMPLE_DIMENSION, source_id, files]
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False).encode('utf-8')).hexdigest()


def _polygons(geometry):
    if geometry.is_empty:
        return
    if geometry.geom_type == 'Polygon':
        yield geometry
    elif geometry.geom_type in ('MultiPolygon', 'GeometryCollection'):
        for part in geometry.geoms:
            yield from _polygons(part)


def _wrap_at_antimeridian(geometry):
    """Split before wrapping, so an eastern edge cannot draw across the world."""
    west, _, east, _ = geometry.bounds
    first_world = math.floor((west + 180) / 360)
    last_world = math.floor((east + 180) / 360)
    for world in range(first_world, last_world + 1):
        part = geometry.intersection(box(-180 + 360 * world, -90, 180 + 360 * world, 90))
        for polygon in _polygons(part):
            if polygon.area > 0:
                yield translate(polygon, xoff=-360 * world)


def _camera_bounds(polygons):
    if not polygons:
        return None
    # Find the complement of the largest empty longitude arc. For Russia this
    # gives approximately [19, south, 191, north], retaining Chukotka in view.
    intervals = sorted((polygon.bounds[0], polygon.bounds[2]) for polygon in polygons)
    merged = []
    for west, east in intervals:
        if merged and west <= merged[-1][1]:
            merged[-1][1] = max(east, merged[-1][1])
        else:
            merged.append([west, east])
    gaps = [merged[index + 1][0] - interval[1] if index + 1 < len(merged)
            else merged[0][0] + 360 - interval[1] for index, interval in enumerate(merged)]
    gap_index = int(np.argmax(gaps))
    west = merged[(gap_index + 1) % len(merged)][0]
    east = west + 360 - gaps[gap_index]
    return [west, min(p.bounds[1] for p in polygons), east, max(p.bounds[3] for p in polygons)]


def _build_coverage(source_id, dem):
    # No full-size arrays and no modification/building of source overviews.
    # Without overviews GDAL still needs to decompress intersecting source
    # blocks once; the persistent cache removes that cost from later requests.
    with rasterio.Env(GDAL_PAM_ENABLED='NO', GDAL_DISABLE_READDIR_ON_OPEN='FALSE',
                      GDAL_CACHEMAX=32 * 1024 * 1024, GDAL_NUM_THREADS='1'), \
            rasterio.open(dem['flow_dir']) as direction, \
            rasterio.open(dem['flow_acc']) as accumulation:
        scale = min(1.0, MAX_SAMPLE_DIMENSION / max(direction.shape))
        height = max(1, math.ceil(direction.height * scale))
        width = max(1, math.ceil(direction.width * scale))
        sampled_transform = direction.transform * Affine.scale(direction.width / width, direction.height / height)
        valid = np.ones((height, width), dtype=bool)
        for dataset in (direction, accumulation):
            sampled = dataset.read(1, out_shape=(height, width), masked=True, resampling=Resampling.nearest)
            valid &= ~np.ma.getmaskarray(sampled) & np.isfinite(sampled.data)
        sample_resolution = max(abs(sampled_transform.a), abs(sampled_transform.e))
        to_geographic = Transformer.from_crs(direction.crs, 'EPSG:4326', always_xy=True, force_over=True)

    polygons = []
    for geometry, value in shapes(valid.astype(np.uint8), mask=valid,
                                  transform=sampled_transform, connectivity=4):
        if value != 1:
            continue
        projected = shape(geometry)
        # Preserve holes and disconnected components, while smoothing the
        # staircase at less than half a sampled cell rather than hiding bays.
        projected = projected.simplify(sample_resolution * 0.35, preserve_topology=True)
        geographic = transform_geometry(to_geographic.transform, projected)
        if not geographic.is_valid:
            geographic = geographic.buffer(0)
        polygons.extend(_wrap_at_antimeridian(geographic))

    return {
        'source_id': source_id,
        'geometry': {
            'type': 'FeatureCollection',
            'features': [{'type': 'Feature', 'properties': {'source_id': source_id},
                          'geometry': mapping(polygon)} for polygon in polygons],
        },
        'bounds': _camera_bounds(polygons),
        'approximate': True,
        'sample_resolution_m': float(sample_resolution),
        'note': _NOTE,
    }


def _valid_cached_result(cached, source_id):
    try:
        if (cached['source_id'] != source_id or cached['approximate'] is not True
                or not isinstance(cached['note'], str)
                or not math.isfinite(cached['sample_resolution_m']) or cached['sample_resolution_m'] <= 0
                or cached['geometry']['type'] != 'FeatureCollection'):
            return False
        features = cached['geometry']['features']
        if not isinstance(features, list):
            return False
        bounds = cached['bounds']
        if not features:
            return bounds is None
        if (not isinstance(bounds, list) or len(bounds) != 4
                or not all(math.isfinite(value) for value in bounds)
                or not (-180 <= bounds[0] <= 180 and bounds[0] < bounds[2] <= bounds[0] + 360)
                or not (-90 <= bounds[1] < bounds[3] <= 90)):
            return False
        for feature in features:
            if feature['type'] != 'Feature' or feature['geometry']['type'] != 'Polygon':
                return False
            rings = feature['geometry']['coordinates']
            if not isinstance(rings, list) or not rings:
                return False
            for ring in rings:
                if len(ring) < 4 or ring[0] != ring[-1]:
                    return False
                for point in ring:
                    if (len(point) != 2 or not all(math.isfinite(value) for value in point)
                            or not -180 <= point[0] <= 180 or not -90 <= point[1] <= 90):
                        return False
        return True
    except (KeyError, TypeError, ValueError, AttributeError):
        return False


def get_dem_coverage(source_id):
    dem = get_dem_config(source_id)
    fingerprint = _fingerprint(source_id, dem)
    cache_file = CACHE_DIR / (fingerprint + '.json')
    with _LOCKS_GUARD:
        lock = _LOCKS.setdefault(source_id, threading.Lock())
    with lock:
        try:
            cached = json.loads(cache_file.read_text(encoding='utf-8'))
            if _valid_cached_result(cached, source_id):
                return cached
        except (OSError, ValueError, AttributeError):
            pass
        result = _build_coverage(source_id, dem)
        if _fingerprint(source_id, dem) != fingerprint:
            raise RuntimeError('Raster files changed while their coverage was being prepared.')
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        name = None
        try:
            with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', suffix='.tmp',
                                             dir=CACHE_DIR, delete=False) as temporary:
                name = temporary.name
                json.dump(result, temporary, ensure_ascii=False, allow_nan=False, separators=(',', ':'))
            os.replace(name, cache_file)
        finally:
            if name is not None and os.path.exists(name):
                os.unlink(name)
        # Return the same JSON-compatible types on cold and cached requests.
        return json.loads(cache_file.read_text(encoding='utf-8'))
