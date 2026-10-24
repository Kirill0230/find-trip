import React, { useEffect, useMemo, useState } from "react";

// Процедурный «ролик»: слайдшоу из 3 иллюстраций с плавным наездом.
// Это НЕ фото отеля — в кадре всегда стоит метка. Если у ролика есть url, играем настоящее видео.

function rng(seedStr) {
  let h = 2166136261;
  for (const ch of seedStr) h = Math.imul(h ^ ch.charCodeAt(0), 16777619);
  return () => {
    h = Math.imul(h ^ (h >>> 15), 2246822507);
    h = Math.imul(h ^ (h >>> 13), 3266489909);
    return ((h ^= h >>> 16) >>> 0) / 4294967296;
  };
}

const SKIES = [
  ["#64b5f6", "#bbdefb"],
  ["#ff8a65", "#ffe0b2"],
  ["#4fc3f7", "#e1f5fe"],
  ["#7e57c2", "#ffab91"],
  ["#1e3a5f", "#5c6bc0"],
];

function Sky({ r, night, uid }) {
  const [a, b] = night ? SKIES[4] : SKIES[Math.floor(r() * 4)];
  return (
    <>
      <defs>
        <linearGradient id={`sky-${uid}`} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stopColor={a} />
          <stop offset="1" stopColor={b} />
        </linearGradient>
      </defs>
      <rect width="400" height="700" fill={`url(#sky-${uid})`} />
      <circle cx={60 + r() * 280} cy={90 + r() * 80} r={night ? 22 : 34} fill={night ? "#fff8e1" : "#fff59d"} opacity="0.95" />
      {[0, 1, 2].map((i) => (
        <ellipse key={i} cx={r() * 400} cy={60 + r() * 140} rx={40 + r() * 40} ry={12 + r() * 8} fill="#fff" opacity="0.55" />
      ))}
    </>
  );
}

function Palm({ x, y, s = 1 }) {
  return (
    <g transform={`translate(${x},${y}) scale(${s})`}>
      <path d="M0 0 C 6 -60, 2 -120, 10 -170" stroke="#6d4c41" strokeWidth="9" fill="none" />
      {[-70, -30, 10, 50, 90, 140].map((a) => (
        <path key={a} d="M10 -170 q 40 -20 75 10" stroke="#2e7d32" strokeWidth="10" fill="none" strokeLinecap="round" transform={`rotate(${a} 10 -170)`} />
      ))}
    </g>
  );
}

function Hotel({ x, y, w, h, floors, color, r }) {
  const rows = [];
  for (let f = 0; f < floors; f++) {
    for (let c = 0; c < Math.floor(w / 26); c++) {
      rows.push(<rect key={`${f}-${c}`} x={x + 8 + c * 26} y={y + 10 + f * (h / floors)} width="14" height={h / floors - 14} fill={r() > 0.35 ? "#bbdefb" : "#fff9c4"} opacity="0.9" />);
    }
  }
  return (
    <g>
      <rect x={x} y={y} width={w} height={h} fill={color} rx="4" />
      {rows}
    </g>
  );
}

function Sea({ r }) {
  return (
    <>
      <rect y="400" width="400" height="140" fill="#1e88e5" />
      <g className="waves">
        {[0, 1, 2, 3].map((i) => (
          <path key={i} d={`M-400 ${420 + i * 28} q 25 -10 50 0 t 50 0 t 50 0 t 50 0 t 50 0 t 50 0 t 50 0 t 50 0 t 50 0 t 50 0 t 50 0 t 50 0 t 50 0 t 50 0 t 50 0 t 50 0 t 50 0 t 50 0 t 50 0`} stroke="#bbdefb" strokeWidth="3" fill="none" opacity={0.7 - i * 0.12} />
        ))}
      </g>
      <path d="M0 520 Q 200 490 400 520 L400 700 L0 700 Z" fill="#ffe0a3" />
      {[0, 1, 2].map((i) => (
        <g key={i} transform={`translate(${70 + i * 110 + r() * 20}, ${585 + r() * 30})`}>
          <path d="M0 0 L 0 -46" stroke="#795548" strokeWidth="3" />
          <path d="M-34 -40 Q 0 -70 34 -40 Z" fill={["#ef5350", "#ffffff", "#26a69a"][i]} />
          <rect x="-26" y="2" width="44" height="8" rx="3" fill="#fff" />
        </g>
      ))}
    </>
  );
}

