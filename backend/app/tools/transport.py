"""search_flights / search_trains.

Без ключей — детерминированный мок (расстояние, сезон, день недели, время суток).
С TRAVELPAYOUTS_TOKEN мок калибруется по реальному кэшу цен Aviasales,
с YANDEX_RASP_KEY берётся реальное расписание поездов (цены остаются моком).
"""
from __future__ import annotations

import os
from datetime import date, datetime, timedelta
from functools import lru_cache

import httpx

from ..catalog import (DESTINATIONS, ORIGINS, RAIL_ROUTES, STATIONS, TZ, haversine_km, noise,
                       stable_hash)
from . import tool

CARRIERS = [("Аэроволна", "AV"), ("СеверСкай", "SS"), ("ЮгАвиа", "YA"), ("Небесный Экспресс", "NE")]
DEP_SLOTS = ["06:10", "07:40", "09:55", "11:30", "13:20", "16:45", "19:30", "23:50"]
DOW_K = [0.95, 0.9, 0.9, 0.97, 1.12, 1.05, 1.15]
OFFLINE = os.getenv("OFFLINE") == "1"


def airport_of(place: str) -> dict | None:
    if place in DESTINATIONS:
        return DESTINATIONS[place]["airport"]
    if place in ORIGINS:
        return ORIGINS[place]["airport"]
    return None


def _season(place: str, month: int) -> float:
    return DESTINATIONS[place]["season"][month - 1] if place in DESTINATIONS else 1.0


def _is_direct(o: str, d: str) -> bool:
    if "moscow" in (o, d) or "spb" in (o, d):
        return True
    if o in ("sochi", "antalya", "dubai", "hurghada") or d in ("sochi", "antalya", "dubai", "hurghada"):
        return True
    return {o, d} in ({"nsk", "altai"}, {"ekb", "altai"})


def flight_minutes(o: str, d: str) -> int | None:
    a, b = airport_of(o), airport_of(d)
    if not a or not b or a["code"] == b["code"]:
        return None
    dist = haversine_km(a["lat"], a["lon"], b["lat"], b["lon"])
    if dist < 350:
        return None
    return int(round((dist / 800 * 60 + 36) / 5) * 5)


def _pax_mult(adults: int, children_ages: list[int], child_k: float = 0.75, infant_k: float = 0.1) -> float:
    m = float(adults)
    for a in children_ages:
        m += 1.0 if a >= 12 else (child_k if a >= 2 else infant_k)
    return m


def _local(d: date, hhmm: str) -> datetime:
    h, m = map(int, hhmm.split(":"))
    return datetime(d.year, d.month, d.day, h, m)


