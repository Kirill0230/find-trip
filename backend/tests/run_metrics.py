"""Прогон 20 тестовых запросов через API и расчёт метрик из ТЗ.

Свайпы имитируются правилом «лайк, если ролик подходит под скрытые предпочтения
теста» (поле like). Запуск из папки backend:

    OFFLINE=1 .venv/bin/python -m tests.run_metrics
"""
from __future__ import annotations

import json
import os
import statistics
import sys
import time
from datetime import date, datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
CASES = json.loads((HERE / "test_queries.json").read_text(encoding="utf-8"))
os.environ.setdefault("AGENT_TODAY", CASES["today"])
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

from fastapi.testclient import TestClient  # noqa: E402

from app.catalog import hotel_features  # noqa: E402
from app.main import app  # noqa: E402
from app.tools.connections import check_connections  # noqa: E402

MAX_SWIPES = 30


def likes(hotel_id: str, rule: dict) -> bool:
    f = hotel_features(hotel_id)
    return all(f.get(k, 0) >= v for k, v in rule.items())


def parse_score(hard: dict, expect: dict) -> tuple[int, int, list[str]]:
    ok, total, misses = 0, 0, []
    for k, v in expect.items():
        total += 1
        if k == "children":
            got = len(hard.get("children_ages") or [])
        elif k == "month":
            got = date.fromisoformat(hard["date_from"]).month
        elif k in ("trip_tags", "destinations_allowed"):
            got = sorted(hard.get(k) or [])
            v = sorted(v)
        else:
            got = hard.get(k)
        if got == v:
            ok += 1
        else:
            misses.append(f"{k}: ждали {v!r}, получили {got!r}")
    return ok, total, misses


def run_case(c: TestClient, case: dict) -> dict:
    res = {"id": case["id"], "kind": case["kind"], "text": case["text"], "errors": [], "corrections": []}
    t0 = time.perf_counter()
    r = c.post("/session", json={"text": case["text"]})
    r.raise_for_status()
    s = r.json()
    sid = s["session_id"]
    ok, total, misses = parse_score(s["profile"]["hard"], case.get("expect", {}))
    res.update({"parse_ok": ok, "parse_total": total, "parse_misses": misses, "mode": s["mode"]})
    if case.get("expect_mode") and s["mode"] != case["expect_mode"]:
        res["errors"].append(f"режим {s['mode']}, ждали {case['expect_mode']}")
    questions = 0
    guard = 0
    while s["mode"] == "clarify" and guard < 3:
        guard += 1
        questions += 1
        opts = s["clarify"]["options"]
        pick = next((o for o in opts if case.get("clarify_pick", "") in o["label"].lower()), opts[0])
        res["clarify"] = {"question": s["clarify"]["question"], "picked": pick["label"]}
        x = c.post(f"/session/{sid}/correct", json={"text": pick["text"], "context": "clarify"}).json()
        s = x.get("session") or c.get(f"/session/{sid}").json()

    def do_corrections(after: str):
        for corr in case.get("corrections", []):
            if corr["after"] != after:
                continue
            x = c.post(f"/session/{sid}/correct", json={"text": corr["text"], "context": after}).json()
            rec = {"text": corr["text"], "after": after, "understood": x.get("understood"), "tool_calls": x.get("tool_calls", []),
                   "affected": x.get("affected"), "notes": x.get("notes")}
            if after == "package" and x.get("package"):
                rec["recomputed"], rec["kept"] = x["package"]["recomputed"], x["package"]["kept"]
                for k in corr.get("must_keep", []):
                    if k not in x["package"]["kept"]:
                        res["errors"].append(f"правка «{corr['text']}»: {k} пересчитан, а должен остаться")
            rec["not_from_scratch"] = not x.get("from_scratch") and not any(t in rec["tool_calls"] for t in corr.get("must_not_call", []))
            if not x.get("understood"):
                res["errors"].append(f"правка «{corr['text']}» не понята")
            res["corrections"].append(rec)

    swipes = 0
    hotel_choice = None
    t_first_feed = None
    if s["mode"] == "feed":
        shown_shortlist = False
        while swipes < MAX_SWIPES and not shown_shortlist:
            f = c.get(f"/session/{sid}/feed?n=3").json()
            if t_first_feed is None:
                t_first_feed = round((time.perf_counter() - t0) * 1000)
            if not f["items"]:
                break
            for it in f["items"]:
                d = "right" if likes(it["hotel"]["id"], case.get("like", {})) else "left"
                sw = c.post(f"/session/{sid}/swipe", json={"video_id": it["video"]["video_id"], "direction": d}).json()
                swipes += 1
                if sw["show_shortlist"]:
                    shown_shortlist = True
                    break
        res["swipes_to_shortlist"] = swipes if shown_shortlist else None
        sl = c.get(f"/session/{sid}/shortlist?stream=0").json()
        do_corrections("shortlist")
        sl = c.get(f"/session/{sid}/shortlist?stream=0").json()
        if not sl["items"]:
            res["errors"].append("пустая подборка")
            return res
        for it in sl["items"]:
            for k in ("why", "tradeoffs", "risks"):
                if not it["explanation"].get(k):
                    res["errors"].append(f"нет {k} в объяснении")
        hotel_choice = sl["items"][0]["hotel"]["id"]
        pkg = c.post(f"/session/{sid}/package?stream=0", json={"hotel_id": hotel_choice}).json()
    elif s["mode"] == "checkout":
        t_first_feed = round((time.perf_counter() - t0) * 1000)  # пакет вместо ленты
        pkg = s["package"]
    else:
        res["errors"].append(f"не дошли до ленты: {s['mode']}")
        return res
    res["t_first_result_ms"] = t_first_feed
    do_corrections("package")
    pkg = c.get(f"/session/{sid}").json()["package"]
    # страница доуточнения: если есть конфликты — правим (это «вопрос пользователю»)
    tries = 0
    while not pkg["can_book"] and tries < 2:
        tries += 1
        questions += 1
        fix = {"arrival_type": "taxi", "departure_type": "taxi"} if any(x["component"] == "transfer" for x in pkg["conflicts"]) else {"flight_pref": "optimal"}
        pkg = c.patch(f"/session/{sid}/package", json=fix).json()
    b = c.post(f"/session/{sid}/book", json={"guests": [{"name": "Тестовый Гость"}], "email": "test@example.com"})
    res["booked"] = b.status_code == 200
    if b.status_code == 200:
        order = b.json()
        errs = [x for x in check_connections(order["package"]) if x["severity"] == "error"]
        res["conflicts_at_booking"] = len(errs)
        res["order_id"] = order["order_id"]
        res["total_rub"] = order["paid_rub"]
    else:
        res["errors"].append(f"book {b.status_code}: {b.text[:200]}")
        res["conflicts_at_booking"] = 0
    st = c.get(f"/session/{sid}").json()
    res["questions"] = questions + (st.get("profile") and 0)
    res["wall_ms"] = round((time.perf_counter() - t0) * 1000)
    return res