function Mountains({ r, snow }) {
  const peaks = [0, 1, 2].map((i) => ({ x: i * 150 - 40 + r() * 60, h: 230 + r() * 120 }));
  return (
    <>
      {peaks.map((p, i) => (
        <g key={i}>
          <polygon points={`${p.x - 170},520 ${p.x + 60},${520 - p.h} ${p.x + 290},520`} fill={["#546e7a", "#607d8b", "#78909c"][i]} />
          {snow && <polygon points={`${p.x + 20},${520 - p.h + 60} ${p.x + 60},${520 - p.h} ${p.x + 100},${520 - p.h + 60}`} fill="#fff" />}
        </g>
      ))}
      <rect y="500" width="400" height="200" fill={snow ? "#eceff1" : "#66bb6a"} />
    </>
  );
}

function Pines({ r, y = 560, n = 9 }) {
  return (
    <>
      {Array.from({ length: n }).map((_, i) => {
        const x = (i / n) * 420 + r() * 20;
        const s = 0.7 + r() * 0.6;
        return (
          <g key={i} transform={`translate(${x},${y}) scale(${s})`}>
            <rect x="-4" y="0" width="8" height="24" fill="#5d4037" />
            <polygon points="0,-90 -30,0 30,0" fill="#2e7d32" />
            <polygon points="0,-120 -22,-50 22,-50" fill="#388e3c" />
          </g>
        );
      })}
    </>
  );
}

function Lake() {
  return <ellipse cx="200" cy="600" rx="240" ry="60" fill="#4fc3f7" opacity="0.85" />;
}

function Skyline({ r }) {
  const out = [];
  let x = -10;
  while (x < 410) {
    const w = 40 + r() * 50;
    const h = 120 + r() * 220;
    out.push(<Hotel key={x} x={x} y={500 - h} w={w} h={h} floors={Math.max(3, Math.floor(h / 40))} color={["#37474f", "#455a64", "#5d4037", "#6d4c41"][Math.floor(r() * 4)]} r={r} />);
    x += w + 6;
  }
  return (
    <>
      {out}
      <rect y="500" width="400" height="200" fill="#90a4ae" />
      <rect y="560" width="400" height="80" fill="#5c8fb8" />
      <path d="M0 560 Q 200 470 400 560" stroke="#cfd8dc" strokeWidth="10" fill="none" />
    </>
  );
}

function Room({ r, view, uid }) {
  return (
    <>
      <rect width="400" height="700" fill="#f5efe6" />
      <rect x="60" y="90" width="280" height="230" fill="#90caf9" stroke="#8d6e63" strokeWidth="10" />
      <clipPath id={`win-${uid}`}><rect x="65" y="95" width="270" height="220" /></clipPath>
      <g clipPath={`url(#win-${uid})`}>
        {view === "sea" && (<><rect x="65" y="95" width="270" height="130" fill="#81d4fa" /><rect x="65" y="225" width="270" height="90" fill="#1e88e5" /></>)}
        {view === "mountains" && (<><polygon points="40,315 160,150 280,315" fill="#78909c" /><polygon points="160,315 280,170 380,315" fill="#607d8b" /></>)}
        {view === "city" && [0, 1, 2, 3, 4].map((i) => <rect key={i} x={70 + i * 55} y={170 + (i % 2) * 40} width="45" height="150" fill="#546e7a" />)}
        {view === "nature" && (<><rect x="65" y="230" width="270" height="85" fill="#4fc3f7" /><Pines r={r} y={240} n={6} /></>)}
      </g>
      <line x1="200" y1="90" x2="200" y2="320" stroke="#8d6e63" strokeWidth="6" />
      <rect x="40" y="420" width="320" height="150" rx="14" fill="#ffffff" stroke="#d7ccc8" strokeWidth="4" />
      <rect x="40" y="400" width="320" height="40" rx="10" fill="#a1887f" />
      <rect x="70" y="440" width="110" height="40" rx="10" fill="#eceff1" />
      <rect x="220" y="440" width="110" height="40" rx="10" fill="#eceff1" />
      <rect x="40" y="500" width="320" height="70" rx="8" fill={["#80cbc4", "#ce93d8", "#ffcc80"][Math.floor(r() * 3)]} />
      <rect y="600" width="400" height="100" fill="#bcaaa4" />
    </>
  );
}

