"""get_weather — Open-Meteo Archive (те же даты год назад) с откатом на климатические нормы.

Прогноз Open-Meteo доступен только на 16 дней вперёд, поэтому для поездки через
месяц берём фактическую погоду тех же дат прошлого года. Температура воды —
из климатических норм каталога (у Open-Meteo Marine нет архива на нужные даты).
"""
from __future__ import annotations

import json
import os
import threading
from datetime import date, timedelta
from pathlib import Path

import httpx

from ..catalog import DESTINATIONS, climate
from . import tool

_CACHE_FILE = Path(__file__).resolve().parent.parent.parent / ".cache" / "weather.json"
_lock = threading.Lock()
try:
    _cache: dict = json.loads(_CACHE_FILE.read_text())
except Exception:
    _cache = {}


def _save():
    try:
        _CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        _CACHE_FILE.write_text(json.dumps(_cache, ensure_ascii=False))
    except Exception:
        pass


def _archive(dest_id: str, d_from: date, d_to: date) -> dict | None:
    if os.getenv("OFFLINE") == "1":
        return None
    d = DESTINATIONS[dest_id]
    start = d_from.replace(year=d_from.year - 1)
    end = start + timedelta(days=max(1, min(14, (d_to - d_from).days)))
    key = f"{dest_id}:{start}:{end}"
    if key in _cache:
        return _cache[key]
    try:
        r = httpx.get("https://archive-api.open-meteo.com/v1/archive", timeout=2.5, params={
            "latitude": d["lat"], "longitude": d["lon"], "start_date": start.isoformat(),
            "end_date": end.isoformat(), "daily": "temperature_2m_max,precipitation_sum", "timezone": "auto"})
        daily = r.json()["daily"]
        temps = [t for t in daily["temperature_2m_max"] if t is not None]
        rain = [p for p in daily["precipitation_sum"] if p is not None]
        if not temps:
            return None
        res = {"air": round(sum(temps) / len(temps)), "rainy_share": round(sum(1 for p in rain if p > 1) / max(1, len(rain)), 2),
               "period": f"{start.strftime('%d.%m.%Y')}–{end.strftime('%d.%m.%Y')}"}
    except Exception:
        return None
    with _lock:
        _cache[key] = res
        _save()
    return res


@tool("get_weather")
def get_weather(dest_id: str, date_from: str, date_to: str) -> dict:
    d_from, d_to = date.fromisoformat(date_from), date.fromisoformat(date_to)
    norm = climate(dest_id, d_from.month)
    arch = _archive(dest_id, d_from, d_to)
    if arch:
        return {"air": arch["air"], "water": norm["water"], "rainy_share": arch["rainy_share"],
                "source": f"Open-Meteo, факт за {arch['period']}", "real": True}
    return {"air": norm["air"], "water": norm["water"], "rainy_share": round(norm["rain_days"] / 30, 2),
            "source": "климатическая норма", "real": False}
