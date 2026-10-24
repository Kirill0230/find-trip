"""Фильтры, ранжирование ленты и правила свайпов (без ML, формула из ТЗ).

score(v) = Σ w_i·f_i(v) − 0.3·sim(v, skipped) + 0.4·novelty(v)

Коэффициент novelty поднят с 0.1 (стартовый в ТЗ) до 0.4 по тестам: при 0.1 пара лайков
уводила ленту в одно направление. Плюс правило: не больше двух роликов одного направления подряд.
"""
from __future__ import annotations

import math
from datetime import date
from functools import lru_cache

from ..catalog import (DESTINATIONS, FEATURE_LABELS, FEATURES, HOTELS, ORIGINS, ROOM_TYPES, TZ,
                       arrival_points, climate, default_room, haversine_km, hotel_by_video, hotel_features,
                       primary_video)
from ..tools.pricing import get_hotel_price
from ..tools.transfer import ABROAD_RATE
from ..tools.transport import _is_direct, flight_minutes, rail_possible, search_flights, search_trains, train_minutes

SIM_K = 0.3
NOVELTY_K = 0.4
BUDGET_SLACK = 1.10
SHORTLIST_EVERY = 3
WARM_WATER = 21


# ---------------------------------------------------------------- направления

def same_city(origin: str, dest: str) -> bool:
    return origin == dest


def reachability(origin: str, dest: str, hard: dict) -> dict:
    """Какими способами и за сколько можно добраться с учётом ограничений."""
    if same_city(origin, dest):
        return {"ok": True, "mode": "local", "minutes": 0, "modes": ["local"]}
    pref = hard.get("transport", "any")
    modes = {}
    if pref in ("any", "train") and rail_possible(origin, dest):
        modes["train"] = train_minutes(origin, dest)
    if pref in ("any", "flight"):
        fm = flight_minutes(origin, dest)
        if fm and (not hard.get("direct_only") or _is_direct(origin, dest)):
            if not _is_direct(origin, dest):
                fm = (flight_minutes(origin, "moscow") or 120) + 150 + (flight_minutes("moscow", dest) or 120)
            modes["flight"] = fm
    if pref == "car":
        if "russia" in DESTINATIONS[dest]["tags"]:
            return {"ok": True, "mode": "car", "minutes": None, "modes": ["car"]}
        return {"ok": False, "reason": "на машине туда не доехать", "modes": []}
    if not modes:
        why = "поездом туда не добраться" if pref == "train" else "нет подходящего транспорта"
        return {"ok": False, "reason": why, "modes": []}
    limit = hard.get("max_travel_hours")
    if limit:
        fit = {m: v for m, v in modes.items() if v <= limit * 60}
        if not fit:
            best = min(modes.values())
            return {"ok": False, "reason": f"в пути {best // 60} ч {best % 60} мин — дольше {limit} ч", "modes": list(modes)}
        modes = fit
    # предпочтение: поезд, если он не сильно дольше самолёта с дорогой до аэропорта
    if "train" in modes and "flight" in modes:
        mode = "train" if modes["train"] <= modes["flight"] + 150 else "flight"
    else:
        mode = next(iter(modes))
    return {"ok": True, "mode": mode, "minutes": modes[mode], "modes": list(modes)}


def dest_allowed(dest: str, hard: dict) -> tuple[bool, str]:
    d = DESTINATIONS[dest]
    if hard.get("destination") and hard["destination"] != dest:
        return False, "другое направление"
    if dest in (hard.get("exclude_dests") or []) and not hard.get("destination"):
        return False, "исключено пользователем"
    if hard.get("destinations_allowed") and dest not in hard["destinations_allowed"]:
        return False, "не в списке"
    month = date.fromisoformat(hard["date_from"]).month
    tags = set(hard.get("trip_tags") or [])
    if not hard.get("destination"):
        if "abroad" in tags and "abroad" not in d["tags"]:
            return False, "не за границей"
        if "russia" in tags and "russia" not in d["tags"]:
            return False, "нужен загранпаспорт"
        cats = tags - {"abroad", "russia"}
        if cats:
            ok = False
            for c in cats:
                if c == "warm_sea" and "sea" in d["tags"] and (climate(dest, month)["water"] or 0) >= WARM_WATER:
                    ok = True
                elif c == "sea" and "sea" in d["tags"]:
                    ok = True
                elif c == "mountains" and "mountains" in d["tags"]:
                    ok = True
                elif c == "nature" and ("nature" in d["tags"] or "mountains" in d["tags"]):
                    ok = True
                elif c == "city" and "city" in d["tags"]:
                    ok = True
            if not ok:
                return False, "не подходит под тип отдыха"
    r = reachability(hard["origin_city"], dest, hard)
    if not r["ok"]:
        return False, r["reason"]
    return True, ""


