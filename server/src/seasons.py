"""The store's calendar: occasions that lift demand, and by how much.

Demo data, like the supplier price lists. A real store would learn these lifts
from its own sales history; here they are a fixed table so the forecast is the
same every time and can be checked by hand.

The dashboard can pretend an occasion is a few days away, so the forecast can
be shown on any day of the year."""
import datetime as dt
from typing import Any, Dict, List, Optional

LEAD_DAYS = 10  # demand starts rising this many days before an occasion
SIMULATED_DAYS_AWAY = 5

# uplift: percent more than normal daily sales, per product, in the run-up.
OCCASIONS: List[Dict[str, Any]] = [
    {
        "name": "Diwali",
        "dates": ["2026-11-08", "2027-10-29", "2028-10-17"],  # moves every year
        "uplift": {"chocolate bar": 40, "cola can": 30, "biscuit packet": 30},
    },
    {
        "name": "Christmas",
        "month_day": (12, 25),
        "uplift": {"chocolate bar": 30, "cola can": 30, "biscuit packet": 20},
    },
    {
        "name": "Valentine's Day",
        "month_day": (2, 14),
        "uplift": {"chocolate bar": 50, "pen": 20},
    },
    {
        "name": "Exam season",
        "month_day": (3, 1),
        "uplift": {"pen": 40, "water bottle": 20},
    },
]

# None: follow the real calendar. "none": no occasion. Otherwise an occasion name.
_simulated: Optional[str] = None


def names() -> List[str]:
    return [o["name"] for o in OCCASIONS]


def simulated() -> Optional[str]:
    return _simulated


def set_simulated(name: Optional[str]) -> Optional[str]:
    """Pretend an occasion is a few days away, "none" for a plain week, or
    None to go back to today's real date."""
    global _simulated
    if name in (None, "", "auto"):
        _simulated = None
    elif name == "none":
        _simulated = "none"
    else:
        match = next((n for n in names() if n.lower() == str(name).lower()), None)
        if match is None:
            raise ValueError(f"Unknown occasion: {name}")
        _simulated = match
    return _simulated


def _days_until(occasion: Dict[str, Any], today: dt.date) -> Optional[int]:
    """Days from today to the next time this occasion falls, if known."""
    if "dates" in occasion:
        ahead = [d for d in (dt.date.fromisoformat(x) for x in occasion["dates"]) if d >= today]
        return (min(ahead) - today).days if ahead else None
    month, day = occasion["month_day"]
    this_year = dt.date(today.year, month, day)
    nxt = this_year if this_year >= today else dt.date(today.year + 1, month, day)
    return (nxt - today).days


def coming_up(today: Optional[dt.date] = None) -> Optional[Dict[str, Any]]:
    """The occasion the store is in the run-up to, if any."""
    if _simulated == "none":
        return None
    if _simulated:
        occasion = next(o for o in OCCASIONS if o["name"] == _simulated)
        return {**occasion, "days_away": SIMULATED_DAYS_AWAY, "simulated": True}
    today = today or dt.date.today()
    near = []
    for occasion in OCCASIONS:
        days = _days_until(occasion, today)
        if days is not None and days <= LEAD_DAYS:
            near.append({**occasion, "days_away": days, "simulated": False})
    return min(near, key=lambda o: o["days_away"]) if near else None


def uplift_for(product: str, today: Optional[dt.date] = None) -> Optional[Dict[str, Any]]:
    """The occasion lifting demand for this product right now, if any:
    {"name", "days_away", "percent", "simulated"}."""
    occasion = coming_up(today)
    if not occasion or product not in occasion["uplift"]:
        return None
    return {
        "name": occasion["name"],
        "days_away": occasion["days_away"],
        "percent": occasion["uplift"][product],
        "simulated": occasion["simulated"],
    }
