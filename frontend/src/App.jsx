import React, { useState } from "react";
import { api, userId } from "./api.js";
import { CorrectionBar, Toast, Trace } from "./components/common.jsx";
import { ProfilePanel, ThinkingPanel } from "./components/panels.jsx";
import Checkout from "./screens/Checkout.jsx";
import Done from "./screens/Done.jsx";
import Feed from "./screens/Feed.jsx";
import Payment from "./screens/Payment.jsx";
import Query from "./screens/Query.jsx";
import Shortlist from "./screens/Shortlist.jsx";

const TITLES = { query: "Запрос", feed: "Лента", shortlist: "Подборка", checkout: "Доуточнение", payment: "Оплата", done: "Готово" };
const BACK = { feed: "query", shortlist: "feed", checkout: "shortlist", payment: "checkout" };

export default function App() {
  const [screen, setScreen] = useState("query");
  const [session, setSession] = useState(null);
  const [initialFeed, setInitialFeed] = useState(null);
  const [feedVersion, setFeedVersion] = useState(0);
  const [likes, setLikes] = useState({ total: 0, since: 0 });
  const [shortlistOverride, setShortlistOverride] = useState(null);
  const [shortlistKey, setShortlistKey] = useState(0);
  const [hotelId, setHotelId] = useState(null);
  const [pkg, setPkg] = useState(null);
  const [order, setOrder] = useState(null);
  const [busy, setBusy] = useState(false);
  const [toast, setToast] = useState(null);
  const [muted, setMuted] = useState(false);
  const [traceOpen, setTraceOpen] = useState(false);
  const [trace, setTrace] = useState([]);
  const [events, setEvents] = useState([]);
  const [priorities, setPriorities] = useState([]);
  const [destStats, setDestStats] = useState({});

  const log = (...items) => setEvents((e) => [...e, ...items].slice(-80));
  const fromTrace = (tr) => (tr || []).map((t) => ({ kind: t.kind, label: t.label, ms: t.ms }));

  const sid = session?.session_id;

  const submit = async (text) => {
    setBusy(true);
    setPkg(null); setOrder(null); setHotelId(null); setShortlistOverride(null); setInitialFeed(null);
    setLikes({ total: 0, since: 0 });
    try {
      setEvents([{ kind: "user", label: `Запрос: «${text}»` }]);
      setDestStats({});
      const s = await api.createSession(text, userId());
      setSession(s);
      setTrace(s.trace);
      setPriorities(s.priorities || []);
      if (s.memory_used?.length) log({ kind: "code", label: "Вспомнил с прошлого раза", detail: s.memory_used.join(", ") });
      log(...fromTrace(s.trace), {
        kind: "code",
        label: s.mode === "clarify" ? "Остановился и задаю один вопрос" : s.mode === "checkout" ? "Всё известно — сразу пакет" : `Лента: ${s.destinations?.length || 0} направл.`,
        detail: s.mode === "clarify" ? s.clarify?.question : s.destinations?.map((d) => d.name).join(", "),
      });
      if (s.mode === "checkout") {
        setPkg(s.package);
        setHotelId(s.package.hotel.hotel_id);
        setToast({ text: "Всё известно — ленту пропускаю и сразу собираю пакет." });
        setScreen("checkout");
      } else if (s.mode === "feed") {
        api.feed(s.session_id, 3).then(setInitialFeed); // предзагрузка первых роликов, пока человек читает
      }
    } catch (e) {
      setToast({ text: "Ошибка: " + e.message, kind: "warn" });
    } finally {
      setBusy(false);
    }
  };

  const contextFor = () => {
    if (session?.mode === "clarify" && screen === "query") return "clarify";
    if (screen === "shortlist") return "shortlist";
    if (screen === "checkout" || screen === "payment") return "package";
    return "feed";
  };

  const correct = async (text) => {
    if (!sid) return;
    setBusy(true);
    try {
      log({ kind: "user", label: `Правка: «${text}»` });
      const res = await api.correct(sid, text, contextFor());
      if (!res.understood) {
        log({ kind: "code", label: "Не понял правку — переспрашиваю" });
        setToast({ text: res.message, kind: "warn" });
        return;
      }
      setPriorities(res.priorities || []);
      log(...fromTrace(res.trace), {
        kind: "tool",
        label: `Дельта: ${(res.notes || []).join("; ") || "—"}`,
        detail: res.tool_calls?.length ? `вызваны: ${[...new Set(res.tool_calls)].join(", ")}` : "без новых запросов к инструментам",
      });
      setToast({ text: res.message, detail: res.package?.changes?.find((c) => c.startsWith("отель заменён")) });
      setTrace((t) => [...t, ...(res.trace || [])].slice(-30));
      setSession((s) => ({ ...(res.session || s), chips: res.chips, priorities: res.priorities }));
      if (res.type === "package") setPkg(res.package);
      if (res.type === "shortlist") setShortlistOverride(res.shortlist);
      if (res.type === "feed") { setInitialFeed(null); setFeedVersion((v) => v + 1); }
      if (res.type === "clarify") setScreen("query");
    } catch (e) {
      setToast({ text: e.message, kind: "warn" });
    } finally {
      setBusy(false);
    }
  };

  const openShortlist = () => {
    setShortlistOverride(null);
    setShortlistKey((k) => k + 1);
    setLikes((l) => ({ ...l, since: 0 }));
    setScreen("shortlist");
  };

  const book = async (body) => {
    setBusy(true);
    try {
      const o = await api.book(sid, body);
      log({ kind: "tool", label: `book → заказ ${o.order_id}`, detail: "оплата демонстрационная" });
      setOrder(o);
      setScreen("done");
    } catch (e) {
      if (e.status === 409) {
        setPkg(e.data.package);
        setToast({ text: "Состыковка не сходится — вернул на доуточнение.", kind: "warn" });
        setScreen("checkout");
      } else setToast({ text: e.message, kind: "warn" });
    } finally {
      setBusy(false);
    }
  };

  const restart = () => {
    setScreen("query"); setSession(null); setPkg(null); setOrder(null); setInitialFeed(null);
  };

  const showBar = sid && ["feed", "shortlist", "checkout"].includes(screen);

  return (
    <>
    <ProfilePanel session={session} priorities={priorities} destStats={destStats} />
    <ThinkingPanel events={events} />
    <div className="app">
      <header className="top">
        {BACK[screen] && session ? <button className="ghost" onClick={() => setScreen(BACK[screen] === "shortlist" && !likes.total && session?.mode === "checkout" ? "query" : BACK[screen])} aria-label="Назад">‹</button> : <span className="brand">О!</span>}
        <div className="title">{TITLES[screen]}</div>
        <div className="row gap">
          {screen === "feed" && <button className="ghost" onClick={() => { setMuted((m) => !m); window.speechSynthesis?.cancel(); }} aria-label="Звук">{muted ? "🔇" : "🔊"}</button>}
          {sid && <button className="ghost" onClick={() => setTraceOpen(true)} aria-label="Как думал агент">🧠</button>}
        </div>
      </header>

      <main>
        {screen === "query" && (
          <Query session={session} busy={busy} onSubmit={submit} onOption={correct}
            onStart={() => setScreen("feed")} onPickDest={(d) => correct(`хочу ${d.name}`)} />
        )}
        {screen === "feed" && sid && (
          <Feed sid={sid} initial={initialFeed} version={feedVersion} muted={muted} likes={likes.total} likesSince={likes.since}
            onSwipeResult={(r, info) => {
              setLikes({ total: r.likes, since: r.likes_since_shortlist });
              setPriorities(r.priorities || []);
              if (r.dest_stats) setDestStats(r.dest_stats);
              if (info) log({ kind: "code", label: `${info.dir === "right" ? "❤" : info.dir === "left" ? "✕" : "👀 досмотрел"} ${info.name}`, detail: info.dir === "right" ? "веса сдвинуты к признакам отеля (+0.2·(f−0.5))" : info.dir === "left" ? "веса от признаков отеля (−0.1·(f−0.5)), место ниже в ленте" : "слабый плюс 0.05" });
              if (r.ask) log({ kind: "code", label: "Серия пропусков — спрашиваю, что поменять" });
              if (r.show_shortlist) log({ kind: "code", label: "3 лайка — собираю подборку с объяснениями" });
            }} onShortlist={openShortlist} onAnswer={correct} />
        )}
        {screen === "shortlist" && sid && (
          <Shortlist key={shortlistKey} sid={sid} override={shortlistOverride}
            onBook={(h) => { if (!pkg || pkg.hotel.hotel_id !== h) setPkg(null); setHotelId(h); setScreen("checkout"); }}
            onMore={() => setScreen("feed")} />
        )}
        {screen === "checkout" && sid && (
          <Checkout sid={sid} session={session} pkg={pkg} setPkg={setPkg} hotelId={hotelId} onToast={setToast} onPay={() => setScreen("payment")} log={log} />
        )}
        {screen === "payment" && pkg && <Payment pkg={pkg} busy={busy} onBook={book} />}
        {screen === "done" && order && <Done sid={sid} order={order} onOrder={setOrder} onRestart={restart} log={log} />}
      </main>

      {showBar && <CorrectionBar context={contextFor()} onSend={correct} busy={busy} />}
      <Toast toast={toast} onClose={() => setToast(null)} />
      <Trace trace={trace} open={traceOpen} onClose={() => setTraceOpen(false)} />
    </div>
    </>
  );
}
