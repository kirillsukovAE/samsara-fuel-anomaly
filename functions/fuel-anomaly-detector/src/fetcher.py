"""
fetcher.py — Samsara API data retrieval using the Python SDK.

Fetches fuelConsumedMilliliters + gps decoration for all vehicles
over the requested time window, paginating until complete.

SDK path (samsara-api 4.x):
  client.vehicle_stats.get_vehicle_stats_history(
      start_time, end_time, types, decorations
  ) -> VehicleStatsListResponse
      .data  -> list[VehicleStatsListResponseData]
          .id, .name
          .fuel_consumed_milliliters -> list[VehicleStatsFuelConsumedMillilitersWithDecoration]
              .time  (str)
              .value (int, ml)
              .decorations.gps
                  .latitude, .longitude, .speed_miles_per_hour, .reverse_geo.formatted_location
      .pagination.has_next_page, .pagination.end_cursor
"""

from datetime import datetime, timezone, timedelta
from samsara import Samsara


def get_fuel_readings(token: str, lookback_days: int) -> list[dict]:
    """
    Returns a flat list of fuel readings across all vehicles.

    Each item:
        {
            "vehicle_id":   str,
            "vehicle_name": str,
            "time":         str  (ISO 8601),
            "fuel_ml":      float,
            "lat":          float | None,
            "lng":          float | None,
            "speed_mph":    float | None,
            "address":      str,
        }
    """
    client = Samsara(token=token)

    end_time   = datetime.now(timezone.utc)
    start_time = end_time - timedelta(days=lookback_days)

    start_str = start_time.strftime("%Y-%m-%dT%H:%M:%SZ")
    end_str   = end_time.strftime("%Y-%m-%dT%H:%M:%SZ")

    readings = []
    after    = None

    while True:
        resp = client.vehicle_stats.get_vehicle_stats_history(
            start_time=start_str,
            end_time=end_str,
            types=["fuelConsumedMilliliters"],
            decorations=["gps"],
            after=after,
        )

        for vehicle in resp.data:
            vid   = str(vehicle.id)
            vname = vehicle.name or vid

            fuel_series = vehicle.fuel_consumed_milliliters or []
            for reading in fuel_series:
                lat       = None
                lng       = None
                speed_mph = None
                address   = ""

                dec = reading.decorations
                if dec and dec.gps:
                    g         = dec.gps
                    lat       = float(g.latitude)  if g.latitude  is not None else None
                    lng       = float(g.longitude) if g.longitude is not None else None
                    speed_mph = float(g.speed_miles_per_hour) if g.speed_miles_per_hour is not None else None
                    if g.reverse_geo and g.reverse_geo.formatted_location:
                        address = g.reverse_geo.formatted_location

                readings.append({
                    "vehicle_id":   vid,
                    "vehicle_name": vname,
                    "time":         str(reading.time),
                    "fuel_ml":      float(reading.value),
                    "lat":          lat,
                    "lng":          lng,
                    "speed_mph":    speed_mph,
                    "address":      address,
                })

        if not resp.pagination.has_next_page:
            break
        after = resp.pagination.end_cursor

    return readings