def _fmt(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M")


@lru_cache(maxsize=256)
def _travelpayouts_min(o_code: str, d_code: str, month: str) -> int | None:
    token = os.getenv("TRAVELPAYOUTS_TOKEN")
    if not token or OFFLINE:
        return None
    try:
        r = httpx.get("https://api.travelpayouts.com/aviasales/v3/prices_for_dates", timeout=3.0, params={
            "origin": o_code, "destination": d_code, "departure_at": month, "one_way": "true",
            "currency": "rub", "sorting": "price", "limit": 1, "token": token})
        data = r.json().get("data") or []
        return int(data[0]["price"]) if data else None
    except Exception:
        return None


def _one_leg(o: str, d: str, dep: datetime, minutes: int) -> datetime:
    return dep + timedelta(minutes=minutes) + timedelta(hours=TZ.get(d, 3) - TZ.get(o, 3))


@tool("search_flights")
def search_flights(origin: str, dest: str, day: str, adults: int, children_ages: list[int] | None = None) -> list[dict]:
    children_ages = children_ages or []
    dur = flight_minutes(origin, dest)
    if dur is None:
        return []
    a, b = airport_of(origin), airport_of(dest)
    d = date.fromisoformat(day)
    dist = haversine_km(a["lat"], a["lon"], b["lat"], b["lon"])
    trip_place = dest if dest in DESTINATIONS and dest not in ORIGINS else (origin if origin in DESTINATIONS else dest)
    base = (2400 + dist * 3.3) * _season(trip_place, d.month) * DOW_K[d.weekday()]
    pax = _pax_mult(adults, children_ages)
    seed = stable_hash(origin, dest, day)
    options = []
    direct = _is_direct(origin, dest)
    if direct:
        slots = sorted({DEP_SLOTS[(seed >> (i * 3)) % len(DEP_SLOTS)] for i in range(5)})[:4]
        for i, slot in enumerate(slots):
            dep = _local(d, slot)
            carrier = CARRIERS[(seed + i) % len(CARRIERS)]
            tod = 0.85 if slot >= "23:00" or slot < "07:00" else (1.1 if "16:00" <= slot <= "20:00" else 1.0)
            options.append({"dep": dep, "arr": _one_leg(origin, dest, dep, dur), "duration_min": dur, "stops": 0,
                            "via": None, "carrier": carrier, "k": tod * noise(seed, i, lo=0.92, hi=1.12)})
    else:
        leg1 = flight_minutes(origin, "moscow") or 120
        leg2 = flight_minutes("moscow", dest) or 120
        for i, slot in enumerate(("07:40", "13:20")):
            dep = _local(d, slot)
            layover = 95 + (seed >> i) % 130
            total = leg1 + layover + leg2
            arr = dep + timedelta(minutes=total) + timedelta(hours=TZ.get(dest, 3) - TZ.get(origin, 3))
            options.append({"dep": dep, "arr": arr, "duration_min": total, "stops": 1, "via": "Москва (SVO)",
                            "carrier": CARRIERS[(seed + i) % len(CARRIERS)], "k": 0.82 * noise(seed, i, lo=0.95, hi=1.1)})
    # самый дешёвый тариф — без багажа
    cheapest = min(range(len(options)), key=lambda i: options[i]["k"])
    real_min = _travelpayouts_min(a["code"], b["code"], d.strftime("%Y-%m"))
    calib = (real_min / (base * options[cheapest]["k"] * 0.85)) if real_min else 1.0
    out = []
    for i, o in enumerate(options):
        k = o["k"] * (0.85 if i == cheapest else 1.0) * calib
        per_adult = int(round(base * k / 10) * 10)
        num = 100 + (seed >> (i + 2)) % 899
        out.append({
            "id": f"f_{origin}_{dest}_{day}_{i}",
            "mode": "flight",
            "carrier": o["carrier"][0],
            "number": f"{o['carrier'][1]} {num}",
            "from": {"code": a["code"], "name": a["name"]},
            "to": {"code": b["code"], "name": b["name"]},
            "dep": _fmt(o["dep"]), "arr": _fmt(o["arr"]),
            "duration_min": o["duration_min"],
            "stops": o["stops"], "via": o["via"],
            "baggage": i != cheapest,
            "price_per_adult": per_adult,
            "price_total": int(round(per_adult * pax / 10) * 10),
            "source": "travelpayouts+mock" if real_min else "mock",
        })
    return out


def _route(from_station: str, to_station: str) -> list[dict] | None:
    for r in RAIL_ROUTES:
        if r["from"] == from_station and r["to"] == to_station:
            return r["trains"]
    for r in RAIL_ROUTES:  # обратное направление — зеркальное расписание
        if r["to"] == from_station and r["from"] == to_station:
            return r["trains"]
    return None


def rail_possible(origin: str, dest: str) -> bool:
    st = ORIGINS.get(origin, {}).get("station")
    return bool(st) and any(_route(st, s) for s in DESTINATIONS[dest]["stations"])


def train_minutes(origin: str, dest: str) -> int | None:
    st = ORIGINS.get(origin, {}).get("station")
    if not st:
        return None
    best = None
    for s in DESTINATIONS[dest]["stations"]:
        for t in _route(st, s) or []:
            best = t["dur_min"] if best is None else min(best, t["dur_min"])
    return best


CLASS_NAMES = {"econom": "сидячий эконом", "business": "бизнес", "platz": "плацкарт", "kupe": "купе", "sv": "СВ"}


@tool("search_trains")
def search_trains(from_station: str, to_station: str, day: str, adults: int,
                  children_ages: list[int] | None = None) -> list[dict]:
    children_ages = children_ages or []
    trains = _route(from_station, to_station)
    if not trains:
        return []
    d = date.fromisoformat(day)
    pax = _pax_mult(adults, children_ages, child_k=0.6, infant_k=0.0)
    out = []
    for i, t in enumerate(trains):
        dep = _local(d, t["dep"])
        arr = dep + timedelta(minutes=t["dur_min"])
        night = t["dur_min"] > 420 and (t["dep"] >= "19:00" or t["dep"] < "02:00")
        classes = {c: int(round(p * DOW_K[d.weekday()] * noise(t["num"], day, c, lo=0.9, hi=1.15) / 10) * 10)
                   for c, p in t["classes"].items()}
        default_cls = "kupe" if night and "kupe" in classes else min(classes, key=classes.get)
        out.append({
            "id": f"t_{from_station}_{to_station}_{day}_{i}",
            "mode": "train",
            "carrier": f"РЖД · {t['name']}",
            "number": t["num"],
            "from": {"code": from_station, "name": STATIONS[from_station]["name"]},
            "to": {"code": to_station, "name": STATIONS[to_station]["name"]},
            "dep": _fmt(dep), "arr": _fmt(arr),
            "duration_min": t["dur_min"],
            "stops": 0, "via": None, "night": night,
            "class": default_cls, "class_name": CLASS_NAMES[default_cls],
            "classes": [{"code": c, "name": CLASS_NAMES[c], "price_per_adult": p} for c, p in classes.items()],
            "baggage": True,
            "price_per_adult": classes[default_cls],
            "price_total": int(round(classes[default_cls] * pax / 10) * 10),
            "source": "mock",
        })
    return out
