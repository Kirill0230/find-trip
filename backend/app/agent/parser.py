"""parse_request без LLM: разбор русского запроса в профиль поездки.

Правила покрывают даты (диапазоны, месяц, выходные, праздники, длительность),
бюджет (к/тыс/₽, на человека, за ночь), состав гостей и возраст детей, город
вылета, направление/тип отдыха, отель и номер, ограничения и «настроение».
Каждое значение помечается источником: text — из текста, default — по умолчанию.
"""
from __future__ import annotations

import calendar
import re
from datetime import date, timedelta

from ..catalog import COUNTRY_SYNONYMS, DEST_SYNONYMS, DESTINATIONS, HOTELS, ORIGIN_SYNONYMS, ROOM_TYPES

DEFAULT_WEIGHTS = {
    "beach_line_1": 0.2, "all_inclusive": 0.1, "stars_5": 0.1, "kids_club": 0.0,
    "active": 0.0, "quiet": 0.0, "free_cancel": 0.2, "center": 0.0, "nature": 0.0,
    "spa": 0.0, "budget": 0.2, "quality": 0.3,
}

MONTH_STEMS = [("январ", 1), ("феврал", 2), ("март", 3), ("апрел", 4), ("ма", 5), ("июн", 6), ("июл", 7),
               ("август", 8), ("сентябр", 9), ("октябр", 10), ("ноябр", 11), ("декабр", 12)]
MONTH_RE = r"(январ[а-я]*|феврал[а-я]*|март[а-я]*|апрел[а-я]*|ма[йяе](?![а-я])|июн[а-я]*|июл[а-я]*|август[а-я]*|сентябр[а-я]*|октябр[а-я]*|ноябр[а-я]*|декабр[а-я]*)"
MONTH_GEN = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября", "ноября", "декабря"]
MONTH_PREP = ["январе", "феврале", "марте", "апреле", "мае", "июне", "июле", "августе", "сентябре", "октябре", "ноябре", "декабре"]

NUM_WORDS = {
    "один": 1, "одна": 1, "одного": 1, "одну": 1, "два": 2, "две": 2, "двух": 2, "двое": 2, "три": 3, "трех": 3, "трое": 3,
    "четыре": 4, "четырех": 4, "четверо": 4, "пять": 5, "пяти": 5, "пятеро": 5, "шесть": 6, "шести": 6, "семь": 7, "семи": 7,
    "восемь": 8, "восьми": 8, "девять": 9, "десять": 10, "десяти": 10, "четырнадцать": 14, "пару": 2, "пара": 2,
    "одним": 1, "одной": 1, "двумя": 2, "тремя": 3, "четырьмя": 4, "пятью": 5,
}
NUM = r"(\d+|одним|двумя|тремя|четырьмя|пятью|один|одна|одного|одну|два|две|двух|двое|три|трех|трое|четыре|четырех|четверо|пять|пяти|пятеро|шесть|шести|семь|семи|восемь|восьми|девять|десять|десяти|четырнадцать|пару|пара)"


def num(s: str) -> int:
    return int(s) if s.isdigit() else NUM_WORDS.get(s, 0)


def norm(text: str) -> str:
    t = text.lower().replace("ё", "е").replace("—", "-").replace("–", "-").replace(" ", " ")
    return re.sub(r"\s+", " ", t).strip()


def month_num(word: str) -> int:
    for stem, n in MONTH_STEMS:
        if word.startswith(stem):
            return n
    return 0


def _mkdate(y: int, m: int, d: int) -> date:
    d = min(d, calendar.monthrange(y, m)[1])
    return date(y, m, d)


def _future(m: int, d: int, today: date, year: int | None = None) -> date:
    if year:
        return _mkdate(year, m, d)
    dt = _mkdate(today.year, m, d)
    if dt < today:
        dt = _mkdate(today.year + 1, m, d)
    return dt


def fmt_range(d1: str, d2: str) -> str:
    a, b = date.fromisoformat(d1), date.fromisoformat(d2)
    if a.month == b.month:
        return f"{a.day}–{b.day} {MONTH_GEN[a.month - 1]}"
    return f"{a.day} {MONTH_GEN[a.month - 1]} – {b.day} {MONTH_GEN[b.month - 1]}"


