from rasterstats import zonal_stats


def categorical_area_stats(
    watershed_gdf,
    raster_path,
    category_map=None,
    decimals=2
):
    """
    Вычисляет распределение категориального растра
    внутри водосборного бассейна.

    Parameters
    ----------
    watershed_gdf : GeoDataFrame
        Полигон водосбора.

    raster_path : str | Path
        Путь к категориальному raster.

    category_map : dict, optional
        Словарь соответствий:
        код -> название категории

    decimals : int
        Округление процентов.

    Returns
    -------
    dict
        {
            "Forest": 34.2,
            "Water": 12.8,
            ...
        }
    """

    stats = zonal_stats(
        vectors=watershed_gdf,
        raster=raster_path,
        stats="count",
        categorical=True,
        category_map=category_map
    )

    if not stats:
        return {}

    data = stats[0]

    total_count = data.get("count", 0)

    if total_count == 0:
        return {}

    result = {}

    for key, value in data.items():

        if key == "count":
            continue

        percent = round(
            value / total_count * 100,
            decimals
        )

        result[key] = percent

    return result
