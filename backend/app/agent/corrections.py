"""apply_correction / anything_else: фраза → дельта профиля → затронутые компоненты.

«На два дня позже» меняет только даты → пересчитываются цена отеля, билеты и
трансфер, а отель, номер и веса остаются. «С нами едет собака» добавляет фильтр
и меняет отель, только если текущий не пускает животных.
"""
from __future__ import annotations

import re
from datetime import date, timedelta

from ..catalog import COUNTRY_SYNONYMS, DEST_SYNONYMS, DESTINATIONS, FEATURE_LABELS, HOTELS, ORIGINS
from .explain import guests_label, rub
from .parser import (NUM, negated_dest, find_destinations, find_hotel, find_origin, find_room, fmt_range, norm, num, parse_budget,
                     parse_constraints, parse_dates, parse_guests, parse_trip_type, parse_weights)

# какое поле профиля → какие компоненты пакета пересчитывать
AFFECTS = {
    "date_from": {"hotel", "transport", "transfer"},
    "date_to": {"hotel", "transport", "transfer"},
    "adults": {"hotel", "transport", "transfer"},
    "children_ages": {"hotel", "transport", "transfer"},
    "origin_city": {"transport", "transfer"},
    "transport": {"transport", "transfer"},
    "direct_only": {"transport", "transfer"},
    "max_travel_hours": {"transport", "transfer"},
    "flight_pref": {"transport_pick"},
    "room_category": {"hotel"},
    "pets": {"hotel_check"},
    "min_stars": {"hotel_check"},
    "budget_total_rub": {"budget_check"},
    "budget_night_rub": {"budget_check"},
    "destination": {"hotel", "transport", "transfer"},
    "trip_tags": {"hotel_check"},
    "exclude_dests": {"hotel_check"},
    "hotel_id": {"hotel", "transport", "transfer"},
}

# пожелания, которые агент передаёт отелю/перевозчику (не меняют поиск)
SPECIAL_REQUESTS = [
    (r"ранн\w* заезд|ранн\w* заселен|заселиться пораньше|приедем рано|заехать пораньше", "ранний заезд"),
    (r"поздн\w* выезд|выехать попозже|выезд попозже", "поздний выезд"),
    (r"кроватк", "детская кроватка в номер"),
    (r"табличк|встретить в аэропорту|встреча в аэропорту", "встреча с табличкой"),
    (r"тих\w* номер|высок\w* этаж|этаж повыше|не у лифта", "тихий номер повыше"),
    (r"годовщин|день рождени|медов\w* месяц|юбилей", "праздничный комплимент в номер"),
    (r"вегетариан|веган|аллерги|без глютен|халяль|кошер", "особое питание"),
    (r"двуспальн|раздельн\w* кроват|две кровати", "тип кроватей"),
    (r"коляск|инвалид|доступн\w* сред|без ступен", "доступная среда"),
    (r"детск\w* кресл", "детские кресла в трансфер"),
]

PERSON_ADULT = r"мам|пап|бабушк|дедушк|друг|подруг|теща|тещ|свекр|брат|сестр|муж|жена|коллег|взросл"
PERSON_CHILD = r"ребен|сын|доч|малыш|внук|племянн"


def _shift(t: str) -> int | None:
    m = re.search(r"на\s+" + NUM + r"?\s*(дн|день|дня|дней|недел\w*)\s+(позже|раньше|попозже|пораньше)", t) \
        or re.search(r"(?:позже|раньше|попозже|пораньше|сдвин\w*|перенес\w*)\s+на\s+" + NUM + r"?\s*(дн|день|дня|дней|недел\w*)", t)
    if not m:
        return None
    n = num(m.group(1)) if m.group(1) else 1
    days = n * (7 if m.group(2).startswith("недел") else 1)
    full = m.group(0)
    sign = -1 if re.search(r"раньше|пораньше", full) else 1
    if re.search(r"^(сдвин|перенес)", full) and not re.search(r"позже|раньше", full):
        sign = -1 if "раньше" in t else 1
    return sign * days


def _extend(t: str) -> int | None:
    m = re.search(r"на\s+" + NUM + r"?\s*(ноч\w*|дн|день|дня|дней)\s+(дольше|больше|длиннее|подольше|меньше|короче)", t)
    if m:
        n = num(m.group(1)) if m.group(1) else 1
        return -n if m.group(3) in ("меньше", "короче") else n
    m = re.search(r"(добав\w*|продл\w*|ещ[её])\s+" + NUM + r"?\s*(ноч\w*|дн|день|дня|дней)", t)
    if m:
        return num(m.group(2)) if m.group(2) else 1
    m = re.search(r"(сократ\w*|убер\w*|минус)\s+" + NUM + r"?\s*(ноч\w*|дн|день|дня|дней)", t)
    if m:
        return -(num(m.group(2)) if m.group(2) else 1)
    return None


