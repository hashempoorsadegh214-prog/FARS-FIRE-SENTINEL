import os
import csv
import io
import json
import requests
from datetime import datetime, timedelta, timezone


# =========================================================
# CONFIG
# =========================================================

API_KEY = os.environ.get("FIRMS_MAP_KEY", "").strip()

BOUNDARY_FILE = "geoBoundaries-IRN-ADM1.geojson"

OUTPUT_FILE = "fires.csv"

ARCHIVE_DIR = "archive"
ARCHIVE_INDEX = "archive/index.json"

UPDATE_INFO_FILE = "update-info.json"


# ---------------------------------------------------------
# محدوده تقریبی استان فارس
# ---------------------------------------------------------

WEST = 50.0
SOUTH = 27.0
EAST = 54.5
NORTH = 31.5

AREA = f"{WEST},{SOUTH},{EAST},{NORTH}"


# ---------------------------------------------------------
# منابع FIRMS
# ---------------------------------------------------------

SENSORS = [
    "VIIRS_SNPP_NRT",
    "VIIRS_NOAA20_NRT",
    "VIIRS_NOAA21_NRT",
    "MODIS_NRT"
]


# ---------------------------------------------------------
# تعداد روز نگهداری
# ---------------------------------------------------------

DAYS_TO_KEEP = 5


# ---------------------------------------------------------
# زمان ایران
# ---------------------------------------------------------

IRAN_OFFSET = timedelta(
    hours=3,
    minutes=30
)


# =========================================================
# OUTPUT FIELDS
# =========================================================

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


# =========================================================
# TIME
# =========================================================

def parse_firms_datetime(
    acq_date,
    acq_time
):
    """
    تبدیل تاریخ و زمان FIRMS به datetime با timezone UTC.

    FIRMS:
        acq_date = YYYY-MM-DD
        acq_time = HHMM

    خروجی:
        datetime آگاه از timezone و بر مبنای UTC
    """

    try:

        date_text = str(
            acq_date
        ).strip()

        time_text = str(
            acq_time
        ).strip()

        if not date_text or not time_text:
            return None

        date_value = datetime.strptime(
            date_text,
            "%Y-%m-%d"
        )

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

        if hour > 23 or minute > 59:
            return None

        return datetime(
            date_value.year,
            date_value.month,
            date_value.day,
            hour,
            minute,
            tzinfo=timezone.utc
        )

    except Exception:
        return None


def get_iran_datetime(
    acq_date,
    acq_time
):
    """
    تبدیل زمان UTC مربوط به FIRMS به زمان ایران.
    """

    utc_value = parse_firms_datetime(
        acq_date,
        acq_time
    )

    if utc_value is None:
        return None

    return utc_value + IRAN_OFFSET


# =========================================================
# GEOJSON POINT IN POLYGON
# =========================================================

def point_in_ring(
    point,
    ring
):

    x, y = point

    inside = False

    j = len(ring) - 1

    for i in range(
        len(ring)
    ):

        xi, yi = ring[i]

        xj, yj = ring[j]

        condition = (
            (yi > y)
            !=
            (yj > y)
        )

        if condition:

            denominator = (
                yj - yi
            )

            if abs(denominator) < 1e-15:
                denominator = 1e-15

            x_intersection = (
                (xj - xi)
                *
                (y - yi)
                /
                denominator
            ) + xi

            if x < x_intersection:
                inside = not inside

        j = i

    return inside


def point_in_polygon(
    lon,
    lat,
    polygon
):

    if not polygon:
        return False

    # Outer ring
    if not point_in_ring(
        (lon, lat),
        polygon[0]
    ):
        return False

    # Holes
    for hole in polygon[1:]:

        if point_in_ring(
            (lon, lat),
            hole
        ):
            return False

    return True


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


# =========================================================
# LOAD FARS BOUNDARY
# =========================================================

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

    features = data.get(
        "features",
        []
    )

    # -----------------------------------------------------
    # جستجوی فارس
    # -----------------------------------------------------

    for feature in features:

        properties = feature.get(
            "properties",
            {}
        )

        possible_names = [

            properties.get(
                "shapeName"
            ),

            properties.get(
                "NAME_1"
            ),

            properties.get(
                "NAME"
            ),

            properties.get(
                "name"
            ),

            properties.get(
                "Name"
            )

        ]

        names = [

            str(value)
            .strip()
            .lower()

            for value in possible_names

            if value is not None
        ]

        if "fars" in names:

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


