"""
Flood risk screening (HAND-модель).

ВАЖНО: это НЕ гидравлическая модель, а screening-level оценка на основе
HAND (Height Above Nearest Drainage) — стандартный в отрасли способ быстро
прикинуть потенциальные зоны подтопления по ЦМР без расчёта расхода воды,
уклона водной поверхности и т.п. Пользователь задаёт условный сценарный
подъём уровня воды над руслом (0.5 / 1 / 2 / 5 м), а не привязывается
к историческому/расчётному паводку заданной обеспеченности.

Ограничение разрешения (сознательно принято, см. диалог с пользователем):
единственный DEM с готовыми flow_dir/flow_acc растрами — ETOPO1_1min,
но его фактическая ячейка ~2996 м (~9 км²). На бассейне в несколько
сотен км² это всего по несколько десятков ячеек, поэтому построенная
зона затопления выглядит крупноблочной мозаикой, а не гладкой поймой.
Для маленьких бассейнов (единицы ячеек) результат может быть
статистически незначим. Это осознанно принятое ограничение текущих
данных, а не баг — интерфейс должен явно предупреждать пользователя
об этом (см. FLOOD_RESOLUTION_WARNING_KM2 ниже и фронтенд).
"""

from pathlib import Path
import tempfile

import numpy as np
import rasterio
from rasterio.mask import mask as rio_mask
from rasterio.warp import reproject, Resampling
from rasterio.features import shapes as rio_shapes
import geopandas as gpd
from shapely.geometry import shape as shapely_shape
from shapely.ops import unary_union
import whitebox
from whitebox.whitebox_tools import default_callback

from config import (
    DEM_FILES,
    ELEVATION_RASTER,
    FLOOD_STREAM_THRESHOLD_KM2,
    FLOOD_WATER_LEVELS_M,
)
from services.watershed import delineate_watershed, clip_raster

wbt = whitebox.WhiteboxTools()

# Бассейны меньше этого размера дают настолько мало ячеек flow_acc
# (на сетке ETOPO1_1min ~9 км²/ячейка), что HAND-анализ статистически
# не имеет смысла. Используется фронтендом/ответом API для предупреждения,
# сам анализ при этом не блокируется.
FLOOD_MIN_RELIABLE_AREA_KM2 = 200.0


class FloodAnalysisError(Exception):
    """Ошибка, которую стоит показать пользователю как есть."""


def _reproject_to_grid(src_path, dst_transform, dst_width, dst_height, dst_crs, resampling):
    """Ресемплирует растр на чужую сетку (используется для elevation -> flow_acc grid)."""
    with rasterio.open(src_path) as src:
        dst_array = np.full((dst_height, dst_width), src.nodata if src.nodata is not None else np.nan,
                             dtype=np.float32)
        reproject(
            source=rasterio.band(src, 1),
            destination=dst_array,
            src_transform=src.transform,
            src_crs=src.crs,
            dst_transform=dst_transform,
            dst_crs=dst_crs,
            dst_nodata=src.nodata,
            resampling=resampling,
        )
        return dst_array, src.nodata


