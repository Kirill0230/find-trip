"""Память между сессиями: что агент помнит о пользователе и как подставляет это в новый запрос.

Явно сказанное в новом запросе всегда важнее памяти: из памяти берётся только то,
что парсер оставил «по умолчанию», и помечается источником memory (подсвечивается).
"""
from __future__ import annotations

from ..catalog import FEATURES, HOTELS, ORIGINS, hotel_by_video

MEMORY_WEIGHT = 0.7  # выученный вес переносится ослабленным: это прошлое, а не текущий запрос


def remember(state: dict, prev: dict | None) -> dict:
    """Собирает предпочтения из сессии (после подборки или покупки) поверх прошлых."""
    prefs = dict(prev or {})
    src, hard = state["source"], state["hard"]
    if src.get("origin_city") in ("text", "correction"):
        prefs["origin_city"] = hard["origin_city"]
    if src.get("adults") in ("text", "correction") or src.get("children_ages") in ("text", "correction"):
        prefs["adults"] = hard["adults"]
        prefs["children_ages"] = hard["children_ages"]
    if hard.get("pets"):
        prefs["pets"] = True
    learned = dict(prefs.get("weights") or {})
    for k in FEATURES:
        if src.get(k) in ("swipes", "text", "correction") and abs(state["weights"].get(k, 0)) >= 0.3:
            learned[k] = round(state["weights"][k], 2)
    prefs["weights"] = learned
    liked = [HOTELS[h]["dest"] for h in (hotel_by_video(v) for v in state.get("liked", [])) if h]
    if liked:
        prefs["liked_dests"] = sorted(set(prefs.get("liked_dests", [])) | set(liked))
    prefs["trips"] = prefs.get("trips", 0) + (1 if state.get("order") else 0)
    return prefs


def recall(state: dict, prefs: dict | None) -> list[str]:
    """Подставляет память в свежий профиль. Возвращает, что именно вспомнили (для подсказки)."""
    if not prefs:
        return []
    hard, src, used = state["hard"], state["source"], []
    if src.get("origin_city") == "default" and prefs.get("origin_city") in ORIGINS:
        hard["origin_city"] = prefs["origin_city"]
        src["origin_city"] = "memory"
        used.append(f"вылет из {ORIGINS[prefs['origin_city']]['gen']}")
    if src.get("adults") == "default" and "adults" in prefs and not hard.get("children_ages"):
        hard["adults"] = prefs["adults"]
        hard["children_ages"] = list(prefs.get("children_ages") or [])
        src["adults"] = "memory"
        if hard["children_ages"]:
            src["children_ages"] = "memory"
        used.append("состав гостей")
    for k, w in (prefs.get("weights") or {}).items():
        if k in FEATURES and src.get(k, "default") == "default":
            state["weights"][k] = round(w * MEMORY_WEIGHT, 2)
            src[k] = "memory"
    if any(src.get(k) == "memory" for k in FEATURES):
        used.append("ваши приоритеты по прошлым свайпам")
    return used
