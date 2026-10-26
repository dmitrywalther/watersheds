"""Delineate complete D8 catchments using bounded, expanding raster windows."""

import math
import os
from pathlib import Path
import subprocess
import tempfile

import geopandas as gpd
import numpy as np
import rasterio
from rasterio.features import shapes
from rasterio.mask import mask
from rasterio.windows import Window
from shapely.geometry import Point, shape
from shapely.ops import unary_union
import whitebox

from services.dem_catalog import RASTER_ENV, get_dem_config


INITIAL_WINDOW_CELLS = 256
MAX_WINDOW_CELLS = 16_000_000
WHITEBOX_TIMEOUT_SECONDS = 180


def create_pour_point(lat: float, lon: float, target_crs):
    return gpd.GeoDataFrame(
        geometry=[Point(lon, lat)], crs="EPSG:4326"
    ).to_crs(target_crs)


def find_containing_basin(point_gdf, basins_path):
    """Legacy helper; delineation no longer clips by unrelated basin polygons."""
    basins = gpd.read_file(basins_path)
    point = point_gdf.to_crs(basins.crs).geometry.iloc[0]
    selected = basins.covers(point)
    if not selected.any():
        raise ValueError("Точка находится за пределами слоя бассейнов.")
    return basins.loc[selected].geometry.values


def clip_raster(src_path, geometry, dst_path):
    """Clip a raster by geometries already expressed in the raster's CRS."""
    with rasterio.Env(**RASTER_ENV), rasterio.open(src_path) as src:
        out_img, out_transform = mask(src, geometry, crop=True)
        meta = src.meta.copy()
        meta.update(height=out_img.shape[1], width=out_img.shape[2], transform=out_transform)
    with rasterio.Env(**RASTER_ENV), rasterio.open(dst_path, "w", **meta) as dst:
        dst.write(out_img)


def _read_window(dataset, window):
    data = dataset.read(1, window=window, masked=True)
    values = np.asarray(data.data)
    valid = ~np.ma.getmaskarray(data) & np.isfinite(values)
    return values, valid


