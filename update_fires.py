import os
import csv
import io
import json
import requests
from datetime import datetime, timedelta, timezone


# ============================================================
# تنظیمات
# ============================================================

API_KEY = os.environ["FIRMS_MAP_KEY"]

BOUNDARY_FILE = "geoBoundaries-IRN-ADM1.geojson"

OUTPUT_FILE = "fires.csv"

ARCHIVE_DIR = "archive"

ARCHIVE_INDEX = "archive/index.json"

UPDATE_INFO_FILE = "update-info.json"


# محدوده تقریبی فارس
WEST = 50.0
SOUTH = 27.0
EAST = 54.5
NORTH = 31.5

AREA = f"{WEST},{SOUTH},{EAST},{NORTH}"


# منابع FIRMS
SENSORS = [
    "VIIRS_SNPP_NRT",
    "VIIRS_NOAA20_NRT",
    "VIIRS_NOAA21_NRT",
    "MODIS_NRT"
]


# تعداد روز آرشیو
DAYS_TO_KEEP = 5


# اختلاف ساعت ایران با UTC
# ایران = UTC + 3:30
IRAN_OFFSET = timedelta(hours=3, minutes=30)


# ============================================================
# ستون‌های خروجی
# ============================================================

FIELDNAMES = [
    "latitude",
    "longitude",
    "acq_date",
    "acq_time",
    "iran_date",
    "iran_time",
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


# ============================================================
# تبدیل تاریخ و ساعت FIRMS به وقت ایران
# ============================================================

def get_iran_datetime(acq_date, acq_time):

    try:

        date_part = datetime.strptime(
            str(acq_date),
            "%Y-%m-%d"
        )

        time_text = str(
            acq_time
        ).strip()

        time_text = (
            time_text
            .replace(":", "")
            .replace(".", "")
            .replace(" ", "")
        )

        time_text = time_text.zfill(4)

        hour = int(
            time_text[:2]
        )

        minute = int(
            time_text[2:4]
        )

        utc_datetime = datetime(
            date_part.year,
            date_part.month,
            date_part.day,
            hour,
            minute,
            tzinfo=timezone.utc
        )

        iran_datetime = (
            utc_datetime + IRAN_OFFSET
        )

        return iran_datetime

    except Exception:

        return None


# ============================================================
# Point in Ring
# ============================================================

def point_in_ring(point, ring):

    x, y = point

    inside = False

    j = len(ring) - 1

    for i in range(
        len(ring)
    ):

        xi, yi = ring[i]

        xj, yj = ring[j]

        intersect = (
            ((yi > y) != (yj > y))
            and
            (
                x <
                (xj - xi)
                *
                (y - yi)
                /
                ((yj - yi) or 1e-15)
                +
                xi
            )
        )

        if intersect:

            inside = not inside

        j = i

    return inside


# ============================================================
# Point in Polygon
# ============================================================

def point_in_polygon(
    lon,
    lat,
    polygon
):

    if not polygon:

        return False

    if not point_in_ring(
        (lon, lat),
        polygon[0]
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
# Point in Geometry
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
# Load Fars Boundary
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
    ) as file:

        data = json.load(file)


    if data.get(
        "type"
    ) != "FeatureCollection":

        raise ValueError(
            "GeoJSON معتبر نیست."
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
        "استان فارس در GeoJSON پیدا نشد."
    )


# ============================================================
# دریافت داده FIRMS
# ============================================================

def get_fire_data(
    sensor,
    days=5
):

    url = (
        "https://firms.modaps.eosdis.nasa.gov/"
        "api/area/csv/"
        f"{API_KEY}/"
        f"{sensor}/"
        f"{AREA}/"
        f"{days}"
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


    if (
        "VIIRS" in instrument
        or
        "VIIRS" in satellite
    ):

        return "VIIRS"


    if (
        "MODIS" in instrument
        or
        "MODIS" in satellite
    ):

        return "MODIS"


    return "UNKNOWN"


# ============================================================
# پردازش رکورد
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


    # محدوده تقریبی
    if not (
        WEST <= lon <= EAST
        and
        SOUTH <= lat <= NORTH
    ):

        return None


    # مرز دقیق فارس
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


    iran_datetime = get_iran_datetime(
        acq_date,
        acq_time
    )


    if iran_datetime is None:

        return None


    brightness = row.get(
        "bright_ti4",
        ""
    )


    if not brightness:

        brightness = row.get(
            "brightness",
            ""
        )


    return {

        "latitude":
            lat,

        "longitude":
            lon,

        "acq_date":
            acq_date,

        "acq_time":
            acq_time,

        "iran_date":
            iran_datetime.strftime(
                "%Y-%m-%d"
            ),

        "iran_time":
            iran_datetime.strftime(
                "%H:%M"
            ),

        "year":
            iran_datetime.year,

        "month":
            iran_datetime.month,

        "day":
            iran_datetime.day,

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
            detect_sensor(row),

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
                brightness
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

def remove_duplicates(
    records
):

    unique = []

    seen = set()


    for row in records:

        try:

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

                row.get(
                    "acq_date",
                    ""
                ),

                row.get(
                    "acq_time",
                    ""
                ),

                row.get(
                    "satellite",
                    ""
                ),

                row.get(
                    "instrument",
                    ""
                )
            )

        except Exception:

            continue


        if key in seen:

            continue


        seen.add(key)

        unique.append(row)


    return unique


# ============================================================
# مرتب‌سازی
# ============================================================

def sort_records(
    records
):

    return sorted(
        records,
        key=lambda row: (
            row.get(
                "iran_date",
                ""
            ),
            row.get(
                "iran_time",
                ""
            )
        ),
        reverse=True
    )


# ============================================================
# ذخیره CSV
# ============================================================

def save_csv(
    filename,
    records
):

    folder = os.path.dirname(
        filename
    )


    if folder:

        os.makedirs(
            folder,
            exist_ok=True
        )


    with open(
        filename,
        "w",
        newline="",
        encoding="utf-8-sig"
    ) as file:

        writer = csv.DictWriter(
            file,
            fieldnames=FIELDNAMES
        )


        writer.writeheader()


        writer.writerows(
            records
        )


# ============================================================
# خواندن CSV قبلی
# ============================================================

def load_existing_csv(
    filename
):

    if not os.path.exists(
        filename
    ):

        return []


    try:

        with open(
            filename,
            "r",
            newline="",
            encoding="utf-8-sig"
        ) as file:

            reader = csv.DictReader(file)

            return [
                row
                for row in reader
                if row
            ]

    except Exception as error:

        print(
            f"خطا در خواندن {filename}: {error}"
        )

        return []


# ============================================================
# تاریخ‌های آرشیو بر اساس ایران
# ============================================================

def get_iran_archive_dates():

    now_utc = datetime.now(
        timezone.utc
    )


    now_iran =
        now_utc + IRAN_OFFSET


    dates = []

    for i in range(
        DAYS_TO_KEEP
    ):

        current_date =
            now_iran.date() - timedelta(days=i)


        dates.append(
            current_date.strftime(
                "%Y-%m-%d"
            )
        )


    return dates


# ============================================================
# ذخیره آرشیو بر اساس تاریخ ایران
# ============================================================

def save_daily_archives(
    all_fires,
    archive_dates
):

    os.makedirs(
        ARCHIVE_DIR,
        exist_ok=True
    )


    for date in archive_dates:

        daily = []


        for row in all_fires:

            if row.get(
                "iran_date",
                ""
            ) == date:

                daily.append(row)


        daily =
            remove_duplicates(
                daily
            )


        daily =
            sort_records(
                daily
            )


        filename =
            os.path.join(
                ARCHIVE_DIR,
                f"{date}.csv"
            )


        if daily:

            save_csv(
                filename,
                daily
            )


            print(
                f"آرشیو ایران {date}: "
                f"{len(daily)} رکورد"
            )


        elif os.path.exists(
            filename
        ):

            print(
                f"آرشیو ایران {date}: "
                "داده جدید ندارد؛ قبلی حفظ شد."
            )


        else:

            save_csv(
                filename,
                []
            )


            print(
                f"آرشیو ایران {date}: "
                "بدون داده."
            )


# ============================================================
# حذف آرشیوهای قدیمی
# ============================================================

def clean_old_archives(
    archive_dates
):

    if not os.path.exists(
        ARCHIVE_DIR
    ):

        return


    valid_files = set()


    for date in archive_dates:

        valid_files.add(
            f"{date}.csv"
        )


    for filename in os.listdir(
        ARCHIVE_DIR
    ):

        if not filename.endswith(
            ".csv"
        ):

            continue


        if filename not in valid_files:

            os.remove(
                os.path.join(
                    ARCHIVE_DIR,
                    filename
                )
            )


# ============================================================
# ساخت index آرشیو
# ============================================================

def build_archive_index(
    archive_dates
):

    os.makedirs(
        ARCHIVE_DIR,
        exist_ok=True
    )


    index = []


    for date in archive_dates:

        filename =
            os.path.join(
                ARCHIVE_DIR,
                f"{date}.csv"
            )


        count = 0


        if os.path.exists(
            filename
        ):

            try:

                with open(
                    filename,
                    "r",
                    encoding="utf-8-sig"
                ) as file:

                    reader =
                        csv.DictReader(file)


                    for _ in reader:

                        count += 1


            except Exception:

                count = 0


        index.append({

            "date":
                date,

            "count":
                count
        })


    with open(
        ARCHIVE_INDEX,
        "w",
        encoding="utf-8"
    ) as file:

        json.dump(
            index,
            file,
            ensure_ascii=False,
            indent=2
        )


# ============================================================
# fires.csv بر اساس امروز ایران
# ============================================================

def update_main_csv(
    all_fires,
    current_iran_date
):

    today_fires = []


    for row in all_fires:

        if row.get(
            "iran_date",
            ""
        ) == current_iran_date:

            today_fires.append(row)


    today_fires =
        remove_duplicates(
            today_fires
        )


    today_fires =
        sort_records(
            today_fires
        )


    if today_fires:

        save_csv(
            OUTPUT_FILE,
            today_fires
        )


        print(
            f"fires.csv به‌روز شد: "
            f"{len(today_fires)} رکورد"
        )


        return today_fires


    existing =
        load_existing_csv(
            OUTPUT_FILE
        )


    if existing:

        print(
            "امروز ایران داده جدید ندارد؛ "
            "fires.csv قبلی حفظ شد."
        )


        return existing


    save_csv(
        OUTPUT_FILE,
        []
    )


    return []


# ============================================================
# زمان آخرین آپدیت
# ============================================================

def save_update_info():

    now_utc =
        datetime.now(
            timezone.utc
        )


    info = {

        "updated_at_utc":
            now_utc.isoformat(),

        "updated_at_timestamp":
            now_utc.timestamp()
    }


    with open(
        UPDATE_INFO_FILE,
        "w",
        encoding="utf-8"
    ) as file:

        json.dump(
            info,
            file,
            ensure_ascii=False,
            indent=2
        )


# ============================================================
# MAIN
# ============================================================

def main():

    print("=" * 60)

    print(
        "شروع پایش حریق استان فارس"
    )

    print("=" * 60)


    fars_geometry =
        load_fars_geometry()


    archive_dates =
        get_iran_archive_dates()


    current_iran_date =
        archive_dates[0]


    print(
        f"تاریخ جاری ایران: "
        f"{current_iran_date}"
    )


    all_fires = []


    successful_sources = 0


    for sensor in SENSORS:

        try:

            raw_records =
                get_fire_data(
                    sensor,
                    DAYS_TO_KEEP
                )


            successful_sources += 1


            for row in raw_records:

                processed =
                    process_record(
                        row,
                        fars_geometry
                    )


                if processed:

                    all_fires.append(
                        processed
                    )


        except Exception as error:

            print(
                f"خطا در {sensor}: "
                f"{error}"
            )


    all_fires =
        remove_duplicates(
            all_fires
        )


    all_fires =
        sort_records(
            all_fires
        )


    print(
        f"کل رکورد معتبر: "
        f"{len(all_fires)}"
    )


    save_daily_archives(
        all_fires,
        archive_dates
    )


    clean_old_archives(
        archive_dates
    )


    build_archive_index(
        archive_dates
    )


    final_records =
        update_main_csv(
            all_fires,
            current_iran_date
        )


    if successful_sources > 0:

        save_update_info()


    viirs_count = 0
    modis_count = 0


    for row in final_records:

        if row.get(
            "detected_sensor"
        ) == "VIIRS":

            viirs_count += 1


        elif row.get(
            "detected_sensor"
        ) == "MODIS":

            modis_count += 1


    print("=" * 60)


    print(
        f"fires.csv: "
        f"{len(final_records)} رکورد"
    )


    print(
        f"VIIRS: "
        f"{viirs_count}"
    )


    print(
        f"MODIS: "
        f"{modis_count}"
    )


    print(
        f"منابع موفق: "
        f"{successful_sources}"
    )


    print(
        "پایش با موفقیت پایان یافت."
    )


    print("=" * 60)


# ============================================================
# START
# ============================================================

if __name__ == "__main__":

    main()
