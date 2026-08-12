import os
import csv
import io
import json
import requests
from datetime import datetime, timedelta, timezone


# ============================================================
# SETTINGS
# ============================================================

API_KEY = os.environ["FIRMS_MAP_KEY"]

BOUNDARY_FILE = "geoBoundaries-IRN-ADM1.geojson"

OUTPUT_FILE = "fires.csv"

ARCHIVE_DIR = "archive"

ARCHIVE_INDEX = "archive/index.json"

WEST = 50.0
SOUTH = 27.0
EAST = 54.5
NORTH = 31.5

AREA = f"{WEST},{SOUTH},{EAST},{NORTH}"

SENSORS = [
    "VIIRS_SNPP_NRT",
    "VIIRS_NOAA20_NRT",
    "VIIRS_NOAA21_NRT",
    "MODIS_NRT"
]

# تعداد روزهای آرشیو
DAYS_TO_KEEP = 5


# ============================================================
# POINT IN RING
# ============================================================

def point_in_ring(point, ring):

    x, y = point
    inside = False

    j = len(ring) - 1

    for i in range(len(ring)):

        xi, yi = ring[i]
        xj, yj = ring[j]

        intersect = (
            ((yi > y) != (yj > y))
            and
            (
                x <
                (xj - xi)
                * (y - yi)
                /
                ((yj - yi) or 1e-15)
                + xi
            )
        )

        if intersect:
            inside = not inside

        j = i

    return inside


# ============================================================
# POINT IN POLYGON
# ============================================================

def point_in_polygon(lon, lat, polygon):

    if not polygon:
        return False

    outer = polygon[0]

    if not point_in_ring(
        (lon, lat),
        outer
    ):
        return False

    for hole in polygon[1:]:

        if point_in_ring(
            (lon, lat),
            hole
        ):
            return False

    return True


# ============================================================
# POINT IN GEOMETRY
# ============================================================

def point_in_geometry(
    lon,
    lat,
    geometry
):

    if not geometry:
        return False

    geometry_type = geometry.get(
        "type"
    )

    coordinates = geometry.get(
        "coordinates"
    )

    if not coordinates:
        return False

    if geometry_type == "Polygon":

        return point_in_polygon(
            lon,
            lat,
            coordinates
        )

    if geometry_type == "MultiPolygon":

        for polygon in coordinates:

            if point_in_polygon(
                lon,
                lat,
                polygon
            ):
                return True

    return False


# ============================================================
# LOAD FARS BOUNDARY
# ============================================================

def load_fars_geometry():

    if not os.path.exists(
        BOUNDARY_FILE
    ):
        raise FileNotFoundError(
            f"{BOUNDARY_FILE} پیدا نشد."
        )

    with open(
        BOUNDARY_FILE,
        "r",
        encoding="utf-8"
    ) as f:

        data = json.load(f)

    if data.get("type") != "FeatureCollection":

        raise ValueError(
            "GeoJSON باید FeatureCollection باشد."
        )

    for feature in data.get(
        "features",
        []
    ):

        properties = feature.get(
            "properties",
            {}
        )

        name = str(
            properties.get(
                "shapeName",
                ""
            )
        ).strip().lower()

        if name == "fars":

            geometry = feature.get(
                "geometry"
            )

            if not geometry:

                raise ValueError(
                    "Geometry فارس پیدا نشد."
                )

            print(
                "مرز استان فارس با موفقیت پیدا شد."
            )

            return geometry

    raise ValueError(
        "استان Fars در GeoJSON پیدا نشد."
    )


# ============================================================
# FIRMS API
# ============================================================

def get_fire_data(
    sensor,
    start_date,
    day_range
):

    url = (
        "https://firms.modaps.eosdis.nasa.gov/"
        "api/area/csv/"
        f"{API_KEY}/"
        f"{sensor}/"
        f"{AREA}/"
        f"{day_range}/"
        f"{start_date}"
    )

    print(
        f"دریافت داده از {sensor}..."
    )

    response = requests.get(
        url,
        timeout=120
    )

    response.raise_for_status()

    text = response.text.strip()

    if not text:

        print(
            f"{sensor}: داده‌ای دریافت نشد."
        )

        return []

    reader = csv.DictReader(
        io.StringIO(text)
    )

    records = list(reader)

    print(
        f"{sensor}: {len(records)} رکورد"
    )

    return records


