"""Topes contra abuso: largo de cada DM, respuestas por persona y gasto diario."""
import asyncio
import time

from igbot.cuenta import cuenta  # noqa: E402
import app as bot  # noqa: E402
from igbot import limites  # noqa: E402
from igbot.store import Store  # noqa: E402


def setup(monkeypatch):
    sent, asked = [], []

    async def fake_reply(store, google, user_id, new_messages, instagram=None):
        asked.append(new_messages)
        return "ok", False

    async def fake_send(user_id, text):
        sent.append(text)

    async def nothing(*a):
        pass

    monkeypatch.setattr(bot.agent, "reply", fake_reply)
    monkeypatch.setattr(bot.agent, "summarize_if_needed", nothing)
    monkeypatch.setattr(bot.instagram, "send_text", fake_send)
    return sent, asked


async def new_store(monkeypatch):
    store = Store(":memory:")
    await store.open()
    monkeypatch.setattr(cuenta(), "store", store)
    return store


async def dm(store, n):
    await store.add_incoming("ana", f"m{n}", f"mensaje {n}")
    await bot.handle("ana", await store.pending("ana"))


def test_los_mensajes_largos_se_recortan(monkeypatch):
    monkeypatch.setattr(cuenta(), "max_message_chars", 10)
    assert limites.trim("corto") == "corto"
    assert limites.trim("x" * 50) == "x" * 10 + " […mensaje recortado]"


def test_tope_diario_por_persona(monkeypatch):
    sent, asked = setup(monkeypatch)
    monkeypatch.setattr(cuenta(), "max_replies_per_user_per_day", 2)

    async def main():
        store = await new_store(monkeypatch)
        for n in range(5):
            await dm(store, n)
        # Dos respuestas, un aviso y luego silencio; Claude solo se llamó dos veces.
        assert sent == ["ok", "ok", limites.LIMIT_NOTICE]
        assert len(asked) == 2
        assert await store.pending_users() == []
        await store.close()

    asyncio.run(main())


def test_presupuesto_diario(monkeypatch):
    sent, asked = setup(monkeypatch)
    monkeypatch.setattr(cuenta(), "daily_budget_usd", 1.0)

    async def main():
        store = await new_store(monkeypatch)
        await dm(store, 0)
        await limites.add_cost(0.6, limites.ATENCION)  # lo que gastó contestando
        await limites.add_cost(0.5, limites.ATENCION)
        await dm(store, 1)
        # Con el presupuesto agotado la persona no se queda en silencio: un aviso, una vez al día.
        assert sent == ["ok", limites.SIN_RESPUESTA] and len(asked) == 1
        await dm(store, 2)
        assert sent == ["ok", limites.SIN_RESPUESTA]
        assert (await store.ultimo_evento("ana", "sin_respuesta"))[0] == {"motivo": "presupuesto"}
        assert await limites.first_budget_alert(store) is False  # ya se avisó al pasarlo
        await store.close()

    asyncio.run(main())


def test_el_contenido_no_gasta_el_presupuesto_del_asistente(monkeypatch):
    """Generar una campaña (rubro contenido) no debe dejar mudo al asistente: solo cuenta lo de atención."""
    sent, asked = setup(monkeypatch)
    monkeypatch.setattr(cuenta(), "daily_budget_usd", 1.0)

    async def main():
        store = await new_store(monkeypatch)
        await limites.add_cost(5.0)  # sin rubro: contenido (una campaña con imágenes)
        await limites.add_cost(0.3, limites.CONTENIDO)
        assert await limites.spent_today(store) == 5.3
        assert await limites.spent_today(store, limites.CONTENIDO) == 5.3
        assert await limites.spent_today(store, limites.ATENCION) == 0
        assert not await limites.over_budget(store)
        await dm(store, 0)
        assert sent == ["ok"]
        # Lo que se gasta dentro de handle (contestar) cae en atención aunque nadie pase el rubro.
        with limites.rubro(limites.ATENCION):
            await limites.add_cost(1.0)
        assert await limites.spent_today(store, limites.ATENCION) == 1.0
        assert await limites.over_budget(store)
        await dm(store, 1)
        assert sent == ["ok", limites.SIN_RESPUESTA] and len(asked) == 1  # se le avisa que se le contesta después
        await store.close()

    asyncio.run(main())


def test_el_rubro_del_contexto_se_restaura():
    assert limites._rubro.get() == limites.CONTENIDO
    with limites.rubro(limites.ATENCION):
        assert limites._rubro.get() == limites.ATENCION
    assert limits_default()


def limits_default() -> bool:
    return limites._rubro.get() == limites.CONTENIDO


def test_al_terminar_la_pausa_el_asistente_retoma_lo_que_quedo_sin_contestar(monkeypatch):
    from igbot.dispatcher import Dispatcher
    sent, asked = setup(monkeypatch)

    async def main():
        store = await new_store(monkeypatch)
        d = Dispatcher(store, bot.atender(cuenta()), 0, 1)
        monkeypatch.setitem(bot.dispatchers, cuenta().slug, d)
        ahora = time.time()
        await store.add_incoming("ana", "m0", "hola")
        await bot.handle("ana", await store.pending("ana"))
        await store.pause("ana", ahora + 3600)  # el dueño tomó el chat
        await store.add_incoming("ana", "m1", "¿y en rojo?")
        await bot.handle("ana", await store.pending("ana"))  # en pausa: se guarda, no se contesta
        assert sent == ["ok"] and await store.pending("ana") == []
        # Termina la pausa y el bucle de fondo revisa: lo que quedó sin contestar vuelve a la cola.
        await store.db.execute("UPDATE paused SET until = ?", (ahora - 1,))
        bot._retomado[cuenta().slug] = ahora - 100
        await bot.retomar(cuenta())
        await d.drain()
        assert asked[-1] == ["¿y en rojo?"] and sent == ["ok", "ok"]
        # Otra vuelta no repite nada; y si el dueño ya contestó a mano, no hay nada que retomar.
        await bot.retomar(cuenta())
        await d.drain()
        assert sent == ["ok", "ok"]
        await store.add_incoming("ana", "m2", "gracias")
        await store.mark_seen([i for i, _ in await store.pending("ana")])
        await store.add_owner_message("ana", None, "De nada")
        assert await store.reabrir("ana", 86400) == 0
        await store.close()

    asyncio.run(main())


