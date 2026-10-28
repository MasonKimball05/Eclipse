# Instant answers in the command bar, worked out on this Mac (no search, no Claude):
#
#   23 * 4 + 7          → 99            sqrt(2) / 2, 2^10, 15% of 80, (3+4)*2
#   5 km in miles       → 3.107 mi      lengths, weights, volumes, temperatures, speeds, data, time
#   time in tokyo       → 4:12 AM Sat   any city in the time-zone database, or "tokyo time"
#   #7c5cff             → a colour swatch, with its RGB and HSL
#
# Return copies the answer.

import ast
import datetime
import math
import operator
import re
from dataclasses import dataclass
from zoneinfo import ZoneInfo, available_timezones
from .timefmt import portable


@dataclass
class Answer:
    text: str      # what's copied, and shown big
    detail: str    # what it answers ("23 × 4 + 7"), shown smaller
    kind: str      # "math", "unit", "time" or "color"
    color: str = ""  # for "color": the colour itself


# --- Math --------------------------------------------------------------------------------------

_OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
        ast.FloorDiv: operator.floordiv, ast.Mod: operator.mod, ast.Pow: operator.pow}
_FUNCS = {"sqrt": math.sqrt, "sin": math.sin, "cos": math.cos, "tan": math.tan, "log": math.log10,
          "ln": math.log, "abs": abs, "round": round, "floor": math.floor, "ceil": math.ceil, "exp": math.exp}
_NAMES = {"pi": math.pi, "e": math.e, "tau": math.tau}