# ---------------------------------------------------------------- оценки стоимости (кэш, не вызов инструмента)

@lru_cache(maxsize=4096)
def _transport_estimate(origin: str, dest: str, d_from: str, d_to: str, adults: int, children: tuple, pref: str,
                        direct_only: bool, limit) -> dict:
    hard = {"transport": pref, "direct_only": direct_only, "max_travel_hours": limit}
    r = reachability(origin, dest, hard)
    if not r["ok"]:
        return {"ok": False}
    if r["mode"] in ("local", "car"):
        return {"ok": True, "mode": r["mode"], "price": 0, "minutes": r["minutes"]}
    ch = list(children)
    best = None
    for mode in r["modes"]:
        if mode == "flight":
            out = search_flights.__wrapped__(origin, dest, d_from, adults, ch)
            back = search_flights.__wrapped__(dest, origin, d_to, adults, ch)
            if direct_only:
                out = [o for o in out if o["stops"] == 0]
                back = [o for o in back if o["stops"] == 0]
        else:
            st = ORIGINS[origin]["station"]
            out, back = [], []
            for s in DESTINATIONS[dest]["stations"]:
                out += search_trains.__wrapped__(st, s, d_from, adults, ch)
                back += search_trains.__wrapped__(s, st, d_to, adults, ch)
        if out and back:
            price = min(o["price_total"] for o in out) + min(o["price_total"] for o in back)
            if best is None or price < best["price"]:
                best = {"ok": True, "mode": mode, "price": price, "minutes": r["minutes"] if mode == r["mode"] else (train_minutes(origin, dest) if mode == "train" else flight_minutes(origin, dest))}
    return best or {"ok": False}


def transport_estimate(hard: dict, dest: str) -> dict:
    return _transport_estimate(hard["origin_city"], dest, hard["date_from"], hard["date_to"], hard["adults"],
                               tuple(hard["children_ages"]), hard.get("transport", "any"), bool(hard.get("direct_only")),
                               hard.get("max_travel_hours"))


def transfer_estimate(hotel_id: str) -> int:
    h = HOTELS[hotel_id]
    pts = arrival_points(h["dest"])
    km = min(haversine_km(p["lat"], p["lon"], h["lat"], h["lon"]) for p in pts) * 1.3
    if km < 1.2:
        return 0
    return int(2 * (250 + km * ABROAD_RATE.get(h["dest"], 30)))


# ---------------------------------------------------------------- кандидаты

def candidate(hard: dict, hotel_id: str, room: str | None = None) -> dict:
    """Отель с ценой на даты и оценкой пакета. Не учитывает фильтры."""
    guests = hard["adults"] + len(hard["children_ages"])
    room = room or (hard.get("room_category") if hard.get("hotel_id") == hotel_id else None) or default_room(hotel_id, guests)
    price = get_hotel_price.__wrapped__(hotel_id, room, hard["date_from"], hard["date_to"], hard["adults"], hard["children_ages"])
    tr = transport_estimate(hard, HOTELS[hotel_id]["dest"])
    tf = transfer_estimate(hotel_id)
    pkg = int(round((price["total_rub"] + (tr.get("price") or 0) + tf) / 10) * 10) if tr.get("ok") else None
    return {"hotel_id": hotel_id, "price": price, "transport": tr, "transfer_est": tf, "package_est": pkg}


