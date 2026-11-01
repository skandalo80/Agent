"""Calendario comercial por país: las fechas que mueven ventas (Día de las Madres, Buen Fin, Navidad…) que caen
en una campaña o justo después, para que el brief y los posts las aprovechen sin que el modelo adivine qué cae
cuándo. Son datos, no instrucciones: el modelo decide si le quedan al negocio y al objetivo.

del_periodo(pais, inicio, fin) devuelve [(fecha, nombre)] ordenadas; tag(...) las deja listas para el prompt como
<fechas_del_periodo>. El país es el del Perfil de empresa (empresa.pais_de); "XX" (Otro) solo lleva las fechas
comunes a todos.
"""
from datetime import date, timedelta

DESPUES = 10  # días después de la campaña que también se avisan: un post puede anticipar la fecha
DIAS = ("lun", "mar", "mié", "jue", "vie", "sáb", "dom")
MESES = ("ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic")


def fijo(mes: int, dia: int):
    return lambda a: date(a, mes, dia)


def enesimo(mes: int, dia_semana: int, n: int):
    """El n-ésimo día de la semana del mes (0 = lunes); n = -1, el último."""
    def f(a: int) -> date:
        if n > 0:
            primero = date(a, mes, 1)
            return primero + timedelta(days=(dia_semana - primero.weekday()) % 7 + 7 * (n - 1))
        ultimo = date(a + (mes == 12), mes % 12 + 1, 1) - timedelta(days=1)
        return ultimo - timedelta(days=(ultimo.weekday() - dia_semana) % 7)
    return f


def buen_fin(a: int) -> date:
    """Arranca el viernes anterior al tercer lunes de noviembre (puente de la Revolución)."""
    return enesimo(11, 0, 3)(a) - timedelta(days=3)


def black_friday(a: int) -> date:
    """El viernes después del cuarto jueves de noviembre (Día de Gracias en Estados Unidos)."""
    return enesimo(11, 3, 4)(a) + timedelta(days=1)


def cyber_monday(a: int) -> date:
    return black_friday(a) + timedelta(days=3)


def hot_sale(a: int) -> date:
    """México: arranca a fines de mayo (la última semana completa). Fecha aproximada."""
    return enesimo(5, 0, -1)(a)


COMUNES = [
    (fijo(1, 1), "Año Nuevo"), (fijo(2, 14), "San Valentín"), (fijo(3, 8), "Día Internacional de la Mujer"),
    (fijo(5, 1), "Día del Trabajo"), (fijo(10, 31), "Halloween"), (black_friday, "Black Friday"),
    (cyber_monday, "Cyber Monday"), (fijo(12, 24), "Nochebuena"), (fijo(12, 25), "Navidad"), (fijo(12, 31), "Fin de año"),
]

MADRES = {"MX": fijo(5, 10), "GT": fijo(5, 10), "SV": fijo(5, 10), "AR": enesimo(10, 6, 3), "ES": enesimo(5, 6, 1),
          "PA": fijo(12, 8), "BO": fijo(5, 27), "NI": fijo(5, 30), "PY": fijo(5, 15), "CR": fijo(8, 15),
          "DO": enesimo(5, 6, -1)}  # los demás: segundo domingo de mayo
PADRES = {"ES": fijo(3, 19), "HN": fijo(3, 19), "BO": fijo(3, 19), "UY": enesimo(7, 6, 2), "NI": fijo(6, 23),
          "SV": fijo(6, 17), "GT": fijo(6, 17), "DO": enesimo(7, 6, -1)}  # los demás: tercer domingo de junio
NINOS = {"MX": fijo(4, 30), "AR": enesimo(8, 6, 3), "CO": enesimo(4, 5, -1), "CL": enesimo(8, 6, 2), "PE": enesimo(8, 6, 3),
         "ES": enesimo(4, 6, 2), "EC": fijo(6, 1), "GT": fijo(10, 1), "UY": enesimo(8, 6, 2), "CR": fijo(9, 9),
         "PA": enesimo(7, 6, 3), "VE": enesimo(7, 6, 3), "BO": fijo(4, 12), "PY": fijo(8, 16), "HN": fijo(9, 10),
         "SV": fijo(10, 1), "NI": fijo(6, 1), "DO": enesimo(9, 6, -1), "PR": enesimo(7, 6, 2)}
PATRIAS = {"MX": (fijo(9, 16), "Independencia de México"), "AR": (fijo(7, 9), "Día de la Independencia"),
           "CO": (fijo(7, 20), "Día de la Independencia"), "CL": (fijo(9, 18), "Fiestas Patrias"),
           "PE": (fijo(7, 28), "Fiestas Patrias"), "ES": (fijo(10, 12), "Fiesta Nacional de España"),
           "US": (fijo(7, 4), "Independence Day"), "EC": (fijo(8, 10), "Primer Grito de Independencia"),
           "GT": (fijo(9, 15), "Independencia"), "UY": (fijo(8, 25), "Declaratoria de la Independencia"),
           "CR": (fijo(9, 15), "Independencia"), "PA": (fijo(11, 3), "Separación de Panamá de Colombia"),
           "DO": (fijo(2, 27), "Independencia Nacional"), "BO": (fijo(8, 6), "Día de la Patria"),
           "PY": (fijo(5, 15), "Independencia"), "VE": (fijo(7, 5), "Independencia"), "HN": (fijo(9, 15), "Independencia"),
           "SV": (fijo(9, 15), "Independencia"), "NI": (fijo(9, 15), "Independencia"), "PR": (fijo(7, 25), "Día de la Constitución")}
