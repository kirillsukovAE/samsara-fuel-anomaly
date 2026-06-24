"""
fetch_diagnostics.py

Fetches 2 days of vehicle diagnostics for the entire fleet using:
  GET /fleet/vehicles/stats/history
  types: ecuSpeedMph, fuelConsumedMilliliters, engineCoolantTemperatureMilliC
  decorations: gps

Each row in the output CSV represents one stat reading and contains:
  vehicle_id, vehicle_name, stat_type, time,
  value,
  gps_latitude, gps_longitude, gps_heading_degrees, gps_speed_mph,
  gps_address, gps_formatted_location

Output: diagnostics.csv

Usage:
  SAMSARA_API_TOKEN=<token> python3 fetch_diagnostics.py

  Optional env vars:
    START_TIME  RFC 3339 start (default: 48 hours ago)
    END_TIME    RFC 3339 end   (default: now)
"""

import csv
import os
import sys
from datetime import datetime, timezone, timedelta

import requests

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
API_TOKEN = os.environ.get("SAMSARA_API_TOKEN")
BASE_URL = "https://api.samsara.com"
OUTPUT_FILE = "diagnostics.csv"

STAT_TYPES = [
    "ecuSpeedMph",
    "fuelConsumedMilliliters",
    "engineCoolantTemperatureMilliC",
]

CSV_COLUMNS = [
    "vehicle_id",
    "vehicle_name",
    "stat_type",
    "time",
    "value",
    "gps_latitude",
    "gps_longitude",
    "gps_heading_degrees",
    "gps_speed_mph",
    "gps_address",
    "gps_formatted_location",
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def default_window():
    now = datetime.now(timezone.utc)
    start = now - timedelta(days=2)
    return (
        start.strftime("%Y-%m-%dT%H:%M:%SZ"),
        now.strftime("%Y-%m-%dT%H:%M:%SZ"),
    )


def get_headers():
    return {"Authorization": f"Bearer {API_TOKEN}"}


def fetch_stats_history(start_time, end_time):
    """
    Paginate through /fleet/vehicles/stats/history for all vehicles.
    Yields each page's data list.
    """
    params = {
        "startTime": start_time,
        "endTime": end_time,
        "types": ",".join(STAT_TYPES),
        "decorations": "gps",
    }
    after = None
    page = 0

    while True:
        if after:
            params["after"] = after

        response = requests.get(
            f"{BASE_URL}/fleet/vehicles/stats/history",
            headers=get_headers(),
            params=params,
        )
        response.raise_for_status()
        body = response.json()

        data = body.get("data", [])
        page += 1
        print(f"  Page {page}: {len(data)} vehicle(s) returned", flush=True)
        yield data

        pagination = body.get("pagination", {})
        if not pagination.get("hasNextPage"):
            break
        after = pagination.get("endCursor")


def extract_gps(decorations):
    """Pull GPS fields out of a reading's decorations dict."""
    gps = (decorations or {}).get("gps") or {}
    address_obj = gps.get("address") or {}
    reverse_geo = gps.get("reverseGeo") or {}
    return {
        "gps_latitude": gps.get("latitude", ""),
        "gps_longitude": gps.get("longitude", ""),
        "gps_heading_degrees": gps.get("headingDegrees", ""),
        "gps_speed_mph": gps.get("speedMilesPerHour", ""),
        "gps_address": address_obj.get("name", ""),
        "gps_formatted_location": reverse_geo.get("formattedLocation", ""),
    }


def flatten_vehicle(vehicle):
    """
    Convert one vehicle block into a flat list of row dicts —
    one row per stat reading across all stat types.
    """
    vid = vehicle.get("id", "")
    vname = vehicle.get("name", "")
    rows = []

    for stat_type in STAT_TYPES:
        readings = vehicle.get(stat_type)
        if not readings:
            continue
        for reading in readings:
            gps_fields = extract_gps(reading.get("decorations"))
            rows.append({
                "vehicle_id": vid,
                "vehicle_name": vname,
                "stat_type": stat_type,
                "time": reading.get("time", ""),
                "value": reading.get("value", ""),
                **gps_fields,
            })

    return rows


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    if not API_TOKEN:
        print("ERROR: SAMSARA_API_TOKEN environment variable is not set.")
        sys.exit(1)

    start_time = os.environ.get("START_TIME")
    end_time = os.environ.get("END_TIME")
    if not start_time or not end_time:
        start_time, end_time = default_window()

    print(f"Fetching diagnostics from {start_time} to {end_time}")
    print(f"Stat types : {', '.join(STAT_TYPES)}")
    print(f"Decorations: gps")
    print(f"Output     : {OUTPUT_FILE}")
    print()

    total_rows = 0
    vehicles_seen = set()

    with open(OUTPUT_FILE, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        writer.writeheader()

        for page_data in fetch_stats_history(start_time, end_time):
            for vehicle in page_data:
                vehicles_seen.add(vehicle.get("id"))
                rows = flatten_vehicle(vehicle)
                writer.writerows(rows)
                total_rows += len(rows)

    print()
    print(f"Done.")
    print(f"  Vehicles with data : {len(vehicles_seen)}")
    print(f"  Total rows written : {total_rows}")
    print(f"  Output file        : {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
