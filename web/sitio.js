document.querySelectorAll('.sugerencia[data-texto]').forEach(function (a) {
  a.addEventListener('click', function (e) {
    if (!navigator.clipboard) return;
    e.preventDefault();
    var ir = function () { window.location.href = a.href; };
    var aviso = document.getElementById('aviso-copia');
    navigator.clipboard.writeText(a.dataset.texto).then(function () {
      aviso.textContent = 'Mensaje copiado. Abriendo Instagram…';
      setTimeout(ir, 400);
    }, ir);
  });
});

// Página /conectar: avisos según cómo volvió del formulario.
var params = new URLSearchParams(window.location.search);
if (params.get('enviado')) document.getElementById('aviso-enviado').hidden = false;
if (params.get('error')) document.getElementById('aviso-error').hidden = false;

// Blog: filtro por categoría.
document.querySelectorAll('.filtros [data-filtro]').forEach(function (b) {
  b.addEventListener('click', function () {
    document.querySelectorAll('.filtros [data-filtro]').forEach(function (o) {
      o.classList.toggle('activa', o === b);
    });
    document.querySelectorAll('.articulo-tarjeta[data-categoria]').forEach(function (t) {
      t.hidden = b.dataset.filtro !== '' && t.dataset.categoria !== b.dataset.filtro;
    });
  });
});

// Skandi: el botón redondo de chat abajo a la derecha, en todas las páginas.
// La ventana es un iframe a /ayuda (Caddy lo pasa al bot); se carga hasta que la abren.
(function () {
  var icono = '<svg viewBox="0 0 24 24" width="28" height="28" fill="currentColor" aria-hidden="true">' +
    '<path d="M12 3C6.5 3 2 6.8 2 11.5c0 2.4 1.2 4.6 3.1 6.1L4.3 21l4-1.9c1.2.4 2.4.6 3.7.6 5.5 0 10-3.8 10-8.6S17.5 3 12 3z"/>' +
    '<circle cx="8" cy="11.5" r="1.3" fill="#FF8A3D"/><circle cx="12" cy="11.5" r="1.3" fill="#FF8A3D"/>' +
    '<circle cx="16" cy="11.5" r="1.3" fill="#FF8A3D"/></svg>';
  var ventana = document.createElement('div');
  ventana.className = 'skandi';
  ventana.setAttribute('role', 'dialog');
  ventana.setAttribute('aria-label', 'Skandi, asistente de Skandar');
  ventana.innerHTML = '<div class="sk-cabeza"><span class="sk-avatar" aria-hidden="true">S</span>' +
    '<div><b>Skandi</b><small>Asistente de Skandar</small></div>' +
    '<button class="sk-cerrar" type="button" aria-label="Cerrar chat">×</button></div>';
  var boton = document.createElement('button');
  boton.className = 'sk-boton';
  boton.type = 'button';
  boton.title = '¿Dudas? Pregúntale a Skandi';
  boton.setAttribute('aria-label', 'Abrir chat con Skandi');
  boton.innerHTML = icono;
  document.body.append(ventana, boton);
  boton.addEventListener('click', function () {
    if (!ventana.querySelector('iframe')) {
      var marco = document.createElement('iframe');
      marco.src = '/ayuda';
      marco.title = 'Chat con Skandi';
      ventana.append(marco);
    }
    document.body.classList.add('skandi-abierta');
  });
  ventana.querySelector('.sk-cerrar').addEventListener('click', function () {
    document.body.classList.remove('skandi-abierta');
    boton.focus();
  });
})();
