"""Vista pública del agente de trading MEXC (Streamlit).

- Señales de hoy: velas diarias REALES de la API pública de MEXC y el MISMO modelo LSTM del agente (pesos de la T4).
- Operaciones y PnL, rendimiento y decisiones: registro real del agente (datos_publicos/snapshot_agente.json),
  exportado de su base SQLite con backend/exportar_snapshot.py.

Es de solo lectura: no hay órdenes (ni reales ni Paper), ni claves, ni LLM en esta página. El agente completo
(LLM local, motor de riesgo, Paper, avisos) corre en la computadora del equipo; ver README.
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import requests
import streamlit as st

RAIZ = Path(__file__).resolve().parent
sys.path.insert(0, str(RAIZ / "backend"))

from app.modelo import ModeloLSTM  # noqa: E402
from app.variables import DIA_MS, DatosInvalidos, Vela, calcular  # noqa: E402
from app.version import FECHA_VERSION, VERSION  # noqa: E402

MEXC = os.getenv("MEXC_URL", "https://api.mexc.com")
REPO = "https://github.com/alan2018fc-spec/agente-trading-mexc"
NOMBRE = {"agente_llm": "Agente con LLM", "modelo": "Modelo sin LLM", "comprar_mantener": "Comprar y mantener"}

st.set_page_config(page_title="Agente de trading MEXC (Paper)", page_icon="📈", layout="wide")


# ----------------------------------------------------------------------------------------- datos
@st.cache_resource
def cargar_modelo() -> tuple[ModeloLSTM, float]:
    m = ModeloLSTM(RAIZ / "backend" / "modelo")          # verifica contra las salidas de Keras al cargar
    return m, m.verificar()


@st.cache_data
def cargar_snapshot() -> dict:
    return json.loads((RAIZ / "datos_publicos" / "snapshot_agente.json").read_text(encoding="utf-8"))


@st.cache_data(ttl=900, show_spinner=False)
def velas_mexc(simbolo: str, limite: int = 90) -> list[dict]:
    """Velas diarias CERRADAS de MEXC (API pública v3). Se guardan 15 min en caché."""
    r = requests.get(f"{MEXC}/api/v3/klines", params={"symbol": simbolo, "interval": "1d", "limit": limite}, timeout=15)
    r.raise_for_status()
    ahora = int(time.time() * 1000)
    vistas: dict[int, dict] = {}
    for k in r.json():
        v = {"open_time": int(k[0]), "open": float(k[1]), "high": float(k[2]), "low": float(k[3]),
             "close": float(k[4]), "volume": float(k[5]), "close_time": int(k[6])}
        if v["close_time"] - v["open_time"] in (DIA_MS, DIA_MS - 1) and v["close_time"] <= ahora:
            vistas.setdefault(v["open_time"], v)
    return [vistas[t] for t in sorted(vistas)]


def senales_en_vivo(modelo: ModeloLSTM) -> tuple[pd.DataFrame, dict, list[str]]:
    variables, historia, errores = [], {}, []
    for s in modelo.pares:
        try:
            filas = velas_mexc(s)
            velas = [Vela(**f) for f in filas]
            variables.append(calcular(s, velas[-60:]))
            historia[s] = filas
        except (requests.RequestException, ValueError, DatosInvalidos) as e:
            errores.append(f"{s}: {str(e)[:120]}")
    if not variables:
        return pd.DataFrame(), historia, errores
    pron = modelo.predecir(variables)
    delta = modelo.regla["delta"]
    df = pd.DataFrame([{
        "Par": v.simbolo, "Vela (UTC)": v.fecha_vela, "Cierre (USDT)": v.cierre,
        "Pronóstico mañana": 100 * p, "Supera δ": bool(p > delta),
        "Señal de la regla": "COMPRAR" if p > delta else "No comprar / vender",
        "dist_sma20": 100 * v.dist_sma20, "vol20": 100 * v.vol20,
    } for v, p in zip(variables, pron)])
    return df.sort_values("Pronóstico mañana", ascending=False), historia, errores


def pct(x: float | None, d: int = 2) -> str:
    return "—" if x is None else f"{x:+.{d}f} %"


def usd(x: float) -> str:
    return f"{x:+,.2f}"


# ----------------------------------------------------------------------------------------- página
snap = cargar_snapshot()
modelo, dif_keras = cargar_modelo()
vivo = snap["modos"]["vivo"]
ultima_vela = vivo["ciclos"][-1]["fecha_vela"] if vivo["ciclos"] else "—"

st.title("📈 Agente de trading MEXC — Paper trading")
st.caption(f"v{VERSION} ({FECHA_VERSION}) · 15 pares de MEXC · LSTM entrenada en T4 + LLM local + motor de riesgo · "
           f"vista pública de solo lectura · [código y README]({REPO})")

c = st.columns(4)
est = {e["estrategia"]: e for e in vivo["pnl"]["estrategias"]}
ag = est.get("agente_llm", {})
c[0].metric("PnL del agente con LLM (en vivo)", pct(ag.get("pnl_total_pct")), f"{usd(ag.get('pnl_total', 0))} USDT")
c[1].metric("Operaciones cerradas del agente", f"{ag.get('cerradas', 0)}",
            f"{ag.get('ganadoras', 0)} con ganancia", delta_color="off")
c[2].metric("Velas operadas en vivo", f"{len(vivo['ciclos'])}", f"última: {ultima_vela}", delta_color="off")
c[3].metric("Órdenes reales", "0", "solo simulación (Paper)", delta_color="off")

tabs = st.tabs(["Señales de hoy (MEXC en vivo)", "Operaciones y PnL", "Rendimiento", "Decisiones del agente",
                "Modelo", "Cómo funciona"])

# --- 1. señales en vivo ---------------------------------------------------------------------------
with tabs[0]:
    st.subheader("Pronóstico del modelo con las velas reales de MEXC")
    st.write("Se descargan las velas diarias **cerradas** de los 15 pares desde la API pública de MEXC y se calcula "
             "el pronóstico con el mismo modelo LSTM del agente. Regla: **comprar si el pronóstico del día siguiente "
             f"supera δ = {100 * modelo.regla['delta']:.2f} %**; si no, no comprar (o vender si hay posición).")
    with st.spinner("Consultando MEXC…"):
        df, historia, errores = senales_en_vivo(modelo)
    if df.empty:
        st.warning("No se pudo consultar MEXC desde este servidor en este momento. Abajo están las últimas "
                   f"decisiones que tomó el agente en vivo (vela {ultima_vela}).")
        dec = pd.DataFrame([d for d in vivo["decisiones"] if d["fecha_vela"] == ultima_vela])
        if not dec.empty:
            dec = dec.assign(pronostico=100 * dec["pronostico"])[["simbolo", "fecha_vela", "cierre", "pronostico",
                                                                    "accion_modelo", "accion_final"]]
            st.dataframe(dec, hide_index=True, use_container_width=True,
                         column_config={"pronostico": st.column_config.NumberColumn("Pronóstico", format="%.3f %%")})
    else:
        n = int(df["Supera δ"].sum())
        st.success(f"Vela {df['Vela (UTC)'].max()} (cierra a las 18:00 de CDMX): **{n} de {len(df)} pares** con señal "
                   "de compra." if n else f"Vela {df['Vela (UTC)'].max()}: ningún par supera el umbral; la regla no compra.")
        st.dataframe(df.drop(columns=["Supera δ"]), hide_index=True, use_container_width=True, column_config={
            "Cierre (USDT)": st.column_config.NumberColumn(format="%.6g"),
            "Pronóstico mañana": st.column_config.NumberColumn(format="%+.3f %%"),
            "dist_sma20": st.column_config.NumberColumn("Distancia a SMA20", format="%+.2f %%"),
            "vol20": st.column_config.NumberColumn("Volatilidad 20 d", format="%.2f %%")})
        par = st.selectbox("Precio de cierre (últimos 90 días)", list(historia.keys()))
        h = pd.DataFrame(historia[par])
        h["Fecha"] = pd.to_datetime(h["open_time"], unit="ms", utc=True).dt.date
        st.line_chart(h.set_index("Fecha")["close"], y_label=f"{par} (USDT)")
    if errores:
        with st.expander(f"{len(errores)} par(es) sin datos válidos"):
            st.write("\n".join(f"- {e}" for e in errores))
    st.caption(f"Fuente: {MEXC}/api/v3/klines · consultado {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC "
               "(se actualiza cada 15 min). El LLM y las órdenes Paper solo corren en el agente local.")

# --- 2. operaciones y PnL -------------------------------------------------------------------------
with tabs[1]:
    modo = st.radio("Periodo", ["vivo", "replay"], horizontal=True,
                    format_func=lambda m: "Operación en vivo" if m == "vivo" else "Replay (1–20 nov)")
    p = snap["modos"][modo]["pnl"]
    st.subheader(f"PnL por estrategia · precios de cierre de MEXC al {p['precios_al']}")
    st.dataframe(pd.DataFrame([{
        "Estrategia": NOMBRE[e["estrategia"]], "PnL total": e["pnl_total_pct"], "PnL (USDT)": e["pnl_total"],
        "Realizado (USDT)": e["pnl_realizado"], "No realizado (USDT)": e["pnl_no_realizado"],
        "Cerradas": e["cerradas"], "Abiertas": e["abiertas"], "Ganadoras": e["ganadoras"],
        "Win rate": e["win_rate_pct"]} for e in p["estrategias"]]), hide_index=True, use_container_width=True,
        column_config={"PnL total": st.column_config.NumberColumn(format="%+.2f %%"),
                       "PnL (USDT)": st.column_config.NumberColumn(format="%+,.2f"),
                       "Realizado (USDT)": st.column_config.NumberColumn(format="%+,.2f"),
                       "No realizado (USDT)": st.column_config.NumberColumn(format="%+,.2f"),
                       "Win rate": st.column_config.NumberColumn(format="%.0f %%")})
    st.caption(f"Capital simulado de {snap['capital_inicial']:,.0f} USDT por estrategia. Realizado = venta − costos − "
               "(compra + costos); no realizado = posiciones abiertas al último cierre de MEXC.")

    estr = st.selectbox("Operaciones de", list(NOMBRE), format_func=NOMBRE.get)
    ops = pd.DataFrame([o for o in p["operaciones"] if o["estrategia"] == estr])
    if ops.empty:
        st.info("Esta estrategia todavía no tiene operaciones.")
    else:
        st.dataframe(ops[["simbolo", "estado", "fecha_entrada", "precio_entrada", "fecha_salida", "precio_salida",
                          "cantidad", "invertido", "pnl", "pnl_pct"]], hide_index=True, use_container_width=True,
                     column_config={"simbolo": "Par", "estado": "Estado", "fecha_entrada": "Entrada (vela)",
                                    "precio_entrada": st.column_config.NumberColumn("Precio entrada", format="%.6g"),
                                    "fecha_salida": "Salida / valuación",
                                    "precio_salida": st.column_config.NumberColumn("Precio salida", format="%.6g"),
                                    "cantidad": st.column_config.NumberColumn("Cantidad", format="%.6g"),
                                    "invertido": st.column_config.NumberColumn("Invertido (USDT)", format="%,.2f"),
                                    "pnl": st.column_config.NumberColumn("PnL (USDT)", format="%+,.2f"),
                                    "pnl_pct": st.column_config.NumberColumn("PnL %", format="%+.2f %%")})

    st.subheader("PnL diario")
    dia = pd.DataFrame(p["diario"])
    if not dia.empty:
        tabla = dia.pivot(index="fecha_vela", columns="estrategia", values="pnl_pct").rename(columns=NOMBRE)
        st.dataframe(tabla.sort_index(ascending=False), use_container_width=True,
                     column_config={c: st.column_config.NumberColumn(format="%+.2f %%") for c in tabla.columns})

# --- 3. rendimiento -------------------------------------------------------------------------------
with tabs[2]:
    for modo, titulo in (("vivo", "Operación en vivo"), ("replay", "Replay (velas del 1 al 20 de noviembre)")):
        st.subheader(f"Rendimiento acumulado · {titulo}")
        pat = pd.DataFrame(snap["modos"][modo]["patrimonio"])
        if pat.empty:
            st.info("Sin datos.")
            continue
        pat["Rendimiento %"] = 100 * (pat["patrimonio"] / snap["capital_inicial"] - 1)
        serie = pat.pivot(index="fecha_vela", columns="estrategia", values="Rendimiento %").rename(columns=NOMBRE)
        st.line_chart(serie, y_label="Rendimiento acumulado (%)")
        st.dataframe(pd.DataFrame([{
            "Estrategia": NOMBRE[r["estrategia"]], "Rendimiento neto": r["rendimiento_neto_pct"],
            "Drawdown máx.": r["drawdown_max_pct"], "Órdenes": r["ordenes"],
            "Operaciones cerradas": r["operaciones_cerradas"], "Win rate": r["win_rate_pct"]}
            for r in snap["modos"][modo]["comparacion"]]), hide_index=True, use_container_width=True,
            column_config={"Rendimiento neto": st.column_config.NumberColumn(format="%+.2f %%"),
                           "Drawdown máx.": st.column_config.NumberColumn(format="%.2f %%"),
                           "Win rate": st.column_config.NumberColumn(format="%.1f %%")})

# --- 4. decisiones --------------------------------------------------------------------------------
with tabs[3]:
    st.subheader("Cada decisión del agente en vivo, con la justificación del LLM")
    dec = pd.DataFrame(vivo["decisiones"])
    if dec.empty:
        st.info("Sin decisiones.")
    else:
        velas = sorted(dec["fecha_vela"].unique(), reverse=True)
        f = st.selectbox("Vela", ["Todas"] + velas)
        if f != "Todas":
            dec = dec[dec["fecha_vela"] == f]
        dec = dec.assign(pronostico=100 * dec["pronostico"], aprobada=dec["aprobada"].map({1: "sí", 0: "no"}),
                         latencia_llm_ms=dec["latencia_llm_ms"] / 1000)
        st.dataframe(dec[["fecha_vela", "simbolo", "pronostico", "accion_modelo", "accion_llm", "accion_final",
                          "aprobada", "motivo", "justificacion", "latencia_llm_ms"]], hide_index=True,
                     use_container_width=True, column_config={
                         "fecha_vela": "Vela", "simbolo": "Par",
                         "pronostico": st.column_config.NumberColumn("Pronóstico", format="%+.3f %%"),
                         "accion_modelo": "Regla", "accion_llm": "LLM", "accion_final": "Final",
                         "aprobada": "Riesgo aprobó", "motivo": "Revisión de riesgo",
                         "justificacion": st.column_config.TextColumn("Justificación del LLM", width="large"),
                         "latencia_llm_ms": st.column_config.NumberColumn("Latencia LLM (s)", format="%.1f")})
    ciclos = pd.DataFrame(vivo["ciclos"])
    if not ciclos.empty:
        st.subheader("Ciclos diarios")
        st.dataframe(ciclos, hide_index=True, use_container_width=True)

# --- 5. modelo ------------------------------------------------------------------------------------
with tabs[4]:
    st.subheader("Un solo LSTM para los 15 pares (capítulos 7 a 11)")
    a, b = st.columns(2)
    a.markdown(f"""
