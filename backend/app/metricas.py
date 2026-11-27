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


def operaciones_pnl(db: DB, modo: str, capital: float) -> dict:
    """Operaciones (compra → venta) y PnL por estrategia. Las abiertas se valoran al último cierre disponible de MEXC.

    PnL realizado = (venta − costo de venta) − (compra + costo de compra). PnL no realizado = valor al último cierre −
    (compra + costo de compra). Los porcentajes se calculan sobre lo invertido en cada operación y, en el total, sobre el
    capital inicial de cada estrategia.
    """
    # Precios de la última valuación del patrimonio (mismo cierre de MEXC); si no existen, último cierre por decisión.
    guardado = db.get(f"precios_valuacion_{modo}") or {}
    ultimo = {s: {"fecha": guardado.get("fecha"), "close": px} for s, px in (guardado.get("precios") or {}).items()}
    for r in db.filas("SELECT d.simbolo, d.fecha_vela fecha, d.cierre close FROM decisiones d JOIN (SELECT simbolo, "
                      "MAX(fecha_vela) f FROM decisiones WHERE modo=? AND cierre IS NOT NULL GROUP BY simbolo) u "
                      "ON d.simbolo=u.simbolo AND d.fecha_vela=u.f WHERE d.modo=?", (modo, modo)):
        if r["simbolo"] not in ultimo or (r["fecha"] or "") > (ultimo[r["simbolo"]]["fecha"] or ""):
            ultimo[r["simbolo"]] = r
    estrategias, ops = [], []
    for e in ("agente_llm", "modelo", "comprar_mantener"):
        abiertas: dict[str, dict] = {}
        propias = []
        for o in db.filas("SELECT * FROM ordenes WHERE modo=? AND estrategia=? ORDER BY id", (modo, e)):
            if o["lado"] == "COMPRA":
                abiertas[o["simbolo"]] = o
            elif o["lado"] == "VENTA" and o["simbolo"] in abiertas:
                c = abiertas.pop(o["simbolo"])
                invertido = c["monto"] + c["costo"]
                pnl = (o["monto"] - o["costo"]) - invertido
                propias.append({"estrategia": e, "simbolo": c["simbolo"], "estado": "cerrada", "cantidad": c["cantidad"],
                                "fecha_entrada": c["fecha_vela"], "precio_entrada": c["precio"],
                                "fecha_salida": o["fecha_vela"], "precio_salida": o["precio"],
                                "invertido": invertido, "pnl": pnl, "pnl_pct": 100 * pnl / invertido})
        for s, c in abiertas.items():
            invertido = c["monto"] + c["costo"]
            u = ultimo.get(s)
            px = u["close"] if u else c["precio"]
            pnl = c["cantidad"] * px - invertido
            propias.append({"estrategia": e, "simbolo": s, "estado": "abierta", "cantidad": c["cantidad"],
                            "fecha_entrada": c["fecha_vela"], "precio_entrada": c["precio"],
                            "fecha_salida": u["fecha"] if u else None, "precio_salida": px,
                            "invertido": invertido, "pnl": pnl, "pnl_pct": 100 * pnl / invertido})
        cerr = [p for p in propias if p["estado"] == "cerrada"]
        realizado = sum(p["pnl"] for p in cerr)
        no_real = sum(p["pnl"] for p in propias if p["estado"] == "abierta")
        estrategias.append({"estrategia": e, "pnl_realizado": realizado, "pnl_no_realizado": no_real,
                            "pnl_total": realizado + no_real, "pnl_total_pct": 100 * (realizado + no_real) / capital,
                            "cerradas": len(cerr), "abiertas": len(propias) - len(cerr),
                            "ganadoras": sum(p["pnl"] > 0 for p in cerr),
                            "win_rate_pct": 100 * sum(p["pnl"] > 0 for p in cerr) / len(cerr) if cerr else None})
        ops += propias
    ops.sort(key=lambda p: (p["fecha_entrada"], p["simbolo"]), reverse=True)
    diario = []
    for e in ("agente_llm", "modelo", "comprar_mantener"):
        prev = capital
        for r in db.filas("SELECT fecha_vela, patrimonio FROM patrimonio WHERE modo=? AND estrategia=? ORDER BY fecha_vela",
                          (modo, e)):
            diario.append({"estrategia": e, "fecha_vela": r["fecha_vela"], "pnl": r["patrimonio"] - prev,
                           "pnl_pct": 100 * (r["patrimonio"] / prev - 1), "acumulado_pct": 100 * (r["patrimonio"] / capital - 1)})
            prev = r["patrimonio"]
    precios_al = max((u["fecha"] for u in ultimo.values()), default=None)
    return {"modo": modo, "precios_al": precios_al, "estrategias": estrategias, "operaciones": ops, "diario": diario}