# =========================================================
# FIRMS API
# =========================================================

def get_fire_data(
    sensor
):

    if not API_KEY:

        raise RuntimeError(
            "FIRMS_MAP_KEY در محیط GitHub Actions تنظیم نشده است."
        )

    url = (
        "https://firms.modaps.eosdis.nasa.gov/"
        "api/area/csv/"
        f"{API_KEY}/"
        f"{sensor}/"
        f"{AREA}/"
        f"{DAYS_TO_KEEP}"
    )

    print(
        f"دریافت داده از {sensor}..."
    )

    response = requests.get(
        url,
        timeout=120,
        headers={
            "User-Agent":
                "Fars-Fire-Sentinel/1.0"
        }
    )

    response.raise_for_status()

    text = response.text.strip()

    if not text:

        print(
            f"{sensor}: داده‌ای دریافت نشد."
        )

        return []

    # -----------------------------------------------------
    # بررسی پاسخ‌های غیر CSV
    # -----------------------------------------------------

    if text.lower().startswith(
        "<html"
    ):

        raise RuntimeError(
            f"{sensor}: پاسخ API به صورت HTML دریافت شد."
        )

    reader = csv.DictReader(
        io.StringIO(text)
    )

    records = list(
        reader
    )

    print(
        f"{sensor}: "
        f"{len(records)} رکورد دریافت شد."
    )

    return records


# =========================================================
# SENSOR
# =========================================================

def detect_sensor(
    row
):

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


# =========================================================
# PROCESS RECORD
# =========================================================

def process_record(
    row,
    fars_geometry
):

    # -----------------------------------------------------
    # مختصات
    # -----------------------------------------------------

    try:

        lat = float(
            row.get(
                "latitude"
            )
        )

        lon = float(
            row.get(
                "longitude"
            )
        )

    except (
        ValueError,
        TypeError
    ):

        return None

    # -----------------------------------------------------
    # Bounding Box اولیه
    # -----------------------------------------------------

    if not (
        WEST <= lon <= EAST
        and
        SOUTH <= lat <= NORTH
    ):

        return None

    # -----------------------------------------------------
    # مرز دقیق فارس
    # -----------------------------------------------------

    if not point_in_geometry(
        lon,
        lat,
        fars_geometry
    ):

        return None

    # -----------------------------------------------------
    # تاریخ و زمان
    # -----------------------------------------------------

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

    # -----------------------------------------------------
    # Brightness
    # VIIRS معمولاً bright_ti4 دارد
    # MODIS معمولاً brightness دارد
    # -----------------------------------------------------

    brightness = row.get(
        "bright_ti4",
        ""
    )

    if not brightness:

        brightness = row.get(
            "brightness",
            ""
        )

    # -----------------------------------------------------
    # خروجی استاندارد
    # -----------------------------------------------------

    return {

        "latitude": lat,

        "longitude": lon,

        "acq_date": acq_date,

        "acq_time": acq_time,

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
            detect_sensor(
                row
            ),

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


# =========================================================
# DUPLICATES
# =========================================================

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

        unique.append(
            row
        )

    return unique


# =========================================================
# SORT
# =========================================================

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


# =========================================================
# SAVE CSV
# =========================================================

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

            fieldnames=FIELDNAMES,

            extrasaction="ignore"

        )

        writer.writeheader()

        writer.writerows(
            records
        )


# =========================================================
# LOAD EXISTING CSV
# =========================================================

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

            reader = csv.DictReader(
                file
            )

            return [
                row
                for row in reader
                if row
            ]

    except Exception as error:

        print(
            f"خطا در خواندن "
            f"{filename}: "
            f"{error}"
        )

        return []


# =========================================================
# IRAN ARCHIVE DATES
# =========================================================

def get_iran_archive_dates():

    now_utc = datetime.now(
        timezone.utc
    )

    now_iran = (
        now_utc +
        IRAN_OFFSET
    )

    dates = []

    for i in range(
        DAYS_TO_KEEP
    ):

        current_date = (
            now_iran.date()
            -
            timedelta(
                days=i
            )
        )

        dates.append(
            current_date.strftime(
                "%Y-%m-%d"
            )
        )

    return dates


# =========================================================
# SAVE DAILY ARCHIVES
# =========================================================

