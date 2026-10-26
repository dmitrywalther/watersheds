"""Audit and prepare the explicit soil, land-cover and monthly climate COG inventory.

Run without flags for a metadata-only audit. --convert creates new ZSTD COGs,
verifies every native pixel/mask, and independently inspects TIFF structure.
Use --staged-groups soil when the conservative direct disk budget is too large.
--verify-existing rechecks metadata, structure, and original file signatures.
Original files and sidecars are retained; existing outputs are never replaced.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
# A machine-wide PROJ installation can be incompatible with rasterio's GDAL.
os.environ.pop('PROJ_LIB', None)
os.environ.pop('PROJ_DATA', None)

import rasterio

from config import is_cloud_placeholder
from services.cog_preparation import COG_ENV, prepare_cog
from scripts.inspect_cog_layout import inspect_cog_layout


INVENTORY = [
    ('soil', ROOT / 'data/soil/soil_map_M2_5.tif'),
    ('landcover', ROOT / 'data/landcover/GLOBCOVER_Clip_Project.tif'),
] + [
    ('precipitation' if variable == 'prec' else 'temperature',
     ROOT / f'data/climate/c1_30s_{variable}_{month:02d}_Clip_Project.tif')
    for variable in ('prec', 'tavg') for month in range(1, 13)
]
ARTIFACTS = ROOT / 'artifacts/thematic-cog'


def file_signatures(source):
    candidates = [source, source.with_suffix('.tfw')]
    candidates.extend(Path(str(source) + suffix) for suffix in
                      ('.msk', '.ovr', '.aux.xml', '.vat.dbf', '.vat.cpg'))
    result = {}
    for path in candidates:
        if path.is_file():
            info = path.stat()
            result[str(path)] = {
                'size_bytes': info.st_size, 'mtime_ns': info.st_mtime_ns,
                'file_attributes': getattr(info, 'st_file_attributes', 0),
                'cloud_placeholder': is_cloud_placeholder(path),
            }
    return result


def semantic_metadata(path, normal=False):
    environment = dict(COG_ENV)
    if normal:
        environment.update(GDAL_PAM_ENABLED='YES', GDAL_DISABLE_READDIR_ON_OPEN='FALSE')
    with rasterio.Env(**environment), rasterio.open(path) as src:
        colormaps = []
        for band in src.indexes:
            try:
                colormaps.append(src.colormap(band))
            except ValueError:
                colormaps.append(None)
        gcps, gcp_crs = src.gcps
        return {
            'shape': list(src.shape), 'count': src.count, 'dtypes': list(src.dtypes),
            'crs': str(src.crs), 'transform': list(src.transform),
            'nodatavals': list(src.nodatavals),
            'scales': list(src.scales), 'offsets': list(src.offsets),
            'units': list(src.units), 'descriptions': list(src.descriptions),
            'colorinterp': [item.name for item in src.colorinterp],
            'mask_flags': [[item.name for item in flags] for flags in src.mask_flag_enums],
            'colormaps': colormaps,
            'gcps': [str(item) for item in gcps], 'gcp_crs': str(gcp_crs),
            'rpcs': str(src.rpcs),
            'area_or_point': src.tags().get('AREA_OR_POINT'),
        }


def supplementary_metadata(path, normal=False):
    environment = dict(COG_ENV)
    if normal:
        environment.update(GDAL_PAM_ENABLED='YES', GDAL_DISABLE_READDIR_ON_OPEN='FALSE')
    with rasterio.Env(**environment), rasterio.open(path) as src:
        return {
            'tags': src.tags(),
            'band_tags': {str(band): src.tags(band) for band in src.indexes},
            'block_shapes': [list(shape) for shape in src.block_shapes],
            'overviews': {str(band): src.overviews(band) for band in src.indexes},
        }


def save_json(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + '\n',
                    encoding='utf-8')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--convert', action='store_true')
    parser.add_argument('--verify-existing', action='store_true',
                        help='Verify metadata, structure and source signatures for existing outputs.')
    parser.add_argument('--groups', nargs='+', choices=('soil', 'landcover', 'precipitation', 'temperature'),
                        help='Restrict conversion groups; metadata audit still covers all sources.')
    parser.add_argument('--staged-groups', nargs='+', choices=('soil', 'landcover', 'precipitation', 'temperature'),
                        default=[], help='Use compressed native staging for specified groups.')
    parser.add_argument('--report', type=Path, default=ARTIFACTS / 'conversion.json')
    args = parser.parse_args()
    if args.convert and args.verify_existing:
        parser.error('--convert and --verify-existing are mutually exclusive')
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    audit_path = ARTIFACTS / 'source-audit.json'
    previous_audit = json.loads(audit_path.read_text(encoding='utf-8')) if audit_path.exists() else None
    original_signatures = ({record['source']: record['source_files']
                            for record in previous_audit['rasters']} if previous_audit else {})
    audit = {'started_at': datetime.now(timezone.utc).isoformat(), 'rasters': []}
    for group, source in INVENTORY:
        signatures = file_signatures(source)
        if str(source) in original_signatures and signatures != original_signatures[str(source)]:
            raise ValueError(f'Source or sidecar signatures differ from initial audit: {source}')
        if not source.is_file() or any(item['cloud_placeholder'] for item in signatures.values()):
            raise ValueError(f'Missing source or cloud placeholder: {source}')
        isolated = semantic_metadata(source)
        normal = semantic_metadata(source, normal=True)
        differences = {key: {'isolated': value, 'normal': normal[key]}
                       for key, value in isolated.items() if value != normal[key]}
        record = {
            'group': group, 'source': str(source), 'source_files': signatures,
            'semantic_metadata': isolated, 'normal_semantic_metadata': normal,
            'semantic_differences': differences,
            'isolated_supplementary_metadata': supplementary_metadata(source),
            'normal_supplementary_metadata': supplementary_metadata(source, normal=True),
        }
        audit['rasters'].append(record)
        print(f'AUDIT {group} {source.name}: {isolated["dtypes"]} {isolated["shape"]}; semantic differences={list(differences)}', flush=True)
    if previous_audit is None:
        save_json(audit_path, audit)
    if any(record['semantic_differences'] for record in audit['rasters']):
        raise ValueError('Normal GDAL read and isolated source metadata differ; review source-audit.json.')
    if args.verify_existing:
        verification = {'checked_at': datetime.now(timezone.utc).isoformat(), 'rasters': []}
        for record in audit['rasters']:
            if args.groups and record['group'] not in args.groups:
                continue
            source = Path(record['source'])
            destination = source.parent / 'cog/zstd' / source.name
            if semantic_metadata(destination) != record['semantic_metadata']:
                raise ValueError(f'Output semantic metadata changed: {destination}')
            output_extra = supplementary_metadata(destination)
            for key in ('tags', 'band_tags'):
                if output_extra[key] != record['isolated_supplementary_metadata'][key]:
                    raise ValueError(f'Output intrinsic {key} changed: {destination}')
            manifest = json.loads(Path(str(destination) + '.cog.json').read_text(encoding='utf-8'))
            if manifest['source_size_bytes'] != source.stat().st_size or manifest['source_mtime_ns'] != source.stat().st_mtime_ns:
                raise ValueError(f'Source signature differs from conversion manifest: {source}')
            if manifest['output_size_bytes'] != destination.stat().st_size:
                raise ValueError(f'Output size differs from conversion manifest: {destination}')
            verification['rasters'].append({
                'source': str(source), 'output': str(destination),
                'semantic_metadata_preserved': True, 'intrinsic_tags_preserved': True,
                'source_and_sidecar_signatures_unchanged': True,
                'independent_structure': inspect_cog_layout(destination),
            })
        save_json(ARTIFACTS / 'metadata-verification.json', verification)
        print(f'METADATA AND STRUCTURE VERIFIED: {len(verification["rasters"])} rasters', flush=True)
        return
    if not args.convert:
        return

    report = {'started_at': datetime.now(timezone.utc).isoformat(), 'rasters': []}
    started = time.perf_counter()
    for record in audit['rasters']:
        if args.groups and record['group'] not in args.groups:
            continue
        source = Path(record['source'])
        destination = source.parent / 'cog/zstd' / source.name
        last_percentage = -1

        def progress(event):
            nonlocal last_percentage
            if event['phase'] == 'validate_native':
                percentage = int(event['rows_done'] * 100 / event['rows']) // 20 * 20
                if percentage <= last_percentage:
                    return
                last_percentage = percentage
                print(f'VERIFY {source.name}: {percentage}%', flush=True)
            else:
                print(json.dumps(event, ensure_ascii=False), flush=True)

        conversion = prepare_cog(source, destination, compression='ZSTD', on_progress=progress,
                                 staged=record['group'] in args.staged_groups)
        output_metadata = semantic_metadata(destination)
        if output_metadata != record['semantic_metadata']:
            differences = {key: {'source': value, 'output': output_metadata[key]}
                           for key, value in record['semantic_metadata'].items()
                           if value != output_metadata[key]}
            raise ValueError(f'Output semantic metadata changed for {source}: {differences}')
        if record['source_files'] != file_signatures(source):
            raise ValueError(f'Source or sidecars changed: {source}')
        record_report = {
            'group': record['group'], **conversion,
            'semantic_metadata_preserved': True,
            'source_and_sidecar_signatures_unchanged': True,
            'independent_structure': inspect_cog_layout(destination),
        }
        report['rasters'].append(record_report)
        save_json(args.report, report)

    for record in audit['rasters']:
        if record['source_files'] != file_signatures(Path(record['source'])):
            raise ValueError(f'Source changed after conversion: {record["source"]}')
    groups = {}
    for record in report['rasters']:
        group = groups.setdefault(record['group'], {
            'count': 0, 'source_bytes': 0, 'cog_bytes': 0,
            'conversion_seconds': 0, 'validation_seconds': 0,
        })
        group['count'] += 1
        group['source_bytes'] += record['source_size_bytes']
        group['cog_bytes'] += record['output_size_bytes']
        group['conversion_seconds'] += record['conversion_seconds']
        group['validation_seconds'] += record['validation_seconds']
    report.update(groups=groups, elapsed_seconds=time.perf_counter() - started,
                  all_source_and_sidecar_signatures_unchanged=True,
                  completed_at=datetime.now(timezone.utc).isoformat())
    report['totals'] = {key: sum(group[key] for group in groups.values())
                        for key in next(iter(groups.values()))}
    save_json(args.report, report)
    print(json.dumps({'totals': report['totals'], 'groups': groups}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
