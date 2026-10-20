"""Genera una foto de ejemplo por estilo de foto (Tu marca > Estilo de tus fotos).

    .venv/bin/python scripts/ejemplos_estilo.py            # solo los que faltan
    .venv/bin/python scripts/ejemplos_estilo.py --todos    # los vuelve a generar

Son 4 imágenes de Gemini (~US$0.16 en total), una sola vez para todos los clientes.
Quedan en data/estilos/<estilo>.jpg; el panel las muestra en cada opción y en la vista
previa de la marca. No cuentan para el cupo de ninguna cuenta.
"""
import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from igbot import contenido, imagenes  # noqa: E402

ESCENA = ("A generic premium product on display: a ceramic coffee cup, a small leather notebook and a "
          "glass perfume bottle arranged together.")


async def main():
    p = argparse.ArgumentParser()
    p.add_argument("--todos", action="store_true", help="regenerar aunque ya existan")
    args = p.parse_args()
    contenido.EJEMPLOS.mkdir(parents=True, exist_ok=True)
    for estilo, (nombre, _, prompt) in contenido.ESTILOS.items():
        destino = contenido.EJEMPLOS / f"{estilo}.jpg"
        if destino.exists() and not args.todos:
            print(f"{nombre}: ya existe ({destino})")
            continue
        idea = f"{ESCENA}\n\nPhoto style: {prompt}"
        datos = await imagenes.proveedor.generar(imagenes.prompt_final(idea, imagenes.PLANTILLA_INICIAL))
        destino.write_bytes(imagenes._jpg(datos))
        print(f"{nombre}: {destino}")


if __name__ == "__main__":
    asyncio.run(main())
