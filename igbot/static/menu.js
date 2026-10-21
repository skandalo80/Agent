// Corre antes de pintar la página (sin defer): si plegaste el menú lateral, sale ya plegado.
try { if (localStorage.getItem("menu:plegado") === "1") document.documentElement.classList.add("menu-plegado"); } catch (e) { /* sin almacenamiento */ }
