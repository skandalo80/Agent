// Vista previa al momento en el panel: Tu marca y el editor de posts.
// Sin este archivo el panel funciona igual, con los botones "Ver cómo queda" y "Actualizar imagen".
document.documentElement.classList.add("js");

const CAMPOS = ["fuente", "peso", "principal", "acento", "fondo", "plantilla", "estilo", "titulo", "subtitulo", "precio", "modo"];
const FAMILIAS = {"Playfair Display": "Georgia, serif", "Lora": "Georgia, serif", "Cormorant Garamond": "Georgia, serif"};

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
  // La vista grande y las miniaturas de otras plantillas (data-plantilla) se piden juntas.
  const imagenes = [vista, ...form.querySelectorAll("img[data-plantilla]")];
  imagenes.forEach((img) => {
    const listo = () => img.classList.remove("cargando");
    img.addEventListener("load", listo);
    img.addEventListener("error", listo);
  });
  let espera;
  const refrescar = () => {
    clearTimeout(espera);
    // data-vista trae la versión de la foto (?m=…): si se genera otra, no sale la vista vieja del caché.
    const base = form.dataset.vista;
    espera = setTimeout(() => {
      const q = consulta(form);
      imagenes.forEach((img) => {
        const p = new URLSearchParams(q);
        if (img.dataset.plantilla) p.set("plantilla", img.dataset.plantilla);
        const src = base + (base.includes("?") ? "&" : "?") + p.toString();
        if (img.src.endsWith(src)) return;
        img.classList.add("cargando");
        img.src = src;
      });
    }, 250);
  };
  form.addEventListener("input", (e) => {
    const t = e.target;
    if (t.type === "color") {
      const hex = form.querySelector('[data-hex="' + t.name + '"]');
      if (hex && hex.value.toUpperCase() !== t.value.toUpperCase()) hex.value = t.value.toUpperCase();
    }
    // El código del color escrito o pegado (#333D1F o 333D1F) mueve el selector y la vista previa.
    if (t.dataset.hex) {
      const v = t.value.trim().replace(/^#?/, "#");
      const color = form.querySelector('input[type=color][name="' + t.dataset.hex + '"]');
      if (color && /^#[0-9a-f]{6}$/i.test(v) && color.value !== v.toLowerCase()) {
        color.value = v.toLowerCase();
        color.dispatchEvent(new Event("input", { bubbles: true }));
      }
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

// Tarjetas plegables (<details class="plegable">): se acuerda si las cerraste, en este navegador.
// Si la dirección apunta a ella o a algo de adentro (#instrucciones, #preguntas), se queda abierta.
function apunta(d) {
  if (!location.hash) return false;
  try { return location.hash === "#" + d.id || !!d.querySelector(location.hash); } catch (e) { return false; }
}
document.querySelectorAll("details.plegable[id]").forEach((d) => {
  const clave = "plegado:" + d.id;
  try { if (localStorage.getItem(clave) === "1" && !apunta(d)) d.open = false; } catch (e) { /* sin almacenamiento */ }
  d.addEventListener("toggle", () => {
    try { localStorage.setItem(clave, d.open ? "0" : "1"); } catch (e) { /* sin almacenamiento */ }
  });
});

// Menú lateral: el botón « lo pliega a solo íconos (o lo fija abierto otra vez) y se acuerda en este navegador.
// En el celular, la hamburguesa lo abre encima de la página; la × o tocar fuera lo cierran.
// (menu.js ya lo dejó plegado antes de pintar la página, si así lo guardaste.)
const raiz = document.documentElement;
document.querySelectorAll("[data-plegar]").forEach((b) => {
  b.addEventListener("click", () => {
    raiz.classList.add("menu-anima");
    const plegado = raiz.classList.toggle("menu-plegado");
    try { localStorage.setItem("menu:plegado", plegado ? "1" : "0"); } catch (e) { /* sin almacenamiento */ }
  });
});
document.querySelectorAll(".hamburguesa, .lateral .cerrar, .velo").forEach((a) => {
  a.addEventListener("click", (e) => {
    e.preventDefault();
    raiz.classList.toggle("menu-abierto", a.classList.contains("hamburguesa"));
  });
});

// Menú de perfil (<details class="perfil">): se cierra al hacer clic fuera de él.
document.addEventListener("click", (e) => {
  document.querySelectorAll("details.perfil[open]").forEach((d) => { if (!d.contains(e.target)) d.open = false; });
});

// Tu marca: el nombre, la frase y el color principal se ven al momento en la vista previa del perfil.
document.querySelectorAll("form[data-vista]").forEach((form) => {
  form.addEventListener("input", (e) => {
    const t = e.target;
    form.querySelectorAll('[data-espejo="' + t.name + '"]').forEach((el) => { el.textContent = t.value; });
    if (t.name === "principal") form.querySelectorAll('[data-fondo="principal"]').forEach((el) => { el.style.background = t.value; });
    if (t.name === "nombre" && t.value.trim()) form.querySelectorAll("[data-inicial]").forEach((el) => { el.textContent = t.value.trim()[0].toUpperCase(); });
  });
});

// Menú lateral: Tu asistente y Contenido despliegan sus opciones en vez de cambiar de página.
// Si el menú está plegado (solo íconos), primero se abre.
document.querySelectorAll(".lat-item[data-sub]").forEach((item) => {
  item.addEventListener("click", (e) => {
    const sub = document.getElementById("sub-" + item.dataset.sub);
    if (!sub) return;
    e.preventDefault();
    if (raiz.classList.contains("menu-plegado") && matchMedia("(min-width: 901px)").matches) {
      raiz.classList.add("menu-anima");
      raiz.classList.remove("menu-plegado");
      try { localStorage.setItem("menu:plegado", "0"); } catch (err) { /* sin almacenamiento */ }
      sub.classList.remove("cerrado");
    } else {
      sub.classList.toggle("cerrado");
    }
    item.setAttribute("aria-expanded", sub.classList.contains("cerrado") ? "false" : "true");
  });
});

// Campañas: arrastrar un post a otro día (si ahí hay uno, se intercambian). Sin esto, el día se cambia en el panel del post.
(() => {
  const dias = document.querySelectorAll(".dia-cam[data-dia]");
  if (!dias.length) return;
  let arrastrado = null;
  document.querySelectorAll('.post-cam[draggable="true"]').forEach((p) => {
    p.addEventListener("dragstart", (e) => {
      arrastrado = p;
      p.classList.add("arrastrando");
      e.dataTransfer.effectAllowed = "move";
      e.dataTransfer.setData("text/plain", p.dataset.k);
    });
    p.addEventListener("dragend", () => {
      p.classList.remove("arrastrando");
      dias.forEach((d) => d.classList.remove("sobre"));
    });
  });
  dias.forEach((d) => {
    d.addEventListener("dragover", (e) => {
      if (!arrastrado) return;
      e.preventDefault();
      // Sin esto, al tomar el post por su imagen Chrome propone "copiar" y no deja soltarlo.
      e.dataTransfer.dropEffect = "move";
      d.classList.add("sobre");
    });
    d.addEventListener("dragleave", () => d.classList.remove("sobre"));
    d.addEventListener("drop", (e) => {
      e.preventDefault();
      d.classList.remove("sobre");
      if (!arrastrado || arrastrado.closest(".dia-cam") === d) return;
      const origen = arrastrado.closest(".dia-cam");
      const otro = d.querySelector(".post-cam");
      // Si el de ese día ya se publicó, el servidor no deja: se recarga y dice por qué. Un día de fuera estira la
      // campaña: también se recarga, para ver el rango nuevo.
      const suave = (!otro || otro.hasAttribute("data-k")) && !d.classList.contains("fuera");
      if (suave) {  // se mueve aquí mismo y el servidor contesta 204: la página no se recarga
        if (otro) origen.append(otro);
        d.append(arrastrado);
        d.classList.remove("libre");
        if (!origen.querySelector(".post-cam") && !origen.hasAttribute("data-publica")) origen.classList.add("libre");
        [arrastrado, otro].forEach((x) => {
          if (!x) return;
          x.classList.remove("movido");
          void x.offsetWidth;  // para que la animación corra otra vez si se vuelve a mover
          x.classList.add("movido");
        });
      }
      // Un formulario y no fetch: la CSP del panel (default-src 'none') bloquea fetch.
      const f = document.createElement("form");
      f.method = "post";
      f.action = d.closest("[data-camp]").dataset.camp;
      for (const [n, v] of Object.entries({accion: "mover", k: arrastrado.dataset.k, dia: d.dataset.dia, suave: suave ? "1" : ""})) {
        const i = document.createElement("input");
        i.type = "hidden"; i.name = n; i.value = v;
        f.append(i);
      }
      document.body.append(f);
      f.submit();
    });
  });
})();

// Campañas, paso 1: el resumen (posts, temas, formatos, imágenes) cambia con lo que se marca.
(() => {
  const form = document.querySelector(".form-cam");
  const resumen = form && form.querySelector(".resumen-exp");
  if (!resumen) return;
  const PUBLICAR = {"lun-sab": [0, 1, 2, 3, 4, 5], diario: [0, 1, 2, 3, 4, 5, 6], "lun-vie": [0, 1, 2, 3, 4], lmv: [0, 2, 4]};
  const poner = (r, texto) => { resumen.querySelector(`[data-r="${r}"]`).textContent = texto; };
  const veces = (n, de) => {
    if (!de) return "";
    const menos = Math.floor(n / de), mas = Math.ceil(n / de);
    if (menos === mas) return `${menos} ${menos === 1 ? "vez" : "veces"} cada uno`;
    return `${menos} o ${mas} veces cada uno`;
  };
  const actualizar = () => {
    const semanas = Number(form.semanas.value) || 1;
    const publica = form.publicar.options[form.publicar.selectedIndex];
    const dias = PUBLICAR[form.publicar.value] || PUBLICAR["lun-sab"];
    const [a, m, d] = (form.inicio.value || "").split("-").map(Number);
    let n = 0;
    if (a) for (let i = 0; i < 7 * semanas; i++) {
      const dia = new Date(a, m - 1, d + i);
      if (dias.includes((dia.getDay() + 6) % 7)) n++;
    }
    const temas = form.querySelectorAll("[data-tema]:checked").length;
    const formatos = [...form.querySelectorAll("[data-formato]:checked")];
    const costo = formatos.reduce((t, f) => t + Number(f.dataset.costo), 0);
    poner("posts", `${n} posts`);
    poner("cuando", `${semanas} semana${semanas > 1 ? "s" : ""}, ${publica.text.toLowerCase()}`);
    poner("temas", `${temas} tema${temas === 1 ? "" : "s"}`);
    poner("temas-x", veces(n, temas));
    poner("formatos", `${formatos.length} formato${formatos.length === 1 ? "" : "s"}`);
    poner("formatos-x", veces(n, formatos.length));
    poner("imagenes", `~${formatos.length ? Math.round(n * costo / formatos.length) : 0} imágenes`);
  };
  form.addEventListener("input", actualizar);
  form.addEventListener("change", actualizar);
})();

// Botones que piden confirmar (p. ej. Descartar post): sin el sí, no se manda el formulario.
document.addEventListener("click", (e) => {
  const b = e.target.closest("[data-confirma]");
  if (b && !confirm(b.dataset.confirma)) e.preventDefault();
});

// Fecha y hora: el calendario se abre al dar click en todo el campo, no solo en el icono.
document.querySelectorAll('input[type="datetime-local"], input[type="date"], input[type="time"]').forEach((i) => {
  i.addEventListener("click", () => {
    try { i.showPicker(); } catch { /* navegadores sin showPicker: queda como siempre */ }
  });
});

// Conversaciones: el buscador busca solo al dejar de escribir medio segundo (recarga la página con
// la búsqueda; sin fetch). Al volver, el cursor sigue en el buscador para seguir escribiendo.
document.querySelectorAll("form[data-vivo]").forEach((form) => {
  const campo = form.querySelector('input[type="search"]');
  let espera;
  try {
    if (sessionStorage.getItem("chats:buscando") === "1") {
      sessionStorage.removeItem("chats:buscando");
      campo.focus();
      campo.setSelectionRange(campo.value.length, campo.value.length);
    }
  } catch { /* sin almacenamiento: solo no regresa el cursor */ }
  campo.addEventListener("input", () => {
    clearTimeout(espera);
    espera = setTimeout(() => {
      try { sessionStorage.setItem("chats:buscando", "1"); } catch { /* sin almacenamiento */ }
      form.requestSubmit();
    }, 500);
  });
});
