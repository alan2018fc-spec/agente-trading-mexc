"""Los 9 errores que exige el protocolo (sección 15). Cada uno debe: mostrar mensaje útil, registrar el evento
y evitar operaciones indebidas."""
from __future__ import annotations

import asyncio

import pytest

from app.config import Riesgo
from app.paper import Paper, SaldoInsuficiente
from app.riesgo import ContextoRiesgo, revisar
from conftest import correr


def _ordenes(ag, modo="vivo"):
    return ag.db.filas("SELECT * FROM ordenes WHERE modo=?", (modo,))


def _eventos(ag, tipo):
    return ag.db.filas("SELECT * FROM eventos WHERE tipo=?", (tipo,))


# 1 ---------------------------------------------------------------------------------------------
@pytest.mark.parametrize("modo", ["limite", "caido"])
def test_01_api_mexc_sin_respuesta_o_limite(entorno, modo):
    ag = entorno["construir"]()
    entorno["mexc"].modo = modo

    async def f():
        await ag.iniciar(); await ag.detener_bucle()
        return await ag.paso()
    assert correr(f()) is None
    assert ag.estado == "PAUSADO_ERROR"                        # pausa nuevas decisiones
    assert "intentos" in ag.db.get("motivo_estado")            # reintentos limitados antes de pausar
    assert entorno["mexc"].llamadas == 3
    assert _eventos(ag, "MexcError") and not _ordenes(ag)


# 2 ---------------------------------------------------------------------------------------------
def test_02_dato_atrasado(entorno):
    entorno["mexc"].atrasado = {"SOLUSDT"}
    ag = entorno["construir"]()

    async def f():
        await ag.iniciar(); await ag.detener_bucle()
        return await ag.paso()
    res = correr(f())
    d = ag.db.fila("SELECT * FROM decisiones WHERE simbolo='SOLUSDT'")
    assert d["accion_final"] == "SIN_DECISION" and "atrasado" in d["motivo"]
    assert not [o for o in _ordenes(ag) if o["simbolo"] == "SOLUSDT"]
    assert res.decisiones == 14 and _eventos(ag, "dato_invalido")
    # y el motor de riesgo rechaza cualquier propuesta con dato más viejo que el límite
    r = entorno["riesgo"]
    rev = revisar(r, ContextoRiesgo(True, "BTCUSDT", "COMPRAR", "2026-11-19", "2026-11-19", 30.0, False, 50000, 50000, 0, 1, False))
    assert not rev.aprobada and "atrasado" in rev.motivo


# 3 ---------------------------------------------------------------------------------------------
def test_03_ollama_detenido(entorno):
    entorno["llm"].modo = "caido"
    ag = entorno["construir"]()

    async def f():
        await ag.iniciar(); await ag.detener_bucle()
        return await ag.paso()
    correr(f())
    assert ag.estado == "PAUSADO_ERROR" and "LLM no disponible" in ag.db.get("motivo_estado")
    assert entorno["llm"].llamadas == 3                        # 3 reintentos con espera creciente
    assert not [o for o in _ordenes(ag) if o["estrategia"] == "agente_llm"]
    assert ag.ultima_procesada("vivo") is None                 # la vela queda pendiente, no se marca como hecha


# 4 ---------------------------------------------------------------------------------------------
def test_04_json_invalido(entorno):
    entorno["llm"].modo = "json_invalido"
    ag = entorno["construir"]()

    async def f():
        await ag.iniciar(); await ag.detener_bucle()
        return await ag.paso()
    correr(f())
    ds = ag.db.filas("SELECT * FROM decisiones")
    assert len(ds) == 15 and all(d["json_valido"] == 0 and d["accion_final"] == "SIN_ORDEN" for d in ds)
    assert "no devolvió JSON" in ds[0]["motivo"]
    assert not [o for o in _ordenes(ag) if o["estrategia"] == "agente_llm"]
    assert len(_eventos(ag, "json_invalido")) == 15
    # símbolo no permitido en la respuesta: también se rechaza
    entorno["llm"].modo = "otro_simbolo"
    ag2 = entorno["construir"](entorno["aj"].db_path.with_name("b.db"))

    async def g():
        await ag2.iniciar(); await ag2.detener_bucle()
        return await ag2.paso()
    correr(g())
    assert all("Símbolo no permitido" in d["motivo"] for d in ag2.db.filas("SELECT motivo FROM decisiones"))


