import React, { useState } from "react";
import { api, rub, userId } from "../api.js";
import { Chips } from "../components/common.jsx";

const EXAMPLES = [
  "Хочу к тёплому морю в ноябре, недорого, с ребёнком, без долгих перелётов",
  "Выходные в Питере вдвоём, из Москвы на Сапсане, 14–16 ноября",
  "Азур Бэй Сочи, номер делюкс с видом на море, 3–10 декабря, двое взрослых",
  "Новый год в горах с собакой, из Питера, бюджет 150 тысяч на двоих",
];

export default function Query({ session, busy, onSubmit, onOption, onStart, onPickDest }) {
  const [text, setText] = useState("");
  const submit = (e) => {
    e?.preventDefault();
    if (text.trim() && !busy) onSubmit(text.trim());
  };
  const clarify = session?.mode === "clarify" ? session.clarify : null;

  return (
    <div className="screen query">
      {!session && <video className="hero-bg" src="/videos/maldives_island.mp4" autoPlay muted loop playsInline aria-hidden="true" />}
      <div className="hero">
        <div className="logo">О!дин запрос</div>
        <p className="muted">Скажи, куда хочешь отправиться. Остальное устроит агент.</p>
      </div>

      <form onSubmit={submit} className="ask">
        <textarea
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && !e.shiftKey && submit(e)}
          placeholder="Например: хочу к морю в декабре вдвоём, до 150 тысяч"
          rows={3}
          aria-label="Опишите поездку"
        />
        <button className="primary" disabled={busy || !text.trim()}>{busy ? "Агент думает…" : "Найти поездку"}</button>
      </form>

      {!session && (
        <div className="examples">
          {EXAMPLES.map((ex) => (
            <button key={ex} className="example" onClick={() => { setText(ex); onSubmit(ex); }} disabled={busy}>{ex}</button>
          ))}
        </div>
      )}

      {session && (
        <div className="understood card">
          <div className="small muted">Агент понял</div>
          {session.memory_used?.length > 0 && (
            <div className="memory small">🧠 С возвращением! Помню: {session.memory_used.join(", ")}.{" "}
              <button className="link" onClick={() => api.forget(userId()).then(() => alert("Готово: забыл ваши предпочтения."))}>Забыть</button>
            </div>
          )}
          <Chips chips={session.chips} />
          {session.chips.some((c) => c.source === "default") && (
            <div className="small muted">Пунктиром — то, что я додумал. Проверим перед оплатой, переспрашивать сейчас не буду.</div>
          )}
        </div>
      )}

      {clarify && (
        <div className="card clarify">
          <div className="q">🤔 {clarify.question}</div>
          <div className="options">
            {clarify.options.map((o) => (
              <button key={o.label} className="option" onClick={() => onOption(o.text)} disabled={busy}>{o.label}</button>
            ))}
          </div>
        </div>
      )}

      {session && session.mode === "feed" && session.destinations?.length > 0 && (
        <div className="dests">
          <div className="small muted">{session.destinations.length > 1 ? "Куда можно поехать — нажмите, чтобы смотреть только это" : "Направление"}</div>
          {session.destinations.map((d) => (
            <button key={d.dest_id} className="dest card" onClick={() => session.destinations.length > 1 && onPickDest(d)} disabled={busy}>
              {d.video && <video className="dest-thumb" src={d.video + "#t=2"} muted playsInline preload="metadata" aria-hidden="true" />}
              <div className="row between"><b>{d.name} <span className="muted small">{d.country}</span></b><span className="price">от {rub(d.price_from)}</span></div>
              <div className="small">{d.why}</div>
              <div className="small muted">
                {d.weather?.water ? `🌊 +${d.weather.water}° ` : ""}{d.weather?.air != null ? `☀️ ${d.weather.air > 0 ? "+" : ""}${d.weather.air}° ` : ""}
                {d.travel?.label ? `· ${d.travel.label} ` : ""}· {d.hotels} отел.{d.weather?.real ? " · погода: Open-Meteo" : " · погода: климат. норма"}
              </div>
            </button>
          ))}
        </div>
      )}

      {session && session.mode === "feed" && (
        <button className="primary big" onClick={onStart}>Смотреть ролики отелей →</button>
      )}
    </div>
  );
}
