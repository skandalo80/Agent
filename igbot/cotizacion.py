"""Cotización formal en PDF con los productos de Tu empresa.

El dueño la enciende en Configura tu asistente > Reglas y límites (kv "perfil", clave "cotizacion": si está
encendida, cuántos días vale y sus condiciones). Los precios salen de Tu empresa > Productos y servicios y las
cuentas se hacen aquí, en código: Claude solo elige productos y cantidades, nunca escribe un número. Solo se
cotizan los productos con precio fijo (sin "Desde" y con un número en el precio).

El PDF lleva el nombre del negocio, su @ y su sitio, y el color principal de Tu marca. Se manda por este chat
(un enlace con token aleatorio, ver tools.quote_url) y, solo en las cuentas con el Google de la plataforma,
también por correo. Queda en la tabla quotes (Resultados, reporte semanal y la etapa Cotizó).
"""
import json
import re
import secrets
from dataclasses import dataclass
from datetime import date, timedelta
from html import escape
from pathlib import Path

from claude_agent_sdk import tool

from . import catalogo_chat, empresa, marco
from .cuenta import Cuenta
from .google import Google
from .instagram import Instagram
from .pdf_marca import Cotizacion
from .store import Store
from .tools import CANAL_DOC, check_quote_delivery, deliver_quote, error, now, ok

DIAS = (7, 15, 30)  # cuánto vale una cotización (lo elige el dueño)
MAX_CONDICIONES = 600
MAX_LINEAS, MAX_CANTIDAD = 20, 9999
PRECIO = re.compile(r"^\$?\s*(\d{1,3}(?:,\d{3})+|\d+)(\.\d{1,2})?$")
HEX = re.compile(r"^#[0-9A-Fa-f]{6}$")


# --- ajustes del panel ---

def ajustes(perfil: dict) -> dict:
    guardado = perfil.get("cotizacion") or {}
    return {"on": bool(guardado.get("on")),
            "dias": guardado.get("dias") if guardado.get("dias") in DIAS else 15,
            "condiciones": str(guardado.get("condiciones") or "")[:MAX_CONDICIONES]}


def del_form(f: dict) -> dict:
    dias = int(f["q_dias"]) if str(f.get("q_dias", "")).isdigit() else 15
    return ajustes({"cotizacion": {"on": f.get("q_on") == "1", "dias": dias,
                                   "condiciones": f.get("q_condiciones", "").strip()}})


def campos(perfil: dict, cotizables: int) -> str:
    """La tarjeta de Reglas y límites: interruptor, vigencia y condiciones (lo de abajo se ve al encender, CSS :has)."""
    a = ajustes(perfil)
    dias = "".join(f'<label><input type="radio" name="q_dias" value="{d}"{" checked" if d == a["dias"] else ""}>'
                   f" {d} días</label>" for d in DIAS)
    falta = ("" if cotizables else '<p class="aviso">Aún no tienes productos con precio fijo en Tu empresa. Agrégalos en'
             ' <a href="/panel/empresa?paso=3">Productos y servicios</a> para poder cotizar.</p>')
    return ('<div class="as-caja as-cot"><input type="hidden" name="q" value="1">'
            '<label class="as-fila"><span><b>Manda cotizaciones en PDF</b><small>Con los productos y precios de Tu'
            " empresa; tu asistente nunca calcula un precio por su cuenta.</small></span>"
            f'<input type="checkbox" class="as-sw" name="q_on" value="1"{" checked" if a["on"] else ""}'
            ' aria-label="Manda cotizaciones en PDF"></label>'
            f'<div class="as-cot-b">{falta}<label>Cada cotización vale</label><div class="chips">{dias}</div>'
            '<label for="q_condiciones">Condiciones</label>'
            f'<textarea id="q_condiciones" name="q_condiciones" rows="3" maxlength="{MAX_CONDICIONES}"'
            ' placeholder="Ej. Precios con IVA incluido. Anticipo del 50 % para apartar. Envío no incluido.">'
            f'{escape(a["condiciones"])}</textarea></div></div>')


def resumen(perfil: dict) -> str:
    a = ajustes(perfil)
    return f"Manda cotizaciones en PDF (valen {a['dias']} días)" if a["on"] else ""


ESTILO = (".as-cot-b{display:none;margin-top:1rem;border-top:1px solid var(--linea-oscura);padding-top:1rem}"
          ".as-cot:has(input[name=q_on]:checked) .as-cot-b{display:block}")


