"""Configuración del backend. Los secretos solo viven en el servidor (.env), nunca en React."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

BASE = Path(__file__).resolve().parent.parent          # carpeta backend/
load_dotenv(BASE / ".env")

PARES = ["BTCUSDT", "ETHUSDT", "XRPUSDT", "BNBUSDT", "SOLUSDT", "ADAUSDT", "DOGEUSDT", "LTCUSDT",
         "TRXUSDT", "LINKUSDT", "DOTUSDT", "BCHUSDT", "XLMUSDT", "ATOMUSDT", "ETCUSDT"]
ACCIONES = ("COMPRAR", "MANTENER", "VENDER")

# Este proyecto NUNCA envía órdenes reales. No existe código firmado ni rutas de trading real.
TRADING_REAL_HABILITADO = False


class ConfigError(RuntimeError):
    pass


def _secreto(nombre: str) -> str:
    v = os.getenv(nombre, "").strip()
    if len(v) < 16:
        raise ConfigError(f"Falta {nombre} en backend/.env (mínimo 16 caracteres). Copia .env.example a .env y "
                          f"ejecuta scripts/generar_claves.ps1.")
    return v


@dataclass
class Ajustes:
    backend_api_key: str
    admin_password: str
    llm_gateway_key: str
    llm_gateway_url: str = "http://127.0.0.1:8001"
    ollama_url: str = "http://127.0.0.1:11434"
    ollama_modelo: str = "qwen2.5:3b"
    mexc_url: str = "https://api.mexc.com"
    poll_segundos: int = 60
    db_path: Path = BASE / "data" / "agente.db"
    modelo_dir: Path = BASE / "modelo"
    riesgo_path: Path = BASE / "config" / "riesgo.json"
    llm_path: Path = BASE / "config" / "llm.json"
    frontend_dist: Path = BASE.parent / "frontend" / "dist"
    extra: dict = field(default_factory=dict)

    @classmethod
    def desde_entorno(cls) -> "Ajustes":
        if os.getenv("TRADING_REAL", "").lower() in ("1", "true", "si", "sí"):
            raise ConfigError("TRADING_REAL no está permitido: el proyecto solo opera en modo Paper.")
        return cls(
            backend_api_key=_secreto("BACKEND_API_KEY"),
            admin_password=_secreto("ADMIN_PASSWORD"),
            llm_gateway_key=_secreto("LLM_GATEWAY_KEY"),
            llm_gateway_url=os.getenv("LLM_GATEWAY_URL", "http://127.0.0.1:8001"),
            ollama_url=os.getenv("OLLAMA_URL", "http://127.0.0.1:11434"),
            ollama_modelo=os.getenv("OLLAMA_MODELO", "qwen2.5:3b"),
            mexc_url=os.getenv("MEXC_URL", "https://api.mexc.com"),
            poll_segundos=int(os.getenv("POLL_SEGUNDOS", "60")),
            db_path=Path(os.getenv("DB_PATH", str(BASE / "data" / "agente.db"))),
        )


@dataclass(frozen=True)
class Riesgo:
    """Límites deterministas. El LLM no puede leerlos para cambiarlos ni modificarlos."""
    capital_inicial: float
    costo_por_lado: float
    deslizamiento: float
    exposicion_max_por_par: float
    exposicion_max_total: float
    perdida_diaria_max: float
    antiguedad_max_horas: float
    max_errores_consecutivos: int
    pares_permitidos: tuple[str, ...]
    acciones_permitidas: tuple[str, ...]

    @classmethod
    def cargar(cls, ruta: Path) -> "Riesgo":
        d = json.loads(Path(ruta).read_text(encoding="utf-8"))
        r = cls(capital_inicial=float(d["capital_inicial"]), costo_por_lado=float(d["costo_por_lado"]),
                deslizamiento=float(d["deslizamiento"]), exposicion_max_por_par=float(d["exposicion_max_por_par"]),
                exposicion_max_total=float(d["exposicion_max_total"]), perdida_diaria_max=float(d["perdida_diaria_max"]),
                antiguedad_max_horas=float(d["antiguedad_max_horas"]),
                max_errores_consecutivos=int(d["max_errores_consecutivos"]),
                pares_permitidos=tuple(d["pares_permitidos"]), acciones_permitidas=tuple(d["acciones_permitidas"]))
        r.validar()
        return r

    def validar(self) -> None:
        if self.capital_inicial <= 0:
            raise ConfigError("capital_inicial debe ser positivo")
        if not 0 <= self.costo_por_lado < 0.05 or not 0 <= self.deslizamiento < 0.05:
            raise ConfigError("costo o deslizamiento fuera de rango")
        if not 0 < self.exposicion_max_por_par <= 1 or not 0 < self.exposicion_max_total <= 1:
            raise ConfigError("exposición fuera de (0, 1]: sin apalancamiento")
        if not 0 < self.perdida_diaria_max < 1 or self.antiguedad_max_horas <= 0:
            raise ConfigError("pérdida diaria o antigüedad fuera de rango")
        if set(self.pares_permitidos) - set(PARES):
            raise ConfigError("pares fuera del contrato del modelo")
        if set(self.acciones_permitidas) - set(ACCIONES):
            raise ConfigError("acciones no permitidas en la configuración")

    def como_dict(self) -> dict:
        return {k: (list(v) if isinstance(v, tuple) else v) for k, v in self.__dict__.items()}
