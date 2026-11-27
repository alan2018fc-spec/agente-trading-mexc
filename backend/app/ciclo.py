"""Ciclo del agente: observar → pronosticar → proponer (LLM) → riesgo → ejecutar en Paper → registrar.

Frecuencia: una decisión por vela DIARIA cerrada (00:00 UTC = 18:00 en Ciudad de México). El bucle revisa
cada POLL_SEGUNDOS si ya cerró una vela nueva; no se acelera la frecuencia del modelo.
Tres libros Paper en paralelo, con el mismo periodo y costos: 'agente_llm', 'modelo' (regla sin LLM)
y 'comprar_mantener'.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Awaitable, Callable

from .config import Ajustes, Riesgo
from .db import DB, ahora_iso
from .llm import ClienteLLM, LLMNoDisponible, LLMRespuestaInvalida
from .mexc import ClienteMEXC, MexcError
from .modelo import ModeloError, ModeloLSTM, Variables
from .notificador import NotificadorWhatsApp, mensaje_oportunidad
from .paper import OrdenDuplicada, OrdenInvalida, Paper, SaldoInsuficiente
from .riesgo import ContextoRiesgo, revisar
from .variables import DIA_MS, DatosInvalidos, Vela, calcular

log = logging.getLogger("agente")
ESTRATEGIAS = ("agente_llm", "modelo", "comprar_mantener")
ESTADOS = ("DETENIDO", "ACTIVO", "PAUSADO", "PAUSADO_ERROR", "BLOQUEADO")

Ejecutor = Callable[[str, str], Awaitable[tuple[float, str]]]      # (simbolo, lado) -> (precio, referencia)


def fecha_mas(fecha: str, dias: int) -> str:
    return (datetime.strptime(fecha, "%Y-%m-%d") + timedelta(days=dias)).strftime("%Y-%m-%d")


def ms_de(fecha: str) -> int:
    return int(datetime.strptime(fecha, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp() * 1000)


@dataclass
class ResultadoCiclo:
    fecha_vela: str
    modo: str
    decisiones: int = 0
    rechazos: int = 0
    duplicados: int = 0
    latencias: list[float] = field(default_factory=list)
    errores: list[str] = field(default_factory=list)


class Agente:
    def __init__(self, ajustes: Ajustes, riesgo: Riesgo, db: DB, mexc: ClienteMEXC, llm: ClienteLLM,
                 modelo: ModeloLSTM, llm_cfg: dict, notificador: NotificadorWhatsApp | None = None):
        self.aj, self.r, self.db, self.mexc, self.llm, self.modelo = ajustes, riesgo, db, mexc, llm, modelo
        self.notificador = notificador
        self.llm_cfg = llm_cfg
        self.paper = Paper(db, riesgo)
        self.delta = float(modelo.regla["delta"])
        self._lock = asyncio.Lock()
        self._tarea: asyncio.Task | None = None
        self._tarea_replay: asyncio.Task | None = None
        self.ultimo_tick: str | None = None
        self.ultima_duracion_ciclo_s: float | None = None
        self.replay = db.get("replay_estado", {"estado": "sin_ejecutar"})
        if self.replay.get("estado") == "en_curso":          # se interrumpió con un reinicio
            self.replay["estado"] = "interrumpido"
            db.set("replay_estado", self.replay)
        db.evento("INFO", "arranque", "reinicio", f"Backend iniciado; estado recuperado: {self.estado}",
                  {"ultima_vela_vivo": self.ultima_procesada("vivo"), "version_modelo": modelo.version})

    # ---------------------------------------------------------------- estado
    @property
    def estado(self) -> str:
        return self.db.get("estado_agente", "DETENIDO")

    def _cambiar(self, nuevo: str, motivo: str) -> None:
        anterior = self.estado
        self.db.set("estado_agente", nuevo)
        self.db.set("motivo_estado", motivo)
        self.db.evento("INFO" if nuevo in ("ACTIVO", "DETENIDO", "PAUSADO") else "ALERTA", "control", "estado",
                       f"{anterior} → {nuevo}: {motivo}")

    def ultima_procesada(self, modo: str) -> str | None:
        return self.db.get(f"ultima_vela_{modo}")

    def iniciar_bucle(self) -> None:
        if self._tarea is None or self._tarea.done():
            self._tarea = asyncio.create_task(self._bucle(), name="bucle_agente")

    async def detener_bucle(self) -> None:
        for t in (self._tarea, self._tarea_replay):
            if t and not t.done():
                t.cancel()
                try:
                    await t
                except asyncio.CancelledError:
                    pass

    async def iniciar(self) -> None:
        self._cambiar("ACTIVO", "iniciado por el usuario")
        self.iniciar_bucle()

    async def pausar(self) -> None:
        self._cambiar("PAUSADO", "pausado por el usuario")

    async def reanudar(self) -> None:
        self._cambiar("ACTIVO", "reanudado por el usuario")
        self.iniciar_bucle()

    async def detener(self) -> None:
        """Bloquea nuevas órdenes. NO liquida: las posiciones quedan abiertas y visibles."""
        self._cambiar("DETENIDO", "detenido por el usuario (posiciones conservadas; liquidar es una acción aparte)")

    async def liquidar(self) -> list[dict]:
        if self.estado == "ACTIVO":
            raise RuntimeError("Primero detén o pausa el agente; liquidar es una acción explícita aparte.")
        hechas = []
        sello = "LIQ-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        for s in list(self.paper.posiciones("vivo", "agente_llm")):
            bid, _ = await self.mexc.precio_libro(s)
            o = self.paper.vender("vivo", "agente_llm", s, sello, bid * (1 - self.r.deslizamiento), "bookTicker (liquidación manual)")
            hechas.append({"simbolo": s, **o})
        self.db.evento("INFO", "control", "liquidacion", f"Liquidación manual de {len(hechas)} posiciones", hechas)
        return hechas

    # ---------------------------------------------------------------- bucle
    async def paso(self) -> ResultadoCiclo | None:
        """Una vuelta del bucle: si el agente está ACTIVO y cerró una vela nueva, la procesa.
        Ante una dependencia caída (tras reintentos) pausa nuevas decisiones y lo registra."""
        self.ultimo_tick = ahora_iso()
        if self.estado != "ACTIVO":
            return None
        try:
            return await self.revisar_nueva_vela()
        except asyncio.CancelledError:
            raise
        except (MexcError, LLMNoDisponible, ModeloError) as e:
            self._cambiar("PAUSADO_ERROR", f"Dependencia fallida: {e}")
            self.db.evento("ERROR", "ciclo", type(e).__name__, str(e))
        except Exception as e:                          # registrar y pausar; nunca operar a ciegas
            log.exception("error inesperado en el ciclo")
            self._cambiar("PAUSADO_ERROR", f"Error inesperado: {type(e).__name__}: {e}")
            self.db.evento("ERROR", "ciclo", "inesperado", str(e))
        return None

    async def _bucle(self) -> None:
        while True:
            await self.paso()
            await asyncio.sleep(self.aj.poll_segundos)

    async def revisar_nueva_vela(self, forzar: bool = False) -> ResultadoCiclo | None:
        ref = await self.mexc.velas_cerradas("BTCUSDT", limite=3)
        if not ref:
            raise MexcError("respuesta", "MEXC no devolvió velas cerradas de BTCUSDT")
        fecha = ref[-1].fecha
        ultima = self.ultima_procesada("vivo")
        if ultima is not None and fecha <= ultima:
            if forzar:
                self.db.evento("INFO", "ciclo", "duplicado", f"La vela {fecha} ya fue procesada: no se repite")
            return None
        return await self.ciclo_vivo(fecha)

    async def ciclo_vivo(self, fecha: str) -> ResultadoCiclo:
        async with self._lock:
            ahora_ms = int(time.time() * 1000)
            velas = {}
            for s in self.r.pares_permitidos:
                velas[s] = await self.mexc.velas_cerradas(s, limite=40, ahora_ms=ahora_ms)

            async def ejecutar(simbolo: str, lado: str) -> tuple[float, str]:
                bid, ask = await self.mexc.precio_libro(simbolo)
                if lado == "COMPRA":
                    return ask * (1 + self.r.deslizamiento), f"ask {ask} + desliz."
                return bid * (1 - self.r.deslizamiento), f"bid {bid} − desliz."

            res = await self.procesar_vela("vivo", fecha, velas, ejecutar, ahora_ms=ahora_ms,
                                           agente_activo=lambda: self.estado == "ACTIVO",
                                           usar_llm=True, herramientas=self.llm_cfg.get("usar_herramientas", True))
            return res

    # ---------------------------------------------------------------- núcleo común (vivo y replay)
    async def procesar_vela(self, modo: str, fecha: str, velas: dict[str, list[Vela]], ejecutar: Ejecutor,
                            ahora_ms: int | None, agente_activo: Callable[[], bool], usar_llm: bool,
                            herramientas: bool) -> ResultadoCiclo:
        t0 = time.perf_counter()
        res = ResultadoCiclo(fecha, modo)
        self.db.ej("INSERT OR IGNORE INTO ciclos(modo, fecha_vela, inicio_utc, estado) VALUES(?,?,?,?)",
                   (modo, fecha, ahora_iso(), "EN_CURSO"))
        ciclo_id = self.db.fila("SELECT id FROM ciclos WHERE modo=? AND fecha_vela=?", (modo, fecha))["id"]
        for e in ESTRATEGIAS:
            self.paper.asegurar_cuenta(modo, e)

        # 1) Variables por par, con validación de activo, frecuencia, orden y antigüedad.
        validas: list[Variables] = []
        antig: dict[str, float] = {}
        cierres: dict[str, float] = {}
        feeds: dict[str, dict] = {}
        for s in self.r.pares_permitidos:
            lista = [v for v in velas.get(s, []) if v.fecha <= fecha]
            feeds[s] = {"velas": len(lista), "ultima_vela": lista[-1].fecha if lista else None,
                        "antiguedad_h": None if (ahora_ms is None or not lista) else round((ahora_ms - lista[-1].close_time) / 3_600_000, 2),
                        "revisado_utc": ahora_iso(), "estado": "ok", "mensaje": ""}
            try:
                if not lista or lista[-1].fecha != fecha:
                    ult = lista[-1].fecha if lista else "ninguna"
                    raise DatosInvalidos(f"{s}: dato atrasado (última vela {ult}, se esperaba {fecha})")
                v = calcular(s, lista[-40:])
            except DatosInvalidos as e:
                res.errores.append(str(e))
                feeds[s].update(estado="error", mensaje=str(e))
                self.db.evento("ALERTA", "datos", "dato_invalido", str(e), {"modo": modo})
                self._registrar_sin_decision(modo, ciclo_id, s, fecha, str(e))
                continue
            antig[s] = 0.0 if ahora_ms is None else (ahora_ms - lista[-1].close_time) / 3_600_000
            cierres[s] = v.cierre
            validas.append(v)

        if modo == "vivo":
            self.db.set("feeds_vivo", feeds)

        # 2) Valuación al cierre de t (resultado de las decisiones anteriores) y límite de pérdida diaria.
        bloqueo = False
        for e in ESTRATEGIAS:
            self.paper.registrar_patrimonio(modo, e, fecha, cierres)
        previo = self.db.fila("SELECT patrimonio FROM patrimonio WHERE modo=? AND estrategia='agente_llm' AND fecha_vela<? "
                              "ORDER BY fecha_vela DESC LIMIT 1", (modo, fecha))
        actual = self.paper.valorar(modo, "agente_llm", cierres)[2]
        if previo and actual < previo["patrimonio"] * (1 - self.r.perdida_diaria_max):
            bloqueo = True
            msg = f"Pérdida diaria {actual / previo['patrimonio'] - 1:.2%} supera el límite de {self.r.perdida_diaria_max:.0%}"
            self.db.evento("ALERTA", "riesgo", "perdida_diaria", msg, {"modo": modo, "fecha": fecha})
            if modo == "vivo":
                self._cambiar("BLOQUEADO", msg)

        if not validas:
            self._cerrar_ciclo(ciclo_id, res, t0, "SIN_DATOS")
            self.db.set(f"ultima_vela_{modo}", fecha)
            return res

        # 3) Pronóstico del modelo congelado (mismas variables que en el entrenamiento).
        pron = dict(zip([v.simbolo for v in validas], self.modelo.predecir(validas)))
        eq_inicio = {e: self.paper.valorar(modo, e, cierres)[2] for e in ESTRATEGIAS}

        # 4) Comprar y mantener: compra todos los pares en el primer ciclo y no vende nunca.
        if not self.db.fila("SELECT 1 FROM ordenes WHERE modo=? AND estrategia='comprar_mantener' LIMIT 1", (modo,)):
            for v in validas:
                await self._ejecutar_estrategia(modo, "comprar_mantener", v.simbolo, fecha, "COMPRAR", ejecutar,
                                                eq_inicio["comprar_mantener"], cierres, True, False, antig[v.simbolo])

        # 5) Por par: regla del modelo, propuesta del LLM, riesgo, ejecución y registro.
        for v in validas:
            s = v.simbolo
            if self.db.fila("SELECT 1 FROM decisiones WHERE modo=? AND simbolo=? AND fecha_vela=?", (modo, s, fecha)):
                res.duplicados += 1
                self.db.evento("INFO", "ciclo", "duplicado", f"{s} {fecha}: decisión ya registrada; no se repite", {"modo": modo})
                continue
            p = float(pron[s])
            # regla sin LLM (estrategia 'modelo')
            acc_mod = self._accion_regla(p, s in self.paper.posiciones(modo, "modelo"))
            await self._ejecutar_estrategia(modo, "modelo", s, fecha, acc_mod, ejecutar, eq_inicio["modelo"], cierres,
                                            agente_activo(), bloqueo, antig[s])
            # agente con LLM
            tiene = s in self.paper.posiciones(modo, "agente_llm")
            acc_regla_agente = self._accion_regla(p, tiene)
            fila = {"accion_modelo": acc_regla_agente, "accion_llm": None, "justificacion": None, "herramientas": None,
                    "json_valido": None, "latencia": None}
            if usar_llm:
                ctx = self._contexto_llm(modo, v, p, acc_regla_agente, cierres, eq_inicio["agente_llm"])
                herr = {"consultar_posicion": lambda sym: self._h_posicion(modo, sym),
                        "consultar_historial": lambda sym: self._h_historial(modo, sym)} if herramientas else None
                try:
                    prop = await self.llm.proponer(ctx, herr, self.llm_cfg.get("max_caracteres_justificacion", 240))
                    accion = prop.accion
                    fila.update(accion_llm=prop.accion, justificacion=prop.justificacion, json_valido=1,
                                herramientas=",".join(prop.herramientas), latencia=prop.latencia_ms)
                    res.latencias.append(prop.latencia_ms)
                except LLMRespuestaInvalida as e:
                    accion = "MANTENER"                    # evitar operaciones indebidas
                    fila.update(json_valido=0, justificacion=f"Respuesta del LLM rechazada: {e}")
                    self.db.evento("ALERTA", "llm", "json_invalido", f"{s} {fecha}: {e}", {"modo": modo})
            else:
                accion = acc_regla_agente
                fila.update(accion_llm=None, justificacion="Replay sin LLM: se aplica la regla del modelo", json_valido=None)
            rev, orden = await self._ejecutar_estrategia(modo, "agente_llm", s, fecha, accion, ejecutar,
                                                         eq_inicio["agente_llm"], cierres, agente_activo(), bloqueo, antig[s])
            if fila["json_valido"] == 0:
                rev_aprob, motivo = False, fila["justificacion"]
            else:
                rev_aprob, motivo = rev.aprobada, rev.motivo
            if not rev_aprob:
                res.rechazos += 1
            self.db.ej("INSERT OR IGNORE INTO decisiones(modo, ciclo_id, simbolo, fecha_vela, ts_utc, cierre, pronostico, dist_sma20,"
                       " vol20, sma20, accion_modelo, accion_llm, justificacion, herramientas, json_valido, accion_final, aprobada,"
                       " motivo, latencia_llm_ms, version_modelo, entradas) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                       (modo, ciclo_id, s, fecha, ahora_iso(), v.cierre, p, v.dist_sma20, v.vol20, v.sma20,
                        fila["accion_modelo"], fila["accion_llm"], fila["justificacion"], fila["herramientas"],
                        fila["json_valido"], accion if rev_aprob else "SIN_ORDEN", int(rev_aprob), motivo,
                        fila["latencia"], self.modelo.version,
                        json.dumps({"seq": v.seq, "dist_sma20": v.dist_sma20, "vol20": v.vol20, "cierre": v.cierre, "sma20": v.sma20})))
            res.decisiones += 1
            if modo == "vivo" and (fila["accion_modelo"] == "COMPRAR" or accion == "COMPRAR"):
                await self._avisar(modo, s, fecha, v.cierre, p, fila["accion_modelo"], accion if rev_aprob else "SIN_ORDEN",
                                   rev_aprob, fila["justificacion"])

        self._cerrar_ciclo(ciclo_id, res, t0, "COMPLETO")
        self.db.set(f"ultima_vela_{modo}", fecha)
        return res

    # ---------------------------------------------------------------- avisos
    async def _avisar(self, modo, simbolo, fecha, cierre, pron, acc_regla, acc_final, aprobada, justificacion) -> None:
        """Un aviso por par y vela (aunque el ciclo se repita). Un fallo al avisar nunca afecta al trading."""
        if self.notificador is None:
            return
        texto = mensaje_oportunidad(simbolo, fecha, cierre, pron, self.delta, acc_regla, acc_final, aprobada, justificacion)
        cur = self.db.ej("INSERT OR IGNORE INTO notificaciones(ts_utc, modo, simbolo, fecha_vela, canal, estado, mensaje) "
                         "VALUES(?,?,?,?,?,?,?)", (ahora_iso(), modo, simbolo, fecha, "whatsapp", "pendiente", texto))
        if cur.rowcount == 0:
            return                                            # ya se avisó esta oportunidad
        ok, detalle = await self.notificador.enviar(texto)
        estado = "enviado" if ok else ("sin_configurar" if not self.notificador.configurado else "error")
        self.db.ej("UPDATE notificaciones SET estado=?, detalle=?, ts_utc=? WHERE modo=? AND simbolo=? AND fecha_vela=? AND canal='whatsapp'",
                   (estado, detalle, ahora_iso(), modo, simbolo, fecha))
        if estado == "error":
            self.db.evento("ALERTA", "whatsapp", "aviso_fallido", f"{simbolo} {fecha}: {detalle}")

    # ---------------------------------------------------------------- auxiliares
    def _accion_regla(self, pronostico: float, tiene_posicion: bool) -> str:
        if pronostico > self.delta:
            return "MANTENER" if tiene_posicion else "COMPRAR"
        return "VENDER" if tiene_posicion else "MANTENER"

    async def _ejecutar_estrategia(self, modo, estrategia, simbolo, fecha, accion, ejecutar, eq_inicio, cierres,
                                   activo, bloqueo, antiguedad):
        posiciones = self.paper.posiciones(modo, estrategia)
        # Exposición: las posiciones abiertas en ESTE ciclo cuentan a su costo (no al cierre anterior, que puede
        # diferir del precio de ejecución); las anteriores, al cierre de la vela.
        valor_pos = sum(p["costo_entrada"] if p["fecha_entrada"] == fecha else p["cantidad"] * cierres.get(sym, p["precio_entrada"])
                        for sym, p in posiciones.items())
        info = await self.mexc.info_par(simbolo) if modo == "vivo" else None
        rev = revisar(self.r, ContextoRiesgo(
            agente_activo=activo, simbolo=simbolo, accion=accion, fecha_vela=fecha, fecha_esperada=fecha,
            antiguedad_horas=antiguedad, tiene_posicion=simbolo in posiciones,
            efectivo=self.paper.efectivo(modo, estrategia), patrimonio_inicio_ciclo=eq_inicio, valor_posiciones=valor_pos,
            monto_minimo=info.monto_minimo if info else 1.0, bloqueo_perdida_diaria=bloqueo))
        orden = None
        if rev.aprobada and accion in ("COMPRAR", "VENDER"):
            lado = "COMPRA" if accion == "COMPRAR" else "VENTA"
            try:
                precio, ref = await ejecutar(simbolo, lado)
                if lado == "COMPRA":
                    orden = self.paper.comprar(modo, estrategia, simbolo, fecha, rev.monto, precio,
                                               info.paso_cantidad if info else 1e-8, ref)
                else:
                    orden = self.paper.vender(modo, estrategia, simbolo, fecha, precio, ref)
            except (SaldoInsuficiente, OrdenInvalida) as e:
                rev.aprobada, rev.motivo = False, f"Rechazada por el simulador: {e}"
                self.db.evento("ALERTA", "paper", type(e).__name__, str(e), {"modo": modo, "estrategia": estrategia})
            except OrdenDuplicada as e:
                rev.aprobada, rev.motivo = False, str(e)
                self.db.evento("INFO", "paper", "duplicado", str(e), {"modo": modo, "estrategia": estrategia})
        return rev, orden

    def _contexto_llm(self, modo, v: Variables, pron, acc_regla, cierres, eq_inicio) -> dict:
        """Datos para el LLM. Todos los porcentajes van en por ciento (una sola unidad) para no confundir al modelo."""
        pos = self.paper.posiciones(modo, "agente_llm").get(v.simbolo)
        return {
            "modo": "paper", "simbolo": v.simbolo, "fecha_vela": v.fecha_vela,
            "accion_sugerida_por_regla": acc_regla,
            "pronostico_supera_delta": bool(pron > self.delta),
            "pronostico_pct": round(pron * 100, 3), "delta_pct": round(self.delta * 100, 3),
            "cierre_usdt": v.cierre,
            "indicadores": {"distancia_a_sma20_pct": round(v.dist_sma20 * 100, 2), "volatilidad_20d_pct": round(v.vol20 * 100, 2),
                            "cierre_sobre_sma20": v.cierre > v.sma20},
            "rendimientos_ultimos_5_dias_pct": [round(x * 100, 2) for x in v.seq[-5:]],
            "posicion": {"tiene_posicion": pos is not None, "cantidad": pos["cantidad"] if pos else 0.0,
                         "precio_entrada": pos["precio_entrada"] if pos else None,
                         "rendimiento_no_realizado_pct": round((v.cierre / pos["precio_entrada"] - 1) * 100, 2) if pos else None},
            "efectivo_disponible_usdt": round(self.paper.efectivo(modo, "agente_llm"), 2),
            "limite_por_par_usdt": round(self.r.exposicion_max_por_par * eq_inicio, 2),
            "acciones_permitidas": list(self.r.acciones_permitidas),
        }

    def _h_posicion(self, modo, simbolo) -> dict:
        p = self.paper.posiciones(modo, "agente_llm").get(simbolo)
        return {"simbolo": simbolo, "tiene_posicion": p is not None, "cantidad": p["cantidad"] if p else 0.0,
                "precio_entrada": p["precio_entrada"] if p else None,
                "efectivo_disponible_usdt": round(self.paper.efectivo(modo, "agente_llm"), 2)}

    def _h_historial(self, modo, simbolo) -> dict:
        filas = self.db.filas("SELECT fecha_vela, pronostico, accion_final FROM decisiones WHERE modo=? AND simbolo=? "
                              "ORDER BY fecha_vela DESC LIMIT 5", (modo, simbolo))
        return {"simbolo": simbolo, "ultimas_decisiones": filas}

    def _registrar_sin_decision(self, modo, ciclo_id, s, fecha, motivo):
        self.db.ej("INSERT OR IGNORE INTO decisiones(modo, ciclo_id, simbolo, fecha_vela, ts_utc, accion_final, aprobada, motivo,"
                   " version_modelo) VALUES(?,?,?,?,?,?,?,?,?)",
                   (modo, ciclo_id, s, fecha, ahora_iso(), "SIN_DECISION", 0, motivo, self.modelo.version))

    def _cerrar_ciclo(self, ciclo_id, res: ResultadoCiclo, t0, estado):
        prom = sum(res.latencias) / len(res.latencias) if res.latencias else None
        self.ultima_duracion_ciclo_s = time.perf_counter() - t0
        self.db.ej("UPDATE ciclos SET fin_utc=?, estado=?, n_decisiones=?, n_rechazos=?, latencia_llm_ms_prom=?, nota=? WHERE id=?",
                   (ahora_iso(), estado, res.decisiones, res.rechazos, prom,
                    json.dumps({"errores": res.errores, "duplicados": res.duplicados}, ensure_ascii=False), ciclo_id))

    # ---------------------------------------------------------------- replay
    def lanzar_replay(self, dias: int, usar_llm: bool) -> None:
        if self._tarea_replay and not self._tarea_replay.done():
            raise RuntimeError("Ya hay un replay en curso.")
        if not 5 <= dias <= 60:
            raise ValueError("El replay admite de 5 a 60 días.")
        self._tarea_replay = asyncio.create_task(self._replay(dias, usar_llm), name="replay")

    async def _replay(self, dias: int, usar_llm: bool) -> None:
        self.replay = {"estado": "en_curso", "dias": dias, "usar_llm": usar_llm, "inicio_utc": ahora_iso(), "avance": 0,
                       "aviso": "REPLAY: repetición histórica, separada de la conexión actual con MEXC"}
        self.db.set("replay_estado", self.replay)
        try:
            self.paper.reiniciar_modo("replay")
            ref = await self.mexc.velas_cerradas("BTCUSDT", limite=3)
            ultima = ref[-1].fecha                                  # L: última vela cerrada
            inicio = fecha_mas(ultima, -(dias + 45))
            hist = {s: await self.mexc.historial(s, ms_de(inicio), ms_de(fecha_mas(ultima, 1))) for s in self.r.pares_permitidos}
            fechas = [fecha_mas(ultima, -dias + k) for k in range(dias)]   # D = L-dias … L-1 (se ejecuta en D+1)
            self.replay.update(desde=fechas[0], hasta=fechas[-1])
            for k, d in enumerate(fechas):
                aperturas = {s: {v.fecha: v.open for v in hist[s]} for s in hist}

                async def ejecutar(simbolo: str, lado: str, d=d) -> tuple[float, str]:
                    sig = fecha_mas(d, 1)
                    op = aperturas[simbolo].get(sig)
                    if op is None:
                        raise OrdenInvalida(f"Sin apertura de {simbolo} el {sig}")
                    f = (1 + self.r.deslizamiento) if lado == "COMPRA" else (1 - self.r.deslizamiento)
                    return op * f, f"open {sig} (replay)"

                await self.procesar_vela("replay", d, hist, ejecutar, ahora_ms=None, agente_activo=lambda: True,
                                         usar_llm=usar_llm, herramientas=self.llm_cfg.get("usar_herramientas_en_replay", False))
                self.replay["avance"] = k + 1
                self.db.set("replay_estado", self.replay)
            cierres = {s: v[-1].close for s, v in hist.items() if v and v[-1].fecha == ultima}
            for e in ESTRATEGIAS:
                self.paper.registrar_patrimonio("replay", e, ultima, cierres)
            self.replay.update(estado="completo", fin_utc=ahora_iso())
        except asyncio.CancelledError:
            self.replay.update(estado="interrumpido")
            raise
        except (MexcError, LLMNoDisponible, ModeloError) as e:
            self.replay.update(estado="error", error=str(e))
            self.db.evento("ERROR", "replay", type(e).__name__, str(e))
        finally:
            self.db.set("replay_estado", self.replay)
