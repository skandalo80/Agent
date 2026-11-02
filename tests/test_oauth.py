"""El state de OAuth, común a todas las conexiones: firmado, de su ámbito y su cuenta, fresco y de un solo uso."""
import time

from igbot import oauth


def test_state_firmado_por_ambito_y_cuenta():
    c = type("C", (), {"slug": "cafe"})()
    state = oauth.nuevo_state("google", c)
    assert state.startswith("google.cafe.") and len(state.split(".")) == 5
    assert oauth.state_valido(state, state, "google", c)
    assert not oauth.state_valido(state, state, "shopify", c)  # otra conexión
    assert not oauth.state_valido(state, state, "google", type("C", (), {"slug": "otra"})())  # otra cuenta
    assert not oauth.state_valido(state, state, "google")  # sin cuenta (slug "-")
    assert not oauth.state_valido(state, None, "google", c)  # sin cookie
    assert not oauth.state_valido(state, state + "x", "google", c)  # cookie distinta
    assert not oauth.state_valido(state[:-1] + ("0" if state[-1] != "0" else "1"), None, "google", c, sin_cookie=True)
    # Sin sesión (conectar, entrar): la cuenta es "-" y, donde el proveedor vuelve en otro navegador, basta la firma.
    suelto = oauth.nuevo_state("instagram")
    assert oauth.state_valido(suelto, None, "instagram", sin_cookie=True)
    assert not oauth.state_valido(suelto, None, "instagram")
    viejo = f"instagram.-.{int(time.time()) - oauth.STATE_TTL - 1}.xyz"
    assert not oauth.state_valido(f"{viejo}.{oauth.firmar(viejo)}", None, "instagram", sin_cookie=True)


def test_state_se_gasta_una_vez(cuenta_de_prueba):
    c, correr = cuenta_de_prueba
    state = oauth.nuevo_state("shopify", c)
    assert correr(oauth.gastable(state, state, "shopify", c))
    assert not correr(oauth.gastable(state, state, "shopify", c))  # el mismo enlace ya no sirve
    assert not correr(oauth.gastable("x.y", "x.y", "shopify", c))
    # Sin base (pruebas de otras partes), solo se revisa la firma.
    assert correr(oauth.gastable(oauth.nuevo_state("facebook"), None, "facebook", store=None, sin_cookie=True))