def plural(n: int, one: str, few: str, many: str) -> str:
    n10, n100 = n % 10, n % 100
    if n10 == 1 and n100 != 11:
        return one
    if 2 <= n10 <= 4 and not 12 <= n100 <= 14:
        return few
    return many


# ---------------------------------------------------------------- даты

def parse_duration(t: str) -> int | None:
    """Число ночей из «на неделю», «на 5 дней», «10 ночей»."""
    t = re.sub(r"через\s+(?:\S+\s+)?(недел\w*|месяц\w*|дн\w*|дня)", " ", t)  # «через две недели» — это старт, а не длительность
    if re.search(r"две недели|2 недели|двух недель", t):
        return 14
    if re.search(r"\bна неделю|\bнеделю\b|\bнеделя\b|на 7 дней", t):
        return 7
    m = re.search(NUM + r"\s*(ноч|ночей|ночи|суток|сутки|дн|дня|дней|день)", t)
    if m:
        n = num(m.group(1))
        unit = m.group(2)
        if n:
            return n if unit.startswith(("ноч", "сут")) else max(1, n - 1)
    if re.search(r"на выходные|выходны[ех]|уикенд|викенд", t):
        return 2
    return None


def parse_dates(t: str, today: date) -> dict:
    """→ {date_from, date_to, flexible_days, exact: bool} или {}."""
    dur = parse_duration(t)
    # 1) 10.11-17.11(.2026)
    m = re.search(r"(\d{1,2})\.(\d{1,2})(?:\.(\d{2,4}))?\s*(?:-|по|до)\s*(\d{1,2})\.(\d{1,2})(?:\.(\d{2,4}))?", t)
    if m:
        y1 = int(m.group(3)) if m.group(3) else None
        y2 = int(m.group(6)) if m.group(6) else None
        y1 = y1 + 2000 if y1 and y1 < 100 else y1
        y2 = y2 + 2000 if y2 and y2 < 100 else y2
        d1 = _future(int(m.group(2)), int(m.group(1)), today, y1)
        d2 = _mkdate(y2 or d1.year, int(m.group(5)), int(m.group(4)))
        if d2 <= d1:
            d2 = _mkdate(d2.year + 1, d2.month, d2.day)
        return {"date_from": d1.isoformat(), "date_to": d2.isoformat(), "flexible_days": 0, "exact": True}
    # 2) «с 10 по 17 ноября», «14-16 ноября», «с 28 ноября по 3 декабря»
    m = re.search(r"(?:с\s+)?(\d{1,2})(?:\s+" + MONTH_RE + r")?\s*(?:-|по|до)\s*(\d{1,2})\s+" + MONTH_RE, t)
    if m:
        dd1, mm1, dd2, mm2 = int(m.group(1)), m.group(2), int(m.group(3)), m.group(4)
        m2 = month_num(mm2)
        m1 = month_num(mm1) if mm1 else (m2 if dd1 <= dd2 else (m2 - 2) % 12 + 1)
        d1 = _future(m1, dd1, today)
        d2 = _mkdate(d1.year if m2 >= m1 else d1.year + 1, m2, dd2)
        if d2 <= d1:
            d2 = d1 + timedelta(days=dur or 7)
        return {"date_from": d1.isoformat(), "date_to": d2.isoformat(), "flexible_days": 0, "exact": True}
    # 3) одиночная дата «с 10 ноября», «10.11»
    m = re.search(r"(?:с\s+)?(\d{1,2})\s+" + MONTH_RE, t) or None
    single = None
    if m:
        single = _future(month_num(m.group(2)), int(m.group(1)), today)
    else:
        m = re.search(r"(?:с\s+)?(\d{1,2})\.(\d{1,2})(?!\d)", t)
        if m and 1 <= int(m.group(2)) <= 12:
            single = _future(int(m.group(2)), int(m.group(1)), today)
    if single:
        n = dur or 7
        return {"date_from": single.isoformat(), "date_to": (single + timedelta(days=n)).isoformat(),
                "flexible_days": 0, "exact": dur is not None, "nights_default": dur is None}
    # 4) праздники
    if re.search(r"нов(ый|ого|ом) год|новогодн|на нг\b", t):
        d1 = _future(12, 30, today)
        n = dur or 5
        return {"date_from": d1.isoformat(), "date_to": (d1 + timedelta(days=n)).isoformat(), "flexible_days": 1, "exact": False}
    if re.search(r"майски", t):
        d1 = _future(5, 1, today)
        n = dur or 4
        return {"date_from": d1.isoformat(), "date_to": (d1 + timedelta(days=n)).isoformat(), "flexible_days": 1, "exact": False}
    # 5) относительные: завтра, через неделю, выходные
    start = None
    if re.search(r"\bпослезавтра", t):
        start = today + timedelta(days=2)
    elif re.search(r"\bзавтра", t):
        start = today + timedelta(days=1)
    elif re.search(r"\bсегодня", t):
        start = today
    else:
        m = re.search(r"через\s+" + NUM + r"?\s*(недел|месяц|дн|дня|дней)", t)
        if m:
            n = num(m.group(1)) if m.group(1) else 1
            unit = m.group(2)
            start = today + timedelta(days=n * (7 if unit.startswith("недел") else 30 if unit.startswith("месяц") else 1))
    weekend = re.search(r"выходны[ех]|уикенд|викенд", t)
    if weekend and not start:
        nxt = "следующ" in t
        days_to_fri = (4 - today.weekday()) % 7
        if days_to_fri == 0 and today.weekday() != 4:
            days_to_fri = 7
        start = today + timedelta(days=days_to_fri + (7 if nxt else 0))
        return {"date_from": start.isoformat(), "date_to": (start + timedelta(days=dur or 2)).isoformat(),
                "flexible_days": 0, "exact": True}
    if start:
        n = dur or (2 if weekend else 7)
        return {"date_from": start.isoformat(), "date_to": (start + timedelta(days=n)).isoformat(),
                "flexible_days": 2, "exact": dur is not None}
    # 6) месяц или сезон: «в ноябре», «зимой»
    mon = None
    m = re.search(r"(?:в|на|к|до)?\s*" + MONTH_RE, t)
    if m:
        mon = month_num(m.group(1))
    else:
        for word, mm in (("зим", 12), ("весн", 4), ("лет", 7), ("осен", 10)):
            if re.search(r"\b" + word + r"(ой|ом|у|ние|ний|ние)?\b", t) and not re.search(r"\d+\s*лет", t):
                mon = mm
                break
    if mon:
        if mon == today.month:
            d1 = today + timedelta(days=10)
        else:
            d1 = _future(mon, 10, today)
        n = dur or 7
        return {"date_from": d1.isoformat(), "date_to": (d1 + timedelta(days=n)).isoformat(),
                "flexible_days": 3, "exact": False, "month_only": True}
    if dur:
        d1 = today + timedelta(days=14)
        return {"date_from": d1.isoformat(), "date_to": (d1 + timedelta(days=dur)).isoformat(),
                "flexible_days": 3, "exact": False, "start_default": True}
    return {}


