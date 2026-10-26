"""Compare real catchments and analyses without changing the application config.

Run from the project root with its Python environment. Baseline samples only
bounded windows of the original GMTED2010 7.5 second rasters. Compare mode runs
the original and each COG pair in rotating order; it does not flush OS caches.
"""

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import statistics
import sys
import time
from unittest.mock import patch

os.environ.pop("PROJ_LIB", None)
os.environ.pop("PROJ_DATA", None)
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import numpy as np
import rasterio
from rasterio.warp import transform
from rasterio.windows import Window
from rasterio.crs import CRS
from shapely import wkb

from config import DEM_FILES
from services import watershed
from services.analysis import run_analysis
from services.dem_catalog import RASTER_ENV, get_dem_config


SOURCE = "GMTED2010_7_5sec"
DEFAULT_BASELINE = ROOT / "artifacts" / "cog-benchmark" / "original_watersheds.json"
REGIONS = [
    ("Moscow", 55.74658358585305, 37.65629073793753, 1000, True),
    ("Upper Volga", 57.0, 35.5, 8000, True),
    ("Urals", 58.0, 59.0, 15000, False),
    ("Central Siberia", 65.0, 95.0, 50000, False),
    ("Baikal region", 53.0, 105.0, 12000, False),
    ("Far East", 50.0, 135.0, 3000, False),
]


def emit(value):
    print(json.dumps(value, ensure_ascii=False), flush=True)


