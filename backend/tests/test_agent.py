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
