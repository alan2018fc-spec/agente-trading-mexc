"""Cálculo de variables con la MISMA definición del entrenamiento (notebook v2.1)."""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone

from .modelo import Variables

DIA_MS = 86_400_000


@dataclass
class Vela:
    open_time: int      # ms UTC
    open: float
    high: float
    low: float
    close: float
    volume: float
    close_time: int     # ms UTC

    @property
    def fecha(self) -> str:
        return datetime.fromtimestamp(self.open_time / 1000, tz=timezone.utc).strftime("%Y-%m-%d")


class DatosInvalidos(ValueError):
    pass


def validar_velas(simbolo: str, velas: list[Vela], minimo: int = 21) -> None:
    """Orden, unicidad, continuidad diaria y precios válidos. Se detiene con un mensaje útil."""
    if len(velas) < minimo:
        raise DatosInvalidos(f"{simbolo}: {len(velas)} velas cerradas; se necesitan al menos {minimo}.")
    for a, b in zip(velas, velas[1:]):
        if b.open_time <= a.open_time:
            raise DatosInvalidos(f"{simbolo}: fechas duplicadas o desordenadas ({a.fecha} → {b.fecha}).")
        if b.open_time - a.open_time != DIA_MS:
            raise DatosInvalidos(f"{simbolo}: hueco en el calendario entre {a.fecha} y {b.fecha}; no se rellenan días.")
    for v in velas:
        x = (v.open, v.high, v.low, v.close)
        if not all(math.isfinite(p) and p > 0 for p in x) or not math.isfinite(v.volume) or v.volume < 0:
            raise DatosInvalidos(f"{simbolo}: vela {v.fecha} con precio o volumen no válido.")
        if v.high + 1e-12 < max(v.open, v.close, v.low) or v.low - 1e-12 > min(v.open, v.close, v.high):
            raise DatosInvalidos(f"{simbolo}: vela {v.fecha} incoherente (High/Low).")


def calcular(simbolo: str, velas: list[Vela]) -> Variables:
    """Variables al cierre de la ÚLTIMA vela de la lista (t)."""
    validar_velas(simbolo, velas)
    c = [v.close for v in velas[-21:]]                         # close_{t-20} .. close_t
    r = [c[i] / c[i - 1] - 1 for i in range(1, 21)]           # r_{t-19} .. r_t
    sma20 = sum(c[1:]) / 20                                     # close_{t-19} .. close_t
    m = sum(r) / 20
    vol20 = math.sqrt(sum((x - m) ** 2 for x in r) / 19)        # ddof = 1
    return Variables(simbolo=simbolo, fecha_vela=velas[-1].fecha, seq=r,
                     dist_sma20=c[-1] / sma20 - 1, vol20=vol20, cierre=c[-1], sma20=sma20)
