"""Corre ya los sugeridos de cada lunes (igbot/sugeridos.py), sin esperar al lunes.

    .venv/bin/python scripts/sugeridos_ahora.py            # todas las cuentas con contenido
    .venv/bin/python scripts/sugeridos_ahora.py skandar    # solo esa (el slug; "principal" = la principal)

Una llamada a Claude por cuenta. Reemplaza los sugeridos que tenga (los del lunes siguiente se escriben igual).
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from igbot import ajustes, sugeridos  # noqa: E402
from igbot.cuenta import cargar, usar  # noqa: E402
from igbot.instagram import Instagram  # noqa: E402
from igbot.store import Store  # noqa: E402


async def una(c, slug: str) -> None:
    await ajustes.cargar(c)
    if c.imagenes_mes <= 0:
        print(f"{slug}: su plan no tiene contenido, se omite")
        return
    with usar(c):
        listo = await sugeridos.generar(c)
        datos = await sugeridos.de(c)
    print(f"{slug}: " + (f"{len(datos.get('objetivos', []))} objetivos y "
                         f"{sum(len(v) for v in datos.get('mensajes', {}).values())} mensajes sugeridos" if listo
                         else "sin sugeridos nuevos (falta el Perfil de empresa o sus opciones, o Claude falló)"))


async def main(solo: str) -> None:
    for c in cargar():
        slug = c.slug or "principal"
        if solo and slug != solo:
            continue
        c.store = Store(c.db_path)  # como en app.py: cargar() no abre la base de cada cuenta
        c.instagram = Instagram(c.store)
        await c.store.open()
        try:
            await una(c, slug)
        finally:
            await c.instagram.close()
            await c.store.close()


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else ""))
