import { useEffect, useState } from "react";
import { api, Ciclo, Decision, Estado, Evento, Feed, Metrica, Orden, PuntoPatrimonio, Salud } from "./api";
import { GraficaPatrimonio } from "./Grafica";
import { hora, NOMBRE_ESTRATEGIA, num, pct, pctDec, usd } from "./formato";

function useDatos<T>(f: () => Promise<T>, deps: unknown[]): [T | null, string] {
  const [d, setD] = useState<T | null>(null);
  const [e, setE] = useState("");
  useEffect(() => { f().then((x) => { setD(x); setE(""); }).catch((x) => setE(x.message)); }, deps); // eslint-disable-line
  return [d, e];
}

const Accion = ({ a }: { a: string | null }) => <span className={`accion accion-${a ?? "NA"}`}>{a ?? "—"}</span>;

// ------------------------------------------------------------------ Operación
export function Operacion({ estado, tick }: { estado: Estado; tick: number }) {
  const [p, err] = useDatos(() => api.pares("vivo"), [tick]);
  const [m] = useDatos(() => api.metricas("vivo"), [tick]);
  const cuenta = estado.cuentas.agente_llm;
  const ag = m?.find((x) => x.estrategia === "agente_llm");
  return (
    <>
      <section className="tarjetas">
        <div className="tarjeta"><div className="valor">{usd(cuenta?.patrimonio)}</div><div className="nombre">Patrimonio Paper (USDT, base {usd(estado.riesgo.capital_inicial, 0)})</div></div>
        <div className="tarjeta"><div className={`valor ${(ag?.rendimiento_neto_pct ?? 0) >= 0 ? "pos" : "neg"}`}>{pct(ag?.rendimiento_neto_pct)}</div><div className="nombre">Rendimiento neto</div></div>
        <div className="tarjeta"><div className="valor">{pct(ag?.drawdown_max_pct)}</div><div className="nombre">Drawdown máximo</div></div>
        <div className="tarjeta"><div className="valor">{usd(cuenta?.efectivo)}</div><div className="nombre">Efectivo</div></div>
        <div className="tarjeta"><div className="valor">{cuenta?.posiciones.length ?? 0} / 15</div><div className="nombre">Posiciones abiertas</div></div>
        <div className="tarjeta"><div className="valor">{ag?.win_rate_pct == null ? "—" : `${ag.win_rate_pct.toFixed(1)} %`}</div><div className="nombre">Win rate ({ag?.operaciones_cerradas ?? 0} cerradas)</div></div>
        <div className="tarjeta"><div className="valor">{ag?.acciones_rechazadas ?? 0}</div><div className="nombre">Acciones rechazadas por riesgo</div></div>
        <div className="tarjeta"><div className="valor">{ag?.latencia_llm_ms_prom ? `${(ag.latencia_llm_ms_prom / 1000).toFixed(1)} s` : "—"}</div><div className="nombre">Latencia media del LLM</div></div>
      </section>
      <section>
        <h2>Pares · vela {p?.fecha_vela ?? "sin procesar"}</h2>
        {err && <p className="error">{err}</p>}
        {!p?.pares.length ? <p className="vacio">Inicia el agente: procesa la última vela diaria cerrada y después cada cierre (18:00 CDMX).</p> :
          <div className="tabla"><table>
            <thead><tr><th>Par</th><th>Cierre</th><th>Pronóstico r(t+1)</th><th>dist SMA20</th><th>vol20</th><th>Regla</th><th>LLM</th><th>Final</th><th>Riesgo</th><th>Motivo del LLM</th><th>Posición</th></tr></thead>
            <tbody>{p.pares.map((d) => (
              <tr key={d.simbolo}>
                <td><b>{d.simbolo}</b></td><td>{num(d.cierre)}</td>
                <td className={(d.pronostico ?? 0) > estado.modelo.regla.delta ? "pos" : ""}>{pctDec(d.pronostico)}</td>
                <td>{pctDec(d.dist_sma20, 2)}</td><td>{pctDec(d.vol20, 2)}</td>
                <td><Accion a={d.accion_modelo} /></td><td><Accion a={d.accion_llm} /></td><td><Accion a={d.accion_final} /></td>
                <td title={d.motivo}><span className={d.aprobada ? "ok" : "rechazo"}>{d.aprobada ? "aprobada" : "rechazada"}</span></td>
                <td className="motivo" title={d.justificacion ?? ""}>{d.justificacion ?? d.motivo}</td>
                <td>{d.posicion ? `${num(d.posicion.cantidad)} @ ${num(d.posicion.precio_entrada)}` : "efectivo"}</td>
              </tr>))}
            </tbody></table></div>}
      </section>
      <section>
        <h2>Posiciones abiertas del agente</h2>
        {!cuenta?.posiciones.length ? <p className="vacio">Sin posiciones.</p> :
          <table><thead><tr><th>Símbolo</th><th>Modo</th><th>Lado</th><th>Cantidad</th><th>Entrada</th><th>Desde</th></tr></thead>
            <tbody>{cuenta.posiciones.map((x) => <tr key={x.simbolo}><td>{x.simbolo}</td><td><span className="chip">Paper</span></td><td>LONG</td><td>{num(x.cantidad)}</td><td>{num(x.precio_entrada)}</td><td>{x.fecha_entrada}</td></tr>)}</tbody></table>}
      </section>
    </>
  );
}

