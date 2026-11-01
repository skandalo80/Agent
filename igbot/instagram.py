"""Envío de DMs y renovación automática del token de Instagram.

Una cuenta se conecta de dos formas: con el login de Instagram (lo normal; token de Instagram, en
graph.instagram.com) o con el login de Facebook (IG_VIA=facebook en su .env; el token es el de su página
y todo va por graph.facebook.com con el id de su Instagram en lugar de "me"). Con "Conectar con Meta" sin página
(IG_VIA=sistema) el token es de usuario del sistema de su portafolio y todo, mensajes incluidos, va por
graph.facebook.com con el id de su Instagram. Los métodos son los mismos.
"""
import asyncio
import hashlib
import logging
import time

import httpx

from . import config
from .cuenta import cuenta
from .pagina_fb import CAMPOS as PAGINA_CAMPOS, FB_GRAPH, Pagina, es_de_facebook
from .store import Store

log = logging.getLogger("igbot.instagram")

GRAPH = "https://graph.instagram.com"
MAX_CHARS = 1000  # límite de Instagram por mensaje de texto
REFRESH_EVERY = 7 * 24 * 3600  # el token dura 60 días; lo renovamos cada semana
# Con el login de Facebook, "me" es la página: estas rutas son de la página (mensajes, botones de inicio y
# avisos); las demás (publicaciones, estadísticas, datos de la cuenta) van con el id del Instagram.
DE_LA_PAGINA = ("me/messages", "me/messenger_profile", "me/subscribed_apps")


def por_facebook() -> bool:
    return cuenta().ig_via == "facebook"


def sin_pagina() -> bool:
    return cuenta().ig_via == "sistema"


def api(path: str) -> str:
    """La dirección de la API para `path`, según cómo se conectó la cuenta."""
    if sin_pagina():
        if path == "me" or path.startswith("me/"):
            path = cuenta().ig_account_id + path[2:]
        return f"{FB_GRAPH}/{config.API_VERSION}/{path}"
    if not por_facebook():
        return f"{GRAPH}/{config.API_VERSION}/{path}"
    if (path == "me" or path.startswith("me/")) and not path.startswith(DE_LA_PAGINA):
        path = cuenta().ig_account_id + path[2:]
    return f"{FB_GRAPH}/{config.API_VERSION}/{path}"


