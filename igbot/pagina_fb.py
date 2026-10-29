"""La página de Facebook de una cuenta: Messenger, comentarios de la página y publicar en ella.

Se conecta desde el panel con el login de Facebook (ver facebook.py). Su id y su token de página se
guardan en la base de la cuenta (fb_page_id, fb_page_token, fb_page_name); el token de página que sale
de un token de usuario de 60 días no vence.

Quien escribe por Messenger se guarda con el prefijo "fb:" (fb:<id en la página>), así su chat no se
mezcla con los de Instagram, y al contestarle Instagram.send_text() lo manda por aquí.
"""
import json
import logging

import httpx

from . import config
from .store import Store

log = logging.getLogger("igbot.pagina_fb")

FB_GRAPH = "https://graph.facebook.com"
PREFIJO = "fb:"
# Avisos de la página: mensajes, botones, ecos (lo que el dueño contesta a mano desde la bandeja) y comentarios.
CAMPOS = "messages,messaging_postbacks,message_echoes,feed"


def es_de_facebook(user_id: str) -> bool:
    return user_id.startswith(PREFIJO)


def de_facebook(psid: str) -> str:
    return PREFIJO + psid


async def conectada(store: Store) -> bool:
    return bool(await store.get("fb_page_id") and await store.get("fb_page_token"))


async def guardar(store: Store, page_id: str, nombre: str, token: str) -> None:
    await store.set("fb_page_id", page_id)
    await store.set("fb_page_name", nombre)
    await store.set("fb_page_token", token)
    if await store.get("fb_publicar") is None:
        await store.set("fb_publicar", "1")  # las campañas salen también en la página, salvo que lo apague


async def quitar(store: Store) -> None:
    for k in ("fb_page_id", "fb_page_name", "fb_page_token"):
        await store.set(k, "")


class Pagina:
    """Lo que se hace en la página. Tiene los mismos métodos que usa comentarios.handle() de Instagram
    (permalink, private_reply, public_reply, hide_comment), así los comentarios de la página siguen las mismas
    reglas que los de Instagram."""

    def __init__(self, store: Store, http: httpx.AsyncClient | None = None):
        self.store = store
        self.http = http or httpx.AsyncClient(timeout=15)

    async def _graph(self, method: str, path: str, json: dict | None = None, **params) -> dict:
        token = await self.store.get("fb_page_token")
        if not token:
            raise RuntimeError("La página de Facebook no está conectada")
        r = await self.http.request(method, f"{FB_GRAPH}/{config.API_VERSION}/{path}",
                                    params={**params, "access_token": token}, json=json)
        if r.is_error:
            try:
                message = r.json()["error"]["message"]
            except Exception:
                message = r.text[:300]
            raise RuntimeError(f"Facebook respondió {r.status_code}: {message}")
        return r.json()

    async def page_id(self) -> str:
        return await self.store.get("fb_page_id") or ""

    # --- Messenger ---

    async def enviar(self, recipient: dict, message: dict) -> None:
        if psid := recipient.get("id"):
            recipient = {"id": psid.removeprefix(PREFIJO)}
        data = await self._graph("POST", f"{await self.page_id()}/messages", json={
            "recipient": recipient, "message": message, "messaging_type": "RESPONSE"})
        # Igual que en Instagram: se anota para reconocer el eco y no confundirlo con el dueño.
        if mid := data.get("message_id"):
            await self.store.add_sent(mid)

    async def nombre(self, user_id: str) -> str:
        """Nombre de quien escribió por Messenger ("Ana López · Messenger")."""
        key = f"username:{user_id}"
        if cached := await self.store.get(key):
            return cached
        try:
            data = await self._graph("GET", user_id.removeprefix(PREFIJO), fields="first_name,last_name")
            name = " ".join(p for p in (data.get("first_name"), data.get("last_name")) if p)
        except Exception:
            log.warning("No se pudo saber el nombre de %s en Messenger", user_id)
            return "Cliente de Messenger"
        name = f"{name} · Messenger" if name else "Cliente de Messenger"
        await self.store.set(key, name)
        return name

    # --- comentarios de la página (mismos nombres que en Instagram) ---

    async def permalink(self, post_id: str) -> str:
        """El link de Instagram del post que se publicó también aquí: así un comentario en Facebook usa las
        mismas palabras clave que el de Instagram. Vacío si el post no salió desde el panel."""
        return await self.store.get(f"fb_de:{post_id}") or ""

    async def private_reply(self, comment_id: str, text: str) -> None:
        """Un mensaje por Messenger a quien comentó (uno por comentario, dentro de 7 días)."""
        from .instagram import split_text
        await self.enviar({"comment_id": comment_id}, {"text": split_text(text)[0]})

    async def public_reply(self, comment_id: str, text: str) -> None:
        await self._graph("POST", f"{comment_id}/comments", message=text[:2000])

    async def hide_comment(self, comment_id: str) -> None:
        await self._graph("POST", comment_id, is_hidden="true")

    # --- avisos y publicar ---

    async def suscribir(self) -> None:
        """Que Meta mande al webhook los mensajes de Messenger y los comentarios de la página."""
        await self._graph("POST", f"{await self.page_id()}/subscribed_apps",
                          subscribed_fields=CAMPOS)
        log.info("Página de Facebook suscrita a mensajes y comentarios")

    async def publicar(self, image_urls: list[str], texto: str) -> str:
        """Publica una foto (o varias en un mismo post) y devuelve el id del post."""
        page = await self.page_id()
        if len(image_urls) == 1:
            data = await self._graph("POST", f"{page}/photos", url=image_urls[0], message=texto)
            return data.get("post_id") or data.get("id", "")
        fotos = []
        for url in image_urls:  # se suben sin publicar y luego van juntas en un post
            fotos.append((await self._graph("POST", f"{page}/photos", url=url, published="false"))["id"])
        data = await self._graph("POST", f"{page}/feed", json={
            "message": texto, "attached_media": [{"media_fbid": f} for f in fotos]})
        return data.get("id", "")


def texto_para_facebook(caption: str, sitio_web: str = "") -> str:
    """En Facebook los enlaces sí se pueden tocar: si el negocio tiene sitio web y el texto no lo trae,
    va al final."""
    sitio_web = sitio_web.strip()
    if sitio_web and sitio_web.removeprefix("https://").removeprefix("http://") not in caption:
        return f"{caption.rstrip()}\n\n{sitio_web}"
    return caption


async def publicar_tambien(store: Store, image_urls: list[str], caption: str, ig_link: str) -> str:
    """Después de publicar en Instagram: lo mismo en la página, si está conectada y no lo apagó.
    Si falla, el post de Instagram no se toca (solo queda en el registro)."""
    try:
        if not await conectada(store) or await store.get("fb_publicar") == "0":
            return ""
        from .empresa import CLAVE  # el Perfil de empresa, del que sale su sitio web
        sitio = json.loads(await store.get(CLAVE) or "{}").get("sitio_web", "")
        async with httpx.AsyncClient(timeout=30) as http:
            post_id = await Pagina(store, http).publicar(image_urls, texto_para_facebook(caption, sitio))
    except Exception:
        log.exception("Publicado en Instagram, pero no en la página de Facebook")
        return ""
    if post_id and ig_link:
        await store.set(f"fb_de:{post_id}", ig_link)
    log.info("También en Facebook: %s", post_id)
    return post_id
