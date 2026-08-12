import os
import csv
import io
import json
import requests
from datetime import datetime, timedelta, timezone


API_KEY = os.environ["FIRMS_MAP_KEY"]

BOUNDARY_FILE = "geoBoundaries-IRN-ADM1.geojson"
OUTPUT_FILE = "fires.csv"
ARCHIVE_DIR = "archive"
ARCHIVE_INDEX = "archive/index.json"
UPDATE_INFO_FILE = "update-info.json"

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

DAYS_TO_KEEP = 5

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


def point_in_ring(point, ring):
    x, y = point
    inside = False
    j = len(ring) - 1

    for i in range(len(ring)):
        xi, yi = ring[i]
        xj, yj = ring[j]

        if ((yi > y) != (yj > y)) and (
            x < (xj - xi) * (y - yi) / ((yj - yi) or 1e-15) + xi
        ):
            inside = not inside

        j = i

    return inside


def point_in_polygon(lon, lat, polygon):
    if not polygon:
        return False

    if not point_in_ring((lon, lat), polygon[0]):
        return False

    for hole in polygon[1:]:
        if point_in_ring((lon, lat), hole):
            return False

    return True


def point_in_geometry(lon, lat, geometry):
    if not geometry:
        return False

    geometry_type = geometry.get("type")
    coordinates = geometry.get("coordinates")

    if not coordinates:
        return False

    if geometry_type == "Polygon":
        return point_in_polygon(lon, lat, coordinates)

    if geometry_type == "MultiPolygon":
        for polygon in coordinates:
            if point_in_polygon(lon, lat, polygon):
                return True

    return False


def load_fars_geometry():
    if not os.path.exists(BOUNDARY_FILE):
        raise FileNotFoundError(f"{BOUNDARY_FILE} پیدا نشد.")

    with open(BOUNDARY_FILE, "r", encoding="utf-8") as file:
        data = json.load(file)

    if data.get("type") != "FeatureCollection":
        raise ValueError("GeoJSON معتبر نیست.")

    for feature in data.get("features", []):
        properties = feature.get("properties", {})
        name = str(properties.get("shapeName", "")).strip().lower()

        if name == "fars":
            geometry = feature.get("geometry")

            if not geometry:
                raise ValueError("Geometry فارس پیدا نشد.")

            print("مرز استان فارس با موفقیت پیدا شد.")
            return geometry

    raise ValueError("استان فارس در GeoJSON پیدا نشد.")


def get_fire_data(sensor, days=5):
    url = (
        "https://firms.modaps.eosdis.nasa.gov/"
        "api/area/csv/"
        f"{API_KEY}/"
        f"{sensor}/"
        f"{AREA}/"
        f"{days}"
    )

    print(f"دریافت داده از {sensor}...")

    response = requests.get(url, timeout=120)
    response.raise_for_status()

    text = response.text.strip()

    if not text:
        print(f"{sensor}: داده‌ای دریافت نشد.")
        return []

    reader = csv.DictReader(io.StringIO(text))
    records = list(reader)

    print(f"{sensor}: {len(records)} رکورد دریافت شد.")

    return records


def detect_sensor(row):
    instrument = str(row.get("instrument", "")).strip().upper()
    satellite = str(row.get("satellite", "")).strip().upper()

    if "VIIRS" in instrument or "VIIRS" in satellite:
        return "VIIRS"

    if "MODIS" in instrument or "MODIS" in satellite:
        return "MODIS"

    return "UNKNOWN"


def process_record(row, fars_geometry):
    try:
        lat = float(row["latitude"])
        lon = float(row["longitude"])
    except (KeyError, ValueError, TypeError):
        return None

    if not (WEST <= lon <= EAST and SOUTH <= lat <= NORTH):
        return None

    if not point_in_geometry(lon, lat, fars_geometry):
        return None

    acq_date = str(row.get("acq_date", "")).strip()
    acq_time = str(row.get("acq_time", "")).strip()

    year = ""
    month = ""
    day = ""

    try:
        date_value = datetime.strptime(acq_date, "%Y-%m-%d")
        year = date_value.year
        month = date_value.month
        day = date_value.day
    except ValueError:
        pass

    brightness = row.get("bright_ti4", "")
    if not brightness:
        brightness = row.get("brightness", "")

    return {
        "latitude": lat,
        "longitude": lon,
        "acq_date": acq_date,
        "acq_time": acq_time,
        "year": year,
        "month": month,
        "day": day,
        "satellite": str(row.get("satellite", "")).strip(),
        "instrument": str(row.get("instrument", "")).strip(),
        "detected_sensor": detect_sensor(row),
        "confidence": str(row.get("confidence", "")).strip(),
        "frp": str(row.get("frp", "")).strip(),
        "brightness": str(brightness).strip(),
        "daynight": str(row.get("daynight", "")).strip(),
        "scan": str(row.get("scan", "")).strip(),
        "track": str(row.get("track", "")).strip()
    }


def remove_duplicates(records):
    unique = []
    seen = set()

    for row in records:
        try:
            key = (
                round(float(row["latitude"]), 5),
                round(float(row["longitude"]), 5),
                row.get("acq_date", ""),
                row.get("acq_time", ""),
                row.get("satellite", ""),
                row.get("instrument", "")
            )
        except Exception:
            continue

        if key in seen:
            continue

        seen.add(key)
        unique.append(row)

    return unique


