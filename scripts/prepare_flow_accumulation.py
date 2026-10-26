"""Prepare an existing catalog source: python scripts/prepare_flow_accumulation.py --source MERIT_3sec"""

import argparse
import json
import os
from pathlib import Path
import sys

for key in ('PROJ_LIB', 'PROJ_DATA'):
    os.environ.pop(key, None)
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import DEM_DIR, DEM_FILES
from services.flow_preparation import prepare_flow_accumulation


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', required=True, choices=DEM_FILES)
    args = parser.parse_args()
    spec = DEM_FILES[args.source]
    if spec['flow_dir_encoding'] != 'esri':
        parser.error('This preparation command currently supports ESRI D8 only.')
    result = prepare_flow_accumulation(
        spec['flow_dir'], DEM_DIR / spec['flow_acc'].name,
        on_progress=lambda item: print(json.dumps(item, ensure_ascii=True), flush=True),
    )
    print(json.dumps(result, ensure_ascii=True), flush=True)


if __name__ == '__main__':
    main()
