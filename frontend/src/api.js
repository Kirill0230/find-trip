// Клиент API бэкенда. Долгие ответы (подборка, пакет) читаем как SSE через fetch.

async function json(method, url, body) {
  const res = await fetch(url, {
    method,
    headers: body ? { "Content-Type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const err = new Error(data.detail || data.error || `HTTP ${res.status}`);
    err.status = res.status;
    err.data = data;
    throw err;
  }
  return data;
}

async function sse(method, url, body, onEvent) {
  const res = await fetch(url, {
    method,
    headers: body ? { "Content-Type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    let idx;
    while ((idx = buf.indexOf("\n\n")) >= 0) {
      const chunk = buf.slice(0, idx);
      buf = buf.slice(idx + 2);
      let event = "message";
      let data = "";
      for (const line of chunk.split("\n")) {
        if (line.startsWith("event: ")) event = line.slice(7);
        else if (line.startsWith("data: ")) data += line.slice(6);
      }
      onEvent(event, data ? JSON.parse(data) : null);
    }
  }
}

export const api = {
  createSession: (text) => json("POST", "/session", { text }),
  getSession: (sid) => json("GET", `/session/${sid}`),
  feed: (sid, n = 3) => json("GET", `/session/${sid}/feed?n=${n}`),
  swipe: (sid, video_id, direction) => json("POST", `/session/${sid}/swipe`, { video_id, direction }),
  shortlist: (sid, onEvent) => sse("GET", `/session/${sid}/shortlist`, null, onEvent),
  correct: (sid, text, context) => json("POST", `/session/${sid}/correct`, { text, context }),
  package: (sid, hotel_id, onEvent) => sse("POST", `/session/${sid}/package`, { hotel_id }, onEvent),
  patchPackage: (sid, patch) => json("PATCH", `/session/${sid}/package`, patch),
  book: (sid, body) => json("POST", `/session/${sid}/book`, body),
  metrics: () => json("GET", "/metrics"),
};

export const rub = (n) => (n == null ? "—" : `${Math.round(n).toLocaleString("ru-RU")} ₽`);

export const dur = (m) => {
  if (m == null) return "";
  const h = Math.floor(m / 60);
  const mm = m % 60;
  return h ? `${h} ч ${String(mm).padStart(2, "0")} мин` : `${mm} мин`;
};

const MONTHS = ["янв", "фев", "мар", "апр", "мая", "июн", "июл", "авг", "сен", "окт", "ноя", "дек"];

export const dt = (s) => {
  if (!s) return "";
  const [d, t] = s.split("T");
  const [, m, day] = d.split("-");
  return `${Number(day)} ${MONTHS[Number(m) - 1]}${t ? `, ${t.slice(0, 5)}` : ""}`;
};

export const time = (s) => (s ? s.split("T")[1]?.slice(0, 5) : "");
