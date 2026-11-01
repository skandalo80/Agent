// Visitas sin cookies (Cloudflare Web Analytics; ver /privacidad). Sin token no se carga nada.
// El token no es secreto: queda a la vista en la página, como en cualquier sitio que lo usa.
var CF_TOKEN = 'd38f0c5816d940d79a84401453be0a6f';
if (CF_TOKEN) {
  var beacon = document.createElement('script');
  beacon.defer = true;
  beacon.src = 'https://static.cloudflareinsights.com/beacon.min.js';
  beacon.setAttribute('data-cf-beacon', JSON.stringify({ token: CF_TOKEN }));
  document.head.appendChild(beacon);
}

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

// Menú en celular: un botón junto a "Entrar" despliega los enlaces que en escritorio van en la barra.
(function () {
  var enlaces = document.querySelector('header .nav-links');
  var acciones = document.querySelector('header .nav-acciones');
  if (!enlaces || !acciones) return;
  var boton = document.createElement('button');
  boton.className = 'menu-boton';
  boton.type = 'button';
  boton.setAttribute('aria-label', 'Abrir menú');
  boton.setAttribute('aria-expanded', 'false');
  boton.innerHTML = '<svg class="abrir-ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" aria-hidden="true"><path d="M4 7h16M4 12h16M4 17h16"/></svg>' +
    '<svg class="cerrar-ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18"/></svg>';
  var poner = function (abierto) {
    document.body.classList.toggle('menu-abierto', abierto);
    boton.setAttribute('aria-expanded', String(abierto));
    boton.setAttribute('aria-label', abierto ? 'Cerrar menú' : 'Abrir menú');
  };
  boton.addEventListener('click', function () { poner(!document.body.classList.contains('menu-abierto')); });
  enlaces.addEventListener('click', function (e) { if (e.target.closest('a')) poner(false); });
  document.addEventListener('keydown', function (e) { if (e.key === 'Escape') poner(false); });
  acciones.insertBefore(boton, acciones.firstChild);
})();

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
    '<div class="sk-nombre"><b>Skandi</b><small>Asistente de Skandar</small><span class="sk-ultimo"></span></div>' +
    '<button class="sk-min" type="button" aria-label="Minimizar" title="Minimizar">' +
    '<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2.2"' +
    ' stroke-linecap="round" aria-hidden="true"><path d="M6 12h12"/></svg></button>' +
    '<button class="sk-cerrar" type="button" aria-label="Cerrar chat" title="Cerrar">×</button></div>';
  var boton = document.createElement('button');
  boton.className = 'sk-boton';
  boton.type = 'button';
  boton.title = '¿Dudas? Pregúntale a Skandi';
  boton.setAttribute('aria-label', 'Abrir chat con Skandi');
  boton.innerHTML = icono;
  document.body.append(ventana, boton);
  var ultimo = ventana.querySelector('.sk-ultimo');
  // Minimizada se queda así al cambiar de página (se guarda en el navegador, con el último mensaje).
  var recordar = function (min, texto) {
    try {
      if (min) { localStorage.setItem('skandi', 'min'); localStorage.setItem('skandi-ultimo', texto); }
      else localStorage.removeItem('skandi');
    } catch (e) {}
  };
  var abrir = function () {
    if (!ventana.querySelector('iframe')) {
      var marco = document.createElement('iframe');
      marco.src = '/ayuda';
      marco.title = 'Chat con Skandi';
      ventana.append(marco);
    }
    document.body.classList.remove('skandi-min');
    document.body.classList.add('skandi-abierta');
    recordar(false);
  };
  var minimizar = function (texto) {
    ultimo.textContent = texto || 'Toca para seguir la conversación';
    document.body.classList.add('skandi-abierta', 'skandi-min');
    recordar(true, ultimo.textContent);
  };
  var ultimoDelChat = function () {
    try {
      var todos = ventana.querySelector('iframe').contentDocument.querySelectorAll('.msj');
      return todos.length ? todos[todos.length - 1].textContent : '';
    } catch (e) { return ''; }
  };
  boton.addEventListener('click', abrir);
  // Minimizar: queda una barrita con el último mensaje; al tocarla se abre igual.
  // Cerrar: la próxima vez empieza una conversación nueva.
  ventana.querySelector('.sk-min').addEventListener('click', function (e) {
    e.stopPropagation();
    minimizar(ultimoDelChat());
  });
  ventana.querySelector('.sk-cabeza').addEventListener('click', function (e) {
    if (document.body.classList.contains('skandi-min') && !e.target.closest('.sk-cerrar')) abrir();
  });
  ventana.querySelector('.sk-cerrar').addEventListener('click', function () {
    document.body.classList.remove('skandi-abierta', 'skandi-min');
    recordar(false);
    var marco = ventana.querySelector('iframe');
    if (marco) marco.src = '/ayuda?nueva=1';
    boton.focus();
  });
  try {
    if (localStorage.getItem('skandi') === 'min') minimizar(localStorage.getItem('skandi-ultimo'));
  } catch (e) {}
})();

