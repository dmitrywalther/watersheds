"""Audit and prepare lossless ZSTD COGs for the two analysis terrain rasters.

The default is a read-only metadata audit. --convert verifies every native
pixel and mask before publication; --verify-existing checks metadata and TIFF
structure against retained originals. Sources and all sidecars are preserved.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.pop('PROJ_LIB', None)
os.environ.pop('PROJ_DATA', None)
os.environ['GDAL_PAM_ENABLED'] = 'NO'

from services.cog_preparation import prepare_cog
from scripts.inspect_cog_layout import inspect_cog_layout
from scripts.prepare_thematic_cog import (
    file_signatures, semantic_metadata, supplementary_metadata, save_json,
)

INVENTORY = [
    ('heights', ROOT / 'data/heights/GMTED2010_30sec_Clip_Project_Fill.tif'),
    ('slopes', ROOT / 'data/slopes/GMTED2010_30sec_Clip_Project_Fill_Slope.tif'),
]
ARTIFACTS = ROOT / 'artifacts/terrain-cog'


def audit_sources():
    """Read intrinsic metadata with PAM disabled and check retained signatures."""
    audit_path = ARTIFACTS / 'source-audit.json'
    previous = json.loads(audit_path.read_text(encoding='utf-8')) if audit_path.exists() else None
    earlier = {item['source']: item['source_files'] for item in previous['rasters']} if previous else {}
    audit = {
        'created_utc': datetime.now(timezone.utc).isoformat(),
        'free_bytes_before': shutil.disk_usage(ROOT).free,
        'read_environment': {'GDAL_PAM_ENABLED': 'NO', 'GDAL_DISABLE_READDIR_ON_OPEN': 'EMPTY_DIR'},
        'rasters': [],
    }
    for group, source in INVENTORY:
        signatures = file_signatures(source)
        if not source.is_file() or any(item['cloud_placeholder'] for item in signatures.values()):
            raise ValueError(f'Missing source or cloud placeholder: {source}')
        if str(source) in earlier and earlier[str(source)] != signatures:
            raise ValueError(f'Source or sidecar changed since initial audit: {source}')
        metadata = semantic_metadata(source)
        extra = supplementary_metadata(source)
        if file_signatures(source) != signatures:
            raise ValueError(f'Source changed during audit: {source}')
        record = {'group': group, 'source': str(source), 'source_files': signatures,
                  'semantic_metadata': metadata, 'intrinsic_supplementary_metadata': extra}
        audit['rasters'].append(record)
        print(f'AUDIT {group}: {source.stat().st_size} bytes; '
              f'{metadata["dtypes"]} {metadata["shape"]}; nodata={metadata["nodatavals"]}; '
              f'masks={metadata["mask_flags"]}', flush=True)
    audit['source_bytes'] = sum(Path(item['source']).stat().st_size for item in audit['rasters'])
    audit['source_and_sidecar_file_count'] = sum(len(item['source_files']) for item in audit['rasters'])
    if previous is None:
        save_json(audit_path, audit)
    return audit


def check_output(record):
    source = Path(record['source'])
    destination = source.parent / 'cog/zstd' / source.name
    if semantic_metadata(destination) != record['semantic_metadata']:
        raise ValueError(f'Output semantic metadata differs: {destination}')
    extra = supplementary_metadata(destination)
    for key in ('tags', 'band_tags'):
        if extra[key] != record['intrinsic_supplementary_metadata'][key]:
            raise ValueError(f'Output intrinsic {key} differs: {destination}')
    if file_signatures(source) != record['source_files']:
        raise ValueError(f'Source or sidecar changed: {source}')
    manifest = json.loads(Path(str(destination) + '.cog.json').read_text(encoding='utf-8'))
    if (manifest['source'] != str(source) or manifest['output'] != str(destination)
            or manifest['source_size_bytes'] != source.stat().st_size
            or manifest['source_mtime_ns'] != source.stat().st_mtime_ns
            or manifest['output_size_bytes'] != destination.stat().st_size):
        raise ValueError(f'Manifest paths or file signatures differ: {source}')
    if (manifest['creation_options']['COMPRESS'] != 'ZSTD'
            or manifest['creation_options']['RESAMPLING'] != 'NEAREST'
            or not manifest['all_native_pixels_validated']
            or not manifest['all_native_masks_validated']
            or not manifest['semantic_metadata_validated']
            or manifest['source_native_sha256'] != manifest['output_native_sha256']
            or manifest['source_mask_sha256'] != manifest['output_mask_sha256']):
        raise ValueError(f'Manifest does not establish lossless native pixels and masks: {source}')
    return {
        'group': record['group'], 'source': str(source), 'output': str(destination),
        'semantic_metadata_preserved': True, 'intrinsic_tags_preserved': True,
        'source_and_sidecar_signatures_unchanged': True,
        'independent_structure': inspect_cog_layout(destination),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    operation = parser.add_mutually_exclusive_group()
    operation.add_argument('--convert', action='store_true')
    operation.add_argument('--verify-existing', action='store_true')
    args = parser.parse_args()
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    audit = audit_sources()
    if not args.convert and not args.verify_existing:
        return
    verification = {'checked_at': datetime.now(timezone.utc).isoformat(), 'rasters': []}
    if args.convert:
        report_path = ARTIFACTS / 'conversion.json'
        if report_path.exists():
            raise FileExistsError(f'Refusing to replace conversion report: {report_path}')
        report = {'started_at': datetime.now(timezone.utc).isoformat(), 'rasters': []}
        started = time.perf_counter()
        for record in audit['rasters']:
            source = Path(record['source'])
            destination = source.parent / 'cog/zstd' / source.name
            last_percentage = -1

            def progress(event):
                nonlocal last_percentage
                if event['phase'] == 'validate_native':
                    percentage = int(event['rows_done'] * 100 / event['rows']) // 10 * 10
                    if percentage <= last_percentage:
                        return
                    last_percentage = percentage
                    print(f'VERIFY {source.name}: {percentage}%', flush=True)
                else:
                    print(json.dumps(event, ensure_ascii=False), flush=True)

            conversion = prepare_cog(source, destination, compression='ZSTD', on_progress=progress)
            verified = check_output(record)
            report['rasters'].append({**conversion, **verified})
            verification['rasters'].append(verified)
            save_json(report_path, report)
        report['totals'] = {
            'count': len(report['rasters']),
            'source_bytes': sum(item['source_size_bytes'] for item in report['rasters']),
            'cog_bytes': sum(item['output_size_bytes'] for item in report['rasters']),
            'conversion_seconds': sum(item['conversion_seconds'] for item in report['rasters']),
            'validation_seconds': sum(item['validation_seconds'] for item in report['rasters']),
        }
        report.update(completed_at=datetime.now(timezone.utc).isoformat(),
                      elapsed_seconds=time.perf_counter() - started,
                      all_source_and_sidecar_signatures_unchanged=True)
        save_json(report_path, report)
        print(json.dumps(report['totals']), flush=True)
    else:
        verification['rasters'] = [check_output(record) for record in audit['rasters']]
    for record in audit['rasters']:
        if file_signatures(Path(record['source'])) != record['source_files']:
            raise ValueError(f'Source or sidecar changed: {record["source"]}')
    verification.update(all_passed=True, free_bytes_after=shutil.disk_usage(ROOT).free,
                        source_and_sidecar_file_count=audit['source_and_sidecar_file_count'])
    save_json(ARTIFACTS / 'metadata-verification.json', verification)
    print(f'METADATA AND STRUCTURE VERIFIED: {len(verification["rasters"])} rasters', flush=True)


if __name__ == '__main__':
    main()
