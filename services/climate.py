from rasterstats import zonal_stats

MONTHS = range(1, 13)


def calculate_monthly_series(
    watershed,
    template_path
):
    result = []

    for month in MONTHS:

        raster_path = template_path.format(
            month=f"{month:02d}"
        )

        stats = zonal_stats(
            watershed,
            raster_path,
            stats="mean"
        )

        result.append(
            stats[0]["mean"]
        )

    return result
