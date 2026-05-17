import json
import logging
from time import sleep
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from app.config import settings

logger = logging.getLogger(__name__)

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"


def geocode_location(location: str | None, *, countrycodes: str = "ca") -> tuple[float, float] | None:
    """Best-effort geocoding through Nominatim.

    Returns (latitude, longitude), or None when the location is missing,
    ambiguous, or the public service is unavailable.
    """
    query = (location or "").strip()
    if not query:
        return None
    params = urlencode({
        "format": "jsonv2",
        "limit": "1",
        "q": query,
        "countrycodes": countrycodes,
    })
    req = Request(
        f"{NOMINATIM_URL}?{params}",
        headers={
            "Accept": "application/json",
            "User-Agent": f"Relinqo/1.0 ({settings.public_base_url})",
        },
    )
    try:
        with urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception:
        logger.exception("Geocode failed for location=%r", query)
        return None

    if not data:
        return None
    try:
        return float(data[0]["lat"]), float(data[0]["lon"])
    except (KeyError, TypeError, ValueError):
        logger.warning("Geocode returned malformed result for location=%r", query)
        return None


def polite_geocode_delay() -> None:
    """Nominatim asks bulk clients to keep requests modest."""
    sleep(1)
