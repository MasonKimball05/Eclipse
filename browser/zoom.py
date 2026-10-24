# Page zoom that remembers each site: zoom GitHub to 125% once and it opens at 125% from then on,
# in any tab, while other sites stay at 100%.

from PyQt6.QtCore import QUrl

ZOOM_STEPS = [0.5, 0.67, 0.75, 0.8, 0.9, 1.0, 1.1, 1.25, 1.5, 1.75, 2.0, 2.5, 3.0]
SETTING = "site_zoom"


def zoom_key(url) -> str | None:
    """The site a zoom level belongs to ("github.com"); None for pages that aren't websites."""
    url = QUrl(url) if isinstance(url, str) else url
    if url.scheme() not in ("http", "https"):
        return None
    host = url.host().lower().removeprefix("www.")
    return f"{host}:{url.port()}" if url.port() != -1 else host or None


def zoom_for(settings: dict, url) -> float:
    key = zoom_key(url)
    return settings.get(SETTING, {}).get(key, 1.0) if key else 1.0


def remember_zoom(settings: dict, url, factor: float):
    key = zoom_key(url)
    if not key:
        return
    zooms = settings.setdefault(SETTING, {})
    if abs(factor - 1.0) < 0.01:
        zooms.pop(key, None)  # 100% is the default: nothing to remember
    else:
        zooms[key] = round(factor, 2)


def step_zoom(current: float, direction: int) -> float | None:
    """The next zoom level up (1) or down (-1), 100% for 0, or None at either end."""
    if direction == 0:
        return 1.0
    steps = [z for z in ZOOM_STEPS if (z > current + 0.01 if direction > 0 else z < current - 0.01)]
    if not steps:
        return None
    return steps[0] if direction > 0 else steps[-1]