function Pool({ r }) {
  return (
    <>
      <rect y="380" width="400" height="320" fill="#e0e0e0" />
      <path d="M40 460 Q 200 420 360 460 L 380 640 Q 200 680 20 640 Z" fill="#26c6da" />
      <g className="waves">
        {[0, 1].map((i) => <path key={i} d={`M-400 ${520 + i * 50} q 25 -8 50 0 t 50 0 t 50 0 t 50 0 t 50 0 t 50 0 t 50 0 t 50 0 t 50 0 t 50 0 t 50 0 t 50 0 t 50 0 t 50 0 t 50 0 t 50 0 t 50 0 t 50 0`} stroke="#e0f7fa" strokeWidth="3" fill="none" opacity="0.7" />)}
      </g>
      <Palm x={20} y={430} s={0.8} />
      <Palm x={370} y={440} s={0.9} />
    </>
  );
}

function frames(hotel, ns) {
  const tags = hotel.tags || [];
  const warm = tags.includes("warm");
  const view = tags.includes("sea") ? "sea" : tags.includes("mountains") ? "mountains" : tags.includes("city") ? "city" : "nature";
  const seed = hotel.id;
  const ext = (r, u = ns + "a") => {
    if (hotel.beach_line > 0 && hotel.beach_line <= 2 && tags.includes("sea")) {
      return (<><Sky r={r} uid={u} /><Hotel x={120} y={230} w={170} h={170} floors={5} color="#fafafa" r={r} /><Sea r={r} />{warm && <><Palm x={50} y={560} /><Palm x={355} y={575} s={0.9} /></>}</>);
    }
    if (view === "mountains" || hotel.dest === "altai") {
      return (<><Sky r={r} uid={u} /><Mountains r={r} snow={false} /><Pines r={r} /><Hotel x={140} y={430} w={120} h={90} floors={2} color="#8d6e63" r={r} /></>);
    }
    if (view === "city") {
      return (<><Sky r={r} night={r() > 0.5} uid={u} /><Skyline r={r} /></>);
    }
    return (<><Sky r={r} uid={u} /><Lake /><Pines r={r} y={540} /><Hotel x={150} y={450} w={100} h={70} floors={2} color="#a1887f" r={r} /></>);
  };
  const second = (r, u = ns + "b") => {
    if (warm || hotel.vibe === "family") return <><Sky r={r} uid={u} /><Pool r={r} /></>;
    if (view === "city") return <><Sky r={r} uid={u} /><Skyline r={r} /></>;
    if (view === "sea") return <><Sky r={r} uid={u} /><Sea r={r} /></>;
    return <><Sky r={r} uid={u} /><Mountains r={r} snow={false} /><Lake /><Pines r={r} y={600} n={6} /></>;
  };
  return [
    { key: "a", draw: () => ext(rng(seed + "a")) },
    { key: "b", draw: () => second(rng(seed + "b")) },
    { key: "c", draw: () => <Room r={rng(seed + "c")} view={view} uid={ns + "c"} /> },
  ];
}

export default function Scene({ hotel, video, playing = true, compact = false }) {
  const ns = useMemo(() => `${hotel.id}-${Math.random().toString(36).slice(2, 7)}`, [hotel.id]);
  const fr = useMemo(() => frames(hotel, ns), [hotel.id, ns]);
  const [i, setI] = useState(0);
  useEffect(() => {
    setI(0);
    if (!playing) return;
    const t = setInterval(() => setI((x) => (x + 1) % fr.length), 6000);
    return () => clearInterval(t);
  }, [hotel.id, playing, fr.length]);

  if (video?.url) {
    return <video className="scene" src={video.url} autoPlay={playing} muted loop playsInline />;
  }
  return (
    <div className={`scene ${compact ? "compact" : ""}`}>
      {fr.map((f, idx) => (
        <svg key={f.key} viewBox="0 0 400 700" preserveAspectRatio="xMidYMid slice" className={`frame ${idx === i ? "on" : ""} kb${idx}`} aria-hidden="true">
          <g className="kb">{f.draw()}</g>
        </svg>
      ))}
      {!compact && <div className="ai-label">Демо-ролик · иллюстрация, не фото отеля</div>}
    </div>
  );
}
