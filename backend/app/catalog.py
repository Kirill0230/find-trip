"""Демо-каталог: направления, отели, номера, ролики и признаки f_i.

Все данные загружаются один раз при импорте. Признаки f_i считаются из
атрибутов отеля (полуавтоматическая разметка из ТЗ: здесь — правилами,
проверяются глазами в hotels.json).
"""
from __future__ import annotations

import json
import math
from functools import lru_cache
from pathlib import Path

DATA_DIR = Path(__file__).parent / "data"

_dest_raw = json.loads((DATA_DIR / "destinations.json").read_text(encoding="utf-8"))
ORIGINS: dict = _dest_raw["origins"]
STATIONS: dict = _dest_raw["stations"]
RAIL_ROUTES: list = _dest_raw["rail_routes"]
DESTINATIONS: dict = _dest_raw["destinations"]

_hotels_raw = json.loads((DATA_DIR / "hotels.json").read_text(encoding="utf-8"))
HOTELS: dict = {h["id"]: h for h in _hotels_raw["hotels"]}

# Часовые пояса (UTC+N) — нужны для местного времени прилёта.
TZ = {
    "moscow": 3, "spb": 3, "kazan": 3, "ekb": 5, "nsk": 7, "krasnodar": 3, "nn": 3,
    "sochi": 3, "antalya": 3, "hurghada": 2, "dubai": 4, "altai": 7, "karelia": 3,
    "sharm": 2, "alanya": 3, "tunisia": 1, "phuket": 7, "nhatrang": 7, "maldives": 5, "goa": 5.5, "srilanka": 5.5,
}

ROOM_TYPES = {
    "standard": {"name": "Стандарт", "capacity": 3, "mult": 1.0},
    "comfort": {"name": "Комфорт", "capacity": 3, "mult": 1.2},
    "deluxe": {"name": "Делюкс", "capacity": 3, "mult": 1.5},
    "deluxe_sea": {"name": "Делюкс с видом на море", "capacity": 3, "mult": 1.7},
    "family": {"name": "Семейный номер", "capacity": 5, "mult": 1.75},
    "suite": {"name": "Люкс", "capacity": 4, "mult": 2.5},
    "cottage": {"name": "Коттедж", "capacity": 5, "mult": 1.6},
    "water_villa": {"name": "Вилла над водой", "capacity": 3, "mult": 1.9},
}

MEALS = {
    "RO": "без питания",
    "BB": "завтраки",
    "HB": "полупансион",
    "FB": "полный пансион",
    "AI": "всё включено",
}

FEATURES = [
    "beach_line_1", "all_inclusive", "stars_5", "kids_club", "active", "quiet",
    "free_cancel", "center", "nature", "spa", "budget", "quality",
]

FEATURE_LABELS = {
    "beach_line_1": "первая линия",
    "all_inclusive": "всё включено",
    "stars_5": "звёздность",
    "kids_club": "детский клуб",
    "active": "активный отдых",
    "quiet": "тишина",
    "free_cancel": "бесплатная отмена",
    "center": "центр города",
    "nature": "природа",
    "spa": "спа",
    "budget": "цена",
    "quality": "рейтинг",
}

DEST_SYNONYMS = {
    "sochi": ["сочи", "адлер", "красная поляна", "красной поляне", "красную поляну", "сириус", "хоста", "черное море", "черном море", "черному морю"],
    "antalya": ["анталь", "антали", "кемер", "белек"],
    "alanya": ["аланья", "аланьи", "аланью", "аланье", "алании", "клеопатр", "конакл"],
    "hurghada": ["хургад", "макади", "эль-гун", "эль гун", "сома бей"],
    "sharm": ["шарм", "синай", "наама"],
    "dubai": ["дубай", "дубае", "абу-даби"],
    "tunisia": ["тунис", "сусс", "монастир", "хаммамет", "джерб"],
    "phuket": ["пхукет", "патонг", "пхи-пхи"],
    "nhatrang": ["нячанг", "камрань"],
    "maldives": ["мальдив"],
    "goa": ["гоа"],
    "srilanka": ["шри-ланк", "шри ланк", "цейлон", "хиккадув", "унаватун"],
    "spb": ["питер", "петербург", "спб", "северную столицу", "северной столице", "ленинград"],
    "kazan": ["казань", "казани", "татарстан"],
    "altai": ["алтай", "алтае", "катун", "телецк", "горно-алтайск", "чемал"],
    "karelia": ["карели", "петрозаводск", "онег", "ладог", "рускеал", "сортавал", "кижи", "валаам"],
}

