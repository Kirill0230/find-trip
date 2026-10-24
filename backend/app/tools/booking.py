"""book — имитация оформления: номер заказа и ваучеры. Деньги никуда не уходят."""
from __future__ import annotations

from datetime import datetime

from ..catalog import HOTELS, stable_hash
from . import tool

ALPH = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def _code(seed: int, n: int = 6) -> str:
    s = ""
    for _ in range(n):
        s += ALPH[seed % len(ALPH)]
        seed //= len(ALPH)
        seed ^= 0x5BD1E995
    return s


@tool("book")
def book(session_id: str, pkg: dict, guests: list[dict], contact: dict) -> dict:
    seed = stable_hash(session_id, pkg["id"], datetime.utcnow().isoformat())
    order = f"OD-{datetime.utcnow():%y%m}-{seed % 1000000:06d}"
    h = HOTELS[pkg["hotel"]["hotel_id"]]
    vouchers = [{
        "kind": "hotel", "title": f"{h['name']} · {pkg['hotel']['room_name']}",
        "code": _code(seed), "details": f"{pkg['hotel']['date_from']} — {pkg['hotel']['date_to']}, {pkg['hotel']['meal_name']}",
    }]
    tr = pkg.get("transport") or {}
    for leg_key, label in (("outbound", "Туда"), ("return", "Обратно")):
        leg = tr.get(leg_key)
        if leg:
            vouchers.append({"kind": leg["mode"], "title": f"{label}: {leg['carrier']} {leg['number']}",
                             "code": _code(seed >> 3 if leg_key == "outbound" else seed >> 7),
                             "details": f"{leg['from']['name']} → {leg['to']['name']}, {leg['dep'].replace('T', ' ')}"})
    for key, label in (("arrival", "Трансфер в отель"), ("departure", "Трансфер обратно")):
        t = (pkg.get("transfers") or {}).get(key)
        if t and t["type"] != "walk":
            vouchers.append({"kind": "transfer", "title": f"{label}: {t['name']}", "code": _code(seed >> 11 if key == "arrival" else seed >> 13),
                             "details": f"{t['point']}, {t['start'].replace('T', ' ')}"})
    return {
        "order_id": order,
        "status": "confirmed",
        "demo_payment": True,
        "paid_rub": pkg["total"]["total_rub"],
        "guests": guests,
        "contact": contact,
        "vouchers": vouchers,
        "created_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
    }