def save_daily_archives(
    all_fires,
    archive_dates
):

    os.makedirs(
        ARCHIVE_DIR,
        exist_ok=True
    )

    for date in archive_dates:

        daily = [

            row

            for row in all_fires

            if row.get(
                "iran_date",
                ""
            ) == date

        ]

        daily = remove_duplicates(
            daily
        )

        daily = sort_records(
            daily
        )

        filename = os.path.join(

            ARCHIVE_DIR,

            f"{date}.csv"

        )

        # -------------------------------------------------
        # اگر داده داریم، آرشیو را به‌روز کن
        # -------------------------------------------------

        if daily:

            save_csv(
                filename,
                daily
            )

            print(
                f"آرشیو ایران "
                f"{date}: "
                f"{len(daily)} رکورد"
            )

            continue

        # -------------------------------------------------
        # اگر داده جدید نداریم ولی آرشیو قبلی وجود دارد،
        # آن را حفظ کن.
        # -------------------------------------------------

        if os.path.exists(
            filename
        ):

            print(
                f"آرشیو ایران "
                f"{date}: "
                "داده جدید ندارد؛ "
                "نسخه قبلی حفظ شد."
            )

            continue

        # -------------------------------------------------
        # اگر اصلاً فایل وجود ندارد،
        # CSV خالی بساز.
        # -------------------------------------------------

        save_csv(
            filename,
            []
        )

        print(
            f"آرشیو ایران "
            f"{date}: "
            "بدون داده."
        )


# =========================================================
# CLEAN OLD ARCHIVES
# =========================================================

def clean_old_archives(
    archive_dates
):

    if not os.path.exists(
        ARCHIVE_DIR
    ):

        return

    valid_files = {

        f"{date}.csv"

        for date in archive_dates

    }

    for filename in os.listdir(
        ARCHIVE_DIR
    ):

        if not filename.endswith(
            ".csv"
        ):

            continue

        if filename not in valid_files:

            full_path = os.path.join(
                ARCHIVE_DIR,
                filename
            )

            try:

                os.remove(
                    full_path
                )

                print(
                    f"آرشیو قدیمی حذف شد: "
                    f"{filename}"
                )

            except Exception as error:

                print(
                    f"خطا در حذف "
                    f"{filename}: "
                    f"{error}"
                )


# =========================================================
# ARCHIVE INDEX
# =========================================================

