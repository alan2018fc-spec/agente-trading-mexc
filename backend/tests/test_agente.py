"""Pruebas del modelo, del ciclo, del motor de riesgo y de la ausencia de trading real."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.config import ConfigError, Riesgo
from app.modelo import ModeloLSTM
from app.riesgo import ContextoRiesgo, revisar
from app.variables import Vela, calcular
from conftest import RAIZ, correr

# Cierres reales de MEXC (2026-10-25 … 2026-11-19) y pronósticos que dio el notebook v2.1 en Colab.
REAL = Path(__file__).with_name("datos_mexc_2026-11-19.json")


def test_modelo_reproduce_keras():
    m = ModeloLSTM(RAIZ / "modelo")
    assert m.diferencia_maxima_vs_keras < 1e-5


def test_variables_y_pronostico_igual_al_notebook():
    datos = json.loads(REAL.read_text(encoding="utf-8"))
    m = ModeloLSTM(RAIZ / "modelo")
    vs = []
    for s, filas in datos["cierres"].items():
        velas = []
        for f, c in filas:
            t = int(datetime.strptime(f, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp() * 1000)
            velas.append(Vela(t, c, c, c, c, 1.0, t + 86_400_000))
        vs.append(calcular(s, velas))
    p = m.predecir(vs)
    for v, x in zip(vs, p):
        assert abs(x - datos["pronostico_notebook"][v.simbolo]) < 5e-6, v.simbolo


def test_ciclo_completo_tres_estrategias(entorno):
    ag = entorno["construir"]()

    async def f():
        await ag.iniciar(); await ag.detener_bucle()
        return await ag.paso()
    r = correr(f())
    assert r.decisiones == 15 and not r.errores
    assert entorno["llm"].con_herramientas == 15                   # el LLM usó la herramienta de solo lectura
    estr = {o["estrategia"] for o in ag.db.filas("SELECT estrategia FROM ordenes")}
    assert "comprar_mantener" in estr
    bh = ag.paper.posiciones("vivo", "comprar_mantener")
    assert len(bh) == 15
    # ninguna compra supera el límite por par (1/15 del patrimonio)
    for o in ag.db.filas("SELECT * FROM ordenes WHERE lado='COMPRA'"):
        assert o["monto"] + o["costo"] <= 0.0667 * 50_000 + 1e-6
    d = ag.db.fila("SELECT * FROM decisiones WHERE accion_llm IS NOT NULL")
    assert d["json_valido"] == 1 and d["justificacion"] and d["version_modelo"].startswith("LSTM16")


def test_llm_no_puede_saltarse_el_riesgo(entorno):
    entorno["llm"].modo = "contrario"            # propone VENDER sin posición (venta en corto)
    ag = entorno["construir"]()

    async def f():
        await ag.iniciar(); await ag.detener_bucle()
        return await ag.paso()
    correr(f())
    ds = ag.db.filas("SELECT * FROM decisiones WHERE accion_llm='VENDER'")
    assert ds and all(d["aprobada"] == 0 and "sin ventas en corto" in d["motivo"] for d in ds)
    assert not ag.db.filas("SELECT * FROM ordenes WHERE estrategia='agente_llm' AND lado='VENTA'")


def test_limite_perdida_diaria_bloquea():
    r = Riesgo.cargar(RAIZ / "config" / "riesgo.json")
    rev = revisar(r, ContextoRiesgo(True, "BTCUSDT", "COMPRAR", "d", "d", 1, False, 50000, 50000, 0, 1, True))
    assert not rev.aprobada and "pérdida diaria" in rev.motivo


def test_replay_separado_del_vivo(entorno):
    ag = entorno["construir"]()

    async def f():
        ag.lanzar_replay(6, usar_llm=True)
        await ag._tarea_replay
    correr(f())
    assert ag.replay["estado"] == "completo"
    assert ag.db.fila("SELECT COUNT(*) c FROM decisiones WHERE modo='replay'")["c"] == 6 * 15
    assert ag.db.fila("SELECT COUNT(*) c FROM decisiones WHERE modo='vivo'")["c"] == 0
    refs = {o["precio_referencia"] for o in ag.db.filas("SELECT precio_referencia FROM ordenes WHERE modo='replay'")}
    assert all("replay" in x for x in refs)


def test_sin_trading_real(monkeypatch):
    import app.mexc as mexc_mod
    fuente = Path(mexc_mod.__file__).read_text(encoding="utf-8")
    assert "/api/v3/order" not in fuente and "signature" not in fuente.lower()
    monkeypatch.setenv("TRADING_REAL", "1")
    from app.config import Ajustes
    with pytest.raises(ConfigError):
        Ajustes.desde_entorno()


def test_cerebro_red_y_feeds(entorno):
    import httpx
    from fastapi.testclient import TestClient
    from app.main import crear_app
    from app.llm import ClienteLLM
    from app.mexc import ClienteMEXC
    app = crear_app(entorno["aj"], mexc=ClienteMEXC(transporte=httpx.MockTransport(entorno["mexc"])),
                    llm=ClienteLLM("http://gateway", entorno["aj"].llm_gateway_key, "qwen2.5:3b",
                                   transporte=httpx.MockTransport(entorno["llm"])), iniciar_bucle=False)
    k = {"X-API-Key": entorno["aj"].backend_api_key}
    with TestClient(app) as c:
        r0 = c.get("/api/red?simbolo=ETHUSDT", headers=k).json()          # antes de decidir: datos en vivo
        assert r0["fuente"].startswith("MEXC") and len(r0["semillas"]) == 3 and len(r0["semillas"][0]["h"]) == 16
        assert abs(r0["pronostico"] - sum(x["salida"] for x in r0["semillas"]) / 3) < 1e-12
        c.post("/api/agente/iniciar", headers=k)
        correr_ciclo = c.post("/api/agente/ciclo-ahora", headers=k)
        assert correr_ciclo.status_code == 200
        import time as _t; _t.sleep(0.5)
        cb = c.get("/api/cerebro", headers=k).json()
        assert cb["feeds_total"] == 15 and cb["ultima_vela"]
        r1 = c.get("/api/red?simbolo=ETHUSDT", headers=k).json()
        assert r1["fuente"].startswith("última decisión")
        d = c.get("/api/decisiones", headers=k).json()
        p_eth = next(x["pronostico"] for x in d if x["simbolo"] == "ETHUSDT")
        assert abs(r1["pronostico"] - p_eth) < 1e-9                         # la red dibuja el mismo pronóstico
        f = c.get("/api/feeds?verificar=true", headers=k).json()
        assert len(f) == 15 and all(x["estado"] == "ok" for x in f.values())
        assert c.get("/api/red?simbolo=FAKE", headers=k).status_code == 400


def test_comprar_y_mantener_invierte_lo_mismo_en_cada_par(entorno):
    """Aunque el precio de ejecución sea menor que el cierre usado para valorar, cada par recibe ~1/15."""
    fm = entorno["mexc"]
    original = fm.__call__

    class Caida:
        def __call__(self, req):
            r = original(req)
            if req.url.path == "/api/v3/ticker/bookTicker":
                import json as _j, httpx as _h
                d = _j.loads(r.content); d["bidPrice"] = str(float(d["bidPrice"]) * 0.9); d["askPrice"] = str(float(d["askPrice"]) * 0.9)
                return _h.Response(200, json=d)
            return r
    import httpx
    from app.mexc import ClienteMEXC
    ag = entorno["construir"]()
    ag.mexc = ClienteMEXC(transporte=httpx.MockTransport(Caida()), espera_inicial=0.01)

    async def f():
        await ag.iniciar(); await ag.detener_bucle()
        return await ag.paso()
    correr(f())
    montos = [o["monto"] + o["costo"] for o in ag.db.filas("SELECT * FROM ordenes WHERE estrategia='comprar_mantener'")]
    assert len(montos) == 15 and min(montos) > 0.0667 * 50_000 * 0.99