def compute_flood_zone(lat, lon, dem_name, water_level_m):
    """
    Строит зону затопления по HAND-модели для точки (lat, lon).

    Returns
    -------
    dict:
        {
            "flood_gdf": GeoDataFrame (EPSG:4326) с (мульти)полигоном зоны затопления,
            "flood_gdf_projected": та же геометрия в исходной CRS растров (EPSG:3395) —
                нужна для zonal_stats по landcover в buildings.py,
            "watershed_gdf": GeoDataFrame (исходная CRS бассейна) — переиспользуется вызывающим кодом,
            "flood_area_km2": float,
            "watershed_area_km2": float,
            "water_level_m": float,
            "reliable": bool,  # False, если бассейн слишком мал для этого разрешения ЦМР
        }
    """
    if dem_name not in DEM_FILES:
        raise FloodAnalysisError(f"Unknown DEM: {dem_name}")

    dem = DEM_FILES[dem_name]
    if not Path(dem["flow_dir"]).exists() or not Path(dem["flow_acc"]).exists():
        raise FloodAnalysisError(
            f"Flood analysis is not available for DEM '{dem_name}': "
            "flow_dir/flow_acc rasters are missing on disk."
        )

    if water_level_m not in FLOOD_WATER_LEVELS_M:
        raise FloodAnalysisError(
            f"water_level_m must be one of {FLOOD_WATER_LEVELS_M}, got {water_level_m}"
        )

    watershed_gdf = delineate_watershed(lat, lon, dem_name)
    watershed_area_km2 = float(watershed_gdf.geometry.area.iloc[0] / 1e6)
    basin_geom = watershed_gdf.geometry.values

    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)

        # --- 1. Клип flow_acc в границах бассейна ---
        flow_acc_clip = tmp / "flow_acc.tif"
        clip_raster(dem["flow_acc"], basin_geom, flow_acc_clip)

        with rasterio.open(flow_acc_clip) as src:
            facc_meta = src.meta.copy()
            facc_transform = src.transform
            facc_width = src.width
            facc_height = src.height
            facc_crs = src.crs
            facc_nodata = src.nodata
            facc_array = src.read(1)
            px_w, px_h = src.res

        cell_area_km2 = (px_w * px_h) / 1e6
        threshold_cells = max(FLOOD_STREAM_THRESHOLD_KM2 / cell_area_km2, 1.0)

        # --- 2. Извлечение русел из flow_acc ---
        streams_path = tmp / "streams.tif"
        wbt.extract_streams(
            flow_acc_clip.as_posix(),
            streams_path.as_posix(),
            threshold_cells,
            zero_background=True,
            callback=default_callback,
        )
        if not streams_path.exists():
            raise FloodAnalysisError("ExtractStreams failed")

        # --- 3. Ресемплинг высот на сетку flow_acc (та же CRS, разное разрешение) ---
        elevation_resampled, elev_nodata = _reproject_to_grid(
            ELEVATION_RASTER, facc_transform, facc_width, facc_height, facc_crs,
            Resampling.bilinear,
        )
        elevation_path = tmp / "elevation_resampled.tif"
        elev_meta = facc_meta.copy()
        elev_meta.update(dtype="float32", nodata=elev_nodata if elev_nodata is not None else -9999.0)
        with rasterio.open(elevation_path, "w", **elev_meta) as dst:
            dst.write(elevation_resampled.astype("float32"), 1)

        # --- 4. HAND: превышение над ближайшим руслом ---
        hand_path = tmp / "hand.tif"
        wbt.elevation_above_stream(
            elevation_path.as_posix(),
            streams_path.as_posix(),
            hand_path.as_posix(),
            callback=default_callback,
        )
        if not hand_path.exists():
            raise FloodAnalysisError("ElevationAboveStream failed")

        with rasterio.open(hand_path) as src:
            hand_array = src.read(1)
            hand_nodata = src.nodata

        # --- 5. Маска затопления: HAND <= уровень воды, только внутри бассейна ---
        valid_basin = facc_array != facc_nodata if facc_nodata is not None else np.ones_like(facc_array, dtype=bool)
        valid_hand = np.isfinite(hand_array)
        if hand_nodata is not None:
            valid_hand &= hand_array != hand_nodata

        flood_mask = valid_basin & valid_hand & (hand_array <= water_level_m)

        if not flood_mask.any():
            empty_projected = gpd.GeoDataFrame(geometry=[], crs=facc_crs)
            empty_4326 = empty_projected.to_crs("EPSG:4326")
            return {
                "flood_gdf": empty_4326,
                "flood_gdf_projected": empty_projected,
                "watershed_gdf": watershed_gdf,
                "flood_area_km2": 0.0,
                "watershed_area_km2": watershed_area_km2,
                "water_level_m": water_level_m,
                "reliable": watershed_area_km2 >= FLOOD_MIN_RELIABLE_AREA_KM2,
            }

        # --- 6. Векторизация ---
        geoms = [
            shapely_shape(geom)
            for geom, value in rio_shapes(
                flood_mask.astype("uint8"), mask=flood_mask, transform=facc_transform
            )
            if value == 1
        ]
        merged = unary_union(geoms)

        flood_gdf = gpd.GeoDataFrame(geometry=[merged], crs=facc_crs)
        flood_area_km2 = float(flood_gdf.geometry.area.iloc[0] / 1e6)
        flood_gdf_4326 = flood_gdf.to_crs("EPSG:4326")

        return {
            "flood_gdf": flood_gdf_4326,
            "flood_gdf_projected": flood_gdf,
            "watershed_gdf": watershed_gdf,
            "flood_area_km2": flood_area_km2,
            "watershed_area_km2": watershed_area_km2,
            "water_level_m": water_level_m,
            "reliable": watershed_area_km2 >= FLOOD_MIN_RELIABLE_AREA_KM2,
        }
