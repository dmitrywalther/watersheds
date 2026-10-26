"""Read-only audit of local DEM flow rasters; never reads whole raster bands.

Run with the project's Python environment. Output goes only to stdout. Cloud
placeholders are skipped, and GDAL sidecar discovery is disabled so a remote
overview cannot be downloaded accidentally.
"""

import argparse
import os
from contextlib import ExitStack
from pathlib import Path
import xml.etree.ElementTree as ET

# A PostGIS installation can point these variables at an incompatible PROJ DB.
# This changes only this audit process, before rasterio loads its own runtime.
for _key in ("PROJ_LIB", "PROJ_DATA"):
    os.environ.pop(_key, None)

import numpy as np
import rasterio
from rasterio.windows import Window
from rasterio.warp import transform


DEFAULT_ROOT = Path.home() / "Yandex.Disk" / "watershed" / "Data"
# OFFLINE, RECALL_ON_OPEN, RECALL_ON_DATA_ACCESS. Python may omit OFFLINE and
# REPARSE_POINT on Windows, but still exposes RECALL_ON_DATA_ACCESS.
CLOUD_FLAGS = 0x1000 | 0x40000 | 0x400000
CODES = (1, 2, 4, 8, 16, 32, 64, 128)
ESRI_STEPS = ((0, 1), (1, 1), (1, 0), (1, -1), (0, -1), (-1, -1), (-1, 0), (-1, 1))
WHITEBOX_STEPS = ((-1, 1), (0, 1), (1, 1), (1, 0), (1, -1), (0, -1), (-1, -1), (-1, 0))
SAMPLE_POINTS = ((37.6, 55.75), (48, 58), (50, 50), (83, 54), (92.9, 56), (129, 62), (150, 65))


def attributes(path):
    return getattr(path.stat(), "st_file_attributes", 0)


def is_local(path):
    return path.is_file() and not (attributes(path) & CLOUD_FLAGS)


def pair_path(direction):
    return direction.with_name(direction.name.replace("FlowDir", "FlowAcc"))


def local_window(dataset, lon, lat, size=64):
    x, y = transform("EPSG:4326", dataset.crs, [lon], [lat])
    row, col = dataset.index(x[0], y[0])
    start_row, start_col = row - size // 2, col - size // 2
    if start_row < 0 or start_col < 0:
        return None
    if start_row + size > dataset.height or start_col + size > dataset.width:
        return None
    return Window(start_col, start_row, size, size)


def compare_d8(direction, accumulation):
    counts = {"ESRI": [0, 0], "Whitebox": [0, 0]}
    values = set()
    windows = 0
    for lon, lat in SAMPLE_POINTS:
        window = local_window(direction, lon, lat)
        if window is None:
            continue
        fd = direction.read(1, window=window)
        acc = accumulation.read(1, window=window)
        windows += 1
        values.update(int(value) for value in np.unique(fd) if value != direction.nodata)
        source, pointers = acc[1:-1, 1:-1], fd[1:-1, 1:-1]
        for name, steps in (("ESRI", ESRI_STEPS), ("Whitebox", WHITEBOX_STEPS)):
            for code, (dr, dc) in zip(CODES, steps):
                target = acc[1 + dr:63 + dr, 1 + dc:63 + dc]
                valid = (pointers == code) & np.isfinite(source) & np.isfinite(target)
                valid &= (source != accumulation.nodata) & (target != accumulation.nodata)
                counts[name][0] += int(np.count_nonzero(valid))
                counts[name][1] += int(np.count_nonzero(valid & (target > source)))
    print(f"  D8 sample: {windows} windows of 64x64; codes={sorted(values)}")
    for name, (total, increasing) in counts.items():
        print(f"    {name}: FlowAcc increases for {increasing}/{total} valid transitions")


def smoke_point(direction, accumulation):
    window = local_window(direction, 37.62, 55.75, size=65)
    if window is None:
        print("  Moscow smoke point: outside raster extent")
        return
    fd = direction.read(1, window=window)
    acc = accumulation.read(1, window=window)
    valid = np.isin(fd, CODES) & (acc >= 100) & (acc <= 1000)
    rows, cols = np.where(valid)
    if not len(rows):
        print("  Moscow smoke point: no candidate in 65x65 window")
        return
    nearest = int(np.argmin((rows - 32) ** 2 + (cols - 32) ** 2))
    rr, cc = int(rows[nearest]), int(cols[nearest])
    row, col = int(window.row_off) + rr, int(window.col_off) + cc
    x, y = direction.xy(row, col)
    lon, lat = transform(direction.crs, "EPSG:4326", [x], [y])
    print(f"  Moscow smoke point: lat={lat[0]:.14f}, lon={lon[0]:.14f}; "
          f"FlowAcc={float(acc[rr, cc]):g} cells; FlowDir={int(fd[rr, cc])}; "
          f"row={row}, col={col}; candidates={len(rows)}")


def metadata(path):
    print(f"\n{path.name}: attributes={attributes(path)}; bytes={path.stat().st_size}")
    if not is_local(path):
        print("  SKIPPED: cloud recall/offline attribute; raster not opened")
        return
    with rasterio.open(path) as dataset:
        print(f"  CRS={dataset.crs}; shape={dataset.shape}; resolution={dataset.res}")
        print(f"  bounds={tuple(dataset.bounds)}; transform={tuple(dataset.transform)[:6]}")
        print(f"  dtype={dataset.dtypes}; nodata={dataset.nodata}; blocks={dataset.block_shapes}")
    lineage = Path(str(path) + ".xml")
    if is_local(lineage):
        for process in ET.parse(lineage).getroot().iter("Process"):
            print(f"  provenance={process.text}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", nargs="?", type=Path, default=DEFAULT_ROOT)
    args = parser.parse_args()
    if not args.source.is_dir():
        parser.error(f"Source directory does not exist: {args.source}")
    rasters = sorted(path for path in args.source.glob("*.tif")
                     if any(token in path.name for token in ("FlowDir", "FlowAcc", "d8_pntr", "ALOS_test")))
    print(f"Source: {args.source}")
    print("Metadata and bounded samples only; no raster output or statistics recomputation.")
    with rasterio.Env(GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR", GDAL_PAM_ENABLED="NO"):
        for path in rasters:
            metadata(path)
        print("\nPAIRS")
        for direction in rasters:
            if "FlowDir" not in direction.name:
                continue
            accumulation = pair_path(direction)
            print(f"\n{direction.name} + {accumulation.name}")
            if not accumulation.is_file():
                print("  INCOMPLETE: accumulation file missing")
                continue
            if not is_local(direction) or not is_local(accumulation):
                print("  SKIPPED: one of the pair is cloud/offline")
                continue
            with ExitStack() as stack:
                fd = stack.enter_context(rasterio.open(direction))
                acc = stack.enter_context(rasterio.open(accumulation))
                matches = (fd.crs == acc.crs, fd.shape == acc.shape, fd.transform == acc.transform)
                print(f"  same CRS/shape/transform={matches}")
                if not all(matches):
                    print("  INCOMPATIBLE: sampling pair skipped")
                    continue
                compare_d8(fd, acc)
                smoke_point(fd, acc)


if __name__ == "__main__":
    main()