# ---------------------------------------------------------------- бюджет

_SKIP_AFTER = re.compile(r"\s*(ч\b|час|лет|год|ноч|дн|дня|дней|звезд|\*|взросл|дет|ребен|человек|чел\b|минут|км|недел|" + MONTH_RE + r"|\.\d|-\d)")


def parse_budget(t: str) -> dict:
    best = None
    for m in re.finditer(r"(бюджет[а-я]*|до|за|не дороже|не больше|в пределах|около|примерно|максимум|макс|уложиться в|потратить)?\s*[:\-]?\s*(\d{1,3}(?:[ ]\d{3})+|\d+)(?:[.,](\d+))?\s*(млн|миллион[а-я]*|тысяч[а-я]*|тыс\.?|т\.?\s?р\.?|тр\b|к\b|k\b|₽|руб[а-я]*|р\.|р\b)?", t):
        kw, raw, frac, unit = m.group(1), m.group(2).replace(" ", ""), m.group(3), m.group(4)
        val = float(raw + ("." + frac if frac else ""))
        if not unit and _SKIP_AFTER.match(t, m.end()):
            continue
        if unit:
            if unit.startswith(("млн", "миллион")):
                val *= 1_000_000
            elif unit.startswith(("тыс", "т", "к", "k")):
                val *= 1000
        elif kw and kw.startswith("бюджет") and val < 1000:
            val *= 1000
        elif not kw or val < 3000:
            continue
        if val < 3000 or val > 20_000_000:
            continue
        tail = t[m.end():m.end() + 30]
        per = None
        if re.match(r"\s*(на|с|за)\s*(человека|чел|каждого|носа|персону)", tail):
            per = "person"
        elif re.match(r"\s*(за|в)\s*(ночь|сутки|день)", tail):
            per = "night"
        cand = {"value": int(val), "per": per}
        if best is None or cand["value"] > best["value"]:
            best = cand
    return best or {}


