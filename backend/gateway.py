"""Gateway privado para Ollama local.

Ollama no exige autenticación por defecto; este servicio agrega la capa de API key:
- Solo escucha en 127.0.0.1 y reenvía a Ollama local (sin inferencia en la nube).
- Rechaza con 401 las solicitudes sin clave o con clave incorrecta, y lo registra.
- Solo permite el modelo configurado.
Ejecutar:  python -m uvicorn gateway:app --host 127.0.0.1 --port 8001
"""
from __future__ import annotations

import hmac
import json
import logging
import os
import time
from pathlib import Path

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, Header, HTTPException, Request

BASE = Path(__file__).resolve().parent
load_dotenv(BASE / ".env")
CLAVE = os.getenv("LLM_GATEWAY_KEY", "")
OLLAMA = os.getenv("OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")
MODELO = os.getenv("OLLAMA_MODELO", "qwen2.5:3b")
if len(CLAVE) < 16:
    raise RuntimeError("Falta LLM_GATEWAY_KEY en backend/.env (mínimo 16 caracteres).")
if not OLLAMA.startswith(("http://127.0.0.1", "http://localhost")):
    raise RuntimeError("OLLAMA_URL debe ser local: el protocolo exige inferencia sin nube.")

(BASE / "logs").mkdir(exist_ok=True)
log = logging.getLogger("gateway")
_h = logging.FileHandler(BASE / "logs" / "gateway.log", encoding="utf-8")
_h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
log.addHandler(_h); log.setLevel(logging.INFO)

app = FastAPI(title="Gateway privado de Ollama", version="1.0")


def _verificar(clave: str | None) -> None:
    if not clave or not hmac.compare_digest(clave.encode(), CLAVE.encode()):
        log.warning("acceso rechazado: clave %s", "ausente" if not clave else "incorrecta")
        raise HTTPException(status_code=401, detail="API key ausente o incorrecta")


@app.get("/salud")
async def salud(x_api_key: str | None = Header(default=None)):
    _verificar(x_api_key)
    t0 = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=5) as c:
            ver = (await c.get(f"{OLLAMA}/api/version")).json()
            tags = (await c.get(f"{OLLAMA}/api/tags")).json()
    except (httpx.TransportError, httpx.TimeoutException, ValueError) as e:
        raise HTTPException(status_code=503, detail=f"Ollama no responde en {OLLAMA}: {type(e).__name__}")
    modelos = {m.get("name"): m for m in tags.get("models", [])}
    m = modelos.get(MODELO) or modelos.get(MODELO + ":latest") or {}
    return {"ollama_version": ver.get("version"), "url_local": OLLAMA, "modelo": MODELO, "modelo_descargado": bool(m),
            "tamano_bytes": m.get("size"), "cuantizacion": (m.get("details") or {}).get("quantization_level"),
            "parametros": (m.get("details") or {}).get("parameter_size"),
            "latencia_ms": round((time.perf_counter() - t0) * 1000, 1)}


@app.post("/api/chat")
async def chat(request: Request, x_api_key: str | None = Header(default=None)):
    _verificar(x_api_key)
    try:
        cuerpo = await request.json()
    except (json.JSONDecodeError, ValueError):
        raise HTTPException(status_code=400, detail="Cuerpo JSON inválido")
    if cuerpo.get("model") != MODELO:
        raise HTTPException(status_code=403, detail=f"Modelo no permitido; solo {MODELO}")
    cuerpo["stream"] = False
    t0 = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=180) as c:
            r = await c.post(f"{OLLAMA}/api/chat", json=cuerpo)
    except (httpx.TransportError, httpx.TimeoutException) as e:
        log.error("Ollama no responde: %s", type(e).__name__)
        raise HTTPException(status_code=503, detail="Ollama detenido o sin respuesta")
    ms = (time.perf_counter() - t0) * 1000
    log.info("chat modelo=%s estado=%s latencia_ms=%.0f herramientas=%s", MODELO, r.status_code, ms, "tools" in cuerpo)
    if r.status_code != 200:
        raise HTTPException(status_code=502, detail=f"Ollama respondió HTTP {r.status_code}")
    return r.json()