# --- precios y cuentas ---

def precio_fijo(p: dict) -> float | None:
    """El precio del producto como número, o None si no tiene uno fijo ("Desde", "a cotizar", vacío)."""
    if p.get("desde"):
        return None
    m = PRECIO.match(str(p.get("precio", "")).strip())
    return float(m.group(1).replace(",", "") + (m.group(2) or "")) if m else None


def cotizables(productos: list[dict]) -> list[dict]:
    return [p for p in productos if precio_fijo(p) is not None]


def dinero(cantidad: float, moneda: str) -> str:
    return f"${cantidad:,.2f} {moneda}"


@dataclass
class Pedido:
    lineas: list[tuple[str, int, float, float]]  # (producto, cantidad, precio unitario, importe)
    total: float
    moneda: str

    def texto(self) -> str:
        out = [f"{n} x {nombre}: {dinero(importe, self.moneda)}" for nombre, n, _, importe in self.lineas]
        return "\n".join(out + [f"Total: {dinero(self.total, self.moneda)}"])

    def resumen(self) -> str:
        return ", ".join(f"{n} {nombre}" for nombre, n, _, _ in self.lineas)[:200]


def calcular(productos: list[dict], items: list) -> Pedido:
    """El pedido con los precios de Tu empresa. Lanza ValueError con lo que hay que decirle a Claude."""
    validos = cotizables(productos)
    nombres = ", ".join(p.get("nombre", "") for p in validos[:30])
    if not isinstance(items, list) or not items:
        raise ValueError("Falta qué productos cotizar y cuántos de cada uno.")
    if len(items) > MAX_LINEAS:
        raise ValueError(f"Una cotización lleva hasta {MAX_LINEAS} productos distintos.")
    lineas, monedas = [], set()
    for item in items:
        item = item if isinstance(item, dict) else {}
        p = catalogo_chat.buscar(validos, str(item.get("producto", ""))[:120])
        if not p:
            raise ValueError(f"No encontré '{item.get('producto', '')}' con precio fijo. Se pueden cotizar: {nombres}."
                             " Si pide otra cosa, ofrece pasar con una persona.")
        try:
            n = int(item.get("cantidad", 0))
        except (TypeError, ValueError):
            n = 0
        if not 1 <= n <= MAX_CANTIDAD:
            raise ValueError(f"La cantidad de {p['nombre']} debe estar entre 1 y {MAX_CANTIDAD}.")
        unitario = precio_fijo(p)
        lineas.append((p["nombre"], n, unitario, round(unitario * n, 2)))
        monedas.add(p.get("moneda") or "MXN")
    if len(monedas) > 1:
        raise ValueError("Esos productos tienen precios en monedas distintas: cotízalos por separado.")
    return Pedido(lineas, round(sum(x[3] for x in lineas), 2), monedas.pop())


def pdf(pedido: Pedido, folio: str, negocio: str, contacto: str, acento: str, cliente: str, empresa_: str,
        email: str, hoy: date, dias: int, condiciones: str) -> bytes:
    doc = Cotizacion(negocio, contacto, folio, acento)
    doc.titulo("Cotización", negocio)
    para = ", ".join(x for x in (cliente, empresa_) if x) or "Cliente"
    doc.datos([("Para", "\n".join(x for x in (para, email) if x)), ("Fecha", f"{hoy:%d/%m/%Y}"),
               ("Válida hasta", f"{hoy + timedelta(days=dias):%d/%m/%Y}")])
    doc.tabla([(f"{n} x {nombre} ({dinero(unitario, pedido.moneda)} c/u)", dinero(importe, pedido.moneda))
               for nombre, n, unitario, importe in pedido.lineas])
    doc.totales([], ("Total", dinero(pedido.total, pedido.moneda)))
    puntos = [x.strip() for x in re.split(r"(?<=[.])\s+|\n", condiciones) if x.strip()]
    doc.seccion("Condiciones", puntos + [f"Precios en {pedido.moneda}."], chica=True)
    return doc.bytes()


# --- la cuenta ---

async def encendida(c: Cuenta) -> bool:
    """Si el asistente tiene las herramientas: la encendió el dueño y hay productos con precio fijo."""
    return ajustes(await marco.perfil(c))["on"] and bool(cotizables(await empresa.productos(c)))


