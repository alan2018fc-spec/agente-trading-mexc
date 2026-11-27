"""Avisos por WhatsApp: se envían por oportunidad de compra, una sola vez, y nunca afectan al trading."""
from __future__ import annotations

import httpx

from app.notificador import NotificadorWhatsApp
from conftest import correr

APIKEY = "1234567"


class FalsoCallMeBot:
    def __init__(self, modo="ok"):
        self.modo, self.mensajes = modo, []

    def __call__(self, req: httpx.Request) -> httpx.Response:
        if self.modo == "caido":
            raise httpx.ConnectError("sin red", request=req)
        q = dict(req.url.params)
        if q.get("apikey") != APIKEY:
            return httpx.Response(200, text="APIKey is invalid")
        self.mensajes.append(q["text"])
        return httpx.Response(200, text="Message queued. You will receive it in a few seconds.")


def _notif(falso, apikey=APIKEY, telefono="+5215512345678"):
    return NotificadorWhatsApp(telefono, apikey, transporte=httpx.MockTransport(falso), espera_inicial=0.01)


def _ciclo(ag):
    async def f():
        await ag.iniciar(); await ag.detener_bucle()
        return await ag.paso()
    return correr(f())


def test_avisa_cada_oportunidad_una_sola_vez(entorno):
    cmb = FalsoCallMeBot()
    ag = entorno["construir"](notificador=_notif(cmb))
    r = _ciclo(ag)
    esperadas = ag.db.fila("SELECT COUNT(*) c FROM decisiones WHERE modo='vivo' AND (accion_modelo='COMPRAR' OR accion_final='COMPRAR')")["c"]
    assert esperadas > 0 and len(cmb.mensajes) == esperadas
    assert all("Oportunidad de compra" in m and "Paper" in m for m in cmb.mensajes)
    assert {n["estado"] for n in ag.db.filas("SELECT estado FROM notificaciones")} == {"enviado"}
    correr(ag.ciclo_vivo(r.fecha_vela))                     # la misma vela otra vez: no se repiten avisos
    assert len(cmb.mensajes) == esperadas


def test_sin_configurar_no_bloquea_el_ciclo(entorno):
    ag = entorno["construir"](notificador=NotificadorWhatsApp("", ""))
    r = _ciclo(ag)
    assert r.decisiones == 15
    assert {n["estado"] for n in ag.db.filas("SELECT estado FROM notificaciones")} <= {"sin_configurar"}


def test_fallo_de_whatsapp_no_afecta_el_trading(entorno):
    ag = entorno["construir"](notificador=_notif(FalsoCallMeBot("caido")))
    r = _ciclo(ag)
    assert r.decisiones == 15 and ag.estado == "ACTIVO"
    assert ag.db.filas("SELECT * FROM notificaciones WHERE estado='error'")
    assert ag.db.filas("SELECT * FROM eventos WHERE tipo='aviso_fallido'")


def test_clave_invalida_se_reporta(entorno):
    ok, detalle = correr(_notif(FalsoCallMeBot(), apikey="mala").enviar("hola"))
    assert not ok and "clave" in detalle


def test_replay_no_manda_avisos(entorno):
    cmb = FalsoCallMeBot()
    ag = entorno["construir"](notificador=_notif(cmb))

    async def f():
        ag.lanzar_replay(5, usar_llm=False)
        await ag._tarea_replay
    correr(f())
    assert ag.replay["estado"] == "completo" and cmb.mensajes == []


def test_endpoint_de_prueba_y_claves_fuera_de_logs(entorno):
    from fastapi.testclient import TestClient
    from app.main import crear_app
    from app.llm import ClienteLLM
    from app.mexc import ClienteMEXC
    import app.config as cfg
    cmb = FalsoCallMeBot()
    app = crear_app(entorno["aj"], mexc=ClienteMEXC(transporte=httpx.MockTransport(entorno["mexc"])),
                    llm=ClienteLLM("http://gateway", entorno["aj"].llm_gateway_key, "qwen2.5:3b",
                                   transporte=httpx.MockTransport(entorno["llm"])), iniciar_bucle=False,
                    notificador=_notif(cmb))
    k = {"X-API-Key": entorno["aj"].backend_api_key}
    with TestClient(app) as c:
        assert c.post("/api/notificaciones/prueba").status_code == 401
        r = c.post("/api/notificaciones/prueba", headers=k)
        assert r.status_code == 200 and r.json()["destino"] == "…5678" and len(cmb.mensajes) == 1
        e = c.get("/api/estado", headers=k).json()
        assert e["whatsapp"] == {"configurado": True, "destino": "…5678", "proveedor": "callmebot"}
        assert APIKEY not in r.text and APIKEY not in c.get("/api/notificaciones", headers=k).text
    log = (cfg.BASE / "logs" / "backend.log").read_text(encoding="utf-8")
    assert APIKEY not in log


# ------------------------------------------------------------------ GREEN-API
TOKEN = "tok-secreto-abcdef123456"


class FalsoGreenAPI:
    def __init__(self, wid="5215512349876@c.us", codigo=200):
        self.wid, self.codigo, self.mensajes = wid, codigo, []

    def __call__(self, req: httpx.Request) -> httpx.Response:
        import json
        assert req.url.host == "7105.api.greenapi.com"
        partes = req.url.path.strip("/").split("/")          # waInstance{id} / metodo / token
        if partes[0] != "waInstance7105000001" or partes[2] != TOKEN:
            return httpx.Response(401, text="Unauthorized")
        if partes[1] == "getSettings":
            return httpx.Response(200, json={"wid": self.wid})
        if partes[1] == "sendMessage":
            if self.codigo != 200:
                return httpx.Response(self.codigo, text="error")
            cuerpo = json.loads(req.content)
            self.mensajes.append(cuerpo)
            return httpx.Response(200, json={"idMessage": "BAE5F4886F6F2D05"})
        return httpx.Response(404)


def _green(falso, token=TOKEN, telefono=""):
    return NotificadorWhatsApp(telefono, "", proveedor="greenapi", greenapi_url="https://7105.api.greenapi.com",
                               id_instance="7105000001", token=token, transporte=httpx.MockTransport(falso),
                               espera_inicial=0.01)


def test_greenapi_te_escribe_a_tu_propio_numero(entorno):
    g = FalsoGreenAPI()
    n = _green(g)
    assert n.configurado and n.destino == "tu número vinculado"
    ok, _ = correr(n.enviar("hola"))
    assert ok and g.mensajes == [{"chatId": "5215512349876@c.us", "message": "hola"}] and n.destino == "…9876"


def test_greenapi_con_telefono_explicito_y_errores(entorno):
    g = FalsoGreenAPI()
    ok, _ = correr(_green(g, telefono="+52 1 55 1234 5678").enviar("x"))
    assert ok and g.mensajes[0]["chatId"] == "5215512345678@c.us"
    ok, det = correr(_green(g, token="malo").enviar("x"))
    assert not ok and "401" in det and "malo" not in det
    ok, det = correr(_green(FalsoGreenAPI(codigo=466)).enviar("x"))
    assert not ok and "límite" in det
    ok, det = correr(_green(FalsoGreenAPI(wid="")).enviar("x"))
    assert not ok and "QR" in det


def test_greenapi_en_el_ciclo(entorno):
    g = FalsoGreenAPI()
    ag = entorno["construir"](notificador=_green(g))
    _ciclo(ag)
    assert g.mensajes and all("Oportunidad de compra" in m["message"] for m in g.mensajes)
    assert {n["estado"] for n in ag.db.filas("SELECT estado FROM notificaciones")} == {"enviado"}
