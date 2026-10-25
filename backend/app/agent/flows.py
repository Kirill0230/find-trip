"""Оркестрация агента: графы шагов по BPMN и операции, которые вызывает API.

intake      — разбор запроса → проверка противоречий → (уточнение | сразу пакет | поиск и лента)
correction  — правка → дельта → пересчёт только затронутого (лента / подборка / пакет)
package     — отель → билеты ∥ трансфер → состыковки → объяснение
"""
from __future__ import annotations

import os
import re
import statistics
import time
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from datetime import date, datetime

from ..catalog import DESTINATIONS, FEATURE_LABELS, HOTELS, ORIGINS, STOCK_CLIPS, hotel_by_video
from ..tools import start_tool_log, tool
from ..tools.booking import book as book_tool
from ..tools.connections import check_connections
from ..tools.weather import get_weather
from . import llm
from .corrections import apply_delta, parse_correction
from .explain import (TRIP_TAG_LABELS, chips_for_hotel, comparison_badges, dur, explain, rub, understood_chips,
                      voiceover)
from .graph import END, StateGraph
from .memory import recall
from .package import build_package, finalize, rebuild
from .parser import MONTH_PREP, parse_request, plural
from .ranking import (all_candidates, candidate, dest_allowed, hotel_passes, liked_hotels, priorities, rank,
                      reachability, served_hotels, transport_estimate, video_for_feed)

_VO_CACHE: dict = {}


def _today() -> date:
    """AGENT_TODAY фиксирует «сегодня» для воспроизводимых прогонов тестов."""
    v = os.getenv("AGENT_TODAY")
    return date.fromisoformat(v) if v else date.today()

_CANDS: dict = {}


def _hard_key(hard: dict) -> str:
    keys = ("date_from", "date_to", "adults", "children_ages", "origin_city", "destination", "destinations_allowed",
            "trip_tags", "transport", "max_travel_hours", "direct_only", "pets", "min_stars", "budget_total_rub",
            "budget_night_rub", "exclude_dests", "budget_soft", "room_category", "hotel_id")
    return repr([hard.get(k) for k in keys])


def candidates(state: dict) -> tuple[list[dict], dict]:
    key = (state["session_id"], _hard_key(state["hard"]))
    if key not in _CANDS:
        if len(_CANDS) > 500:
            _CANDS.clear()
        _CANDS[key] = all_candidates(state["hard"])
    return _CANDS[key]


@tool("search_hotels")
def search_hotels(state: dict) -> list[dict]:
    cands, _ = candidates(state)
    return cands


def ensure_weather(state: dict, dests: list[str]) -> None:
    w = state.setdefault("weather", {})
    need = [d for d in dict.fromkeys(dests) if d not in w]
    if not need:
        return
    hard = state["hard"]
    with ThreadPoolExecutor(max_workers=8) as ex:
        futs = {d: ex.submit(copy_context().run, get_weather, d, hard["date_from"], hard["date_to"]) for d in need}
        for d, f in futs.items():
            w[d] = f.result()


def new_state(session_id: str, text: str) -> dict:
    return {"session_id": session_id, "query": text, "created_at": time.time(), "mode": "new",
            "hard": {}, "weights": {}, "source": {}, "liked": [], "skipped": [], "served": [],
            "swipes": 0, "likes_since_shortlist": 0, "shortlists_shown": 0, "package": None, "order": None,
            "clarify": None, "destinations": [], "trace": [], "history": [],
            "metrics": {"questions": 0, "clarify_questions": 0, "corrections": [], "booked": False}}


# ================================================================ INTAKE

def n_parse(run):
    st = run["state"]
    p = parse_request(st["query"], run["today"])
    if llm.enabled():
        p = llm.merge(p, llm.llm_parse(st["query"]))
    st["hard"], st["weights"], st["source"] = p["hard"], p["weights"], p["source"]
    st["known_all"] = p["known_all"]
    st["unknown_place"] = p.get("unknown_place")
    st["llm_used"] = p.get("llm_used", False)


def _clarify(state: dict, question: str, options: list[tuple[str, str]], reason: str):
    state["clarify"] = {"question": question, "reason": reason,
                        "options": [{"label": l, "text": t} for l, t in options]}
    state["mode"] = "clarify"
    state["metrics"]["clarify_questions"] += 1


