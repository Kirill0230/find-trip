"""search_transfer — мок: расстояние по координатам × тариф направления."""
from __future__ import annotations

from datetime import datetime, timedelta

from ..catalog import DESTINATIONS, HOTELS, haversine_km
from . import tool

ABROAD_RATE = {"antalya": 38, "hurghada": 30, "dubai": 55}
CITY_PUBLIC = {"spb": ("метро", 75), "kazan": ("метро/автобус", 50), "dubai": ("метро", 250)}


def _in_hours(t: datetime, start: str, end: str) -> bool:
    hm = t.strftime("%H:%M")
    return start <= hm <= end if start < end else (hm >= start or hm <= end)


@tool("search_transfer")
def search_transfer(point: dict, hotel_id: str, at: str, adults: int, children: int = 0) -> list[dict]:
    """point — точка прибытия/отправления {kind, name, lat, lon}; at — время начала поездки (местное)."""
    h = HOTELS[hotel_id]
    dest = h["dest"]
    km = haversine_km(point["lat"], point["lon"], h["lat"], h["lon"]) * 1.3
    t = datetime.fromisoformat(at)
    pax = adults + children
    rate = ABROAD_RATE.get(dest, 30)
    drive = int(km / (32 if km < 15 else 50) * 60) + 10
    out = []
    if km < 1.2:
        out.append({"type": "walk", "name": "Пешком", "duration_min": max(3, int(km * 13)), "price_rub": 0,
                    "available": True, "hours": "круглосуточно", "note": "отель рядом"})
    big = pax > 4
    taxi_price = int(round((250 + km * rate) * (1.6 if big else 1) / 10) * 10)
    out.append({"type": "taxi", "name": "Минивэн-такси" if big else "Такси", "duration_min": drive,
                "price_rub": taxi_price, "available": True, "hours": "круглосуточно",
                "note": "детское кресло по запросу, не гарантировано" if children else ""})
    out.append({"type": "individual", "name": "Индивидуальный трансфер", "duration_min": drive,
                "price_rub": int(round(taxi_price * 1.7 / 10) * 10), "available": True, "hours": "круглосуточно",
                "note": "встреча с табличкой" + (", детские кресла включены" if children else "")})
    if h.get("shuttle") or "abroad" in DESTINATIONS[dest]["tags"]:
        per = 900 if "abroad" in DESTINATIONS[dest]["tags"] else 450
        out.append({"type": "shuttle", "name": "Групповой трансфер", "duration_min": int(drive * 1.4) + 20,
                    "price_rub": per * pax, "available": _in_hours(t, "07:00", "23:00"), "hours": "07:00–23:00",
                    "note": "с остановками у других отелей"})
    if dest in CITY_PUBLIC and point["kind"] in ("airport", "station") and km > 1.2:
        name, per = CITY_PUBLIC[dest]
        out.append({"type": "public", "name": name.capitalize(), "duration_min": int(drive * 1.5) + 10,
                    "price_rub": per * pax, "available": _in_hours(t, "06:00", "00:30"), "hours": "06:00–00:30",
                    "note": "с пересадкой, с багажом неудобно"})
    for o in out:
        o["km"] = round(km, 1)
        o["start"] = at
        o["end"] = (t + timedelta(minutes=o["duration_min"])).strftime("%Y-%m-%dT%H:%M")
        o["point"] = point["name"]
    return out


def pick_default(options: list[dict], adults: int, children: int) -> dict:
    avail = [o for o in options if o["available"]]
    walk = next((o for o in avail if o["type"] == "walk"), None)
    if walk:
        return walk
    if children:
        return next(o for o in avail if o["type"] == "individual")
    # самое дешёвое из доступных, но не дольше такси на 50+ минут
    taxi = next(o for o in avail if o["type"] == "taxi")
    reasonable = [o for o in avail if o["duration_min"] <= taxi["duration_min"] + 50]
    return min(reasonable, key=lambda o: o["price_rub"])
