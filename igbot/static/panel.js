// Vista previa al momento en el panel: Tu marca y el editor de posts.
// Sin este archivo el panel funciona igual, con los botones "Ver cómo queda" y "Actualizar imagen".
document.documentElement.classList.add("js");

const CAMPOS = ["fuente", "peso", "principal", "acento", "fondo", "plantilla", "estilo", "titulo", "subtitulo", "precio"];
const FAMILIAS = {"Playfair Display": "Georgia, serif", "Lora": "Georgia, serif"};

function consulta(form) {
  const datos = new FormData(form);
  const q = new URLSearchParams();
  for (const k of CAMPOS) {
    const v = datos.get(k);
    if (typeof v === "string" && v) q.set(k, v);
  }
  if (datos.get("con_precio") === "1") q.set("con_precio", "1");
  return q.toString();
}

function vigilar(form) {
  const vista = document.getElementById("vista");
  if (!vista) return;
  let espera;
  const refrescar = () => {
    clearTimeout(espera);
    // data-vista trae la versión de la foto (?m=…): si se genera otra, no sale la vista vieja del caché.
    const base = form.dataset.vista;
    espera = setTimeout(() => { vista.src = base + (base.includes("?") ? "&" : "?") + consulta(form); }, 250);
  };
  form.addEventListener("input", (e) => {
    const t = e.target;
    if (t.type === "color") {
      const hex = form.querySelector('[data-hex="' + t.name + '"]');
      if (hex) hex.textContent = t.value.toUpperCase();
    }
    if (t.name === "fuente") {
      const familia = "'" + t.value + "', " + (FAMILIAS[t.value] || "system-ui, sans-serif");
      form.querySelectorAll('input[name="peso"]').forEach((p) => { p.parentElement.style.fontFamily = familia; });
    }
    if (t.name === "peso") {
      form.querySelectorAll('input[name="fuente"]').forEach((f) => { f.parentElement.style.fontWeight = t.value; });
    }
    if (CAMPOS.includes(t.name) || t.name === "con_precio") refrescar();
  });
}

document.querySelectorAll("form[data-vista]").forEach(vigilar);
