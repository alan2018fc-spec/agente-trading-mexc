"""API del agente (FastAPI). Documentación interactiva en /docs.

Autenticación: cookie de sesión httpOnly (login con contraseña de operador) o cabecera X-API-Key.
Iniciar, pausar, detener, liquidar, replay y cambiar configuración están protegidos.
No existe ninguna ruta de órdenes reales.
"""
from __future__ import annotations

import asyncio
import hmac
import json
import logging
import os
import secrets
import time
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import metricas
from .ciclo import ESTRATEGIAS, Agente
from .config import BASE, TRADING_REAL_HABILITADO, Ajustes, Riesgo
from .db import DB
from .llm import ClienteLLM
from .mexc import ClienteMEXC, MexcError
from .modelo import ModeloLSTM, Variables
from .variables import DatosInvalidos, calcular, validar_velas
from .db import ahora_iso

(BASE / "logs").mkdir(exist_ok=True)


class _SinSecretos(logging.Filter):
    """Evita que una clave termine en los logs."""
    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        for nombre in ("BACKEND_API_KEY", "ADMIN_PASSWORD", "LLM_GATEWAY_KEY"):
            v = os.getenv(nombre)
            if v and v in msg:
                record.msg, record.args = msg.replace(v, "***"), ()
        return True


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s",
                    handlers=[logging.FileHandler(BASE / "logs" / "backend.log", encoding="utf-8"), logging.StreamHandler()])
for h in logging.getLogger().handlers:
    h.addFilter(_SinSecretos())
log = logging.getLogger("api")

SESIONES: dict[str, float] = {}


class Login(BaseModel):
    password: str = Field(min_length=1, max_length=200)


class PedidoReplay(BaseModel):
    dias: int = Field(20, ge=5, le=60)
    usar_llm: bool = True
DURACION_SESION = 12 * 3600