# страны и моря, за которыми стоят несколько направлений каталога
COUNTRY_SYNONYMS = {
    ("египет", "египт", "красное море", "красном море", "красному морю"): ["hurghada", "sharm"],
    ("турци",): ["antalya", "alanya"],
    ("оаэ", "эмират"): ["dubai"],
    ("таиланд", "тайланд"): ["phuket"],
    ("вьетнам",): ["nhatrang"],
    ("инди",): ["goa"],
    ("средиземн",): ["antalya", "alanya", "tunisia"],
    ("азию", "азии", "азия"): ["phuket", "nhatrang"],
    ("индийск",): ["maldives", "goa", "srilanka"],
}

ORIGIN_SYNONYMS = {
    "moscow": ["москв", "мск"],
    "spb": ["питер", "петербург", "спб", "санкт-петербург"],
    "kazan": ["казан"],
    "ekb": ["екатеринбург", "екб"],
    "nsk": ["новосибирск", "нск"],
    "krasnodar": ["краснодар"],
    "nn": ["нижн", "нижнего новгорода", "нн"],
}


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def stable_hash(*parts) -> int:
    """Детерминированный хэш (в отличие от hash()), чтобы моки не прыгали между запусками."""
    s = "|".join(str(p) for p in parts)
    h = 2166136261
    for ch in s.encode("utf-8"):
        h = ((h ^ ch) * 16777619) & 0xFFFFFFFF
    return h


def noise(*parts, lo: float = 0.9, hi: float = 1.1) -> float:
    return lo + (stable_hash(*parts) % 10000) / 10000 * (hi - lo)


_log_prices = [math.log(h["base"]) for h in HOTELS.values()]
_LP_MIN, _LP_MAX = min(_log_prices), max(_log_prices)


@lru_cache(maxsize=None)
def hotel_features(hotel_id: str) -> dict:
    h = HOTELS[hotel_id]
    line = h["beach_line"]
    meal = h["meal"]
    vibe = h["vibe"]
    return {
        "beach_line_1": {1: 1.0, 2: 0.5, 3: 0.2}.get(line, 0.0),
        "all_inclusive": {"AI": 1.0, "FB": 0.7, "HB": 0.5, "BB": 0.2}.get(meal, 0.0),
        "stars_5": max(0.0, min(1.0, (h["stars"] - 3) / 2)),
        "kids_club": 1.0 if h["kids_club"] else 0.0,
        "active": {"active": 1.0, "city": 0.6, "family": 0.5}.get(vibe, 0.0),
        "quiet": {"quiet": 1.0, "family": 0.3, "city": 0.2}.get(vibe, 0.0),
        "free_cancel": 1.0 if h["cancel_days"] >= 3 else (0.6 if h["cancel_days"] > 0 else 0.0),
        "center": float(h["center"]),
        "nature": float(h["nature"]),
        "spa": 1.0 if h["spa"] else 0.0,
        "budget": round(1 - (math.log(h["base"]) - _LP_MIN) / (_LP_MAX - _LP_MIN), 3),
        "quality": max(0.0, min(1.0, (h["rating"] - 7.5) / 2)),
    }


_stock_raw = json.loads((DATA_DIR / "videos.json").read_text(encoding="utf-8"))
_VIDEO_DIRS = [Path(__file__).resolve().parents[2] / "frontend" / d / "videos" for d in ("public", "dist")]
# в каталог попадают только клипы, файлы которых реально лежат рядом с фронтом
STOCK_CLIPS: dict = {k: {**v, "key": k, "url": f"/videos/{k}.mp4"} for k, v in _stock_raw["clips"].items()
                     if any((d / f"{k}.mp4").exists() and (d / f"{k}.mp4").stat().st_size > 100_000 for d in _VIDEO_DIRS)}


def _spread(items: list, hotel_id: str, group: list[str]) -> dict | None:
    """Раздаёт клипы по кругу внутри группы отелей, чтобы соседи не получали одно и то же видео."""
    if not items:
        return None
    return items[sorted(group).index(hotel_id) % len(items)]


def _place_tag(h: dict) -> str | None:
    if h["dest"] == "sochi" and h["beach_line"] == 0:
        return "mountains"
    if h["beach_line"] in (1, 2):
        return "beach"
    if h["dest"] == "dubai":
        return "city"
    return None


