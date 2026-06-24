"""
entrypoint.py — Samsara Function handler for fuel anomaly detection.

Schedule: nightly (configured via Functions API)
Trigger:  schedule | manual | api

What it does
------------
1. Fetch fuelConsumedMilliliters + GPS for the past `lookback_days` days
2. Load per-vehicle baselines from persistent storage DB
3. Run anomaly detection (high consumption + sudden drops)
4. Save updated baselines back to DB
5. Write anomalies.geojson to persistent storage (S3)
6. Generate a presigned URL for the GeoJSON and log the "View Map" link
7. POST a webhook notification (optional) containing vehicle summaries
   and the map URL — this is the link users click to open the map

Secrets (configure via Functions API dashboard)
-----------------------------------------------
    SAMSARA_API_TOKEN   — Samsara API token with Read Vehicles + Read Vehicle Statistics
    WEBHOOK_URL         — (optional) webhook/Slack/Teams URL for notifications
    MAP_APP_BASE_URL    — base URL where map.html is hosted
                          e.g. https://your-bucket.s3.amazonaws.com/map.html
                          The function appends ?data=<presigned_geojson_url>

Params (configure via Functions API dashboard)
----------------------------------------------
    lookback_days       — how many days of history to fetch (default: 2)
    z_threshold         — z-score cutoff for anomaly flagging (default: 2.5)
    geojson_key         — S3 key for the output GeoJSON (default: anomalies/latest.geojson)
    geojson_expiry_secs — presigned URL expiry in seconds (default: 86400 = 24 h)
"""

import json
import urllib.request

import samsarafnlogs as log_module
import samsarafnsecrets as secrets_module
import samsarafnstorage as storage_module

from fetcher  import get_fuel_readings
from detector import detect_anomalies, to_geojson

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
BASELINE_DB_NAMESPACE = "fuel-anomaly-baselines"
GEOJSON_DEFAULT_KEY   = "anomalies/latest.geojson"


# ---------------------------------------------------------------------------
# Main handler
# ---------------------------------------------------------------------------
def main(event: dict, _):
    log_module.setup_logger_once(event)
    corr_id = event["SamsaraFunctionCorrelationId"]
    log_module.log(corr_id, "fuel-anomaly-detector started", {"trigger": event.get("SamsaraFunctionTriggerSource")})

    # -- params --------------------------------------------------------------
    lookback_days      = int(event.get("lookback_days",      "2"))
    z_threshold        = float(event.get("z_threshold",      "2.5"))
    geojson_key        = event.get("geojson_key",            GEOJSON_DEFAULT_KEY)
    geojson_expiry     = int(event.get("geojson_expiry_secs","86400"))

    log_module.log(corr_id, "params", {
        "lookback_days": lookback_days,
        "z_threshold":   z_threshold,
        "geojson_key":   geojson_key,
    })

    # -- secrets -------------------------------------------------------------
    secrets         = secrets_module.get_secrets()
    samsara_token   = secrets["SAMSARA_API_TOKEN"]
    webhook_url     = secrets.get("WEBHOOK_URL")
    map_base_url    = secrets.get("MAP_APP_BASE_URL", "")

    # -- fetch ---------------------------------------------------------------
    log_module.log(corr_id, f"fetching fuel readings for past {lookback_days} days")
    readings = get_fuel_readings(samsara_token, lookback_days)
    log_module.log(corr_id, f"fetched {len(readings)} readings")

    if not readings:
        log_module.log(corr_id, "no readings returned — nothing to do", level="WARN")
        return

    # -- load baselines ------------------------------------------------------
    db        = storage_module.get_database(BASELINE_DB_NAMESPACE)
    raw       = db.get_dict("baselines") or {}
    baselines = {k: v for k, v in raw.items()}
    log_module.log(corr_id, f"loaded baselines for {len(baselines)} vehicles")

    # -- detect --------------------------------------------------------------
    anomalies, updated_baselines = detect_anomalies(readings, baselines, z_threshold)
    log_module.log(corr_id, f"detected {len(anomalies)} anomalies")

    # -- save baselines ------------------------------------------------------
    db.put_dict("baselines", updated_baselines)
    log_module.log(corr_id, "baselines saved")

    # -- write GeoJSON -------------------------------------------------------
    storage     = storage_module.get_storage()
    geojson     = to_geojson(anomalies)
    geojson_bytes = json.dumps(geojson, indent=2).encode("utf-8")

    storage.put(
        Key=geojson_key,
        Body=geojson_bytes,
        ContentType="application/geo+json",
    )
    log_module.log(corr_id, f"GeoJSON written to storage key: {geojson_key}")

    # -- presigned URL -------------------------------------------------------
    presigned_url = storage.generate_presigned_url(geojson_key, expiry_seconds=geojson_expiry)

    import urllib.parse
    map_url = f"{map_base_url}?data={urllib.parse.quote(presigned_url, safe='')}" if map_base_url else presigned_url

    log_module.log(corr_id, "=== VIEW ANOMALY MAP ===")
    log_module.log(corr_id, map_url)
    log_module.log(corr_id, "========================")

    # -- webhook notification ------------------------------------------------
    if webhook_url and anomalies:
        _send_notification(webhook_url, anomalies, map_url, corr_id)

    log_module.log(corr_id, "fuel-anomaly-detector completed", {
        "readings":  len(readings),
        "anomalies": len(anomalies),
        "map_url":   map_url,
    })


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _send_notification(webhook_url: str, anomalies: list[dict], map_url: str, corr_id: str):
    """Send a summary notification to a webhook (Slack/Teams/custom)."""
    # Group by vehicle
    by_vehicle: dict[str, list] = {}
    for a in anomalies:
        by_vehicle.setdefault(a["vehicle_name"], []).append(a)

    lines = [f"*Fuel Anomaly Report* — {len(anomalies)} anomaly point(s) across {len(by_vehicle)} vehicle(s)\n"]
    for vname, vanomalies in sorted(by_vehicle.items()):
        critical = sum(1 for a in vanomalies if a["severity"] == "critical")
        warnings = sum(1 for a in vanomalies if a["severity"] == "warning")
        drops    = sum(1 for a in vanomalies if a["reason"] == "sudden_drop")
        lines.append(
            f"• *{vname}*: {len(vanomalies)} event(s)"
            + (f" — {critical} critical" if critical else "")
            + (f" — {warnings} warning(s)" if warnings else "")
            + (f" — {drops} sudden drop(s)" if drops else "")
        )

    lines.append(f"\n<{map_url}|View Anomaly Map>")

    payload = {"text": "\n".join(lines)}

    try:
        req  = urllib.request.Request(
            webhook_url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            log_module.log(corr_id, f"webhook sent, status={resp.status}")
    except Exception as exc:
        log_module.log(corr_id, f"webhook failed: {exc}", level="WARN")
