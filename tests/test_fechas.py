"""Calendario comercial por país: las fechas que caen en la campaña (o justo después) para el brief y los posts."""
from datetime import date

from igbot import fechas


def test_fechas_del_periodo_por_pais():
    # México, campaña del 2 al 22 de noviembre: Día de Muertos y El Buen Fin dentro; Black Friday justo después.
    lista = fechas.del_periodo("MX", date(2026, 11, 2), date(2026, 11, 22))
    assert (date(2026, 11, 2), "Día de Muertos") in lista and (date(2026, 11, 13), "El Buen Fin (por estos días)") in lista
    assert (date(2026, 11, 27), "Black Friday") in lista and (date(2026, 11, 30), "Cyber Monday") in lista
    assert lista == sorted(lista)
    # Argentina: el Día de las Madres es el tercer domingo de octubre; en México es el 10 de mayo.
    assert (date(2026, 10, 18), "Día de las Madres") in fechas.del_periodo("AR", date(2026, 10, 1), date(2026, 10, 31))
    assert (date(2026, 5, 10), "Día de las Madres") in fechas.del_periodo("MX", date(2026, 5, 1), date(2026, 5, 31))
    assert (date(2026, 5, 10), "Día de las Madres") in fechas.del_periodo("CO", date(2026, 5, 1), date(2026, 5, 31))
    # "Otro" solo lleva lo común a todos; nada de un país.
    assert [n for _, n in fechas.del_periodo("XX", date(2026, 5, 1), date(2026, 5, 31))] == ["Día del Trabajo"]
    # Cambio de año: Navidad y Año Nuevo en la misma campaña.
    nombres = [n for _, n in fechas.del_periodo("ES", date(2026, 12, 20), date(2027, 1, 7))]
    assert nombres[:3] == ["Lotería de Navidad", "Nochebuena", "Navidad"] and "Día de Reyes" in nombres


def test_el_tag_para_el_prompt():
    tag = fechas.tag("MX", date(2026, 11, 2), date(2026, 11, 22))
    assert tag.startswith("<fechas_del_periodo>\n- lun 2 nov: Día de Muertos\n")
    assert "- vie 27 nov: Black Friday (después de la campaña: se puede anticipar)" in tag
    assert fechas.tag("XX", date(2026, 3, 10), date(2026, 3, 20)) == ""
    assert fechas.enesimo(11, 0, -1)(2026) == date(2026, 11, 30) and fechas.black_friday(2026) == date(2026, 11, 27)


def test_zona_y_formatos_de_la_cuenta():
    """La zona de la cuenta y la única forma de escribir fechas (campañas, informes, panel)."""
    import time
    from datetime import date, datetime
    from types import SimpleNamespace

    from igbot import fechas

    c = SimpleNamespace(timezone="America/Mexico_City")
    assert str(fechas.zona(c)) == "America/Mexico_City"
    medianoche = fechas.inicio_del_dia(c, date(2026, 11, 3))
    assert fechas.en_zona(c, medianoche) == datetime(2026, 11, 3, 0, 0, tzinfo=fechas.zona(c))
    assert fechas.en_zona(c, medianoche + 3600 * 10).hour == 10
    assert fechas.hoy(c) == fechas.ahora(c).date()
    assert fechas.corta("2026-11-03") == "Mar 3" and fechas.larga(date(2026, 11, 3)) == "Mar 3 nov"
    assert fechas.con_anio(date(2026, 11, 3)) == "3 nov 2026"
    ahora = time.time()
    assert fechas.hace(ahora - 10) == "hace un momento"
    assert fechas.hace(ahora - 5 * 60) == "hace 5 min"
    assert fechas.hace(ahora - 3 * 3600) == "hace 3 h"
    assert fechas.hace(ahora - 30 * 3600) == "ayer"
    assert fechas.hace(ahora - 4 * 86400) == "hace 4 días"
