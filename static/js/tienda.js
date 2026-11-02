/* Tabla de productos: buscador en vivo, filtro por categoría y (en Nueva venta) selección de varios productos.
   - Stock/Catálogo: solo filtra las filas de la tabla.
   - Nueva venta: marcas filas (checkbox o clic en la fila), ajustas cantidades y "Añadir seleccionados" los manda al carrito
     sin recargar la página. Sin JavaScript todo sigue funcionando (el formulario se envía normalmente). */
(() => {
  const tabla = document.getElementById('tienda');
  if (!tabla) return;

  const filas = [...tabla.querySelectorAll('tbody tr')];
  const buscar = document.getElementById('buscar-tienda') || document.querySelector('input[name=q]');
  const cat = document.getElementById('cat-tienda') || document.querySelector('select[name=categoria]');
  const vacio = document.getElementById('vacio-tienda');
  const contador = document.getElementById('contador-tienda');

  const norm = t => (t || '').toLocaleLowerCase().normalize('NFD').replace(/[\u0300-\u036f]/g, '');

  // ---------------------------------------------------------- paginación (solo si la página tiene #paginacion-tienda)
  const pager = document.getElementById('paginacion-tienda');
  const porPagina = pager ? (parseInt(pager.dataset.porPagina, 10) || 20) : 0;
  let pagina = 1;

  function dibujarPaginas(total) {
    if (!pager) return;
    const paginas = Math.max(1, Math.ceil(total / porPagina));
    pager.hidden = paginas <= 1;
    pager.innerHTML = '';
    if (paginas <= 1) return;
    const boton = (txt, p, extra = '') => {
      const b = document.createElement('button');
      b.type = 'button';
      b.className = 'pag-btn ' + extra;
      b.textContent = txt;
      if (p === null) b.disabled = true;
      else b.addEventListener('click', () => irAPagina(p));
      return b;
    };
    pager.appendChild(boton('‹', pagina > 1 ? pagina - 1 : null, 'pag-flecha'));
    // 1 … (actual-1) actual (actual+1) … última
    const mostrar = new Set([1, paginas, pagina - 1, pagina, pagina + 1]);
    let previo = 0;
    for (let p = 1; p <= paginas; p++) {
      if (!mostrar.has(p)) continue;
      if (p - previo > 1) {
        const s = document.createElement('span');
        s.className = 'pag-puntos';
        s.textContent = '…';
        pager.appendChild(s);
      }
      pager.appendChild(boton(String(p), p === pagina ? null : p, p === pagina ? 'pag-actual' : ''));
      previo = p;
    }
    pager.appendChild(boton('›', pagina < paginas ? pagina + 1 : null, 'pag-flecha'));
  }

  function irAPagina(p) {
    pagina = p;
    filtrar(false);
    // vuelve al inicio de la lista, debajo de la barra fija
    const barra = document.querySelector('.barra-fija');
    const alto = barra ? barra.offsetHeight : 0;
    window.scrollTo({ top: Math.max(0, tabla.getBoundingClientRect().top + window.scrollY - alto - 8), behavior: 'auto' });
  }

  /** Muestra solo las filas que coinciden con la búsqueda y la categoría (y de la página actual, si hay paginación). */
  function filtrar(reiniciar = true) {
    if (reiniciar !== false) pagina = 1;
    const q = norm(buscar ? buscar.value.trim() : '');
    const c = cat ? cat.value : '';
    const coinciden = filas.filter(f => (!q || norm(f.dataset.busqueda).includes(q)) && (!c || f.dataset.cat === c));
    const n = coinciden.length;
    if (pager) {
      const paginas = Math.max(1, Math.ceil(n / porPagina));
      if (pagina > paginas) pagina = paginas;
    }
    const visibles = pager ? new Set(coinciden.slice((pagina - 1) * porPagina, pagina * porPagina)) : new Set(coinciden);
    filas.forEach(f => { f.hidden = !visibles.has(f); });
    if (vacio) vacio.hidden = n > 0;
    if (contador) contador.textContent = `${n} producto(s)`;
    dibujarPaginas(n);
    actualizarSeleccion();
  }
  if (buscar) {
    buscar.addEventListener('input', filtrar);
    // En el teléfono, "Buscar" del teclado cierra el teclado en vez de recargar la página
    buscar.addEventListener('keydown', e => {
      if (e.key === 'Enter') { e.preventDefault(); buscar.blur(); }
    });
  }
  if (cat) cat.addEventListener('change', filtrar);

  // ---------------------------------------------------------- selección (solo Nueva venta)
  const form = document.getElementById('form-seleccion');
  const nSel = document.getElementById('n-sel');
  const btnAnadir = document.getElementById('btn-anadir');
  const marcarTodos = document.getElementById('marcar-todos');

  function actualizarSeleccion() {
    if (!form) return;
    const marcadas = filas.filter(f => f.querySelector('input[name=sel]').checked && !f.querySelector('input[name=sel]').disabled);
    marcadas.forEach(f => f.classList.add('elegido'));
    nSel.textContent = marcadas.length;
    btnAnadir.disabled = marcadas.length === 0;
    if (marcarTodos) {
      const vis = filas.filter(f => !f.hidden && !f.querySelector('input[name=sel]').disabled);
      marcarTodos.checked = vis.length > 0 && vis.every(f => f.querySelector('input[name=sel]').checked);
    }
  }

  if (form) {
    filas.forEach(f => {
      const chk = f.querySelector('input[name=sel]');
      if (chk.disabled) return;   // producto agotado: no se puede elegir
      chk.addEventListener('change', () => { f.classList.toggle('elegido', chk.checked); actualizarSeleccion(); });
      // clic en cualquier parte de la fila (menos en controles) marca / desmarca
      f.addEventListener('click', e => {
        if (e.target.closest('input, button, a, label')) return;
        chk.checked = !chk.checked;
        chk.dispatchEvent(new Event('change'));
      });
      // al escribir una cantidad, la fila queda elegida
      const cant = f.querySelector('.c-cant input');
      if (cant) cant.addEventListener('input', () => { if (!chk.checked) { chk.checked = true; chk.dispatchEvent(new Event('change')); } });
    });
    if (marcarTodos) marcarTodos.addEventListener('change', () => {
      filas.filter(f => !f.hidden).forEach(f => {
        const chk = f.querySelector('input[name=sel]');
        if (chk.disabled) return;
        chk.checked = marcarTodos.checked;
        f.classList.toggle('elegido', chk.checked);
      });
      actualizarSeleccion();
    });

    form.addEventListener('submit', async e => {
      e.preventDefault();
      btnAnadir.disabled = true;
      try {
        const r = await fetch(form.action, { method: 'POST', body: new FormData(form), headers: { 'X-Requested-With': 'fetch' } });
        const d = await r.json();
        if (d.errores && d.errores.length) alert(d.errores.join('\n'));
        // refresca la columna "En carrito", desmarca lo añadido y actualiza la barra
        filas.forEach(f => {
          const q = d.carrito ? d.carrito[f.dataset.id] : null;
          const celda = f.querySelector('.en-carrito-cel');
          if (q && celda) celda.innerHTML = `<span class="badge ok">✓ ${Number(q).toLocaleString('es-VE', { maximumFractionDigits: 3 })}</span>`;
        });
        if (d.agregados) {
          filas.forEach(f => {
            const chk = f.querySelector('input[name=sel]');
            if (chk.checked && (d.ids || []).includes(f.dataset.id)) {
              chk.checked = false; f.classList.remove('elegido');
              const c = f.querySelector('.c-cant input'); if (c) c.value = 1;
            }
          });
        }
        document.getElementById('carrito-n').textContent = d.items;
        document.getElementById('btn-carrito').hidden = !d.items;
      } catch (_) { form.submit(); return; }
      finally { actualizarSeleccion(); }
    });
  }

  filtrar();
})();
