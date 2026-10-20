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
