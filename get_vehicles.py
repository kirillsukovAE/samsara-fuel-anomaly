import csv
import os
import requests

API_TOKEN = os.environ.get("SAMSARA_API_TOKEN")
BASE_URL = "https://api.samsara.com"
OUTPUT_FILE = "vehicles.csv"
COLUMNS = ["id", "name", "licensePlate", "make", "model", "year"]


def get_all_vehicles():
    vehicles = []
    after = None

    while True:
        params = {"limit": 512}
        if after:
            params["after"] = after

        response = requests.get(
            f"{BASE_URL}/fleet/vehicles",
            headers={"Authorization": f"Bearer {API_TOKEN}"},
            params=params,
        )
        response.raise_for_status()
        body = response.json()

        vehicles.extend(body.get("data", []))

        pagination = body.get("pagination", {})
        if not pagination.get("hasNextPage"):
            break
        after = pagination.get("endCursor")

    return vehicles


def save_to_csv(vehicles, filepath):
    with open(filepath, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS, extrasaction="ignore")
        writer.writeheader()
        for vehicle in vehicles:
            writer.writerow({col: vehicle.get(col, "") for col in COLUMNS})


def main():
    if not API_TOKEN:
        raise EnvironmentError("SAMSARA_API_TOKEN environment variable is not set.")

    print("Fetching vehicles...")
    vehicles = get_all_vehicles()
    save_to_csv(vehicles, OUTPUT_FILE)
    print(f"Saved {len(vehicles)} vehicles to {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
