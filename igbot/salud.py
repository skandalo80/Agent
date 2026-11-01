"""Salud del servicio: /panel/salud, solo para la cuenta principal (Skandar).

Los cuatro números que deciden cuándo crecer (ver el plan de arquitectura): la RAM del proceso y de los
subprocesos de Claude, el tiempo por respuesta de cada cuenta (mediana y percentil 95 de las últimas 24 h),
cuántas conversaciones están en curso y cuántas veces el bucle se trabó hoy. Además: cuentas y su estado,
trabajos de campaña en curso o fallidos, gasto del día por cuenta y el último respaldo. Todo se mide en el
proceso mismo (/proc), sin servicios de fuera. Lo que no se mide se ve como "sin datos".
"""
import asyncio
import collections
import logging
import os
import time
from html import escape
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from . import config, lentitud, limites, marco
from .cuenta import Cuenta

log = logging.getLogger("igbot.salud")

router = APIRouter(prefix="/panel")

VENTANA = 24 * config.HORA  # los tiempos de respuesta que se guardan (en memoria; se pierden al reiniciar)
MAXIMO = 2000  # por cuenta
_respuestas: dict[str, collections.deque] = collections.defaultdict(lambda: collections.deque(maxlen=MAXIMO))
RESPALDOS = Path.home() / "respaldos"

# Las pone app.py: de dónde salen las cuentas, sus colas y los trabajos de campaña.
fuentes: dict = {"cuentas": lambda: [], "dispatchers": lambda: {}, "trabajos": lambda: {}}


def anotar_respuesta(slug: str, seg: float, ahora: float | None = None) -> None:
    """Cuánto tardó una respuesta de DM (del primer mensaje pendiente al envío)."""
    _respuestas[slug].append((ahora or time.time(), seg))


def tiempos(slug: str, ahora: float | None = None) -> list[float]:
    desde = (ahora or time.time()) - VENTANA
    return [seg for t, seg in _respuestas.get(slug, ()) if t >= desde]


def percentil(valores: list[float], p: float) -> float:
    if not valores:
        return 0.0
    orden = sorted(valores)
    return orden[min(len(orden) - 1, int(round(p / 100 * (len(orden) - 1))))]


# --- memoria, leída de /proc ---

def _rss_kb(pid: int) -> int:
    try:
        with open(f"/proc/{pid}/status") as f:
            for linea in f:
                if linea.startswith("VmRSS:"):
                    return int(linea.split()[1])
    except OSError:
        pass
    return 0


def descendientes(pid: int) -> list[int]:
    """Los procesos que cuelgan de `pid` (los Claude Code CLI y lo que ellos abran)."""
    hijos: dict[int, list[int]] = collections.defaultdict(list)
    for p in os.listdir("/proc"):
        if not p.isdigit():
            continue
        try:
            with open(f"/proc/{p}/stat") as f:
                resto = f.read().rsplit(")", 1)[1].split()  # después del nombre (que puede traer espacios)
            hijos[int(resto[1])].append(int(p))
        except (OSError, IndexError, ValueError):
            continue
    todos, cola = [], [pid]
    while cola:
        for h in hijos.get(cola.pop(), ()):
            todos.append(h)
            cola.append(h)
    return todos


