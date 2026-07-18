"""Singapore LTA bus domain: arrivals API, stop cache, saved locations."""

import json
import math
from datetime import datetime
from pathlib import Path
from typing import Optional

import httpx

from ykbot._logging import logger


class LtaBusClient:
    """Wraps Singapore LTA DataMall's real-time bus arrival API."""

    BASE_URL = "https://datamall2.mytransport.sg/ltaodataservice"

    def __init__(self, api_key: Optional[str]):
        self._api_key = api_key

    @property
    def is_configured(self) -> bool:
        return bool(self._api_key)

    async def get_arrivals(self, bus_stop_code: str) -> list:
        """Returns services sorted by number, each as
        {"service_no": "21", "arrivals": ["2 min", "Arr", "15 min"]}. Callers
        are expected to check is_configured first (as _format_arrivals
        does); this just turns a misuse into a clear error instead of
        silently sending a broken AccountKey header."""
        api_key = self._api_key
        if api_key is None:
            raise RuntimeError("LTA_API_KEY is not configured")

        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                f"{self.BASE_URL}/v3/BusArrival",
                params={"BusStopCode": bus_stop_code},
                headers={"AccountKey": api_key, "accept": "application/json"},
            )
            resp.raise_for_status()
            data = resp.json()

        return self._parse_services(data)

    @classmethod
    def _parse_services(cls, data: dict) -> list:
        services = []
        for svc in data.get("Services", []):
            arrivals = [
                cls._format_eta(svc.get(key, {}).get("EstimatedArrival"))
                for key in ("NextBus", "NextBus2", "NextBus3")
            ]
            services.append({"service_no": svc["ServiceNo"], "arrivals": arrivals})

        services.sort(key=lambda s: s["service_no"])
        return services

    @staticmethod
    def _format_eta(eta_iso: Optional[str]) -> str:
        if not eta_iso:
            return "—"
        arrival = datetime.fromisoformat(eta_iso)
        minutes = round((arrival - datetime.now(arrival.tzinfo)).total_seconds() / 60)
        return "Arr" if minutes <= 0 else f"{minutes} min"



class BusStopsCache:
    """Loads the static LTA bus-stops dataset (built by fetch_bus_stops.py)
    for nearest-stop lookups from a live location."""

    def __init__(self, cache_file: Path):
        self._cache_file = cache_file

    @property
    def is_available(self) -> bool:
        return self._cache_file.exists()

    def nearest(self, lat: float, lon: float, count: int) -> list:
        stops = self._load()
        for stop in stops:
            stop["distance_m"] = self._haversine_m(
                lat, lon, stop["Latitude"], stop["Longitude"]
            )
        stops.sort(key=lambda s: s["distance_m"])
        return stops[:count]

    def find_by_code(self, code: str) -> Optional[dict]:
        return next((s for s in self._load() if s["BusStopCode"] == code), None)

    def _load(self) -> list:
        if not self._cache_file.exists():
            return []
        try:
            return json.loads(self._cache_file.read_text())
        except json.JSONDecodeError:
            logger.exception("%s is corrupt", self._cache_file)
            return []

    @staticmethod
    def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
        earth_radius_m = 6371000
        phi1, phi2 = math.radians(lat1), math.radians(lat2)
        dphi = math.radians(lat2 - lat1)
        dlambda = math.radians(lon2 - lon1)
        a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
        return 2 * earth_radius_m * math.asin(math.sqrt(a))



class BusLocationsStore:
    """Manages the name -> bus stop code mapping shown as /bus's preset
    location buttons. Hand-editable, and also written to directly when a
    stop is added/removed as preferred from the bot."""

    def __init__(self, path: Path):
        self._path = path

    def list(self) -> dict:
        if not self._path.exists():
            return {}
        try:
            return json.loads(self._path.read_text())
        except json.JSONDecodeError:
            logger.exception("%s is corrupt", self._path)
            return {}

    def has_code(self, code: str) -> bool:
        return code in self.list().values()

    def add(self, name: str, code: str) -> None:
        locations = self.list()
        locations[name] = code
        self._write(locations)

    def remove_by_code(self, code: str) -> bool:
        """Removes every entry pointing at this stop code. Returns whether
        anything was removed."""
        locations = self.list()
        names = [name for name, stop_code in locations.items() if stop_code == code]
        if not names:
            return False
        for name in names:
            del locations[name]
        self._write(locations)
        return True

    def _write(self, locations: dict) -> None:
        self._path.write_text(json.dumps(locations, indent=2))