def parse_correction(text: str, state: dict, today: date, context: str = "feed") -> dict:
    t = norm(text)
    hard = state["hard"]
    ch: dict = {}
    weights: dict = {}
    notes: list[str] = []
    flight_pref = None
    hotel_change = None
    exclude_dest = None

    # --- даты
    sh = _shift(t)
    ext = _extend(t)
    if sh:
        d1 = date.fromisoformat(hard["date_from"]) + timedelta(days=sh)
        d2 = date.fromisoformat(hard["date_to"]) + timedelta(days=sh)
        ch["date_from"], ch["date_to"] = d1.isoformat(), d2.isoformat()
        notes.append(f"сдвинул даты на {abs(sh)} дн {'позже' if sh > 0 else 'раньше'}: {fmt_range(ch['date_from'], ch['date_to'])}")
    elif ext:
        d2 = date.fromisoformat(hard["date_to"]) + timedelta(days=ext)
        if d2 > date.fromisoformat(hard["date_from"]):
            ch["date_to"] = d2.isoformat()
            notes.append(f"{'добавил' if ext > 0 else 'убрал'} {abs(ext)} ноч.: {fmt_range(hard['date_from'], ch['date_to'])}")
    else:
        dd = parse_dates(t, today)
        if dd and (dd.get("exact") or dd.get("month_only") or not dd.get("start_default")) and not dd.get("start_default"):
            ch["date_from"], ch["date_to"] = dd["date_from"], dd["date_to"]
            notes.append(f"новые даты: {fmt_range(dd['date_from'], dd['date_to'])}")
        elif dd and dd.get("start_default"):  # только длительность: «на неделю»
            n = (date.fromisoformat(dd["date_to"]) - date.fromisoformat(dd["date_from"])).days
            ch["date_to"] = (date.fromisoformat(hard["date_from"]) + timedelta(days=n)).isoformat()
            notes.append(f"{n} ноч.: {fmt_range(hard['date_from'], ch['date_to'])}")

    if re.search(r"даты не важн|когда угодно|в любое время|без разницы когда|не важно когда", t):
        ch["flexible_days"] = 7
        notes.append("даты гибкие — ищу на ближайшие недели, проверим перед оплатой")

    # --- бюджет
    b = parse_budget(t)
    if b and b["per"] != "night":
        pax = hard["adults"] + len(hard["children_ages"])
        ch["budget_total_rub"] = b["value"] * (pax if b["per"] == "person" else 1)
        notes.append(f"бюджет до {rub(ch['budget_total_rub'])}")
    elif b and b["per"] == "night":
        ch["budget_night_rub"] = b["value"]
        notes.append(f"до {rub(b['value'])} за ночь")
    elif re.search(r"бюджет\w* (меньше|поменьше|ниже)|дешевле|подешевле|сэконом|урез|сократ\w* бюджет|меньше денег|слишком дорого|дороговато", t) \
            and not (context == "package" and re.search(r"рейс|билет|перел|поезд", t)):
        cur = hard.get("budget_total_rub") or (state.get("package") or {}).get("total", {}).get("total_rub") or state.get("median_package")
        if cur:
            ch["budget_total_rub"] = int(round(cur * 0.8 / 1000) * 1000)
            notes.append(f"бюджет меньше: до {rub(ch['budget_total_rub'])}")
        weights["budget"] = max(state["weights"].get("budget", 0), 0.8)
    elif re.search(r"бюджет\w* (больше|побольше|выше)|можно дороже|подороже|не экономь", t):
        cur = hard.get("budget_total_rub")
        if cur:
            ch["budget_total_rub"] = int(round(cur * 1.25 / 1000) * 1000)
            notes.append(f"бюджет больше: до {rub(ch['budget_total_rub'])}")
        weights["budget"] = min(state["weights"].get("budget", 0), 0.0)

    # --- гости
    adults, kids = hard["adults"], list(hard["children_ages"])
    if re.search(r"с нами (едет|поедет|будет)|ещ[её] (едет|поедет|будет)|добав\w*|плюс|\+\s*\d?", t) and re.search(PERSON_ADULT + "|" + PERSON_CHILD, t):
        add_ad = len(re.findall(r"(?:" + PERSON_ADULT + r")\w*", t))
        add_kids = re.findall(r"(?:" + PERSON_CHILD + r")\w*(?:\s+(?:\w+\s+)?(\d{1,2})\s*(?:лет|года|год))?", t)
        if add_ad:
            adults += add_ad
        for age in add_kids:
            kids.append(int(age) if age else 7)
    elif re.search(r"взросл|вдвоем|втроем|вчетвером|один|одна|детьми|детей|ребенк|ребен|сын|доч|без детей", t):
        g = parse_guests(t)
        if re.search(r"без детей|детей не будет|дети не едут", t):
            kids = []
            if not re.search(r"взросл|вдвоем|втроем", t):
                g["adults"] = adults
        elif g["children_ages"]:
            kids = g["children_ages"]
        if not g.get("adults_default"):
            adults = g["adults"]
    if adults != hard["adults"]:
        ch["adults"] = adults
    if kids != list(hard["children_ages"]):
        ch["children_ages"] = kids
    if "adults" in ch or "children_ages" in ch:
        notes.append(f"гости: {guests_label(adults, kids)}")

    # --- питомцы
    if re.search(r"собак|собачк|пес\b|пса\b|щен|кошк|кот\b|кота\b|питом|животн", t):
        if re.search(r"без (собак|кошк|питом|животн)|не (едет|поедет) (собак|кошк)", t):
            if hard.get("pets"):
                ch["pets"] = False
                notes.append("без питомца")
        elif not hard.get("pets"):
            ch["pets"] = True
            notes.append("едем с питомцем — только отели, где можно с животными")

    # --- откуда / куда
    origin, span = find_origin(t)
    if origin and origin != hard["origin_city"]:
        ch["origin_city"] = origin
        notes.append(f"вылет из {ORIGINS[origin]['gen']}")
    t_dest = t[:span[0]] + " " + t[span[1]:] if span else t
    h = find_hotel(t)
    if h:
        hotel_change = h
        notes.append(f"отель: {HOTELS[h]['name']}")
    dests = find_destinations(t_dest)
    negated = [d for d in dests if negated_dest(t_dest, d)]
    if negated:  # «только не Турция», «не Анталья и не Хургада»
        dests = [d for d in dests if d not in negated]
        excl = sorted(set(hard.get("exclude_dests") or []) | set(negated))
        ch["exclude_dests"] = excl
        if hard.get("destination") in negated:
            ch["destination"] = None
        if hard.get("destinations_allowed"):
            ch["destinations_allowed"] = [d for d in hard["destinations_allowed"] if d not in negated]
        notes.append("убираю: " + ", ".join(DESTINATIONS[d]["name"] for d in negated))
    if dests and not h:
        if len(dests) == 1 and dests[0] != hard.get("destination"):
            ch["destination"] = dests[0]
            ch["destinations_allowed"] = []
            notes.append(f"направление: {DESTINATIONS[dests[0]]['name']}")
        elif len(dests) > 1:
            ch["destination"] = None
            ch["destinations_allowed"] = dests
            notes.append("направления: " + " или ".join(DESTINATIONS[d]["name"] for d in dests))
    elif re.search(r"друго\w* направлен|куда-нибудь ещ|не (хочу|нравится) (это|туда)|другую страну", t):
        exclude_dest = hard.get("destination")
        ch["destination"] = None
        notes.append("ищу другие направления")
    trip = parse_trip_type(t, None)
    if trip["tags"] and not dests and not h and not negated:
        ch["trip_tags"] = trip["tags"]
        ch["destination"] = None
        notes.append("тип отдыха: " + ", ".join(trip["tags"]))

    # --- транспорт и ограничения
    c = parse_constraints(t)
    if c.get("transport") and c["transport"] != hard.get("transport"):
        ch["transport"] = c["transport"]
        notes.append({"train": "поедем на поезде", "flight": "полетим самолётом", "car": "едем своим ходом"}[c["transport"]])
    if c.get("direct_only") and not hard.get("direct_only"):
        ch["direct_only"] = True
        notes.append("только прямые рейсы")
    if c.get("max_travel_hours") and c["max_travel_hours"] != hard.get("max_travel_hours"):
        ch["max_travel_hours"] = c["max_travel_hours"]
        notes.append(f"в пути не дольше {c['max_travel_hours']} ч")
    if c.get("min_stars") and c["min_stars"] != hard.get("min_stars"):
        ch["min_stars"] = c["min_stars"]
        notes.append(f"от {c['min_stars']}★")
    if re.search(r"рейс|билет|перел|поезд|вылет", t):
        if re.search(r"дешев|подешевле|эконом", t):
            flight_pref = "cheap"
            notes.append("самые дешёвые билеты")
            ch.pop("budget_total_rub", None)
            notes[:] = [n for n in notes if not n.startswith("бюджет меньше")]
            weights.pop("budget", None)
        elif re.search(r"быстр|побыстрее|короче|без пересад", t):
            flight_pref = "fast"
            notes.append("самые быстрые билеты")
        elif re.search(r"удобн|оптимальн", t):
            flight_pref = "optimal"
            notes.append("удобные по времени билеты")
        elif re.search(r"утр|пораньше", t):
            flight_pref = "fast"

    # --- номер
    room = find_room(t, hotel_change or (state.get("package") or {}).get("hotel", {}).get("hotel_id") or hard.get("hotel_id"))
    if room and re.search(r"номер|люкс|делюкс|сюит|коттедж|семейн|вид", t):
        ch["room_category"] = room
        notes.append(f"номер: {room}")

    # --- веса
    w, _ = parse_weights(t)
    for k, v in w.items():
        if k == "budget" and "budget" in weights:
            continue
        if k == "budget" and flight_pref == "cheap":
            continue
        weights[k] = v
    for k, v in weights.items():
        if k == "budget" and any(n.startswith("бюджет") for n in notes):
            continue
        if abs(v) < 0.05:
            notes.append(f"«{FEATURE_LABELS[k]}» больше не влияет на выбор")
        elif v > 0:
            notes.append(f"«{FEATURE_LABELS[k]}» теперь важно")
        else:
            notes.append(f"«{FEATURE_LABELS[k]}» — лучше избегать")

    requests = [label for rx, label in SPECIAL_REQUESTS if re.search(rx, t)]
    for r in requests:
        notes.append(f"передам отелю: {r}")

    affected: set[str] = set()
    for k in ch:
        affected |= AFFECTS.get(k, set())
    if flight_pref:
        affected |= AFFECTS["flight_pref"]
    if hotel_change:
        affected |= AFFECTS["hotel_id"]
    understood = bool(ch or weights or flight_pref or hotel_change or exclude_dest or requests)
    return {"hard": ch, "weights": weights, "requests": requests, "flight_pref": flight_pref, "hotel_change": hotel_change,
            "exclude_dest": exclude_dest, "notes": notes, "affected": affected, "understood": understood}


