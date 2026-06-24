"""
detector.py — Fuel anomaly detection logic.

Algorithm
---------
fuelConsumedMilliliters is a cumulative, always-increasing counter.
We convert it to per-interval consumption by computing successive deltas,
then derive fuel efficiency as ml/km using GPS speed to estimate distance.

Baseline is stored in persistent storage (DB) as a per-vehicle rolling
summary: { "mean": float, "std": float, "n": int }.

A reading is flagged as an anomaly when its efficiency exceeds
  mean + z_threshold * std
(i.e. unusually HIGH fuel consumption for the distance covered).

We also flag SUDDEN DROPS (negative deltas) which indicate a possible
fuel theft or sensor reset.
"""

import math
from typing import TypedDict


class Baseline(TypedDict):
    mean: float
    std: float
    n: int


class AnomalyPoint(TypedDict):
    vehicle_id: str
    vehicle_name: str
    time: str
    lat: float
    lng: float
    speed_mph: float
    address: str
    fuel_delta_ml: float
    efficiency_ml_per_km: float
    baseline_mean: float
    baseline_std: float
    z_score: float
    reason: str          # "high_consumption" | "sudden_drop"
    severity: str        # "warning" | "critical"


# Minimum distance between readings to compute efficiency (avoids division by
# near-zero distances when the vehicle is stationary).
_MIN_DISTANCE_KM = 0.05   # ~50 metres


def _estimate_distance_km(r1: dict, r2: dict) -> float:
    """Haversine distance between two GPS points."""
    lat1, lon1 = r1.get("lat"), r1.get("lng")
    lat2, lon2 = r2.get("lat"), r2.get("lng")
    if None in (lat1, lon1, lat2, lon2):
        return 0.0

    R = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi  = math.radians(lat2 - lat1)
    dlam  = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _update_baseline(baseline: Baseline, value: float) -> Baseline:
    """Welford online mean/variance update."""
    n    = baseline["n"] + 1
    mean = baseline["mean"]
    std  = baseline["std"]

    delta  = value - mean
    mean  += delta / n
    delta2 = value - mean
    # track M2 (variance * n) via std field temporarily as M2
    M2 = (std ** 2) * (baseline["n"]) + delta * delta2
    std = math.sqrt(M2 / n) if n > 1 else 0.0

    return {"mean": mean, "std": std, "n": n}


def detect_anomalies(
    readings: list[dict],
    baselines: dict[str, Baseline],
    z_threshold: float = 2.5,
) -> tuple[list[AnomalyPoint], dict[str, Baseline]]:
    """
    Process fuel readings for all vehicles.

    Args:
        readings:     flat list from fetcher.get_fuel_readings()
        baselines:    dict keyed by vehicle_id, loaded from persistent storage
        z_threshold:  standard deviations above mean to flag as anomaly

    Returns:
        (anomalies, updated_baselines)
    """
    # Group readings by vehicle and sort chronologically
    by_vehicle: dict[str, list[dict]] = {}
    for r in readings:
        by_vehicle.setdefault(r["vehicle_id"], []).append(r)

    for vid in by_vehicle:
        by_vehicle[vid].sort(key=lambda r: r["time"])

    anomalies: list[AnomalyPoint] = []
    updated_baselines = dict(baselines)

    for vid, vehicle_readings in by_vehicle.items():
        baseline = updated_baselines.get(vid, {"mean": 0.0, "std": 0.0, "n": 0})

        for i in range(1, len(vehicle_readings)):
            prev = vehicle_readings[i - 1]
            curr = vehicle_readings[i]

            fuel_delta = curr["fuel_ml"] - prev["fuel_ml"]

            # Sudden drop — likely fuel theft or sensor anomaly
            if fuel_delta < -500:
                anomalies.append({
                    "vehicle_id":            vid,
                    "vehicle_name":          curr["vehicle_name"],
                    "time":                  curr["time"],
                    "lat":                   curr.get("lat") or 0.0,
                    "lng":                   curr.get("lng") or 0.0,
                    "speed_mph":             curr.get("speed_mph") or 0.0,
                    "address":               curr.get("address", ""),
                    "fuel_delta_ml":         fuel_delta,
                    "efficiency_ml_per_km":  0.0,
                    "baseline_mean":         baseline["mean"],
                    "baseline_std":          baseline["std"],
                    "z_score":               0.0,
                    "reason":                "sudden_drop",
                    "severity":              "critical",
                })
                continue

            # Skip negative or zero deltas (engine off, same reading repeated)
            if fuel_delta <= 0:
                continue

            distance_km = _estimate_distance_km(prev, curr)

            if distance_km < _MIN_DISTANCE_KM:
                # Vehicle stationary — update baseline with raw ml delta but
                # don't compute efficiency (would be infinite)
                baseline = _update_baseline(baseline, fuel_delta)
                continue

            efficiency = fuel_delta / distance_km  # ml per km

            # Update baseline before scoring so the first few readings build it
            if baseline["n"] >= 10:
                # Enough history — check for anomaly first, then update
                std = baseline["std"] if baseline["std"] > 0 else 1.0
                z   = (efficiency - baseline["mean"]) / std

                if z > z_threshold:
                    severity = "critical" if z > z_threshold * 1.5 else "warning"
                    anomalies.append({
                        "vehicle_id":            vid,
                        "vehicle_name":          curr["vehicle_name"],
                        "time":                  curr["time"],
                        "lat":                   curr.get("lat") or 0.0,
                        "lng":                   curr.get("lng") or 0.0,
                        "speed_mph":             curr.get("speed_mph") or 0.0,
                        "address":               curr.get("address", ""),
                        "fuel_delta_ml":         fuel_delta,
                        "efficiency_ml_per_km":  round(efficiency, 2),
                        "baseline_mean":         round(baseline["mean"], 2),
                        "baseline_std":          round(baseline["std"], 2),
                        "z_score":               round(z, 2),
                        "reason":                "high_consumption",
                        "severity":              severity,
                    })

            baseline = _update_baseline(baseline, efficiency)

        updated_baselines[vid] = baseline

    return anomalies, updated_baselines


def to_geojson(anomalies: list[AnomalyPoint]) -> dict:
    """Convert anomaly list to a GeoJSON FeatureCollection."""
    features = []
    for a in anomalies:
        if a["lat"] == 0.0 and a["lng"] == 0.0:
            continue
        features.append({
            "type": "Feature",
            "geometry": {
                "type": "Point",
                "coordinates": [a["lng"], a["lat"]],
            },
            "properties": {k: v for k, v in a.items() if k not in ("lat", "lng")},
        })
    return {"type": "FeatureCollection", "features": features}