def hotel_passes(hard: dict, c: dict) -> tuple[bool, str]:
    h = HOTELS[c["hotel_id"]]
    ok, why = dest_allowed(h["dest"], hard)
    if not ok:
        return False, why
    if hard.get("pets") and not h["pets"]:
        return False, "без животных"
    if hard.get("min_stars") and h["stars"] < hard["min_stars"]:
        return False, f"меньше {hard['min_stars']}★"
    if not c["price"]["available"]:
        return False, "нет мест на даты" if c["price"]["capacity_ok"] else "не помещаются гости"
    if hard.get("budget_total_rub") and not hard.get("budget_soft") and c["package_est"] and c["package_est"] > hard["budget_total_rub"] * BUDGET_SLACK:
        return False, "дороже бюджета"
    if hard.get("budget_night_rub") and not hard.get("budget_soft") and c["price"]["per_night_rub"] > hard["budget_night_rub"] * BUDGET_SLACK:
        return False, "дороже бюджета за ночь"
    if not c["transport"].get("ok"):
        return False, "не добраться"
    return True, ""


def all_candidates(hard: dict) -> tuple[list[dict], dict]:
    """→ (подходящие кандидаты, причины отсева по отелям)."""
    passed, rejected = [], {}
    for hid in HOTELS:
        c = candidate(hard, hid)
        ok, why = hotel_passes(hard, c)
        if ok:
            passed.append(c)
        else:
            rejected[hid] = {"why": why, "package_est": c["package_est"]}
    return passed, rejected


# ---------------------------------------------------------------- скоринг

def _centered(f: dict) -> list[float]:
    return [f[k] - 0.5 for k in FEATURES if k != "quality"]


def _cos(a: list[float], b: list[float]) -> float:
    na = math.sqrt(sum(x * x for x in a)) or 1
    nb = math.sqrt(sum(x * x for x in b)) or 1
    return sum(x * y for x, y in zip(a, b)) / (na * nb)


def features_in_context(c: dict, cands: list[dict]) -> dict:
    """f_i ролика; признак «цена» — относительно других кандидатов на эти даты."""
    f = dict(hotel_features(c["hotel_id"]))
    prices = [x["package_est"] or x["price"]["total_rub"] for x in cands] or [1]
    lo, hi = min(prices), max(prices)
    p = c["package_est"] or c["price"]["total_rub"]
    f["budget"] = round(1 - (p - lo) / (hi - lo), 3) if hi > lo else 0.5
    return f


def dest_affinity(stats: dict | None, dest: str) -> float:
    """Отношение к месту по свайпам: пропуски без единого лайка — минус, лайки — небольшой плюс."""
    st = (stats or {}).get(dest) or {}
    likes, skips = st.get("like", 0), st.get("skip", 0)
    if likes:
        return 0.15 * min(1.0, likes / 2)
    return -0.6 * min(1.0, max(0, skips - 1) / 2)  # 1 пропуск — случайность, 3 — место не нравится


def score(c: dict, cands: list[dict], weights: dict, skipped_hotels: list[str], recent_dests: list[str],
          named: str | None = None, dest_stats: dict | None = None) -> dict:
    f = features_in_context(c, cands)
    contrib = {k: weights.get(k, 0) * f[k] for k in FEATURES}
    base = sum(contrib.values())
    sim = max((_cos(_centered(f), _centered(hotel_features(s))) for s in skipped_hotels), default=0.0)
    sim = max(0.0, sim)
    dest = HOTELS[c["hotel_id"]]["dest"]
    novelty = 1 - (recent_dests.count(dest) / len(recent_dests)) if recent_dests else 1.0
    total = base - SIM_K * sim + NOVELTY_K * novelty + dest_affinity(dest_stats, dest)
    if len(recent_dests) >= 2 and recent_dests[-1] == recent_dests[-2] == dest:
        total -= 1.0  # третий подряд из того же места — только если других нет
    if named and c["hotel_id"] == named:  # отель назван в запросе — показываем первым
        total += 10
    norm = sum(abs(weights.get(k, 0)) for k in FEATURES) or 1
    match = max(1, min(99, round(50 + 50 * base / norm)))
    return {"score": round(total, 4), "match": match, "contrib": contrib, "features": f, "sim": round(sim, 3), "novelty": round(novelty, 2)}