// ------------------------------------------------------------------ Rendimiento / comparación
export function TablaMetricas({ m }: { m: Metrica[] }) {
  return (
    <div className="tabla"><table>
      <thead><tr><th>Estrategia</th><th>Periodo</th><th>Rend. neto</th><th>Drawdown máx.</th><th>Órdenes</th><th>Cerradas</th><th>Win rate</th><th>Profit factor</th><th>Costos</th><th>Latencia LLM</th><th>Rechazadas</th></tr></thead>
      <tbody>{m.map((x) => (
        <tr key={x.estrategia}><td><b>{NOMBRE_ESTRATEGIA[x.estrategia]}</b></td><td>{x.desde ?? "—"} → {x.hasta ?? "—"} ({x.dias} d)</td>
          <td className={x.rendimiento_neto_pct >= 0 ? "pos" : "neg"}>{pct(x.rendimiento_neto_pct)}</td><td>{pct(x.drawdown_max_pct)}</td>
          <td>{x.ordenes}</td><td>{x.operaciones_cerradas}</td><td>{x.win_rate_pct == null ? "no definido" : `${x.win_rate_pct.toFixed(1)} %`}</td>
          <td>{x.profit_factor == null ? "—" : x.profit_factor.toFixed(2)}</td><td>{usd(x.costos_totales)}</td>
          <td>{x.latencia_llm_ms_prom ? `${(x.latencia_llm_ms_prom / 1000).toFixed(1)} s` : "—"}</td>
          <td>{x.acciones_rechazadas ?? "—"}</td></tr>))}
      </tbody></table></div>
  );
}

export function Rendimiento({ capital, modo, tick }: { capital: number; modo: string; tick: number }) {
  const [m, e1] = useDatos(() => api.metricas(modo), [modo, tick]);
  const [pts] = useDatos<PuntoPatrimonio[]>(() => api.patrimonio(modo), [modo, tick]);
  return (
    <section>
      <h2>{modo === "vivo" ? "Operación en vivo (MEXC actual)" : "REPLAY histórico"} · mismas fechas y costos (0 %)</h2>
      {e1 && <p className="error">{e1}</p>}
      {m && <TablaMetricas m={m} />}
      <GraficaPatrimonio puntos={pts ?? []} capital={capital} />
      <p className="nota">El agente con LLM y el modelo sin LLM usan los mismos límites de riesgo; comprar y mantener reparte el capital en los 15 pares en el primer ciclo. Win rate = operaciones cerradas con beneficio neto / operaciones cerradas.</p>
    </section>
  );
}