def n_validate(run):
    st = run["state"]
    hard = st["hard"]
    st["clarify"] = None
    st["mode"] = "searching"
    month = date.fromisoformat(hard["date_from"]).month
    if st.pop("unknown_place", None) and not hard.get("destination") and not hard.get("trip_tags"):
        m = re.search(r"(?:в|во|на)\s+([А-ЯЁ][а-яё\-]{2,})", st["query"])
        name = m.group(1) if m else "это место"
        _clarify(st, f"{name} пока нет в демо-каталоге (8 направлений). Подобрать похожее?",
                 [("Город на выходные: Петербург, Казань", "город"), ("Тёплое море", "тёплое море"),
                  ("Предложи что угодно", "покажи что есть")], "unknown_place")
        return
    if st["source"].get("dates") == "default" and not st.get("known_all") and not st.get("dates_asked"):
        # без дат цены и наличие — гадание; это единственное, что спрашиваем до ленты
        st["dates_asked"] = True
        nxt = MONTH_PREP[(date.fromisoformat(hard["date_from"]).month) % 12]
        _clarify(st, "Когда планируете поездку? От дат зависят цены, погода и наличие.",
                 [("Ближайшие выходные", "на выходные"), ("Через 2 недели, на неделю", "через две недели на неделю"),
                  (f"В {nxt}", f"в {nxt} на неделю"), ("Даты не важны", "даты не важны")], "no_dates")
        return
    if hard.get("destination"):
        r = reachability(hard["origin_city"], hard["destination"], hard)
        if not r["ok"]:
            dname = DESTINATIONS[hard["destination"]]["name"]
            opts = []
            if hard.get("transport") == "train":
                opts.append(("✈️ Полетим самолётом", "полетим самолётом"))
            if hard.get("max_travel_hours"):
                opts.append(("Можно дольше в пути", "в пути не больше 12 часов"))
            opts.append(("Другое направление", "давай другое направление"))
            _clarify(st, f"{dname}: {r['reason']}. Как поступим?", opts, "unreachable")
            return
    tags = set(hard.get("trip_tags") or [])
    if "warm_sea" in tags and not hard.get("destination") and not hard.get("destinations_allowed"):
        warm = [d for d in DESTINATIONS if dest_allowed(d, hard)[0]]
        if not warm:
            opts = []
            if hard.get("transport") == "train":
                opts.append(("✈️ Полетим самолётом", "полетим самолётом"))
            if hard.get("max_travel_hours"):
                opts.append(("Лететь до 7 ч — ок", "в пути не больше 7 часов"))
            opts.append(("Море в России, пусть прохладнее", "море в россии"))
            _clarify(st, f"Тёплого моря {('в ' + MONTH_PREP[month - 1]) if month else ''} с такими условиями не нашёл. Что важнее?", opts, "no_warm_sea")


def r_after_validate(run):
    st = run["state"]
    if st["mode"] == "clarify":
        return "clarify"
    return "known" if st.get("known_all") else "search"


def n_direct_package(run):
    st = run["state"]
    hard = st["hard"]
    st["package"] = build_package(st, hard["hotel_id"], hard.get("room_category"))
    st["mode"] = "checkout"


def n_search(run):
    st = run["state"]
    cands = search_hotels(st)
    run["cands"] = cands
    if cands:
        pk = [c["package_est"] for c in cands if c["package_est"]]
        st["median_package"] = int(statistics.median(pk)) if pk else None


def r_after_search(run):
    return "found" if run["cands"] else "empty"


