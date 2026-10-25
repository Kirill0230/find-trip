import React, { useEffect, useState } from "react";
import { api, dt, dur, rub, time } from "../api.js";
import { RebuildBanner } from "../components/common.jsx";

const ORIGINS = { moscow: "Москва", spb: "Санкт-Петербург", kazan: "Казань", ekb: "Екатеринбург", nsk: "Новосибирск", krasnodar: "Краснодар", nn: "Нижний Новгород" };
const STEPS = { hotel: "Цена и наличие номера", transport: "Билеты туда и обратно", transfer: "Трансфер под время прибытия", connections: "Проверка состыковок" };

function Leg({ leg, label }) {
  if (!leg) return null;
  return (
    <div className="leg">
      <div className="small muted">{label}</div>
      <div className="row between">
        <b>{time(leg.dep)} → {time(leg.arr)}</b>
        <span>{rub(leg.price_total)}</span>
      </div>
      <div className="small">{dt(leg.dep)} · {leg.carrier} {leg.number} · {dur(leg.duration_min)}{leg.stops ? ` · пересадка ${leg.via}` : " · без пересадок"}{leg.class_name ? ` · ${leg.class_name}` : ""}{leg.mode === "flight" && !leg.baggage ? " · без багажа" : ""}</div>
    </div>
  );
}