def _eval(node):
    if isinstance(node, ast.Expression):
        return _eval(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
        left, right = _eval(node.left), _eval(node.right)
        if isinstance(node.op, ast.Pow) and (abs(right) > 1000 or abs(left) > 1e6):
            raise ValueError("too big")
        return _OPS[type(node.op)](left, right)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
        value = _eval(node.operand)
        return value if isinstance(node.op, ast.UAdd) else -value
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in _FUNCS and not node.keywords:
        return _FUNCS[node.func.id](*[_eval(a) for a in node.args])
    if isinstance(node, ast.Name) and node.id in _NAMES:
        return _NAMES[node.id]
    raise ValueError("not arithmetic")


def _number(value) -> str:
    if isinstance(value, float):
        if math.isinf(value) or math.isnan(value):
            raise ValueError("no answer")
        if value.is_integer() and abs(value) < 1e15:
            return f"{int(value):,}"
        if abs(value) >= 1e15 or (0 < abs(value) < 1e-6):
            return f"{value:.6g}"
        return f"{value:,.10f}".rstrip("0").rstrip(".")
    return f"{value:,}"


def calculate(text: str) -> Answer | None:
    q = text.strip().lower().replace("×", "*").replace("÷", "/").replace("^", "**").replace(",", "")
    q = re.sub(r"^\s*=\s*", "", q)
    m = re.fullmatch(r"([\d.]+)\s*%\s*of\s*([\d.]+)", q)  # "15% of 80"
    if m:
        value = float(m.group(1)) / 100 * float(m.group(2))
        return Answer(_number(value), f"{m.group(1)}% of {m.group(2)}", "math")
    if re.fullmatch(r"\d{4}-\d{1,2}-\d{1,2}|(?:\d{3}-)?\d{3}-\d{4}|1-\d{3}-\d{3}-\d{4}", q):
        return None  # a date (2024-10-26) or a phone number (555-1234), not a sum
    q = re.sub(r"(\d+(?:\.\d+)?)\s*%(?!\s*[\d(])", r"(\1/100)", q)  # "20% * 50" (but "10 % 3" is a remainder)
    # Worth answering only if there's arithmetic in it: "2024" or "pi" alone isn't a question
    if not re.search(r"\d", q) or not re.search(r"[-+*/%(]|\b(?:" + "|".join(_FUNCS) + r")\b", q):
        return None
    if re.search(r"[a-z]", re.sub(r"\b(?:" + "|".join(list(_FUNCS) + list(_NAMES)) + r")\b", "", q)):
        return None  # other words: a search, not a sum
    try:
        value = _eval(ast.parse(q, mode="eval"))
    except (SyntaxError, ValueError, TypeError, ZeroDivisionError, OverflowError, RecursionError):
        return None
    pretty = text.strip().replace("**", "^").replace("*", "×").replace("/", "÷")
    try:
        return Answer(_number(value), pretty, "math")
    except ValueError:
        return None


# --- Units ---------------------------------------------------------------------------------------

# unit → (dimension, factor to the base unit). Temperatures are handled on their own.
_UNITS = {}
for names, dim, factor in [
    (("mm", "millimeter", "millimeters", "millimetre", "millimetres"), "length", 0.001),
    (("cm", "centimeter", "centimeters", "centimetre", "centimetres"), "length", 0.01),
    (("m", "meter", "meters", "metre", "metres"), "length", 1.0),
    (("km", "kilometer", "kilometers", "kilometre", "kilometres"), "length", 1000.0),
    (("in", "inch", "inches", '"'), "length", 0.0254),
    (("ft", "foot", "feet", "'"), "length", 0.3048),
    (("yd", "yard", "yards"), "length", 0.9144),
    (("mi", "mile", "miles"), "length", 1609.344),
    (("nmi", "nautical mile", "nautical miles"), "length", 1852.0),
    (("mg", "milligram", "milligrams"), "mass", 1e-6),
    (("g", "gram", "grams"), "mass", 0.001),
    (("kg", "kilo", "kilos", "kilogram", "kilograms"), "mass", 1.0),
    (("t", "tonne", "tonnes", "metric ton"), "mass", 1000.0),
    (("oz", "ounce", "ounces"), "mass", 0.028349523125),
    (("lb", "lbs", "pound", "pounds"), "mass", 0.45359237),
    (("st", "stone", "stones"), "mass", 6.35029318),
    (("ml", "milliliter", "milliliters", "millilitre", "millilitres"), "volume", 0.001),
    (("l", "liter", "liters", "litre", "litres"), "volume", 1.0),
    (("tsp", "teaspoon", "teaspoons"), "volume", 0.00492892159375),
    (("tbsp", "tablespoon", "tablespoons"), "volume", 0.01478676478125),
    (("floz", "fl oz", "fluid ounce", "fluid ounces"), "volume", 0.0295735295625),
    (("cup", "cups"), "volume", 0.2365882365),
    (("pt", "pint", "pints"), "volume", 0.473176473),
    (("qt", "quart", "quarts"), "volume", 0.946352946),
    (("gal", "gallon", "gallons"), "volume", 3.785411784),
    (("m/s", "mps"), "speed", 1.0),
    (("km/h", "kmh", "kph"), "speed", 1 / 3.6),
    (("mph",), "speed", 0.44704),
    (("knot", "knots", "kn"), "speed", 0.514444),
    (("b", "byte", "bytes"), "data", 1.0),
    (("kb", "kilobyte", "kilobytes"), "data", 1e3),
    (("mb", "megabyte", "megabytes"), "data", 1e6),
    (("gb", "gigabyte", "gigabytes"), "data", 1e9),
    (("tb", "terabyte", "terabytes"), "data", 1e12),
    (("kib",), "data", 1024.0), (("mib",), "data", 1024.0 ** 2), (("gib",), "data", 1024.0 ** 3),
    (("ms", "millisecond", "milliseconds"), "time", 0.001),
    (("s", "sec", "secs", "second", "seconds"), "time", 1.0),
    (("min", "mins", "minute", "minutes"), "time", 60.0),
    (("h", "hr", "hrs", "hour", "hours"), "time", 3600.0),
    (("day", "days"), "time", 86400.0),
    (("week", "weeks", "wk"), "time", 604800.0),
    (("year", "years", "yr", "yrs"), "time", 31557600.0),
]:
    for name in names:
        _UNITS[name] = (dim, factor, names[0])

# How each unit is written in an answer (the first name in the table above, unless it's here)
_DISPLAY = {"b": "B", "kb": "KB", "mb": "MB", "gb": "GB", "tb": "TB", "kib": "KiB", "mib": "MiB", "gib": "GiB",
            "cup": "cups", "floz": "fl oz", "knot": "knots", "day": "days", "week": "weeks", "year": "years",
            "l": "L", "ml": "mL"}

_TEMPS = {"c": "°C", "°c": "°C", "celsius": "°C", "f": "°F", "°f": "°F", "fahrenheit": "°F",
          "k": "K", "kelvin": "K"}


def _to_celsius(value, unit):
    return {"°C": value, "°F": (value - 32) * 5 / 9, "K": value - 273.15}[unit]


def _from_celsius(value, unit):
    return {"°C": value, "°F": value * 9 / 5 + 32, "K": value + 273.15}[unit]


def _short(value: float) -> str:
    if value == 0:
        return "0"
    digits = max(0, 4 - int(math.floor(math.log10(abs(value)))) - 1)
    text = f"{value:,.{min(digits, 6)}f}"
    return text.rstrip("0").rstrip(".") if "." in text else text


def convert(text: str) -> Answer | None:
    m = re.fullmatch(r"\s*(-?[\d.,]+)\s*([a-z°/'\" ]+?)\s+(?:in|to|as|into|=)\s+([a-z°/'\" ]+?)\s*", text.lower())
    if not m:
        return None
    try:
        value = float(m.group(1).replace(",", ""))
    except ValueError:
        return None
    src, dst = m.group(2).strip(), m.group(3).strip()
    if src in _TEMPS and dst in _TEMPS:
        a, b = _TEMPS[src], _TEMPS[dst]
        result = _from_celsius(_to_celsius(value, a), b)
        return Answer(f"{_short(result)} {b}", f"{_short(value)} {a}", "unit")
    if src in _UNITS and dst in _UNITS and _UNITS[src][0] == _UNITS[dst][0]:
        (_dim, f1, s1), (_dim2, f2, s2) = _UNITS[src], _UNITS[dst]
        s1, s2 = _DISPLAY.get(s1, s1), _DISPLAY.get(s2, s2)
        return Answer(f"{_short(value * f1 / f2)} {s2}", f"{_short(value)} {s1}", "unit")
    return None


# --- Time zones ----------------------------------------------------------------------------------

_ALIASES = {"nyc": "America/New_York", "new york": "America/New_York", "sf": "America/Los_Angeles",
            "san francisco": "America/Los_Angeles", "la": "America/Los_Angeles", "seattle": "America/Los_Angeles",
            "london": "Europe/London", "uk": "Europe/London", "paris": "Europe/Paris", "berlin": "Europe/Berlin",
            "madrid": "Europe/Madrid", "rome": "Europe/Rome", "beijing": "Asia/Shanghai", "china": "Asia/Shanghai",
            "india": "Asia/Kolkata", "delhi": "Asia/Kolkata", "mumbai": "Asia/Kolkata", "bangalore": "Asia/Kolkata",
            "japan": "Asia/Tokyo", "sydney": "Australia/Sydney", "dallas": "America/Chicago",
            "houston": "America/Chicago", "austin": "America/Chicago", "boston": "America/New_York",
            "miami": "America/New_York", "atlanta": "America/New_York", "dc": "America/New_York",
            "washington": "America/New_York", "toronto": "America/Toronto", "vancouver": "America/Vancouver",
            "mexico city": "America/Mexico_City", "dubai": "Asia/Dubai", "singapore": "Asia/Singapore",
            "hong kong": "Asia/Hong_Kong", "seoul": "Asia/Seoul", "korea": "Asia/Seoul", "denver": "America/Denver",
            "phoenix": "America/Phoenix", "hawaii": "Pacific/Honolulu", "honolulu": "Pacific/Honolulu",
            "utc": "UTC", "gmt": "UTC", "pst": "America/Los_Angeles", "pdt": "America/Los_Angeles",
            "est": "America/New_York", "edt": "America/New_York", "cst": "America/Chicago", "cdt": "America/Chicago",
            "mst": "America/Denver", "cet": "Europe/Paris"}
_zones = None


def find_zone(place: str) -> str | None:
    global _zones
    place = place.strip().lower()
    if place in _ALIASES:
        return _ALIASES[place]
    if _zones is None:
        # (not Etc/GMT+5 and the like: those count backwards, so "gmt+5" would be five hours behind)
        _zones = {z.rsplit("/", 1)[-1].replace("_", " ").lower(): z for z in available_timezones()
                  if "/" in z and not z.startswith("Etc/")}
    return _zones.get(place)


def fixed_zone(place: str) -> datetime.timezone | None:
    """"utc+5", "gmt-3:30": a fixed offset from UTC, the way people write it."""
    m = re.fullmatch(r"(?:utc|gmt)\s*([+-])\s*(\d{1,2})(?::?(\d{2}))?", place.strip().lower())
    if not m:
        return None
    minutes = int(m.group(2)) * 60 + int(m.group(3) or 0)
    if minutes > 14 * 60:
        return None
    return datetime.timezone(datetime.timedelta(minutes=minutes if m.group(1) == "+" else -minutes))


def world_time(text: str, now: datetime.datetime | None = None) -> Answer | None:
    m = (re.fullmatch(r"\s*(?:what\s+)?(?:time\s+(?:is\s+it\s+)?in|time\s+at)\s+(.+?)\s*\??", text.lower())
         or re.fullmatch(r"\s*(.+?)\s+time\s*\??", text.lower()))
    if not m:
        return None
    zone = fixed_zone(m.group(1)) or find_zone(m.group(1))
    if zone is None:
        return None
    now = (now or datetime.datetime.now(datetime.timezone.utc)).astimezone(
        zone if isinstance(zone, datetime.timezone) else ZoneInfo(zone))
    local = datetime.datetime.now().astimezone()
    offset = (now.utcoffset() - local.utcoffset()).total_seconds() / 3600
    diff = "same as here" if offset == 0 else f"{abs(offset):g} hour{'s' if abs(offset) != 1 else ''} " \
                                              f"{'ahead' if offset > 0 else 'behind'}"
    place = m.group(1).strip()
    place = place.upper() if len(place) <= 3 or isinstance(zone, datetime.timezone) else place.title()
    return Answer(now.strftime(portable("%-I:%M %p · %a %b %-d")), f"Time in {place} ({diff})", "time")


# --- Colours ---------------------------------------------------------------------------------------

def color(text: str) -> Answer | None:
    q = text.strip().lower()
    m = re.fullmatch(r"#([0-9a-f]{6}|[0-9a-f]{3})", q)  # the # is required: "facade" is a word, not a colour
    if m:
        hexa = m.group(1)
        if len(hexa) == 3:
            hexa = "".join(c * 2 for c in hexa)
        r, g, b = (int(hexa[i:i + 2], 16) for i in (0, 2, 4))
    else:
        m = re.fullmatch(r"rgba?\(\s*(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*(\d{1,3})(?:\s*,\s*[\d.]+)?\s*\)", q)
        if not m:
            return None
        r, g, b = (min(int(x), 255) for x in m.groups())
    hexa = f"#{r:02x}{g:02x}{b:02x}"
    mx, mn = max(r, g, b) / 255, min(r, g, b) / 255
    light = (mx + mn) / 2
    sat = 0 if mx == mn else (mx - mn) / (1 - abs(2 * light - 1))
    if mx == mn:
        hue = 0
    elif mx == r / 255:
        hue = 60 * (((g - b) / 255 / (mx - mn)) % 6)
    elif mx == g / 255:
        hue = 60 * ((b - r) / 255 / (mx - mn) + 2)
    else:
        hue = 60 * ((r - g) / 255 / (mx - mn) + 4)
    return Answer(hexa, f"rgb({r}, {g}, {b}) · hsl({round(hue)}, {round(sat * 100)}%, {round(light * 100)}%)",
                  "color", hexa)


def answer(text: str) -> Answer | None:
    """The instant answer to what's typed, if there is one."""
    if not text or len(text) > 200:
        return None
    for solver in (color, convert, world_time, calculate):
        found = solver(text)
        if found is not None:
            return found
    return None
