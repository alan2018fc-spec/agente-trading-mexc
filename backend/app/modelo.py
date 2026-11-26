"""Modelo predictivo de la práctica (LSTM 16 neuronas, versión B, ensamble de 3 semillas).

La inferencia se hace con NumPy a partir de los pesos exportados de los archivos .keras
entrenados en la T4 (corrida 20261120_144755). Así no hace falta TensorFlow en el equipo
local. Al cargar, se verifica contra salidas de referencia calculadas con Keras.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np

SEMILLAS = (11, 22, 33)


def _sig(x: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-x))


@dataclass
class Variables:
    """Variables de un par al cierre de t (mismas definiciones que el notebook)."""
    simbolo: str
    fecha_vela: str            # día UTC de la vela t (YYYY-MM-DD)
    seq: list[float]           # r_{t-19} ... r_t (antiguo → reciente)
    dist_sma20: float
    vol20: float
    cierre: float
    sma20: float


class ModeloError(RuntimeError):
    pass


class ModeloLSTM:
    def __init__(self, carpeta: str | Path):
        carpeta = Path(carpeta)
        self.carpeta = carpeta
        z = np.load(carpeta / "lstm16_B_pesos.npz")
        self.pesos = {s: {k: z[f"s{s}_{k}"].astype(np.float64)
                          for k in ("lstm_k", "lstm_rk", "lstm_b", "d1_k", "d1_b", "d2_k", "d2_b")}
                      for s in SEMILLAS}
        self._ref = {s: {k: z[f"s{s}_ref_{k}"] for k in ("seq", "ids", "ind", "out")} for s in SEMILLAS}
        self.esc = json.loads((carpeta / "escaladores.json").read_text(encoding="utf-8"))
        self.contrato = json.loads((carpeta / "contrato_variables.json").read_text(encoding="utf-8"))
        self.pares: list[str] = self.contrato["orden_pares_onehot"]
        self.regla = self.contrato["regla_trading"]
        h = hashlib.sha256()
        for nombre in ("lstm16_B_pesos.npz", "escaladores.json", "contrato_variables.json"):
            h.update((carpeta / nombre).read_bytes())
        self.huella = h.hexdigest()
        self.version = f"{self.contrato['modelo']}-{self.huella[:12]}"
        self.verificar()

    # --- red ---------------------------------------------------------------------------
    def _forward(self, s: int, seq: np.ndarray, ids: np.ndarray, ind: np.ndarray) -> np.ndarray:
        p = self.pesos[s]
        u = p["lstm_rk"].shape[0]
        n = seq.shape[0]
        h = np.zeros((n, u)); c = np.zeros((n, u))
        for t in range(seq.shape[1]):                      # orden de compuertas Keras: i, f, c, o
            z = seq[:, t:t + 1] @ p["lstm_k"] + h @ p["lstm_rk"] + p["lstm_b"]
            i, f, g, o = _sig(z[:, :u]), _sig(z[:, u:2 * u]), np.tanh(z[:, 2 * u:3 * u]), _sig(z[:, 3 * u:])
            c = f * c + i * g
            h = o * np.tanh(c)
        x = np.concatenate([h, ids, ind], axis=1)          # mismo orden que Concatenate([lstm, ids, ind])
        d = np.maximum(x @ p["d1_k"] + p["d1_b"], 0.0)     # Dropout inactivo en inferencia
        return (d @ p["d2_k"] + p["d2_b"]).ravel()

    def verificar(self, tol: float = 1e-4) -> float:
        peor = 0.0
        for s in SEMILLAS:
            r = self._ref[s]
            out = self._forward(s, r["seq"].astype(np.float64), r["ids"].astype(np.float64), r["ind"].astype(np.float64))
            peor = max(peor, float(np.max(np.abs(out - r["out"]))))
        if not np.isfinite(peor) or peor > tol:
            raise ModeloError(f"Los pesos no reproducen la salida de Keras (dif. máx {peor:.2e}). Revisar exportación.")
        self.diferencia_maxima_vs_keras = peor
        return peor

    # --- uso -----------------------------------------------------------------------------
    def entradas(self, vs: list[Variables]) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        n = len(vs)
        if n == 0:
            raise ModeloError("Sin variables para pronosticar.")
        seq = np.array([v.seq for v in vs], dtype=np.float64)
        if seq.shape != (n, 20):
            raise ModeloError(f"Forma incorrecta de la secuencia: esperada ({n}, 20), recibida {seq.shape}.")
        ind = np.array([[v.dist_sma20, v.vol20] for v in vs], dtype=np.float64)
        if not (np.isfinite(seq).all() and np.isfinite(ind).all()):
            raise ModeloError("Variables con NaN o infinito: no se pronostica.")
        ids = np.zeros((n, len(self.pares)))
        for k, v in enumerate(vs):
            if v.simbolo not in self.pares:
                raise ModeloError(f"Símbolo fuera del contrato: {v.simbolo}")
            ids[k, self.pares.index(v.simbolo)] = 1.0
        e = self.esc
        seq_z = (seq - e["rezagos"]["media"]) / e["rezagos"]["sd"]
        ind_z = (ind - np.array(e["indicadores"]["media"])) / np.array(e["indicadores"]["escala"])
        return seq_z, ids, ind_z

    def predecir(self, vs: list[Variables]) -> np.ndarray:
        seq_z, ids, ind_z = self.entradas(vs)
        z = np.mean([self._forward(s, seq_z, ids, ind_z) for s in SEMILLAS], axis=0)
        r = z * self.esc["objetivo"]["escala"] + self.esc["objetivo"]["media"]
        if not np.isfinite(r).all():
            raise ModeloError("Pronóstico no finito.")
        return r

    def activaciones(self, v: Variables) -> dict:
        """Activaciones reales de cada capa para un par (vista 'Red neuronal' de la interfaz)."""
        seq_z, ids, ind_z = self.entradas([v])
        sal = []
        for s in SEMILLAS:
            p = self.pesos[s]
            u = p["lstm_rk"].shape[0]
            h = np.zeros((1, u)); c = np.zeros((1, u))
            for t in range(seq_z.shape[1]):
                z = seq_z[:, t:t + 1] @ p["lstm_k"] + h @ p["lstm_rk"] + p["lstm_b"]
                i, f, g, o = _sig(z[:, :u]), _sig(z[:, u:2 * u]), np.tanh(z[:, 2 * u:3 * u]), _sig(z[:, 3 * u:])
                c = f * c + i * g
                h = o * np.tanh(c)
            x = np.concatenate([h, ids, ind_z], axis=1)
            d = np.maximum(x @ p["d1_k"] + p["d1_b"], 0.0)
            out_z = float((d @ p["d2_k"] + p["d2_b"]).ravel()[0])
            sal.append({"semilla": s, "h": h.ravel().round(4).tolist(), "d": d.ravel().round(4).tolist(), "salida_z": round(out_z, 5),
                        "salida": out_z * self.esc["objetivo"]["escala"] + self.esc["objetivo"]["media"]})
        p11 = self.pesos[SEMILLAS[0]]
        return {"simbolo": v.simbolo, "fecha_vela": v.fecha_vela,
                "entradas": {"seq": [round(x, 6) for x in v.seq], "seq_z": seq_z.ravel().round(4).tolist(),
                             "dist_sma20": v.dist_sma20, "vol20": v.vol20, "ind_z": ind_z.ravel().round(4).tolist(),
                             "par_indice": self.pares.index(v.simbolo)},
                "semillas": sal, "pronostico": float(np.mean([x["salida"] for x in sal])),
                "pesos_semilla_11": {"d1": p11["d1_k"].round(4).tolist(), "d2": p11["d2_k"].ravel().round(4).tolist()},
                "arquitectura": {"pasos": 20, "lstm": int(p11["lstm_rk"].shape[0]), "pares": len(self.pares), "indicadores": 2,
                                 "densa": int(p11["d1_k"].shape[1]), "semillas": list(SEMILLAS)}}
