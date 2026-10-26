"""Validate and time native-resolution reads of original and COG raster pairs.

Fresh GDAL handles are opened for every trial. This is deliberately NOT a
cold-disk benchmark: OS caches are not flushed. Conversion and validation can
warm them. Only reads of both masked bands are included in primary read times;
opening the pair is measured separately, and hashing is outside timed regions.
"""

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import random
import statistics
import sys
import time

os.environ.pop("PROJ_LIB", None)
os.environ.pop("PROJ_DATA", None)
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import numpy as np
import rasterio
from rasterio.windows import Window

from services.dem_catalog import RASTER_ENV
from services.watershed import _centered_window


DEFAULT_BASELINE = ROOT / "artifacts" / "cog-benchmark" / "original_watersheds.json"
WINDOW_SIZES = (256, 1024, 2048)
GDAL_CACHE_BYTES = 64 * 1024 * 1024


def emit(value):
    print(json.dumps(value, ensure_ascii=False), flush=True)


def dump(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")


def array_fingerprint(values):
    """Compare native bytes, not rounded numbers, including the NoData mask."""
    raw = np.ascontiguousarray(np.ma.getdata(values))
    valid_mask = np.ascontiguousarray(np.ma.getmaskarray(values), dtype=np.uint8)
    return {"shape": list(raw.shape), "dtype": raw.dtype.str,
            "raw_sha256": hashlib.sha256(memoryview(raw)).hexdigest(),
            "mask_sha256": hashlib.sha256(memoryview(valid_mask)).hexdigest(),
            "masked_pixels": int(np.count_nonzero(valid_mask))}


def make_cases(points, height, width):
    cases = []
    for point in points:
        for size in WINDOW_SIZES:
            window = _centered_window(point["snapped_row"], point["snapped_col"], size, height, width)
            cases.append({"id": f'{point["name"]}:{size}', "point": point["name"],
                          "window_size": size,
                          "window": [int(window.col_off), int(window.row_off),
                                     int(window.width), int(window.height)]})
    return cases


def trial_schedule(cases, variant_names, repeats, seed):
    """Deterministic shuffled cases, with a balanced rotating variant order."""
    rng = random.Random(seed)
    for repeat in range(repeats):
        shuffled = list(cases)
        rng.shuffle(shuffled)
        for case_index, case in enumerate(shuffled):
            offset = (repeat + case_index) % len(variant_names)
            names = variant_names[offset:] + variant_names[:offset]
            for order, name in enumerate(names):
                yield {"repeat": repeat + 1, "variant_order": order + 1,
                       "variant": name, "case": case}


def metadata(path):
    with rasterio.open(path) as dataset:
        return {"crs": dataset.crs, "transform": dataset.transform,
                "height": dataset.height, "width": dataset.width, "count": dataset.count,
                "dtype": dataset.dtypes[0], "nodata": dataset.nodata}


def check_pairs(variants):
    reference = {key: metadata(variants[0][key]) for key in ("flow_dir", "flow_acc")}
    for key in ("crs", "transform", "height", "width", "count"):
        if reference["flow_dir"][key] != reference["flow_acc"][key]:
            raise ValueError(f"Original direction/accumulation grid mismatch: {key}")
    if reference["flow_dir"]["count"] != 1:
        raise ValueError("Expected a single band in each raster.")
    for variant in variants[1:]:
        for role in ("flow_dir", "flow_acc"):
            candidate = metadata(variant[role])
            for key, expected in reference[role].items():
                actual = candidate[key]
                both_nan = key == "nodata" and isinstance(expected, float) and isinstance(actual, float) \
                    and np.isnan(expected) and np.isnan(actual)
                if not both_nan and actual != expected:
                    raise ValueError(f'{variant["name"]} {role}: metadata differs at {key}')
    return reference["flow_dir"]


def read_pair(variant, window):
    with rasterio.open(variant["flow_dir"]) as direction, rasterio.open(variant["flow_acc"]) as accumulation:
        return (direction.read(1, window=window, masked=True),
                accumulation.read(1, window=window, masked=True))


def validate_windows(cases, variants):
    result = []
    for case in cases:
        expected = None
        for variant in variants:
            values = read_pair(variant, Window(*case["window"]))
            fingerprints = [array_fingerprint(value) for value in values]
            del values
            if expected is None:
                expected = fingerprints
            elif fingerprints != expected:
                raise ValueError(f'Native pixels or masks differ: {variant["name"]}, {case["id"]}')
            result.append({"case": case["id"], "variant": variant["name"],
                           "equal": True, "flow_dir": fingerprints[0], "flow_acc": fingerprints[1]})
        emit({"phase": "validation", "case": case["id"], "variants_equal": len(variants)})
    return result


def timed_read(variant, window, warm_repeats):
    started = time.perf_counter()
    with rasterio.open(variant["flow_dir"]) as direction, rasterio.open(variant["flow_acc"]) as accumulation:
        opened = time.perf_counter()
        pointers = direction.read(1, window=window, masked=True)
        counts = accumulation.read(1, window=window, masked=True)
        finished = time.perf_counter()
        # Release primary results before any optional immediate rereads.
        del pointers, counts
        warm = []
        for _ in range(warm_repeats):
            warm_started = time.perf_counter()
            pointers = direction.read(1, window=window, masked=True)
            counts = accumulation.read(1, window=window, masked=True)
            warm.append(time.perf_counter() - warm_started)
            del pointers, counts
    return {"open_seconds": opened - started, "read_seconds": finished - opened,
            "open_plus_read_seconds": finished - started,
            "same_handle_reread_seconds": warm}


def distribution(values):
    return {"n": len(values), "median_seconds": statistics.median(values),
            "p90_seconds": float(np.percentile(values, 90))}


def summarize(runs, variants, repeats):
    result = {}
    for variant in variants:
        name = variant["name"]
        own = [run for run in runs if run["variant"] == name]
        per_repeat = [{"repeat": repeat,
                       "read_seconds": sum(run["read_seconds"] for run in own if run["repeat"] == repeat),
                       "open_seconds": sum(run["open_seconds"] for run in own if run["repeat"] == repeat),
                       "open_plus_read_seconds": sum(run["open_plus_read_seconds"] for run in own
                                                      if run["repeat"] == repeat)}
                      for repeat in range(1, repeats + 1)]
        result[name] = {
            "by_window_size": {
                str(size): {
                    metric: distribution([run[metric] for run in own if run["window_size"] == size])
                    for metric in ("read_seconds", "open_seconds", "open_plus_read_seconds")
                } for size in sorted({run["window_size"] for run in own})
            },
            "whole_workload_per_repeat": per_repeat,
            "whole_workload": {
                metric: distribution([repeat[metric] for repeat in per_repeat])
                for metric in ("read_seconds", "open_seconds", "open_plus_read_seconds")
            },
            "pooled_individual_read": distribution([run["read_seconds"] for run in own]),
        }
        warm = [duration for run in own for duration in run["same_handle_reread_seconds"]]
        if warm:
            result[name]["secondary_same_handle_rereads"] = distribution(warm)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    parser.add_argument("--variant", nargs=3, action="append", required=True,
                        metavar=("NAME", "FLOW_DIR", "FLOW_ACC"))
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--seed", type=int, default=20261024)
    parser.add_argument("--same-handle-rereads", type=int, default=0,
                        help="Optional secondary warm rereads, excluded from primary statistics.")
    parser.add_argument("--output", type=Path, default=DEFAULT_BASELINE.with_name("io_comparison.json"))
    args = parser.parse_args()
    if args.repeats < 1 or args.same_handle_rereads < 0:
        parser.error("--repeats must be positive; --same-handle-rereads must be nonnegative")
    if args.output.resolve() == args.baseline.resolve():
        parser.error("The comparison output must not overwrite the baseline")
    reference = json.loads(args.baseline.read_text(encoding="utf-8"))
    variants = [{"name": "ORIGINAL", **reference["original_pair"]}]
    variants.extend({"name": name, "flow_dir": direction, "flow_acc": accumulation}
                    for name, direction, accumulation in args.variant)
    if len({variant["name"].upper() for variant in variants}) != len(variants):
        parser.error("Variant names must be unique and cannot be ORIGINAL")
    variant_map = {variant["name"]: variant for variant in variants}
    report = {
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "method": "Fresh GDAL handles per trial, both bands read masked at native resolution. Separate opening and read timings. No cache flushing: NOT cold-disk timing. Validation and prior work can warm OS caches. Rotating variant order; deterministic shuffled cases. Whole-workload timing is the sum of all point/window trials within each repeat, then median across repeats.",
        "baseline": str(args.baseline.resolve()), "variants": variants,
        "gdal_cache_bytes": GDAL_CACHE_BYTES, "gdal_num_threads": 1,
        "os_cache_flushed": False, "seed": args.seed, "repeats": args.repeats,
        "same_handle_rereads": args.same_handle_rereads,
        "runs": [],
    }
    with rasterio.Env(**RASTER_ENV, GDAL_CACHEMAX=GDAL_CACHE_BYTES, GDAL_NUM_THREADS="1"):
        grid = check_pairs(variants)
        cases = make_cases(reference["points"], grid["height"], grid["width"])
        report["cases"] = cases
        report["validation"] = validate_windows(cases, variants)
        report["all_windows_equal"] = True
        dump(args.output, report)
        schedule = trial_schedule(cases, list(variant_map), args.repeats, args.seed)
        for index, trial in enumerate(schedule, start=1):
            case = trial["case"]
            timing = timed_read(variant_map[trial["variant"]], Window(*case["window"]),
                                args.same_handle_rereads)
            run = {key: value for key, value in trial.items() if key != "case"}
            run.update(case=case["id"], window_size=case["window_size"], **timing)
            report["runs"].append(run)
            if index % len(variants) == 0:
                emit({"phase": "timing", "completed_trials": index,
                      "total_trials": len(cases) * len(variants) * args.repeats,
                      "repeat": trial["repeat"], "case": case["id"]})
                dump(args.output, report)
    report["summary"] = summarize(report["runs"], variants, args.repeats)
    dump(args.output, report)
    emit({"phase": "complete", "output": str(args.output), "all_windows_equal": True,
          "whole_workload_read_median_seconds": {
              name: summary["whole_workload"]["read_seconds"]["median_seconds"]
              for name, summary in report["summary"].items()}})


if __name__ == "__main__":
    main()