async def _membrete(c: Cuenta) -> tuple[str, str, str]:
    """Nombre del negocio, línea de contacto (@ y sitio) y color de acento (el principal de Tu marca)."""
    e = await empresa.de(c)
    usuario = await marco.usuario(c)
    nombre = e.get("nombre") or usuario or "Cotización"
    contacto = "  ·  ".join(x for x in (f"@{usuario}" if usuario else "", e.get("sitio_web", "")) if x)
    principal = str(json.loads(await c.store.get("marca") or "{}").get("principal", ""))
    return nombre, contacto, principal if HEX.match(principal) else ""


def herramientas(c: Cuenta, store: Store, google: Google | None, instagram: Instagram | None, user_id: str,
                 state: dict) -> list:
    """`cotizar` y `enviar_cotizacion` para una cuenta con la cotización encendida. `google`: solo si la cuenta
    manda correos con el Google de la plataforma; si no, la cotización va solo por este chat."""
    items = {"type": "array", "maxItems": MAX_LINEAS, "items": {
        "type": "object", "properties": {"producto": {"type": "string"}, "cantidad": {"type": "integer"}},
        "required": ["producto", "cantidad"]}}

    @tool(
        "cotizar",
        "Calcula el total exacto de productos del catálogo para decírselo a la persona. items: cada producto con"
        " su nombre tal cual viene en el catálogo y la cantidad. Usa SIEMPRE esto para dar un total; nunca hagas"
        " cuentas tú.",
        {"type": "object", "properties": {"items": items}, "required": ["items"]},
    )
    async def cotizar(args):
        try:
            return ok(calcular(await empresa.productos(c), args.get("items")).texto())
        except ValueError as e:
            return error(str(e))

    @tool(
        "enviar_cotizacion",
        "Genera la cotización formal en PDF (con folio) de esos productos y se la manda a la persona. Antes"
        " confirma con ella los productos y las cantidades. " + CANAL_DOC
        + (" Solo se puede mandar por este chat: usa canal 'chat'." if not google else "")
        + " nombre y empresa son opcionales (cadena vacía si no los dio).",
        {"type": "object", "properties": {
            "items": items, "canal": {"type": "string"}, "email": {"type": "string"},
            "nombre": {"type": "string"}, "empresa": {"type": "string"}},
         "required": ["items", "canal", "email", "nombre", "empresa"]},
    )
    async def enviar_cotizacion(args):
        canal, email = str(args.get("canal", "")).strip().lower(), str(args.get("email", "")).strip()
        if canal != "chat" and not google:
            return error("Esta cuenta no manda correos: ofrece mandarla por este chat.")
        if problema := await check_quote_delivery(store, instagram, user_id, canal, email):
            return error(problema)
        try:
            pedido = calcular(await empresa.productos(c), args.get("items"))
        except ValueError as e:
            return error(str(e))
        a = ajustes(await marco.perfil(c))
        nombre, empresa_ = str(args.get("nombre", "")).strip()[:80], str(args.get("empresa", "")).strip()[:80]
        token = secrets.token_urlsafe(16)
        numero = await store.add_quote(user_id, token, pedido.resumen(), "", 0, pedido.total, email)
        folio = f"COT-{numero:04d}"
        negocio, contacto, acento = await _membrete(c)
        data = pdf(pedido, folio, negocio, contacto, acento, nombre, empresa_, email, now().date(), a["dias"],
                   a["condiciones"])
        carpeta = Path(c.quotes_dir)
        carpeta.mkdir(parents=True, exist_ok=True)
        (carpeta / f"{token}.pdf").write_bytes(data)
        asunto = f"Cotización {folio} · {negocio}"
        cuerpo = (f"Hola{' ' + nombre if nombre else ''}:\n\nTe comparto la cotización {folio} que platicamos por"
                  f" Instagram (va en el PDF adjunto).\n\n{pedido.texto()}\n\nEs válida por {a['dias']} días. Si"
                  f" tienes dudas, contesta este correo o escríbenos por Instagram.\n\nSaludos,\n{negocio}")
        enviada = await deliver_quote(store, google, instagram, user_id, canal, email, folio, token, data, asunto,
                                      cuerpo, c.mail_from_name or negocio)
        if not enviada:
            return error("No se pudo mandar la cotización en este momento. Ofrece intentarlo en un rato.")
        state["acted"] = True
        await store.avanzar_etapa(user_id, "cotizo")  # su etapa en Conversaciones
        return ok(f"Cotización {folio} enviada {enviada}.\n{pedido.texto()}")

    return [cotizar, enviar_cotizacion]
