"""voiceover и explain: тексты строятся из данных (шаблоны), LLM не обязательна.

Каждое утверждение опирается на конкретное поле: признак, цену, отзыв, погоду —
поэтому объяснение нельзя «нагаллюцинировать».
"""
from __future__ import annotations

from datetime import date

from ..catalog import DESTINATIONS, FEATURE_LABELS, HOTELS, ORIGINS
from .parser import MONTH_GEN, fmt_range, plural

TRIP_TAG_LABELS = {"warm_sea": "тёплое море", "sea": "море", "mountains": "горы", "nature": "природа",
                   "city": "город", "abroad": "за границу", "russia": "по России"}


def rub(n: int | None) -> str:
    return "—" if n is None else f"{int(n):,}".replace(",", " ") + " ₽"


def dur(minutes: int | None) -> str:
    if minutes is None:
        return ""
    h, m = divmod(int(minutes), 60)
    return f"{h} ч {m:02d} мин" if h else f"{m} мин"


def guests_label(adults: int, children_ages: list[int]) -> str:
    s = f"{adults} {plural(adults, 'взрослый', 'взрослых', 'взрослых')}"
    if children_ages:
        if len(children_ages) == 1:
            s += f" + ребёнок {children_ages[0]} {plural(children_ages[0], 'год', 'года', 'лет')}"
        else:
            s += f" + {len(children_ages)} детей ({', '.join(map(str, children_ages))})"
    return s


def _speak_price(n: int) -> str:
    if n >= 1000:
        k = round(n / 1000)
        return f"{k} {plural(k, 'тысяча', 'тысячи', 'тысяч')} рублей"
    return f"{n} рублей"


FEATURE_PHRASE = {
    "beach_line_1": lambda h: "первая линия" if h["beach_line"] == 1 else ("до моря пара минут" if h["beach_line"] == 2 else ""),
    "all_inclusive": lambda h: {"AI": "всё включено", "FB": "полный пансион", "HB": "полупансион"}.get(h["meal"], ""),
    "kids_club": lambda h: "детский клуб" if h["kids_club"] else "",
    "spa": lambda h: "спа" if h["spa"] else "",
    "quiet": lambda h: "тихо и спокойно" if h["vibe"] == "quiet" else "",
    "active": lambda h: "много активностей" if h["vibe"] == "active" else "",
    "center": lambda h: "в самом центре" if h["center"] >= 0.8 else "",
    "nature": lambda h: "вокруг природа" if h["nature"] >= 0.7 else "",
    "stars_5": lambda h: "пять звёзд" if h["stars"] == 5 else "",
    "free_cancel": lambda h: "",
    "budget": lambda h: "",
    "quality": lambda h: f"рейтинг {h['rating']}" if h["rating"] >= 9 else "",
}


def top_matches(c: dict, weights: dict, k: int = 2) -> list[str]:
    h = HOTELS[c["hotel_id"]]
    contrib = c.get("contrib") or {}
    keys = sorted(contrib, key=lambda x: -contrib[x])
    out = []
    for key in keys:
        if weights.get(key, 0) <= 0.1 or contrib[key] <= 0.05:
            continue
        ph = FEATURE_PHRASE[key](h)
        if ph and ph not in out:
            out.append(ph)
        if len(out) >= k:
            break
    if not out:
        for key in ("beach_line_1", "all_inclusive", "kids_club", "spa", "center", "nature"):
            ph = FEATURE_PHRASE[key](h)
            if ph:
                out.append(ph)
            if len(out) >= k:
                break
    return out


def voiceover(c: dict, state: dict) -> str:
    """Текст на 15–20 секунд (≤ 50 слов)."""
    h = HOTELS[c["hotel_id"]]
    d = DESTINATIONS[h["dest"]]
    hard = state["hard"]
    p = c["price"]
    stars = {5: "пять звёзд", 4: "четыре звезды", 3: "три звезды"}.get(h["stars"], "")
    parts = [f"{d['name']}, {h['name']}, {stars}."]
    m = top_matches(c, state["weights"])
    if m:
        parts.append((", ".join(m)).capitalize() + ".")
    a, b = date.fromisoformat(p["date_from"]), date.fromisoformat(p["date_to"])
    when = f"С {a.day} по {b.day} {MONTH_GEN[b.month - 1]}"
    who = "на всех" if hard["adults"] + len(hard["children_ages"]) > 1 else "за вас"
    parts.append(f"{when} — {_speak_price(p['total_rub'])} {who}, {p['meal_name']}.")
    if c.get("package_est") and c["transport"].get("price"):
        parts.append(f"С дорогой выйдет примерно {_speak_price(c['package_est'])}.")
    parts.append("Бесплатная отмена." if p["cancel"]["free"] else "Тариф невозвратный.")
    pro = h["pros"][0][0] if h["pros"] else None
    con = h["cons"][0][0] if h["cons"] else None
    if pro:
        parts.append(f"Гости хвалят {pro}" + (f", минус — {con}." if con else "."))
    text = " ".join(parts)
    words = text.split()
    if len(words) > 50:
        text = " ".join(words[:50]).rstrip(",") + "…"
    return text


