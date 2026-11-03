"""Pasa el catalogo.yaml de antes de la cuenta principal a su panel (una sola vez, en el deploy).

    cd ~/ig-bot && .venv/bin/python scripts/catalogo_a_tu_empresa.py ~/respaldos/catalogo-AAAAMMDD.yaml

Los planes quedan como productos en Tu empresa (uno por plan y forma de pago, y el bloque de respuestas
extra), la vigencia y las condiciones en la cotización de Configura tu asistente (encendida), y la guía de la
demo en vivo en "Algo más que deba saber". No borra ni cambia lo que ya exista: un producto con el mismo
nombre se deja como está, y lo que ya esté en las instrucciones no se repite. Al final vuelve a escribir las
instrucciones del asistente. Se puede correr otra vez sin duplicar nada.
"""
import asyncio
import json
import secrets
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from igbot import cotizacion, empresa, marco, panel  # noqa: E402
from igbot.cuenta import cuenta  # noqa: E402
from igbot.store import Store  # noqa: E402


def _producto(nombre: str, precio: float, moneda: str, descripcion: str) -> dict:
    return {"id": secrets.token_hex(4), "nombre": nombre[:80], "descripcion": descripcion[:800],
            "precio": f"{precio:g}", "moneda": moneda if moneda in empresa.MONEDAS else "MXN", "desde": False,
            "imagenes": [], "opciones": [], "url": "", "tipo": "servicio", "destacado": False}


def convertir(cat: dict) -> tuple[list[dict], dict, str]:
    """(productos, ajustes de la cotización, guía de la demo) a partir del catálogo de antes."""
    moneda, meses = cat.get("moneda", "MXN"), int(cat.get("meses_pagados_en_anual") or 12)
    productos = []
    for p in (cat.get("paquetes") or {}).values():
        incluye = "Incluye: " + "; ".join(p.get("incluye") or []) + "." if p.get("incluye") else ""
        base = " ".join(x for x in (str(p.get("resumen", "")).strip().capitalize() + ".", incluye) if x != ".")
        respuestas = f" {int(p['respuestas_mes']):,} respuestas al mes." if p.get("respuestas_mes") else ""
        productos.append(_producto(f"{p['nombre']}, pago mensual", p["mensualidad"], moneda, base + respuestas))
        productos.append(_producto(f"{p['nombre']}, pago anual", p["mensualidad"] * meses, moneda,
                                   f"12 meses de servicio por {meses} mensualidades.{respuestas} {base}"))
    if extra := (cat.get("extras") or {}).get("respuestas"):
        productos.append(_producto(extra["descripcion"], extra["precio"], moneda, "Se cobra cada mes."))
    condiciones = " ".join(cat.get("condiciones") or [])
    dias = int(cat.get("vigencia_dias") or 15)
    ajustes = cotizacion.ajustes({"cotizacion": {"on": True, "dias": dias if dias in cotizacion.DIAS else 15,
                                                 "condiciones": condiciones}})
    giros = (cat.get("demo") or {}).get("giros") or {}
    demo = ("Demo en vivo dentro del mismo DM: pregunta a qué se dedica su negocio y contesta unos mensajes como lo"
            " haría su asistente, con datos de ejemplo. Guía por giro (no es un guion): "
            + "; ".join(f"{g}: {que}" for g, que in giros.items()) + ".") if giros else ""
    return productos, ajustes, demo


async def main(ruta: str) -> None:
    cat = yaml.safe_load(Path(ruta).read_text(encoding="utf-8"))
    productos, ajustes, demo = convertir(cat)
    c = cuenta()
    c.store = Store(c.db_path)
    await c.store.open()
    try:
        actuales = await empresa.productos(c)
        nombres = {p.get("nombre") for p in actuales}
        nuevos = [p for p in productos if p["nombre"] not in nombres][:max(0, empresa.MAX_PRODUCTOS - len(actuales))]
        await c.store.set(empresa.CLAVE_PRODUCTOS, json.dumps(actuales + nuevos, ensure_ascii=False))
        perfil = await marco.perfil(c)
        if not perfil.get("cotizacion"):
            perfil["cotizacion"] = ajustes
        if demo and demo not in perfil.get("instrucciones", ""):
            perfil["instrucciones"] = "\n\n".join(x for x in (perfil.get("instrucciones", "").strip(), demo) if x)[
                :panel.MAX_INSTRUCCIONES]
        await c.store.set("perfil", json.dumps(perfil, ensure_ascii=False))
        prompt = panel.generar(await marco.usuario(c), perfil, await panel.negocio_de_empresa(c))
        c.prompt_destino().parent.mkdir(parents=True, exist_ok=True)
        c.prompt_destino().write_text(prompt, encoding="utf-8")
        print(f"Listo: {len(nuevos)} productos nuevos en Tu empresa, cotización "
              f"{'encendida' if perfil['cotizacion'].get('on') else 'como estaba'}, instrucciones al día.")
    finally:
        await c.store.close()


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    asyncio.run(main(sys.argv[1]))
