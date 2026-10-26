from rasterstats import zonal_stats

from services.climate import (
    calculate_monthly_series
)

from services.categorical_analysis import (
    categorical_area_stats
)
from services.soil import analyze_soil
from services.soil_overlay import build_soil_map
from services.landcover_overlay import build_landcover_map
from services.terrain_overlay import build_elevation_map, build_slope_map

from config import (
    CLIMATE_TEMPERATURE_TEMPLATE,
    CLIMATE_RAINFALL_TEMPLATE,
    ELEVATION_RASTER,
    LANDCOVER_RASTER,
    SLOPE_RASTER
)

from data.landcover.landcover_categories import (
    LANDCOVER_CLASSES
)


def run_analysis(watershed_gdf):

    # Максимальные, минимальные и средние высоты
    height_stat = zonal_stats(watershed_gdf,
                              ELEVATION_RASTER)
    # Максимальные, минимальные и средние углы наклона
    slope_stat = zonal_stats(watershed_gdf,
                             SLOPE_RASTER)

    temps = calculate_monthly_series(
        watershed_gdf,
        CLIMATE_TEMPERATURE_TEMPLATE
        )

    rainfall = calculate_monthly_series(
        watershed_gdf,
        CLIMATE_RAINFALL_TEMPLATE
        )

    landcover = categorical_area_stats(
        watershed_gdf,
        LANDCOVER_RASTER,
        LANDCOVER_CLASSES)

    return {'area_km2': float(watershed_gdf.geometry.area.iloc[0] / 1e6),
            'terrain': {
                'elevation': height_stat[0],
                'slope': slope_stat[0]
            },
            'climate': {
                'temperature': temps,
                'rainfall': rainfall
            },
            'landcover': landcover,
            'soil': analyze_soil(watershed_gdf),
            'soil_map': build_soil_map(watershed_gdf),
            'landcover_map': build_landcover_map(watershed_gdf),
            'elevation_map': build_elevation_map(watershed_gdf),
            'slope_map': build_slope_map(watershed_gdf)
            }