def dump(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")


@contextmanager
def use_pair(flow_dir, flow_acc):
    original = DEM_FILES[SOURCE]
    DEM_FILES[SOURCE] = {**original, "flow_dir": Path(flow_dir), "flow_acc": Path(flow_acc)}
    try:
        yield
    finally:
        DEM_FILES[SOURCE] = original


def calculate(point, include_analysis):
    spec = get_dem_config(SOURCE)
    with rasterio.Env(**RASTER_ENV), rasterio.open(spec["flow_dir"]) as direction, \
            rasterio.open(spec["flow_acc"]) as accumulation:
        projected = watershed.create_pour_point(point["lat"], point["lon"], direction.crs).geometry.iloc[0]
        row, col = watershed._snap_outlet(direction, accumulation, projected.x, projected.y)
        native_acc = accumulation.read(1, window=Window(col, row, 1, 1)).item()
        cell_area = abs(direction.transform.a * direction.transform.e)
    windows = []
    original_whitebox = watershed._run_whitebox_watershed

    def track_windows(executable, pointer_path, pour_point_path, output_path, encoding):
        windows.append(int(pointer_path.stem.rsplit("_", 1)[1]))
        return original_whitebox(executable, pointer_path, pour_point_path, output_path, encoding)

    started = time.perf_counter()
    with patch.object(watershed, "_run_whitebox_watershed", track_windows):
        basin = watershed.delineate_watershed(point["lat"], point["lon"], SOURCE)
    elapsed = time.perf_counter() - started
    geometry = basin.geometry.iloc[0]
    area = float(geometry.area)
    encoded = geometry.wkb
    result = {
        **point, "snapped_row": row, "snapped_col": col,
        "native_accumulation": native_acc, "area_m2": area,
        "cell_count": round(area / cell_area), "crs": str(basin.crs),
        "geometry_wkb_hex": encoded.hex(), "geometry_sha256": hashlib.sha256(encoded).hexdigest(),
        "delineation_seconds": elapsed, "window_sizes": windows,
    }
    if include_analysis:
        started = time.perf_counter()
        result["analysis"] = run_analysis(basin)
        result["analysis_seconds"] = time.perf_counter() - started
    return result


def candidate_points(lat, lon, target):
    """Find moderate outlets in a 256 square neighborhood, without full reads."""
    spec = get_dem_config(SOURCE)
    with rasterio.Env(**RASTER_ENV), rasterio.open(spec["flow_dir"]) as direction, \
            rasterio.open(spec["flow_acc"]) as accumulation:
        xs, ys = transform("EPSG:4326", direction.crs, [lon], [lat])
        row, col = direction.index(xs[0], ys[0])
        window = watershed._centered_window(row, col, 256, direction.height, direction.width)
        values = accumulation.read(1, window=window, masked=True)
        pointers = direction.read(1, window=window, masked=True)
        raw = np.asarray(values.data)
        valid = ~np.ma.getmaskarray(values) & ~np.ma.getmaskarray(pointers) & np.isfinite(raw)
        valid &= (raw >= 50) & (raw <= 100000)
        # Candidates must already be local maxima inside the one-cell snapping
        # circle. Avoid accidentally selecting a much larger neighboring river.
        for dr, dc in [(-1, 0), (1, 0), (0, -1), (0, 1)]:
            valid &= raw >= np.roll(raw, (dr, dc), axis=(0, 1))
        valid[[0, -1], :] = False
        valid[:, [0, -1]] = False
        scores = np.where(valid, np.abs(np.log(np.maximum(raw, 1) / target)), np.inf)
        order = np.argsort(scores, axis=None)
        selected = []
        for index in order:
            rr, cc = np.unravel_index(index, raw.shape)
            if not np.isfinite(scores[rr, cc]):
                break
            if any(abs(rr - prev_r) + abs(cc - prev_c) < 12 for prev_r, prev_c in selected):
                continue
            selected.append((rr, cc))
            x, y = direction.xy(int(window.row_off) + rr, int(window.col_off) + cc)
            lons, lats = transform(direction.crs, "EPSG:4326", [x], [y])
            yield {"lat": lats[0], "lon": lons[0]}
            if len(selected) >= 5:
                break


def baseline(args):
    if args.output.exists():
        raise FileExistsError(f"Preserve the existing baseline: {args.output}")
    spec = get_dem_config(SOURCE)
    report = {
        "created_utc": datetime.now(timezone.utc).isoformat(), "source": SOURCE,
        "original_pair": {key: str(spec[key]) for key in ("flow_dir", "flow_acc")},
        "points": [], "failed_candidates": [],
        "timing_note": "Baseline is one real calculation per point; OS cache is not cleared.",
    }
    for name, lat, lon, target, analysis in REGIONS:
        candidates = [{"lat": lat, "lon": lon}] if name == "Moscow" else []
        candidates.extend(candidate_points(lat, lon, target))
        for candidate in candidates:
            point = {"name": name, **candidate, "analysis_requested": analysis}
            emit({"phase": "baseline_start", **point})
            try:
                result = calculate(point, analysis)
            except Exception as exc:
                failure = {**point, "error": str(exc)}
                report["failed_candidates"].append(failure)
                emit({"phase": "candidate_failed", **failure})
                continue
            report["points"].append(result)
            dump(args.output, report)
            emit({"phase": "baseline_complete", **{key: value for key, value in result.items()
                  if key not in ("geometry_wkb_hex", "analysis")}})
            break
        else:
            raise RuntimeError(f"Could not find a successful catchment near {name}")
    emit({"phase": "complete", "output": str(args.output), "points": len(report["points"])})


def compare_result(expected, actual):
    failures = []
    for key in ("snapped_row", "snapped_col", "native_accumulation", "cell_count"):
        if actual[key] != expected[key]:
            failures.append(key)
    if CRS.from_user_input(actual["crs"]) != CRS.from_user_input(expected["crs"]):
        failures.append("crs")
    if not math.isclose(actual["area_m2"], expected["area_m2"], rel_tol=1e-12, abs_tol=1e-6):
        failures.append("area_m2")
    if not wkb.loads(bytes.fromhex(actual["geometry_wkb_hex"])).equals(
            wkb.loads(bytes.fromhex(expected["geometry_wkb_hex"]))):
        failures.append("logical_geometry")
    if "analysis" in expected and actual.get("analysis") != expected["analysis"]:
        failures.append("analysis")
    return failures


def compare(args):
    if args.output.resolve() == args.baseline.resolve():
        raise ValueError("The comparison output must not overwrite its original baseline.")
    reference = json.loads(args.baseline.read_text(encoding="utf-8"))
    variants = [("original", reference["original_pair"]["flow_dir"], reference["original_pair"]["flow_acc"])]
    variants.extend(tuple(value) for value in args.variant)
    if len({variant[0] for variant in variants}) != len(variants):
        raise ValueError("Each variant must have a unique name other than 'original'.")
    for name, direction, accumulation in variants:
        with use_pair(direction, accumulation):
            get_dem_config(SOURCE)
    report = {
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "baseline": str(args.baseline.resolve()), "repeats": args.repeats,
        "timing_note": "Real delineation with repeated rotating variant order. OS caches are not flushed; includes Whitebox and polygonization. Analysis checked once per variant where baseline includes it.",
        "variants": [{"name": name, "flow_dir": direction, "flow_acc": accumulation}
                     for name, direction, accumulation in variants],
        "runs": [], "all_equal": True,
    }
    for repeat in range(args.repeats):
        for point_index, expected in enumerate(reference["points"]):
            offset = (repeat + point_index) % len(variants)
            order = variants[offset:] + variants[:offset]
            for name, direction, accumulation in order:
                point = {key: expected[key] for key in ("name", "lat", "lon", "analysis_requested")}
                with use_pair(direction, accumulation):
                    actual = calculate(point, "analysis" in expected and repeat == 0)
                comparable = expected if repeat == 0 else {key: value for key, value in expected.items() if key != "analysis"}
                failures = compare_result(comparable, actual)
                compact = {key: value for key, value in actual.items() if key not in ("geometry_wkb_hex", "analysis")}
                compact.update(variant=name, repeat=repeat + 1, equal=not failures,
                               differing_fields=failures,
                               wkb_identical=expected["geometry_sha256"] == actual["geometry_sha256"])
                report["runs"].append(compact)
                report["all_equal"] &= not failures
                dump(args.output, report)
                emit({"phase": "comparison", **compact})
    report["timing_summary"] = {
        name: {
            "runs": len(durations), "median_seconds": statistics.median(durations),
            "total_seconds": sum(durations),
            "per_point_median_seconds": {
                point["name"]: statistics.median(run["delineation_seconds"] for run in report["runs"]
                    if run["variant"] == name and run["name"] == point["name"])
                for point in reference["points"]
            },
        }
        for name, _, _ in variants
        for durations in [[run["delineation_seconds"] for run in report["runs"] if run["variant"] == name]]
    }
    dump(args.output, report)
    emit({"phase": "complete", "all_equal": report["all_equal"], "output": str(args.output),
          "timing_summary": report["timing_summary"]})
    if not report["all_equal"]:
        raise SystemExit("Watershed regression detected.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    baseline_command = commands.add_parser("baseline")
    baseline_command.add_argument("--output", type=Path, default=DEFAULT_BASELINE)
    baseline_command.set_defaults(func=baseline)
    compare_command = commands.add_parser("compare")
    compare_command.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    compare_command.add_argument("--variant", nargs=3, action="append", required=True,
                                 metavar=("NAME", "FLOW_DIR", "FLOW_ACC"))
    compare_command.add_argument("--repeats", type=int, default=3)
    compare_command.add_argument("--output", type=Path,
                                 default=DEFAULT_BASELINE.with_name("watershed_comparison.json"))
    compare_command.set_defaults(func=compare)
    args = parser.parse_args()
    if getattr(args, "repeats", 1) < 1:
        parser.error("--repeats must be positive")
    os.chdir(ROOT)
    args.func(args)


if __name__ == "__main__":
    main()
