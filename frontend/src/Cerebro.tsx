import { useEffect, useState } from "react";
import { api, Cerebro as TCerebro, Feed } from "./api";
import { hora } from "./formato";

type Estado = "activo" | "atencion" | "error" | "inactivo";
const COLOR: Record<Estado, string> = { activo: "#4f8cff", atencion: "#d29922", error: "#f85149", inactivo: "#4a5566" };
const TIPO: Record<string, string> = { datos: "#4f8cff", decision: "#b07cff", riesgo: "#3fb950", ejecucion: "#c2884a", control: "#8b97a8" };

interface Nodo { id: string; x: number; y: number; titulo: string; grupo: string; desc: string; r?: number }
const NODOS: Nodo[] = [
  { id: "validador", x: 285, y: 270, titulo: "Validador de datos", grupo: "Percepción", desc: "Solo velas cerradas; revisa activo, frecuencia diaria, orden, continuidad y antigüedad (≤ 26 h)." },
  { id: "variables", x: 395, y: 165, titulo: "Variables", grupo: "Percepción", desc: "20 rendimientos, dist_sma20 y vol20 con la misma definición del entrenamiento." },
  { id: "lstm", x: 505, y: 270, titulo: "Modelo LSTM-B ×3", grupo: "Modelo", desc: "Ensamble de 3 semillas entrenado en T4; pronostica r(t+1).", r: 13 },
  { id: "llm", x: 640, y: 150, titulo: "LLM local", grupo: "Cerebro LLM", desc: "Ollama en esta computadora, vía gateway con API key. Propone COMPRAR/MANTENER/VENDER en JSON.", r: 16 },
  { id: "herr", x: 640, y: 52, titulo: "Herramientas", grupo: "Cerebro LLM", desc: "Solo lectura: consultar_posicion y consultar_historial." },
  { id: "json", x: 760, y: 215, titulo: "Validador JSON", grupo: "Núcleo determinista", desc: "Tipos, acción permitida, símbolo y fecha de vela. Si falla: no se opera." },
  { id: "riesgo", x: 790, y: 335, titulo: "Motor de riesgo", grupo: "Núcleo determinista", desc: "Exposición por par, saldo, pérdida diaria, dato atrasado, estado del agente. El LLM no puede saltarlo.", r: 14 },
  { id: "paper", x: 930, y: 200, titulo: "Paper · agente", grupo: "Ejecución", desc: "Ejecuta en el simulador con dinero virtual (50,000 USDT)." },
  { id: "modelo", x: 930, y: 320, titulo: "Modelo sin LLM", grupo: "Ejecución", desc: "Regla de la práctica (pronóstico > δ) con los mismos límites." },
  { id: "bh", x: 930, y: 420, titulo: "Comprar y mantener", grupo: "Ejecución", desc: "Referencia pasiva con los mismos costos." },
  { id: "sqlite", x: 735, y: 440, titulo: "Registro SQLite", grupo: "Memoria", desc: "Velas procesadas, decisiones, órdenes, patrimonio y errores; recupera el estado al reiniciar." },
  { id: "ui", x: 540, y: 440, titulo: "Control humano", grupo: "Memoria", desc: "Iniciar, pausar, detener y liquidar; protegidos con sesión." },
];
const ARISTAS: [string, string, string][] = [
  ["validador", "variables", "datos"], ["variables", "lstm", "datos"], ["lstm", "llm", "decision"], ["herr", "llm", "decision"],
  ["llm", "json", "decision"], ["json", "riesgo", "decision"], ["lstm", "modelo", "decision"], ["riesgo", "paper", "riesgo"],
  ["riesgo", "modelo", "riesgo"], ["riesgo", "bh", "riesgo"], ["paper", "sqlite", "ejecucion"], ["riesgo", "sqlite", "ejecucion"],
  ["ui", "riesgo", "control"],
];
const PARES = ["BTCUSDT", "ETHUSDT", "XRPUSDT", "BNBUSDT", "SOLUSDT", "ADAUSDT", "DOGEUSDT", "LTCUSDT", "TRXUSDT", "LINKUSDT", "DOTUSDT", "BCHUSDT", "XLMUSDT", "ATOMUSDT", "ETCUSDT"];