def sort_records(records):
    return sorted(
        records,
        key=lambda row: (
            row.get("acq_date", ""),
            row.get("acq_time", "")
        ),
        reverse=True
    )


def save_csv(filename, records):
    folder = os.path.dirname(filename)

    if folder:
        os.makedirs(folder, exist_ok=True)

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
        writer.writerows(records)


def load_existing_csv(filename):
    if not os.path.exists(filename):
        return []

    try:
        with open(
            filename,
            "r",
            newline="",
            encoding="utf-8-sig"
        ) as file:

            reader = csv.DictReader(file)
            return [row for row in reader if row]

    except Exception as error:
        print(f"خطا در خواندن {filename}: {error}")
        return []


def get_archive_dates():
    today = datetime.now(timezone.utc).date()

    dates = []

    for i in range(DAYS_TO_KEEP):
        current = today - timedelta(days=i)
        dates.append(current.strftime("%Y-%m-%d"))

    return dates


def save_daily_archives(all_fires, archive_dates):
    os.makedirs(ARCHIVE_DIR, exist_ok=True)

    for date in archive_dates:
        daily = []

        for row in all_fires:
            if row.get("acq_date", "") == date:
                daily.append(row)

        daily = remove_duplicates(daily)
        daily = sort_records(daily)

        filename = os.path.join(
            ARCHIVE_DIR,
            f"{date}.csv"
        )

        if daily:
            save_csv(filename, daily)

            print(
                f"آرشیو {date}: "
                f"{len(daily)} رکورد"
            )

        elif os.path.exists(filename):
            print(
                f"آرشیو {date}: داده جدید ندارد؛ "
                f"نسخه قبلی حفظ شد."
            )

        else:
            save_csv(filename, [])

            print(
                f"آرشیو {date}: بدون داده."
            )


def clean_old_archives(archive_dates):
    if not os.path.exists(ARCHIVE_DIR):
        return

    valid_files = set()

    for date in archive_dates:
        valid_files.add(f"{date}.csv")

    for filename in os.listdir(ARCHIVE_DIR):
        if not filename.endswith(".csv"):
            continue

        if filename not in valid_files:
            path = os.path.join(
                ARCHIVE_DIR,
                filename
            )

            os.remove(path)

            print(
                f"حذف آرشیو قدیمی: {filename}"
            )


def build_archive_index(archive_dates):
    os.makedirs(ARCHIVE_DIR, exist_ok=True)

    index = []

    for date in archive_dates:
        filename = os.path.join(
            ARCHIVE_DIR,
            f"{date}.csv"
        )

        count = 0

        if os.path.exists(filename):
            try:
                with open(
                    filename,
                    "r",
                    encoding="utf-8-sig"
                ) as file:

                    reader = csv.DictReader(file)

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

    print("archive/index.json ساخته شد.")


def update_main_csv(all_fires, current_date):
    today_fires = []

    for row in all_fires:
        if row.get("acq_date", "") == current_date:
            today_fires.append(row)

    today_fires = remove_duplicates(today_fires)
    today_fires = sort_records(today_fires)

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

    existing = load_existing_csv(
        OUTPUT_FILE
    )

    if existing:
        print(
            "امروز داده جدید وجود ندارد؛ "
            "fires.csv قبلی حفظ شد."
        )

        return existing

    save_csv(
        OUTPUT_FILE,
        []
    )

    print(
        "fires.csv خالی ساخته شد."
    )

    return []


def save_update_info():
    now_utc = datetime.now(timezone.utc)

    info = {
        "updated_at_utc": now_utc.isoformat(),
        "updated_at_timestamp": now_utc.timestamp()
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
        "زمان آخرین به‌روزرسانی ذخیره شد."
    )

    print(
        now_utc.isoformat()
    )


def main():
    print("=" * 60)
    print("شروع پایش حریق استان فارس")
    print("=" * 60)

    fars_geometry = load_fars_geometry()

    archive_dates = get_archive_dates()
    current_date = archive_dates[0]

    all_fires = []
    successful_sources = 0

    for sensor in SENSORS:

        try:
            raw_records = get_fire_data(
                sensor,
                DAYS_TO_KEEP
            )

            successful_sources += 1

            for row in raw_records:

                processed = process_record(
                    row,
                    fars_geometry
                )

                if processed:
                    all_fires.append(processed)

        except Exception as error:

            print(
                f"خطا در {sensor}: {error}"
            )

    all_fires = remove_duplicates(
        all_fires
    )

    all_fires = sort_records(
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

    final_records = update_main_csv(
        all_fires,
        current_date
    )

    if successful_sources > 0:
        save_update_info()

    else:
        print(
            "هیچ منبع FIRMS پاسخ نداد."
        )

    viirs_count = 0
    modis_count = 0

    for row in final_records:

        if row.get("detected_sensor") == "VIIRS":
            viirs_count += 1

        elif row.get("detected_sensor") == "MODIS":
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


if __name__ == "__main__":
    main()
