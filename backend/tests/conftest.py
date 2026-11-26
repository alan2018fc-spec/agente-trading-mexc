"""Simuladores de MEXC y del gateway LLM para probar el agente sin red ni Ollama."""
from __future__ import annotations

import asyncio
import json
import math
import os
import sys
import time
from pathlib import Path

import httpx
import pytest

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
os.environ.setdefault("BACKEND_API_KEY", "clave-backend-de-prueba-123456")
os.environ.setdefault("ADMIN_PASSWORD", "contrasena-de-prueba-123")
os.environ.setdefault("LLM_GATEWAY_KEY", "clave-gateway-de-prueba-123456")
os.environ["AGENTE_NO_AUTOCREAR"] = "1"

from app.ciclo import Agente                      # noqa: E402
from app.config import PARES, Ajustes, Riesgo    # noqa: E402
from app.db import DB                             # noqa: E402
from app.llm import ClienteLLM                    # noqa: E402
from app.mexc import ClienteMEXC                  # noqa: E402
from app.modelo import ModeloLSTM                 # noqa: E402

DIA = 86_400_000


class FalsoMEXC:
    """Velas diarias deterministas hasta HOY (la de hoy sigue abierta y debe descartarse)."""
    def __init__(self):
        self.modo = "ok"                    # ok | limite | caido
        self.atrasado: set[str] = set()     # pares cuya última vela es de hace 3 días
        self.llamadas = 0
        hoy = int(time.time() * 1000) // DIA * DIA
        self.dias = [hoy - k * DIA for k in range(120, -1, -1)]

    def precio(self, s: str, t: int) -> float:
        i = (t // DIA) % 997
        base = 10 + PARES.index(s) * 7 if s in PARES else 10
        return base * (1 + 0.25 * math.sin(i / 6 + PARES.index(s)) + 0.03 * math.sin(i * 1.7))

    def __call__(self, req: httpx.Request) -> httpx.Response:
        self.llamadas += 1
        if self.modo == "caido":
            raise httpx.ConnectTimeout("sin respuesta", request=req)
        if self.modo == "limite":
            return httpx.Response(429, text="Too many requests")
        q = dict(req.url.params)
        if req.url.path == "/api/v3/ping":
            return httpx.Response(200, json={})
        if req.url.path == "/api/v3/exchangeInfo":
            return httpx.Response(200, json={"symbols": [{"symbol": q["symbol"], "baseAssetPrecision": 8, "quotePrecision": 4,
                                                          "baseSizePrecision": "0.0001", "quoteAmountPrecision": "1"}]})
        if req.url.path == "/api/v3/ticker/bookTicker":
            p = self.precio(q["symbol"], self.dias[-1])
            return httpx.Response(200, json={"symbol": q["symbol"], "bidPrice": str(p * 0.9999), "askPrice": str(p * 1.0001)})
        if req.url.path == "/api/v3/klines":
            s = q["symbol"]
            dias = self.dias[:-3] if s in self.atrasado else self.dias
            ini, fin = int(q.get("startTime", 0)), int(q.get("endTime", 10**15))
            sel = [t for t in dias if ini <= t < fin]
            if "startTime" not in q:
                sel = sel[-int(q.get("limit", 500)):]
            sel = sel[:500]
            filas = []
            for t in sel:
                c, o = self.precio(s, t), self.precio(s, t - DIA)
                filas.append([t, str(o), str(max(o, c) * 1.01), str(min(o, c) * 0.99), str(c), "1000", t + DIA, "1"])
            return httpx.Response(200, json=filas)
        return httpx.Response(404)


class FalsoLLM:
    """Gateway simulado. modo: ok | json_invalido | caido | clave | otro_simbolo | contrario."""
    def __init__(self):
        self.modo = "ok"
        self.llamadas = 0
        self.con_herramientas = 0

    def __call__(self, req: httpx.Request) -> httpx.Response:
        self.llamadas += 1
        if self.modo == "caido":
            raise httpx.ConnectError("Ollama detenido", request=req)
        if self.modo == "clave":
            return httpx.Response(401, json={"detail": "API key ausente o incorrecta"})
        cuerpo = json.loads(req.content)
        datos = json.loads(cuerpo["messages"][1]["content"].split("\n", 1)[1])
        if "tools" in cuerpo:
            self.con_herramientas += 1
            return httpx.Response(200, json={"message": {"role": "assistant", "content": "", "tool_calls": [
                {"function": {"name": "consultar_posicion", "arguments": {"simbolo": datos["simbolo"]}}}]}})
        if self.modo == "json_invalido":
            return httpx.Response(200, json={"message": {"role": "assistant", "content": "Compraría, sin duda {no es json"}})
        accion = datos["accion_sugerida_por_regla"]
        if self.modo == "contrario":
            accion = "VENDER" if not datos["posicion"]["tiene_posicion"] else "COMPRAR"
        sim = "FAKEUSDT" if self.modo == "otro_simbolo" else datos["simbolo"]
        txt = json.dumps({"accion": accion, "simbolo": sim, "fecha_vela": datos["fecha_vela"],
                          "justificacion": f"Regla: pronóstico {datos['pronostico_pct']:.3f} % vs delta {datos['delta_pct']:.2f} %"})
        return httpx.Response(200, json={"message": {"role": "assistant", "content": txt}})


@pytest.fixture
def entorno(tmp_path):
    aj = Ajustes(backend_api_key=os.environ["BACKEND_API_KEY"], admin_password=os.environ["ADMIN_PASSWORD"],
                 llm_gateway_key=os.environ["LLM_GATEWAY_KEY"], db_path=tmp_path / "agente.db", poll_segundos=1)
    fm, fl = FalsoMEXC(), FalsoLLM()
    riesgo = Riesgo.cargar(aj.riesgo_path)
    llm_cfg = json.loads(aj.llm_path.read_text(encoding="utf-8"))

    def construir(db_path=None):
        db = DB(db_path or aj.db_path)
        mexc = ClienteMEXC(transporte=httpx.MockTransport(fm), espera_inicial=0.01)
        llm = ClienteLLM("http://gateway", aj.llm_gateway_key, aj.ollama_modelo, espera_inicial=0.01,
                         transporte=httpx.MockTransport(fl))
        return Agente(aj, riesgo, db, mexc, llm, ModeloLSTM(aj.modelo_dir), llm_cfg)

    return {"aj": aj, "mexc": fm, "llm": fl, "riesgo": riesgo, "construir": construir}


def correr(coro):
    return asyncio.run(coro)
