"""Motor Paper: compras y ventas SIMULADAS con dinero virtual. No existe ninguna ruta a órdenes reales."""
from __future__ import annotations

import math
import sqlite3

from .config import Riesgo
from .db import DB, ahora_iso


class SaldoInsuficiente(RuntimeError):
    pass


class OrdenDuplicada(RuntimeError):
    pass


class OrdenInvalida(ValueError):
    pass


class Paper:
    def __init__(self, db: DB, riesgo: Riesgo):
        self.db = db
        self.riesgo = riesgo

    def asegurar_cuenta(self, modo: str, estrategia: str) -> None:
        self.db.ej("INSERT OR IGNORE INTO cuentas(modo, estrategia, efectivo, capital_inicial) VALUES(?,?,?,?)",
                   (modo, estrategia, self.riesgo.capital_inicial, self.riesgo.capital_inicial))

    def efectivo(self, modo: str, estrategia: str) -> float:
        self.asegurar_cuenta(modo, estrategia)
        return float(self.db.fila("SELECT efectivo FROM cuentas WHERE modo=? AND estrategia=?", (modo, estrategia))["efectivo"])

    def posiciones(self, modo: str, estrategia: str) -> dict[str, dict]:
        return {p["simbolo"]: p for p in self.db.filas(
            "SELECT * FROM posiciones WHERE modo=? AND estrategia=?", (modo, estrategia))}

    def valorar(self, modo: str, estrategia: str, precios: dict[str, float]) -> tuple[float, float, float]:
        ef = self.efectivo(modo, estrategia)
        pos = sum(p["cantidad"] * precios.get(s, p["precio_entrada"]) for s, p in self.posiciones(modo, estrategia).items())
        return ef, pos, ef + pos

    def comprar(self, modo: str, estrategia: str, simbolo: str, fecha_vela: str, monto: float, precio: float,
                paso: float, ref: str) -> dict:
        if not (precio > 0 and monto > 0 and paso > 0 and math.isfinite(precio)):
            raise OrdenInvalida(f"Orden inválida {simbolo}: precio={precio}, monto={monto}")
        c = self.riesgo.costo_por_lado
        cantidad = math.floor(monto / (precio * (1 + c)) / paso + 1e-9) * paso
        cantidad = float(f"{cantidad:.12g}")
        if cantidad <= 0:
            raise OrdenInvalida(f"{simbolo}: la cantidad redondeada al paso {paso} es 0")
        importe = cantidad * precio
        costo = importe * c
        with self.db.transaccion():
            self.asegurar_cuenta(modo, estrategia)
            ef = float(self.db.fila("SELECT efectivo FROM cuentas WHERE modo=? AND estrategia=?", (modo, estrategia))["efectivo"])
            if importe + costo > ef + 1e-9:
                raise SaldoInsuficiente(f"{simbolo}: se requieren {importe + costo:.2f} USDT y hay {ef:.2f}")
            if self.db.fila("SELECT 1 FROM posiciones WHERE modo=? AND estrategia=? AND simbolo=?", (modo, estrategia, simbolo)):
                raise OrdenInvalida(f"{simbolo}: ya existe posición")
            nuevo = ef - importe - costo
            try:
                self.db.ej("INSERT INTO ordenes(modo, estrategia, simbolo, fecha_vela, lado, ts_utc, cantidad, precio, monto, costo,"
                           " efectivo_despues, precio_referencia) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                           (modo, estrategia, simbolo, fecha_vela, "COMPRA", ahora_iso(), cantidad, precio, importe, costo, nuevo, ref))
            except sqlite3.IntegrityError as e:
                raise OrdenDuplicada(f"{simbolo} {fecha_vela}: compra ya registrada (evento duplicado)") from e
            self.db.ej("UPDATE cuentas SET efectivo=? WHERE modo=? AND estrategia=?", (nuevo, modo, estrategia))
            self.db.ej("INSERT INTO posiciones VALUES(?,?,?,?,?,?,?)",
                       (modo, estrategia, simbolo, cantidad, precio, importe + costo, fecha_vela))
        return {"lado": "COMPRA", "cantidad": cantidad, "precio": precio, "monto": importe, "costo": costo, "efectivo": nuevo}

    def vender(self, modo: str, estrategia: str, simbolo: str, fecha_vela: str, precio: float, ref: str) -> dict:
        if not (precio > 0 and math.isfinite(precio)):
            raise OrdenInvalida(f"Precio de venta inválido para {simbolo}: {precio}")
        with self.db.transaccion():
            p = self.db.fila("SELECT * FROM posiciones WHERE modo=? AND estrategia=? AND simbolo=?", (modo, estrategia, simbolo))
            if not p:
                raise OrdenInvalida(f"{simbolo}: no hay posición que vender (sin ventas en corto)")
            importe = p["cantidad"] * precio
            costo = importe * self.riesgo.costo_por_lado
            ef = float(self.db.fila("SELECT efectivo FROM cuentas WHERE modo=? AND estrategia=?", (modo, estrategia))["efectivo"])
            nuevo = ef + importe - costo
            try:
                self.db.ej("INSERT INTO ordenes(modo, estrategia, simbolo, fecha_vela, lado, ts_utc, cantidad, precio, monto, costo,"
                           " efectivo_despues, precio_referencia) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                           (modo, estrategia, simbolo, fecha_vela, "VENTA", ahora_iso(), p["cantidad"], precio, importe, costo, nuevo, ref))
            except sqlite3.IntegrityError as e:
                raise OrdenDuplicada(f"{simbolo} {fecha_vela}: venta ya registrada (evento duplicado)") from e
            self.db.ej("UPDATE cuentas SET efectivo=? WHERE modo=? AND estrategia=?", (nuevo, modo, estrategia))
            self.db.ej("DELETE FROM posiciones WHERE modo=? AND estrategia=? AND simbolo=?", (modo, estrategia, simbolo))
        return {"lado": "VENTA", "cantidad": p["cantidad"], "precio": precio, "monto": importe, "costo": costo,
                "efectivo": nuevo, "pnl": importe - costo - p["costo_entrada"]}

    def registrar_patrimonio(self, modo: str, estrategia: str, fecha_vela: str, precios: dict[str, float]) -> float:
        ef, pos, eq = self.valorar(modo, estrategia, precios)
        self.db.set(f"precios_valuacion_{modo}", {"fecha": fecha_vela, "precios": precios})   # para el PnL no realizado
        self.db.ej("INSERT INTO patrimonio VALUES(?,?,?,?,?,?,?) ON CONFLICT(modo, estrategia, fecha_vela) DO UPDATE SET "
                   "ts_utc=excluded.ts_utc, efectivo=excluded.efectivo, posiciones=excluded.posiciones, patrimonio=excluded.patrimonio",
                   (modo, estrategia, fecha_vela, ahora_iso(), ef, pos, eq))
        return eq

    def reiniciar_modo(self, modo: str) -> None:
        with self.db.transaccion():
            for t in ("ordenes", "cuentas", "posiciones", "patrimonio", "decisiones", "ciclos", "velas"):
                self.db.ej(f"DELETE FROM {t} WHERE modo=?", (modo,))