# ---------------------------------------------------------------- гости

def parse_guests(t: str) -> dict:
    res: dict = {}
    adults = None
    total = None
    m = re.search(NUM + r"\s*взросл", t)
    if m:
        adults = num(m.group(1))
    if re.search(r"вдвоем|на двоих|вдвоем|с жен|с муж|с девушк|с парнем|с супруг|с подруг|с другом|с любим|с невест|с женихом|\bпарой\b|медов|романтич|для двоих", t):
        total = 2
    for w, n in (("втроем", 3), ("вчетвером", 4), ("впятером", 5), ("вшестером", 6)):
        if w in t:
            total = n
    m = re.search(r"(?:компани[а-я]* из|нас|группа из|группой)\s+" + NUM + r"(?!\s*(лет|год))", t)
    if m:
        total = num(m.group(1))
    if re.search(r"\b(один|одна)\b(?!\s*(день|дня|ноч|недел|раз|из|отель))|\bсоло\b|в одиночку|одному\b|одной\b", t) and total is None and adults is None:
        adults = 1
    extra = len(re.findall(r"с мамой|с папой|с бабушкой|с дедушкой|с тещей|со свекровью|с родител", t))
    if "с родител" in t:
        extra += 1

    children = None
    ages = [int(a) for a in re.findall(r"(\d{1,2})\s*-?\s*летн", t)]
    for m in re.finditer(r"(?:ребен[а-я]*|сын[а-я]*|доч[а-я]*|дет[а-я]*|малыш[а-я]*|детям|внук[а-я]*)\D{0,12}?(\d{1,2})(?:\s*(?:,|и)\s*(\d{1,2}))?\s*(?:лет|года|год)", t):
        ages += [int(x) for x in m.groups() if x]
    if not ages:
        for m in re.finditer(r"(\d{1,2})(?:\s*(?:,|и)\s*(\d{1,2}))?\s*(?:лет|года|год)", t):
            ctx = t[max(0, m.start() - 25):m.end() + 5]
            if re.search(r"ребен|дет|сын|доч|малыш|внук", ctx):
                ages += [int(x) for x in m.groups() if x]
    ages = [a for a in ages if a < 18]
    m = re.search(r"(?:с\s+)?" + NUM + r"\s*(?:детьми|детей|ребенка|ребенком|дет)", t)
    if m and num(m.group(1)) <= 6:
        children = num(m.group(1))
    elif re.search(r"с детьми|\bдети\b", t):
        children = max(2, len(ages)) if not ages else len(ages)
    elif re.search(r"с ребен|с сыном|с дочк|с дочер|с малыш|ребенок|ребенка|с младенц|с грудничк|с подростк|с внук", t):
        children = 1
    elif re.search(r"семь[ея]й|всей семьей|семейн", t):
        children = 1
        res["family_guess"] = True
    if ages and (children is None or len(ages) > children):
        children = len(ages)
    if children:
        while len(ages) < children:
            if re.search(r"младен|грудн", t):
                ages.append(1)
            elif re.search(r"малыш", t):
                ages.append(3)
            elif re.search(r"подрост", t):
                ages.append(14)
            else:
                ages.append(7)
                res["child_age_default"] = True
        ages = ages[:children]
    res["children_ages"] = ages if children else []
    kids = len(res["children_ages"])
    if adults is None and total is not None:
        adults = max(1, total - kids) if total > kids else total
    if adults is None:
        res["adults_default"] = True
        adults = 2
    adults += extra if not re.search(NUM + r"\s*взросл", t) else 0
    res["adults"] = adults
    res["explicit"] = not res.get("adults_default") or bool(kids)
    return res


