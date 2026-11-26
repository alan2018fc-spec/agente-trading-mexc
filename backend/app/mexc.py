"""Cliente de DATOS PÚBLICOS de MEXC Spot v3. No firma solicitudes ni tiene rutas de órdenes.

- Reintentos limitados con espera creciente ante límite de solicitudes (429), errores 5xx o fallas de red.
- Solo devuelve velas CERRADAS: una vela todavía abierta nunca se usa como definitiva.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

import httpx

from .variables import DIA_MS, Vela


class MexcError(RuntimeError):
    def __init__(self, tipo: str, mensaje: str):
        super().__init__(mensaje)
        self.tipo = tipo            # 'red' | 'limite' | 'http' | 'respuesta'


@dataclass
class InfoPar:
    simbolo: str
    paso_cantidad: float        # baseSizePrecision
    decimales_base: int
    decimales_precio: int
    monto_minimo: float         # quoteAmountPrecision (USDT)


class ClienteMEXC:
    def __init__(self, base_url: str = "https://api.mexc.com", transporte: httpx.AsyncBaseTransport | None = None,
                 reintentos: int = 3, espera_inicial: float = 1.0, timeout: float = 15.0):
        self.base_url = base_url.rstrip("/")
        self.cliente = httpx.AsyncClient(base_url=self.base_url, timeout=timeout, transport=transporte)
        self.reintentos = reintentos
        self.espera_inicial = espera_inicial
        self.ultima_latencia_ms: float | None = None
        self._info: dict[str, InfoPar] = {}

    async def cerrar(self) -> None:
        await self.cliente.aclose()

    async def _get(self, ruta: str, params: dict | None = None):
        espera, ultimo = self.espera_inicial, ""
        for intento in range(1, self.reintentos + 1):
            t0 = time.perf_counter()
            try:
                r = await self.cliente.get(ruta, params=params)
            except (httpx.TimeoutException, httpx.TransportError) as e:
                ultimo = f"sin respuesta ({type(e).__name__})"
                tipo = "red"
            else:
                self.ultima_latencia_ms = (time.perf_counter() - t0) * 1000
                if r.status_code == 200:
                    try:
                        return r.json()
                    except ValueError as e:
                        raise MexcError("respuesta", f"MEXC devolvió algo que no es JSON en {ruta}") from e
                if r.status_code == 429 or r.status_code >= 500:
                    ultimo, tipo = f"HTTP {r.status_code}", ("limite" if r.status_code == 429 else "http")
                else:
                    raise MexcError("http", f"MEXC {ruta}: HTTP {r.status_code} {r.text[:150]}")
            if intento < self.reintentos:
                await asyncio.sleep(espera)
                espera *= 2
        raise MexcError(tipo, f"MEXC {ruta}: {ultimo} tras {self.reintentos} intentos")

    async def velas_cerradas(self, simbolo: str, limite: int = 60, inicio_ms: int | None = None,
                             fin_ms: int | None = None, ahora_ms: int | None = None) -> list[Vela]:
        params: dict = {"symbol": simbolo, "interval": "1d", "limit": min(limite, 1000)}
        if inicio_ms is not None:
            params["startTime"] = inicio_ms
        if fin_ms is not None:
            params["endTime"] = fin_ms
        datos = await self._get("/api/v3/klines", params)
        if not isinstance(datos, list):
            raise MexcError("respuesta", f"Velas de {simbolo}: formato inesperado")
        ahora_ms = ahora_ms if ahora_ms is not None else int(time.time() * 1000)
        velas: dict[int, Vela] = {}
        for k in datos:
            try:
                v = Vela(int(k[0]), float(k[1]), float(k[2]), float(k[3]), float(k[4]), float(k[5]), int(k[6]))
            except (TypeError, ValueError, IndexError) as e:
                raise MexcError("respuesta", f"Vela mal formada de {simbolo}: {str(k)[:80]}") from e
            if v.close_time - v.open_time not in (DIA_MS, DIA_MS - 1):
                raise MexcError("respuesta", f"{simbolo}: la vela {v.fecha} no es diaria")
            if v.close_time <= ahora_ms:              # solo velas cerradas
                velas.setdefault(v.open_time, v)    # duplicadas: se conserva la primera
        return [velas[t] for t in sorted(velas)]

    async def historial(self, simbolo: str, inicio_ms: int, fin_ms: int) -> list[Vela]:
        """Velas cerradas con apertura en [inicio, fin). Avanza desde la última vela recibida (500 por consulta)."""
        t, salida = inicio_ms, []
        while t < fin_ms:
            e = min(t + 500 * DIA_MS, fin_ms)
            lote = [v for v in await self.velas_cerradas(simbolo, 1000, t, e) if t <= v.open_time < fin_ms]
            if lote:
                salida.extend(lote)
                t = lote[-1].open_time + DIA_MS
            else:
                t = e
        unicas = {v.open_time: v for v in salida}
        return [unicas[k] for k in sorted(unicas)]

    async def precio_libro(self, simbolo: str) -> tuple[float, float]:
        d = await self._get("/api/v3/ticker/bookTicker", {"symbol": simbolo})
        try:
            bid, ask = float(d["bidPrice"]), float(d["askPrice"])
        except (KeyError, TypeError, ValueError) as e:
            raise MexcError("respuesta", f"Libro de {simbolo} mal formado") from e
        if not (bid > 0 and ask > 0 and ask >= bid):
            raise MexcError("respuesta", f"Libro de {simbolo} inválido (bid={bid}, ask={ask})")
        return bid, ask

    async def info_par(self, simbolo: str) -> InfoPar:
        if simbolo in self._info:
            return self._info[simbolo]
        d = await self._get("/api/v3/exchangeInfo", {"symbol": simbolo})
        try:
            s = next(x for x in d["symbols"] if x["symbol"] == simbolo)
            paso = float(s.get("baseSizePrecision") or 0) or 10 ** -int(s["baseAssetPrecision"])
            info = InfoPar(simbolo, paso, int(s["baseAssetPrecision"]), int(s.get("quotePrecision", 8)),
                           float(s.get("quoteAmountPrecision") or 1))
        except (KeyError, StopIteration, TypeError, ValueError) as e:
            raise MexcError("respuesta", f"exchangeInfo de {simbolo} incompleto") from e
        self._info[simbolo] = info
        return info

    async def ping(self) -> float:
        t0 = time.perf_counter()
        await self._get("/api/v3/ping")
        return (time.perf_counter() - t0) * 1000
