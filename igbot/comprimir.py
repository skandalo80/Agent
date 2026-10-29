"""Comprime con gzip las respuestas completas (las páginas del panel pesan ~140 KB por su CSS).

Solo las que ya traen su tamaño (content-length): así no toca lo que va llegando por
partes, como el chat de prueba o los avisos en vivo, que deben salir al momento.
"""
import gzip

MINIMO = 1024
TIPOS = ("text/html", "text/css", "text/javascript", "application/javascript", "application/json", "image/svg+xml")


class Comprimir:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        acepta = dict(scope.get("headers", [])).get(b"accept-encoding", b"")
        if scope["type"] != "http" or scope["method"] == "HEAD" or b"gzip" not in acepta:
            return await self.app(scope, receive, send)
        inicio: dict | None = None
        partes: list[bytes] = []

        async def enviar(msg):
            nonlocal inicio
            if msg["type"] == "http.response.start":
                h = {k.lower(): v for k, v in msg.get("headers", [])}
                tipo = h.get(b"content-type", b"").decode("latin-1")
                largo = h.get(b"content-length", b"")
                if (msg["status"] == 200 and largo.isdigit() and int(largo) >= MINIMO and tipo.startswith(TIPOS)
                        and b"content-encoding" not in h):
                    inicio = msg  # se guarda hasta tener todo el cuerpo
                    return
                return await send(msg)
            if inicio is None:
                return await send(msg)
            partes.append(msg.get("body", b""))
            if msg.get("more_body"):
                return
            cuerpo = gzip.compress(b"".join(partes), compresslevel=5)
            cabeceras = [(k, v) for k, v in inicio.get("headers", []) if k.lower() != b"content-length"]
            cabeceras += [(b"content-length", str(len(cuerpo)).encode()), (b"content-encoding", b"gzip"),
                          (b"vary", b"Accept-Encoding")]
            await send({**inicio, "headers": cabeceras})
            await send({"type": "http.response.body", "body": cuerpo})

        await self.app(scope, receive, enviar)