# 5 ---------------------------------------------------------------------------------------------
def test_05_clave_incorrecta(entorno):
    from fastapi.testclient import TestClient
    from app.main import crear_app
    import httpx
    from app.llm import ClienteLLM
    from app.mexc import ClienteMEXC
    app = crear_app(entorno["aj"], mexc=ClienteMEXC(transporte=httpx.MockTransport(entorno["mexc"])),
                    llm=ClienteLLM("http://gateway", "x" * 20, "qwen2.5:3b", transporte=httpx.MockTransport(entorno["llm"])),
                    iniciar_bucle=False)
    with TestClient(app) as c:
        assert c.get("/api/estado").status_code == 401                                   # sin clave
        assert c.get("/api/estado", headers={"X-API-Key": "incorrecta"}).status_code == 401
        assert c.post("/api/agente/iniciar", headers={"X-API-Key": "incorrecta"}).status_code == 401
        assert c.post("/api/login", json={"password": "mala"}).status_code == 401
        assert c.get("/api/estado", headers={"X-API-Key": entorno["aj"].backend_api_key}).status_code == 200
        assert c.post("/api/login", json={"password": entorno["aj"].admin_password}).status_code == 200
        assert c.get("/api/estado").status_code == 200                                    # cookie de sesión
        ev = c.get("/api/eventos").json()
    assert sum(e["tipo"] == "clave_incorrecta" for e in ev) == 3
    # el gateway del LLM también rechaza claves incorrectas
    entorno["llm"].modo = "clave"
    ag = entorno["construir"](entorno["aj"].db_path.with_name("c.db"))

    async def f():
        await ag.iniciar(); await ag.detener_bucle()
        return await ag.paso()
    correr(f())
    assert ag.estado == "PAUSADO_ERROR" and "rechazó la clave" in ag.db.get("motivo_estado")


def test_05b_gateway_rechaza_clave(monkeypatch):
    import importlib
    from fastapi.testclient import TestClient
    gw = importlib.import_module("gateway")
    with TestClient(gw.app) as c:
        assert c.post("/api/chat", json={"model": gw.MODELO, "messages": []}).status_code == 401
        assert c.post("/api/chat", json={"model": gw.MODELO, "messages": []}, headers={"X-API-Key": "mala"}).status_code == 401
        assert c.post("/api/chat", json={"model": "otro", "messages": []}, headers={"X-API-Key": gw.CLAVE}).status_code == 403


# 6 ---------------------------------------------------------------------------------------------
def test_06_saldo_insuficiente(entorno):
    ag = entorno["construir"]()
    p: Paper = ag.paper
    p.asegurar_cuenta("vivo", "agente_llm")
    with pytest.raises(SaldoInsuficiente):
        p.comprar("vivo", "agente_llm", "BTCUSDT", "2026-11-19", 60_000, 100.0, 0.0001, "prueba")
    assert p.efectivo("vivo", "agente_llm") == 50_000 and not _ordenes(ag)
    rev = revisar(entorno["riesgo"], ContextoRiesgo(True, "BTCUSDT", "COMPRAR", "d", "d", 1, False, 0.5, 50000, 49999, 1, False))
    assert not rev.aprobada and "Saldo insuficiente" in rev.motivo


# 7 ---------------------------------------------------------------------------------------------
def test_07_evento_duplicado(entorno):
    ag = entorno["construir"]()

    async def f():
        await ag.iniciar(); await ag.detener_bucle()
        r1 = await ag.paso()
        n = len(_ordenes(ag))
        r2 = await ag.revisar_nueva_vela(forzar=True)          # misma vela otra vez
        r3 = await ag.ciclo_vivo(r1.fecha_vela)                # forzado directo al ciclo
        return r1, n, r2, r3
    r1, n, r2, r3 = correr(f())
    assert r1.decisiones == 15 and r2 is None
    assert r3.duplicados == 15 and len(_ordenes(ag)) == n     # no se duplica ninguna orden
    assert ag.db.fila("SELECT COUNT(*) c FROM decisiones")["c"] == 15
    assert _eventos(ag, "duplicado")


# 8 ---------------------------------------------------------------------------------------------
def test_08_reinicio_backend(entorno):
    ag = entorno["construir"]()

    async def f():
        await ag.iniciar(); await ag.detener_bucle()
        return await ag.paso()
    r = correr(f())
    pos_antes = ag.paper.posiciones("vivo", "agente_llm")
    n = len(_ordenes(ag))
    ag2 = entorno["construir"]()                                # "reinicio": mismo archivo SQLite
    assert ag2.estado == "ACTIVO" and ag2.ultima_procesada("vivo") == r.fecha_vela
    assert ag2.paper.posiciones("vivo", "agente_llm").keys() == pos_antes.keys()

    async def g():
        return await ag2.paso()
    assert correr(g()) is None and len(_ordenes(ag2)) == n     # no reprocesa ni duplica
    assert ag2.db.filas("SELECT * FROM eventos WHERE tipo='reinicio'")


# 9 ---------------------------------------------------------------------------------------------
def test_09_boton_detener(entorno):
    ag = entorno["construir"]()

    async def f():
        await ag.iniciar(); await ag.detener_bucle()
        await ag.paso()
        await ag.detener()
        return await ag.paso()
    assert correr(f()) is None
    assert ag.estado == "DETENIDO"
    pos = ag.paper.posiciones("vivo", "agente_llm")
    assert pos, "detener NO liquida: las posiciones siguen abiertas y visibles"
    rev = revisar(entorno["riesgo"], ContextoRiesgo(False, "BTCUSDT", "COMPRAR", "d", "d", 1, False, 50000, 50000, 0, 1, False))
    assert not rev.aprobada and "detenido" in rev.motivo
    with pytest.raises(RuntimeError):                           # liquidar solo con el agente detenido o pausado
        ag.db.set("estado_agente", "ACTIVO"); correr(ag.liquidar())
    ag.db.set("estado_agente", "DETENIDO")
    liq = correr(ag.liquidar())
    assert len(liq) == len(pos) and not ag.paper.posiciones("vivo", "agente_llm")
