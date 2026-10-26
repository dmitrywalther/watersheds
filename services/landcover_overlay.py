"""Land-cover preview using the bounded categorical overlay engine."""

from config import LANDCOVER_RASTER
from data.landcover.landcover_categories import LANDCOVER_CLASSES
from services.categorical_overlay import OverlayLabels, build_categorical_map


_LABELS = OverlayLabels(
    map_subject='Карта земного покрова',
    map_object='карту земного покрова',
    map_genitive='карты земного покрова',
    layer_genitive='слоя земного покрова',
    class_genitive='земного покрова',
    tab='Покров',
)


def build_landcover_map(watershed_gdf, raster_path=None):
    """Keep classes and gaps distinct, including the dictionary's No data code."""
    return build_categorical_map(
        watershed_gdf, LANDCOVER_RASTER if raster_path is None else raster_path,
        LANDCOVER_CLASSES, labels=_LABELS, excluded_codes=(230,),
    )
