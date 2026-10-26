"""Capture fresh terrain results, then compare after verified COG activation.

Reads raster data only. Full API responses, native statistics, interval-map
geometries/legends, hover responses, and unaligned coarse sampling grids are
compared exactly. Run baseline before switching configuration; compare in a
fresh Python process against the restarted backend afterwards.
"""

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import sys
import time
from urllib.parse import urlencode, urlparse
from urllib.request import urlopen

for key in ('PROJ_LIB', 'PROJ_DATA'):
    os.environ.pop(key, None)
os.environ['GDAL_PAM_ENABLED'] = 'NO'
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

import geopandas as gpd
import numpy as np
from pyproj import Transformer
import rasterio
from rasterio.mask import raster_geometry_mask
from rasterio.warp import Resampling
from rasterio.windows import Window
from rasterstats import zonal_stats
from shapely.geometry import Point, Polygon, box, mapping, shape
from shapely.ops import unary_union

import config
from services.analysis import run_analysis
from services.categorical_overlay import _sample_grid
from services.flood import _reproject_to_grid
from services.soil import RASTER_ENV
from services.terrain_overlay import build_elevation_map, build_slope_map
from services.terrain_sample import sample_terrain_value
from start import app


def read_json(path):
    return json.loads(path.read_text(encoding='utf-8'))


def json_value(value):
    return json.loads(json.dumps(value, ensure_ascii=False, allow_nan=False))