@lru_cache(maxsize=None)
def _place_allocation(dest: str) -> dict:
    """Клип места для каждого отеля направления: сначала подходящий по типу (пляж/горы/город),
    но без повторов, пока есть неиспользованные клипы этого направления."""
    clips = sorted((c for c in STOCK_CLIPS.values() if c["dest"] == dest), key=lambda c: c["key"])
    used: dict[str, int] = {c["key"]: 0 for c in clips}
    alloc = {}
    for hid in sorted(h for h, x in HOTELS.items() if x["dest"] == dest):
        if not clips:
            break
        want = _place_tag(HOTELS[hid])
        best = min(clips, key=lambda c: (used[c["key"]], 0 if want in c["tags"] else 1, c["key"]))
        used[best["key"]] += 1
        alloc[hid] = best
    return alloc


def _stock_for(hotel_id: str) -> list[dict]:
    """Плейлист отеля: клип места (под тип отеля) → бассейн (для курортов) → номер."""
    h = HOTELS[hotel_id]
    dest = h["dest"]
    out = [_place_allocation(dest).get(hotel_id)]
    resort = "warm" in DESTINATIONS[dest]["tags"] or (h["beach_line"] in (1, 2) and h["stars"] >= 4)
    if resort:
        pools = sorted((c for c in STOCK_CLIPS.values() if "pool" in c["tags"]), key=lambda c: c["key"])
        out.append(_spread(pools, hotel_id, [hid for hid in HOTELS if hid.startswith("h_")]))
    sea_room = h["beach_line"] == 1 and "sea" in DESTINATIONS[dest]["tags"]
    rooms = sorted((c for c in STOCK_CLIPS.values() if ("room_sea" if sea_room else "room") in c["tags"]), key=lambda c: c["key"]) \
        or sorted((c for c in STOCK_CLIPS.values() if "room" in c["tags"]), key=lambda c: c["key"])
    out.append(_spread(rooms, hotel_id, list(HOTELS)))
    return [c for c in out if c]


def videos_for(hotel_id: str) -> list[dict]:
    """2 ролика на отель: территория и номер. Если есть стоковые клипы направления —
    source = stock (видео места, не отеля); иначе фронт рисует иллюстрацию (mock_illustration)."""
    h = HOTELS[hotel_id]
    rooms = h["rooms"]
    best_room = next((r for r in ("deluxe_sea", "suite", "deluxe", "cottage", "family") if r in rooms), rooms[0])
    playlist = _stock_for(hotel_id)
    out = []
    for i, (kind, room) in enumerate((("territory", rooms[0]), ("room", best_room)), start=1):
        clips = playlist if kind == "territory" else playlist[-1:] + playlist[:-1]
        source = h.get("video_source") or ("stock" if clips else "mock_illustration")
        out.append({
            "video_id": f"v_{hotel_id[2:]}_{i}",
            "hotel_id": hotel_id,
            "room_category": room,
            "kind": kind,
            "source": source,
            "url": h.get("video_url") or (clips[0]["url"] if clips else None),
            "playlist": [c["url"] for c in clips],
            "credits": [{"title": c["title"], "pexels": c["pexels"], "generic": c.get("generic", False)} for c in clips],
            "duration_sec": 18,
            "features": hotel_features(hotel_id),
        })
    return out


VIDEOS: dict = {v["video_id"]: v for hid in HOTELS for v in videos_for(hid)}


def primary_video(hotel_id: str) -> dict:
    return VIDEOS[f"v_{hotel_id[2:]}_1"]


def hotel_by_video(video_id: str) -> str | None:
    v = VIDEOS.get(video_id)
    return v["hotel_id"] if v else None


def default_room(hotel_id: str, guests: int) -> str:
    h = HOTELS[hotel_id]
    for r in h["rooms"]:
        if ROOM_TYPES[r]["capacity"] >= guests:
            return r
    return max(h["rooms"], key=lambda r: ROOM_TYPES[r]["capacity"])


def room_fits(room: str, guests: int) -> bool:
    return ROOM_TYPES[room]["capacity"] >= guests


def climate(dest_id: str, month: int) -> dict:
    c = DESTINATIONS[dest_id]["climate"]
    water = c["water"][month - 1] if c["water"] else None
    return {"air": c["air"][month - 1], "water": water, "rain_days": c["rain_days"][month - 1]}


def arrival_points(dest_id: str) -> list[dict]:
    d = DESTINATIONS[dest_id]
    pts = [{"kind": "airport", "id": d["airport"]["code"], "name": d["airport"]["name"], "lat": d["airport"]["lat"], "lon": d["airport"]["lon"]}]
    for sid in d["stations"]:
        s = STATIONS[sid]
        pts.append({"kind": "station", "id": sid, "name": s["name"], "lat": s["lat"], "lon": s["lon"]})
    return pts
