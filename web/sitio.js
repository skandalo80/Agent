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
