"""Avisos por WhatsApp cuando el agente detecta una oportunidad de compra.

Dos proveedores gratuitos para enviarte mensajes a ti mismo (elige con WHATSAPP_PROVEEDOR en backend/.env):

* greenapi  (recomendado) — GREEN-API, plan Developer gratis: vinculas TU WhatsApp escaneando un QR (como WhatsApp Web)
  y el agente te escribe a tu propio chat. Variables: GREENAPI_URL, GREENAPI_ID_INSTANCE, GREENAPI_TOKEN y, opcional,
  WHATSAPP_TELEFONO (si se deja vacío se usa el número vinculado).
* callmebot — CallMeBot: WHATSAPP_TELEFONO y WHATSAPP_APIKEY (a veces su bot está lleno y no da claves nuevas).

Las claves viven solo en backend/.env y nunca se registran. Un fallo al avisar NUNCA detiene ni cambia una decisión de
trading: solo se registra.
"""
from __future__ import annotations

import asyncio
import os
import re

import httpx

PROVEEDORES = ("greenapi", "callmebot")


def _env(nombre: str, valor: str | None) -> str:
    return (valor if valor is not None else os.getenv(nombre, "")).strip()


class NotificadorWhatsApp:
    def __init__(self, telefono: str | None = None, apikey: str | None = None, *, proveedor: str | None = None,
                 greenapi_url: str | None = None, id_instance: str | None = None, token: str | None = None,
                 transporte: httpx.AsyncBaseTransport | None = None, reintentos: int = 2, espera_inicial: float = 2.0):
        self.telefono = _env("WHATSAPP_TELEFONO", telefono).replace(" ", "")
        self.apikey = _env("WHATSAPP_APIKEY", apikey)
        self.greenapi_url = _env("GREENAPI_URL", greenapi_url).rstrip("/")
        self.id_instance = _env("GREENAPI_ID_INSTANCE", id_instance)
        self.token = _env("GREENAPI_TOKEN", token)
        prov = _env("WHATSAPP_PROVEEDOR", proveedor).lower()
        if prov not in PROVEEDORES:
            prov = "greenapi" if (self.id_instance or self.token) else "callmebot"
        self.proveedor = prov
        self.reintentos, self.espera_inicial = reintentos, espera_inicial
        self.cliente = httpx.AsyncClient(timeout=20, transport=transporte)
        self._chat_propio: str | None = None

    # ------------------------------------------------------------------ estado
    @property
    def configurado(self) -> bool:
        if self.proveedor == "greenapi":
            return bool(self.greenapi_url.startswith("https://") and self.id_instance and self.token)
        return bool(self.telefono and self.apikey)

    @property
    def destino(self) -> str:
        num = self.telefono or (self._chat_propio or "").split("@")[0]
        if num:
            return f"…{num[-4:]}"
        return "tu número vinculado" if self.configurado else "sin configurar"

    def _faltan(self) -> str:
        if self.proveedor == "greenapi":
            return "faltan GREENAPI_URL (https://…), GREENAPI_ID_INSTANCE o GREENAPI_TOKEN en backend/.env"
        return "faltan WHATSAPP_TELEFONO o WHATSAPP_APIKEY en backend/.env"

    async def cerrar(self) -> None:
        await self.cliente.aclose()

    # ------------------------------------------------------------------ envío
    async def enviar(self, texto: str) -> tuple[bool, str]:
        """Devuelve (ok, detalle). Nunca lanza excepción hacia el ciclo del agente."""
        if not self.configurado:
            return False, f"WhatsApp no configurado ({self._faltan()})"
        espera, ultimo = self.espera_inicial, ""
        for intento in range(1, self.reintentos + 1):
            try:
                ok, ultimo, reintentar = await (self._greenapi(texto) if self.proveedor == "greenapi"
                                                else self._callmebot(texto))
            except (httpx.TimeoutException, httpx.TransportError) as e:
                ok, ultimo, reintentar = False, f"sin respuesta de {self.proveedor} ({type(e).__name__})", True
            except Exception as e:  # respuesta inesperada: se registra y no se reintenta
                ok, ultimo, reintentar = False, f"respuesta inesperada de {self.proveedor} ({type(e).__name__})", False
            if ok:
                return True, "enviado"
            if not reintentar:
                break
            if intento < self.reintentos:
                await asyncio.sleep(espera)
                espera *= 2
        return False, ultimo

    async def _callmebot(self, texto: str) -> tuple[bool, str, bool]:
        r = await self.cliente.get("https://api.callmebot.com/whatsapp.php",
                                   params={"phone": self.telefono, "text": texto[:1000], "apikey": self.apikey,
                                           "source": "agente-mexc"})
        cuerpo = r.text.lower()
        if r.status_code == 200 and "error" not in cuerpo and "invalid" not in cuerpo:
            return True, "enviado", False
        return False, f"CallMeBot HTTP {r.status_code}: revisa teléfono y clave", r.status_code >= 500

    def _url(self, metodo: str) -> str:
        return f"{self.greenapi_url}/waInstance{self.id_instance}/{metodo}/{self.token}"

    async def _chat_destino(self) -> str:
        if self.telefono:
            return re.sub(r"\D", "", self.telefono) + "@c.us"
        if not self._chat_propio:                   # el propio número vinculado (te escribes a ti mismo)
            r = await self.cliente.get(self._url("getSettings"))
            r.raise_for_status()
            wid = (r.json() or {}).get("wid") or ""
            if not wid.endswith("@c.us"):
                raise ValueError("instancia sin número vinculado")
            self._chat_propio = wid
        return self._chat_propio

    async def _greenapi(self, texto: str) -> tuple[bool, str, bool]:
        try:
            chat = await self._chat_destino()
        except httpx.HTTPStatusError as e:
            return False, _error_greenapi(e.response.status_code), e.response.status_code >= 500
        except ValueError:
            return False, "GREEN-API: la instancia no tiene WhatsApp vinculado; escanea el QR en la consola", False
        r = await self.cliente.post(self._url("sendMessage"), json={"chatId": chat, "message": texto[:4000]})
        if r.status_code == 200 and (r.json() or {}).get("idMessage"):
            return True, "enviado", False
        return False, _error_greenapi(r.status_code), r.status_code >= 500