export default function Checkout({ sid, session, pkg, setPkg, hotelId, onPay, onToast, log = () => {} }) {
  const [steps, setSteps] = useState([]);
  const [busy, setBusy] = useState(false);
  const [extra, setExtra] = useState("");
  const [extraDone, setExtraDone] = useState(false);
  const src = session?.profile?.source || {};

  useEffect(() => {
    if (pkg && (!hotelId || pkg.hotel.hotel_id === hotelId)) return;
    if (!hotelId) return;
    setSteps([]);
    api.package(sid, hotelId, (ev, data) => {
      if (ev === "status") { setSteps((s) => [...s, data.step]); log({ kind: "tool", label: data.text }); }
      if (ev === "package") {
        setPkg(data);
        const errs = data.conflicts.filter((c) => c.severity === "error").length;
        log({ kind: "code", label: `Пакет собран: ${data.total.total_rub.toLocaleString("ru-RU")} ₽`, detail: `состыковки: ${errs ? errs + " конфликт(а)" : "без ошибок"}, предупреждений ${data.conflicts.length - errs}` });
      }
      if (ev === "error") onToast({ text: "Не удалось собрать пакет: " + data.message, kind: "warn" });
    }).catch((e) => onToast({ text: e.message, kind: "warn" }));
  }, [hotelId]);

  const patch = async (p) => {
    setBusy(true);
    try {
      log({ kind: "user", label: `Доуточнение: ${Object.keys(p).join(", ")}` });
      const res = await api.patchPackage(sid, p);
      setPkg(res);
      log({ kind: "tool", label: `Пересчитано: ${(res.recomputed || []).join(", ")}`, detail: res.kept?.length ? `сохранено: ${res.kept.join(", ")}` : null });
      if (res.anything_else && !res.anything_else.understood) onToast({ text: res.anything_else.message, kind: "warn" });
      else if (res.changes?.length) onToast({ text: "Пересобрал: " + res.changes.join("; ") });
    } catch (e) {
      onToast({ text: e.message, kind: "warn" });
    } finally {
      setBusy(false);
    }
  };

  if (!pkg) {
    return (
      <div className="screen checkout">
        <h2>Собираю поездку</h2>
        <ul className="steps">
          {Object.entries(STEPS).map(([k, label]) => {
            const done = steps.indexOf(k) >= 0 && steps[steps.length - 1] !== k;
            const active = steps[steps.length - 1] === k;
            return <li key={k} className={done ? "done" : active ? "active" : ""}>{done ? "✓" : active ? "⏳" : "·"} {label}</li>;
          })}
        </ul>
      </div>
    );
  }

  const h = pkg.hotel;
  const tr = pkg.transport || {};
  const tf = pkg.transfers || {};
  const g = pkg.guests;
  const hl = (keys) => keys.some((k) => src[k] === "default") ? "hl" : keys.some((k) => src[k] === "correction") ? "hl-c" : "";
  const errors = pkg.conflicts.filter((c) => c.severity === "error");
  const warns = pkg.conflicts.filter((c) => c.severity === "warning");

  return (
    <div className={`screen checkout ${busy ? "busy" : ""}`}>
      <h2>Проверьте поездку</h2>
      <RebuildBanner pkg={pkg} />

      <div className="card">
        <div className="small muted">Ключевые поля — подсвечены те, что агент додумал сам</div>
        <div className="fields">
          <label className={hl(["adults", "children_ages"])}>Взрослые
            <div className="stepper">
              <button disabled={g.adults <= 1} onClick={() => patch({ adults: g.adults - 1 })}>−</button>
              <b>{g.adults}</b>
              <button disabled={g.adults >= 6} onClick={() => patch({ adults: g.adults + 1 })}>+</button>
            </div>
          </label>
          <label className={hl(["children_ages"])}>Дети (возраст)
            <input defaultValue={g.children_ages.join(", ")} placeholder="нет" key={g.children_ages.join(",")}
              onBlur={(e) => {
                const ages = e.target.value.split(/[,\s]+/).filter(Boolean).map(Number).filter((n) => n >= 0 && n < 18);
                if (ages.join(",") !== g.children_ages.join(",")) patch({ children_ages: ages });
              }} />
          </label>
          <label className={hl(["dates", "nights"])}>Заезд
            <input type="date" value={h.date_from} onChange={(e) => e.target.value && patch({ date_from: e.target.value })} />
          </label>
          <label className={hl(["dates", "nights"])}>Выезд
            <input type="date" value={h.date_to} min={h.date_from} onChange={(e) => e.target.value && patch({ date_to: e.target.value })} />
          </label>
          <label className={hl(["origin_city"])}>Откуда
            <select value={pkg.origin_city} onChange={(e) => patch({ origin_city: e.target.value })}>
              {Object.entries(ORIGINS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
            </select>
          </label>
        </div>
      </div>

      <div className="card">
        <div className="row between"><b>🏨 {h.name}</b><span>{rub(h.total_rub)}</span></div>
        <div className="small">{h.dest_name}, {h.area}</div>
        <div className="small">{dt(h.date_from)} — {dt(h.date_to)} · {h.nights} ноч. · {h.meal_name} · заезд с {h.checkin}</div>
        <div className="row gap">
          <select value={h.room_category} onChange={(e) => patch({ room_category: e.target.value })} aria-label="Категория номера">
            {h.rooms.map((r) => <option key={r} value={r}>{{ standard: "Стандарт", comfort: "Комфорт", deluxe: "Делюкс", deluxe_sea: "Делюкс с видом на море", family: "Семейный", suite: "Люкс", cottage: "Коттедж", water_villa: "Вилла над водой" }[r]}</option>)}
          </select>
          <span className={`small ${h.cancel.free ? "ok" : "warn"}`}>{h.cancel.text}</span>
        </div>
      </div>

      {tr.mode && tr.mode !== "local" && tr.mode !== "car" && (
        <div className="card">
          <div className="row between"><b>{tr.mode === "train" ? "🚆 Поезд" : "✈️ Перелёт"}</b><span>{rub(tr.total_rub)}</span></div>
          <div className="seg">
            {[["optimal", "Удобный"], ["cheap", "Дешёвый"], ["fast", "Быстрый"]].map(([k, l]) => (
              <button key={k} className={tr.pref === k ? "on" : ""} onClick={() => patch({ flight_pref: k })}>
                {l}<span className="small">{tr.alternatives?.[k] ? rub(tr.alternatives[k].total_rub) : ""}</span>
              </button>
            ))}
          </div>
          <Leg leg={tr.outbound} label="Туда" />
          <Leg leg={tr.return} label="Обратно" />
          <details>
            <summary className="small">Все варианты туда ({tr.options_out?.length})</summary>
            {tr.options_out?.map((o) => (
              <button key={o.id} className={`alt ${o.id === tr.outbound.id ? "on" : ""}`} onClick={() => patch({ outbound_id: o.id })}>
                {dt(o.dep)} → {time(o.arr)} · {o.carrier} · {dur(o.duration_min)} · {rub(o.price_total)}{o.stops ? " · 1 пересадка" : ""}
              </button>
            ))}
          </details>
        </div>
      )}
      {(tr.mode === "local" || tr.mode === "car") && <div className="card small">🚗 {tr.note} — билеты не нужны.</div>}

      {tf.arrival && (
        <div className="card">
          <div className="row between"><b>🚕 Трансфер</b><span>{rub((tf.arrival?.price_rub || 0) + (tf.departure?.price_rub || 0))}</span></div>
          <div className="small">{tf.arrival.point} → отель: {dur(tf.arrival.duration_min)}, {tf.arrival.km} км</div>
          <select value={tf.arrival.type} onChange={(e) => patch({ arrival_type: e.target.value, departure_type: e.target.value })} aria-label="Тип трансфера">
            {tf.options_arrival.map((o) => <option key={o.type} value={o.type} disabled={!o.available}>{o.name} · {rub(o.price_rub)} · {dur(o.duration_min)}{o.available ? "" : ` (${o.hours})`}</option>)}
          </select>
          {tf.arrival.note && <div className="small muted">{tf.arrival.note}</div>}
        </div>
      )}

      <div className="card expl-card">
        <div className="expl"><span>✅</span> {pkg.explanation.why}</div>
        <div className="expl"><span>⚖️</span> {pkg.explanation.tradeoffs}</div>
        <div className="expl"><span>⚠️</span> {pkg.explanation.risks}</div>
      </div>

      {errors.length > 0 && (
        <div className="card conflicts">
          <b>Состыковки не сходятся — поправьте перед оплатой</b>
          {errors.map((c) => <div key={c.code} className="conflict err">⛔ {c.message} <div className="small">{c.fix}</div></div>)}
        </div>
      )}
      {warns.length > 0 && (
        <details className="card"><summary>Риски и замечания ({warns.length})</summary>
          {warns.map((c) => <div key={c.code} className="conflict warn">⚠️ {c.message} {c.fix && <div className="small muted">{c.fix}</div>}</div>)}
        </details>
      )}
      {pkg.requests?.length > 0 && <div className="card small">📝 Передадим отелю: {pkg.requests.join(", ")}</div>}

      {!extraDone && (
        <div className="card">
          <b>Что-нибудь ещё?</b>
          <textarea rows={2} value={extra} onChange={(e) => setExtra(e.target.value)} placeholder="Например: нужен ранний заезд, рейс побыстрее, на день позже" />
          <div className="row gap">
            <button className="secondary" onClick={() => setExtraDone(true)}>Пропустить</button>
            <button className="primary" disabled={!extra.trim() || busy} onClick={async () => { await patch({ anything_else: extra }); setExtra(""); setExtraDone(true); }}>Учесть</button>
          </div>
        </div>
      )}

      <div className="total-bar">
        <div>
          <div className="small muted">Итого за {g.label}</div>
          <b className="big-price">{rub(pkg.total.total_rub)}</b>
          {pkg.budget && <div className={`small ${pkg.total.total_rub <= pkg.budget ? "ok" : "warn"}`}>бюджет {rub(pkg.budget)}</div>}
        </div>
        <button className="primary" disabled={!pkg.can_book || busy} onClick={onPay}>{pkg.can_book ? "К оплате" : "Есть конфликт"}</button>
      </div>
    </div>
  );
}