// Animaciones (estilos en animaciones.css). Nada se mueve si el sistema pide reducir el movimiento.
(function () {
  if (!('IntersectionObserver' in window)) return;
  if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
  document.documentElement.classList.add('anima');

  // Aparición al bajar: lo que entra a la vez en pantalla aparece uno tras otro.
  var bloques = document.querySelectorAll('main .antetitulo, main h1, main h2, main .bajada, main .acciones, main .micro,' +
    ' main .tarjeta, main .funcion, main .pasos > article, main .plan, main .articulo-tarjeta, main .pastilla');
  var vigia = new IntersectionObserver(function (entradas) {
    var n = 0;
    entradas.forEach(function (e) {
      if (!e.isIntersecting) return;
      e.target.style.setProperty('--retraso', Math.min(n++ * 0.08, 0.48) + 's');
      e.target.classList.add('visto');
      vigia.unobserve(e.target);
      // Al terminar se quitan las clases para que el hover de cada tarjeta vuelva a su transición normal.
      var t = e.target;
      setTimeout(function () { t.classList.remove('revela', 'visto'); t.style.removeProperty('--retraso'); }, 1300);
    });
  }, { rootMargin: '0px 0px -8% 0px', threshold: 0.12 });
  bloques.forEach(function (b) {
    if (b.closest('.visual, .articulo')) return;
    b.classList.add('revela');
    vigia.observe(b);
  });

  // Escenas de la portada y de "Un día con Skandar" (estilos en escenas.css): cada una corre cuando se ve
  // y vuelve a empezar al terminar su ciclo (data-ciclo, en ms); fuera de pantalla se detiene y se reinicia.
  document.querySelectorAll('.escena[data-ciclo]').forEach(function (escena) {
    var ciclo = parseInt(escena.dataset.ciclo, 10) || 12000;
    var reloj = null;
    // Los puntitos de "escribiendo" no ocupan lugar: se dibujan sobre el sitio donde va a entrar la respuesta.
    var acomodarTeclas = function () {
      escena.querySelectorAll('.tecla').forEach(function (t) {
        var sig = t.nextElementSibling;
        if (sig) t.style.setProperty('--arriba', sig.offsetTop + 'px');
      });
    };
    var correr = function () {
      acomodarTeclas();
      escena.classList.remove('corre');
      void escena.offsetWidth; // reinicia las animaciones
      escena.classList.add('corre');
      reloj = setTimeout(correr, ciclo + 1800);
    };
    var vigiaEscena = new IntersectionObserver(function (entradas) {
      if (entradas[0].isIntersecting) { if (!reloj) correr(); }
      else { clearTimeout(reloj); reloj = null; escena.classList.remove('corre'); }
    }, { threshold: 0.35 });
    vigiaEscena.observe(escena);
  });

  // Chat del teléfono: el cliente escribe, el asistente "teclea" y contesta; al final llega la tarjeta del anuncio.
  var chat = document.querySelector('.telefono .chat');
  if (!chat) return;
  var burbujas = chat.querySelectorAll('.burbuja');
  var idea = document.querySelector('.tarjeta-idea');
  var escribe = document.createElement('span');
  escribe.className = 'escribe';
  escribe.innerHTML = '<i></i><i></i><i></i>';
  chat.append(escribe);
  var espera = function (ms) { return new Promise(function (listo) { setTimeout(listo, ms); }); };
  var conversar = function () {
    var paso = espera(400);
    burbujas.forEach(function (b) {
      paso = paso.then(function () {
        if (!b.classList.contains('agente')) return;
        escribe.style.top = b.offsetTop + 'px';
        escribe.classList.add('visto');
        return espera(1000).then(function () { escribe.classList.remove('visto'); });
      }).then(function () {
        b.classList.add('visto');
        return espera(b.classList.contains('agente') ? 900 : 500);
      });
    });
    paso.then(function () { if (idea) idea.classList.add('visto'); });
  };
  var vigiaChat = new IntersectionObserver(function (entradas) {
    if (!entradas[0].isIntersecting) return;
    vigiaChat.disconnect();
    conversar();
  }, { threshold: 0.4 });
  vigiaChat.observe(chat);
})();