def memoria() -> dict:
    """RAM del proceso, de sus subprocesos y libre en la máquina, en MB (0 si /proc no está)."""
    yo = os.getpid()
    hijos = descendientes(yo)
    total = libre = 0
    try:
        with open("/proc/meminfo") as f:
            for linea in f:
                if linea.startswith("MemTotal:"):
                    total = int(linea.split()[1])
                elif linea.startswith("MemAvailable:"):
                    libre = int(linea.split()[1])
    except OSError:
        pass
    return {"proceso_mb": _rss_kb(yo) // 1024, "hijos": len(hijos), "hijos_mb": sum(_rss_kb(h) for h in hijos) // 1024,
            "libre_mb": libre // 1024, "total_mb": total // 1024}


def ultimo_respaldo(carpeta: Path = RESPALDOS) -> str:
    """La fecha de la carpeta de respaldo más reciente (AAAA-MM-DD), o ''."""
    try:
        fechas = sorted(p.name for p in carpeta.iterdir() if p.is_dir() and len(p.name) == 10)
    except OSError:
        return ""
    return fechas[-1] if fechas else ""


# --- los datos y la página ---

async def datos(ahora: float | None = None) -> dict:
    ahora = ahora or time.time()
    cuentas: list[Cuenta] = list(fuentes["cuentas"]())
    dispatchers = fuentes["dispatchers"]()
    trabajos = list(fuentes["trabajos"]().values())
    filas = []
    for c in cuentas:
        d = dispatchers.get(c.slug)
        t = tiempos(c.slug, ahora)
        esperando = len(await c.store.pending_users()) if c.store and c.store.db else 0
        filas.append({
            "slug": c.slug or "principal", "activa": await c.store.get("bot_activo") != "0" if c.store and c.store.db else None,
            "en_curso": len(d.active) if d else 0, "esperando": esperando,
            "respuestas": len(t), "p50": percentil(t, 50), "p95": percentil(t, 95), "max": max(t, default=0.0),
            "gasto": await limites.spent_today(c.store) if c.store and c.store.db else 0.0,
        })
    return {
        "memoria": memoria(), "cuentas": filas, "lentitud": dict(lentitud.CONTADORES),
        "trabajos": {"en_curso": sum(1 for t in trabajos if not t.hecho),
                     "fallidos": sum(1 for t in trabajos if t.hecho and t.error)},
        "respaldo": ultimo_respaldo(), "ahora": ahora,
    }


def _seg(s: float) -> str:
    return f"{s:.1f} s" if s else "–"


def html(d: dict) -> str:
    m = d["memoria"]
    libre_pct = round(100 * m["libre_mb"] / m["total_mb"]) if m["total_mb"] else 0
    alerta = ' class="mal"' if m["total_mb"] and libre_pct < 15 else ""
    hoy = d["lentitud"]
    filas = "".join(
        f'<tr><td>{escape(f["slug"])}</td><td>{"sí" if f["activa"] else ("no" if f["activa"] is False else "–")}</td>'
        f'<td>{f["en_curso"]}</td><td>{f["esperando"]}</td><td>{f["respuestas"]}</td>'
        f'<td>{_seg(f["p50"])}</td><td>{_seg(f["p95"])}</td><td>{_seg(f["max"])}</td><td>US${f["gasto"]:.2f}</td></tr>'
        for f in d["cuentas"])
    return (
        '<style>.salud .tarjeta{margin-bottom:1rem}.salud table{width:100%;border-collapse:collapse;font-size:.9rem}'
        '.salud th,.salud td{text-align:left;padding:.35rem .5rem;border-bottom:1px solid rgba(255,255,255,.08)}'
        '.salud th{color:var(--secundario,#A8A8AE);font-weight:600}.salud .datos{display:flex;flex-wrap:wrap;gap:1.5rem}'
        '.salud .dato b{display:block;font-size:1.5rem}.salud .mal b{color:#FF8A3D}</style>'
        '<div class="salud">'
        '<div class="tarjeta"><span class="num">Servidor</span><h2>Memoria</h2><div class="datos">'
        f'<div class="dato"><b>{m["proceso_mb"]} MB</b>el proceso del bot</div>'
        f'<div class="dato"><b>{m["hijos_mb"]} MB</b>{m["hijos"]} subproceso(s) de Claude</div>'
        f'<div class="dato"{alerta}><b>{m["libre_mb"]} MB</b>libres de {m["total_mb"]} MB ({libre_pct} %)</div>'
        '</div></div>'
        '<div class="tarjeta"><span class="num">Hoy</span><h2>Bucle y peticiones</h2><div class="datos">'
        f'<div class="dato"><b>{hoy.get("bloqueos", 0)}</b>bloqueos del bucle (≥ {lentitud.BLOQUEO_SEG:g} s)</div>'
        f'<div class="dato"><b>{hoy.get("lentos", 0)}</b>peticiones lentas (≥ {lentitud.LENTO_SEG:g} s)</div>'
        f'<div class="dato"><b>{d["trabajos"]["en_curso"]}</b>trabajos de campaña en curso</div>'
        f'<div class="dato"><b>{d["trabajos"]["fallidos"]}</b>trabajos fallidos</div>'
        f'<div class="dato"><b>{escape(d["respaldo"] or "–")}</b>último respaldo</div>'
        '</div></div>'
        '<div class="tarjeta"><span class="num">Cuentas</span><h2>Respuestas de las últimas 24 h</h2>'
        '<table><thead><tr><th>Cuenta</th><th>Activa</th><th>En curso</th><th>Esperando</th><th>Respuestas</th>'
        f'<th>Mediana</th><th>p95</th><th>Máx.</th><th>Gasto hoy</th></tr></thead><tbody>{filas}</tbody></table>'
        '<p class="nota">Los tiempos van del primer mensaje pendiente al envío de la respuesta y se guardan en memoria:'
        ' al reiniciar empiezan de cero.</p></div></div>'
    )


# --- alertas al administrador (la cuenta principal), por DM y correo, con una hora de silencio por tipo ---

RAM_LIBRE_MIN = 15  # % de RAM libre por debajo del cual se avisa
COLA_MAX = 20  # conversaciones esperando (en todas las cuentas) dos revisiones seguidas
REINICIOS_MAX = 3  # arranques en una hora
RESPALDO_DIAS = 2  # días sin carpeta de respaldo nueva
SILENCIO = config.HORA  # entre dos avisos del mismo tipo
_avisado: dict[str, float] = {}
_cola_alta = False  # la revisión anterior ya tenía la cola alta
avisar = None  # la pone app.py: conectar.avisar (DM a ADMIN_IG_IDS y correo al dueño de la principal)


async def alertar(tipo: str, texto: str, ahora: float | None = None) -> bool:
    """Manda el aviso si no se mandó uno igual en la última hora. True si se mandó."""
    ahora = ahora or time.time()
    if ahora - _avisado.get(tipo, 0) < SILENCIO or avisar is None:
        return False
    _avisado[tipo] = ahora
    try:
        await avisar(f"Skandar: {tipo}", texto)
    except Exception:
        log.exception("No se pudo mandar la alerta «%s»", tipo)
    return True


async def anotar_arranque(store, ahora: float | None = None) -> int:
    """Guarda el arranque en la base de la principal y devuelve cuántos hubo en la última hora."""
    ahora = ahora or time.time()
    import json
    previos = [t for t in json.loads(await store.get("arranques") or "[]") if ahora - t < config.HORA]
    previos.append(ahora)
    await store.set("arranques", json.dumps(previos))
    return len(previos)


async def revisar(ahora: float | None = None) -> list[str]:
    """Una revisión: qué alertas tocaba mandar (y las manda). Para las pruebas devuelve sus tipos."""
    global _cola_alta
    ahora = ahora or time.time()
    mandadas = []
    m = memoria()
    if m["total_mb"] and 100 * m["libre_mb"] / m["total_mb"] < RAM_LIBRE_MIN:
        if await alertar("poca memoria", f"Quedan {m['libre_mb']} MB libres de {m['total_mb']} MB en el servidor. "
                         f"El bot usa {m['proceso_mb']} MB y sus {m['hijos']} subproceso(s) de Claude {m['hijos_mb']} MB. "
                         "Si sigue así, el sistema va a matar el proceso. Revisa /panel/salud.", ahora):
            mandadas.append("poca memoria")
    esperando = 0
    for c in fuentes["cuentas"]():
        if c.store and c.store.db:
            d = fuentes["dispatchers"]().get(c.slug)
            esperando += sum(1 for u in await c.store.pending_users() if not d or u not in d.active)
    if esperando > COLA_MAX and _cola_alta:
        if await alertar("cola atascada", f"Hay {esperando} conversaciones esperando respuesta desde hace más de un minuto "
                         "entre todas las cuentas. Revisa /panel/salud y el registro (journalctl -u igbot).", ahora):
            mandadas.append("cola atascada")
    _cola_alta = esperando > COLA_MAX
    ultimo = ultimo_respaldo()
    if ultimo:
        from datetime import date
        try:
            dias = (date.fromtimestamp(ahora) - date.fromisoformat(ultimo)).days
        except ValueError:
            dias = 0
        if dias >= RESPALDO_DIAS and await alertar(
                "respaldo atrasado", f"El último respaldo es del {ultimo}, hace {dias} días. Revisa el timer: "
                "systemctl status igbot-respaldo.timer y journalctl -u igbot-respaldo.", ahora):
            mandadas.append("respaldo atrasado")
    return mandadas


async def vigilar(cada: float = 60) -> None:
    """Bucle de fondo: revisa cada minuto y avisa al administrador cuando algo se sale de lo normal."""
    while True:
        try:
            await revisar()
        except Exception:
            log.exception("Falló la revisión de salud")
        await asyncio.sleep(cada)


@router.get("/salud")
async def ver(request: Request, c: marco.Sesion):
    if c.slug:
        return await marco.no_encontrada(request)
    d = await datos()
    return marco.pagina(await marco.menu_de(c, "cuenta", sub="salud") + '<main><div class="contenedor">' + html(d)
                        + "</div></main>")
