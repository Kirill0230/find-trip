import React from "react";
import { SOURCE_LABEL } from "./common.jsx";

// Боковые панели для широкого экрана (демо на ноутбуке): слева — что агент знает о поездке,
// справа — что он делает прямо сейчас. На телефоне скрыты, там это кнопка 🧠.

export function ProfilePanel({ session, priorities, destStats }) {
  if (!session) {
    return (
      <aside className="side left">
        <div className="side-title">Профиль поездки</div>
        <p className="muted small">Напишите запрос — здесь появится то, что агент понял: даты, бюджет, гости, ограничения и приоритеты.</p>
      </aside>
    );
  }
  const stats = Object.entries(destStats || {});
  return (
    <aside className="side left">
      <div className="side-title">Профиль поездки</div>
      <div className="side-block">
        {session.chips.map((c, i) => (
          <div key={c.key + i} className={`prof-row src-${c.source}`}>
            <span>{c.icon}</span>
            <span className="grow">{c.label}</span>
            <span className="tag">{SOURCE_LABEL[c.source] || c.source}</span>
          </div>
        ))}
      </div>
      {priorities?.length > 0 && (
        <>
          <div className="side-title sm">Приоритеты (веса −1…1)</div>
          <div className="side-block">
            {priorities.map((p) => (
              <div key={p.key} className="weight">
                <div className="row between small"><span>{p.label}</span><span className="muted">{p.weight > 0 ? "+" : ""}{p.weight.toFixed(2)} · {SOURCE_LABEL[p.source] || p.source}</span></div>
                <div className="bar"><div className={p.weight > 0 ? "pos" : "neg"} style={{ width: `${Math.min(100, Math.abs(p.weight) * 100)}%` }} /></div>
              </div>
            ))}
          </div>
        </>
      )}
      {stats.length > 0 && (
        <>
          <div className="side-title sm">Реакция на места</div>
          <div className="side-block">
            {stats.map(([name, v]) => (
              <div key={name} className="row between small dest-stat">
                <span>{name}</span>
                <span><span className="like">❤ {v.like}</span> <span className="nope">✕ {v.skip}</span></span>
              </div>
            ))}
          </div>
        </>
      )}
    </aside>
  );
}

const KIND = { llm: "язык", tool: "инструмент", code: "код", user: "вы" };

export function ThinkingPanel({ events }) {
  return (
    <aside className="side right">
      <div className="side-title">Агент думает</div>
      <p className="muted small">LLM — только язык; фильтры, ранжирование, состыковки и оплата — обычный код.</p>
      <ol className="events">
        {[...events].reverse().slice(0, 40).map((e, i) => (
          <li key={events.length - i} className={`ev ev-${e.kind}`}>
            <span className={`kind ${e.kind}`}>{KIND[e.kind] || e.kind}</span>
            <span className="grow">{e.label}{e.detail && <div className="muted small">{e.detail}</div>}</span>
            {e.ms != null && <span className="muted small">{e.ms} мс</span>}
          </li>
        ))}
      </ol>
    </aside>
  );
}
