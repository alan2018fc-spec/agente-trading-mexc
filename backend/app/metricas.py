"""Métricas para comparar, con el mismo periodo y costos: agente con LLM, modelo sin LLM y comprar y mantener."""
from __future__ import annotations

from .db import DB


def _operaciones_cerradas(ordenes: list[dict]) -> list[float]:
    abiertas: dict[str, dict] = {}
    pnl = []
    for o in ordenes:
        if o["lado"] == "COMPRA":
            abiertas[o["simbolo"]] = o
        elif o["lado"] == "VENTA" and o["simbolo"] in abiertas:
            c = abiertas.pop(o["simbolo"])
            pnl.append((o["monto"] - o["costo"]) - (c["monto"] + c["costo"]))
    return pnl


def resumen(db: DB, modo: str, estrategia: str, capital: float) -> dict:
    serie = db.filas("SELECT fecha_vela, patrimonio FROM patrimonio WHERE modo=? AND estrategia=? ORDER BY fecha_vela",
                     (modo, estrategia))
    ordenes = db.filas("SELECT * FROM ordenes WHERE modo=? AND estrategia=? ORDER BY id", (modo, estrategia))
    eq = [capital] + [s["patrimonio"] for s in serie]
    pico, dd = eq[0], 0.0
    for x in eq:
        pico = max(pico, x)
        dd = min(dd, x / pico - 1)
    pnl = _operaciones_cerradas(ordenes)
    ganancias, perdidas = sum(p for p in pnl if p > 0), -sum(p for p in pnl if p < 0)
    out = {"estrategia": estrategia, "modo": modo, "dias": len(serie),
           "desde": serie[0]["fecha_vela"] if serie else None, "hasta": serie[-1]["fecha_vela"] if serie else None,
           "patrimonio_final": eq[-1], "rendimiento_neto_pct": 100 * (eq[-1] / capital - 1), "drawdown_max_pct": 100 * dd,
           "ordenes": len(ordenes), "operaciones_cerradas": len(pnl),
           "win_rate_pct": 100 * sum(p > 0 for p in pnl) / len(pnl) if pnl else None,
           "profit_factor": (ganancias / perdidas) if perdidas > 0 else None,
           "costos_totales": sum(o["costo"] for o in ordenes)}
    if estrategia == "agente_llm":
        d = db.fila("SELECT COUNT(*) n, SUM(aprobada=0) rech, SUM(json_valido=0) inval, AVG(latencia_llm_ms) lat, "
                    "SUM(accion_llm IS NOT NULL AND accion_llm<>accion_modelo) difiere FROM decisiones WHERE modo=?", (modo,))
        out.update(decisiones=d["n"] or 0, acciones_rechazadas=d["rech"] or 0, json_invalidos=d["inval"] or 0,
                   latencia_llm_ms_prom=d["lat"], llm_difiere_de_regla=d["difiere"] or 0)
    return out


def comparacion(db: DB, modo: str, capital: float) -> list[dict]:
    return [resumen(db, modo, e, capital) for e in ("agente_llm", "modelo", "comprar_mantener")]
