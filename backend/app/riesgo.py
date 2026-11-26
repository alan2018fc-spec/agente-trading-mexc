"""Motor de riesgo DETERMINISTA. Revisa toda propuesta (del LLM o de la regla) antes de ejecutarla en Paper.
La propuesta del LLM nunca sustituye estas reglas."""
from __future__ import annotations

from dataclasses import dataclass

from .config import Riesgo


@dataclass
class Revision:
    aprobada: bool
    motivo: str
    monto: float = 0.0          # USDT a comprar (solo COMPRAR aprobada)


@dataclass
class ContextoRiesgo:
    agente_activo: bool
    simbolo: str
    accion: str
    fecha_vela: str
    fecha_esperada: str
    antiguedad_horas: float
    tiene_posicion: bool
    efectivo: float
    patrimonio_inicio_ciclo: float
    valor_posiciones: float
    monto_minimo: float
    bloqueo_perdida_diaria: bool


def revisar(r: Riesgo, c: ContextoRiesgo) -> Revision:
    if c.simbolo not in r.pares_permitidos:
        return Revision(False, f"Símbolo no permitido: {c.simbolo}")
    if c.accion not in r.acciones_permitidas:
        return Revision(False, f"Acción no permitida: {c.accion}")
    if c.fecha_vela != c.fecha_esperada:
        return Revision(False, f"La propuesta es para la vela {c.fecha_vela}, se esperaba {c.fecha_esperada}")
    if c.antiguedad_horas > r.antiguedad_max_horas:
        return Revision(False, f"Dato atrasado: {c.antiguedad_horas:.1f} h > {r.antiguedad_max_horas:.0f} h")
    if c.accion == "MANTENER":
        return Revision(True, "Mantener: sin orden")
    if not c.agente_activo:
        return Revision(False, "Agente detenido o pausado: nuevas órdenes bloqueadas")
    if c.bloqueo_perdida_diaria:
        return Revision(False, f"Límite de pérdida diaria ({r.perdida_diaria_max:.0%}) alcanzado: órdenes bloqueadas")
    if c.accion == "VENDER":
        if not c.tiene_posicion:
            return Revision(False, "Vender sin posición no está permitido (sin ventas en corto)")
        return Revision(True, "Venta de posición existente")
    # COMPRAR
    if c.tiene_posicion:
        return Revision(False, "Ya hay posición: no se recompra una posición abierta")
    limite_par = r.exposicion_max_por_par * c.patrimonio_inicio_ciclo
    limite_total = r.exposicion_max_total * c.patrimonio_inicio_ciclo - c.valor_posiciones
    monto = min(limite_par, c.efectivo, limite_total)
    if c.efectivo < c.monto_minimo:
        return Revision(False, f"Saldo insuficiente: {c.efectivo:.2f} USDT < mínimo {c.monto_minimo:.2f}")
    if monto < c.monto_minimo:
        return Revision(False, f"Exposición máxima alcanzada: disponible {max(monto, 0):.2f} USDT")
    return Revision(True, f"Compra por {monto:.2f} USDT (≤ {r.exposicion_max_por_par:.2%} del patrimonio)", monto)
