import React, { useEffect, useRef, useState } from "react";

export const SOURCE_LABEL = { text: "из запроса", swipes: "по свайпам", default: "по умолчанию", correction: "вы поправили", llm: "понял LLM" };

export function Chips({ chips }) {
  return (
    <div className="chips">
      {chips.map((c, i) => (
        <span key={c.key + i} className={`chip src-${c.source}`} title={SOURCE_LABEL[c.source] || ""}>
          <span>{c.icon}</span> {c.label}
          {c.source === "default" && <em> · проверим</em>}
        </span>
      ))}
    </div>
  );
}

export function Priorities({ items, title = "Что агент понял о вас" }) {
  if (!items?.length) return null;
  return (
    <div className="priorities">
      <div className="muted small">{title}</div>
      <div className="chips">
        {items.map((p) => (
          <span key={p.key} className={`chip prio ${p.weight > 0 ? "pos" : "neg"}`}>
            {p.weight > 0 ? "↑" : "↓"} {p.label}
            <em> · {SOURCE_LABEL[p.source] || p.source}</em>
          </span>
        ))}
      </div>
    </div>
  );
}

const HINTS = {
  feed: ["а если на два дня позже?", "бюджет меньше", "с нами едет собака", "хочу первую линию"],
  shortlist: ["питание не важно", "важна бесплатная отмена", "бюджет меньше", "а если на два дня позже?"],
  package: ["а если на два дня позже?", "рейс побыстрее", "с нами едет собака", "нужен ранний заезд"],
};

export function CorrectionBar({ context, onSend, busy }) {
  const [text, setText] = useState("");
  const [hint, setHint] = useState(0);
  const hints = HINTS[context] || HINTS.feed;
  useEffect(() => {
    const t = setInterval(() => setHint((h) => (h + 1) % hints.length), 3500);
    return () => clearInterval(t);
  }, [hints.length]);
  const send = (e) => {
    e?.preventDefault();
    const v = text.trim();
    if (!v || busy) return;
    onSend(v);
    setText("");
  };
  return (
    <form className="correction" onSubmit={send}>
      <input value={text} onChange={(e) => setText(e.target.value)} placeholder={`Поправить: «${hints[hint]}»`} aria-label="Поправить условия" />
      <button type="submit" disabled={busy || !text.trim()} aria-label="Отправить правку">{busy ? "…" : "↵"}</button>
    </form>
  );
}

export function Toast({ toast, onClose }) {
  useEffect(() => {
    if (!toast) return;
    const t = setTimeout(onClose, toast.kind === "warn" ? 6000 : 4500);
    return () => clearTimeout(t);
  }, [toast]);
  if (!toast) return null;
  return (
    <div className={`toast ${toast.kind || ""}`} onClick={onClose} role="status">
      <div>{toast.text}</div>
      {toast.detail && <div className="small muted">{toast.detail}</div>}
    </div>
  );
}

const COMP = { hotel: "отель", hotel_price: "цена отеля", transport: "билеты", transport_choice: "выбор рейса", transfer: "трансфер", transfer_choice: "выбор трансфера", connections: "состыковки" };

export function RebuildBanner({ pkg }) {
  if (!pkg?.recomputed || pkg.recomputed.length >= 4 || !pkg.changes) return null;
  return (
    <div className="rebuild">
      <b>Пересобрал, а не искал заново.</b> Пересчитано: {pkg.recomputed.map((c) => COMP[c] || c).join(", ")}.
      {pkg.kept?.length > 0 && <> Сохранено: {pkg.kept.map((c) => COMP[c] || c).join(", ")}.</>}
    </div>
  );
}

export function Trace({ trace, open, onClose }) {
  if (!open) return null;
  return (
    <div className="sheet" onClick={onClose}>
      <div className="sheet-body" onClick={(e) => e.stopPropagation()}>
        <div className="row between"><h3>Как думал агент</h3><button className="ghost" onClick={onClose}>✕</button></div>
        <p className="small muted">Узлы графа (повторяет BPMN). LLM — только язык; фильтры, ранжирование, состыковки и оплата — обычный код.</p>
        <ol className="trace">
          {(trace || []).map((t, i) => (
            <li key={i}><span className={`kind ${t.kind}`}>{t.kind}</span> {t.label} <span className="muted small">{t.ms} мс</span></li>
          ))}
        </ol>
      </div>
    </div>
  );
}

export function useSpeech(text, enabled) {
  const [idx, setIdx] = useState(0);
  const [ended, setEnded] = useState(false);
  const boundary = useRef(false);
  useEffect(() => {
    setIdx(0);
    setEnded(false);
    boundary.current = false;
    if (!text) return;
    const synth = window.speechSynthesis;
    let fallback;
    if (enabled && synth) {
      const u = new SpeechSynthesisUtterance(text);
      u.lang = "ru-RU";
      u.rate = 1.07;
      const voice = synth.getVoices().find((v) => v.lang?.toLowerCase().startsWith("ru"));
      if (voice) u.voice = voice;
      u.onboundary = (e) => {
        boundary.current = true;
        setIdx(e.charIndex);
      };
      u.onend = () => setEnded(true);
      synth.cancel();
      synth.speak(u);
    }
    // субтитры идут и без звука: по таймеру, если браузер не шлёт boundary
    const start = Date.now();
    fallback = setInterval(() => {
      if (boundary.current) return;
      const chars = Math.floor(((Date.now() - start) / 1000) * 14);
      setIdx(chars);
      if (chars > text.length + 20) setEnded(true);
    }, 250);
    return () => {
      clearInterval(fallback);
      synth?.cancel();
    };
  }, [text, enabled]);
  return { charIndex: idx, ended };
}