# ============================================================
# SENSOR
# ============================================================

def detect_sensor(row):

    instrument = str(
        row.get(
            "instrument",
            ""
        )
    ).strip().upper()

    satellite = str(
        row.get(
            "satellite",
            ""
        )
    ).strip().upper()

    if "VIIRS" in instrument:
        return "VIIRS"

    if "MODIS" in instrument:
        return "MODIS"

    if "VIIRS" in satellite:
        return "VIIRS"

    if "MODIS" in satellite:
        return "MODIS"

    return "UNKNOWN"


# ============================================================
# PROCESS RECORD
# ============================================================

def process_record(
    row,
    fars_geometry
):

    try:

        lat = float(
            row["latitude"]
        )

        lon = float(
            row["longitude"]
        )

    except (
        KeyError,
        ValueError,
        TypeError
    ):

        return None

    if not (
        WEST <= lon <= EAST
        and
        SOUTH <= lat <= NORTH
    ):

        return None

    if not point_in_geometry(
        lon,
        lat,
        fars_geometry
    ):

        return None

    acq_date = str(
        row.get(
            "acq_date",
            ""
        )
    ).strip()

    acq_time = str(
        row.get(
            "acq_time",
            ""
        )
    ).strip()

    year = ""
    month = ""
    day = ""

    try:

        dt = datetime.strptime(
            acq_date,
            "%Y-%m-%d"
        )

        year = dt.year
        month = dt.month
        day = dt.day

    except ValueError:
        pass

    return {

        "latitude": lat,

        "longitude": lon,

        "acq_date": acq_date,

        "acq_time": acq_time,

        "year": year,

        "month": month,

        "day": day,

        "satellite": str(
            row.get(
                "satellite",
                ""
            )
        ).strip(),

        "instrument": str(
            row.get(
                "instrument",
                ""
            )
        ).strip(),

        "detected_sensor":
            detect_sensor(row),

        "confidence": str(
            row.get(
                "confidence",
                ""
            )
        ).strip(),

        "frp": str(
            row.get(
                "frp",
                ""
            )
        ).strip(),

        "brightness": str(
            row.get(
                "bright_ti4",
                row.get(
                    "brightness",
                    ""
                )
            )
        ).strip(),

        "daynight": str(
            row.get(
                "daynight",
                ""
            )
        ).strip(),

        "scan": str(
            row.get(
                "scan",
                ""
            )
        ).strip(),

        "track": str(
            row.get(
                "track",
                ""
            )
        ).strip()
    }


# ============================================================
# DUPLICATES
# ============================================================

def remove_duplicates(records):

    unique = []
    seen = set()

    for row in records:

        key = (
            round(
                float(
                    row["latitude"]
                ),
                5
            ),

            round(
                float(
                    row["longitude"]
                ),
                5
            ),

            row["acq_date"],

            row["acq_time"],

            row["satellite"],

            row["instrument"]
        )

        if key in seen:
            continue

        seen.add(key)
        unique.append(row)

    return unique


# ============================================================
# SORT
# ============================================================

def sort_records(records):

    return sorted(
        records,
        key=lambda x: (
            x.get(
                "acq_date",
                ""
            ),
            x.get(
                "acq_time",
                ""
            )
        ),
        reverse=True
    )


# ============================================================
# CSV
# ============================================================

FIELDNAMES = [

    "latitude",
    "longitude",

    "acq_date",
    "acq_time",

    "year",
    "month",
    "day",

    "satellite",
    "instrument",

    "detected_sensor",

    "confidence",
    "frp",

    "brightness",
    "daynight",

    "scan",
    "track"
]


def save_csv(
    filename,
    records
):

    with open(
        filename,
        "w",
        newline="",
        encoding="utf-8-sig"
    ) as f:

        writer = csv.DictWriter(
            f,
            fieldnames=FIELDNAMES
        )

        writer.writeheader()

        writer.writerows(records)


# ============================================================
# DATE RANGE
# ============================================================

