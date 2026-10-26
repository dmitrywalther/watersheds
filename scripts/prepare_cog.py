"""Prepare and fully verify a lossless DEFLATE or ZSTD Cloud Optimized GeoTIFF."""

import argparse
import json
import os
from pathlib import Path
import sys

for key in ('PROJ_LIB', 'PROJ_DATA'):
    os.environ.pop(key, None)
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.cog_preparation import prepare_cog


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', required=True, type=Path, help='Original raster, opened read-only.')
    parser.add_argument('--output', required=True, type=Path, help='New COG path; never overwritten.')
    parser.add_argument('--compression', choices=['DEFLATE', 'ZSTD'], default='DEFLATE')
    args = parser.parse_args()
    report = prepare_cog(args.input, args.output, compression=args.compression,
                         on_progress=lambda value: print(json.dumps(value, ensure_ascii=True), flush=True))
    print(json.dumps({'phase': 'complete', 'report': report}, ensure_ascii=True), flush=True)


if __name__ == '__main__':
    main()