// ------------------------------------------------------------------ Riesgo
export function RiesgoPanel({ estado, alCambiar }: { estado: Estado; alCambiar: () => void }) {
  const r = estado.riesgo;
  const cuenta = estado.cuentas.agente_llm;
  const expo = cuenta ? cuenta.patrimonio - cuenta.efectivo : 0;
  const [f, setF] = useState({ exposicion_max_por_par: r.exposicion_max_por_par, perdida_diaria_max: r.perdida_diaria_max, antiguedad_max_horas: r.antiguedad_max_horas });
  const [msg, setMsg] = useState("");
  const guardar = async () => {
    try { await api.cambiarRiesgo(f); setMsg("Límites guardados y registrados en la bitácora."); alCambiar(); }
    catch (e) { setMsg((e as Error).message); }
  };
  const editable = estado.estado !== "ACTIVO";
  return (
    <section className="dos">
      <div>
        <h2>Límites deterministas (el LLM no puede cambiarlos)</h2>
        <table><tbody>
          <tr><td>Capital inicial</td><td>{usd(r.capital_inicial, 0)} USDT</td></tr>
          <tr><td>Costo por compra o venta</td><td>{(r.costo_por_lado * 100).toFixed(2)} %</td></tr>
          <tr><td>Deslizamiento supuesto</td><td>{(r.deslizamiento * 100).toFixed(3)} % (compra al ask, venta al bid)</td></tr>
          <tr><td>Exposición máxima por par</td><td>{(r.exposicion_max_por_par * 100).toFixed(2)} % del patrimonio</td></tr>
          <tr><td>Exposición máxima total</td><td>{(r.exposicion_max_total * 100).toFixed(0)} % (sin apalancamiento)</td></tr>
          <tr><td>Pérdida diaria máxima</td><td>{(r.perdida_diaria_max * 100).toFixed(1)} % → bloquea nuevas órdenes</td></tr>
          <tr><td>Antigüedad máxima del dato</td><td>{r.antiguedad_max_horas} h</td></tr>
          <tr><td>Ventas en corto / apalancamiento</td><td>no permitidos</td></tr>
        </tbody></table>
      </div>
      <div>
        <h2>Estado de riesgo</h2>
        <table><tbody>
          <tr><td>Estado del agente</td><td><b>{estado.estado}</b></td></tr>
          <tr><td>Exposición actual</td><td>{usd(expo)} USDT ({cuenta ? ((expo / cuenta.patrimonio) * 100).toFixed(1) : 0} %)</td></tr>
          <tr><td>Posiciones abiertas</td><td>{cuenta?.posiciones.length ?? 0}</td></tr>
          <tr><td>Motivo</td><td>{estado.motivo || "—"}</td></tr>
        </tbody></table>
        <h3>Cambiar límites {editable ? "" : "(detén o pausa el agente primero)"}</h3>
        {(["exposicion_max_por_par", "perdida_diaria_max", "antiguedad_max_horas"] as const).map((k) => (
          <label key={k}>{k}<input type="number" step="any" disabled={!editable} value={f[k]}
            onChange={(e) => setF({ ...f, [k]: Number(e.target.value) })} /></label>))}
        <button disabled={!editable} onClick={guardar}>Guardar límites</button>
        {msg && <p className="nota">{msg}</p>}
      </div>
    </section>
  );
}

// ------------------------------------------------------------------ Historial
export function Historial({ tick }: { tick: number }) {
  const [d] = useDatos<Decision[]>(() => api.decisiones("vivo"), [tick]);
  const [o] = useDatos<Orden[]>(() => api.ordenes("vivo"), [tick]);
  const [c] = useDatos<Ciclo[]>(() => api.ciclos("vivo"), [tick]);
  return (
    <>
      <section><h2>Ciclos de decisión ({c?.length ?? 0})</h2>
        <div className="tabla"><table><thead><tr><th>Vela</th><th>Inicio</th><th>Fin</th><th>Estado</th><th>Decisiones</th><th>Rechazos</th><th>Latencia LLM</th></tr></thead>
          <tbody>{c?.map((x) => <tr key={x.id}><td>{x.fecha_vela}</td><td>{hora(x.inicio_utc)}</td><td>{hora(x.fin_utc)}</td><td>{x.estado}</td><td>{x.n_decisiones}</td><td>{x.n_rechazos}</td><td>{x.latencia_llm_ms_prom ? `${(x.latencia_llm_ms_prom / 1000).toFixed(1)} s` : "—"}</td></tr>)}</tbody></table></div></section>
      <section><h2>Órdenes Paper</h2>
        <div className="tabla"><table><thead><tr><th>Hora</th><th>Estrategia</th><th>Par</th><th>Vela</th><th>Lado</th><th>Cantidad</th><th>Precio</th><th>Monto</th><th>Costo</th><th>Referencia</th></tr></thead>
          <tbody>{o?.map((x) => <tr key={x.id}><td>{hora(x.ts_utc)}</td><td>{NOMBRE_ESTRATEGIA[x.estrategia]}</td><td>{x.simbolo}</td><td>{x.fecha_vela}</td><td>{x.lado}</td><td>{num(x.cantidad)}</td><td>{num(x.precio)}</td><td>{usd(x.monto)}</td><td>{usd(x.costo)}</td><td>{x.precio_referencia}</td></tr>)}</tbody></table></div></section>
      <section><h2>Decisiones (incluye rechazos)</h2>
        <div className="tabla"><table><thead><tr><th>Vela</th><th>Par</th><th>Pronóstico</th><th>Regla</th><th>LLM</th><th>Herramientas</th><th>Final</th><th>Motivo</th></tr></thead>
          <tbody>{d?.map((x) => <tr key={x.id} className={x.aprobada ? "" : "fila-rechazo"}><td>{x.fecha_vela}</td><td>{x.simbolo}</td><td>{pctDec(x.pronostico)}</td><td>{x.accion_modelo ?? "—"}</td><td>{x.accion_llm ?? (x.json_valido === 0 ? "JSON inválido" : "—")}</td><td>{x.herramientas || "—"}</td><td>{x.accion_final}</td><td className="motivo" title={x.justificacion ?? ""}>{x.motivo}</td></tr>)}</tbody></table></div></section>
    </>
  );
}

