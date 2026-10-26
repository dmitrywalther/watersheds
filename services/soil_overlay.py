"""Soil preview using the bounded categorical overlay engine."""

from config import SOIL_RASTER
from data.soil.soil_categories import SOIL_CLASSES
from services.categorical_overlay import OverlayLabels, build_categorical_map


_LABELS = OverlayLabels(
    map_subject='Почвенная карта',
    map_object='почвенную карту',
    map_genitive='почвенной карты',
    layer_genitive='почвенного слоя',
    class_genitive='почв',
    tab='Почвы',
)


def build_soil_map(watershed_gdf, raster_path=None):
    """Return the clipped WGS84 preview; exact shares are calculated separately."""
    return build_categorical_map(
        watershed_gdf, SOIL_RASTER if raster_path is None else raster_path,
        SOIL_CLASSES, labels=_LABELS,
    )
