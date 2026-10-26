"""Record immutable original basins, then verify a ZSTD COG catalog against them.

No source raster or configuration file is changed. Baselines use bounded native
windows. Comparison requires each converter manifest to attest full native pixel
and mask equality before any COG watershed calculation is attempted.
"""

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import sys

os.environ.pop("PROJ_LIB", None)
os.environ.pop("PROJ_DATA", None)
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import rasterio
from rasterio.crs import CRS

import benchmark_cog_watersheds as regression
from config import DEM_FILES
from services.dem_catalog import get_dem_config, RASTER_ENV


DEFAULT_ROOT = ROOT / "artifacts" / "cog-catalog"
SOURCE_IDS = [name for name in DEM_FILES if name != "GMTED2010_7_5sec"]


def emit(value):
    print(json.dumps(value, ensure_ascii=False), flush=True)


def save_new(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        json.dump(data, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


@contextmanager
def selected_source(source_id, pair):
    previous_source = regression.SOURCE
    previous_spec = DEM_FILES[source_id]
    regression.SOURCE = source_id
    DEM_FILES[source_id] = {**previous_spec, **{key: Path(pair[key]) for key in ("flow_dir", "flow_acc")}}
    try:
        yield
    finally:
        DEM_FILES[source_id] = previous_spec
        regression.SOURCE = previous_source


def file_signature(path):
    stat = Path(path).stat()
    return {"size_bytes": stat.st_size, "mtime_ns": stat.st_mtime_ns}


def raster_record(path):
    path = Path(path).resolve()
    with rasterio.Env(**RASTER_ENV), rasterio.open(path) as src:
        nodata = src.nodata
        if nodata is not None and not math.isfinite(nodata):
            nodata = str(nodata)
        return {"path": str(path), **file_signature(path),
                "shape": list(src.shape), "count": src.count, "dtype": src.dtypes[0],
                "nodata": nodata, "crs": str(src.crs), "transform": list(src.transform),
                "source_files": {str(Path(item).resolve()): file_signature(item)
                                 for item in src.files if Path(item).is_file()}}


def check_original_unchanged(record):
    if file_signature(record["path"]) != {key: record[key] for key in ("size_bytes", "mtime_ns")}:
        raise ValueError(f'Original raster changed since baseline: {record["path"]}')
    for path, expected in record.get("source_files", {}).items():
        if file_signature(path) != expected:
            raise ValueError(f"Original raster sidecar changed since baseline: {path}")


def source_snapshot():
    sources = {}
    for source_id in SOURCE_IDS:
        spec = get_dem_config(source_id)
        pair = {key: str(spec[key].resolve()) for key in ("flow_dir", "flow_acc")}
        if any("cog" in Path(path).parts for path in pair.values()):
            raise ValueError(f"Capture original paths before enabling COG: {source_id}")
        sources[source_id] = {
            "source_id": source_id, "original_pair": pair,
            "rasters": {key: raster_record(path) for key, path in pair.items()},
        }
    return {"created_utc": datetime.now(timezone.utc).isoformat(), "sources": sources}


def point_groups(source_id):
    if source_id in ("MERIT_3sec", "ALOS_local"):
        first = ((64.99996917616875, 94.99983861836263) if source_id == "MERIT_3sec"
                 else (65.00028940874923, 95.00039922212396))
        return [
            ("Siberia small", [first, (65.0, 95.0)], 300, True),
            ("Siberia medium", [(64.0, 98.0), (63.0, 98.0), (65.0, 95.0)], 5000, False),
            ("Siberia larger", [(60.0, 102.0), (62.0, 96.0), (65.0, 95.0)], 30000, False),
        ]
    return [
        ("Moscow small", [(55.74658358585305, 37.65629073793753), (56.0, 38.0)], 200, True),
        ("Upper Volga medium", [(57.0, 35.5), (56.0, 38.0)], 3000, False),
        ("Urals larger", [(58.0, 59.0), (57.0, 54.0), (56.0, 50.0)], 25000, False),
    ]


def baseline(args):
    completed_path = args.output_root / "baseline.json"
    if completed_path.exists():
        raise FileExistsError(f"Preserve the completed baseline: {completed_path}")
    snapshot_path = args.output_root / "original_sources.json"
    if snapshot_path.exists():
        snapshot = read_json(snapshot_path)
    else:
        snapshot = source_snapshot()
        save_new(snapshot_path, snapshot)
    result = {"created_utc": datetime.now(timezone.utc).isoformat(), "sources": {}}
    for source_id, source in snapshot["sources"].items():
        for record in source["rasters"].values():
            check_original_unchanged(record)
        source_path = args.output_root / f"{source_id}.baseline.json"
        if source_path.exists():
            previous = read_json(source_path)
            if previous["original_pair"] != source["original_pair"] or len(previous["points"]) != 3:
                raise ValueError(f"Incomplete or mismatched saved source baseline: {source_id}")
            result["sources"][source_id] = previous
            continue
        report = {**source, "points": [], "failed_candidates": []}
        with selected_source(source_id, source["original_pair"]):
            for name, centers, target, direct_first in point_groups(source_id):
                found = False
                for center_index, (lat, lon) in enumerate(centers):
                    candidates = [{"lat": lat, "lon": lon}] if direct_first and center_index == 0 else []
                    candidates.extend(regression.candidate_points(lat, lon, target))
                    for candidate in candidates:
                        point = {"name": name, **candidate, "analysis_requested": True}
                        emit({"phase": "baseline_start", "source": source_id, **point})
                        try:
                            actual = regression.calculate(point, True)
                            if any((previous["snapped_row"], previous["snapped_col"]) ==
                                   (actual["snapped_row"], actual["snapped_col"]) for previous in report["points"]):
                                raise ValueError("Duplicate outlet already represented in this source baseline")
                        except Exception as exc:
                            report["failed_candidates"].append({**point, "error": str(exc)})
                            emit({"phase": "candidate_failed", "source": source_id, "point": name,
                                  "error": str(exc)})
                            continue
                        report["points"].append(actual)
                        emit({"phase": "baseline_point", "source": source_id, "point": name,
                              "cells": actual["cell_count"], "windows": actual["window_sizes"],
                              "analysis_verified": True})
                        found = True
                        break
                    if found:
                        break
                if not found:
                    raise ValueError(f"No complete watershed found for {source_id}: {name}")
        for record in source["rasters"].values():
            check_original_unchanged(record)
        save_new(source_path, report)
        result["sources"][source_id] = report
        emit({"phase": "baseline_source", "source": source_id, "points": 3,
              "expanded_windows": sum(len(point["window_sizes"]) > 1 for point in report["points"]),
              "completed_sources": len(result["sources"]), "total_sources": len(snapshot["sources"])})
    save_new(completed_path, result)
    emit({"phase": "baseline_complete", "path": str(completed_path),
          "sources": len(result["sources"]), "points": sum(len(s["points"]) for s in result["sources"].values())})


def verified_manifest(original, output):
    output = Path(output).resolve()
    check_original_unchanged(original)
    manifest_path = output.with_suffix(output.suffix + ".cog.json")
    report = read_json(manifest_path)
    if Path(report.get("source", "")).resolve() != Path(original["path"]).resolve():
        raise ValueError(f"COG manifest does not reference baseline original: {output}")
    if Path(report.get("output", "")).resolve() != output:
        raise ValueError(f"COG manifest output path mismatch: {output}")
    if report.get("source_size_bytes") != original["size_bytes"] or report.get("source_mtime_ns") != original["mtime_ns"]:
        raise ValueError(f"COG manifest source signature mismatch: {output}")
    if report.get("output_size_bytes") != output.stat().st_size:
        raise ValueError(f"COG output size changed after validation: {output}")
    for flag in ("all_native_pixels_validated", "all_native_masks_validated"):
        if report.get(flag) is not True:
            raise ValueError(f"COG lacks complete native validation ({flag}): {output}")
    for left, right in [("source_native_sha256", "output_native_sha256"),
                        ("source_mask_sha256", "output_mask_sha256")]:
        digest = report.get(left)
        if not isinstance(digest, str) or len(digest) != 64 or digest != report.get(right):
            raise ValueError(f"COG manifest has unequal or missing verification hashes: {output}")
        try:
            int(digest, 16)
        except ValueError as exc:
            raise ValueError(f"Invalid verification hash: {output}") from exc
    if report.get("creation_options", {}).get("COMPRESS") != "ZSTD":
        raise ValueError(f"Expected ZSTD COG: {output}")
    if report.get("image_structure", {}).get("LAYOUT") != "COG":
        raise ValueError(f"Manifest does not attest COG layout: {output}")
    for key in ("shape", "dtype", "nodata", "count", "transform"):
        if report.get(key) != original[key]:
            raise ValueError(f"Manifest native metadata mismatch at {key}: {output}")
    if CRS.from_user_input(report["crs"]) != CRS.from_user_input(original["crs"]):
        raise ValueError(f"Manifest CRS mismatch: {output}")
    with rasterio.Env(**RASTER_ENV), rasterio.open(output) as dataset:
        actual_nodata = dataset.nodata
        if actual_nodata is not None and not math.isfinite(actual_nodata):
            actual_nodata = str(actual_nodata)
        structure = dataset.tags(ns="IMAGE_STRUCTURE")
        if (list(dataset.shape) != original["shape"] or dataset.dtypes[0] != original["dtype"]
                or dataset.count != original["count"] or actual_nodata != original["nodata"]
                or list(dataset.transform) != original["transform"]
                or dataset.crs != CRS.from_user_input(original["crs"])
                or structure.get("LAYOUT") != "COG" or structure.get("COMPRESSION") != "ZSTD"):
            raise ValueError(f"Actual COG metadata no longer matches validation: {output}")
    return {"path": str(manifest_path), "source_native_sha256": report["source_native_sha256"],
            "source_mask_sha256": report["source_mask_sha256"],
            "output_signature": file_signature(output)}


def compare(args):
    baseline_path = args.output_root / "baseline.json"
    result_path = args.output_root / "comparison.json"
    if result_path.exists():
        raise FileExistsError(f"Preserve existing comparison: {result_path}")
    reference = read_json(baseline_path)
    report = {"created_utc": datetime.now(timezone.utc).isoformat(), "baseline": str(baseline_path),
              "sources": {}, "all_equal": True}
    for source_id, source in reference["sources"].items():
        pair = {role: args.cog_root / source_id.lower() / "zstd" / Path(path).name
                for role, path in source["original_pair"].items()}
        manifests = {role: verified_manifest(source["rasters"][role], path) for role, path in pair.items()}
        source_result = {"original_pair": source["original_pair"],
                         "cog_pair": {role: str(path) for role, path in pair.items()},
                         "manifests": manifests, "points": [], "all_equal": True}
        with selected_source(source_id, pair):
            for expected in source["points"]:
                point = {key: expected[key] for key in ("name", "lat", "lon", "analysis_requested")}
                actual = regression.calculate(point, True)
                failures = regression.compare_result(expected, actual)
                compact = {key: value for key, value in actual.items() if key != "geometry_wkb_hex"}
                compact.update(equal=not failures, differing_fields=failures,
                               wkb_identical=actual["geometry_sha256"] == expected["geometry_sha256"])
                source_result["points"].append(compact)
                source_result["all_equal"] &= not failures
                emit({"phase": "comparison_point", "source": source_id, "point": point["name"],
                      "equal": not failures, "cells": actual["cell_count"],
                      "wkb_identical": compact["wkb_identical"], "differing_fields": failures})
        for role, path in pair.items():
            check_original_unchanged(source["rasters"][role])
            if file_signature(path) != manifests[role]["output_signature"]:
                raise ValueError(f"COG changed while comparing watersheds: {path}")
        report["sources"][source_id] = source_result
        report["all_equal"] &= source_result["all_equal"]
    save_new(result_path, report)
    emit({"phase": "comparison_complete", "all_equal": report["all_equal"], "path": str(result_path)})
    if not report["all_equal"]:
        raise SystemExit("Catalog watershed regression detected")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["baseline", "compare"])
    parser.add_argument("--output-root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--cog-root", type=Path, default=ROOT / "data" / "dem" / "cog")
    args = parser.parse_args()
    os.chdir(ROOT)
    args.func = baseline if args.command == "baseline" else compare
    args.func(args)


if __name__ == "__main__":
    main()
