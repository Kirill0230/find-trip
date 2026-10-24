"""check_connections — проверка состыковок пакета. Чистый код, без LLM.

severity=error блокирует оплату и возвращает на страницу доуточнения,
severity=warning показывается как риск.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

from ..catalog import HOTELS
from . import tool


def _dt(s: str) -> datetime:
    return datetime.fromisoformat(s)


def _hm(s: str) -> str:
    return _dt(s).strftime("%H:%M")


def _c(code: str, severity: str, component: str, message: str, fix: str = "") -> dict:
    return {"code": code, "severity": severity, "component": component, "message": message, "fix": fix}


@tool("check_connections")
def check_connections(pkg: dict) -> list[dict]:
    out: list[dict] = []
    hotel = pkg["hotel"]
    h = HOTELS[hotel["hotel_id"]]
    d_in, d_out = date.fromisoformat(hotel["date_from"]), date.fromisoformat(hotel["date_to"])
    checkin_dt = datetime.combine(d_in, datetime.strptime(hotel["checkin"], "%H:%M").time())
    checkout_dt = datetime.combine(d_out, datetime.strptime(hotel["checkout"], "%H:%M").time())
    guests = pkg["guests"]["adults"] + len(pkg["guests"]["children_ages"])

    if not hotel.get("available", True):
        out.append(_c("hotel_unavailable", "error", "hotel", "На эти даты номер распродан.", "Сдвиньте даты или выберите другой номер."))
    if not hotel.get("capacity_ok", True):
        out.append(_c("room_capacity", "error", "hotel", f"В номере «{hotel['room_name']}» не поместятся {guests} гостей.", "Выберите семейный номер."))

    tr = pkg.get("transport") or {}
    out_leg, ret_leg = tr.get("outbound"), tr.get("return")
    tf = pkg.get("transfers") or {}
    t_in, t_out = tf.get("arrival"), tf.get("departure")

    if out_leg:
        if out_leg.get("pax") is not None and out_leg["pax"] != guests:
            out.append(_c("pax_mismatch", "error", "transport", f"Билеты на {out_leg['pax']} чел., а гостей {guests}.", "Пересоберите билеты."))
        arr = _dt(out_leg["arr"])
        at_hotel = _dt(t_in["end"]) if t_in else arr
        if arr.date() > d_in:
            out.append(_c("arrive_after_checkin_date", "error", "transport",
                          f"Прибытие {arr.strftime('%d.%m в %H:%M')} — позже даты заезда {d_in.strftime('%d.%m')}: первая ночь сгорит.",
                          "Выберите более ранний рейс или сдвиньте заезд."))
        elif arr.date() < d_in - timedelta(days=1) or (arr.date() == d_in - timedelta(days=1) and arr.hour < 20):
            out.append(_c("arrive_before_checkin_date", "error", "transport",
                          f"Прибытие {arr.strftime('%d.%m')}, а заезд только {d_in.strftime('%d.%m')} — ночь без номера.",
                          "Выберите рейс в день заезда или добавьте ночь."))
        elif arr.date() < d_in:
            out.append(_c("night_arrival_before", "warning", "transport",
                          f"Прибытие накануне в {arr.strftime('%H:%M')}, заселение {d_in.strftime('%d.%m')} с {hotel['checkin']}.",
                          "Можно добавить ночь или запросить ранний заезд."))
        else:
            wait = (checkin_dt - at_hotel).total_seconds() / 3600
            if wait > 3:
                out.append(_c("early_arrival", "warning", "hotel",
                              f"В отеле будете в {at_hotel.strftime('%H:%M')}, заселение с {hotel['checkin']} — ждать {wait:.0f} ч.",
                              "Запросим ранний заезд (бесплатно не гарантирован)." if h.get("early_checkin") else "Оставите багаж на ресепшене."))
        if t_in and not t_in.get("available", True):
            out.append(_c("transfer_unavailable", "error", "transfer",
                          f"{t_in['name']} работает {t_in['hours']}, а вы прибываете в {arr.strftime('%H:%M')}.",
                          "Поменяйте трансфер на такси или индивидуальный."))
    if ret_leg:
        dep = _dt(ret_leg["dep"])
        if dep.date() < d_out:
            out.append(_c("leave_before_checkout_date", "error", "transport",
                          f"Обратный рейс {dep.strftime('%d.%m')}, а выезд из отеля {d_out.strftime('%d.%m')} — оплаченная ночь пропадёт.",
                          "Выберите обратный рейс в день выезда."))
        elif dep.date() > d_out + timedelta(days=1) or (dep.date() == d_out + timedelta(days=1) and dep.hour >= 6):
            out.append(_c("leave_after_checkout_date", "error", "transport",
                          f"Обратный рейс {dep.strftime('%d.%m %H:%M')} — после выезда {d_out.strftime('%d.%m')} остаётесь без номера.",
                          "Выберите рейс в день выезда или продлите проживание."))
        else:
            buffer = 150 if ret_leg["mode"] == "flight" else 40
            drive = t_out["duration_min"] if t_out else 30
            leave = dep - timedelta(minutes=buffer + drive)
            if leave < checkout_dt - timedelta(hours=10):
                out.append(_c("night_departure", "warning", "transport",
                              f"Выезжать из отеля в {leave.strftime('%H:%M')} {leave.strftime('%d.%m')} — ночной выезд.",
                              "Можно выбрать более поздний рейс."))
            elif leave > checkout_dt + timedelta(hours=3):
                out.append(_c("late_departure", "warning", "hotel",
                              f"Выезд из номера до {hotel['checkout']}, а ехать {'в аэропорт' if ret_leg['mode'] == 'flight' else 'на вокзал'} — в {leave.strftime('%H:%M')}.",
                              "Попросим поздний выезд или оставите багаж."))
        if t_out and not t_out.get("available", True):
            out.append(_c("transfer_back_unavailable", "error", "transfer",
                          f"{t_out['name']} работает {t_out['hours']}, а выезжать нужно ночью.", "Поменяйте обратный трансфер на такси."))
    if out_leg and ret_leg and _dt(ret_leg["dep"]) <= _dt(out_leg["arr"]):
        out.append(_c("return_before_arrival", "error", "transport", "Обратный рейс раньше прибытия.", "Пересоберите билеты."))

    budget = pkg.get("budget")
    total = pkg.get("total", {}).get("total_rub")
    if budget and total and total > budget:
        out.append(_c("over_budget", "warning", "total", f"Пакет {total:,} ₽ — выше бюджета {budget:,} ₽ на {total - budget:,} ₽.".replace(",", " "),
                      "Скажите «бюджет меньше» — подберу дешевле."))
    if pkg.get("pets") and out_leg and out_leg["mode"] == "flight":
        out.append(_c("pet_flight", "warning", "transport", "Собака в салоне — до 8 кг с переноской, доплата ~3 000 ₽ за плечо.", "Крупную собаку — только в багажном отсеке."))
    if out_leg and out_leg["mode"] == "flight" and not out_leg.get("baggage", True):
        out.append(_c("no_baggage", "warning", "transport", "Самый дешёвый тариф — только ручная кладь.", "Багаж можно докупить или выбрать другой рейс."))
    if not hotel["cancel"]["free"]:
        out.append(_c("non_refundable", "warning", "hotel", "Тариф отеля невозвратный.", ""))
    return out
