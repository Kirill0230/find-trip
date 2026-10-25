"""Сборка пакета: отель + билеты + трансферы + проверка состыковок + объяснение.

Каждый компонент считается отдельной функцией, поэтому пересборка вызывает
только затронутые (см. corrections.AFFECTS).
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from datetime import date, datetime, timedelta

from ..catalog import DESTINATIONS, HOTELS, ORIGINS, STATIONS, arrival_points
from ..tools.connections import check_connections
from ..tools.pricing import get_hotel_price
from ..tools.transfer import pick_default, search_transfer
from ..tools.transport import search_flights, search_trains
from .explain import dur, guests_label, rub
from .ranking import reachability

COMPONENTS = ["hotel", "transport", "transfer", "connections"]
WATER_TRANSFER_WINDOW = {"maldives": (6, 21)}  # часы, когда ходят катера


def _dt(s: str) -> datetime:
    return datetime.fromisoformat(s)


# ---------------------------------------------------------------- компоненты

def price_hotel(hard: dict, hotel_id: str, room: str | None) -> dict:
    p = get_hotel_price(hotel_id, room, hard["date_from"], hard["date_to"], hard["adults"], hard["children_ages"])
    h = HOTELS[hotel_id]
    return {**p, "name": h["name"], "stars": h["stars"], "area": h["area"], "dest": h["dest"],
            "dest_name": DESTINATIONS[h["dest"]]["name"], "rooms": h["rooms"], "lat": h["lat"], "lon": h["lon"]}


def _choose(options: list[dict], pref: str, leg: str = "out") -> dict:
    if pref == "cheap":
        return min(options, key=lambda o: o["price_total"])
    if pref == "fast":
        return min(options, key=lambda o: (o["duration_min"], o["price_total"]))

    # optimal: цена + 600 ₽ за час в пути на человека + штрафы за неудобное время и тариф без багажа
    def cost(o):
        pax = o.get("pax", 1)
        dep, arr = _dt(o["dep"]), _dt(o["arr"])
        c = o["price_total"] + o["duration_min"] / 60 * 600 * pax
        if not o.get("night"):
            if dep.hour < 6 or arr.hour < 6:
                c += 2500 * pax
            elif dep.hour < 7:
                c += 1200 * pax
        if leg == "out" and arr.hour >= 21:
            c += 2000 * pax  # теряем вечер и заселяемся ночью
        if leg == "back" and dep.hour < 10 and not o.get("night"):
            c += 1500 * pax  # ранний выезд из отеля
        if not o.get("baggage", True):
            c += 1200 * pax
        return c
    return min(options, key=cost)


def _arrival_ok(o: dict, d_in: date) -> bool:
    a = _dt(o["arr"])
    return a.date() == d_in or (a.date() == d_in - timedelta(days=1) and a.hour >= 20)


def _return_ok(o: dict, d_out: date) -> bool:
    d = _dt(o["dep"])
    return d.date() == d_out or (d.date() == d_out + timedelta(days=1) and d.hour < 6)


def search_transport(hard: dict, dest: str, pref: str = "optimal", forced: dict | None = None) -> dict:
    """Ищет туда и обратно параллельно, отбрасывает несостыкующиеся по датам варианты."""
    origin = hard["origin_city"]
    pax = hard["adults"] + len(hard["children_ages"])
    r = reachability(origin, dest, hard)
    if not r["ok"]:
        return {"mode": None, "error": r.get("reason", "не добраться")}
    if r["mode"] in ("local", "car"):
        return {"mode": r["mode"], "outbound": None, "return": None, "options_out": [], "options_back": [], "pref": pref,
                "total_rub": 0, "note": "вы уже в этом городе" if r["mode"] == "local" else "едете своим ходом"}
    d_in, d_out = date.fromisoformat(hard["date_from"]), date.fromisoformat(hard["date_to"])
    jobs = []
    with ThreadPoolExecutor(max_workers=4) as ex:
        for mode in r["modes"]:
            if mode == "flight":
                jobs.append(("out", ex.submit(copy_context().run, search_flights, origin, dest, d_in.isoformat(), hard["adults"], hard["children_ages"])))
                jobs.append(("back", ex.submit(copy_context().run, search_flights, dest, origin, d_out.isoformat(), hard["adults"], hard["children_ages"])))
            elif mode == "train":
                st = ORIGINS[origin]["station"]
                for s in DESTINATIONS[dest]["stations"]:
                    for day in (d_in - timedelta(days=1), d_in):  # ночной поезд накануне
                        jobs.append(("out", ex.submit(copy_context().run, search_trains, st, s, day.isoformat(), hard["adults"], hard["children_ages"])))
                    jobs.append(("back", ex.submit(copy_context().run, search_trains, s, st, d_out.isoformat(), hard["adults"], hard["children_ages"])))
        outs, backs = [], []
        for kind, fut in jobs:
            (outs if kind == "out" else backs).extend(fut.result())
    if hard.get("direct_only"):
        outs = [o for o in outs if o["stops"] == 0]
        backs = [o for o in backs if o["stops"] == 0]
    for o in outs + backs:
        o["pax"] = pax
    outs_ok = [o for o in outs if _arrival_ok(o, d_in)] or outs
    backs_ok = [o for o in backs if _return_ok(o, d_out)] or backs
    win = WATER_TRANSFER_WINDOW.get(dest)
    if win:  # на остров добираются только катером: прилёт и выезд в часы его работы
        lo, hi = win
        outs_ok = [o for o in outs_ok if lo <= _dt(o["arr"]).hour < hi] or outs_ok
        backs_ok = [o for o in backs_ok if lo <= (_dt(o["dep"]) - timedelta(hours=3, minutes=30)).hour < hi] or backs_ok
    if not outs_ok or not backs_ok:
        return {"mode": None, "error": "нет билетов на эти даты"}
    forced = forced or {}
    out = next((o for o in outs if o["id"] == forced.get("outbound_id")), None) or _choose(outs_ok, pref, "out")
    back = next((o for o in backs if o["id"] == forced.get("return_id")), None) or _choose(backs_ok, pref, "back")
    alt = {}
    for p in ("cheap", "fast", "optimal"):
        a, b = _choose(outs_ok, p, "out"), _choose(backs_ok, p, "back")
        alt[p] = {"outbound_id": a["id"], "return_id": b["id"], "total_rub": a["price_total"] + b["price_total"],
                  "duration_min": a["duration_min"]}
    return {"mode": out["mode"], "outbound": out, "return": back, "pref": pref,
            "options_out": sorted(outs_ok, key=lambda o: o["dep"]), "options_back": sorted(backs_ok, key=lambda o: o["dep"]),
            "alternatives": alt, "total_rub": out["price_total"] + back["price_total"]}


def _point_for(leg: dict | None, dest: str, hotel: dict, side: str) -> dict:
    pts = arrival_points(dest)
    if leg:
        code = leg["to"]["code"] if side == "arrival" else leg["from"]["code"]
        for p in pts:
            if p["id"] == code:
                return p
    # своим ходом / местный — от ближайшего вокзала или центра
    return min(pts, key=lambda p: abs(p["lat"] - hotel["lat"]) + abs(p["lon"] - hotel["lon"]))


def search_transfers(hard: dict, hotel: dict, transport: dict, forced: dict | None = None) -> dict:
    forced = forced or {}
    if transport.get("mode") in ("local", "car", None):
        return {"arrival": None, "departure": None, "options_arrival": [], "options_departure": []}
    kids = len(hard["children_ages"])
    out, back = transport["outbound"], transport["return"]
    p_in = _point_for(out, hotel["dest"], hotel, "arrival")
    p_out = _point_for(back, hotel["dest"], hotel, "departure")
    opts_in = search_transfer(p_in, hotel["hotel_id"], out["arr"], hard["adults"], kids)
    # обратно: выезд = отправление − запас − время в пути (оценка по такси)
    taxi_min = next((o["duration_min"] for o in opts_in if o["type"] == "taxi"), opts_in[0]["duration_min"])
    buffer = 150 if back["mode"] == "flight" else 40
    start_back = (_dt(back["dep"]) - timedelta(minutes=buffer + taxi_min)).strftime("%Y-%m-%dT%H:%M")
    opts_out = search_transfer(p_out, hotel["hotel_id"], start_back, hard["adults"], kids)

    def pick(opts, key):
        f = next((o for o in opts if o["type"] == forced.get(key)), None)
        return f or pick_default(opts, hard["adults"], kids)
    return {"arrival": pick(opts_in, "arrival_type"), "departure": pick(opts_out, "departure_type"),
            "options_arrival": opts_in, "options_departure": opts_out}


# ---------------------------------------------------------------- итог, объяснение, план

def totals(pkg: dict) -> dict:
    hotel = pkg["hotel"]["total_rub"]
    tr = (pkg.get("transport") or {}).get("total_rub", 0) or 0
    tf = sum((pkg["transfers"].get(k) or {}).get("price_rub", 0) for k in ("arrival", "departure"))
    total = hotel + tr + tf
    pax = pkg["guests"]["adults"] + len(pkg["guests"]["children_ages"])
    return {"hotel_rub": hotel, "transport_rub": tr, "transfer_rub": tf, "total_rub": total, "per_person_rub": total // max(1, pax)}


def explain_package(pkg: dict) -> dict:
    hotel, tr, tf = pkg["hotel"], pkg.get("transport") or {}, pkg.get("transfers") or {}
    why, trade, risks = [], [], []
    out = tr.get("outbound")
    if out:
        arr = _dt(out["arr"])
        at_hotel = _dt(tf["arrival"]["end"]) if tf.get("arrival") else arr
        kind = "Прилёт" if out["mode"] == "flight" else "Прибытие"
        why.append(f"{kind} {arr.strftime('%d.%m в %H:%M')}, в отеле к {at_hotel.strftime('%H:%M')}, заселение с {hotel['checkin']}.")
        alt = tr.get("alternatives", {})
        if tr.get("pref") == "optimal" and alt:
            cheap, fast = alt["cheap"], alt["fast"]
            cur = tr["total_rub"]
            if cur > cheap["total_rub"] + 500:
                trade.append(f"билеты на {rub(cur - cheap['total_rub'])} дороже самых дешёвых — зато удобнее время" + ("" if out.get("baggage", True) else "") )
            elif fast["duration_min"] < out["duration_min"] - 20:
                trade.append(f"есть вариант быстрее на {dur(out['duration_min'] - fast['duration_min'])}, но дороже на {rub(fast['total_rub'] - cur)}")
        if out["stops"]:
            trade.append(f"пересадка: {out['via']}")
        if out["mode"] == "train" and out.get("night"):
            why.append(f"Ночной поезд, {out['class_name']} — экономит ночь в отеле и день в пути.")
    elif tr.get("mode") in ("local", "car"):
        why.append("Билеты не нужны: " + tr.get("note", "") + ".")
    if tf.get("arrival"):
        a = tf["arrival"]
        why.append(f"Трансфер: {a['name'].lower()}, {dur(a['duration_min'])}" + (f", {rub(a['price_rub'])}" if a["price_rub"] else "") + ".")
        if pkg["guests"]["children_ages"] and a["type"] == "individual":
            why.append("Взял индивидуальный трансфер — с детскими креслами.")
    budget = pkg.get("budget")
    if budget:
        diff = budget - pkg["total"]["total_rub"]
        (why if diff >= 0 else trade).append(f"Итого {rub(pkg['total']['total_rub'])} — " + (f"в бюджете, запас {rub(diff)}." if diff >= 0 else f"выше бюджета на {rub(-diff)}."))
    for c in pkg.get("conflicts", []):
        if c["severity"] == "warning" and c["code"] not in ("over_budget",):
            risks.append(c["message"])
    return {"why": " ".join(why[:3]), "tradeoffs": ("Чем жертвуем: " + "; ".join(trade) + ".") if trade else "Серьёзных компромиссов нет.",
            "risks": ("Риски: " + " ".join(risks[:3])) if risks else "Явных рисков не видно."}


def timeline(pkg: dict) -> list[dict]:
    ev = []
    tr, tf, hotel = pkg.get("transport") or {}, pkg.get("transfers") or {}, pkg["hotel"]
    out, back = tr.get("outbound"), tr.get("return")
    icon = {"flight": "✈️", "train": "🚆"}
    if out:
        ev.append({"at": out["dep"], "icon": icon[out["mode"]], "title": f"Отправление: {out['from']['name']}", "detail": f"{out['carrier']} {out['number']}, в пути {dur(out['duration_min'])}"})
        ev.append({"at": out["arr"], "icon": "📍", "title": f"Прибытие: {out['to']['name']}", "detail": "местное время"})
    if tf.get("arrival"):
        a = tf["arrival"]
        ev.append({"at": a["start"], "icon": "🚕", "title": f"Трансфер в отель: {a['name']}", "detail": f"{dur(a['duration_min'])}, {a['km']} км"})
    checkin_at = f"{hotel['date_from']}T{hotel['checkin']}"
    at_hotel = (tf.get("arrival") or {}).get("end") or (out or {}).get("arr")
    if at_hotel and at_hotel > checkin_at:
        checkin_at = at_hotel  # заселяемся, когда доехали, а не в начале окна заезда
    ev.append({"at": checkin_at, "icon": "🏨", "title": f"Заселение: {hotel['name']}", "detail": f"{hotel['room_name']}, {hotel['meal_name']}, заезд с {hotel['checkin']}"})
    ev.append({"at": f"{hotel['date_to']}T{hotel['checkout']}", "icon": "🧳", "title": "Выезд из номера", "detail": "багаж можно оставить на ресепшене"})
    if tf.get("departure"):
        d = tf["departure"]
        ev.append({"at": d["start"], "icon": "🚕", "title": f"Трансфер: {d['name']}", "detail": f"до точки «{d['point']}», {dur(d['duration_min'])}"})
    if back:
        ev.append({"at": back["dep"], "icon": icon[back["mode"]], "title": f"Обратно: {back['from']['name']}", "detail": f"{back['carrier']} {back['number']}"})
        ev.append({"at": back["arr"], "icon": "🏠", "title": f"Дома: {back['to']['name']}", "detail": "местное время"})
    ev.sort(key=lambda e: e["at"])
    return ev


def finalize(pkg: dict, state: dict) -> dict:
    hard = state["hard"]
    pkg["guests"] = {"adults": hard["adults"], "children_ages": list(hard["children_ages"]),
                     "label": guests_label(hard["adults"], hard["children_ages"])}
    pkg["budget"] = hard.get("budget_total_rub")
    pkg["pets"] = hard.get("pets", False)
    pkg["origin_city"] = hard["origin_city"]
    pkg["origin_name"] = ORIGINS[hard["origin_city"]]["name"]
    pkg["requests"] = list(state.get("requests") or [])
    pkg["total"] = totals(pkg)
    pkg["conflicts"] = check_connections(pkg)
    pkg["explanation"] = explain_package(pkg)
    pkg["timeline"] = timeline(pkg)
    pkg["can_book"] = not any(c["severity"] == "error" for c in pkg["conflicts"])
    return pkg


def build_package(state: dict, hotel_id: str, room: str | None = None, pref: str = "optimal", on_step=None) -> dict:
    """Полная сборка (первый раз). on_step(name, payload) — для SSE-статусов."""
    hard = state["hard"]
    step = on_step or (lambda *_: None)
    step("hotel", {"text": "Проверяю цену и наличие номера"})
    hotel = price_hotel(hard, hotel_id, room)
    step("transport", {"text": "Ищу билеты туда и обратно"})
    transport = search_transport(hard, hotel["dest"], pref)
    step("transfer", {"text": "Подбираю трансфер под время прибытия"})
    transfers = search_transfers(hard, hotel, transport) if transport.get("mode") else {"arrival": None, "departure": None}
    step("connections", {"text": "Проверяю состыковки"})
    pkg = {"id": f"p_{state['session_id']}_{state.get('package_rev', 0) + 1}", "hotel": hotel, "transport": transport, "transfers": transfers,
           "forced": {}, "recomputed": list(COMPONENTS), "kept": []}
    state["package_rev"] = state.get("package_rev", 0) + 1
    return finalize(pkg, state)


def _pick_transport(tr: dict, forced: dict) -> dict:
    """Смена рейса или «дешевле/быстрее» — выбор из уже найденных вариантов, без нового поиска."""
    tr = dict(tr)
    outs, backs = tr.get("options_out") or [], tr.get("options_back") or []
    if not outs or not backs:
        return tr
    pref = tr.get("pref", "optimal")
    tr["outbound"] = next((o for o in outs if o["id"] == forced.get("outbound_id")), None) or _choose(outs, pref, "out")
    tr["return"] = next((o for o in backs if o["id"] == forced.get("return_id")), None) or _choose(backs, pref, "back")
    tr["total_rub"] = tr["outbound"]["price_total"] + tr["return"]["price_total"]
    return tr


def _pick_transfers(tf: dict, forced: dict, hard: dict) -> dict:
    tf = dict(tf)
    kids = len(hard["children_ages"])
    for side, key in (("arrival", "arrival_type"), ("departure", "departure_type")):
        opts = tf.get(f"options_{side}") or []
        if opts:
            tf[side] = next((o for o in opts if o["type"] == forced.get(key)), None) or pick_default(opts, hard["adults"], kids)
    return tf


def rebuild(state: dict, affected: set[str], hotel_id: str | None = None, room: str | None = None) -> dict:
    """Пересборка только затронутых компонентов; остальное берётся из текущего пакета."""
    pkg = dict(state["package"])
    hard = state["hard"]
    forced = dict(pkg.get("forced") or {})
    affected = set(affected)
    recomputed = []
    hotel_changed = bool(hotel_id and hotel_id != pkg["hotel"]["hotel_id"])
    if hotel_changed:
        affected |= {"hotel", "transport", "transfer"}
        forced = {}
    if "hotel" in affected:
        old_room = pkg["hotel"]["room_category"]
        pkg["hotel"] = price_hotel(hard, hotel_id or pkg["hotel"]["hotel_id"], room or old_room)
        # тот же отель — только новая цена; другой отель или номер — новый выбор
        recomputed.append("hotel" if hotel_changed or pkg["hotel"]["room_category"] != old_room else "hotel_price")
    pref = (pkg.get("transport") or {}).get("pref", "optimal")
    if "transport" in affected:
        forced.pop("outbound_id", None)
        forced.pop("return_id", None)
        pkg["transport"] = search_transport(hard, pkg["hotel"]["dest"], pref)
        recomputed.append("transport")
    elif "transport_pick" in affected and pkg.get("transport", {}).get("mode") in ("flight", "train"):
        old = (pkg["transport"].get("outbound") or {}).get("id"), (pkg["transport"].get("return") or {}).get("id")
        pkg["transport"] = _pick_transport(pkg["transport"], forced)
        recomputed.append("transport_choice")
        if old != (pkg["transport"]["outbound"]["id"], pkg["transport"]["return"]["id"]):
            affected.add("transfer")
    if "transfer" in affected or "transport" in affected:
        pkg["transfers"] = search_transfers(hard, pkg["hotel"], pkg["transport"], forced) if pkg["transport"].get("mode") else {"arrival": None, "departure": None}
        recomputed.append("transfer")
    elif "transfer_pick" in affected:
        pkg["transfers"] = _pick_transfers(pkg["transfers"], forced, hard)
        recomputed.append("transfer_choice")
    pkg["forced"] = forced
    pkg["recomputed"] = recomputed + ["connections"]
    pkg["kept"] = [c for c in ("hotel", "transport", "transfer") if c not in recomputed]
    state["package_rev"] = state.get("package_rev", 0) + 1
    pkg["id"] = f"p_{state['session_id']}_{state['package_rev']}"
    return finalize(pkg, state)