def main() -> int:
    c = TestClient(app)
    results = []
    for case in CASES["queries"]:
        try:
            results.append(run_case(c, case))
        except Exception as e:  # падение — это провал сценария, а не скрипта
            results.append({"id": case["id"], "text": case["text"], "errors": [f"exception: {e!r}"], "booked": False})
    n = len(results)
    booked = sum(1 for r in results if r.get("booked"))
    pok = sum(r.get("parse_ok", 0) for r in results)
    ptot = sum(r.get("parse_total", 0) for r in results)
    tff = [r["t_first_result_ms"] for r in results if r.get("t_first_result_ms") is not None]
    sw = [r["swipes_to_shortlist"] for r in results if r.get("swipes_to_shortlist")]
    qs = [r.get("questions", 0) for r in results]
    corr = [x for r in results for x in r.get("corrections", []) if x["after"] in ("package", "shortlist")]
    summary = {
        "cases": n,
        "completion": f"{booked} из {n}",
        "completion_rate": round(booked / n, 3),
        "t_first_result_ms_max": max(tff) if tff else None,
        "t_first_result_ms_median": statistics.median(tff) if tff else None,
        "questions_avg": round(statistics.mean(qs), 2),
        "questions_max": max(qs),
        "swipes_to_shortlist_avg": round(statistics.mean(sw), 1) if sw else None,
        "parse_accuracy": round(pok / ptot, 3) if ptot else None,
        "conflicts_at_booking": sum(r.get("conflicts_at_booking", 0) for r in results),
        "rebuild_not_from_scratch": f"{sum(1 for x in corr if x['not_from_scratch'])} из {len(corr)}",
        "failed_cases": [r["id"] for r in results if r.get("errors")],
    }
    report = {"generated_at": datetime.now().isoformat(timespec="seconds"), "summary": summary, "results": results}
    (HERE / "metrics_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1))
    print(json.dumps(summary, ensure_ascii=False, indent=1))
    for r in results:
        if r.get("errors") or r.get("parse_misses"):
            print(f"#{r['id']} {r['text'][:60]}")
            for e in r.get("errors", []):
                print("   ✗", e)
            for m in r.get("parse_misses", []):
                print("   ~", m)
    return 0 if booked == n else 1


if __name__ == "__main__":
    sys.exit(main())