def save_new(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write('\n')


def original_signatures():
    return {str(path.relative_to(ROOT)): {'size_bytes': path.stat().st_size,
                                         'mtime_ns': path.stat().st_mtime_ns}
            for folder in ('heights', 'slopes')
            for path in sorted((ROOT / 'data' / folder).iterdir()) if path.is_file()}


def active_paths():
    return {'elevation': str(config.ELEVATION_RASTER), 'slope': str(config.SLOPE_RASTER)}


def native_stats(basin, paths):
    return {kind: zonal_stats(basin, path, stats='count min max mean sum std')[0]
            for kind, path in paths.items()}


def flood_grid_inputs(basin, source_id, elevation_path):
    """Reproduce HAND's elevation input without writing rasters or using OSM."""
    result = {}
    for grid_id in dict.fromkeys((source_id, 'ETOPO1_1min')):
        flow_path = config.DEM_FILES[grid_id]['flow_acc']
        with rasterio.Env(**RASTER_ENV), rasterio.open(flow_path) as src:
            native = basin.to_crs(src.crs)
            _, transform, window = raster_geometry_mask(src, native.geometry.values, crop=True)
            width, height = int(window.width), int(window.height)
            values, nodata = _reproject_to_grid(elevation_path, transform, width, height,
                                              src.crs, Resampling.bilinear)
            valid = np.isfinite(values)
            if nodata is not None:
                valid &= values != np.float32(nodata)
            result[grid_id] = {'shape': [height, width], 'transform': list(transform),
                               'crs': str(src.crs), 'nodata': nodata,
                               'data_sha256': hashlib.sha256(values.tobytes(order='C')).hexdigest(),
                               'valid_mask_sha256': hashlib.sha256(valid.tobytes(order='C')).hexdigest(),
                               'valid_count': int(valid.sum()),
                               'min': float(values[valid].min()) if valid.any() else None,
                               'max': float(values[valid].max()) if valid.any() else None}
    return result


def get_json(client, path, base_url):
    if base_url:
        with urlopen(base_url.rstrip('/') + path, timeout=240) as response:
            return json.load(response)
    response = client.get(path)
    if response.status_code != 200:
        raise RuntimeError(f'{path}: {response.status_code} {response.get_json()}')
    return response.get_json()


def hover_cases(geometry, paths, client=None, base_url=None):
    minx, miny, maxx, maxy = geometry.bounds
    points = [('inside', geometry.representative_point()),
              ('outside_basin', Point(maxx + max(.01, (maxx-minx) * .1), maxy)),
              ('outside_coverage', Point(0, 0))]
    result = []
    for name, point in points:
        for kind, path in paths.items():
            local = sample_terrain_value(point.y, point.x, kind, path)
            record = {'name': name, 'layer': kind, 'longitude': point.x,
                      'latitude': point.y, 'inside_basin': geometry.covers(point),
                      'native_sample': local}
            if client is not None:
                query = urlencode({'latitude': point.y, 'longitude': point.x, 'layer': kind})
                record['api_response'] = get_json(client, '/terrain_value?' + query, base_url)
                record['api_matches_native'] = record['api_response'] == local
            result.append(record)
    return result


def synthetic_cases():
    outer = box(37.4, 55.6, 37.8, 55.9)
    inner = box(37.52, 55.69, 37.68, 55.81)
    return [
        {'name': 'Moscow polygon with interior hole',
         'geometry': mapping(Polygon(outer.exterior.coords, [inner.exterior.coords])),
         'hole': mapping(inner), 'full_analysis': True},
        {'name': 'Outside raster coverage', 'geometry': mapping(box(0, 0, .03, .03)),
         'full_analysis': True},
        {'name': 'Large generalized terrain map', 'geometry': mapping(box(31.123, 53.217, 45.389, 62.671)),
         'full_analysis': False},
    ]


def map_summary(analysis):
    return {layer: {key: analysis[layer][key] for key in
                    ('status', 'approximate', 'sample_resolution_m', 'range', 'unit')} |
                   {'features': len(analysis[layer]['geometry']['features']),
                    'bands': len(analysis[layer]['bands'])}
            for layer in ('elevation_map', 'slope_map')}


def layer_checks(analysis, geometry, hole=None, no_data=False, approximate=False):
    checks = {}
    for layer in ('elevation_map', 'slope_map'):
        value = analysis[layer]
        features = value['geometry']['features']
        checks[layer + '_bounded_to_basin'] = all(
            shape(feature['geometry']).difference(geometry.buffer(1e-10)).is_empty
            for feature in features)
        if hole is not None:
            checks[layer + '_hole_preserved'] = all(
                shape(feature['geometry']).intersection(hole.buffer(-1e-10)).is_empty
                for feature in features)
        if no_data:
            checks[layer + '_no_data'] = value['status'] == 'no_data' and not features
        else:
            checks[layer + '_available'] = value['status'] == 'ok' and bool(features)
        if approximate:
            checks[layer + '_generalized'] = value['approximate'] is True
    return checks


def sample_grid_audit(paths):
    """Exercise 512-tile boundaries, partial cells and different stride parity.

    Every sampled grid is also checked against independently selected native
    values. There is deliberately no out_shape argument: display sampling must
    not change when internal overviews are present in the COG.
    """
    result = {}
    for kind, path in paths.items():
        with rasterio.Env(**RASTER_ENV), rasterio.open(path) as src:
            x, y = Transformer.from_crs('EPSG:4326', src.crs, always_xy=True).transform(37.65, 55.75)
            row, col = src.index(x, y)
            windows = [Window(col-257, row-259, 777, 1031),
                       Window(509, 511, 1027, 769),
                       Window(src.width-773, src.height-517, 773, 517)]
            records = []
            for window in windows:
                full_values = src.read(1, window=window)
                full_valid = src.read_masks(1, window=window) != 0
                if src.nodata is not None:
                    full_valid &= full_values != src.nodata
                for stride in (1, 3, 4, 17):
                    values, valid, transform = _sample_grid(src, None, window, stride, ())
                    rows = np.minimum(np.arange(math.ceil(window.height/stride))*stride+stride//2,
                                      int(window.height)-1).astype(int)
                    cols = np.minimum(np.arange(math.ceil(window.width/stride))*stride+stride//2,
                                      int(window.width)-1).astype(int)
                    records.append({
                        'window': list(window.flatten()), 'stride': stride, 'shape': list(values.shape),
                        'transform': list(transform), 'dtype': values.dtype.str,
                        'data_sha256': hashlib.sha256(values.tobytes(order='C')).hexdigest(),
                        'mask_sha256': hashlib.sha256(valid.tobytes(order='C')).hexdigest(),
                        'valid_count': int(valid.sum()),
                        'native_selection_exact': np.array_equal(values, full_values[np.ix_(rows, cols)]),
                        'native_mask_exact': np.array_equal(valid, full_valid[np.ix_(rows, cols)]),
                    })
            result[kind] = records
    return result


def checks_against(old, record, payload_key):
    prior_analysis = old[payload_key]['analysis'] if payload_key == 'payload' else old[payload_key]
    analysis = record[payload_key]['analysis'] if payload_key == 'payload' else record[payload_key]
    checks = {'complete_result_exact': old[payload_key] == record[payload_key],
              'terrain_statistics_exact': prior_analysis['terrain'] == analysis['terrain'],
              'native_statistics_exact': old['native_terrain'] == record['native_terrain'],
              'elevation_map_and_legend_exact': prior_analysis['elevation_map'] == analysis['elevation_map'],
              'slope_map_and_legend_exact': prior_analysis['slope_map'] == analysis['slope_map'],
              'hover_responses_exact': old['hover'] == record['hover']}
    if 'flood_grid_inputs' in record:
        checks['flood_resampled_elevation_exact'] = old['flood_grid_inputs'] == record['flood_grid_inputs']
    return checks


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('baseline', 'compare'))
    parser.add_argument('--directory', type=Path, default=ROOT / 'artifacts' / 'terrain-cog')
    parser.add_argument('--live-base-url')
    args = parser.parse_args()
    target = args.directory / ('regression-baseline.json' if args.mode == 'baseline' else 'regression-comparison.json')
    if target.exists():
        raise SystemExit(f'Refusing to overwrite {target}')
    prior = read_json(args.directory / 'regression-baseline.json') if args.mode == 'compare' else None
    paths = active_paths()
    if args.mode == 'baseline' and any('cog' in Path(path).parts for path in paths.values()):
        raise SystemExit('Baseline requires the original terrain paths')
    originals = original_signatures()
    result = {'created_utc': datetime.now(timezone.utc).isoformat(), 'mode': args.mode,
              'transport': args.live_base_url or 'Flask test client (fresh Python process)',
              'active_paths': paths, 'original_files': originals,
              'cases': [], 'synthetic_cases': []}
    fixture = read_json(ROOT / 'artifacts' / 'map-hover' / 'before-api.json')
    with app.test_client() as client:
        for case in fixture['cases']:
            started = time.perf_counter()
            query = urlparse(case['url']).query
            payload = get_json(client, '/get_watershed?' + query, args.live_base_url)
            geometry = unary_union([shape(feature['geometry']) for feature in payload['watershed']['features']])
            basin = gpd.GeoDataFrame(geometry=[geometry], crs='EPSG:4326').to_crs('EPSG:3395')
            record = {'source_id': case['source_id'], 'point': case['point'], 'query': query,
                      'payload': payload, 'native_terrain': native_stats(basin, paths),
                      'flood_grid_inputs': flood_grid_inputs(basin, case['source_id'], paths['elevation']),
                      'hover': hover_cases(geometry, paths, client, args.live_base_url),
                      'layer_checks': layer_checks(payload['analysis'], geometry)}
            if prior:
                old = next(item for item in prior['cases'] if item['query'] == query)
                record['checks'] = checks_against(old, record, 'payload')
            record['seconds'] = round(time.perf_counter()-started, 6)
            result['cases'].append(record)
            print(json.dumps({'case': case['point'], 'source': case['source_id'],
                              'seconds': record['seconds'], 'checks': record.get('checks'),
                              'layer_checks': record['layer_checks'],
                              'maps': map_summary(payload['analysis'])}), flush=True)
        for case in synthetic_cases():
            started = time.perf_counter()
            geometry = shape(case['geometry'])
            basin = gpd.GeoDataFrame(geometry=[geometry], crs='EPSG:4326').to_crs('EPSG:3395')
            if case['full_analysis']:
                analysis = json_value(run_analysis(basin))
            else:
                analysis = json_value({'terrain': {kind: zonal_stats(basin, path)[0]
                                                    for kind, path in paths.items()},
                                       'elevation_map': build_elevation_map(basin),
                                       'slope_map': build_slope_map(basin)})
            record = case | {'analysis': analysis, 'native_terrain': native_stats(basin, paths),
                             'hover': hover_cases(geometry, paths, client, args.live_base_url),
                             'layer_checks': layer_checks(analysis, geometry,
                                 hole=shape(case['hole']) if 'hole' in case else None,
                                 no_data=case['name'] == 'Outside raster coverage',
                                 approximate=not case['full_analysis'])}
            if prior:
                old = next(item for item in prior['synthetic_cases'] if item['name'] == case['name'])
                record['checks'] = checks_against(old, record, 'analysis')
            record['seconds'] = round(time.perf_counter()-started, 6)
            result['synthetic_cases'].append(record)
            print(json.dumps({'case': case['name'], 'seconds': record['seconds'],
                              'checks': record.get('checks'), 'layer_checks': record['layer_checks'],
                              'maps': map_summary(analysis)}), flush=True)
    result['sampling_grids'] = sample_grid_audit(paths)
    result['original_files_unchanged_during_run'] = originals == original_signatures()
    if prior:
        result['original_files_unchanged_since_baseline'] = prior['original_files'] == originals
        result['both_active_paths_use_cog'] = all('cog' in Path(path).parts for path in paths.values())
        result['all_sampling_grids_exact'] = prior['sampling_grids'] == result['sampling_grids']
    checks = [result['original_files_unchanged_during_run'],
              result.get('original_files_unchanged_since_baseline', True),
              result.get('both_active_paths_use_cog', True),
              result.get('all_sampling_grids_exact', True)]
    for record in result['cases'] + result['synthetic_cases']:
        checks.extend(record['layer_checks'].values())
        checks.extend(record.get('checks', {}).values())
        checks.extend(item['api_matches_native'] for item in record['hover'])
    checks.extend(item[key] for items in result['sampling_grids'].values() for item in items
                  for key in ('native_selection_exact', 'native_mask_exact'))
    result['all_passed'] = all(checks)
    save_new(target, result)
    print(json.dumps({'all_passed': result['all_passed'], 'report': str(target)}), flush=True)
    if not result['all_passed']:
        raise SystemExit('Terrain regression checks failed; inspect the report')


if __name__ == '__main__':
    main()
