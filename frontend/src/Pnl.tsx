import { useEffect, useState } from "react";
import { api, Pnl } from "./api";
import { NOMBRE_ESTRATEGIA, num, pct, usd } from "./formato";

const ESTRATEGIAS = ["agente_llm", "modelo", "comprar_mantener"] as const;
const signo = (x: number) => (x > 0 ? "pos" : x < 0 ? "neg" : "");
const usdS = (x: number) => `${x > 0 ? "+" : ""}${usd(x)}`;

/** Operaciones Paper (compra → venta) y PnL realizado / no realizado, con precios reales de cierre de MEXC. */
export function OperacionesPnl({ tick, modo = "vivo" }: { tick: number; modo?: string }) {
  const [d, setD] = useState<Pnl | null>(null);
  const [err, setErr] = useState("");
  const [filtro, setFiltro] = useState<string>("agente_llm");
  useEffect(() => { api.pnl(modo).then((x) => { setD(x); setErr(""); }).catch((e) => setErr((e as Error).message)); }, [tick, modo]);
  if (err) return <section><p className="error">{err}</p></section>;
  if (!d) return <section><p className="vacio">Cargando…</p></section>;
  const ops = d.operaciones.filter((o) => filtro === "todas" || o.estrategia === filtro);
  const fechas = Array.from(new Set(d.diario.map((x) => x.fecha_vela))).sort();
  return (
    <>
      <section>
        <h2>PnL por estrategia · modo {modo} · precios de cierre MEXC al {d.precios_al ?? "—"}</h2>
        <div className="tabla"><table>
          <thead><tr><th>Estrategia</th><th>PnL total</th><th>PnL total (USDT)</th><th>Realizado (USDT)</th><th>No realizado (USDT)</th>
            <th>Cerradas</th><th>Abiertas</th><th>Ganadoras</th><th>Win rate</th></tr></thead>
          <tbody>{d.estrategias.map((e) => (
            <tr key={e.estrategia}><td><b>{NOMBRE_ESTRATEGIA[e.estrategia]}</b></td>
              <td className={signo(e.pnl_total)}><b>{pct(e.pnl_total_pct)}</b></td>
              <td className={signo(e.pnl_total)}>{usdS(e.pnl_total)}</td>
              <td className={signo(e.pnl_realizado)}>{usdS(e.pnl_realizado)}</td>
              <td className={signo(e.pnl_no_realizado)}>{usdS(e.pnl_no_realizado)}</td>
              <td>{e.cerradas}</td><td>{e.abiertas}</td><td>{e.ganadoras}</td><td>{e.win_rate_pct == null ? "—" : `${e.win_rate_pct.toFixed(0)} %`}</td></tr>))}
          </tbody></table></div>
        <p className="nota">Realizado = venta − costos − (compra + costos). No realizado = posiciones abiertas valuadas al último cierre de MEXC.
          El % total es sobre el capital inicial de cada estrategia. Operación simulada (Paper): no hay órdenes reales.</p>
      </section>

      <section>
        <h2>PnL diario (% del patrimonio del día anterior)</h2>
        {!fechas.length ? <p className="vacio">Sin días registrados.</p> :
          <div className="tabla"><table>
            <thead><tr><th>Vela</th>{ESTRATEGIAS.map((e) => <th key={e}>{NOMBRE_ESTRATEGIA[e]}</th>)}</tr></thead>
            <tbody>{fechas.slice().reverse().map((f) => (
              <tr key={f}><td>{f}</td>{ESTRATEGIAS.map((e) => {
                const x = d.diario.find((y) => y.estrategia === e && y.fecha_vela === f);
                return <td key={e} className={x ? signo(x.pnl) : ""} title={x ? `${usdS(x.pnl)} USDT · acumulado ${pct(x.acumulado_pct)}` : ""}>
                  {x ? `${pct(x.pnl_pct)} (${usdS(x.pnl)})` : "—"}</td>;
              })}</tr>))}</tbody></table></div>}
      </section>

      <section>
        <div className="cab"><h2>Operaciones ({ops.length})</h2>
          <label>Estrategia{" "}
            <select value={filtro} onChange={(e) => setFiltro(e.target.value)}>
              {ESTRATEGIAS.map((e) => <option key={e} value={e}>{NOMBRE_ESTRATEGIA[e]}</option>)}
              <option value="todas">Todas</option>
            </select></label></div>
        {!ops.length ? <p className="vacio">Todavía no hay operaciones.</p> :
          <div className="tabla"><table>
            <thead><tr><th>Par</th>{filtro === "todas" && <th>Estrategia</th>}<th>Estado</th><th>Entrada (vela)</th><th>Precio entrada</th>
              <th>Salida / valuación</th><th>Precio salida</th><th>Cantidad</th><th>Invertido (USDT)</th><th>PnL (USDT)</th><th>PnL %</th></tr></thead>
            <tbody>{ops.map((o, i) => (
              <tr key={i}><td><b>{o.simbolo}</b></td>{filtro === "todas" && <td>{NOMBRE_ESTRATEGIA[o.estrategia]}</td>}
                <td>{o.estado === "abierta" ? <span className="chip">abierta</span> : "cerrada"}</td>
                <td>{o.fecha_entrada}</td><td>{num(o.precio_entrada)}</td>
                <td>{o.fecha_salida ?? "—"}{o.estado === "abierta" && " (cierre)"}</td><td>{num(o.precio_salida)}</td>
                <td>{num(o.cantidad)}</td><td>{usd(o.invertido)}</td>
                <td className={signo(o.pnl)}>{usdS(o.pnl)}</td><td className={signo(o.pnl)}><b>{pct(o.pnl_pct)}</b></td></tr>))}
            </tbody></table></div>}
      </section>
    </>
  );
}
