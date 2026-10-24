"""Сводка метрик качества агента — по живым сессиям и по последнему прогону тестов."""
from __future__ import annotations

import json
import statistics
from pathlib import Path

REPORT = Path(__file__).resolve().parent.parent / "tests" / "metrics_report.json"

TARGETS = {
    "completion_rate": "20 из 20",
    "t_first_feed_ms_p95": "< 10 000 мс",
    "questions_avg": "≤ 3",
    "swipes_to_shortlist_avg": "10–15",
    "parse_accuracy": "≥ 90%",
    "conflicts_at_booking": "0",
    "rebuild_not_from_scratch": "во всех тестах с правкой",
}


def _p95(xs: list[float]) -> float | None:
    if not xs:
        return None
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(round(0.95 * (len(xs) - 1))))]


def live_summary(states: list[dict]) -> dict:
    n = len(states)
    m = [s.get("metrics", {}) for s in states]
    booked = sum(1 for x in m if x.get("booked"))
    feed_t = [x["t_first_feed_ms"] for x in m if "t_first_feed_ms" in x]
    swipes = [x["swipes_to_first_shortlist"] for x in m if "swipes_to_first_shortlist" in x]
    corr = [c for x in m for c in x.get("corrections", []) if c["context"] in ("package", "shortlist")]
    return {
        "sessions": n,
        "booked": booked,
        "completion_rate": round(booked / n, 3) if n else None,
        "t_first_feed_ms_median": statistics.median(feed_t) if feed_t else None,
        "t_first_feed_ms_p95": _p95(feed_t),
        "questions_avg": round(statistics.mean([x.get("questions", 0) + x.get("clarify_questions", 0) for x in m]), 2) if m else None,
        "swipes_to_shortlist_avg": round(statistics.mean(swipes), 1) if swipes else None,
        "conflicts_at_booking": sum(x.get("conflicts_at_booking", 0) for x in m),
        "corrections_total": len(corr),
        "rebuild_not_from_scratch": round(sum(1 for c in corr if not c["from_scratch"]) / len(corr), 3) if corr else None,
    }


def summary(states: list[dict]) -> dict:
    out = {"targets": TARGETS, "live": live_summary(states), "test_run": None}
    if REPORT.exists():
        try:
            rep = json.loads(REPORT.read_text())
            out["test_run"] = rep.get("summary")
            out["test_run_at"] = rep.get("generated_at")
        except Exception:
            pass
    return out
