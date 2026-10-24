"""Опциональная LLM для parse_request (structured output).

Включается, только если задан ANTHROPIC_API_KEY и установлен пакет `anthropic`.
LLM дополняет правила: заполняет поля, которые парсер оставил «по умолчанию»,
и никогда не перетирает то, что правила уверенно вытащили из текста.
Таймаут короткий — запрос жюри должен дойти до ленты быстрее 10 секунд.
"""
from __future__ import annotations

import json
import logging
import os

from ..catalog import DESTINATIONS, FEATURES

log = logging.getLogger(__name__)

MODEL = os.getenv("LLM_MODEL", "claude-opus-5-5")
TIMEOUT = float(os.getenv("LLM_TIMEOUT", "6"))

_nullable_int = {"anyOf": [{"type": "integer"}, {"type": "null"}]}
SCHEMA = {
    "type": "object",
    "properties": {
        "destination": {"anyOf": [{"type": "string", "enum": list(DESTINATIONS)}, {"type": "null"}]},
        "trip_tags": {"type": "array", "items": {"type": "string", "enum": ["warm_sea", "sea", "mountains", "nature", "city", "abroad", "russia"]}},
        "adults": _nullable_int,
        "children_ages": {"type": "array", "items": {"type": "integer"}},
        "budget_total_rub": _nullable_int,
        "pets": {"type": "boolean"},
        "max_travel_hours": _nullable_int,
        "weights": {
            "type": "object",
            "properties": {k: {"type": "number"} for k in FEATURES if k != "quality"},
            "required": [k for k in FEATURES if k != "quality"],
            "additionalProperties": False,
        },
    },
    "required": ["destination", "trip_tags", "adults", "children_ages", "budget_total_rub", "pets", "max_travel_hours", "weights"],
    "additionalProperties": False,
}

SYSTEM = (
    "Ты разбираешь запрос путешественника на русском языке в параметры поиска. "
    "Направления каталога: " + ", ".join(f"{k} ({v['name']})" for k, v in DESTINATIONS.items()) + ". "
    "destination — только если пользователь явно назвал место. Веса предпочтений от -1 до 1: "
    "0 — не упомянуто, положительные — хочет, отрицательные — не хочет. "
    "Не выдумывай то, чего нет в тексте: неизвестное — null или пустой список."
)


def enabled() -> bool:
    if not os.getenv("ANTHROPIC_API_KEY"):
        return False
    try:
        import anthropic  # noqa: F401
    except ImportError:
        return False
    return True


def llm_parse(text: str) -> dict | None:
    if not enabled():
        return None
    import anthropic
    try:
        client = anthropic.Anthropic(timeout=TIMEOUT, max_retries=0)
        resp = client.messages.create(
            model=MODEL,
            max_tokens=2000,
            system=SYSTEM,
            messages=[{"role": "user", "content": text}],
            output_config={"effort": "low", "format": {"type": "json_schema", "schema": SCHEMA}},
        )
        if resp.stop_reason == "refusal":
            return None
        raw = next(b.text for b in resp.content if b.type == "text")
        return json.loads(raw)
    except Exception as e:  # сеть, таймаут, лимиты — откатываемся на правила
        log.warning("LLM parse failed: %s", e)
        return None


def merge(parsed: dict, llm: dict | None) -> dict:
    """Дополняет результат правил ответом LLM только там, где правила не уверены."""
    if not llm:
        return parsed
    hard, src = parsed["hard"], parsed["source"]
    if not hard.get("destination") and not hard.get("destinations_allowed") and llm.get("destination"):
        hard["destination"] = llm["destination"]
        src["destination"] = "llm"
    if not hard.get("trip_tags") and llm.get("trip_tags"):
        hard["trip_tags"] = llm["trip_tags"]
    if src.get("adults") == "default" and llm.get("adults"):
        hard["adults"] = llm["adults"]
        src["adults"] = "llm"
    if not hard.get("children_ages") and llm.get("children_ages"):
        hard["children_ages"] = [a for a in llm["children_ages"] if 0 <= a < 18]
        src["children_ages"] = "llm"
    if not hard.get("budget_total_rub") and not hard.get("budget_night_rub") and llm.get("budget_total_rub"):
        hard["budget_total_rub"] = llm["budget_total_rub"]
        src["budget_total_rub"] = "llm"
    if not hard.get("pets") and llm.get("pets"):
        hard["pets"] = True
        hard["constraints"].append("с питомцем")
    if not hard.get("max_travel_hours") and llm.get("max_travel_hours"):
        hard["max_travel_hours"] = llm["max_travel_hours"]
        hard["constraints"].append(f"в пути не дольше {llm['max_travel_hours']} ч")
    for k, v in (llm.get("weights") or {}).items():
        if k not in src and abs(v) >= 0.3:
            parsed["weights"][k] = max(-1.0, min(1.0, float(v)))
            src[k] = "llm"
    parsed["llm_used"] = True
    return parsed
