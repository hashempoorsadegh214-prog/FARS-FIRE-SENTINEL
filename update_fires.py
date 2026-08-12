import os
import csv
import io
import json
import requests
from datetime import datetime

# ============================================================
# تنظیمات
# ============================================================

API_KEY = os.environ["FIRMS_MAP_KEY"]

# محدوده تقریبی اطراف استان فارس
WEST = 50.0
SOUTH = 27.0
EAST = 54.5
NORTH = 31.5

AREA = f"{WEST},{SOUTH},{EAST},{NORTH}"

# سنجنده‌های FIRMS
SENSORS = [
    "VIIRS_SNPP_NRT",
    "VIIRS_NOAA20_NRT",
    "VIIRS_NOAA21_NRT",
    "MODIS_NRT"
]

# فایل مرز دقیق فارس
BOUNDARY_FILE = "fars.geojson"

# فایل خروجی
OUTPUT_FILE = "fires.csv"


# ============================================================
# Point in Ring
# ============================================================

def point_in_ring(point, ring):

    x, y = point
    inside = False

    j = len(ring) - 1

    for i in range(len(ring)):

        xi, yi = ring[i]
        xj, yj = ring[j]

        if (
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
        ):
            inside = not inside

        j = i

    return inside


# ============================================================
# Point in Polygon
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

    # بررسی سوراخ‌های Polygon
    for hole in polygon[1:]:

        if point_in_ring(
            (lon, lat),
            hole
        ):
            return False

    return True


# ============================================================
# Point in Geometry
# ============================================================

def point_in_geometry(lon, lat, geometry):

    if not geometry:
        return False

    geom_type = geometry.get("type")
    coords = geometry.get("coordinates")

    if not coords:
        return False

    # Polygon
    if geom_type == "Polygon":

        return point_in_polygon(
            lon,
            lat,
            coords
        )

    # MultiPolygon
    if geom_type == "MultiPolygon":

        for polygon in coords:

            if point_in_polygon(
                lon,
                lat,
                polygon
            ):
                return True

        return False

    return False


# ============================================================
# خواندن Geometry فارس
# ============================================================

def load_fars_geometries():

    if not os.path.exists(BOUNDARY_FILE):

        raise FileNotFoundError(
            f"فایل {BOUNDARY_FILE} پیدا نشد."
        )

    with open(
        BOUNDARY_FILE,
        "r",
        encoding="utf-8"
    ) as f:

        data = json.load(f)

    geometries = []

    # Geometry مستقیم
    if data.get("type") in (
        "Polygon",
        "MultiPolygon"
    ):

        geometries.append(data)

    # Feature
    elif data.get("type") == "Feature":

        geometry = data.get("geometry")

        if geometry:
            geometries.append(geometry)

    # FeatureCollection
    elif data.get("type") == "FeatureCollection":

        features = data.get(
            "features",
            []
        )

        for feature in features:

            geometry = feature.get(
                "geometry"
            )

            if geometry:
                geometries.append(
                    geometry
                )

    else:

        raise ValueError(
            "ساختار fars.geojson معتبر نیست."
        )

    if not geometries:

        raise ValueError(
            "هیچ Geometry معتبری در fars.geojson پیدا نشد."
        )

    return geometries


# ============================================================
# بررسی نقطه داخل فارس
# ============================================================

def point_inside_fars(
    lon,
    lat,
    geometries
):

    for geometry in geometries:

        if point_in_geometry(
            lon,
            lat,
            geometry
        ):
            return True

    return False


# ============================================================
# دریافت داده NASA FIRMS
# ============================================================

def get_fire_data(sensor):

    url = (
        "https://firms.modaps.eosdis.nasa.gov/"
        "api/area/csv/"
        f"{API_KEY}/"
        f"{sensor}/"
        f"{AREA}/1"
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
        f"{sensor}: "
        f"{len(records)} رکورد دریافت شد."
    )

    return records


# ============================================================
# تشخیص سنجنده
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

    return (
        instrument
        or satellite
        or "UNKNOWN"
    )


# ============================================================
# پردازش رکورد
# ============================================================