PROPIAS = {  # lo comercial de cada país además de lo de arriba
    "MX": [(fijo(1, 6), "Día de Reyes"), (fijo(5, 15), "Día del Maestro"), (hot_sale, "Hot Sale (por estos días)"),
           (fijo(11, 1), "Día de Muertos"), (fijo(11, 2), "Día de Muertos"), (buen_fin, "El Buen Fin (por estos días)"),
           (fijo(12, 12), "Día de la Virgen de Guadalupe"), (enesimo(8, 0, -1), "Regreso a clases")],
    "AR": [(fijo(5, 25), "Revolución de Mayo"), (fijo(7, 20), "Día del Amigo"), (fijo(9, 21), "Día de la Primavera y del Estudiante"),
           (enesimo(5, 0, 2), "Hot Sale (por estos días)"), (enesimo(11, 0, 1), "Cyber Monday Argentina (por estos días)")],
    "CO": [(enesimo(9, 5, 3), "Día del Amor y la Amistad"), (enesimo(1, 0, -1), "Regreso a clases"),
           (fijo(12, 7), "Día de las Velitas")],
    "CL": [(fijo(9, 19), "Fiestas Patrias (Día de las Glorias del Ejército)"), (enesimo(5, 0, -1), "CyberDay (por estos días)"),
           (enesimo(10, 0, 1), "CyberMonday Chile (por estos días)"), (enesimo(3, 0, 1), "Regreso a clases")],
    "PE": [(enesimo(7, 0, 2), "Cyber Wow (por estos días)"), (fijo(10, 31), "Día de la Canción Criolla"),
           (enesimo(3, 0, 1), "Regreso a clases")],
    "ES": [(fijo(1, 6), "Día de Reyes"), (fijo(4, 23), "Día del Libro (Sant Jordi)"), (enesimo(9, 0, 2), "Vuelta al cole"),
           (fijo(12, 22), "Lotería de Navidad")],
    "US": [(enesimo(1, 0, 3), "Martin Luther King Day"), (enesimo(5, 0, -1), "Memorial Day"), (enesimo(9, 0, 1), "Labor Day"),
           (enesimo(11, 3, 4), "Thanksgiving"), (enesimo(8, 0, 2), "Back to school")],
    "EC": [(fijo(12, 6), "Fundación de Quito"), (fijo(10, 9), "Independencia de Guayaquil")],
    "GT": [(fijo(11, 1), "Día de Todos los Santos")],
    "UY": [(fijo(9, 21), "Día de la Primavera")],
    "CR": [(fijo(8, 2), "Día de la Virgen de los Ángeles")],
    "PA": [(fijo(11, 10), "Primer Grito de Independencia de La Villa de Los Santos")],
    "DO": [(fijo(1, 21), "Día de la Altagracia")],
    "BO": [(fijo(8, 21), "Día del Peatón"), (enesimo(2, 0, 1), "Regreso a clases")],
    "PY": [(fijo(7, 30), "Día de la Amistad")],
    "VE": [(fijo(4, 19), "Declaración de la Independencia"), (fijo(12, 16), "Aguinaldos y Navidad (empiezan)")],
    "HN": [(fijo(9, 10), "Día del Niño")],
    "SV": [(fijo(8, 6), "Fiestas Agostinas")],
    "NI": [(fijo(12, 7), "La Gritería")],
    "PR": [(fijo(1, 6), "Día de Reyes"), (fijo(11, 19), "Descubrimiento de Puerto Rico")],
}


def reglas(pais: str) -> list[tuple]:
    lista = list(COMUNES)
    if pais in PATRIAS:
        lista.append(PATRIAS[pais])
        lista += [(MADRES.get(pais, enesimo(5, 6, 2)), "Día de las Madres"),
                  (PADRES.get(pais, enesimo(6, 6, 3)), "Día del Padre"), (NINOS.get(pais, enesimo(4, 6, -1)), "Día del Niño")]
        lista += PROPIAS.get(pais, [])
    return lista


def del_periodo(pais: str, inicio: date, fin: date) -> list[tuple[date, str]]:
    """Las fechas del país entre `inicio` y `fin` + DESPUES días, ordenadas."""
    hasta = fin + timedelta(days=DESPUES)
    salida = []
    for a in range(inicio.year, hasta.year + 1):
        for regla, nombre in reglas(pais):
            try:
                d = regla(a)
            except ValueError:
                continue
            if inicio <= d <= hasta:
                salida.append((d, nombre))
    return sorted(set(salida))


def tag(pais: str, inicio: date, fin: date) -> str:
    """<fechas_del_periodo> para el prompt (vacío si no cae ninguna)."""
    lineas = []
    for d, nombre in del_periodo(pais, inicio, fin):
        despues = " (después de la campaña: se puede anticipar)" if d > fin else ""
        lineas.append(f"- {DIAS[d.weekday()]} {d.day} {MESES[d.month - 1]}: {nombre}{despues}")
    return f"<fechas_del_periodo>\n" + "\n".join(lineas) + "\n</fechas_del_periodo>\n\n" if lineas else ""