# ---------------------------------------------------------------- места

def find_origin(t: str) -> tuple[str | None, tuple[int, int] | None]:
    for m in re.finditer(r"(?:\bиз|вылет из|выезд из|живу в|я из|с вылетом из|летим из|едем из)\s+([а-я\-]+(?:\s+[а-я\-]+)?)", t):
        word = m.group(1)
        for oid, syns in ORIGIN_SYNONYMS.items():
            if any(word.startswith(s) for s in syns):
                return oid, m.span()
    return None, None


def _word_at(t: str, s: str) -> int:
    for m in re.finditer(re.escape(s), t):
        if m.start() == 0 or not t[m.start() - 1].isalpha():
            return m.start()
    return -1


def negated_dest(t: str, dest: str) -> bool:
    """«не Анталья», «только не Турция», «без Египта», «кроме Дубая»."""
    syns = list(DEST_SYNONYMS[dest]) + [s for group, ds in COUNTRY_SYNONYMS.items() if dest in ds for s in group]
    for syn in syns:
        for m in re.finditer(re.escape(syn), t):
            if re.search(r"(?:\bне|\bбез|кроме|исключи\w*|убери\w*)\s+(?:в\s+|во\s+|на\s+)?$", t[max(0, m.start() - 16):m.start()]):
                return True
    return False


def find_destinations(t: str) -> list[str]:
    found = []
    for did, syns in DEST_SYNONYMS.items():
        for s in syns:
            idx = _word_at(t, s)
            if idx >= 0:
                found.append((idx, did))
                break
    for syns, dests in COUNTRY_SYNONYMS.items():
        idx = min((i for i in (_word_at(t, s) for s in syns) if i >= 0), default=-1)
        if idx >= 0:
            found += [(idx, d) for d in dests]
    out = []
    for _, d in sorted(found):
        if d not in out:
            out.append(d)
    return out


_GENERIC = {"отель", "резорт", "гранд", "парк", "бич", "сити", "клаб", "спа", "инн", "лодж", "бутик", "хостел-отель", "хостел",
            "делюкс", "классик", "фэмили", "аква", "дома", "отель", "лоджия", "beach", "resort", "гольф", "и"}


_KNOWN_WORDS = {"россии", "россию", "турцию", "египет", "египте", "море", "горы", "майские", "выходные", "новый", "неделю",
                "ноябре", "декабре", "январе", "феврале", "марте", "апреле", "мае", "июне", "июле", "августе", "сентябре", "октябре"}


def find_unknown_place(text: str) -> str | None:
    """Названное место, которого нет в демо-каталоге («в Париж»): лучше спросить, чем молча искать другое."""
    for m in re.finditer(r"(?:^|[\s,])(?:в|во|на)\s+([А-ЯЁ][а-яё\-]{2,})", text):
        w = norm(m.group(1))
        if w in _KNOWN_WORDS or month_num(w) and re.match(MONTH_RE, w):
            continue
        if any(w.startswith(s[:max(4, len(s) - 1)]) or s.startswith(w[:5]) for syns in list(DEST_SYNONYMS.values()) + list(ORIGIN_SYNONYMS.values()) for s in syns):
            continue
        if find_hotel(w):
            continue
        return m.group(1)
    return None


def find_hotel(t: str) -> str | None:
    dest_words = {s for syns in DEST_SYNONYMS.values() for s in syns}
    best, best_score = None, 0
    for hid, h in HOTELS.items():
        name = norm(h["name"])
        if name in t:
            return hid
        tokens = [w for w in re.split(r"[\s\-]+", name) if w not in _GENERIC and not any(w.startswith(d) for d in dest_words) and len(w) > 2]
        hits = [w for w in tokens if re.search(r"(?<![а-я])" + re.escape(w[:max(4, len(w) - 2)]), t)]
        score = len(hits)
        # все отличительные слова названия должны встретиться; одно слово — только длинное
        if tokens and score == len(tokens) and (score >= 2 or len(hits[0]) >= 6):
            if score > best_score:
                best, best_score = hid, score
    return best