def get_date_list():

    today = datetime.now(
        timezone.utc
    ).date()

    dates = []

    for i in range(
        DAYS_TO_KEEP
    ):

        date_value = (
            today -
            timedelta(days=i)
        )

        dates.append(
            date_value.strftime(
                "%Y-%m-%d"
            )
        )

    return dates


# ============================================================
# SAVE DAILY ARCHIVES
# ============================================================

def save_daily_archives(
    records,
    dates
):

    os.makedirs(
        ARCHIVE_DIR,
        exist_ok=True
    )

    grouped = {}

    for date in dates:

        grouped[date] = []

    for row in records:

        date = row.get(
            "acq_date",
            ""
        )

        if date in grouped:

            grouped[date].append(
                row
            )

    for date in dates:

        daily = remove_duplicates(
            grouped[date]
        )

        daily = sort_records(
            daily
        )

        filename = os.path.join(
            ARCHIVE_DIR,
            f"{date}.csv"
        )

        save_csv(
            filename,
            daily
        )

        print(
            f"آرشیو {date}: "
            f"{len(daily)} رکورد"
        )


# ============================================================
# CLEAN OLD ARCHIVES
# ============================================================

def clean_old_archives(
    dates_to_keep
):

    if not os.path.exists(
        ARCHIVE_DIR
    ):
        return

    valid_names = {
        f"{date}.csv"
        for date in dates_to_keep
    }

    for filename in os.listdir(
        ARCHIVE_DIR
    ):

        if not filename.endswith(
            ".csv"
        ):
            continue

        if filename not in valid_names:

            path = os.path.join(
                ARCHIVE_DIR,
                filename
            )

            os.remove(path)

            print(
                f"حذف آرشیو قدیمی: "
                f"{filename}"
            )


# ============================================================
# ARCHIVE INDEX
# ============================================================

def save_archive_index(
    dates
):

    os.makedirs(
        ARCHIVE_DIR,
        exist_ok=True
    )

    index = []

    for date in dates:

        filename = os.path.join(
            ARCHIVE_DIR,
            f"{date}.csv"
        )

        count = 0

        if os.path.exists(
            filename
        ):

            with open(
                filename,
                "r",
                encoding="utf-8-sig"
            ) as f:

                count = max(
                    0,
                    sum(
                        1
                        for _ in f
                    ) - 1
                )

        index.append(
            {
                "date": date,
                "count": count
            }
        )

    with open(
        ARCHIVE_INDEX,
        "w",
        encoding="utf-8"
    ) as f:

        json.dump(
            index,
            f,
            ensure_ascii=False,
            indent=2
        )


# ============================================================
# MAIN
# ============================================================

def main():

    print(
        "=" * 60
    )

    print(
        "FARS FIRE SENTINEL"
    )

    print(
        "=" * 60
    )

    fars_geometry = (
        load_fars_geometry()
    )

    dates = get_date_list()

    start_date = dates[-1]

    print(
        f"بازه داده: "
        f"{start_date} تا {dates[0]}"
    )

    all_fires = []

    for sensor in SENSORS:

        try:

            records = get_fire_data(
                sensor,
                start_date,
                DAYS_TO_KEEP
            )

            for row in records:

                processed = (
                    process_record(
                        row,
                        fars_geometry
                    )
                )

                if processed:

                    all_fires.append(
                        processed
                    )

        except Exception as e:

            print(
                f"خطا در {sensor}: {e}"
            )

    all_fires = remove_duplicates(
        all_fires
    )

    all_fires = sort_records(
        all_fires
    )

    # آرشیو ۵ روزه
    save_daily_archives(
        all_fires,
        dates
    )

    # حذف آرشیو قدیمی
    clean_old_archives(
        dates
    )

    # ساخت index
    save_archive_index(
        dates
    )

    # داده امروز
    today_records = [
        row
        for row in all_fires
        if row["acq_date"] == dates[0]
    ]

    today_records = sort_records(
        today_records
    )

    save_csv(
        OUTPUT_FILE,
        today_records
    )

    print()
    print(
        f"حریق امروز: "
        f"{len(today_records)}"
    )

    print(
        f"کل داده ۵ روز اخیر: "
        f"{len(all_fires)}"
    )

    print(
        "به‌روزرسانی با موفقیت انجام شد."
    )

    print(
        "=" * 60
    )


if __name__ == "__main__":
    main()