// ------------------------------------------------------------------ Replay
export function Replay({ estado, alLanzar, tick }: { estado: Estado; alLanzar: () => void; tick: number }) {
  const [dias, setDias] = useState(20);
  const [llm, setLlm] = useState(true);
  const [msg, setMsg] = useState("");
  const rp = estado.replay || {};
  const lanzar = async () => {
    try { await api.replay(dias, llm); setMsg("Replay iniciado."); alLanzar(); } catch (e) { setMsg((e as Error).message); }
  };
  return (
    <>
      <section>
        <h2>REPLAY · repetición histórica (separada de la operación en vivo)</h2>
        <p className="nota">Reproduce los últimos días cerrados con el mismo ciclo: variables → modelo → LLM → riesgo → Paper, ejecutando cada señal en la apertura del día siguiente. Sus resultados se guardan aparte (modo <code>replay</code>) y no se mezclan con la conexión actual. Con el LLM en CPU puede tardar ~1 minuto por día.</p>
        <label>Días <input type="number" min={5} max={60} value={dias} onChange={(e) => setDias(Number(e.target.value))} /></label>
        <label><input type="checkbox" checked={llm} onChange={(e) => setLlm(e.target.checked)} /> Usar el LLM (si no, solo la regla)</label>
        <button className="primario" disabled={rp.estado === "en_curso"} onClick={lanzar}>Lanzar replay</button>
        {msg && <p className="nota">{msg}</p>}
        <p>Estado: <b>{rp.estado ?? "sin ejecutar"}</b> {rp.avance != null && `· ${rp.avance}/${rp.dias} días`} {rp.desde && `· ${rp.desde} → ${rp.hasta}`} {rp.error && <span className="error"> · {rp.error}</span>}</p>
      </section>
      <Rendimiento capital={estado.riesgo.capital_inicial} modo="replay" tick={tick} />
    </>
  );
}

