"""Prueba las plantillas de contenido y la conexión con OpenAI.

    # Sin gastar: rotula una foto tuya con las 4 plantillas
    .venv/bin/python scripts/probar_imagen.py --foto producto.jpg

    # Con OpenAI (1 imagen, ~US$0.05): genera la imagen y la rotula con las 4
    .venv/bin/python scripts/probar_imagen.py --idea "Cartera de piel sobre mesa de granito"

Deja los JPG en data/pruebas/ (o en --salida). No cuenta para el cupo de ninguna cuenta.
"""
import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from igbot import imagenes  # noqa: E402


def main():
    p = argparse.ArgumentParser()
    fuente = p.add_mutually_exclusive_group(required=True)
    fuente.add_argument("--foto", help="imagen propia (no llama a OpenAI)")
    fuente.add_argument("--idea", help="descripción de la imagen para OpenAI")
    p.add_argument("--titulo", default="Cartera Atelier")
    p.add_argument("--subtitulo", default="Piel italiana, hecha a mano en Guadalajara.")
    p.add_argument("--precio", default="Desde $1,290 MXN")
    p.add_argument("--firma", default="@skandar.online")
    p.add_argument("--acento", default=imagenes.ACENTO)
    p.add_argument("--fuente", default="Inter", choices=list(imagenes.FUENTES))
    p.add_argument("--peso", type=int, default=600, choices=list(imagenes.PESOS))
    p.add_argument("--salida", default="data/pruebas")
    a = p.parse_args()

    if a.foto:
        base = Path(a.foto).read_bytes()
    else:
        print(f"Generando con {imagenes.proveedor.model}...")
        base = asyncio.run(imagenes.proveedor.generar(imagenes.prompt_final(a.idea, "editorial")))
    salida = Path(a.salida)
    salida.mkdir(parents=True, exist_ok=True)
    if a.idea:
        (salida / "base.jpg").write_bytes(imagenes._jpg(base))
    texto = imagenes.Texto(a.titulo, a.subtitulo, a.precio, a.firma)
    marca = imagenes.Marca(fuente=a.fuente, peso=a.peso, acento=a.acento)
    for plantilla in imagenes.PLANTILLAS:
        destino = salida / f"{plantilla}.jpg"
        destino.write_bytes(imagenes.rotular(base, plantilla, texto, marca))
        print(destino)


if __name__ == "__main__":
    main()
