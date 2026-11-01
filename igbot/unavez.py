"""Cosas que solo deben pasar una vez o pocas veces por hora, guardadas en la base (tabla kv) y no en
memoria: un reinicio del proceso no vuelve a dar por bueno un enlace ya usado ni borra los topes.

- gastar(store, clave, ttl): True la primera vez que se pide esa clave; False si ya se gastó y no venció.
- cabe(store, clave, maximo, ventana): True si en la ventana (segundos) van menos de `maximo` usos, y lo anota.

Las claves vencidas se borran de paso cada vez que se escribe una nueva.
"""
import json
import time

PREFIJO = "unavez:"


async def _limpiar(store, ahora: float) -> None:
    for clave, valor in await store.kv_con_prefijo(PREFIJO):
        try:
            vence = json.loads(valor)["vence"]
        except (ValueError, KeyError, TypeError):
            vence = 0
        if vence < ahora:
            await store.borrar_kv(clave)


async def gastar(store, clave: str, ttl: float, ahora: float | None = None) -> bool:
    if store is None:
        return True
    ahora = time.time() if ahora is None else ahora
    k = PREFIJO + clave
    if (guardado := await store.get(k)) and json.loads(guardado)["vence"] >= ahora:
        return False
    await _limpiar(store, ahora)
    await store.set(k, json.dumps({"vence": ahora + ttl}))
    return True


async def cabe(store, clave: str, maximo: int, ventana: float = 3600, ahora: float | None = None) -> bool:
    if store is None:
        return True
    ahora = time.time() if ahora is None else ahora
    k = PREFIJO + clave
    try:
        usos = [t for t in json.loads(await store.get(k) or "{}").get("usos", []) if ahora - t < ventana]
    except (ValueError, AttributeError):
        usos = []
    if len(usos) >= maximo:
        return False
    usos.append(ahora)
    await store.set(k, json.dumps({"usos": usos, "vence": ahora + ventana}))
    return True