def process_record(
    row,
    fars_geometries
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

    # --------------------------------------------------------
    # بررسی محدوده تقریبی
    # --------------------------------------------------------

    if not (
        WEST <= lon <= EAST
        and
        SOUTH <= lat <= NORTH
    ):

        return None

    # --------------------------------------------------------
    # بررسی مرز دقیق فارس
    # --------------------------------------------------------

    if not point_inside_fars(
        lon,
        lat,
        fars_geometries
    ):

        return None

    # --------------------------------------------------------
    # اطلاعات پایه
    # --------------------------------------------------------

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

    sensor = detect_sensor(row)

    # --------------------------------------------------------
    # خروجی استاندارد
    # --------------------------------------------------------

    return {

        "latitude":
            lat,

        "longitude":
            lon,

        "acq_date":
            acq_date,

        "acq_time":
            acq_time,

        "year":
            year,

        "month":
            month,

        "day":
            day,

        "satellite":
            str(
                row.get(
                    "satellite",
                    ""
                )
            ).strip(),

        "instrument":
            str(
                row.get(
                    "instrument",
                    ""
                )
            ).strip(),

        "detected_sensor":
            sensor,

        "confidence":
            str(
                row.get(
                    "confidence",
                    ""
                )
            ).strip(),

        "frp":
            str(
                row.get(
                    "frp",
                    ""
                )
            ).strip(),

        "brightness":
            str(
                row.get(
                    "bright_ti4",
                    row.get(
                        "brightness",
                        ""
                    )
                )
            ).strip(),

        "daynight":
            str(
                row.get(
                    "daynight",
                    ""
                )
            ).strip(),

        "scan":
            str(
                row.get(
                    "scan",
                    ""
                )
            ).strip(),

        "track":
            str(
                row.get(
                    "track",
                    ""
                )
            ).strip()
    }


# ============================================================
# حذف رکوردهای تکراری
# ============================================================

def remove_duplicates(records):

    unique = []
    seen = set()

    for row in records:

        key = (
            round(
                float(row["latitude"]),
                5
            ),

            round(
                float(row["longitude"]),
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
# ذخیره CSV
# ============================================================

def save_csv(records):

    fieldnames = [
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

    with open(
        OUTPUT_FILE,
        "w",
        newline="",
        encoding="utf-8-sig"
    ) as f:

        writer = csv.DictWriter(
            f,
            fieldnames=fieldnames
        )

        writer.writeheader()

        writer.writerows(records)


# ============================================================
# آمار
# ============================================================

def print_statistics(records):

    viirs_count = sum(
        1
        for row in records
        if row["detected_sensor"] == "VIIRS"
    )

    modis_count = sum(
        1
        for row in records
        if row["detected_sensor"] == "MODIS"
    )

    print()
    print("=" * 60)
    print("آمار نهایی حریق استان فارس")
    print("=" * 60)

    print(
        f"کل حریق‌ها: {len(records)}"
    )

    print(
        f"VIIRS: {viirs_count}"
    )

    print(
        f"MODIS: {modis_count}"
    )

    print(
        f"خروجی: {OUTPUT_FILE}"
    )

    print("=" * 60)


# ============================================================
# Main
# ============================================================

def main():

    print()
    print("=" * 60)
    print("NASA FIRMS - FARS FIRE SENTINEL")
    print("=" * 60)

    # --------------------------------------------------------
    # خواندن مرز
    # --------------------------------------------------------

    fars_geometries = load_fars_geometries()

    print(
        f"تعداد Geometryهای مرز فارس: "
        f"{len(fars_geometries)}"
    )

    all_fires = []

    # --------------------------------------------------------
    # دریافت سنجنده‌ها
    # --------------------------------------------------------

    for sensor in SENSORS:

        try:

            records = get_fire_data(
                sensor
            )

            for row in records:

                result = process_record(
                    row,
                    fars_geometries
                )

                if result:

                    all_fires.append(
                        result
                    )

        except Exception as e:

            print(
                f"خطا در {sensor}: {e}"
            )

    # --------------------------------------------------------
    # مرتب‌سازی
    # --------------------------------------------------------

    all_fires.sort(
        key=lambda row: (
            row["acq_date"],
            row["acq_time"]
        ),
        reverse=True
    )

    # --------------------------------------------------------
    # حذف تکراری
    # --------------------------------------------------------

    before = len(all_fires)

    all_fires = remove_duplicates(
        all_fires
    )

    after = len(all_fires)

    print()
    print(
        f"رکورد قبل از حذف تکراری: {before}"
    )

    print(
        f"رکورد نهایی: {after}"
    )

    # --------------------------------------------------------
    # ساخت CSV
    # --------------------------------------------------------

    save_csv(
        all_fires
    )

    # --------------------------------------------------------
    # آمار
    # --------------------------------------------------------

    print_statistics(
        all_fires
    )


# ============================================================
# اجرای برنامه
# ============================================================

if __name__ == "__main__":
    main()