- **Datos:** velas diarias UTC de MEXC del 2020-09-01 al 2026-11-19 (2,271 por par)
- **Entradas:** 20 rendimientos (antiguo → reciente), par one-hot, `dist_sma20` y `vol20`
- **Red:** LSTM(16) → Dense(16, ReLU) → Dropout(0.10) → Dense(1); ensamble de 3 semillas
- **Entrenamiento:** Google Colab, NVIDIA Tesla T4, corrida `{modelo.contrato['run_id']}`
- **Regla:** comprar si el pronóstico > {100 * modelo.regla['delta']:.2f} %
- **Versión cargada:** `{modelo.version}` · diferencia contra Keras: {dif_keras:.1e}
""")
    b.dataframe(pd.DataFrame([
        {"Modelo (TE, 2025-12-18 a 2026-11-19)": "LSTM 16 + indicadores", "RMSE (pp)": 3.3131, "Precisión direccional": "48.37 %"},
        {"Modelo (TE, 2025-12-18 a 2026-11-19)": "Pronóstico cero", "RMSE (pp)": 3.3191, "Precisión direccional": "—"},
        {"Modelo (TE, 2025-12-18 a 2026-11-19)": "MLP 16 + indicadores", "RMSE (pp)": 3.3518, "Precisión direccional": "47.82 %"},
        {"Modelo (TE, 2025-12-18 a 2026-11-19)": "Regresión lineal", "RMSE (pp)": 3.3719, "Precisión direccional": "48.61 %"},
    ]), hide_index=True, use_container_width=True)
    b.dataframe(pd.DataFrame([
        {"Simulación en TE": "Regla congelada, costo 0.10 %", "Rendimiento neto": "−9.86 %", "Drawdown máx.": "−19.18 %"},
        {"Simulación en TE": "Regla congelada, costo 0 % (MEXC)", "Rendimiento neto": "−7.36 %", "Drawdown máx.": "−18.30 %"},
        {"Simulación en TE": "Comprar y mantener", "Rendimiento neto": "−40.50 %", "Drawdown máx.": "−48.53 %"},
    ]), hide_index=True, use_container_width=True)
    st.caption("La LSTM tiene el menor error, pero apenas mejora al pronóstico cero; su valor práctico es reducir el "
               "drawdown frente a comprar y mantener.")

# --- 6. cómo funciona -----------------------------------------------------------------------------
with tabs[5]:
    st.subheader("Cómo funciona el agente completo")
    st.markdown(f"""
