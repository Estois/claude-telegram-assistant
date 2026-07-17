"""One-off / periodic script: build bus_stops_cache.json from LTA DataMall's
static BusStops dataset, used for nearest-stop lookups when /bus is given a
live location.

Usage:
    python fetch_bus_stops.py

Requires LTA_API_KEY in .env — sign up for free at
https://datamall.lta.gov.sg/content/datamall/en/request-for-api.html
"""

import json
import os
from pathlib import Path

import httpx
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent
OUTPUT_FILE = BASE_DIR / "bus_stops_cache.json"

BUS_STOPS_URL = "https://datamall2.mytransport.sg/ltaodataservice/BusStops"
PAGE_SIZE = 500


def fetch_bus_stops(api_key: str) -> list:
    headers = {"AccountKey": api_key, "accept": "application/json"}
    stops = []
    skip = 0

    with httpx.Client(headers=headers, timeout=15) as client:
        while True:
            resp = client.get(BUS_STOPS_URL, params={"$skip": skip})
            resp.raise_for_status()
            page = resp.json().get("value", [])
            if not page:
                break

            stops.extend(page)
            print(f"  fetched {len(stops)} bus stops so far...")
            if len(page) < PAGE_SIZE:
                break
            skip += PAGE_SIZE

    return stops


def main() -> None:
    api_key = os.environ.get("LTA_API_KEY")
    if not api_key:
        raise RuntimeError("LTA_API_KEY is not set. Add it to .env first.")

    stops = fetch_bus_stops(api_key)
    OUTPUT_FILE.write_text(json.dumps(stops, indent=2))
    print(f"Saved {len(stops)} bus stops to {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