def find_room(t: str, hotel_id: str | None) -> str | None:
    rooms = HOTELS[hotel_id]["rooms"] if hotel_id else list(ROOM_TYPES)
    sea = re.search(r"вид\w* на море|видом на море|с видом", t)
    cand = None
    if re.search(r"вилл\w* (над|на) вод|water villa|бунгало над вод", t):
        cand = "water_villa"
    elif re.search(r"делюкс|deluxe|делюск", t):
        cand = "deluxe_sea" if sea and "deluxe_sea" in rooms else "deluxe"
    elif re.search(r"(?<!де)люкс|сюит|suite", t):
        cand = "suite"
    elif re.search(r"семейн\w* номер|семейный люкс|family", t):
        cand = "family"
    elif re.search(r"стандарт", t):
        cand = "standard"
    elif re.search(r"комфорт", t):
        cand = "comfort"
    elif re.search(r"коттедж|домик|шале", t):
        cand = "cottage"
    elif sea:
        cand = "deluxe_sea"
    if cand and cand not in rooms:
        cand = "deluxe" if cand == "deluxe_sea" and "deluxe" in rooms else (cand if not hotel_id else None)
    return cand


# ---------------------------------------------------------------- предпочтения

# (регэксп, признак, значение при позитивном упоминании)
WEIGHT_RULES = [
    (r"перв\w* лини|у моря|на берегу|у самого моря|рядом с морем|у воды|свой пляж", "beach_line_1", 0.8),
    (r"вс[её] включено|олл инклюзив|all inclusive|\bai\b|ультра", "all_inclusive", 0.9),
    (r"питани", "all_inclusive", 0.6),
    (r"5 звезд|пять звезд|пятизвезд|5\*|люксов|роскош|шикарн|премиум|дорого-богато", "stars_5", 0.8),
    (r"детск\w* клуб|аниматор|анимаци|мини-клуб|детск\w* бассейн|горк|аквапарк", "kids_club", 0.9),
    (r"тих|спокойн|уедин|релакс|отдохнуть от всех|без шума|романт|перезагруз|выспаться", "quiet", 0.7),
    (r"активн|тусов|вечеринк|движ|ночн\w* жизн|дайв|серф|кайт|\bсап\b|поход|трекинг|экстрим|лыж|сноуборд|рафтинг", "active", 0.7),
    (r"бесплатн\w* отмен|можно отменить|возможностью отмен|с отменой|вернуть деньги", "free_cancel", 0.9),
    (r"\bспа\b|spa|массаж|сауна|баня|бассейн с подогревом|термы", "spa", 0.7),
    (r"в центре|центр города|пешком до|рядом с метро|в центр|достопримечат|экскурси|музе|гулять по городу|прогулк", "center", 0.7),
    (r"природ|горы|горах|лес|озер|звезды на небе|звездное небо|в горы|тайг", "nature", 0.7),
    (r"недорог|дешев|бюджетн|эконом|подешевле|не дорого|сэконом|по-бюджетному", "budget", 0.7),
    (r"рейтинг|отзыв|проверенн|хорош\w* отел", "quality", 0.6),
]

NEG_PREFIX = r"(?:не важн\w*|неважн\w*|без разницы|все равно|не нужн\w*|не хочу|без|не надо|не люблю|не интересн\w*|не обязательн\w*)"