export function Cerebro({ tick }: { tick: number }) {
  const [c, setC] = useState<TCerebro | null>(null);
  const [feeds, setFeeds] = useState<Record<string, Feed>>({});
  const [sel, setSel] = useState<string>("llm");
  const [err, setErr] = useState("");
  useEffect(() => {
    api.cerebro().then((x) => { setC(x); setErr(""); }).catch((e) => setErr(e.message));
    api.feeds().then(setFeeds).catch(() => undefined);
  }, [tick]);
  if (!c) return <section>{err ? <p className="error">{err}</p> : "Cargando…"}</section>;

  const activo = c.estado === "ACTIVO";
  const est = (id: string): Estado => {
    if (id === "llm" || id === "herr" || id === "json") return c.salud.llm.ok ? (activo ? "activo" : "inactivo") : "error";
    if (id === "validador" || id === "variables") return c.salud.mexc.ok ? (activo ? "activo" : "inactivo") : "error";
    if (id === "riesgo") return c.estado === "BLOQUEADO" ? "atencion" : c.estado === "PAUSADO_ERROR" ? "error" : activo ? "activo" : "inactivo";
    if (id === "ui") return "activo";
    return activo ? "activo" : c.estado === "PAUSADO_ERROR" ? "atencion" : "inactivo";
  };
  const estFeed = (s: string): Estado => {
    const f = feeds[s];
    if (!c.salud.mexc.ok) return "error";
    if (!f) return "inactivo";
    return f.estado === "ok" ? "activo" : f.estado === "atrasado" ? "atencion" : "error";
  };
  const pos = Object.fromEntries(NODOS.map((n) => [n.id, n]));
  const feedPos = PARES.map((s, i) => {
    const a = (-70 + (140 * i) / (PARES.length - 1)) * (Math.PI / 180);
    return { s, x: 210 - 150 * Math.cos(a), y: 270 + 205 * Math.sin(a) };
  });
  const nodoSel = NODOS.find((n) => n.id === sel);
  const ct = c.conteos;
  const dur = activo ? 2.2 : 0;

  return (
    <>
      <section className="cerebro">
        <div className="cab"><h2>Cerebro · constelación de agentes</h2><span className="nota">cada estrella es un componente; las partículas fluyen al ritmo real del ciclo (solo con el agente ACTIVO)</span></div>
        <svg viewBox="0 0 1000 520" className="constelacion" role="img" aria-label="Constelación de componentes del agente">
          <defs>
            <radialGradient id="brillo"><stop offset="0%" stopColor="#fff" stopOpacity=".9" /><stop offset="100%" stopColor="#fff" stopOpacity="0" /></radialGradient>
            <filter id="glow"><feGaussianBlur stdDeviation="3.5" result="b" /><feMerge><feMergeNode in="b" /><feMergeNode in="SourceGraphic" /></feMerge></filter>
          </defs>
          {[...Array(70)].map((_, i) => <circle key={i} cx={(i * 137) % 1000} cy={(i * 251) % 520} r={(i % 3) * 0.4 + 0.3} fill="#fff" opacity={0.15 + (i % 5) * 0.05} />)}
          <text x="40" y="28" className="grupo">PERCEPCIÓN · FEEDS MEXC</text>
          <text x="470" y="320" className="grupo">MODELO DE LA PRÁCTICA</text>
          <text x="585" y="105" className="grupo">CEREBRO LLM</text>
          <text x="700" y="385" className="grupo">NÚCLEO DETERMINISTA</text>
          {feedPos.map((f, i) => (
            <g key={f.s}>
              <path id={`fe${i}`} d={`M${f.x},${f.y} Q${(f.x + 285) / 2 + 20},${(f.y + 270) / 2} 285,270`} fill="none" stroke={TIPO.datos} strokeOpacity=".22" />
              {dur > 0 && estFeed(f.s) === "activo" && <circle r="1.8" fill={TIPO.datos}><animateMotion dur={`${dur + (i % 4) * 0.4}s`} repeatCount="indefinite"><mpath href={`#fe${i}`} /></animateMotion></circle>}
              <circle cx={f.x} cy={f.y} r="5" fill={COLOR[estFeed(f.s)]} filter="url(#glow)"><title>{`${f.s}: ${feeds[f.s]?.estado ?? "sin datos"} · última vela ${feeds[f.s]?.ultima_vela ?? "—"}`}</title></circle>
              <text x={f.x - 10} y={f.y + 4} textAnchor="end" className="etq">{f.s.replace("USDT", "")}</text>
            </g>
          ))}
          {ARISTAS.map(([a, b, t], i) => {
            const A = pos[a], B = pos[b];
            const d = `M${A.x},${A.y} Q${(A.x + B.x) / 2},${(A.y + B.y) / 2 - 25} ${B.x},${B.y}`;
            const roto = est(a) === "error" || est(b) === "error";
            return (
              <g key={i}>
                <path id={`ar${i}`} d={d} fill="none" stroke={roto ? COLOR.error : TIPO[t]} strokeOpacity={roto ? 0.6 : 0.35} strokeWidth="1.4" strokeDasharray={t === "control" ? "4 4" : undefined} />
                {dur > 0 && !roto && t !== "control" && [0, 1].map((k) => (
                  <circle key={k} r="2.4" fill={TIPO[t]}><animateMotion dur={`${dur}s`} begin={`${k * dur / 2}s`} repeatCount="indefinite"><mpath href={`#ar${i}`} /></animateMotion></circle>))}
              </g>);
          })}
          {NODOS.map((n) => {
            const e = est(n.id);
            const r = n.r ?? 9;
            return (
              <g key={n.id} onClick={() => setSel(n.id)} style={{ cursor: "pointer" }}>
                <circle cx={n.x} cy={n.y} r={r * 2.4} fill="url(#brillo)" opacity={e === "inactivo" ? 0.08 : 0.25} />
                <circle cx={n.x} cy={n.y} r={r} fill={COLOR[e]} filter="url(#glow)" stroke={sel === n.id ? "#fff" : "none"} strokeWidth="1.5">
                  {e === "error" && <animate attributeName="opacity" values="1;.35;1" dur="1.2s" repeatCount="indefinite" />}
                </circle>
                <text x={n.x} y={n.y + r + 15} textAnchor="middle" className="nombre-nodo">{n.titulo}</text>
                {n.id === "llm" && <text x={n.x} y={n.y + r + 28} textAnchor="middle" className="etq">{c.llm_modelo}</text>}
              </g>);
          })}
          <g transform="translate(40,505)">
            {Object.entries(TIPO).map(([k, col], i) => <g key={k} transform={`translate(${i * 95},0)`}><line x1="0" x2="18" stroke={col} strokeWidth="2" strokeDasharray={k === "control" ? "4 3" : undefined} /><text x="23" y="4" className="etq">{k}</text></g>)}
            {(["activo", "atencion", "error", "inactivo"] as Estado[]).map((k, i) => <g key={k} transform={`translate(${500 + i * 85},0)`}><circle r="5" fill={COLOR[k]} /><text x="10" y="4" className="etq">{k === "atencion" ? "atención" : k}</text></g>)}
          </g>
        </svg>
      </section>
      <section className="tarjetas">
        <div className="tarjeta"><div className="valor">{c.estado}</div><div className="nombre">Régimen · {c.motivo || "—"}</div></div>
        <div className="tarjeta"><div className={`valor ${c.salud.llm.ok ? "pos" : "neg"}`}>{ct.lat ? `${(ct.lat / 1000).toFixed(1)} s` : c.salud.llm.ok ? "listo" : "sin LLM"}</div><div className="nombre">Latencia media del LLM (última vela)</div></div>
        <div className="tarjeta"><div className={`valor ${c.salud.mexc.ok ? "pos" : "neg"}`}>{c.salud.mexc.ok ? `${c.salud.mexc.latencia_ms} ms` : "caída"}</div><div className="nombre">Latencia MEXC</div></div>
        <div className="tarjeta"><div className="valor">{c.feeds_ok} / {c.feeds_total}</div><div className="nombre">Feeds válidos (vela {c.ultima_vela ?? "—"})</div></div>
        <div className="tarjeta"><div className="valor">{ct.compras ?? 0} · {ct.ventas ?? 0} · {ct.mantener ?? 0}</div><div className="nombre">Comprar · vender · mantener</div></div>
        <div className="tarjeta"><div className="valor">{ct.rechazos ?? 0}</div><div className="nombre">Rechazos ({ct.json_invalidos ?? 0} JSON inválidos)</div></div>
        <div className="tarjeta"><div className="valor">{ct.con_herramientas ?? 0} / {ct.n ?? 0}</div><div className="nombre">Decisiones que usaron herramientas</div></div>
      </section>
      <section className="dos">
        <div>
          <h2>{nodoSel?.titulo} · {nodoSel?.grupo}</h2>
          <p>{nodoSel?.desc}</p>
          <p className="nota">Estado: <b style={{ color: COLOR[est(sel)] }}>{est(sel)}</b> · Modelo {c.modelo}</p>
        </div>
        <div>
          <h2>Actividad reciente</h2>
          <table><tbody>{c.eventos.map((e, i) => <tr key={i} className={`nivel-${e.nivel}`}><td>{hora(e.ts_utc)}</td><td>{e.etapa}</td><td className="motivo">{e.mensaje}</td></tr>)}</tbody></table>
        </div>
      </section>
    </>
  );
}