def n_diagnose(run):
    """Ничего не нашлось — понять, какое условие мешает, и предложить варианты (а не анкету)."""
    st = run["state"]
    hard = st["hard"]
    _, rejected = candidates(st)
    opts: list[tuple[str, str]] = []
    reasons = [r["why"] for r in rejected.values()]
    budget = hard.get("budget_total_rub")
    if budget:
        over = [r["package_est"] for r in rejected.values() if r["why"] == "дороже бюджета" and r["package_est"]]
        if over:
            need = int(-(-min(over) // 1000) * 1000)
            opts.append((f"Поднять бюджет до {rub(need)}", f"бюджет до {need} рублей"))
            if hard["nights"] > 3:
                n = max(2, hard["nights"] - 2)
                opts.append((f"Сократить до {n} ночей", f"на {n} {plural(n, 'ночь', 'ночи', 'ночей')}"))
    if any("дольше" in r for r in reasons) and hard.get("max_travel_hours"):
        opts.append(("Можно дольше в пути", f"в пути не больше {hard['max_travel_hours'] + 3} часов"))
    if hard.get("transport") == "train" and any("поезд" in r for r in reasons):
        opts.append(("✈️ Полетим самолётом", "полетим самолётом"))
    if hard.get("min_stars"):
        opts.append((f"Можно {hard['min_stars'] - 1}★", f"от {hard['min_stars'] - 1} звезд"))
    if hard.get("destination") or hard.get("trip_tags"):
        opts.append(("Другие направления", "давай другое направление"))
    opts.append(("Покажи, что есть", "покажи что есть"))
    main = "в бюджет ничего не помещается" if budget and "дороже бюджета" in reasons else "под все условия ничего не подходит"
    _clarify(st, f"Честно: {main}. Что можно ослабить?", opts[:4], "empty")


def n_inspire(run):
    """Вдохновение: если направление не задано — карточки направлений с ценой, погодой и дорогой."""
    st = run["state"]
    hard = st["hard"]
    cands = run["cands"]
    by_dest: dict[str, list[dict]] = {}
    for c in cands:
        by_dest.setdefault(HOTELS[c["hotel_id"]]["dest"], []).append(c)
    ensure_weather(st, list(by_dest))
    ranked = rank(cands, st)
    best_score = {}
    for c in ranked:
        d = HOTELS[c["hotel_id"]]["dest"]
        best_score.setdefault(d, c["score"])
    cards = []
    for d, cs in by_dest.items():
        dd = DESTINATIONS[d]
        w = st["weather"].get(d) or {}
        tr = transport_estimate(hard, d)
        price_from = min(c["package_est"] or c["price"]["total_rub"] for c in cs)
        reasons = []
        if w.get("water") and "sea" in dd["tags"]:
            reasons.append(f"море +{w['water']}°")
        if w.get("air") is not None:
            reasons.append(f"воздух +{w['air']}°" if w["air"] > 0 else f"воздух {w['air']}°")
        mode_label = {"flight": "лететь", "train": "на поезде", "local": "вы уже здесь", "car": "своим ходом"}.get(tr.get("mode"), "")
        if tr.get("minutes"):
            reasons.append(f"{mode_label} {dur(tr['minutes'])}")
        if dd["visa"] != "не нужна":
            reasons.append(f"виза: {dd['visa']}")
        cards.append({"dest_id": d, "name": dd["name"], "country": dd["country"], "pitch": dd["pitch"],
                      "why": dd["pitch"] + " " + ", ".join(reasons[:3]).capitalize() + ".",
                      "price_from": price_from, "hotels": len(cs), "weather": w,
                      "travel": {"mode": tr.get("mode"), "minutes": tr.get("minutes"), "label": f"{mode_label} {dur(tr.get('minutes'))}".strip()},
                      "visa": dd["visa"], "score": best_score.get(d, 0),
                      "video": next((c["url"] for c in STOCK_CLIPS.values() if c["dest"] == d and not c.get("generic")), None)})
    cards.sort(key=lambda c: -c["score"])
    st["destinations"] = cards[:4] if not hard.get("destination") else cards[:1]
    st["mode"] = "feed"


def n_recall(run):
    st = run["state"]
    st["memory_used"] = recall(st, run.get("memory"))


def n_clarify_done(run):
    pass


_intake = (StateGraph("intake")
           .add_node("parse_request", n_parse, "llm" if llm.enabled() else "code", "Разбор запроса")
           .add_node("recall", n_recall, "code", "Память: что помню о пользователе")
           .add_node("validate", n_validate, "code", "Проверка противоречий")
           .add_node("ask_user", n_clarify_done, "code", "Уточняющий вопрос")
           .add_node("build_package", n_direct_package, "tool", "Сразу собрать пакет (всё известно)")
           .add_node("search_hotels", n_search, "tool", "Поиск по каталогу + фильтры")
           .add_node("diagnose", n_diagnose, "code", "Что мешает? Варианты ослабить")
           .add_node("inspire", n_inspire, "tool", "Направления: цена, погода, дорога")
           .set_entry_point("parse_request")
           .add_edge("parse_request", "recall")
           .add_edge("recall", "validate")
           .add_conditional_edges("validate", r_after_validate, {"clarify": "ask_user", "known": "build_package", "search": "search_hotels"})
           .add_conditional_edges("search_hotels", r_after_search, {"found": "inspire", "empty": "diagnose"})
           .add_edge("diagnose", "ask_user")
           .add_edge("ask_user", END).add_edge("build_package", END).add_edge("inspire", END)
           .compile())

# повторный поиск после ответа на уточнение / правки в режиме уточнения
_research = (StateGraph("research")
             .add_node("validate", n_validate, "code", "Проверка противоречий")
             .add_node("ask_user", n_clarify_done, "code", "Уточняющий вопрос")
             .add_node("search_hotels", n_search, "tool", "Поиск по каталогу + фильтры")
             .add_node("diagnose", n_diagnose, "code", "Что мешает? Варианты ослабить")
             .add_node("inspire", n_inspire, "tool", "Направления: цена, погода, дорога")
             .set_entry_point("validate")
             .add_conditional_edges("validate", lambda r: "clarify" if r["state"]["mode"] == "clarify" else "search",
                                    {"clarify": "ask_user", "search": "search_hotels"})
             .add_conditional_edges("search_hotels", r_after_search, {"found": "inspire", "empty": "diagnose"})
             .add_edge("diagnose", "ask_user")
             .compile())


def intake(session_id: str, text: str, today: date | None = None, memory: dict | None = None, user_id: str | None = None) -> dict:
    st = new_state(session_id, text)
    st["user_id"] = user_id
    tools = start_tool_log()
    run = _intake.invoke({"state": st, "today": today or _today(), "memory": memory})
    st["trace"] = run["trace"]
    st["metrics"]["t_intake_ms"] = round(sum(t["ms"] for t in run["trace"]), 1)
    st["metrics"]["intake_tools"] = [t["tool"] for t in tools]
    return st


def profile_view(st: dict) -> dict:
    return {"session_id": st["session_id"], "hard": st["hard"], "weights": st["weights"], "source": st["source"],
            "liked": st["liked"], "skipped": st["skipped"], "cart": st["package"]["id"] if st.get("package") else None}


def session_view(st: dict) -> dict:
    return {"session_id": st["session_id"], "mode": st["mode"], "profile": profile_view(st), "chips": understood_chips(st),
            "clarify": st.get("clarify"), "destinations": st.get("destinations", []), "priorities": priorities(st),
            "package": st.get("package"), "trace": st.get("trace", [])[-12:], "llm_used": st.get("llm_used", False),
            "likes": len(st["liked"]), "likes_since_shortlist": st.get("likes_since_shortlist", 0),
            "memory_used": st.get("memory_used", [])}


# ================================================================ FEED / SWIPE

def feed_item(st: dict, c: dict) -> dict:
    h = HOTELS[c["hotel_id"]]
    v = video_for_feed(c["hotel_id"])
    hard = st["hard"]
    key = (v["video_id"], hard["date_from"], hard["date_to"], hard["adults"], tuple(hard["children_ages"]), hard["origin_city"])
    if key not in _VO_CACHE:
        _VO_CACHE[key] = voiceover(c, st)
    text = _VO_CACHE[key]
    w = st.get("weather", {}).get(h["dest"])
    tr = c["transport"]
    return {
        "video": {k: v[k] for k in ("video_id", "hotel_id", "room_category", "kind", "source", "url", "playlist", "credits", "duration_sec")},
        "hotel": {"id": h["id"], "name": h["name"], "stars": h["stars"], "area": h["area"], "dest": h["dest"],
                  "dest_name": DESTINATIONS[h["dest"]]["name"], "country": DESTINATIONS[h["dest"]]["country"],
                  "rating": h["rating"], "reviews": h["reviews"], "desc": h["desc"], "vibe": h["vibe"],
                  "beach_line": h["beach_line"], "tags": DESTINATIONS[h["dest"]]["tags"],
                  "pros": h["pros"], "cons": h["cons"]},
        "price": {"total_rub": c["price"]["total_rub"], "per_night_rub": c["price"]["per_night_rub"], "nights": c["price"]["nights"],
                  "meal": c["price"]["meal_name"], "cancel": c["price"]["cancel"]["text"], "room": c["price"]["room_name"],
                  "package_est_rub": c["package_est"]},
        "transport": {"mode": tr.get("mode"), "minutes": tr.get("minutes"), "label": dur(tr.get("minutes")) if tr.get("minutes") else ""},
        "weather": w,
        "voiceover": text,
        "subtitles": [s.strip() for s in text.replace("…", ".").split(".") if s.strip()],
        "chips": chips_for_hotel(c),
        "match": c.get("match"),
        "score": c.get("score"),
        "reasons": [FEATURE_LABELS[k] for k, val in sorted((c.get("contrib") or {}).items(), key=lambda x: -x[1])
                    if val > 0.08 and k not in ("quality",)][:3],
    }


def get_feed(st: dict, n: int = 3) -> dict:
    cands = search_hotels(st)
    shown = served_hotels(st)
    batch = rank(cands, st, exclude=shown, n=n)
    if not batch and cands:  # лента закончилась — по второму кругу, без лайкнутых/пропущенных
        decided = {hotel_by_video(v) for v in st["liked"] + st["skipped"]}
        st["served"] = [v for v in st["served"] if hotel_by_video(v) in decided]
        batch = rank(cands, st, exclude=decided, n=n)
    ensure_weather(st, [HOTELS[c["hotel_id"]]["dest"] for c in batch])
    items = [feed_item(st, c) for c in batch]
    for it in items:
        st["served"].append(it["video"]["video_id"])
    if "t_first_feed_ms" not in st["metrics"]:
        st["metrics"]["t_first_feed_ms"] = round((time.time() - st["created_at"]) * 1000)
    return {"items": items, "exhausted": not items, "total_candidates": len(cands)}


SKIP_STREAK_ASK = 5


def _streak_question(st: dict) -> dict:
    """После серии дизлайков агент не листает дальше вслепую, а спрашивает, что поменять."""
    hard = st["hard"]
    stats = st.get("dest_stats", {})
    disliked = sorted((d for d, x in stats.items() if x["skip"] >= 2 and not x["like"]), key=lambda d: -stats[d]["skip"])[:3]
    opts: list[tuple[str, str]] = []
    if hard.get("destination"):
        opts.append(("Другое направление", "давай другое направление"))
    elif disliked:
        probe = dict(hard, exclude_dests=sorted(set(hard.get("exclude_dests") or []) | set(disliked)))
        if any(dest_allowed(d, probe)[0] for d in DESTINATIONS):
            names = [DESTINATIONS[d]["name"] for d in disliked]
            opts.append((f"Не {', не '.join(names)}", "не " + " и не ".join(names)))
    opts.append(("Отели получше", "хочу отели получше, от 4 звезд"))
    opts.append(("Подешевле", "дешевле"))
    if {"sea", "warm_sea"} & set(hard.get("trip_tags") or []) or not hard.get("trip_tags"):
        opts.append(("Не море — город или горы", "хочу в город или в горы"))
    st["metrics"]["clarify_questions"] += 1
    return {"question": f"{st['skip_streak']} мимо подряд — похоже, это не то. Что поменять?",
            "options": [{"label": l, "text": t} for l, t in opts[:4]]}


def swipe(st: dict, video_id: str, direction: str) -> dict:
    from .ranking import apply_swipe
    res = apply_swipe(st, video_id, direction)
    if res["show_shortlist"] and "swipes_to_first_shortlist" not in st["metrics"]:
        st["metrics"]["swipes_to_first_shortlist"] = st["swipes"]
    ask = None
    if direction == "left" and st.get("skip_streak", 0) >= SKIP_STREAK_ASK:
        ask = _streak_question(st)
        st["skip_streak"] = 0
    return {"weights": st["weights"], "priorities": priorities(st), "show_shortlist": res["show_shortlist"],
            "likes": len(st["liked"]), "likes_since_shortlist": st["likes_since_shortlist"], "ask": ask,
            "dest_stats": {DESTINATIONS[d]["name"]: v for d, v in st.get("dest_stats", {}).items()}}


# ================================================================ SHORTLIST

def build_shortlist(st: dict, reprice_logged: bool = False) -> dict:
    cands = candidates(st)[0]  # пересортировка закэшированных кандидатов, без нового поиска
    ranked = {c["hotel_id"]: c for c in rank(cands, st)}
    liked = liked_hotels(st)
    items, removed = [], []
    for hid in liked:
        if reprice_logged:  # правка дат/гостей: пересчитываем цены только лайкнутых
            from ..tools.pricing import get_hotel_price
            get_hotel_price(hid, None, st["hard"]["date_from"], st["hard"]["date_to"], st["hard"]["adults"], st["hard"]["children_ages"])
        if hid in ranked:
            items.append({**ranked[hid], "agent_pick": False})
        else:
            c = candidate(st["hard"], hid)
            ok, why = hotel_passes(st["hard"], c)
            removed.append({"hotel_id": hid, "name": HOTELS[hid]["name"], "why": why})
    skipped = {hotel_by_video(v) for v in st["skipped"]}
    if len(items) < 3:
        for hid, c in ranked.items():
            if len(items) >= 3:
                break
            if hid not in liked and hid not in skipped:
                items.append({**c, "agent_pick": True})
    if not items:  # под условия подходит только то, что пользователь пропустил — честно показываем его
        items = [{**c, "agent_pick": True} for c in list(ranked.values())[:3]]
    items.sort(key=lambda c: (-c["score"]))
    items = items[:5]
    ensure_weather(st, [HOTELS[c["hotel_id"]]["dest"] for c in items])
    badges = comparison_badges(items)
    out = []
    for c in items:
        h = HOTELS[c["hotel_id"]]
        others = [o for o in items if o["hotel_id"] != c["hotel_id"]]
        out.append({**feed_item(st, c), "explanation": explain(c, st, others, st["weather"].get(h["dest"])),
                    "badges": badges.get(c["hotel_id"], []), "agent_pick": c["agent_pick"]})
    return {"items": out, "removed": removed, "priorities": priorities(st)}


def mark_shortlist_shown(st: dict):
    st["likes_since_shortlist"] = 0
    st["shortlists_shown"] += 1
    if st["mode"] in ("feed", "new"):
        st["mode"] = "shortlist"


# ================================================================ PACKAGE

def create_package(st: dict, hotel_id: str, room: str | None = None, on_step=None) -> dict:
    tools = start_tool_log()
    st["package"] = build_package(st, hotel_id, room, on_step=on_step)
    st["package"]["tool_calls"] = [t["tool"] for t in tools]
    st["mode"] = "checkout"
    return st["package"]


def patch_package(st: dict, patch: dict, today: date | None = None) -> dict:
    """Правки со страницы доуточнения: поля + «Что-нибудь ещё?»."""
    tools = start_tool_log()
    pkg = st["package"]
    hard = st["hard"]
    ch, notes = {}, []
    for k in ("adults", "date_from", "date_to", "origin_city", "children_ages"):
        if k in patch and patch[k] is not None and patch[k] != hard.get(k):
            ch[k] = patch[k]
    if "adults" in ch:
        notes.append(f"взрослых: {ch['adults']}")
    if "date_from" in ch or "date_to" in ch:
        notes.append("новые даты")
    if "origin_city" in ch:
        notes.append(f"вылет из {ORIGINS[ch['origin_city']]['gen']}")
    affected: set[str] = set()
    from .corrections import AFFECTS
    for k in ch:
        affected |= AFFECTS[k]
    delta = {"hard": ch, "weights": {}, "notes": notes, "affected": affected, "flight_pref": None,
             "hotel_change": None, "exclude_dest": None}
    if ch:
        apply_delta(st, delta)
    forced = dict(pkg.get("forced") or {})
    picks = set()
    if patch.get("flight_pref") and patch["flight_pref"] != pkg["transport"].get("pref"):
        pkg["transport"]["pref"] = patch["flight_pref"]
        picks.add("transport_pick")
        forced.pop("outbound_id", None)
        forced.pop("return_id", None)
        notes.append({"cheap": "дешёвый рейс", "fast": "быстрый рейс", "optimal": "удобный рейс"}[patch["flight_pref"]])
    for key in ("outbound_id", "return_id", "arrival_type", "departure_type"):
        if patch.get(key) and patch[key] != forced.get(key):
            forced[key] = patch[key]
            picks.add("transfer_pick" if key.endswith("_type") else "transport_pick")
            notes.append("выбран другой вариант")
    room = patch.get("room_category")
    if room and room != pkg["hotel"]["room_category"]:
        affected.add("hotel")
        notes.append("другой номер")
    pkg["forced"] = forced
    n_fields = len(ch) + len(picks) + (1 if "hotel" in affected and room else 0)
    st["metrics"]["questions"] += n_fields
    extra = None
    if patch.get("anything_else"):
        d2 = parse_correction(patch["anything_else"], st, today or _today(), context="package")
        if d2["understood"]:
            apply_delta(st, d2)
            affected |= d2["affected"]
            notes += d2["notes"]
            st["metrics"]["questions"] += 1
            if d2["flight_pref"]:
                pkg["transport"]["pref"] = d2["flight_pref"]
                picks.add("transport_pick")
            extra = {"understood": True, "notes": d2["notes"]}
        else:
            extra = {"understood": False, "notes": [], "message": "Не понял пожелание — уточните, например: «нужен ранний заезд», «на 2 дня позже», «рейс побыстрее»."}
    new_hotel = _hotel_replacement(st) if affected & {"hotel_check"} else None
    st["package"] = rebuild(st, (affected | picks) - {"hotel_check", "budget_check"}, hotel_id=new_hotel, room=room)
    st["package"]["tool_calls"] = [t["tool"] for t in tools]
    st["package"]["changes"] = notes
    st["package"]["anything_else"] = extra
    return st["package"]


def _hotel_replacement(st: dict) -> str | None:
    """Если текущий отель больше не проходит фильтры (например, «с собакой»), подобрать замену:
    сначала из лайкнутых, затем лучший по рейтингу ленты."""
    cur = st["package"]["hotel"]["hotel_id"]
    c = candidate(st["hard"], cur)
    ok, _ = hotel_passes(st["hard"], c)
    if ok:
        return None
    cands = search_hotels(st)
    ranked = rank(cands, st)
    liked = set(liked_hotels(st))
    for c in ranked:
        if c["hotel_id"] in liked:
            return c["hotel_id"]
    return ranked[0]["hotel_id"] if ranked else None


# ================================================================ CORRECTION

def correct(st: dict, text: str, context: str = "feed", today: date | None = None) -> dict:
    tools = start_tool_log()
    t0 = time.perf_counter()
    trace = []
    delta = parse_correction(text, st, today or _today(), context)
    trace.append({"graph": "correction", "node": "apply_correction", "label": "Разбор правки → дельта", "kind": "code", "ms": round((time.perf_counter() - t0) * 1000, 1)})
    if not delta["understood"]:
        if text.strip().lower() in ("покажи что есть", "покажи, что есть"):
            st["hard"]["budget_soft"] = True
            delta = {"hard": {"budget_soft": True}, "weights": {"budget": 0.8}, "notes": ["показываю ближайшее по цене"],
                     "affected": set(), "understood": True, "flight_pref": None, "hotel_change": None, "exclude_dest": None}
        else:
            return {"type": "ask", "understood": False,
                    "message": "Не понял правку. Можно так: «на 2 дня позже», «бюджет 100 тысяч», «с нами едет собака», «питание не важно», «рейс побыстрее»."}
    apply_delta(st, delta)
    if context == "package" and not st.get("package"):
        context = "feed"
    if st["mode"] == "clarify" or (context == "feed" and set(delta["hard"]) & {"destination", "trip_tags", "transport", "max_travel_hours", "budget_total_rub", "min_stars", "budget_soft", "destinations_allowed", "exclude_dests"} and not st["liked"]):
        run = _research.invoke({"state": st, "today": today or _today()})
        trace += run["trace"]
        result = {"type": "clarify" if st["mode"] == "clarify" else "feed", "session": session_view(st)}
    elif context in ("package", "checkout") and st.get("package"):
        t1 = time.perf_counter()
        new_hotel = delta["hotel_change"] or (_hotel_replacement(st) if delta["affected"] & {"hotel_check"} else None)
        if delta["flight_pref"]:
            st["package"]["transport"]["pref"] = delta["flight_pref"]
        aff = set(delta["affected"]) - {"hotel_check", "budget_check"}
        st["package"] = rebuild(st, aff, hotel_id=new_hotel, room=delta["hard"].get("room_category"))
        st["package"]["changes"] = delta["notes"] + ([f"отель заменён: {HOTELS[new_hotel]['name']} (прежний не подходит)"] if new_hotel and not delta["hotel_change"] else [])
        trace.append({"graph": "correction", "node": "rebuild_package", "label": "Пересборка затронутых компонентов", "kind": "tool", "ms": round((time.perf_counter() - t1) * 1000, 1)})
        result = {"type": "package", "package": st["package"]}
    elif context == "shortlist":
        t1 = time.perf_counter()
        sl = build_shortlist(st, reprice_logged=bool(delta["affected"] & {"hotel"}))
        trace.append({"graph": "correction", "node": "rerank_shortlist", "label": "Пересчёт подборки (лайки сохранены)", "kind": "code", "ms": round((time.perf_counter() - t1) * 1000, 1)})
        result = {"type": "shortlist", "shortlist": sl}
    else:
        result = {"type": "feed", "session": session_view(st)}
    calls = [t["tool"] for t in tools]
    rec = {"text": text, "context": context, "affected": sorted(delta["affected"]), "tool_calls": calls,
           "recomputed": (st["package"] or {}).get("recomputed") if result["type"] == "package" else None,
           "kept": (st["package"] or {}).get("kept") if result["type"] == "package" else None,
           "from_scratch": context in ("package", "shortlist") and "search_hotels" in calls and not (set(delta["hard"]) & {"destination", "destinations_allowed", "trip_tags"}) and not delta["affected"] & {"hotel_check"}}
    st["metrics"]["corrections"].append(rec)
    st["trace"] = (st.get("trace", []) + trace)[-30:]
    result.update({"understood": True, "from_scratch": rec["from_scratch"], "notes": delta["notes"], "affected": sorted(delta["affected"]), "tool_calls": calls,
                   "message": "Понял: " + "; ".join(delta["notes"]) + "." if delta["notes"] else "Понял.",
                   "priorities": priorities(st), "chips": understood_chips(st), "trace": trace})
    return result


# ================================================================ BOOK

class BookingConflict(Exception):
    def __init__(self, conflicts):
        self.conflicts = conflicts


def book(st: dict, guests: list[dict], contact: dict) -> dict:
    pkg = st["package"]
    conflicts = check_connections(pkg)
    errors = [c for c in conflicts if c["severity"] == "error"]
    if errors:
        pkg["conflicts"] = conflicts
        pkg["can_book"] = False
        st["mode"] = "checkout"
        raise BookingConflict(errors)
    order = book_tool(st["session_id"], pkg, guests, contact)
    order["timeline"] = pkg["timeline"]
    order["package"] = pkg
    st["order"] = order
    st["mode"] = "done"
    st["metrics"]["booked"] = True
    st["metrics"]["conflicts_at_booking"] = len(errors)
    st["metrics"]["t_to_book_ms"] = round((time.time() - st["created_at"]) * 1000)
    return order


def graphs_mermaid() -> dict:
    return {"intake": _intake.mermaid(), "research": _research.mermaid()}


# ================================================================ ПОСЛЕ ПОКУПКИ

TICKET_CHANGE_FEE = 2500  # ₽ за пассажира за плечо, тариф с багажом
EARLY_RX = r"ранн\w* заезд|заселиться пораньше|заехать раньше|ранн\w* заселен"
LATE_RX = r"поздн\w* выезд|выехать позже|выезд попозже"


def _order_fees(st: dict, old: dict, new: dict) -> tuple[int, list[str]]:
    """Штрафы за изменение оплаченного заказа (мок правил отеля и перевозчика)."""
    fee, why = 0, []
    pax = new["guests"]["adults"] + len(new["guests"]["children_ages"])
    h_old, h_new = old["hotel"], new["hotel"]
    if (h_old["date_from"], h_old["date_to"], h_old["hotel_id"]) != (h_new["date_from"], h_new["date_to"], h_new["hotel_id"]):
        free_until = h_old["cancel"].get("deadline")
        if not h_old["cancel"]["free"] or (free_until and _today().isoformat() > free_until):
            night = h_old["total_rub"] // max(1, h_old["nights"])
            fee += night
            why.append(f"отель: тариф без бесплатного изменения — штраф 1 ночь ({rub(night)})")
    for leg in ("outbound", "return"):
        a, b = (old.get("transport") or {}).get(leg), (new.get("transport") or {}).get(leg)
        if a and b and a["id"] != b["id"]:
            if a.get("baggage", True):
                fee += TICKET_CHANGE_FEE * pax
                why.append(f"обмен билета «{'туда' if leg == 'outbound' else 'обратно'}»: {rub(TICKET_CHANGE_FEE * pax)}")
            else:
                pen = a["price_total"] // 2
                fee += pen
                why.append(f"тариф без багажа не меняется — возврат 50%, штраф {rub(pen)}")
    return fee, why


def order_request(st: dict, text: str) -> dict:
    """Просьба по оплаченному заказу: ранний заезд, перенос дат, другой трансфер, гости."""
    import copy
    from .corrections import apply_delta as _apply
    from .parser import norm
    order = st["order"]
    t = norm(text)
    h = HOTELS[st["package"]["hotel"]["hotel_id"]]
    if re.search(EARLY_RX, t) or re.search(LATE_RX, t):
        early = bool(re.search(EARLY_RX, t))
        if (h.get("early_checkin") and early) or (not early and h["stars"] >= 4):
            status = "подтверждено отелем бесплатно (по наличию в день заезда)"
        else:
            night = st["package"]["hotel"]["per_night_rub"]
            status = f"возможно за доплату ~{rub(night // 2)} (50% ночи), оплата на месте"
        what = "ранний заезд" if early else "поздний выезд"
        order.setdefault("service_requests", []).append({"what": what, "status": status})
        return {"type": "info", "message": f"Запросил {what} в {h['name']}: {status}."}
    delta = parse_correction(text, st, _today(), context="package")
    if not delta["understood"]:
        return {"type": "ask", "message": "Не понял, что поменять в заказе. Например: «ранний заезд», «на день позже», «трансфер с табличкой», «с нами едет ещё ребёнок»."}
    trial = copy.deepcopy(st)
    _apply(trial, delta)
    if delta["flight_pref"]:
        trial["package"]["transport"]["pref"] = delta["flight_pref"]
    if re.search(r"табличк|индивидуальн", t):
        trial["package"].setdefault("forced", {}).update({"arrival_type": "individual", "departure_type": "individual"})
        delta["affected"] = set(delta["affected"]) | {"transfer_pick"}
    new_hotel = _hotel_replacement(trial) if delta["affected"] & {"hotel_check"} else None
    new = rebuild(trial, set(delta["affected"]) - {"hotel_check", "budget_check"}, hotel_id=new_hotel)
    fee, why = _order_fees(st, st["package"], new)
    diff = new["total"]["total_rub"] - st["package"]["total"]["total_rub"]  # разница стоимости поездки, штрафы — отдельно
    errors = [c for c in new["conflicts"] if c["severity"] == "error"]
    st["order_proposal"] = {"package": new, "hard": trial["hard"], "fee": fee, "diff": diff, "notes": delta["notes"]}
    pay = diff + fee
    msg = ("Доплата " + rub(pay)) if pay > 0 else ("Вернём " + rub(-pay)) if pay < 0 else "Без доплаты"
    return {"type": "proposal", "message": f"Понял: {'; '.join(delta['notes'])}. {msg}.",
            "proposal": {"notes": delta["notes"], "new_total": new["total"]["total_rub"], "paid": order["paid_rub"],
                         "old_total": st["package"]["total"]["total_rub"],
                         "diff": diff, "fee": fee, "fee_reasons": why, "to_pay": pay, "can_apply": not errors,
                         "conflicts": errors, "timeline": new["timeline"], "recomputed": new["recomputed"]}}


def order_confirm(st: dict) -> dict:
    prop = st.pop("order_proposal", None)
    if not prop:
        raise ValueError("нет предложения для подтверждения")
    order = st["order"]
    st["package"], st["hard"] = prop["package"], prop["hard"]
    order["paid_rub"] += prop["diff"] + prop["fee"]
    fresh = book_tool(st["session_id"], prop["package"], order["guests"], order["contact"])
    order.update({"vouchers": fresh["vouchers"], "timeline": prop["package"]["timeline"], "package": prop["package"]})
    order.setdefault("changes", []).append({"notes": prop["notes"], "paid_delta": prop["diff"] + prop["fee"], "fee": prop["fee"]})
    return order


def order_ics(st: dict) -> str:
    """Напоминания в календарь: события плана + онлайн-регистрация за сутки."""
    order = st["order"]
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//odin-zapros//RU", "CALSCALE:GREGORIAN"]
    for i, e in enumerate(order["timeline"]):
        start = e["at"].replace("-", "").replace(":", "") + "00"
        lines += ["BEGIN:VEVENT", f"UID:{order['order_id']}-{i}@odin-zapros", f"DTSTART:{start}",
                  f"SUMMARY:{e['title']}", f"DESCRIPTION:{e['detail']} · заказ {order['order_id']}"]
        if e["icon"] in ("✈️", "🚆"):
            lines += ["BEGIN:VALARM", "TRIGGER:-PT24H", "ACTION:DISPLAY", "DESCRIPTION:Онлайн-регистрация и документы", "END:VALARM",
                      "BEGIN:VALARM", "TRIGGER:-PT3H", "ACTION:DISPLAY", "DESCRIPTION:Пора выезжать", "END:VALARM"]
        lines.append("END:VEVENT")
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines)