def parse_weights(t: str) -> tuple[dict, dict]:
    """→ (абсолютные значения весов, источник). Отрицания «питание не важно», «без детей» обрабатываются."""
    out: dict = {}
    for rx, feat, val in WEIGHT_RULES:
        for m in re.finditer(rx, t):
            before = t[max(0, m.start() - 22):m.start()]
            after = t[m.end():m.end() + 22]
            neg_before = re.search(NEG_PREFIX + r"\s*\w*\s*$", before)
            neg_after = re.match(r"\w*\s*(?:не важн|неважн|не нужн|не обязательн|без разницы|не принципиальн|не главное|не критичн)", after)
            if feat == "kids_club" and re.search(r"без детей", t):
                out[feat] = -0.6
            elif neg_after or (neg_before and not re.search(r"без долг", before)):
                out[feat] = 0.0 if neg_after or re.search(r"важн|разниц|равно|обязательн|интересн", before) else -0.5
            elif feat == "budget" and re.search(r"не дешев|не эконом", before + m.group(0)):
                out[feat] = -0.3
            else:
                out[feat] = val
            break
    if re.search(r"без детей|только взрослые|adults only|18\+", t):
        out["kids_club"] = -0.6
        out["quiet"] = max(out.get("quiet", 0), 0.6)
    if re.search(r"не важна цена|деньги не главное|без ограничений по бюджету|цена не важна|бюджет не важен|не экономим", t):
        out["budget"] = -0.2
        out["stars_5"] = max(out.get("stars_5", 0), 0.6)
    return out, {k: "text" for k in out}


def parse_trip_type(t: str, month: int | None) -> dict:
    """Тип отдыха → фильтр по направлениям (теги) + добавка к весам."""
    tags: list[str] = []
    weights: dict = {}
    if re.search(r"тепл\w* мор|тепло|жар|солнц|позагорать|загорать|пляжн|на пляж|купаться|искупаться", t):
        tags.append("warm_sea")
        weights["beach_line_1"] = 0.6
    elif re.search(r"\bмор[еяюи]|\bморе\b", t):
        tags.append("sea")
        weights["beach_line_1"] = 0.5
    if re.search(r"горнолыж|лыж|сноуборд|\bгор[ыауе]\b|в горы|горах", t):
        tags.append("mountains")
    if re.search(r"природ|лес|озер|поход|тишин\w* и природ|глэмпинг|на природу", t):
        tags.append("nature")
    if re.search(r"\bгород|экскурс|музе|прогулк|гастроном|архитектур|культурн|театр", t):
        tags.append("city")
    if re.search(r"за границ|за рубеж|заграниц", t):
        tags.append("abroad")
    if re.search(r"по росси|в росси|без визы|без загран|нет загран|внутри страны|без загранпаспорта", t):
        tags.append("russia")
    return {"tags": tags, "weights": weights}


def parse_constraints(t: str) -> dict:
    c: dict = {"labels": []}
    m = re.search(r"(?:перел[её]т\w*|лететь|в пути|дорога)\D{0,20}?(?:не (?:больше|дольше|более)|до|максимум|меньше|не длиннее)\s*" + NUM + r"\s*(?:ч|час)", t) \
        or re.search(r"(?:не (?:больше|дольше|более)|до|максимум)\s*" + NUM + r"\s*(?:ч|час)\w*\s*(?:перел|лет|в пути|дорог)", t)
    if m:
        c["max_travel_hours"] = num(m.group(1))
    elif re.search(r"без долг\w* перел|недолг\w* перел|короткий перел|недалеко|не далеко|не очень далеко|поближе|близко лететь|без длинн\w* перел|не долго лететь|недолго лететь", t):
        c["max_travel_hours"] = 5
    if "max_travel_hours" in c:
        c["labels"].append(f"в пути не дольше {c['max_travel_hours']} ч")
    if re.search(r"без пересад|прям\w* рейс|прямым|без стыков", t):
        c["direct_only"] = True
        c["labels"].append("без пересадок")
    if re.search(r"на машин|на авто|своим ходом|на своей", t):
        c["transport"] = "car"
        c["labels"].append("своим ходом")
    elif re.search(r"на поезд|поездом|сапсан|ласточк|\bжд\b|ж/д|по железн|не летать|не люблю летать|боюсь летать|без самолет|без перелет|не на самолет|на электричк", t):
        c["transport"] = "train"
        c["labels"].append("на поезде")
    elif re.search(r"самол[её]т|перел[её]т|лететь|полет|прилет|авиа", t) and not re.search(r"без долг\w* перел|недолг\w* перел|короткий перел|без длинн", t):
        c["transport"] = "flight"
    if re.search(r"с собак|с псом|с питомц|с кошк|с котом|с кот[её]нк|с животн|с щенк|с собачк|pet", t) and not re.search(r"без (собак|питомц|животн)", t):
        c["pets"] = True
        c["labels"].append("с питомцем")
    m = re.search(r"(?:не ниже|от|минимум|хотя бы)?\s*([345])\s*(?:\*|звезд)", t)
    if m:
        c["min_stars"] = int(m.group(1))
        c["labels"].append(f"от {m.group(1)}★")
    elif re.search(r"пятизвезд|пять звезд", t):
        c["min_stars"] = 5
        c["labels"].append("от 5★")
    return c