def rank(cands: list[dict], state: dict, exclude: set[str] = frozenset(), n: int | None = None) -> list[dict]:
    weights = state["weights"]
    skipped = [h for h in (hotel_by_video(v) for v in state.get("skipped", [])) if h]
    recent = [HOTELS[h]["dest"] for h in (hotel_by_video(v) for v in state.get("served", [])[-4:]) if h]
    pool = [c for c in cands if c["hotel_id"] not in exclude]
    out = []
    while pool and (n is None or len(out) < n):
        scored = [(score(c, cands, weights, skipped, recent, state["hard"].get("hotel_id"), state.get("dest_stats")), c) for c in pool]
        s, best = max(scored, key=lambda x: x[0]["score"])
        out.append({**best, **s})
        pool.remove(best)
        recent = (recent + [HOTELS[best["hotel_id"]]["dest"]])[-4:]
    return out


# ---------------------------------------------------------------- свайпы

def apply_swipe(state: dict, video_id: str, direction: str) -> dict:
    hid = hotel_by_video(video_id)
    f = hotel_features(hid)
    step = {"right": 0.2, "left": -0.1, "watched": 0.05}[direction]
    sw = state.setdefault("swipe_delta", {})
    for k in FEATURES:
        if k == "quality":
            continue
        d = step * (f[k] - 0.5)
        state["weights"][k] = round(max(-1.0, min(1.0, state["weights"].get(k, 0) + d)), 3)
        sw[k] = round(sw.get(k, 0) + d, 3)
        if abs(sw[k]) >= 0.15 and state["source"].get(k) not in ("correction",):
            state["source"][k] = "swipes"
    if direction in ("right", "left"):
        ds = state.setdefault("dest_stats", {}).setdefault(HOTELS[hid]["dest"], {"like": 0, "skip": 0})
        ds["like" if direction == "right" else "skip"] += 1
        state["skip_streak"] = 0 if direction == "right" else state.get("skip_streak", 0) + 1
    if direction == "right":
        if video_id not in state["liked"]:
            state["liked"].append(video_id)
        state["likes_since_shortlist"] = state.get("likes_since_shortlist", 0) + 1
    elif direction == "left":
        if video_id not in state["skipped"]:
            state["skipped"].append(video_id)
    if direction in ("right", "left"):
        state["swipes"] = state.get("swipes", 0) + 1
    show = state.get("likes_since_shortlist", 0) >= SHORTLIST_EVERY
    return {"show_shortlist": show}


def priorities(state: dict, top: int = 6) -> list[dict]:
    from .parser import DEFAULT_WEIGHTS
    items = []
    for k in FEATURES:
        w = state["weights"].get(k, 0)
        src = state["source"].get(k, "default")
        if k == "quality" and src == "default":
            continue
        if src == "default" and abs(w - DEFAULT_WEIGHTS.get(k, 0)) < 0.05:
            continue
        if abs(w) < 0.12:
            continue
        if src == "default":
            src = "swipes"  # отличается от стартового значения — значит, сдвинули свайпы
        items.append({"key": k, "label": FEATURE_LABELS[k], "weight": round(w, 2), "source": src,
                      "direction": "важно" if w > 0 else "лучше избегать"})
    items.sort(key=lambda x: -abs(x["weight"]))
    return items[:top]


def liked_hotels(state: dict) -> list[str]:
    seen, out = set(), []
    for v in state.get("liked", []):
        h = hotel_by_video(v)
        if h and h not in seen:
            seen.add(h)
            out.append(h)
    return out


def served_hotels(state: dict) -> set[str]:
    return {h for h in (hotel_by_video(v) for v in state.get("served", [])) if h}


def video_for_feed(hotel_id: str) -> dict:
    return primary_video(hotel_id)


def local_tz(place: str) -> int:
    return TZ.get(place, 3)
