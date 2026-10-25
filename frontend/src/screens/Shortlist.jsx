import React, { useEffect, useState } from "react";
import { api, dur, rub } from "../api.js";
import Scene from "../components/Scene.jsx";
import { Priorities } from "../components/common.jsx";

export default function Shortlist({ sid, override, onBook, onMore }) {
  const [items, setItems] = useState([]);
  const [status, setStatus] = useState("Собираю подборку…");
  const [prio, setPrio] = useState([]);
  const [removed, setRemoved] = useState([]);
  const [view, setView] = useState("cards");

  useEffect(() => {
    if (override) {
      setItems(override.items);
      setPrio(override.priorities);
      setRemoved(override.removed || []);
      setStatus(null);
      return;
    }
    let alive = true;
    setItems([]);
    api.shortlist(sid, (ev, data) => {
      if (!alive) return;
      if (ev === "status") setStatus(data.text);
      if (ev === "priorities") setPrio(data);
      if (ev === "item") setItems((x) => [...x, data]);
      if (ev === "removed") setRemoved(data);
      if (ev === "done") setStatus(null);
    }).catch((e) => setStatus("Ошибка: " + e.message));
    return () => { alive = false; };
  }, [sid, override]);

  return (
    <div className="screen shortlist">
      <h2>Подборка</h2>
      <Priorities items={prio} title="Приоритеты, которые агент вывел из запроса и свайпов — поправьте внизу, если не так" />
      {status && <div className="loader small">{status}</div>}
      {items.length > 1 && (
        <div className="seg view-toggle">
          <button className={view === "cards" ? "on" : ""} onClick={() => setView("cards")}>Карточки</button>
          <button className={view === "table" ? "on" : ""} onClick={() => setView("table")}>Сравнить</button>
        </div>
      )}
      {view === "table" && items.length > 1 && <Compare items={items} onBook={onBook} />}
      {removed.length > 0 && (
        <div className="notice small">Убрал из подборки: {removed.map((r) => `${r.name} (${r.why})`).join("; ")}</div>
      )}
      {view === "cards" && items.map((it) => (
        <div key={it.hotel.id} className="card option-card">
          <div className="thumb"><Scene hotel={it.hotel} video={it.video} playing={false} compact /></div>
          <div className="body">
            <div className="row between">
              <b>{it.hotel.name} <span className="stars">{"★".repeat(it.hotel.stars)}</span></b>
              <span className="match">{it.match}%</span>
            </div>
            <div className="small muted">{it.hotel.dest_name} · {it.hotel.area} · ⭐ {it.hotel.rating}</div>
            <div className="badges">
              {it.agent_pick && <span className="badge pick">добавил агент</span>}
              {it.badges.map((b) => <span key={b} className="badge">{b}</span>)}
            </div>
            <div className="price-line"><b>{rub(it.price.package_est_rub || it.price.total_rub)}</b> <span className="small muted">за всё · отель {rub(it.price.total_rub)}, {it.price.meal}, {it.price.cancel}</span></div>
            <div className="expl"><span>✅</span> {it.explanation.why}</div>
            <div className="expl"><span>⚖️</span> {it.explanation.tradeoffs}</div>
            <div className="expl"><span>⚠️</span> {it.explanation.risks}</div>
            <button className="primary" onClick={() => onBook(it.hotel.id)}>Оформить поездку</button>
          </div>
        </div>
      ))}
      <button className="secondary big" onClick={onMore}>Смотреть ещё ролики</button>
    </div>
  );
}

const LINE = { 1: "1-я", 2: "2-я", 3: "3-я", 0: "нет пляжа" };

function Compare({ items, onBook }) {
  const rows = [
    ["Совпадение", (it) => it.match, (it) => `${it.match}%`, "max"],
    ["За всё", (it) => it.price.package_est_rub || it.price.total_rub, (it) => rub(it.price.package_est_rub || it.price.total_rub), "min"],
    ["В пути", (it) => it.transport.minutes ?? 1e9, (it) => (it.transport.minutes ? dur(it.transport.minutes) : "—"), "min"],
    ["Рейтинг", (it) => it.hotel.rating, (it) => `⭐ ${it.hotel.rating}`, "max"],
    ["Линия", (it) => (it.hotel.beach_line || 9), (it) => LINE[it.hotel.beach_line] ?? "—", "min"],
    ["Питание", null, (it) => it.price.meal],
    ["Отмена", null, (it) => it.price.cancel],
    ["Вода", (it) => it.weather?.water ?? -1, (it) => (it.weather?.water ? `+${it.weather.water}°` : "—"), "max"],
  ];
  return (
    <div className="card compare">
      <table>
        <thead>
          <tr><th />{items.map((it) => <th key={it.hotel.id}>{it.hotel.name}<div className="muted small">{it.hotel.dest_name}</div></th>)}</tr>
        </thead>
        <tbody>
          {rows.map(([label, val, show, best]) => {
            const vals = val ? items.map(val) : [];
            const target = best === "max" ? Math.max(...vals) : best === "min" ? Math.min(...vals) : null;
            return (
              <tr key={label}>
                <td className="muted">{label}</td>
                {items.map((it, i) => <td key={it.hotel.id} className={val && vals[i] === target && new Set(vals).size > 1 ? "best" : ""}>{show(it)}</td>)}
              </tr>
            );
          })}
          <tr><td />{items.map((it) => <td key={it.hotel.id}><button className="primary small-btn" onClick={() => onBook(it.hotel.id)}>Оформить</button></td>)}</tr>
        </tbody>
      </table>
    </div>
  );
}
