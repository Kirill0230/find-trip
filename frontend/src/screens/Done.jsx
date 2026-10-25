import React, { useState } from "react";
import { api, dt, rub } from "../api.js";

const QUICK = ["Нужен ранний заезд", "На день позже", "Трансфер с табличкой", "С нами едет ещё ребёнок"];

function Manage({ sid, order, onOrder, log }) {
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [reply, setReply] = useState(null);

  const ask = async (t) => {
    if (!t.trim() || busy) return;
    setBusy(true);
    log?.({ kind: "user", label: `После покупки: «${t}»` });
    try {
      const r = await api.orderRequest(sid, t);
      setReply(r);
      log?.({ kind: "code", label: r.type === "proposal" ? `Пересчитал заказ: ${r.message}` : r.message });
      setText("");
    } finally {
      setBusy(false);
    }
  };
  const confirm = async () => {
    setBusy(true);
    try {
      const o = await api.orderConfirm(sid);
      onOrder(o);
      log?.({ kind: "tool", label: "Заказ изменён, ваучеры перевыпущены" });
      setReply({ type: "info", message: "Готово — заказ изменён, новые ваучеры ниже." });
    } finally {
      setBusy(false);
    }
  };
  const p = reply?.proposal;

  return (
    <div className="card manage">
      <b>Управление поездкой</b>
      <div className="small muted">Агент работает и после оплаты: попросите что-нибудь — посчитаю доплату или возврат по правилам отеля и перевозчика.</div>
      <div className="chips">
        {QUICK.map((q) => <button key={q} className="chip quick" disabled={busy} onClick={() => ask(q)}>{q}</button>)}
        <a className="chip quick" href={api.calendarUrl(sid)} download>📅 Напоминания в календарь</a>
      </div>
      <form className="row gap" onSubmit={(e) => { e.preventDefault(); ask(text); }}>
        <input value={text} onChange={(e) => setText(e.target.value)} placeholder="Что поменять в заказе?" aria-label="Что поменять в заказе" />
        <button className="primary" disabled={busy || !text.trim()}>{busy ? "…" : "→"}</button>
      </form>
      {reply && reply.type !== "proposal" && <div className={`notice small ${reply.type === "ask" ? "warn" : ""}`}>{reply.message}</div>}
      {p && (
        <div className="proposal">
          <div><b>{p.notes.join("; ")}</b></div>
          <div className="row between small"><span>Поездка была</span><span>{rub(p.old_total)}</span></div>
          <div className="row between small"><span>Станет</span><span>{rub(p.new_total)}</span></div>
          {p.fee > 0 && <div className="row between small warn"><span>Штрафы за изменение</span><span>{rub(p.fee)}</span></div>}
          {p.fee_reasons.map((r) => <div key={r} className="small muted">· {r}</div>)}
          <div className="row between total"><b>{p.to_pay >= 0 ? "Доплатить" : "Вернём"}</b><b>{rub(Math.abs(p.to_pay))}</b></div>
          {p.conflicts.length > 0 && p.conflicts.map((c) => <div key={c.code} className="conflict err small">⛔ {c.message}</div>)}
          <div className="row gap">
            <button className="secondary" onClick={() => setReply(null)}>Отмена</button>
            <button className="primary" disabled={!p.can_apply || busy} onClick={confirm}>Подтвердить (демо)</button>
          </div>
        </div>
      )}
      {order.service_requests?.map((r, i) => <div key={i} className="small">🛎 {r.what}: {r.status}</div>)}
    </div>
  );
}

export default function Done({ sid, order, onOrder, onRestart, log }) {
  return (
    <div className="screen done">
      <div className="success">🎉</div>
      <h2>Поездка оформлена</h2>
      <div className="muted">Заказ <b>{order.order_id}</b> · {rub(order.paid_rub)} · демо-оплата</div>

      <Manage sid={sid} order={order} onOrder={onOrder} log={log} />

      <div className="card">
        <b>План по часам</b>
        <ol className="timeline">
          {order.timeline.map((e, i) => (
            <li key={i}>
              <span className="when">{dt(e.at)}</span>
              <span className="what">{e.icon} {e.title}<div className="small muted">{e.detail}</div></span>
            </li>
          ))}
        </ol>
      </div>

      <div className="card">
        <b>Ваучеры</b>
        {order.vouchers.map((v) => (
          <div key={v.code + v.title} className="voucher">
            <div className="row between"><span>{v.title}</span><code>{v.code}</code></div>
            <div className="small muted">{v.details}</div>
          </div>
        ))}
      </div>
      {order.package?.requests?.length > 0 && <div className="card small">📝 Отелю передано: {order.package.requests.join(", ")}</div>}
      <button className="secondary big" onClick={onRestart}>Новый запрос</button>
    </div>
  );
}