def _centered_window(row, col, size, height, width):
    win_height, win_width = min(size, height), min(size, width)
    top = max(0, min(row - win_height // 2, height - win_height))
    left = max(0, min(col - win_width // 2, width - win_width))
    return Window(left, top, win_width, win_height)


def _snap_outlet(direction, accumulation, x, y):
    row, col = direction.index(x, y)
    if not (0 <= row < direction.height and 0 <= col < direction.width):
        raise ValueError("Выбранная точка находится вне покрытия этой ЦМР.")

    neighborhood = Window(
        max(col - 1, 0), max(row - 1, 0),
        min(col + 2, direction.width) - max(col - 1, 0),
        min(row + 2, direction.height) - max(row - 1, 0),
    )
    _, valid_dir = _read_window(direction, neighborhood)
    values, valid_acc = _read_window(accumulation, neighborhood)
    local_row, local_col = row - int(neighborhood.row_off), col - int(neighborhood.col_off)
    if not (valid_dir[local_row, local_col] and valid_acc[local_row, local_col]):
        raise ValueError("В выбранной точке нет данных ЦМР (NoData). Выберите другую точку.")

    rows, cols = np.indices(values.shape)
    xs, ys = rasterio.transform.xy(
        direction.transform, rows + neighborhood.row_off, cols + neighborhood.col_off
    )
    distances = (np.asarray(xs).reshape(values.shape) - x) ** 2
    distances += (np.asarray(ys).reshape(values.shape) - y) ** 2
    radius = max(direction.res)
    candidates = valid_dir & valid_acc & (distances <= radius ** 2 * (1 + 1e-9))
    if not candidates.any():
        raise ValueError("Рядом с выбранной точкой нет подходящих ячеек стока.")
    best = np.max(values[candidates])
    distances = np.where(candidates & (values == best), distances, np.inf)
    snapped_row, snapped_col = np.unravel_index(np.argmin(distances), values.shape)
    return int(neighborhood.row_off + snapped_row), int(neighborhood.col_off + snapped_col)


def _run_whitebox_watershed(executable, pointer_path, pour_point_path, output_path, encoding):
    # The Python wrapper changes process-wide cwd and ignores the binary exit
    # status. A direct subprocess keeps simultaneous requests independent.
    command = [
        str(executable), "--run=Watershed", f"--d8_pntr={pointer_path}",
        f"--pour_pts={pour_point_path}", f"--output={output_path}", "-v=false",
    ]
    if encoding == "esri":
        command.append("--esri_pntr")
    try:
        result = subprocess.run(
            command, cwd=output_path.parent, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=WHITEBOX_TIMEOUT_SECONDS,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ValueError("Не удалось выполнить расчёт водосбора WhiteboxTools.") from exc
    if result.returncode != 0 or not output_path.is_file():
        raise ValueError("WhiteboxTools не смог построить водосбор по выбранной ЦМР.")


def _touches_nodata(catchment, valid):
    # Eight-neighbour adjacency is conservative: an unknown neighbouring cell
    # might drain into the catchment. Sea/coast NoData can also trigger refusal.
    height, width = catchment.shape
    for dr in (-1, 0, 1):
        for dc in (-1, 0, 1):
            if dr == dc == 0:
                continue
            top, bottom = max(0, -dr), min(height, height - dr)
            left, right = max(0, -dc), min(width, width - dc)
            if np.any(catchment[top:bottom, left:right] &
                      ~valid[top + dr:bottom + dr, left + dc:right + dc]):
                return True
    return False


def delineate_watershed(lat, lon, dem_name):
    try:
        lat, lon = float(lat), float(lon)
    except (TypeError, ValueError) as exc:
        raise ValueError("Некорректные координаты выбранной точки.") from exc
    if not (math.isfinite(lat) and math.isfinite(lon) and -90 <= lat <= 90 and -180 <= lon <= 180):
        raise ValueError("Некорректные координаты: проверьте широту и долготу.")

    dem = get_dem_config(dem_name)
    with rasterio.Env(**RASTER_ENV), rasterio.open(dem["flow_dir"]) as direction, \
            rasterio.open(dem["flow_acc"]) as accumulation:
        point = create_pour_point(lat, lon, direction.crs).geometry.iloc[0]
        if not (math.isfinite(point.x) and math.isfinite(point.y)):
            raise ValueError("Не удалось преобразовать координаты точки в проекцию ЦМР.")
        row, col = _snap_outlet(direction, accumulation, point.x, point.y)
        wbt = whitebox.WhiteboxTools()
        executable = Path(wbt.exe_path) / wbt.exe_name

        with tempfile.TemporaryDirectory() as directory:
            temporary = Path(directory)
            snapped_x, snapped_y = direction.xy(row, col)
            pour_point_path = temporary / "outlet.shp"
            gpd.GeoDataFrame(
                {"FID": [1]}, geometry=[Point(snapped_x, snapped_y)], crs=direction.crs
            ).to_file(pour_point_path)
            size = INITIAL_WINDOW_CELLS
            while True:
                window = _centered_window(row, col, size, direction.height, direction.width)
                if window.width * window.height > MAX_WINDOW_CELLS:
                    raise ValueError("Водосбор слишком большой: превышен лимит ячеек расчёта.")
                pointers, valid_dir = _read_window(direction, window)
                _, valid_acc = _read_window(accumulation, window)
                valid = valid_dir & valid_acc
                if not np.all(np.isin(pointers[valid], [0, 1, 2, 4, 8, 16, 32, 64, 128])):
                    raise ValueError("В ЦМР обнаружены неподдерживаемые коды направления стока D8.")

                pointer_path = temporary / f"flow_dir_{size}.tif"
                output_path = temporary / f"watershed_{size}.tif"
                transform = direction.window_transform(window)
                with rasterio.open(
                    pointer_path, "w", driver="GTiff", count=1,
                    height=int(window.height), width=int(window.width),
                    dtype="int16", crs=direction.crs, transform=transform, nodata=-32768,
                ) as dst:
                    dst.write(np.where(valid, pointers.astype("int16"), -32768), 1)
                _run_whitebox_watershed(
                    executable, pointer_path, pour_point_path, output_path,
                    dem["flow_dir_encoding"],
                )
                with rasterio.open(output_path) as output:
                    if output.shape != pointers.shape or output.transform != transform:
                        raise ValueError("WhiteboxTools вернул несовместимую сетку водосбора.")
                    basin, valid_output = _read_window(output, Window(0, 0, output.width, output.height))
                catchment = valid & valid_output & (basin > 0)
                if not catchment[row - int(window.row_off), col - int(window.col_off)]:
                    raise ValueError("WhiteboxTools вернул пустой водосбор для выбранной точки.")

                edges = (catchment[0, :].any(), catchment[-1, :].any(),
                         catchment[:, 0].any(), catchment[:, -1].any())
                source_edges = (window.row_off == 0,
                                window.row_off + window.height == direction.height,
                                window.col_off == 0,
                                window.col_off + window.width == direction.width)
                if any(touches and at_source for touches, at_source in zip(edges, source_edges)):
                    raise ValueError(
                        "Водосбор достигает границы покрытия ЦМР. Полный бассейн определить "
                        "нельзя; выберите источник с более широким покрытием."
                    )
                if _touches_nodata(catchment, valid):
                    raise ValueError(
                        "Водосбор примыкает к области NoData. Полнота покрытия ЦМР не "
                        "подтверждена; выберите источник с более широким покрытием."
                    )
                if not any(edges):
                    geometry = unary_union([
                        shape(geometry) for geometry, value in shapes(
                            catchment.astype("uint8"), mask=catchment, transform=transform
                        ) if value == 1
                    ])
                    return gpd.GeoDataFrame(geometry=[geometry], crs=direction.crs)
                next_size = min(size * 2, math.isqrt(MAX_WINDOW_CELLS))
                if next_size <= size:
                    raise ValueError("Водосбор слишком большой: превышен лимит ячеек расчёта.")
                size = next_size