def crear_app(ajustes: Ajustes | None = None, mexc: ClienteMEXC | None = None, llm: ClienteLLM | None = None,
              iniciar_bucle: bool = True) -> FastAPI:
    aj = ajustes or Ajustes.desde_entorno()
    llm_cfg = json.loads(aj.llm_path.read_text(encoding="utf-8"))
    if llm_cfg.get("modelo_permitido") and llm_cfg["modelo_permitido"] != aj.ollama_modelo:
        log.warning("OLLAMA_MODELO (%s) distinto del de config/llm.json (%s)", aj.ollama_modelo, llm_cfg["modelo_permitido"])

    @asynccontextmanager
    async def vida(app: FastAPI):
        app.state.aj = aj
        app.state.riesgo = Riesgo.cargar(aj.riesgo_path)
        app.state.db = DB(aj.db_path)
        app.state.mexc = mexc or ClienteMEXC(aj.mexc_url)
        app.state.llm = llm or ClienteLLM(aj.llm_gateway_url, aj.llm_gateway_key, aj.ollama_modelo,
                                          temperatura=llm_cfg.get("temperatura", 0.1), timeout_s=llm_cfg.get("timeout_s", 120),
                                          reintentos=llm_cfg.get("reintentos", 3))
        app.state.modelo = ModeloLSTM(aj.modelo_dir)
        app.state.agente = Agente(aj, app.state.riesgo, app.state.db, app.state.mexc, app.state.llm, app.state.modelo, llm_cfg)
        if iniciar_bucle:
            app.state.agente.iniciar_bucle()          # si estaba ACTIVO antes del reinicio, continúa sin duplicar
        yield
        await app.state.agente.detener_bucle()
        await app.state.mexc.cerrar()
        await app.state.llm.cerrar()

    app = FastAPI(title="Agente de trading MEXC (Paper)", version="1.0", lifespan=vida,
                  description="Backend del agente: modelo LSTM de la práctica + LLM local (Ollama) + riesgo + motor Paper.")

    # ---------------------------------------------------------------- autenticación
    def autenticado(request: Request) -> str:
        clave = request.headers.get("x-api-key")
        if clave is not None:
            if hmac.compare_digest(clave.encode(), aj.backend_api_key.encode()):
                return "api_key"
            request.app.state.db.evento("ALERTA", "seguridad", "clave_incorrecta",
                                        f"Acceso rechazado a {request.url.path}: API key incorrecta")
            raise HTTPException(401, "API key incorrecta")
        token = request.cookies.get("sesion")
        if token and SESIONES.get(token, 0) > time.time():
            return "sesion"
        raise HTTPException(401, "No autenticado: inicia sesión")

    @app.post("/api/login", tags=["sesión"])
    async def login(datos: Login, request: Request, response: Response):
        if not hmac.compare_digest(datos.password.encode(), aj.admin_password.encode()):
            request.app.state.db.evento("ALERTA", "seguridad", "clave_incorrecta", "Intento de inicio de sesión con contraseña incorrecta")
            await asyncio.sleep(0.5)
            raise HTTPException(401, "Contraseña incorrecta")
        token = secrets.token_urlsafe(32)
        SESIONES[token] = time.time() + DURACION_SESION
        response.set_cookie("sesion", token, httponly=True, samesite="strict", max_age=DURACION_SESION)
        return {"ok": True}

    @app.post("/api/logout", tags=["sesión"])
    async def logout(request: Request, response: Response):
        SESIONES.pop(request.cookies.get("sesion", ""), None)
        response.delete_cookie("sesion")
        return {"ok": True}

    @app.get("/api/ping", tags=["salud"])
    async def ping():
        return {"ok": True, "trading_real": TRADING_REAL_HABILITADO}

    # ---------------------------------------------------------------- lectura
    @app.get("/api/estado", tags=["agente"], dependencies=[Depends(autenticado)])
    async def estado(request: Request):
        s = request.app.state
        ag: Agente = s.agente
        capital = s.riesgo.capital_inicial
        cuentas = {}
        for e in ESTRATEGIAS:
            filas = s.db.filas("SELECT * FROM patrimonio WHERE modo='vivo' AND estrategia=? ORDER BY fecha_vela DESC LIMIT 1", (e,))
            cuentas[e] = {"efectivo": s.agente.paper.efectivo("vivo", e),
                          "patrimonio": filas[0]["patrimonio"] if filas else capital,
                          "posiciones": list(s.agente.paper.posiciones("vivo", e).values())}
        return {"estado": ag.estado, "motivo": s.db.get("motivo_estado", ""), "modo": "PAPER", "trading_real": TRADING_REAL_HABILITADO,
                "ultima_vela_procesada": ag.ultima_procesada("vivo"), "ultimo_tick": ag.ultimo_tick,
                "duracion_ultimo_ciclo_s": ag.ultima_duracion_ciclo_s, "poll_segundos": s.aj.poll_segundos,
                "modelo": {"version": s.modelo.version, "huella": s.modelo.huella, "regla": s.modelo.regla,
                           "dif_vs_keras": s.modelo.diferencia_maxima_vs_keras, "run_id": s.modelo.contrato.get("run_id")},
                "llm": {"modelo": s.aj.ollama_modelo}, "riesgo": s.riesgo.como_dict(), "cuentas": cuentas,
                "replay": ag.replay}

    @app.get("/api/pares", tags=["agente"], dependencies=[Depends(autenticado)])
    async def pares(request: Request, modo: str = "vivo"):
        s = request.app.state
        fecha = s.agente.ultima_procesada(modo)
        filas = s.db.filas("SELECT * FROM decisiones WHERE modo=? AND fecha_vela=? ORDER BY simbolo", (modo, fecha)) if fecha else []
        pos = s.agente.paper.posiciones(modo, "agente_llm")
        for f in filas:
            p = pos.get(f["simbolo"])
            f["posicion"] = p
        return {"fecha_vela": fecha, "pares": filas}

    @app.get("/api/decisiones", tags=["registro"], dependencies=[Depends(autenticado)])
    async def decisiones(request: Request, modo: str = "vivo", limite: int = 300):
        return request.app.state.db.filas("SELECT * FROM decisiones WHERE modo=? ORDER BY fecha_vela DESC, simbolo LIMIT ?",
                                          (modo, min(limite, 2000)))

    @app.get("/api/ordenes", tags=["registro"], dependencies=[Depends(autenticado)])
    async def ordenes(request: Request, modo: str = "vivo", limite: int = 300):
        return request.app.state.db.filas("SELECT * FROM ordenes WHERE modo=? ORDER BY id DESC LIMIT ?", (modo, min(limite, 2000)))

    @app.get("/api/eventos", tags=["registro"], dependencies=[Depends(autenticado)])
    async def eventos(request: Request, limite: int = 200):
        return request.app.state.db.filas("SELECT * FROM eventos ORDER BY id DESC LIMIT ?", (min(limite, 2000),))

    @app.get("/api/ciclos", tags=["registro"], dependencies=[Depends(autenticado)])
    async def ciclos(request: Request, modo: str = "vivo"):
        return request.app.state.db.filas("SELECT * FROM ciclos WHERE modo=? ORDER BY fecha_vela DESC", (modo,))

    @app.get("/api/patrimonio", tags=["registro"], dependencies=[Depends(autenticado)])
    async def patrimonio(request: Request, modo: str = "vivo"):
        return request.app.state.db.filas("SELECT estrategia, fecha_vela, patrimonio FROM patrimonio WHERE modo=? ORDER BY fecha_vela",
                                          (modo,))

    @app.get("/api/metricas", tags=["registro"], dependencies=[Depends(autenticado)])
    async def ver_metricas(request: Request, modo: str = "vivo"):
        s = request.app.state
        return metricas.comparacion(s.db, modo, s.riesgo.capital_inicial)

    @app.get("/api/salud", tags=["salud"], dependencies=[Depends(autenticado)])
    async def salud(request: Request):
        s = request.app.state
        out: dict = {"trading_real": TRADING_REAL_HABILITADO}
        try:
            out["mexc"] = {"ok": True, "latencia_ms": round(await s.mexc.ping(), 1)}
        except MexcError as e:
            out["mexc"] = {"ok": False, "error": str(e)}
        try:
            async with httpx.AsyncClient(timeout=6) as c:
                r = await c.get(f"{s.aj.llm_gateway_url}/salud", headers={"X-API-Key": s.aj.llm_gateway_key})
            out["llm"] = {"ok": r.status_code == 200, **(r.json() if r.status_code == 200 else {"error": r.text[:200]})}
        except (httpx.TransportError, httpx.TimeoutException, ValueError) as e:
            out["llm"] = {"ok": False, "error": f"Gateway/Ollama sin respuesta ({type(e).__name__})"}
        try:
            import psutil
            p = psutil.Process()
            out["sistema"] = {"memoria_backend_mb": round(p.memory_info().rss / 2**20, 1),
                              "memoria_libre_mb": round(psutil.virtual_memory().available / 2**20),
                              "cpu_pct": psutil.cpu_percent(interval=None)}
        except ImportError:
            out["sistema"] = {}
        out["ultimo_tick"] = s.agente.ultimo_tick
        return out

    # ---------------------------------------------------------------- cerebro, red neuronal y feeds
    async def _salud_cache(request: Request) -> dict:
        s = request.app.state
        c = getattr(s, "salud_cache", None)
        if c and time.time() - c["t"] < 15:
            return c["d"]
        d: dict = {}
        try:
            d["mexc"] = {"ok": True, "latencia_ms": round(await s.mexc.ping(), 1)}
        except MexcError as e:
            d["mexc"] = {"ok": False, "error": str(e)}
        try:
            async with httpx.AsyncClient(timeout=5) as c2:
                r = await c2.get(f"{s.aj.llm_gateway_url}/salud", headers={"X-API-Key": s.aj.llm_gateway_key})
            d["llm"] = {"ok": r.status_code == 200, **(r.json() if r.status_code == 200 else {})}
        except (httpx.TransportError, httpx.TimeoutException, ValueError):
            d["llm"] = {"ok": False}
        s.salud_cache = {"t": time.time(), "d": d}
        return d

    @app.get("/api/cerebro", tags=["agente"], dependencies=[Depends(autenticado)])
    async def cerebro(request: Request):
        s = request.app.state
        ag: Agente = s.agente
        fecha = ag.ultima_procesada("vivo")
        ciclo = s.db.fila("SELECT * FROM ciclos WHERE modo='vivo' ORDER BY fecha_vela DESC LIMIT 1")
        cnt = s.db.fila("SELECT COUNT(*) n, SUM(accion_final='COMPRAR') compras, SUM(accion_final='VENDER') ventas, "
                        "SUM(accion_final='MANTENER') mantener, SUM(aprobada=0) rechazos, SUM(json_valido=0) json_invalidos, "
                        "SUM(herramientas IS NOT NULL AND herramientas<>'') con_herramientas, AVG(latencia_llm_ms) lat "
                        "FROM decisiones WHERE modo='vivo' AND fecha_vela=?", (fecha,)) if fecha else {}
        feeds = s.db.get("feeds_vivo", {})
        return {"estado": ag.estado, "motivo": s.db.get("motivo_estado", ""), "ultima_vela": fecha, "ultimo_tick": ag.ultimo_tick,
                "salud": await _salud_cache(request), "ciclo": ciclo, "conteos": cnt or {},
                "feeds_ok": sum(1 for f in feeds.values() if f.get("estado") == "ok"), "feeds_total": len(s.riesgo.pares_permitidos),
                "replay": ag.replay.get("estado"), "modelo": s.modelo.version, "llm_modelo": s.aj.ollama_modelo,
                "eventos": s.db.filas("SELECT ts_utc, nivel, etapa, tipo, mensaje FROM eventos ORDER BY id DESC LIMIT 8")}

    @app.get("/api/red", tags=["agente"], dependencies=[Depends(autenticado)])
    async def red(request: Request, simbolo: str = "BTCUSDT"):
        s = request.app.state
        if simbolo not in s.riesgo.pares_permitidos:
            raise HTTPException(400, "Símbolo no permitido")
        d = s.db.fila("SELECT fecha_vela, entradas, accion_final, motivo FROM decisiones WHERE modo='vivo' AND simbolo=? "
                      "AND entradas IS NOT NULL ORDER BY fecha_vela DESC LIMIT 1", (simbolo,))
        if d:
            e = json.loads(d["entradas"])
            v = Variables(simbolo, d["fecha_vela"], e["seq"], e["dist_sma20"], e["vol20"], e["cierre"], e["sma20"])
            fuente = "última decisión registrada"
        else:
            try:
                v = calcular(simbolo, await s.mexc.velas_cerradas(simbolo, limite=40))
            except (MexcError, DatosInvalidos) as e:
                raise HTTPException(503, f"No hay datos para {simbolo}: {e}")
            fuente = "MEXC en vivo (aún sin decisiones)"
        out = s.modelo.activaciones(v)
        out.update(fuente=fuente, delta=s.modelo.delta if hasattr(s.modelo, "delta") else s.modelo.regla["delta"],
                   decision=d and {"accion_final": d["accion_final"], "motivo": d["motivo"]})
        return out

    @app.get("/api/feeds", tags=["salud"], dependencies=[Depends(autenticado)])
    async def ver_feeds(request: Request, verificar: bool = False):
        s = request.app.state
        if not verificar:
            return s.db.get("feeds_vivo", {})
        ahora = int(time.time() * 1000)
        out = {}
        for sym in s.riesgo.pares_permitidos:
            t0 = time.perf_counter()
            try:
                velas = await s.mexc.velas_cerradas(sym, limite=40, ahora_ms=ahora)
                validar_velas(sym, velas)
                u = velas[-1]
                ant = (ahora - u.close_time) / 3_600_000
                out[sym] = {"velas": len(velas), "ultima_vela": u.fecha, "antiguedad_h": round(ant, 2), "revisado_utc": ahora_iso(),
                            "estado": "ok" if ant <= s.riesgo.antiguedad_max_horas else "atrasado", "mensaje": "",
                            "latencia_ms": round((time.perf_counter() - t0) * 1000, 1)}
            except (MexcError, DatosInvalidos) as e:
                out[sym] = {"estado": "error", "mensaje": str(e), "revisado_utc": ahora_iso()}
        return out

    # ---------------------------------------------------------------- control (protegido)
    @app.post("/api/agente/{accion}", tags=["control"], dependencies=[Depends(autenticado)])
    async def control(accion: str, request: Request):
        ag: Agente = request.app.state.agente
        if accion == "iniciar":
            await ag.iniciar()
        elif accion == "pausar":
            await ag.pausar()
        elif accion == "reanudar":
            await ag.reanudar()
        elif accion == "detener":
            await ag.detener()
        elif accion == "liquidar":
            try:
                return {"liquidadas": await ag.liquidar()}
            except (RuntimeError, MexcError) as e:
                raise HTTPException(409, str(e))
        elif accion == "ciclo-ahora":
            if ag.estado != "ACTIVO":
                raise HTTPException(409, "El agente debe estar ACTIVO para procesar una vela")
            try:
                r = await ag.revisar_nueva_vela(forzar=True)
            except MexcError as e:
                raise HTTPException(503, f"MEXC: {e}")
            return {"procesada": r.fecha_vela if r else None, "mensaje": "Sin vela nueva: ya fue procesada" if r is None else "Ciclo completo"}
        else:
            raise HTTPException(404, "Acción desconocida")
        return {"estado": ag.estado}

    @app.post("/api/replay", tags=["control"], dependencies=[Depends(autenticado)])
    async def replay(p: PedidoReplay, request: Request):
        try:
            request.app.state.agente.lanzar_replay(p.dias, p.usar_llm)
        except (RuntimeError, ValueError) as e:
            raise HTTPException(409, str(e))
        return {"ok": True, "aviso": "REPLAY: resultados separados de la operación en vivo"}

    @app.get("/api/config/riesgo", tags=["configuración"], dependencies=[Depends(autenticado)])
    async def ver_riesgo(request: Request):
        return request.app.state.riesgo.como_dict()

    @app.put("/api/config/riesgo", tags=["configuración"], dependencies=[Depends(autenticado)])
    async def cambiar_riesgo(cambios: dict, request: Request):
        s = request.app.state
        if s.agente.estado == "ACTIVO":
            raise HTTPException(409, "Detén o pausa el agente antes de cambiar límites")
        actual = json.loads(s.aj.riesgo_path.read_text(encoding="utf-8"))
        permitidas = {"exposicion_max_por_par", "exposicion_max_total", "perdida_diaria_max", "antiguedad_max_horas", "deslizamiento"}
        if set(cambios) - permitidas:
            raise HTTPException(400, f"Solo se pueden cambiar: {sorted(permitidas)}")
        actual.update(cambios)
        tmp = s.aj.riesgo_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(actual, indent=2, ensure_ascii=False), encoding="utf-8")
        try:
            nuevo = Riesgo.cargar(tmp)
        except Exception as e:
            tmp.unlink(missing_ok=True)
            raise HTTPException(400, f"Configuración inválida: {e}")
        tmp.replace(s.aj.riesgo_path)
        s.riesgo = nuevo
        s.agente.r = nuevo
        s.agente.paper.riesgo = nuevo
        s.db.evento("INFO", "configuracion", "riesgo", "Límites de riesgo cambiados por el usuario", cambios)
        return nuevo.como_dict()

    # ---------------------------------------------------------------- interfaz compilada
    dist = aj.frontend_dist
    if dist.exists():
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

        @app.get("/{ruta:path}", include_in_schema=False)
        async def spa(ruta: str):
            if ruta.startswith("api/"):
                raise HTTPException(404)
            return FileResponse(dist / "index.html")

    return app


app = None
if os.getenv("AGENTE_NO_AUTOCREAR") != "1":
    try:
        app = crear_app()
    except Exception as e:                       # mensaje claro si falta .env o el modelo
        logging.getLogger("api").error("No se pudo iniciar el backend: %s", e)
        raise