def _error_greenapi(codigo: int) -> str:
    return {401: "GREEN-API 401: ID de instancia o token incorrectos",
            403: "GREEN-API 403: instancia sin permiso (¿no autorizada o vencida?)",
            466: "GREEN-API 466: se agotó el límite del plan Developer (máx. 3 chats al mes)",
            429: "GREEN-API 429: demasiadas peticiones, intenta en un momento"}.get(
        codigo, f"GREEN-API HTTP {codigo}: revisa la instancia (¿QR escaneado?) y el número")


def mensaje_oportunidad(simbolo: str, fecha: str, cierre: float, pronostico: float, delta: float,
                        accion_regla: str, accion_final: str, aprobada: bool, justificacion: str | None) -> str:
    if accion_final == "COMPRAR" and aprobada:
        que_hizo = "El agente COMPRÓ en Paper (aprobado por el motor de riesgo)."
    elif accion_final == "COMPRAR":
        que_hizo = "El agente propuso comprar, pero el motor de riesgo lo rechazó."
    else:
        que_hizo = f"Señal de compra del modelo; el agente decidió {accion_final}."
    lineas = [
        f"Oportunidad de compra: {simbolo}",
        f"Vela {fecha} (cierre 18:00 CDMX)",
        f"Pronóstico: {pronostico * 100:+.2f} % (umbral {delta * 100:.2f} %)",
        f"Precio de cierre: {cierre:,.6g} USDT",
        que_hizo,
    ]
    if justificacion:
        lineas.append(f"Motivo: {justificacion[:160]}")
    lineas.append("Modo Paper: no se envían órdenes reales.")
    return "\n".join(lineas)