def apply_delta(state: dict, delta: dict) -> None:
    hard = state["hard"]
    for k, v in delta["hard"].items():
        hard[k] = v
        src_key = "dates" if k in ("date_from", "date_to") else k
        state["source"][src_key] = "correction"
    if "pets" in delta["hard"] and delta["hard"]["pets"]:
        if "с питомцем" not in hard["constraints"]:
            hard["constraints"].append("с питомцем")
    if "pets" in delta["hard"] and not delta["hard"]["pets"]:
        hard["constraints"] = [c for c in hard["constraints"] if c != "с питомцем"]
    for k, lab in (("transport", None), ("direct_only", "без пересадок")):
        if k == "direct_only" and delta["hard"].get(k) and lab not in hard["constraints"]:
            hard["constraints"].append(lab)
    if "max_travel_hours" in delta["hard"]:
        hard["constraints"] = [c for c in hard["constraints"] if not c.startswith("в пути не дольше")] + [f"в пути не дольше {hard['max_travel_hours']} ч"]
    if "transport" in delta["hard"]:
        hard["constraints"] = [c for c in hard["constraints"] if c not in ("на поезде", "своим ходом")]
        if hard["transport"] == "train":
            hard["constraints"].append("на поезде")
        elif hard["transport"] == "car":
            hard["constraints"].append("своим ходом")
    hard["nights"] = (date.fromisoformat(hard["date_to"]) - date.fromisoformat(hard["date_from"])).days
    if delta.get("exclude_dest"):
        excl = set(hard.get("exclude_dests") or []) | {delta["exclude_dest"]}
        hard["exclude_dests"] = sorted(excl)
    for k, v in delta["weights"].items():
        state["weights"][k] = v
        state["source"][k] = "correction"
    for r in delta.get("requests") or []:
        if r not in state.setdefault("requests", []):
            state["requests"].append(r)
    state.setdefault("history", []).append({"notes": delta["notes"], "affected": sorted(delta["affected"])})
