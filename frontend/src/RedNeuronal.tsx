import { useEffect, useState } from "react";
import { api, Red } from "./api";
import { pctDec } from "./formato";

const PARES = ["BTCUSDT", "ETHUSDT", "XRPUSDT", "BNBUSDT", "SOLUSDT", "ADAUSDT", "DOGEUSDT", "LTCUSDT", "TRXUSDT", "LINKUSDT", "DOTUSDT", "BCHUSDT", "XLMUSDT", "ATOMUSDT", "ETCUSDT"];

// azul = positivo, rojo = negativo; la intensidad es la magnitud
const div = (v: number, m: number) => {
  const a = Math.min(1, Math.abs(v) / (m || 1));
  return v >= 0 ? `rgba(79,140,255,${0.15 + 0.85 * a})` : `rgba(248,81,73,${0.15 + 0.85 * a})`;
};

export function RedNeuronal() {
  const [sim, setSim] = useState("BTCUSDT");
  const [semilla, setSemilla] = useState(0);
  const [r, setR] = useState<Red | null>(null);
  const [err, setErr] = useState("");
  useEffect(() => { setR(null); api.red(sim).then((x) => { setR(x); setErr(""); }).catch((e) => setErr(e.message)); }, [sim]);

  const W = 1000, H = 600;
  const xs = { ent: 90, lstm: 330, conc: 520, dens: 700, sal: 850, fin: 950 };
  const ys = (n: number, i: number, y0 = 70, y1 = 520) => y0 + (n === 1 ? (y1 - y0) / 2 : (i * (y1 - y0)) / (n - 1));

  return (
    <>
      <section>
        <div className="cab"><h2>Red neuronal · LSTM-B (ensamble de 3 semillas)</h2>
          <span className="nota">activaciones reales del último pronóstico de cada par; los pesos son los entrenados en la T4</span></div>
        <div className="selector">{PARES.map((p) => <button key={p} className={p === sim ? "activa" : ""} onClick={() => setSim(p)}>{p.replace("USDT", "")}</button>)}</div>
        <div className="selector">{[11, 22, 33].map((s, i) => <button key={s} className={i === semilla ? "activa" : ""} onClick={() => setSemilla(i)}>semilla {s}</button>)}</div>
        {err && <p className="error">{err}</p>}
        {!r ? <p className="vacio">Calculando activaciones…</p> : (() => {
          const s = r.semillas[semilla];
          const mh = Math.max(...s.h.map(Math.abs)), md = Math.max(...s.d), mx = Math.max(...r.entradas.seq.map(Math.abs));
          const w1 = r.pesos_semilla_11.d1, w2 = r.pesos_semilla_11.d2;
          const mw1 = Math.max(...w1.flat().map(Math.abs)), mw2 = Math.max(...w2.map(Math.abs));
          // entradas de la capa densa: 16 LSTM + 15 par + 2 indicadores
          const conc = [...s.h.map((v, i) => ({ v, etq: `h${i + 1}`, tipo: "lstm" })),
            ...PARES.map((p, i) => ({ v: i === r.entradas.par_indice ? 1 : 0, etq: p.replace("USDT", ""), tipo: "par" })),
            { v: r.entradas.ind_z[0], etq: "dist_sma20", tipo: "ind" }, { v: r.entradas.ind_z[1], etq: "vol20", tipo: "ind" }];
          const yc = (i: number) => ys(conc.length, i, 40, 560);
          const compra = r.pronostico > r.delta;
          return (
            <svg viewBox={`0 0 ${W} ${H}`} className="redneuronal" role="img" aria-label={`Red neuronal para ${r.simbolo}`}>
              <text x={xs.ent} y="22" textAnchor="middle" className="grupo">ENTRADA · 20 DÍAS</text>
              <text x={xs.lstm} y="22" textAnchor="middle" className="grupo">LSTM · 16 MEMORIAS</text>
              <text x={xs.conc} y="22" textAnchor="middle" className="grupo">CONCATENAR (33)</text>
              <text x={xs.dens} y="22" textAnchor="middle" className="grupo">DENSA ReLU · 16</text>
              <text x={xs.sal + 50} y="22" textAnchor="middle" className="grupo">SALIDA</text>
              {/* entrada → LSTM (procesamiento secuencial) */}
              {r.entradas.seq.map((_, i) => s.h.map((__, j) => (i % 2 === 0 && j % 2 === 0) ? <line key={`${i}-${j}`} x1={xs.ent + 45} y1={ys(20, i)} x2={xs.lstm - 9} y2={ys(16, j)} stroke="#4f8cff" strokeOpacity=".06" /> : null))}
              {/* concatenación → densa con pesos */}
              {conc.map((c, i) => s.d.map((_, j) => {
                const w = w1[i][j]; if (Math.abs(w) < mw1 * 0.25) return null;
                const act = c.tipo === "par" && c.v === 0;
                return <line key={`c${i}-${j}`} x1={xs.conc + 8} y1={yc(i)} x2={xs.dens - 9} y2={ys(16, j)} stroke={w >= 0 ? "#4f8cff" : "#f85149"} strokeOpacity={act ? 0.03 : 0.08 + 0.4 * Math.abs(w) / mw1} strokeWidth={0.5 + 1.5 * Math.abs(w) / mw1} />;
              }))}
              {s.h.map((_, i) => <line key={`hc${i}`} x1={xs.lstm + 9} y1={ys(16, i)} x2={xs.conc - 8} y2={yc(i)} stroke="#8b97a8" strokeOpacity=".25" />)}
              {s.d.map((_, j) => <line key={`ds${j}`} x1={xs.dens + 9} y1={ys(16, j)} x2={xs.sal - 12} y2={240 + semilla * 60} stroke={w2[j] >= 0 ? "#4f8cff" : "#f85149"} strokeOpacity={0.1 + 0.6 * Math.abs(w2[j]) / mw2} strokeWidth={0.5 + 2.5 * Math.abs(w2[j]) / mw2} />)}
              {/* entrada: barras de rendimientos */}
              {r.entradas.seq.map((v, i) => (
                <g key={`e${i}`}>
                  <line x1={xs.ent} x2={xs.ent} y1={ys(20, i) - 8} y2={ys(20, i) + 8} stroke="#3a4556" />
                  <rect x={v >= 0 ? xs.ent : xs.ent - 40 * Math.abs(v) / mx} y={ys(20, i) - 6} width={40 * Math.abs(v) / mx} height={12} fill={v >= 0 ? "#3fb950" : "#f85149"} opacity=".8"><title>{`t−${19 - i}: ${(v * 100).toFixed(2)} %`}</title></rect>
                  {(i === 0 || i === 19) && <text x={xs.ent - 48} y={ys(20, i) + 4} textAnchor="end" className="etq">{i === 0 ? "t−19" : "t"}</text>}
                </g>))}
              {/* LSTM */}
              {s.h.map((v, i) => <circle key={`h${i}`} cx={xs.lstm} cy={ys(16, i)} r={9} fill={div(v, mh)} stroke="#4f8cff" strokeOpacity=".5"><title>{`memoria h${i + 1} = ${v.toFixed(3)}`}</title></circle>)}
              {/* concatenación */}
              {conc.map((c, i) => (
                <g key={`k${i}`}>
                  <rect x={xs.conc - 7} y={yc(i) - 6} width={14} height={12} rx={2}
                    fill={c.tipo === "lstm" ? div(c.v, mh) : c.tipo === "par" ? (c.v ? "#d29922" : "#1f2836") : div(c.v, 2)} stroke="#3a4556" />
                  {(c.tipo !== "lstm" && (c.tipo === "ind" || c.v === 1)) && <text x={xs.conc + 14} y={yc(i) + 4} className="etq">{c.tipo === "ind" ? `${c.etq} (${c.v.toFixed(2)}σ)` : `par: ${c.etq}`}</text>}
                </g>))}
              {/* densa */}
              {s.d.map((v, j) => <circle key={`d${j}`} cx={xs.dens} cy={ys(16, j)} r={9} fill={`rgba(79,140,255,${0.1 + 0.9 * (md ? v / md : 0)})`} stroke="#4f8cff" strokeOpacity=".5"><title>{`neurona ${j + 1} = ${v.toFixed(3)}${v === 0 ? " (apagada por ReLU)" : ""}`}</title></circle>)}
              {/* salidas de las 3 semillas y promedio */}
              {r.semillas.map((x, k) => (
                <g key={`s${k}`} opacity={k === semilla ? 1 : 0.45}>
                  <circle cx={xs.sal} cy={240 + k * 60} r={12} fill={x.salida >= 0 ? "#2b4a8a" : "#5a2422"} stroke={k === semilla ? "#fff" : "#3a4556"} />
                  <text x={xs.sal} y={244 + k * 60} textAnchor="middle" className="etq">{x.semilla}</text>
                  <text x={xs.sal} y={268 + k * 60} textAnchor="middle" className="etq">{pctDec(x.salida)}</text>
                  <line x1={xs.sal + 12} y1={240 + k * 60} x2={xs.fin - 18} y2={300} stroke="#8b97a8" strokeOpacity=".4" />
                </g>))}
              <circle cx={xs.fin} cy={300} r={18} fill={compra ? "#1d4d2b" : "#2a2f3a"} stroke={compra ? "#3fb950" : "#8b97a8"} strokeWidth="2" />
              <text x={xs.fin} y={305} textAnchor="middle" className="nombre-nodo">r̂</text>
              <text x={xs.fin} y={340} textAnchor="middle" className="valor-red">{pctDec(r.pronostico)}</text>
              <text x={xs.fin} y={360} textAnchor="middle" className="etq">δ = {(r.delta * 100).toFixed(2)} %</text>
              <text x={xs.fin} y={380} textAnchor="middle" className={compra ? "etq pos" : "etq"}>{compra ? "regla: COMPRAR" : "regla: EFECTIVO"}</text>
            </svg>);
        })()}
      </section>
      {r && <section className="dos">
        <div><h2>{r.simbolo} · vela {r.fecha_vela}</h2>
          <p className="nota">Fuente: {r.fuente}. dist_sma20 = {pctDec(r.entradas.dist_sma20, 2)} · vol20 = {pctDec(r.entradas.vol20, 2)}.</p>
          {r.decision && <p className="nota">Decisión final del agente: <b>{r.decision.accion_final}</b> — {r.decision.motivo}</p>}
        </div>
        <div><h2>Cómo leerla</h2>
          <p className="nota">Las barras son los 20 rendimientos diarios (verde sube, rojo baja). La LSTM los recorre en orden y resume la secuencia en 16 memorias (azul positivo, rojo negativo). Esas memorias se unen con el par (cuadro ámbar) y los dos indicadores; la capa densa (las neuronas apagadas valen 0 por ReLU) produce el rendimiento estandarizado. Las tres semillas se promedian para obtener r̂ y la regla compara r̂ con δ. Las líneas muestran los pesos más fuertes de la semilla 11 (azul positivo, rojo negativo).</p>
        </div>
      </section>}
    </>
  );
}
