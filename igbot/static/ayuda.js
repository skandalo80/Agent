// Skandi en la web (dentro del iframe de sitio.js): "escribiendo…" mientras contesta y,
// al volver, baja al último mensaje. Sin esto el chat funciona igual.
const form = document.querySelector("form");
const mensajes = document.getElementById("mensajes");
mensajes.scrollTop = mensajes.scrollHeight;
form.addEventListener("submit", () => {
  const tuyo = document.createElement("div");
  tuyo.className = "msj tu";
  tuyo.textContent = form.mensaje.value;
  const espera = document.createElement("div");
  espera.className = "msj skandi escribiendo";
  espera.textContent = "Skandi está escribiendo…";
  mensajes.append(tuyo, espera);
  mensajes.scrollTop = mensajes.scrollHeight;
  setTimeout(() => { form.mensaje.readOnly = true; form.querySelector("button").disabled = true; }, 0);
});
// Al volver con la respuesta, el cursor regresa a la caja solo donde hay ratón: en celular abriría el teclado.
if (location.hash === "#fin" && !window.matchMedia("(hover: none)").matches) form.mensaje.focus({ preventScroll: true });
