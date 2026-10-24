"""get_hotel_price — мок вместо API Островка.

Цена = базовая × сезонный коэффициент × день недели × категория номера × гости,
плюс детерминированный шум. Наличие тоже детерминированное (часть дат распродана).
"""
from __future__ import annotations

from datetime import date, timedelta

from ..catalog import DESTINATIONS, HOTELS, MEALS, ROOM_TYPES, default_room, noise, stable_hash
from . import tool

DOW = [0.95, 0.9, 0.9, 0.95, 1.12, 1.18, 1.0]  # пн..вс (ночь с пятницы и субботы дороже)


def _guest_mult(adults: int, children_ages: list[int]) -> float:
    m = 1.0 + 0.35 * max(0, adults - 2) - (0.15 if adults == 1 else 0)
    for age in children_ages:
        if age >= 12:
            m += 0.35
        elif age >= 2:
            m += 0.15
    return m


def nightly_price(hotel_id: str, room: str, night: date, adults: int, children_ages: list[int]) -> int:
    h = HOTELS[hotel_id]
    season = DESTINATIONS[h["dest"]]["season"][night.month - 1]
    # Новогодние ночи — отдельный пик
    if (night.month == 12 and night.day >= 29) or (night.month == 1 and night.day <= 7):
        season *= 1.35
    p = h["base"] * season * DOW[night.weekday()] * ROOM_TYPES[room]["mult"] * _guest_mult(adults, children_ages)
    p *= noise(hotel_id, room, night.isoformat(), lo=0.94, hi=1.08)
    return int(round(p / 50) * 50)


def _rooms_left(hotel_id: str, room: str, d_from: date, d_to: date) -> int:
    worst = 9
    d = d_from
    while d < d_to:
        hv = stable_hash("avail", hotel_id, room, d.isoformat())
        left = 0 if hv % 1000 < 12 else 1 + (hv >> 10) % 8
        worst = min(worst, left)
        d += timedelta(days=1)
    return worst


@tool("get_hotel_price")
def get_hotel_price(hotel_id: str, room: str | None, date_from: str, date_to: str,
                    adults: int, children_ages: list[int] | None = None) -> dict:
    children_ages = children_ages or []
    h = HOTELS[hotel_id]
    guests = adults + len(children_ages)
    room = room if room in h["rooms"] else default_room(hotel_id, guests)
    d_from, d_to = date.fromisoformat(date_from), date.fromisoformat(date_to)
    nights = max(1, (d_to - d_from).days)
    total = 0
    d = d_from
    for _ in range(nights):
        total += nightly_price(hotel_id, room, d, adults, children_ages)
        d += timedelta(days=1)
    left = _rooms_left(hotel_id, room, d_from, d_from + timedelta(days=nights))
    capacity_ok = ROOM_TYPES[room]["capacity"] >= guests
    cancel_days = h["cancel_days"]
    if cancel_days > 0:
        deadline = d_from - timedelta(days=cancel_days)
        cancel = {"free": True, "deadline": deadline.isoformat(),
                  "text": f"бесплатная отмена до {deadline.strftime('%d.%m')}"}
    else:
        cancel = {"free": False, "deadline": None, "text": "невозвратный тариф"}
    return {
        "hotel_id": hotel_id,
        "room_category": room,
        "room_name": ROOM_TYPES[room]["name"],
        "date_from": date_from, "date_to": (d_from + timedelta(days=nights)).isoformat(),
        "nights": nights,
        "available": left > 0 and capacity_ok,
        "rooms_left": left,
        "capacity_ok": capacity_ok,
        "total_rub": total,
        "per_night_rub": total // nights,
        "meal": h["meal"], "meal_name": MEALS[h["meal"]],
        "cancel": cancel,
        "checkin": h["checkin"], "checkout": h["checkout"],
        "early_checkin": h.get("early_checkin", False),
        "source": "mock",
    }