# ---------------------------------------------------------------- сборка профиля

def parse_request(text: str, today: date) -> dict:
    t = norm(text)
    origin, span = find_origin(t)
    t_dest = t[:span[0]] + " " + t[span[1]:] if span else t
    hotel_id = find_hotel(t)
    dests = find_destinations(t_dest)
    excluded = [d for d in dests if negated_dest(t_dest, d)]
    dests = [d for d in dests if d not in excluded]
    if hotel_id:
        dests = [HOTELS[hotel_id]["dest"]]
    room = find_room(t.replace(norm(HOTELS[hotel_id]["name"]), " ") if hotel_id else t, hotel_id)
    dates = parse_dates(t, today)
    guests = parse_guests(t)
    budget = parse_budget(t)
    constraints = parse_constraints(t)
    month = date.fromisoformat(dates["date_from"]).month if dates else None
    trip = parse_trip_type(t, month)
    w_text, src_w = parse_weights(t)

    weights = dict(DEFAULT_WEIGHTS)
    source: dict = {}
    for k, v in trip["weights"].items():
        weights[k] = v
        source[k] = "text"
    if guests["children_ages"]:
        weights["kids_club"] = 0.6
        source["kids_club"] = "text"
    weights.update(w_text)
    source.update(src_w)

    if not dates:
        d1 = today + timedelta(days=14)
        dates = {"date_from": d1.isoformat(), "date_to": (d1 + timedelta(days=7)).isoformat(), "flexible_days": 3,
                 "exact": False, "start_default": True}
    source["dates"] = "text" if (dates.get("exact") or dates.get("month_only") or not dates.get("start_default")) else "default"
    if dates.get("nights_default") or dates.get("month_only"):
        source["nights"] = "default"
    source["adults"] = "default" if guests.get("adults_default") else "text"
    if guests["children_ages"]:
        source["children_ages"] = "default" if guests.get("child_age_default") or guests.get("family_guess") else "text"
    source["origin_city"] = "text" if origin else "default"

    nights = (date.fromisoformat(dates["date_to"]) - date.fromisoformat(dates["date_from"])).days
    pax = guests["adults"] + len(guests["children_ages"])
    budget_total = None
    budget_night = None
    if budget:
        if budget["per"] == "person":
            budget_total = budget["value"] * pax
        elif budget["per"] == "night":
            budget_night = budget["value"]
        else:
            budget_total = budget["value"]
        source["budget_total_rub"] = "text"

    hard = {
        "date_from": dates["date_from"], "date_to": dates["date_to"], "nights": nights,
        "flexible_days": dates.get("flexible_days", 0),
        "budget_total_rub": budget_total, "budget_night_rub": budget_night,
        "adults": guests["adults"], "children_ages": guests["children_ages"],
        "origin_city": origin or "moscow",
        "destination": dests[0] if len(dests) == 1 else None,
        "destinations_allowed": dests if len(dests) > 1 else [],
        "trip_tags": trip["tags"],
        "exclude_dests": excluded,
        "hotel_id": hotel_id, "room_category": room,
        "transport": constraints.get("transport", "any"),
        "max_travel_hours": constraints.get("max_travel_hours"),
        "direct_only": constraints.get("direct_only", False),
        "pets": constraints.get("pets", False),
        "min_stars": constraints.get("min_stars"),
        "constraints": constraints["labels"],
    }
    if hotel_id:
        source["hotel_id"] = "text"
        source["room_category"] = "text" if room else "default"
    known_all = bool(hotel_id and dates.get("exact"))
    unknown = None if (dests or hotel_id or excluded) else find_unknown_place(text)
    return {"hard": hard, "weights": weights, "source": source, "known_all": known_all, "text": text, "unknown_place": unknown}
