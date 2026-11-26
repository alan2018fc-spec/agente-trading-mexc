import { useCallback, useEffect, useState } from "react";
import { api, Estado, NoAutenticado, Salud } from "./api";
import { hora } from "./formato";
import { Cerebro } from "./Cerebro";
import { Historial, Operacion, Rendimiento, Replay, RiesgoPanel, Sistema } from "./Paneles";
import { RedNeuronal } from "./RedNeuronal";

const PESTANAS = ["Operación", "Cerebro", "Red neuronal", "Rendimiento", "Riesgo", "Historial", "Replay", "Sistema"] as const;

function Login({ alEntrar }: { alEntrar: () => void }) {
  const [clave, setClave] = useState("");
  const [error, setError] = useState("");
  const enviar = async (e: React.FormEvent) => {
    e.preventDefault();
    setError("");
    try { await api.login(clave); setClave(""); alEntrar(); }
    catch (err: any) { setError(err.message); }
  };
  return (
    <form className="login" onSubmit={enviar}>
      <h1>Agente MEXC · Paper</h1>
      <p>Introduce la contraseña de operador (está en <code>backend/.env</code>, nunca en el navegador).</p>
      <input type="password" value={clave} onChange={(e) => setClave(e.target.value)} placeholder="Contraseña" autoFocus />
      <button className="primario" type="submit">Entrar</button>
      {error && <p className="error">{error}</p>}
    </form>
  );
}

export default function App() {
  const [sesion, setSesion] = useState<boolean | null>(null);
  const [estado, setEstado] = useState<Estado | null>(null);
  const [salud, setSalud] = useState<Salud | null>(null);
  const [pestana, setPestana] = useState<(typeof PESTANAS)[number]>("Operación");
  const [aviso, setAviso] = useState("");
  const [ocupado, setOcupado] = useState(false);
  const [tick, setTick] = useState(0);

  const cargar = useCallback(async () => {
    try { setEstado(await api.estado()); setSesion(true); }
    catch (e) { if (e instanceof NoAutenticado) setSesion(false); else setAviso((e as Error).message); }
  }, []);

  useEffect(() => { cargar(); const t = setInterval(() => { cargar(); setTick((k) => k + 1); }, 10000); return () => clearInterval(t); }, [cargar]);
  useEffect(() => {
    if (!sesion) return;
    const f = () => api.salud().then(setSalud).catch(() => undefined);
    f(); const t = setInterval(f, 30000); return () => clearInterval(t);
  }, [sesion]);

  const accion = async (a: string, confirmar?: string) => {
    if (confirmar && !window.confirm(confirmar)) return;
    setOcupado(true); setAviso("");
    try {
      const r = await api.control(a);
      if (r?.mensaje) setAviso(r.mensaje);
      if (r?.liquidadas) setAviso(`Liquidadas ${r.liquidadas.length} posiciones (Paper).`);
      await cargar(); setTick((k) => k + 1);
    } catch (e) { setAviso((e as Error).message); }
    finally { setOcupado(false); }
  };

  if (sesion === false) return <Login alEntrar={cargar} />;
  if (!estado) return <div className="cargando">Conectando con el backend…</div>;

  const st = estado.estado;
  return (
    <div className="app">
      <header>
        <div>
          <div className="etiqueta">Modo de trading</div>
          <div className="modo">PAPER TRADING</div>
          <div className="sub">Trading real: <b>deshabilitado</b> (no existe ninguna ruta de órdenes reales)</div>
        </div>
        <div className={`estado estado-${st}`}>
          <span className="punto" /> {st}
          <div className="sub">{estado.motivo}</div>
        </div>
        <div className="controles">
          {st !== "ACTIVO" && <button className="primario" disabled={ocupado}
            onClick={() => accion(st === "DETENIDO" ? "iniciar" : "reanudar")}>▶ {st === "DETENIDO" ? "Iniciar" : "Reanudar"}</button>}
          {st === "ACTIVO" && <button disabled={ocupado} onClick={() => accion("pausar")}>❚❚ Pausar</button>}
          {st !== "DETENIDO" && <button className="peligro" disabled={ocupado}
            onClick={() => accion("detener", "Detener bloquea nuevas órdenes. Las posiciones NO se liquidan. ¿Continuar?")}>■ Detener</button>}
          {st === "ACTIVO" && <button disabled={ocupado} onClick={() => accion("ciclo-ahora")}>⟳ Revisar vela</button>}
          {(st === "DETENIDO" || st === "PAUSADO") && (estado.cuentas.agente_llm?.posiciones.length ?? 0) > 0 &&
            <button className="peligro" disabled={ocupado}
              onClick={() => accion("liquidar", "Vender en Paper TODAS las posiciones del agente al precio actual. ¿Continuar?")}>Liquidar (Paper)</button>}
          <button className="tenue" onClick={() => api.logout().then(() => setSesion(false))}>Salir</button>
        </div>
      </header>
      <div className="franja">
        <span>Datos MEXC · última vela procesada: <b>{estado.ultima_vela_procesada ?? "ninguna"}</b> (cierre diario 00:00 UTC = 18:00 CDMX)</span>
        <span>Modelo <b>{estado.modelo.version}</b> · δ = {(estado.modelo.regla.delta * 100).toFixed(2)} %</span>
        <span>LLM local <b>{estado.llm.modelo}</b> {salud && (salud.llm.ok ? "· disponible" : "· NO disponible")}</span>
        <span>Última revisión: {hora(estado.ultimo_tick)}</span>
      </div>
      {aviso && <div className="aviso" onClick={() => setAviso("")}>{aviso}</div>}
      <nav>{PESTANAS.map((p) => <button key={p} className={p === pestana ? "activa" : ""} onClick={() => setPestana(p)}>{p}</button>)}</nav>
      <main>
        {pestana === "Operación" && <Operacion estado={estado} tick={tick} />}
        {pestana === "Cerebro" && <Cerebro tick={tick} />}
        {pestana === "Red neuronal" && <RedNeuronal />}
        {pestana === "Rendimiento" && <Rendimiento capital={estado.riesgo.capital_inicial} modo="vivo" tick={tick} />}
        {pestana === "Riesgo" && <RiesgoPanel estado={estado} alCambiar={cargar} />}
        {pestana === "Historial" && <Historial tick={tick} />}
        {pestana === "Replay" && <Replay estado={estado} alLanzar={cargar} tick={tick} />}
        {pestana === "Sistema" && <Sistema salud={salud} estado={estado} tick={tick} />}
      </main>
      <footer>Proyecto académico · simulación con dinero virtual · datos públicos de MEXC · LLM local con Ollama</footer>
    </div>
  );
}
