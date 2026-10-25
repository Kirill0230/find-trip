"""Юнит-тесты ключевой логики + сквозной прогон 20 запросов. Запуск: OFFLINE=1 .venv/bin/pytest -q"""
import os
from datetime import date

os.environ.setdefault("OFFLINE", "1")
os.environ.setdefault("AGENT_TODAY", "2026-10-24")
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

from app.agent.parser import parse_request  # noqa: E402
from app.tools.connections import check_connections  # noqa: E402

TODAY = date(2026, 10, 24)


def test_parse_case_example():
    p = parse_request("Хочу куда-нибудь к тёплому морю в ноябре, недорого, с ребёнком, без долгих перелётов", TODAY)
    h = p["hard"]
    assert h["max_travel_hours"] == 5
    assert len(h["children_ages"]) == 1 and p["source"]["children_ages"] == "default"
    assert "warm_sea" in h["trip_tags"]
    assert p["weights"]["budget"] >= 0.5


def test_parse_known_all_skips_feed():
    p = parse_request("Азур Бэй Сочи, номер делюкс с видом на море, 3–10 декабря, двое взрослых", TODAY)
    assert p["known_all"] and p["hard"]["room_category"] == "deluxe_sea"


def test_parse_negation():
    p = parse_request("питание не важно, без детей, тихо", TODAY)
    assert p["weights"]["all_inclusive"] == 0
    assert p["weights"]["kids_club"] < 0


def test_connections_detect_late_arrival():
    pkg = {
        "hotel": {"hotel_id": "h_sochi_01", "date_from": "2026-12-03", "date_to": "2026-12-10", "checkin": "15:00", "checkout": "12:00",
                  "available": True, "capacity_ok": True, "room_name": "Стандарт", "cancel": {"free": True}},
        "guests": {"adults": 2, "children_ages": []},
        "transport": {"outbound": {"mode": "flight", "dep": "2026-12-03T23:50", "arr": "2026-12-04T02:10", "pax": 2, "baggage": True},
                      "return": {"mode": "flight", "dep": "2026-12-10T16:00", "arr": "2026-12-10T18:20", "pax": 2, "baggage": True}},
        "transfers": {}, "total": {"total_rub": 1},
    }
    codes = {c["code"] for c in check_connections(pkg) if c["severity"] == "error"}
    assert "arrive_after_checkin_date" in codes


def test_twenty_queries_reach_booking():
    from tests.run_metrics import main
    assert main() == 0


def _client():
    from fastapi.testclient import TestClient
    from app.main import app
    return TestClient(app)


def test_post_purchase_change_dates_with_fee():
    c = _client()
    s = c.post("/session", json={"text": "Азур Бэй Сочи, номер делюкс с видом на море, 3–10 декабря, двое взрослых"}).json()
    sid = s["session_id"]
    paid = c.post(f"/session/{sid}/book", json={"guests": [{"name": "A B"}, {"name": "C D"}]}).json()["paid_rub"]
    r = c.post(f"/session/{sid}/order/request", json={"text": "на день позже"}).json()
    assert r["type"] == "proposal" and r["proposal"]["can_apply"]
    o = c.post(f"/session/{sid}/order/confirm").json()
    assert o["paid_rub"] == paid + r["proposal"]["to_pay"]
    assert o["package"]["hotel"]["date_from"] == "2026-12-04"
    assert c.get(f"/session/{sid}/order/calendar.ics").text.startswith("BEGIN:VCALENDAR")


def test_memory_between_sessions():
    c = _client()
    s = c.post("/session", json={"text": "море в декабре из Казани вдвоём", "user_id": "u_pytest"}).json()
    c.get(f"/session/{s['session_id']}/shortlist?stream=0")
    s2 = c.post("/session", json={"text": "хочу к морю в январе", "user_id": "u_pytest"}).json()
    assert s2["profile"]["hard"]["origin_city"] == "kazan"
    assert s2["profile"]["source"]["origin_city"] == "memory"


def test_no_dates_asks_when():
    c = _client()
    s = c.post("/session", json={"text": "хочу к морю"}).json()
    assert s["mode"] == "clarify" and "Когда" in s["clarify"]["question"]
