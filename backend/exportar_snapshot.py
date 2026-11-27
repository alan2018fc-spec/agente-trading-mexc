"""Exporta el registro del agente a datos_publicos/snapshot_agente.json para la app pública de Streamlit.

Solo datos de la simulación (decisiones, operaciones, PnL, patrimonio, ciclos, avisos). Nunca claves ni el .env.
Uso (desde la carpeta del proyecto):  .venv\\Scripts\\python backend\\exportar_snapshot.py
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

from app import metricas  # noqa: E402
from app.db import DB  # noqa: E402
from app.version import VERSION  # noqa: E402

CAPITAL = json.loads((BASE / "config" / "riesgo.json").read_text(encoding="utf-8"))["capital_inicial"]


def exportar(db_path: Path, salida: Path) -> dict:
    db = DB(db_path)
    d: dict = {"generado_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "version": VERSION,
               "capital_inicial": CAPITAL, "modos": {}}
    for modo in ("vivo", "replay"):
        d["modos"][modo] = {
            "comparacion": metricas.comparacion(db, modo, CAPITAL),
            "pnl": metricas.operaciones_pnl(db, modo, CAPITAL),
            "patrimonio": db.filas("SELECT estrategia, fecha_vela, patrimonio FROM patrimonio WHERE modo=? "
                                   "ORDER BY fecha_vela", (modo,)),
            "decisiones": db.filas(
                "SELECT fecha_vela, simbolo, cierre, pronostico, dist_sma20, vol20, accion_modelo, accion_llm, "
                "accion_final, aprobada, motivo, justificacion, json_valido, latencia_llm_ms FROM decisiones "
                "WHERE modo=? ORDER BY fecha_vela DESC, simbolo", (modo,)),
            "ciclos": db.filas("SELECT fecha_vela, inicio_utc, fin_utc, estado, n_decisiones, n_rechazos, "
                               "latencia_llm_ms_prom FROM ciclos WHERE modo=? ORDER BY id", (modo,)),
        }
    d["avisos"] = db.filas("SELECT ts_utc, simbolo, fecha_vela, estado, mensaje FROM notificaciones "
                           "WHERE modo='vivo' ORDER BY id DESC")
    d["eventos"] = db.filas("SELECT nivel, tipo, COUNT(*) n FROM eventos GROUP BY nivel, tipo ORDER BY n DESC")
    salida.parent.mkdir(parents=True, exist_ok=True)
    salida.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    return d


if __name__ == "__main__":
    destino = BASE.parent / "datos_publicos" / "snapshot_agente.json"
    r = exportar(BASE / "data" / "agente.db", destino)
    v = r["modos"]["vivo"]
    print(f"Listo: {destino}")
    print(f"Velas en vivo: {len(v['ciclos'])} · decisiones: {len(v['decisiones'])} · "
          f"operaciones: {len(v['pnl']['operaciones'])}")