def chips_for_hotel(c: dict) -> list[str]:
    h = HOTELS[c["hotel_id"]]
    out = []
    if h["beach_line"] == 1:
        out.append("1-я линия")
    elif h["beach_line"] == 2:
        out.append("2-я линия")
    out.append(c["price"]["meal_name"])
    if h["kids_club"]:
        out.append("детский клуб")
    if h["spa"]:
        out.append("спа")
    if h["pets"]:
        out.append("можно с питомцем")
    out.append("бесплатная отмена" if c["price"]["cancel"]["free"] else "невозвратный")
    return out


def explain(c: dict, state: dict, others: list[dict], weather: dict | None = None) -> dict:
    """why / tradeoffs / risks по 1–2 предложения + бейджи сравнения."""
    h = HOTELS[c["hotel_id"]]
    hard = state["hard"]
    w = state["weights"]
    f = c.get("features") or {}
    p = c["price"]
    pkg = c.get("package_est") or p["total_rub"]

    why = []
    m = top_matches(c, w)
    liked_src = any(state["source"].get(k) == "swipes" for k in (c.get("contrib") or {}) if (c.get("contrib") or {}).get(k, 0) > 0.05)
    if m:
        why.append(("Совпадает с тем, что вы лайкали: " if liked_src else "Есть то, что вы просили: ") + ", ".join(m) + ".")
    budget = hard.get("budget_total_rub")
    if budget and pkg <= budget:
        why.append(f"Поездка целиком ≈ {rub(pkg)} — в бюджете {rub(budget)}, остаётся {rub(budget - pkg)}.")
    elif others and pkg == min((o.get("package_est") or o["price"]["total_rub"]) for o in others + [c]):
        why.append(f"Самый выгодный вариант в подборке: ≈ {rub(pkg)} за всё.")
    tr = c.get("transport") or {}
    if hard.get("max_travel_hours") and tr.get("minutes"):
        why.append(f"В пути {dur(tr['minutes'])} — в пределах ваших {hard['max_travel_hours']} ч.")
    if weather and weather.get("water") and "sea" in DESTINATIONS[h["dest"]]["tags"] and weather["water"] >= 21:
        why.append(f"Вода около +{weather['water']}°, воздух +{weather['air']}°.")

    trade = []
    for key, phrase in (("beach_line_1", "не первая линия — до пляжа идти" if h["beach_line"] in (2, 3) else "нет своего пляжа"),
                        ("all_inclusive", f"питание: {p['meal_name']}, остальное за свой счёт"),
                        ("kids_club", "нет детского клуба"),
                        ("quiet", "бывает шумно"),
                        ("active", "мало активностей"),
                        ("spa", "нет спа"),
                        ("center", "далеко от центра"),
                        ("nature", "городская локация"),
                        ("stars_5", f"{h['stars']}★, а не 5★"),
                        ("free_cancel", "отмена платная" if p["cancel"]["free"] is False else "")):
        if w.get(key, 0) >= 0.3 and f.get(key, 1) < 0.5 and phrase:
            trade.append(phrase)
    if others:
        cheapest = min(others + [c], key=lambda o: o.get("package_est") or o["price"]["total_rub"])
        cp = cheapest.get("package_est") or cheapest["price"]["total_rub"]
        if cheapest["hotel_id"] != c["hotel_id"] and pkg - cp > 3000:
            trade.append(f"на {rub(pkg - cp)} дороже, чем {HOTELS[cheapest['hotel_id']]['name']}")
    if budget and pkg > budget:
        trade.append(f"выше бюджета на {rub(pkg - budget)}")
    if tr.get("mode") == "flight" and tr.get("minutes", 0) > 300:
        trade.append(f"перелёт {dur(tr['minutes'])}")

    risks = []
    if not p["cancel"]["free"]:
        risks.append("тариф невозвратный — если планы поменяются, деньги не вернут")
    for con, share in h["cons"]:
        if share >= 0.2:
            risks.append(f"{round(share * 100)}% отзывов: {con}")
            break
    if p["rooms_left"] <= 2:
        risks.append(f"мест почти нет: {p['rooms_left']} {plural(p['rooms_left'], 'номер', 'номера', 'номеров')} на эти даты")
    if weather and weather.get("rainy_share", 0) >= 0.35:
        risks.append(f"дожди примерно в {round(weather['rainy_share'] * 100)}% дней")
    if "Египет" == DESTINATIONS[h["dest"]]["country"]:
        risks.append("виза по прибытии, 25 $")
    if c.get("transfer_est", 0) > 6000:
        risks.append("дорогой и долгий трансфер")

    return {
        "why": " ".join(why[:2]) or f"Хороший баланс цены и рейтинга ({h['rating']}).",
        "tradeoffs": ("Чем жертвуем: " + "; ".join(trade[:2]) + ".") if trade else "Серьёзных компромиссов нет.",
        "risks": ("Риски: " + "; ".join(risks[:2]) + ".") if risks else "Явных рисков не видно.",
    }