class Instagram:
    def __init__(self, store: Store):
        self.store = store
        self.http = httpx.AsyncClient(timeout=15)
        self.pagina = Pagina(store, self.http)  # Messenger, si conectó su página de Facebook

    async def close(self) -> None:
        await self.http.aclose()

    async def token(self) -> str:
        # Si en .env se pegó un token distinto al de la última vez (p. ej. uno
        # nuevo con más permisos), manda ese y deja atrás el renovado viejo.
        env_hash = hashlib.sha256(cuenta().access_token.encode()).hexdigest()
        if await self.store.get("ig_token_env") != env_hash:
            await self.store.set("ig_token", cuenta().access_token)
            await self.store.set("ig_token_env", env_hash)
            await self.store.set("ig_token_refreshed_at", str(time.time()))
        return await self.store.get("ig_token") or cuenta().access_token

    async def _send(self, recipient_id: str, message: dict) -> None:
        await self._send_to({"id": recipient_id}, message)

    async def _send_to(self, recipient: dict, message: dict) -> None:
        if es_de_facebook(recipient.get("id", "")):  # escribió por Messenger
            await self.pagina.enviar(recipient, message)
            return
        r = await self.http.post(
            api("me/messages"),
            headers={"Authorization": f"Bearer {await self.token()}"},
            json={"recipient": recipient, "message": message},
        )
        if r.is_error:
            log.error("Meta no aceptó el mensaje (%s): %s", r.status_code, r.text[:500])
        r.raise_for_status()
        # Se anota el id para reconocer el eco y no confundirlo con el dueño.
        if mid := r.json().get("message_id"):
            await self.store.add_sent(mid)

    async def send_text(self, recipient_id: str, text: str) -> None:
        for chunk in split_text(text):
            await self._send(recipient_id, {"text": chunk})
        log.info("Respondido a %s", recipient_id)

    async def send_file(self, recipient_id: str, url: str) -> None:
        """Manda un PDF (u otro archivo) que Instagram descarga de `url`."""
        await self._send(
            recipient_id, {"attachment": {"type": "file", "payload": {"url": url}}}
        )

    async def send_image(self, recipient_id: str, url: str) -> None:
        await self._send(recipient_id, {"attachment": {"type": "image", "payload": {"url": url}}})

    async def private_reply(self, comment_id: str, text: str) -> None:
        """DM a quien comentó (Instagram permite uno por comentario, dentro de 7 días)."""
        await self._send_to({"comment_id": comment_id}, {"text": split_text(text)[0]})

    async def public_reply(self, comment_id: str, text: str) -> None:
        r = await self.http.post(
            api(f"{comment_id}/replies"),
            headers={"Authorization": f"Bearer {await self.token()}"},
            json={"message": text[:2000]},
        )
        r.raise_for_status()

    async def texto_mencion(self, media_id: str, comment_id: str = "") -> str:
        """El comentario (o el texto del post) de otra cuenta donde mencionaron a esta (aviso "mentions")."""
        if comment_id:
            d = await self._graph("GET", "me", fields=f"mentioned_comment.comment_id({comment_id}){{text}}")
            return str((d.get("mentioned_comment") or {}).get("text") or "")
        d = await self._graph("GET", "me", fields=f"mentioned_media.media_id({media_id}){{caption}}")
        return str((d.get("mentioned_media") or {}).get("caption") or "")

    async def contestar_mencion(self, media_id: str, comment_id: str, text: str) -> None:
        """Responde en el post (o el comentario) de otra cuenta donde mencionaron a esta."""
        datos = {"media_id": media_id, "message": text[:2000]}
        if comment_id:
            datos["comment_id"] = comment_id
        await self._graph("POST", f"{await self.account_id()}/mentions", **datos)

    async def hide_comment(self, comment_id: str) -> None:
        await self._graph("POST", comment_id, hide="true")

    async def set_ice_breakers(self, questions: list[str]) -> None:
        """Botones de inicio: preguntas que salen al abrir un chat nuevo (máximo 4)."""
        r = await self.http.post(
            api("me/messenger_profile"),
            headers={"Authorization": f"Bearer {await self.token()}"},
            json={"platform": "instagram", "ice_breakers": [{
                "locale": "default",
                "call_to_actions": [{"question": q, "payload": f"boton_{i}"} for i, q in enumerate(questions, 1)],
            }]},
        )
        if r.is_error:
            raise RuntimeError(f"Instagram respondió {r.status_code}: {r.text[:300]}")

    async def permalink(self, media_id: str) -> str:
        key = f"permalink:{media_id}"
        if cached := await self.store.get(key):
            return cached
        r = await self.http.get(
            api(media_id),
            params={"fields": "permalink"},
            headers={"Authorization": f"Bearer {await self.token()}"},
        )
        r.raise_for_status()
        link = r.json().get("permalink", "")
        await self.store.set(key, link)
        if link:  # para leer sus Insights después (Campañas > reporte)
            await self.store.set(f"media_de:{link}", media_id)
        return link

    # Qué métricas da Instagram según el formato (pedir una que no aplica tira toda la llamada): los posts
    # dan además visitas al perfil y seguidores que dejó; los reels no; las historias dan respuestas.
    METRICAS_POST = "reach,views,likes,comments,saved,shares,total_interactions,profile_visits,follows"
    METRICAS_REEL = "reach,views,likes,comments,saved,shares,total_interactions"
    METRICAS_HISTORIA = "reach,views,replies,shares,profile_visits,follows"

    async def media_insights(self, media_id: str, tipo: str = "post") -> dict[str, int]:
        """Alcance, vistas, likes, comentarios, guardados, compartidos (y visitas al perfil y seguidores en posts
        e historias; respuestas en historias) de una publicación propia. Necesita instagram_business_manage_insights."""
        metricas = {"historia": self.METRICAS_HISTORIA, "reel": self.METRICAS_REEL}.get(tipo, self.METRICAS_POST)
        data = await self._graph("GET", f"{media_id}/insights", metric=metricas)
        return {m["name"]: (m.get("values") or [{}])[0].get("value", 0) for m in data.get("data", [])}

    async def numeros_cuenta(self) -> dict[str, int]:
        """Seguidores, seguidos y publicaciones de la cuenta, en este momento."""
        data = await self._graph("GET", "me", fields="followers_count,follows_count,media_count")
        return {k: int(data[k]) for k in ("followers_count", "follows_count", "media_count") if k in data}

    async def insights_cuenta(self, metric: str, **params) -> list[dict]:
        """Insights de la cuenta (alcance, vistas, seguidores ganados, público...), tal como los da
        Instagram: una lista con name y total_value. Necesita instagram_business_manage_insights."""
        return (await self._graph("GET", "me/insights", metric=metric, **params)).get("data", [])

    async def recent_media(self, limit: int = 12) -> list[dict]:
        """Las últimas publicaciones de la cuenta (las suba quien las suba)."""
        data = await self._graph("GET", "me/media", limit=limit,
                                 fields="id,caption,media_type,media_url,thumbnail_url,permalink,timestamp")
        return data.get("data", [])

    MEDIA_FIELDS = "id,caption,media_type,media_product_type,media_url,thumbnail_url,permalink,timestamp"

    async def media_page(self, limit: int, after: str = "", fields: str = MEDIA_FIELDS) -> tuple[list[dict], str]:
        """Una página de las publicaciones de la cuenta, de la más nueva a la más vieja, y el
        cursor para pedir la siguiente ("" si ya no hay más)."""
        data = await self._graph("GET", "me/media", limit=limit, fields=fields, **({"after": after} if after else {}))
        paging = data.get("paging", {})
        return data.get("data", []), paging.get("cursors", {}).get("after", "") if paging.get("next") else ""

    async def media_count(self) -> int:
        """Cuántas publicaciones tiene la cuenta."""
        return int((await self._graph("GET", "me", fields="media_count")).get("media_count", 0))

    async def media_basico(self, media_id: str) -> dict:
        """Foto, texto, enlace y fecha de un post (Resultados > Comentarios)."""
        return await self._graph("GET", media_id, fields="caption,media_type,media_product_type,media_url,"
                                 "thumbnail_url,permalink,timestamp")

    async def media_info(self, media_id: str) -> dict:
        return await self._graph("GET", media_id, fields="id,caption")

    async def subscribe(self, fields: str) -> None:
        """Suscribe esta cuenta a los avisos del webhook (p. ej. 'messages,comments')."""
        if por_facebook():
            # Se suscribe la página (los de Instagram llegan por la suscripción de la app a "instagram").
            fields = PAGINA_CAMPOS
        r = await self.http.post(
            api("me/subscribed_apps"),
            params={"subscribed_fields": fields},
            headers={"Authorization": f"Bearer {await self.token()}"},
        )
        if r.is_error:
            log.error("Meta no suscribió la cuenta (%s): %s", r.status_code, r.text[:500])
        r.raise_for_status()
        log.info("Cuenta suscrita a: %s", fields)

    async def account_id(self) -> str:
        """Id de la cuenta de este token (el mismo que viene en los avisos de Meta)."""
        if por_facebook():  # el token es de la página: su Instagram es el enlazado a ella
            r = await self.http.get(f"{FB_GRAPH}/{config.API_VERSION}/me",
                                    params={"fields": "instagram_business_account{id,username}"},
                                    headers={"Authorization": f"Bearer {await self.token()}"})
            r.raise_for_status()
            data = r.json().get("instagram_business_account") or {}
            if data.get("username"):
                await self.store.set("account_username", data["username"])
            return str(data.get("id", ""))
        r = await self.http.get(
            api("me"),
            params={"fields": "user_id,username"},
            headers={"Authorization": f"Bearer {await self.token()}"},
        )
        r.raise_for_status()
        data = r.json()
        log.info("Cuenta de Instagram: @%s (%s)", data.get("username"), data["user_id"])
        if data.get("username"):
            await self.store.set("account_username", data["username"])
        return str(data["user_id"])

    async def username(self, user_id: str) -> str:
        """@usuario de alguien que le escribió a la cuenta (o su id si no se puede saber)."""
        if es_de_facebook(user_id):
            return await self.pagina.nombre(user_id)
        key = f"username:{user_id}"
        if cached := await self.store.get(key):
            return cached
        try:
            r = await self.http.get(
                api(user_id),
                params={"fields": "username"},
                headers={"Authorization": f"Bearer {await self.token()}"},
            )
            r.raise_for_status()
            name = "@" + r.json()["username"]
        except Exception:
            log.warning("No se pudo saber el usuario de %s", user_id)
            return user_id
        await self.store.set(key, name)
        return name

    async def download(self, url: str) -> bytes:
        """Descarga un adjunto de un DM (los links de Instagram caducan en horas)."""
        r = await self.http.get(url, follow_redirects=True, timeout=60)
        r.raise_for_status()
        return r.content

    # --- publicar en la cuenta (permiso instagram_business_content_publish) ---

    async def _graph(self, method: str, path: str, **data) -> dict:
        r = await self.http.request(
            method, api(path),
            headers={"Authorization": f"Bearer {await self.token()}"},
            **({"data": data} if method == "POST" else {"params": data}),
        )
        if r.is_error:
            try:
                message = r.json()["error"]["message"]
            except Exception:
                message = r.text[:300]
            raise RuntimeError(f"Instagram respondió {r.status_code}: {message}")
        return r.json()

    async def publish(self, image_urls: list[str], caption: str) -> str:
        """Publica una foto (o un carrusel de hasta 10) y devuelve el link de la publicación."""
        return (await self.publicar(image_urls, caption))[1]

    async def publish_story(self, image_url: str) -> str:
        """Publica una historia y devuelve su link, si Instagram lo da."""
        return (await self.publicar_historia(image_url))[1]

    async def publicar(self, image_urls: list[str], caption: str) -> tuple[str, str]:
        """Publica una foto (o un carrusel de hasta 10). Devuelve (id de Instagram, link).

        Instagram descarga cada foto de su URL, así que tienen que ser públicas y JPG.
        """
        if len(image_urls) == 1:
            container = await self._graph("POST", "me/media", image_url=image_urls[0], caption=caption)
        else:
            children = []
            for url in image_urls:
                child = await self._graph("POST", "me/media", image_url=url, is_carousel_item="true")
                children.append(child["id"])
            for child in children:
                await self._wait_ready(child)
            container = await self._graph(
                "POST", "me/media", media_type="CAROUSEL", children=",".join(children), caption=caption
            )
        await self._wait_ready(container["id"])
        media = await self._graph("POST", "me/media_publish", creation_id=container["id"])
        ig_id = str(media.get("id", ""))
        try:
            return ig_id, await self.permalink(ig_id)
        except Exception:
            log.exception("Publicado (%s), pero no se pudo obtener el link", ig_id)
            return ig_id, ""

    async def publicar_historia(self, image_url: str) -> tuple[str, str]:
        """Publica una historia (una foto 9:16). Devuelve (id de Instagram, link si lo da). Mismo permiso que
        los posts; las historias no llevan texto publicado (caption) y duran 24 horas. El id es el que llega
        en reply_to.story cuando alguien le responde."""
        container = await self._graph("POST", "me/media", image_url=image_url, media_type="STORIES")
        await self._wait_ready(container["id"])
        media = await self._graph("POST", "me/media_publish", creation_id=container["id"])
        ig_id = str(media.get("id", ""))
        try:
            return ig_id, await self.permalink(ig_id)
        except Exception:
            log.warning("Historia publicada (%s), sin link", ig_id)
            return ig_id, ""

    async def _wait_ready(self, container_id: str, tries: int = 20, every: float = 3) -> None:
        for _ in range(tries):
            status = (await self._graph("GET", container_id, fields="status_code")).get("status_code")
            if status in (None, "FINISHED"):
                return
            if status in ("ERROR", "EXPIRED"):
                raise RuntimeError(f"Instagram no pudo procesar la foto (estado {status})")
            await asyncio.sleep(every)
        raise RuntimeError("Instagram tardó demasiado en procesar la foto")

    async def refresh_token(self) -> None:
        r = await self.http.get(
            f"{GRAPH}/refresh_access_token",
            params={"grant_type": "ig_refresh_token", "access_token": await self.token()},
        )
        r.raise_for_status()
        data = r.json()
        await self.store.set("ig_token", data["access_token"])
        await self.store.set("ig_token_refreshed_at", str(time.time()))
        days = int(data.get("expires_in", 0)) // 86400
        log.info("Token de Instagram renovado; vence en %d días", days)

    async def refresh_if_due(self) -> None:
        """Renueva el token si ya pasó una semana (lo llama el bucle de fondo de app.py)."""
        if por_facebook() or sin_pagina():
            return  # el token de una página o de un usuario del sistema no vence
        last = float(await self.store.get("ig_token_refreshed_at") or 0)
        if time.time() - last > REFRESH_EVERY:
            await self.refresh_token()


def split_text(text: str, limit: int = MAX_CHARS) -> list[str]:
    """Parte un texto largo en trozos, cortando en saltos de línea o espacios."""
    chunks = []
    text = text.strip()
    while len(text) > limit:
        cut = text.rfind("\n", 0, limit)
        if cut < limit // 2:
            cut = text.rfind(" ", 0, limit)
        if cut < limit // 2:
            cut = limit
        chunks.append(text[:cut].strip())
        text = text[cut:].strip()
    if text:
        chunks.append(text)
    return chunks
