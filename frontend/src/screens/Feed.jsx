import React, { useCallback, useEffect, useRef, useState } from "react";
import { api, rub } from "../api.js";
import Scene from "../components/Scene.jsx";
import { useSpeech } from "../components/common.jsx";

const STARS = (n) => "★".repeat(n);

export default function Feed({ sid, initial, version, muted, likes, likesSince, onSwipeResult, onShortlist }) {
  const [queue, setQueue] = useState(initial?.items || []);
  const [exhausted, setExhausted] = useState(false);
  const [drag, setDrag] = useState(0);
  const [leaving, setLeaving] = useState(null);
  const loading = useRef(false);
  const start = useRef(null);
  const watchedSent = useRef(new Set());

  const refill = useCallback(async (reset = false) => {
    if (loading.current) return;
    loading.current = true;
    try {
      const res = await api.feed(sid, 3);
      setQueue((q) => (reset ? res.items : [...q, ...res.items.filter((it) => !q.some((x) => x.video.video_id === it.video.video_id))]));
      setExhausted(res.exhausted);
    } finally {
      loading.current = false;
    }
  }, [sid]);

  // правка условий сбрасывает очередь — лента пересобирается под новый профиль
  const first = useRef(true);
  useEffect(() => {
    if (first.current) { first.current = false; if (!queue.length) refill(true); return; }
    refill(true);
  }, [version]);

  useEffect(() => {
    if (queue.length < 2 && !exhausted) refill();
  }, [queue.length, exhausted, refill]);

  const cur = queue[0];
  const { charIndex, ended } = useSpeech(cur?.voiceover, !muted);

  useEffect(() => {
    if (cur && ended && !watchedSent.current.has(cur.video.video_id)) {
      watchedSent.current.add(cur.video.video_id);
      api.swipe(sid, cur.video.video_id, "watched").then(onSwipeResult).catch(() => {});
    }
  }, [ended, cur]);

  const decide = async (dir) => {
    if (!cur || leaving) return;
    setLeaving(dir);
    window.speechSynthesis?.cancel();
    const id = cur.video.video_id;
    const resP = api.swipe(sid, id, dir === "right" ? "right" : "left");
    setTimeout(() => {
      setQueue((q) => q.slice(1));
      setLeaving(null);
      setDrag(0);
    }, 260);
    const res = await resP;
    onSwipeResult(res);
    if (res.show_shortlist) setTimeout(onShortlist, 300);
  };

  const onDown = (e) => { start.current = e.clientX; };
  const onMove = (e) => { if (start.current != null) setDrag(e.clientX - start.current); };
  const onUp = () => {
    if (start.current == null) return;
    start.current = null;
    if (drag > 90) decide("right");
    else if (drag < -90) decide("left");
    else setDrag(0);
  };

  useEffect(() => {
    const k = (e) => { if (e.target.tagName === "INPUT") return; if (e.key === "ArrowRight") decide("right"); if (e.key === "ArrowLeft") decide("left"); };
    window.addEventListener("keydown", k);
    return () => window.removeEventListener("keydown", k);
  });

  if (!cur) {
    return (
      <div className="screen feed empty">
        {exhausted ? (
          <div className="card">
            <p>Под ваши условия больше вариантов нет.</p>
            <button className="primary" onClick={onShortlist}>Показать подборку</button>
          </div>
        ) : <div className="loader">Подбираю ролики…</div>}
      </div>
    );
  }

  const sentences = cur.subtitles;
  let acc = 0;
  let subIdx = sentences.length - 1;
  for (let i = 0; i < sentences.length; i++) {
    acc += sentences[i].length + 2;
    if (charIndex < acc) { subIdx = i; break; }
  }
  const tx = leaving === "right" ? 500 : leaving === "left" ? -500 : drag;
  const h = cur.hotel;

  return (
    <div className="screen feed">
      <div
        className="reel"
        style={{ transform: `translateX(${tx}px) rotate(${tx / 25}deg)`, transition: start.current != null ? "none" : "transform .25s" }}
        onPointerDown={onDown} onPointerMove={onMove} onPointerUp={onUp} onPointerCancel={onUp} onPointerLeave={onUp}
      >
        <Scene hotel={h} video={cur.video} key={cur.video.video_id} />
        {drag > 40 && <div className="stamp like">Нравится</div>}
        {drag < -40 && <div className="stamp nope">Мимо</div>}
        <div className="reel-top">
          <span className="pill">❤ {likesSince}/3 до подборки</span>
          {cur.match != null && <span className="pill">совпадение {cur.match}%</span>}
        </div>
        <div className="reel-info">
          <div className="subs" aria-live="polite">{sentences[subIdx]}</div>
          <div className="name">{h.name} <span className="stars">{STARS(h.stars)}</span></div>
          <div className="small">{h.dest_name}, {h.area} · ⭐ {h.rating} ({h.reviews})</div>
          <div className="price-plate">
            <div><b>{rub(cur.price.total_rub)}</b> <span className="small">отель, {cur.price.nights} ноч. · {cur.price.meal}</span></div>
            {cur.price.package_est_rub && cur.price.package_est_rub !== cur.price.total_rub && <div className="small">≈ {rub(cur.price.package_est_rub)} с дорогой и трансфером</div>}
          </div>
          <div className="chips">{cur.chips.map((c) => <span key={c} className="chip dark">{c}</span>)}{cur.transport.label && <span className="chip dark">{cur.transport.mode === "train" ? "🚆" : cur.transport.mode === "flight" ? "✈️" : "📍"} {cur.transport.label}</span>}{cur.weather?.water ? <span className="chip dark">🌊 +{cur.weather.water}°</span> : null}</div>
        </div>
      </div>
      <div className="swipe-buttons">
        <button className="round nope" onClick={() => decide("left")} aria-label="Пропустить">✕</button>
        <button className="ghost small" onClick={onShortlist}>Подборка ({likes})</button>
        <button className="round like" onClick={() => decide("right")} aria-label="Нравится">❤</button>
      </div>
    </div>
  );
}