def comparison_badges(items: list[dict]) -> dict[str, list[str]]:
    badges: dict[str, list[str]] = {i["hotel_id"]: [] for i in items}
    if len(items) < 2:
        return badges
    cheapest = min(items, key=lambda o: o.get("package_est") or o["price"]["total_rub"])
    badges[cheapest["hotel_id"]].append("самый выгодный")
    best = max(items, key=lambda o: HOTELS[o["hotel_id"]]["rating"])
    badges[best["hotel_id"]].append("лучший рейтинг")
    top = max(items, key=lambda o: o.get("match", 0))
    badges[top["hotel_id"]].append("лучше всего совпадает")
    flex = [o for o in items if o["price"]["cancel"]["free"]]
    if flex and len(flex) < len(items):
        far = max(flex, key=lambda o: o["price"]["cancel"]["deadline"] or "")
        badges[far["hotel_id"]].append("гибкая отмена")
    return badges


def understood_chips(state: dict) -> list[dict]:
    hard, src = state["hard"], state["source"]
    chips = []
    nights = hard["nights"]
    label = f"{fmt_range(hard['date_from'], hard['date_to'])} · {nights} {plural(nights, 'ночь', 'ночи', 'ночей')}"
    if hard.get("flexible_days"):
        label += f" (±{hard['flexible_days']} дн)"
    chips.append({"key": "dates", "icon": "📅", "label": label, "source": src.get("dates", "default") if src.get("nights") != "default" else "default"})
    chips.append({"key": "guests", "icon": "👥", "label": guests_label(hard["adults"], hard["children_ages"]),
                  "source": "memory" if src.get("adults") == "memory" else "default" if "default" in (src.get("adults"), src.get("children_ages")) else "text"})
    if hard.get("budget_total_rub"):
        chips.append({"key": "budget", "icon": "💰", "label": f"до {rub(hard['budget_total_rub'])}", "source": src.get("budget_total_rub", "text")})
    elif hard.get("budget_night_rub"):
        chips.append({"key": "budget", "icon": "💰", "label": f"до {rub(hard['budget_night_rub'])} за ночь", "source": "text"})
    elif state["weights"].get("budget", 0) >= 0.5:
        chips.append({"key": "budget", "icon": "💰", "label": "недорого", "source": "text"})
    o = ORIGINS.get(hard["origin_city"])
    chips.append({"key": "origin", "icon": "🛫", "label": f"из {o['gen']}" if o else hard["origin_city"], "source": src.get("origin_city", "default")})
    if hard.get("hotel_id"):
        chips.append({"key": "hotel", "icon": "🏨", "label": HOTELS[hard["hotel_id"]]["name"], "source": "text"})
    elif hard.get("destination"):
        chips.append({"key": "destination", "icon": "📍", "label": DESTINATIONS[hard["destination"]]["name"], "source": src.get("destination", "text")})
    elif hard.get("destinations_allowed"):
        chips.append({"key": "destination", "icon": "📍", "label": " или ".join(DESTINATIONS[d]["name"] for d in hard["destinations_allowed"]), "source": "text"})
    elif hard.get("trip_tags"):
        chips.append({"key": "destination", "icon": "🧭", "label": ", ".join(TRIP_TAG_LABELS[t] for t in hard["trip_tags"]), "source": "text"})
    else:
        chips.append({"key": "destination", "icon": "🧭", "label": "куда — подскажу", "source": "default"})
    for c in hard.get("constraints", []):
        chips.append({"key": "constraint", "icon": "⚠️", "label": c, "source": "text"})
    for k, v in state["weights"].items():
        if src.get(k) == "text" and v >= 0.5 and k not in ("budget",):
            chips.append({"key": f"w_{k}", "icon": "✨", "label": FEATURE_LABELS[k], "source": "text"})
        elif src.get(k) == "text" and v <= -0.3:
            chips.append({"key": f"w_{k}", "icon": "🚫", "label": f"без: {FEATURE_LABELS[k]}", "source": "text"})
    return chips