def test_presupuesto_de_contenido_del_mes(monkeypatch):
    """Lo que se gasta en contenido suma al mes; al llegar al tope no se empieza nada nuevo que cueste."""
    monkeypatch.setattr(cuenta(), "contenido_usd_mes", 1.0)
    monkeypatch.setattr(limites.config, "IMAGE_COST_USD", 0.05)

    async def main():
        store = await new_store(monkeypatch)
        assert await limites.presupuesto_restante(store) == 1.0
        assert await limites.imagenes_restantes(store) == 20  # sin imágenes aún: al costo fijo
        with limites.rubro(limites.ATENCION):
            await limites.add_cost(0.5)  # contestar DMs no gasta el presupuesto de contenido
        await limites.add_cost(0.2)  # el texto de una campaña
        await limites.add_cost(0.1, limites.CONTENIDO, imagen=True)
        await limites.add_cost(0.3, limites.CONTENIDO, imagen=True)
        assert round(await limites.gasto_contenido_mes(store), 6) == 0.6
        assert await limites.imagenes_restantes(store) == 2  # US$0.40 a US$0.20 por imagen
        assert not await limites.sin_presupuesto(store)
        await limites.add_cost(0.5)
        assert await limites.sin_presupuesto(store) and await limites.imagenes_restantes(store) == 0
        # Sin tope en su plan: nunca se agota.
        monkeypatch.setattr(cuenta(), "contenido_usd_mes", 0)
        assert await limites.presupuesto_restante(store) is None and not await limites.sin_presupuesto(store)
        await store.close()

    asyncio.run(main())


def test_el_plan_community_trae_10_dolares_de_contenido():
    from igbot.cuenta import Cuenta
    env = {"IG_ACCESS_TOKEN": "t", "PLAN": "community"}
    assert Cuenta.desde_env("x", env).contenido_usd_mes == 10
    assert Cuenta.desde_env("x", {**env, "CONTENT_BUDGET_USD_MONTH": "25"}).contenido_usd_mes == 25
    assert Cuenta.desde_env("x", {"IG_ACCESS_TOKEN": "t"}).contenido_usd_mes == 0


def test_la_principal_no_tiene_topes_de_contenido(monkeypatch):
    """La cuenta principal (@skandar.online) es la de pruebas del dueño: crea campañas, anuncios e imágenes aunque
    pase el cupo, el presupuesto del mes y los topes por día u hora. Un cliente con los mismos números se frena."""
    import dataclasses

    from igbot import campanas, contenido, imagenes
    from igbot.cuenta import usar

    monkeypatch.setattr(limites.config, "PRINCIPAL_SIN_TOPES", True)
    monkeypatch.setattr(limites.config, "GENERACIONES_HORA", 1)
    monkeypatch.setattr(campanas, "IA_POR_DIA", 1)

    async def main():
        principal = dataclasses.replace(cuenta(), slug="", imagenes_mes=1, contenido_usd_mes=1.0,
                                        store=await new_store(monkeypatch))
        cliente = dataclasses.replace(principal, slug="cafe", store=Store(":memory:"))
        await cliente.store.open()
        for c in (principal, cliente):
            with usar(c):
                await limites.add_cost(2.0)  # ya pasó su presupuesto del mes
                await imagenes.reservar(c.store, permitir_extra=True)  # y su cupo de imágenes
                await campanas.usar_ia(c)
            await limites.cabe_generacion(c.store, ahora=10, c=c)
        # La principal: sin tope.
        assert limites.sin_topes(principal) and not limites.sin_topes(cliente)
        with usar(principal):
            assert not await limites.sin_presupuesto(principal.store)
            assert await imagenes.reservar(principal.store) == "incluida"
        assert not await campanas.sin_presupuesto(principal)
        assert await campanas.ia_restantes(principal) == campanas.SIN_LIMITE
        assert await contenido.falta_cupo(principal, False) == ""
        assert await limites.cabe_generacion(principal.store, ahora=11, c=principal)
        # El cliente: sus topes siguen igual.
        with usar(cliente):
            assert await limites.sin_presupuesto(cliente.store)
            try:
                await imagenes.reservar(cliente.store)
                raise AssertionError("debió frenarse")
            except imagenes.SinCupo:
                pass
        assert await campanas.sin_presupuesto(cliente) and await campanas.ia_restantes(cliente) == 0
        assert await contenido.falta_cupo(cliente, False) == "extra"
        assert not await limites.cabe_generacion(cliente.store, ahora=11, c=cliente)
        # Con PRINCIPAL_SIN_TOPES=0 la principal vuelve a tener topes.
        monkeypatch.setattr(limites.config, "PRINCIPAL_SIN_TOPES", False)
        assert not limites.sin_topes(principal) and await campanas.sin_presupuesto(principal)
        await principal.store.close()
        await cliente.store.close()

    asyncio.run(main())