def build_archive_index(
    archive_dates
):

    os.makedirs(
        ARCHIVE_DIR,
        exist_ok=True
    )

    index = []

    for date in archive_dates:

        filename = os.path.join(

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

                    reader = csv.DictReader(
                        file
                    )

                    for _ in reader:
                        count += 1

            except Exception:

                count = 0

        index.append({

            "date": date,

            "count": count

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

    print(
        "archive/index.json به‌روز شد."
    )


# =========================================================
# UPDATE MAIN CSV
# =========================================================

def update_main_csv(
    all_fires,
    archive_dates
):

    """
    fires.csv اکنون شامل کل ۵ روز اخیر است.

    این تغییر مهم است؛ چون index.html می‌تواند
    خودش ۲۴ ساعت / ۵ روز / امروز را فیلتر کند.
    """

    valid_dates = set(
        archive_dates
    )

    recent_fires = [

        row

        for row in all_fires

        if row.get(
            "iran_date",
            ""
        ) in valid_dates

    ]

    recent_fires = remove_duplicates(
        recent_fires
    )

    recent_fires = sort_records(
        recent_fires
    )

    if recent_fires:

        save_csv(

            OUTPUT_FILE,

            recent_fires

        )

        print(
            f"fires.csv به‌روز شد: "
            f"{len(recent_fires)} رکورد "
            f"از {len(valid_dates)} روز اخیر"
        )

        return recent_fires

    # -----------------------------------------------------
    # اگر API داده‌ای برنگرداند، CSV قبلی را حفظ کن
    # -----------------------------------------------------

    existing = load_existing_csv(
        OUTPUT_FILE
    )

    if existing:

        print(
            "داده جدید معتبر دریافت نشد؛ "
            "fires.csv قبلی حفظ شد."
        )

        return existing

    save_csv(
        OUTPUT_FILE,
        []
    )

    print(
        "fires.csv بدون داده ساخته شد."
    )

    return []


# =========================================================
# UPDATE INFO
# =========================================================

def save_update_info():

    now_utc = datetime.now(
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

    print(
        "update-info.json ساخته شد."
    )


# =========================================================
# MAIN
# =========================================================

def main():

    print("=" * 60)

    print(
        "شروع پایش حریق استان فارس"
    )

    print("=" * 60)

    # -----------------------------------------------------
    # API KEY
    # -----------------------------------------------------

    if not API_KEY:

        raise RuntimeError(
            "FIRMS_MAP_KEY موجود نیست."
        )

    # -----------------------------------------------------
    # Boundary
    # -----------------------------------------------------

    fars_geometry = load_fars_geometry()

    # -----------------------------------------------------
    # تاریخ ایران
    # -----------------------------------------------------

    archive_dates = (
        get_iran_archive_dates()
    )

    current_iran_date = (
        archive_dates[0]
    )

    print(
        f"تاریخ جاری ایران: "
        f"{current_iran_date}"
    )

    print(
        f"بازه نگهداری: "
        f"{archive_dates[-1]} "
        f"تا "
        f"{archive_dates[0]}"
    )

    # -----------------------------------------------------
    # دریافت داده
    # -----------------------------------------------------

    all_fires = []

    successful_sources = 0

    source_counts = {}

    for sensor in SENSORS:

        try:

            raw_records = get_fire_data(
                sensor
            )

            successful_sources += 1

            source_counts[sensor] = (
                len(raw_records)
            )

            for row in raw_records:

                processed = process_record(

                    row,

                    fars_geometry

                )

                if processed:

                    all_fires.append(
                        processed
                    )

        except Exception as error:

            source_counts[sensor] = 0

            print(
                f"خطا در {sensor}: "
                f"{error}"
            )

    # -----------------------------------------------------
    # Deduplicate
    # -----------------------------------------------------

    all_fires = remove_duplicates(
        all_fires
    )

    all_fires = sort_records(
        all_fires
    )

    print(
        f"کل رکورد معتبر داخل فارس: "
        f"{len(all_fires)}"
    )

    # -----------------------------------------------------
    # آرشیو روزانه
    # -----------------------------------------------------

    save_daily_archives(

        all_fires,

        archive_dates

    )

    # -----------------------------------------------------
    # حذف آرشیوهای قدیمی
    # -----------------------------------------------------

    clean_old_archives(
        archive_dates
    )

    # -----------------------------------------------------
    # ساخت index
    # -----------------------------------------------------

    build_archive_index(
        archive_dates
    )

    # -----------------------------------------------------
    # fires.csv
    # -----------------------------------------------------

    final_records = update_main_csv(

        all_fires,

        archive_dates

    )

    # -----------------------------------------------------
    # update-info
    # -----------------------------------------------------

    if successful_sources > 0:

        save_update_info()

    # -----------------------------------------------------
    # آمار سنجنده‌ها
    # -----------------------------------------------------

    viirs_count = 0

    modis_count = 0

    unknown_count = 0

    for row in final_records:

        sensor = row.get(
            "detected_sensor",
            "UNKNOWN"
        )

        if sensor == "VIIRS":

            viirs_count += 1

        elif sensor == "MODIS":

            modis_count += 1

        else:

            unknown_count += 1

    # -----------------------------------------------------
    # آمار روزها
    # -----------------------------------------------------

    daily_counts = {}

    for date in archive_dates:

        count = sum(

            1

            for row in final_records

            if row.get(
                "iran_date",
                ""
            ) == date

        )

        daily_counts[date] = count

    # -----------------------------------------------------
    # گزارش نهایی
    # -----------------------------------------------------

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

    if unknown_count:

        print(
            f"UNKNOWN: "
            f"{unknown_count}"
        )

    print(
        f"منابع موفق: "
        f"{successful_sources} / "
        f"{len(SENSORS)}"
    )

    print(
        "--------------------------------------------"
    )

    print(
        "آمار روزانه:"
    )

    for date in archive_dates:

        print(
            f"  {date}: "
            f"{daily_counts.get(date, 0)}"
        )

    print(
        "--------------------------------------------"
    )

    print(
        "منابع FIRMS:"
    )

    for sensor in SENSORS:

        print(
            f"  {sensor}: "
            f"{source_counts.get(sensor, 0)}"
        )

    print("=" * 60)

    print(
        "پایش با موفقیت پایان یافت."
    )

    print("=" * 60)


# =========================================================
# RUN
# =========================================================

if __name__ == "__main__":

    main()