Cada día, al cierre de la vela de MEXC (18:00 en Ciudad de México), el agente local:

1. Descarga las velas **cerradas** de los 15 pares y valida orden, continuidad y antigüedad (≤ 26 h).
2. Calcula las variables con la misma definición del entrenamiento y obtiene el pronóstico de la LSTM.
3. Consulta a un **LLM local** (Ollama, `qwen2.5:3b`), que responde en JSON con acción y justificación.
4. Pasa la propuesta por un **motor de riesgo** determinista (exposición por par 1/15, pérdida diaria 5 %,
   antigüedad del dato), que el LLM no puede saltarse.
5. Ejecuta en **Paper** al ask/bid real de MEXC con 0.05 % de deslizamiento y lo registra en SQLite.
6. Si hay una oportunidad de compra, manda un **aviso por WhatsApp**.

En paralelo lleva tres libros: agente con LLM, modelo sin LLM y comprar y mantener.

**Esta página** usa el mismo modelo con las velas reales de MEXC y muestra el registro del agente exportado el
{snap['generado_utc'].replace('T', ' ').replace('Z', ' UTC')}. No envía órdenes, no usa claves y no ejecuta el LLM.

Código, README (versiones, avances, resultados, instalación) y pruebas: [{REPO}]({REPO})
""")
    ev = pd.DataFrame(snap.get("eventos", []))
    if not ev.empty:
        st.subheader("Bitácora del agente (conteo de eventos)")
        st.dataframe(ev, hide_index=True, use_container_width=True)
