"""Persistencia SQLite: velas procesadas, decisiones, órdenes Paper, patrimonio, errores y estado.

Las restricciones UNIQUE garantizan idempotencia: la misma vela nunca se procesa dos veces
y una orden nunca se registra dos veces, aunque el evento llegue repetido o el backend se reinicie.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ESQUEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS estado (clave TEXT PRIMARY KEY, valor TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS velas (
  modo TEXT NOT NULL, simbolo TEXT NOT NULL, fecha TEXT NOT NULL, open_time INTEGER NOT NULL,
  open REAL, high REAL, low REAL, close REAL, volume REAL, close_time INTEGER,
  PRIMARY KEY (modo, simbolo, fecha));
CREATE TABLE IF NOT EXISTS ciclos (
  id INTEGER PRIMARY KEY AUTOINCREMENT, modo TEXT NOT NULL, fecha_vela TEXT NOT NULL,
  inicio_utc TEXT NOT NULL, fin_utc TEXT, estado TEXT NOT NULL, n_decisiones INTEGER DEFAULT 0,
  n_rechazos INTEGER DEFAULT 0, latencia_llm_ms_prom REAL, nota TEXT,
  UNIQUE (modo, fecha_vela));
CREATE TABLE IF NOT EXISTS decisiones (
  id INTEGER PRIMARY KEY AUTOINCREMENT, modo TEXT NOT NULL, ciclo_id INTEGER, simbolo TEXT NOT NULL,
  fecha_vela TEXT NOT NULL, ts_utc TEXT NOT NULL, cierre REAL, pronostico REAL, dist_sma20 REAL, vol20 REAL,
  sma20 REAL, accion_modelo TEXT, accion_llm TEXT, justificacion TEXT, herramientas TEXT, json_valido INTEGER,
  accion_final TEXT, aprobada INTEGER, motivo TEXT, latencia_llm_ms REAL, version_modelo TEXT,
  UNIQUE (modo, simbolo, fecha_vela));
CREATE TABLE IF NOT EXISTS ordenes (
  id INTEGER PRIMARY KEY AUTOINCREMENT, modo TEXT NOT NULL, estrategia TEXT NOT NULL, simbolo TEXT NOT NULL,
  fecha_vela TEXT NOT NULL, lado TEXT NOT NULL, ts_utc TEXT NOT NULL, cantidad REAL NOT NULL, precio REAL NOT NULL,
  monto REAL NOT NULL, costo REAL NOT NULL, efectivo_despues REAL NOT NULL, precio_referencia TEXT,
  UNIQUE (modo, estrategia, simbolo, fecha_vela, lado));
CREATE TABLE IF NOT EXISTS cuentas (
  modo TEXT NOT NULL, estrategia TEXT NOT NULL, efectivo REAL NOT NULL, capital_inicial REAL NOT NULL,
  PRIMARY KEY (modo, estrategia));
CREATE TABLE IF NOT EXISTS posiciones (
  modo TEXT NOT NULL, estrategia TEXT NOT NULL, simbolo TEXT NOT NULL, cantidad REAL NOT NULL,
  precio_entrada REAL NOT NULL, costo_entrada REAL NOT NULL, fecha_entrada TEXT NOT NULL,
  PRIMARY KEY (modo, estrategia, simbolo));
CREATE TABLE IF NOT EXISTS patrimonio (
  modo TEXT NOT NULL, estrategia TEXT NOT NULL, fecha_vela TEXT NOT NULL, ts_utc TEXT NOT NULL,
  efectivo REAL NOT NULL, posiciones REAL NOT NULL, patrimonio REAL NOT NULL,
  PRIMARY KEY (modo, estrategia, fecha_vela));
CREATE TABLE IF NOT EXISTS notificaciones (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts_utc TEXT NOT NULL, modo TEXT NOT NULL, simbolo TEXT NOT NULL,
  fecha_vela TEXT NOT NULL, canal TEXT NOT NULL, estado TEXT NOT NULL, detalle TEXT, mensaje TEXT,
  UNIQUE (modo, simbolo, fecha_vela, canal));
CREATE TABLE IF NOT EXISTS eventos (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts_utc TEXT NOT NULL, nivel TEXT NOT NULL, etapa TEXT NOT NULL,
  tipo TEXT NOT NULL, mensaje TEXT NOT NULL, detalle TEXT);
"""


def ahora_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class DB:
    def __init__(self, ruta: Path | str):
        ruta = Path(ruta)
        if str(ruta) != ":memory:":
            ruta.parent.mkdir(parents=True, exist_ok=True)
        self.con = sqlite3.connect(str(ruta), check_same_thread=False, isolation_level=None)
        self.con.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        self.con.executescript(ESQUEMA)
        try:                                   # migración: entradas del modelo por decisión (para la vista de la red)
            self.con.execute("ALTER TABLE decisiones ADD COLUMN entradas TEXT")
        except sqlite3.OperationalError:
            pass

    # utilidades -----------------------------------------------------------------------
    def ej(self, sql: str, params: tuple | dict = ()) -> sqlite3.Cursor:
        with self.lock:
            return self.con.execute(sql, params)

    def filas(self, sql: str, params: tuple | dict = ()) -> list[dict]:
        with self.lock:
            return [dict(r) for r in self.con.execute(sql, params).fetchall()]

    def fila(self, sql: str, params: tuple | dict = ()) -> dict | None:
        r = self.filas(sql, params)
        return r[0] if r else None

    def transaccion(self):
        db = self

        class _T:
            def __enter__(self):
                db.lock.acquire()
                db.con.execute("BEGIN IMMEDIATE")
                return db

            def __exit__(self, tipo, val, tb):
                try:
                    db.con.execute("ROLLBACK" if tipo else "COMMIT")
                finally:
                    db.lock.release()
                return False
        return _T()

    # estado clave-valor ---------------------------------------------------------------------
    def get(self, clave: str, defecto: Any = None) -> Any:
        r = self.fila("SELECT valor FROM estado WHERE clave=?", (clave,))
        return json.loads(r["valor"]) if r else defecto

    def set(self, clave: str, valor: Any) -> None:
        self.ej("INSERT INTO estado(clave, valor) VALUES(?, ?) ON CONFLICT(clave) DO UPDATE SET valor=excluded.valor",
                (clave, json.dumps(valor, ensure_ascii=False)))

    # eventos / bitácora -------------------------------------------------------------------
    def evento(self, nivel: str, etapa: str, tipo: str, mensaje: str, detalle: Any = None) -> None:
        self.ej("INSERT INTO eventos(ts_utc, nivel, etapa, tipo, mensaje, detalle) VALUES(?,?,?,?,?,?)",
                (ahora_iso(), nivel, etapa, tipo, mensaje[:1000],
                 None if detalle is None else json.dumps(detalle, ensure_ascii=False, default=str)[:4000]))
