"""Save fresh thematic analysis baselines, then compare after COG activation.

Only point coordinates are reused from the hover fixtures. Every analysis and
GeoJSON response is calculated anew. This script never calls flood/OSM APIs and
never writes raster data. Run baseline before changing the active paths, then
comparison in a fresh Python process after configuration has switched.
"""

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import time
from urllib.parse import urlparse
from urllib.request import urlopen

for key in ('PROJ_LIB', 'PROJ_DATA'):
    os.environ.pop(key, None)
os.environ['GDAL_PAM_ENABLED'] = 'NO'
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

import geopandas as gpd
from rasterstats import zonal_stats
from shapely.geometry import Polygon, box, mapping, shape
from shapely.ops import unary_union

import config
from services.analysis import run_analysis
from start import app


def read_json(path):
    return json.loads(path.read_text(encoding='utf-8'))


def save_new(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write('\n')


def signature(path):
    stat = path.stat()
    return {'size_bytes': stat.st_size, 'mtime_ns': stat.st_mtime_ns}


def original_signatures():
    return {str(path.relative_to(ROOT)): signature(path)
            for name in ('soil', 'landcover', 'climate')
            for path in sorted((ROOT / 'data' / name).iterdir())
            if path.is_file() and ('.tif' in path.name or path.suffix == '.tfw')}


def climate_paths():
    # New configuration supplies absolute templates; the baseline predates it.
    templates = {
        'tavg': getattr(config, 'CLIMATE_TEMPERATURE_TEMPLATE',
                        str(ROOT / 'data' / 'climate' / 'c1_30s_tavg_{month}_Clip_Project.tif')),
        'prec': getattr(config, 'CLIMATE_RAINFALL_TEMPLATE',
                        str(ROOT / 'data' / 'climate' / 'c1_30s_prec_{month}_Clip_Project.tif')),
    }
    return {f'{kind}_{month:02d}': templates[kind].format(month=f'{month:02d}')
            for kind in ('tavg', 'prec') for month in range(1, 13)}


def native_climate(basin, paths):
    # Native rasterstats reads (no out_shape or overview requests); retain extra
    # statistics to catch changes even if a rounded monthly mean were equal.
    return {key: zonal_stats(basin, path, stats='count min max mean sum')[0]
            for key, path in paths.items()}


def json_value(value):
    return json.loads(json.dumps(value, ensure_ascii=False, allow_nan=False))


def synthetic_cases():
    outer = box(37.4, 55.6, 37.8, 55.9)
    inner = box(37.52, 55.69, 37.68, 55.81)
    holed = Polygon(outer.exterior.coords, [inner.exterior.coords])
    return [
        {'name': 'Moscow polygon with interior hole', 'geometry': mapping(holed),
         'hole': mapping(inner)},
        {'name': 'Outside Russian thematic coverage', 'geometry': mapping(box(0, 0, .03, .03))},
    ]


def summarize_analysis(analysis):
    return {'area_km2': analysis['area_km2'],
            'soil_status': analysis['soil']['status'],
            'soil_classes': len(analysis['soil']['categories']),
            'landcover_classes': len(analysis['landcover']),
            'soil_map_status': analysis['soil_map']['status'],
            'soil_map_features': len(analysis['soil_map']['geometry']['features']),
            'landcover_map_status': analysis['landcover_map']['status'],
            'landcover_map_features': len(analysis['landcover_map']['geometry']['features']),
            'climate_months': {key: len(value) for key, value in analysis['climate'].items()}}


def compare_items(before, after):
    return {
        'entire_response_exact': before['payload'] == after['payload'],
        'watershed_geometry_exact': before['payload']['watershed'] == after['payload']['watershed'],
        'all_analysis_exact': before['payload']['analysis'] == after['payload']['analysis'],
        'soil_stats_exact': before['payload']['analysis']['soil'] == after['payload']['analysis']['soil'],
        'soil_map_exact': before['payload']['analysis']['soil_map'] == after['payload']['analysis']['soil_map'],
        'landcover_stats_exact': before['payload']['analysis']['landcover'] == after['payload']['analysis']['landcover'],
        'landcover_map_exact': before['payload']['analysis']['landcover_map'] == after['payload']['analysis']['landcover_map'],
        'climate_series_exact': before['payload']['analysis']['climate'] == after['payload']['analysis']['climate'],
        'native_climate_statistics_exact': before['native_climate'] == after['native_climate'],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('baseline', 'compare'))
    parser.add_argument('--directory', type=Path, default=ROOT / 'artifacts' / 'thematic-cog')
    parser.add_argument('--live-base-url', help='Use the restarted local Flask HTTP server instead of a test client.')
    args = parser.parse_args()
    target = args.directory / ('regression-baseline.json' if args.mode == 'baseline' else 'regression-comparison.json')
    if target.exists():
        raise SystemExit(f'Refusing to overwrite {target}')
    prior = read_json(args.directory / 'regression-baseline.json') if args.mode == 'compare' else None
    fixture = read_json(ROOT / 'artifacts' / 'map-hover' / 'before-api.json')
    originals_before = original_signatures()
    climate = climate_paths()
    result = {'created_utc': datetime.now(timezone.utc).isoformat(), 'mode': args.mode,
              'transport': args.live_base_url or 'Flask test client (fresh Python process)',
              'active_paths': {'soil': str(config.SOIL_RASTER), 'landcover': str(config.LANDCOVER_RASTER),
                               'climate': climate},
              'original_files': originals_before, 'cases': [], 'synthetic_cases': []}
    with app.test_client() as client:
        for case in fixture['cases']:
            url = urlparse(case['url'])
            started = time.perf_counter()
            if args.live_base_url:
                with urlopen(args.live_base_url.rstrip('/') + '/get_watershed?' + url.query, timeout=180) as response:
                    payload = json.load(response)
            else:
                response = client.get('/get_watershed?' + url.query)
                if response.status_code != 200:
                    raise RuntimeError(f'{case["source_id"]}: {response.status_code} {response.get_json()}')
                payload = response.get_json()
            geometry = unary_union([shape(feature['geometry']) for feature in payload['watershed']['features']])
            basin = gpd.GeoDataFrame(geometry=[geometry], crs='EPSG:4326').to_crs('EPSG:3395')
            record = {'source_id': case['source_id'], 'point': case['point'], 'url': case['url'],
                      'payload': payload, 'native_climate': native_climate(basin, climate),
                      'seconds': round(time.perf_counter() - started, 6)}
            if prior:
                old = next(item for item in prior['cases'] if item['source_id'] == case['source_id'] and item['point'] == case['point'])
                record['checks'] = compare_items(old, record)
                record['passed'] = all(record['checks'].values())
            result['cases'].append(record)
            print(json.dumps({key: value for key, value in record.items() if key not in ('payload', 'native_climate')} |
                             {'analysis': summarize_analysis(payload['analysis'])}, ensure_ascii=True), flush=True)

    for case in synthetic_cases():
        basin = gpd.GeoDataFrame(geometry=[shape(case['geometry'])], crs='EPSG:4326').to_crs('EPSG:3395')
        started = time.perf_counter()
        analysis = json_value(run_analysis(basin))
        record = case | {'analysis': analysis, 'native_climate': native_climate(basin, climate),
                         'seconds': round(time.perf_counter() - started, 6)}
        if 'hole' in case:
            hole = shape(case['hole'])
            # Reprojection round trips can shift an edge by a few floating
            # point ulps. Check the hole interior, excluding a sub-millimetre
            # geographic tolerance, and retain raw overlap areas for audit.
            record['hole_overlap_degrees2'] = {
                layer: sum(shape(feature['geometry']).intersection(hole).area
                           for feature in analysis[layer]['geometry']['features'])
                for layer in ('soil_map', 'landcover_map')}
            record['hole_checks'] = {
                layer: all(shape(feature['geometry']).intersection(hole.buffer(-1e-10)).is_empty
                           for feature in analysis[layer]['geometry']['features'])
                for layer in ('soil_map', 'landcover_map')}
        else:
            record['no_data_checks'] = {
                'soil_no_data': analysis['soil']['status'] == 'no_data',
                'soil_map_no_data': analysis['soil_map']['status'] == 'no_data',
                'landcover_map_no_data': analysis['landcover_map']['status'] == 'no_data',
                'climate_all_null': all(value is None for values in analysis['climate'].values() for value in values)}
        if prior:
            old = next(item for item in prior['synthetic_cases'] if item['name'] == case['name'])
            record['checks'] = {'analysis_exact': old['analysis'] == analysis,
                                'native_climate_statistics_exact': old['native_climate'] == record['native_climate']}
            record['passed'] = all(record['checks'].values())
        result['synthetic_cases'].append(record)
        print(json.dumps({key: value for key, value in record.items()
                          if key not in ('analysis', 'native_climate', 'geometry', 'hole')}, ensure_ascii=True), flush=True)

    result['original_files_unchanged_during_run'] = originals_before == original_signatures()
    if prior:
        result['original_files_unchanged_since_baseline'] = prior['original_files'] == originals_before
        result['all_26_active_paths_use_cog'] = all('cog' in Path(path).parts for path in
                                                  [str(config.SOIL_RASTER), str(config.LANDCOVER_RASTER), *climate.values()])
    result['all_passed'] = (result['original_files_unchanged_during_run']
                           and result.get('original_files_unchanged_since_baseline', True)
                           and result.get('all_26_active_paths_use_cog', True)
                           and all(item.get('passed', True) for item in result['cases'] + result['synthetic_cases'])
                           and all(all(item.get('hole_checks', {}).values()) and all(item.get('no_data_checks', {}).values())
                                   for item in result['synthetic_cases']))
    save_new(target, result)
    print(json.dumps({'all_passed': result['all_passed'], 'report': str(target)}, ensure_ascii=True), flush=True)
    if not result['all_passed']:
        raise SystemExit('Thematic COG regression failed')


if __name__ == '__main__':
    main()
