import React from "react";
import { dt, rub } from "../api.js";

export default function Done({ order, onRestart }) {
  return (
    <div className="screen done">
      <div className="success">🎉</div>
      <h2>Поездка оформлена</h2>
      <div className="muted">Заказ <b>{order.order_id}</b> · {rub(order.paid_rub)} · демо-оплата</div>

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
