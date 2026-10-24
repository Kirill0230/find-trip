import React, { useState } from "react";
import { rub } from "../api.js";

export default function Payment({ pkg, onBook, busy }) {
  const n = pkg.guests.adults + pkg.guests.children_ages.length;
  const [names, setNames] = useState(Array.from({ length: n }, () => ""));
  const [email, setEmail] = useState("");
  const [phone, setPhone] = useState("");
  const ok = names.every((x) => x.trim().length > 1);

  return (
    <div className="screen payment">
      <h2>Оформление</h2>
      <div className="demo-banner">Оплата демонстрационная: деньги не списываются, бронь имитируется.</div>
      <div className="card">
        <div className="row between"><span>Отель · {pkg.hotel.nights} ноч.</span><span>{rub(pkg.total.hotel_rub)}</span></div>
        {pkg.total.transport_rub > 0 && <div className="row between"><span>Билеты туда-обратно</span><span>{rub(pkg.total.transport_rub)}</span></div>}
        {pkg.total.transfer_rub > 0 && <div className="row between"><span>Трансферы</span><span>{rub(pkg.total.transfer_rub)}</span></div>}
        <div className="row between total"><b>Итого</b><b>{rub(pkg.total.total_rub)}</b></div>
      </div>
      <div className="card">
        <b>Гости</b>
        {names.map((v, i) => (
          <input key={i} value={v} placeholder={i < pkg.guests.adults ? `Взрослый ${i + 1}: имя и фамилия` : `Ребёнок ${i - pkg.guests.adults + 1}: имя и фамилия`}
            onChange={(e) => setNames((a) => a.map((x, j) => (j === i ? e.target.value : x)))} />
        ))}
        <input type="email" value={email} onChange={(e) => setEmail(e.target.value)} placeholder="Email для ваучеров (необязательно)" />
        <input type="tel" value={phone} onChange={(e) => setPhone(e.target.value)} placeholder="Телефон (необязательно)" />
      </div>
      <button className="primary big" disabled={!ok || busy} onClick={() => onBook({ guests: names.map((name) => ({ name })), email, phone })}>
        {busy ? "Оформляю…" : `Оплатить ${rub(pkg.total.total_rub)} (демо)`}
      </button>
    </div>
  );
}
