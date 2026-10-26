"""
Оценка ущерба от затопления: число зданий и приблизительное население
в зоне затопления.

Гибридный подход:
1. Пытаемся получить реальные здания из OpenStreetMap через Overpass API
   (по границам зоны затопления). Если получилось — confidence="high".
2. Если Overpass недоступен, вернул ошибку/таймаут, либо зона затопления
   больше FLOOD_OVERPASS_MAX_AREA_KM2 (риск перегрузки публичного API) —
   используем fallback: доля категории "Artificial areas" в landcover
   внутри зоны затопления, умноженная на условную плотность застройки
   BUILDINGS_PER_KM2_URBAN_FALLBACK. confidence="low".

Население = building_count * people_per_building (без денежной оценки).
"""

import json

import requests
from rasterstats import zonal_stats

from config import (
    OVERPASS_URL,
    OVERPASS_TIMEOUT_S,
    DEFAULT_PEOPLE_PER_BUILDING,
    URBAN_LANDCOVER_CLASS,
    BUILDINGS_PER_KM2_URBAN_FALLBACK,
    FLOOD_OVERPASS_MAX_AREA_KM2,
    LANDCOVER_RASTER,
)
from data.landcover.landcover_categories import LANDCOVER_CLASSES

# Overpass API возвращает 406 Not Acceptable на запросы без явного User-Agent
# (в отличие от многих API, дефолтный UA requests здесь не подходит).
_OVERPASS_HEADERS = {"User-Agent": "watersheds-flood-risk/1.0"}


def _overpass_query_for_polygon(polygon_4326):
    """Строит Overpass QL запрос на здания внутри полигона (lat lon ... строкой poly)."""
    # Overpass ожидает координаты в порядке "lat lon lat lon ...".
    coords = polygon_4326.exterior.coords
    poly_str = " ".join(f"{lat} {lon}" for lon, lat in coords)

    query = f"""
    [out:json][timeout:{OVERPASS_TIMEOUT_S}];
    (
      way["building"](poly:"{poly_str}");
      relation["building"](poly:"{poly_str}");
    );
    out count;
    """
    return query


def _count_buildings_overpass(flood_gdf_4326):
    """Пытается получить число зданий из OSM Overpass. Возвращает int либо бросает исключение."""
    geom = flood_gdf_4326.geometry.iloc[0]

    # Overpass poly-фильтр работает с одним замкнутым контуром;
    # для мультиполигона считаем по каждому кольцу и суммируем.
    polygons = list(geom.geoms) if geom.geom_type == "MultiPolygon" else [geom]

    total = 0
    for poly in polygons:
        if poly.is_empty:
            continue
        query = _overpass_query_for_polygon(poly)
        response = requests.post(
            OVERPASS_URL,
            data={"data": query},
            timeout=OVERPASS_TIMEOUT_S,
            headers=_OVERPASS_HEADERS,
        )
        response.raise_for_status()
        payload = response.json()
        # out count; -> один элемент elements с тегами total/ways/relations
        elements = payload.get("elements", [])
        if elements:
            tags = elements[0].get("tags", {})
            total += int(tags.get("total", 0))

    return total


def _estimate_buildings_fallback(flood_gdf, flood_area_km2):
    """Оценка числа зданий по доле застроенной территории (landcover) в зоне затопления."""
    if flood_area_km2 <= 0:
        return 0

    stats = zonal_stats(
        vectors=flood_gdf,
        raster=LANDCOVER_RASTER,
        stats="count",
        categorical=True,
        category_map=LANDCOVER_CLASSES,
    )

    if not stats:
        return 0

    data = stats[0]
    total_count = data.get("count", 0)
    if total_count == 0:
        return 0

    urban_count = data.get(URBAN_LANDCOVER_CLASS, 0)
    urban_fraction = urban_count / total_count
    urban_area_km2 = urban_fraction * flood_area_km2

    return int(round(urban_area_km2 * BUILDINGS_PER_KM2_URBAN_FALLBACK))


def estimate_flood_damage(flood_gdf_4326, flood_gdf_projected, flood_area_km2,
                           people_per_building=None):
    """
    Parameters
    ----------
    flood_gdf_4326 : GeoDataFrame
        Зона затопления в EPSG:4326 (для Overpass).
    flood_gdf_projected : GeoDataFrame
        Та же зона в проекции, совместимой с landcover-растром (для fallback).
    flood_area_km2 : float
    people_per_building : float, optional
        Если не задано — берётся DEFAULT_PEOPLE_PER_BUILDING.

    Returns
    -------
    dict: {"building_count": int, "population_estimate": int,
           "people_per_building": float, "confidence": "high"|"low",
           "source": "osm"|"landcover_fallback"}
    """
    people_per_building = people_per_building or DEFAULT_PEOPLE_PER_BUILDING

    if flood_area_km2 <= 0:
        return {
            "building_count": 0,
            "population_estimate": 0,
            "people_per_building": people_per_building,
            "confidence": "high",
            "source": "empty_zone",
        }

    if flood_area_km2 <= FLOOD_OVERPASS_MAX_AREA_KM2:
        try:
            building_count = _count_buildings_overpass(flood_gdf_4326)
            return {
                "building_count": building_count,
                "population_estimate": int(round(building_count * people_per_building)),
                "people_per_building": people_per_building,
                "confidence": "high",
                "source": "osm",
            }
        except (requests.RequestException, ValueError, json.JSONDecodeError, KeyError):
            pass  # переходим к fallback

    building_count = _estimate_buildings_fallback(flood_gdf_projected, flood_area_km2)
    return {
        "building_count": building_count,
        "population_estimate": int(round(building_count * people_per_building)),
        "people_per_building": people_per_building,
        "confidence": "low",
        "source": "landcover_fallback",
    }