// ------------------------------------------------------------------ Sistema
export function Sistema({ salud, estado, tick }: { salud: Salud | null; estado: Estado; tick: number }) {
  const [ev] = useDatos<Evento[]>(() => api.eventos(), [tick]);
  return (
    <>
      <section className="tarjetas">
        <div className="tarjeta"><div className={`valor ${salud?.mexc.ok ? "pos" : "neg"}`}>{salud?.mexc.ok ? `${salud.mexc.latencia_ms} ms` : "caída"}</div><div className="nombre">Latencia MEXC</div></div>
        <div className="tarjeta"><div className={`valor ${salud?.llm.ok ? "pos" : "neg"}`}>{salud?.llm.ok ? "local" : "no disponible"}</div><div className="nombre">Ollama {salud?.llm.ollama_version ?? ""} · {salud?.llm.modelo ?? estado.llm.modelo}</div></div>
        <div className="tarjeta"><div className="valor">{salud?.sistema.memoria_backend_mb ?? "—"} MB</div><div className="nombre">Memoria del backend</div></div>
        <div className="tarjeta"><div className="valor">{salud?.sistema.memoria_libre_mb ?? "—"} MB</div><div className="nombre">Memoria libre del equipo</div></div>
        <div className="tarjeta"><div className="valor">{estado.duracion_ultimo_ciclo_s ? `${estado.duracion_ultimo_ciclo_s.toFixed(1)} s` : "—"}</div><div className="nombre">Duración del último ciclo</div></div>
        <div className="tarjeta"><div className="valor">{estado.poll_segundos} s</div><div className="nombre">Revisión de vela nueva</div></div>
      </section>
      <section className="dos">
        <div><h2>Modelo predictivo</h2><table><tbody>
          <tr><td>Versión</td><td>{estado.modelo.version}</td></tr>
          <tr><td>Corrida de entrenamiento (T4)</td><td>{estado.modelo.run_id}</td></tr>
          <tr><td>Diferencia vs Keras</td><td>{estado.modelo.dif_vs_keras?.toExponential(1)}</td></tr>
          <tr><td>Regla</td><td>comprar si pronóstico &gt; {(estado.modelo.regla.delta * 100).toFixed(2)} %</td></tr>
        </tbody></table></div>
        <div><h2>LLM local</h2><table><tbody>
          <tr><td>URL</td><td>{salud?.llm.url_local ?? "—"} (vía gateway con API key)</td></tr>
          <tr><td>Modelo</td><td>{salud?.llm.modelo ?? estado.llm.modelo} · {salud?.llm.parametros ?? ""} · {salud?.llm.cuantizacion ?? ""}</td></tr>
          <tr><td>Latencia de consulta</td><td>{salud?.llm.latencia_ms ? `${salud.llm.latencia_ms} ms` : "—"}</td></tr>
          <tr><td>Estado</td><td>{salud?.llm.ok ? "disponible" : (salud?.llm.error ?? "—")}</td></tr>
        </tbody></table></div>
      </section>
      <Feeds tick={tick} />
      <section><h2>Bitácora de eventos y errores</h2>
        <div className="tabla"><table><thead><tr><th>Hora</th><th>Nivel</th><th>Etapa</th><th>Tipo</th><th>Mensaje</th></tr></thead>
          <tbody>{ev?.map((x) => <tr key={x.id} className={`nivel-${x.nivel}`}><td>{hora(x.ts_utc)}</td><td>{x.nivel}</td><td>{x.etapa}</td><td>{x.tipo}</td><td className="motivo">{x.mensaje}</td></tr>)}</tbody></table></div></section>
    </>
  );
}

function Feeds({ tick }: { tick: number }) {
  const [f, setF] = useState<Record<string, Feed>>({});
  const [cargando, setCargando] = useState(false);
  useEffect(() => { api.feeds().then(setF).catch(() => undefined); }, [tick]);
  const verificar = async () => { setCargando(true); try { setF(await api.feeds(true)); } finally { setCargando(false); } };
  const filas = Object.entries(f);
  return (
    <section>
      <div className="cab"><h2>Feeds e integridad (15 pares)</h2><button disabled={cargando} onClick={verificar}>{cargando ? "Verificando…" : "Verificar ahora en MEXC"}</button></div>
      {!filas.length ? <p className="vacio">Sin revisión todavía: se llena en cada ciclo o con «Verificar ahora».</p> :
        <div className="tabla"><table><thead><tr><th>Símbolo</th><th>Estado</th><th>Velas cerradas</th><th>Última vela</th><th>Antigüedad</th><th>Latencia</th><th>Revisado</th><th>Detalle</th></tr></thead>
          <tbody>{filas.map(([s, x]) => <tr key={s}><td><b>{s}</b></td><td className={x.estado === "ok" ? "ok" : "rechazo"}>{x.estado}</td><td>{x.velas ?? "—"}</td><td>{x.ultima_vela ?? "—"}</td>
            <td>{x.antiguedad_h == null ? "—" : `${x.antiguedad_h.toFixed(1)} h`}</td><td>{x.latencia_ms ? `${x.latencia_ms} ms` : "—"}</td><td>{hora(x.revisado_utc)}</td><td className="motivo">{x.mensaje || ""}</td></tr>)}</tbody></table></div>}
    </section>
  );
}
