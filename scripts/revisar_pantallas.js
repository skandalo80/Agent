// Revisión de pantallas en celular y escritorio (ver CLAUDE.md, "Revisión de pantallas").
//
//   node scripts/revisar_pantallas.js http://127.0.0.1:8766 / /que-es-skandar.html
//   PANEL_COOKIE=... node scripts/revisar_pantallas.js http://127.0.0.1:8765 /panel/inicio /panel/resultados
//
// Recorre cada ruta a 390x844 (celular) y 1440x900 (escritorio) y falla (código 1) si encuentra:
//   - scroll horizontal (algo más ancho que la ventana);
//   - en celular, campos de texto de menos de 16 px (iPhone hace zoom) o botones de menos de 44 px de alto;
//   - en escritorio, un slide de la web (.caso, .casos-intro, #planes, #como-funciona) más alto que la ventana
//     (en celular los slides no encajan: escenas.css solo los activa desde 801 px de ancho).
// Deja una captura por ruta y tamaño en capturas/ (fuera de git).
const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');

const [base, ...rutas] = process.argv.slice(2);
if (!base || !rutas.length) { console.error('Uso: node scripts/revisar_pantallas.js <url base> <ruta> [ruta...]'); process.exit(2); }
const SALIDA = path.join(__dirname, '..', 'capturas');
fs.mkdirSync(SALIDA, { recursive: true });
const TAMANOS = [['celular', { width: 390, height: 844 }, true], ['escritorio', { width: 1440, height: 900 }, false]];

const PROBLEMAS = `(${function (esCelular) {
  const fallas = [];
  const visible = (el) => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0 && getComputedStyle(el).visibility !== 'hidden'; };
  const de = (el) => (el.id ? '#' + el.id : el.tagName.toLowerCase() + (el.className && typeof el.className === 'string' ? '.' + el.className.trim().split(/\s+/).slice(0, 2).join('.') : ''));
  const ancho = document.documentElement.clientWidth;
  if (document.documentElement.scrollWidth > ancho + 1) {
    const culpables = [...document.querySelectorAll('body *')].filter((el) => visible(el) && el.getBoundingClientRect().right > ancho + 1).slice(0, 3).map(de);
    fallas.push(`scroll horizontal: ${document.documentElement.scrollWidth} px en ${ancho} (${culpables.join(', ')})`);
  }
  if (esCelular) {
    for (const el of document.querySelectorAll('input:not([type=checkbox],[type=radio],[type=range],[type=file],[type=hidden],[type=submit],[type=button]),select,textarea')) {
      if (visible(el) && parseFloat(getComputedStyle(el).fontSize) < 16) fallas.push(`campo de ${getComputedStyle(el).fontSize} (menos de 16 px): ${de(el)}`);
    }
    for (const el of document.querySelectorAll('button,a.boton,a.btn,input[type=submit],[role=button]')) {
      if (visible(el) && el.getBoundingClientRect().height < 44 && !el.closest('.paso,.wiz-pasos,.as-pasos,.riel,.tabs,.res-vista,nav,.lateral,.menu')) fallas.push(`botón de ${Math.round(el.getBoundingClientRect().height)} px de alto (menos de 44): ${de(el)}`);
    }
  }
  for (const el of esCelular ? [] : document.querySelectorAll('.caso,.casos-intro,#planes,#como-funciona')) {
    if (visible(el) && el.getBoundingClientRect().height > window.innerHeight + 1) fallas.push(`slide más alto que la ventana (${Math.round(el.getBoundingClientRect().height)} > ${window.innerHeight}): ${de(el)}`);
  }
  return [...new Set(fallas)].slice(0, 12);
}})`;

(async () => {
  const navegador = await chromium.launch();
  let total = 0;
  for (const [nombre, viewport, movil] of TAMANOS) {
    const ctx = await navegador.newContext({ viewport, deviceScaleFactor: 2, isMobile: movil, hasTouch: movil });
    if (process.env.PANEL_COOKIE) {
      const { hostname } = new URL(base);
      await ctx.addCookies([{ name: 'panel', value: process.env.PANEL_COOKIE, domain: hostname, path: '/panel' }]);
    }
    const pagina = await ctx.newPage();
    for (const ruta of rutas) {
      await pagina.goto(base + ruta, { waitUntil: 'load' });
      await pagina.waitForTimeout(800);
      const fallas = await pagina.evaluate(`${PROBLEMAS}(${movil})`);
      const archivo = path.join(SALIDA, `${nombre}-${ruta.replace(/[^a-z0-9]+/gi, '-').replace(/^-|-$/g, '') || 'inicio'}.png`);
      await pagina.screenshot({ path: archivo, fullPage: false });
      total += fallas.length;
      console.log(`${fallas.length ? 'FALLA' : 'ok   '} ${nombre} ${ruta}  (${path.relative(process.cwd(), archivo)})`);
      for (const f of fallas) console.log('      - ' + f);
    }
    await ctx.close();
  }
  await navegador.close();
  process.exit(total ? 1 : 0);
})();
