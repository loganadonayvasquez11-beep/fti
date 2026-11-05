/* PASO 2 de la venta (carrito y cliente): totales en vivo, precio mayor en dólares y plazo de crédito.
   Es solo ayuda visual: el servidor vuelve a validar y recalcular todo antes de guardar. */
(() => {
  const form = document.getElementById('venta-form');
  if (!form) return;

  const filas = [...form.querySelectorAll('tr.linea')];
  const fmt = n => n.toLocaleString('es-VE', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  const r2 = n => Math.round(n * 100) / 100;
  const esCredito = () => form.querySelector('[name=tipo_pago]:checked').value === 'credito';
  let totalUsd = 0;

  /** Precio base mayor de la fila. */
  const precioLista = tr => r2(parseFloat(tr.dataset.mayor) || 0);

  /** Marca la fila si el precio escrito difiere del base (queda registrado para el admin). */
  function marcarEditado(tr) {
    const lst = parseFloat(tr.dataset.lista);
    const p = parseFloat(tr.querySelector('.precio').value);
    const dif = !isNaN(lst) && !isNaN(p) && Math.abs(p - lst) > 0.004;
    tr.dataset.editado = dif ? '1' : '';
    tr.classList.toggle('modificado', dif);
    tr.querySelector('.lista-info').textContent = dif ? `Precio base: ${fmt(lst)}` : '';
  }

  /** Recalcula precios no editados, subtotales, total y total. */
  function recalcular() {
    let subtotal = 0;
    filas.forEach(tr => {
      tr.dataset.lista = precioLista(tr);
      const inp = tr.querySelector('.precio');
      if (!tr.dataset.editado || inp.value === '') inp.value = Number(tr.dataset.lista).toFixed(2);
      marcarEditado(tr);
      const c = parseFloat(tr.querySelector('.cant').value) || 0;
      const s = r2(c * (parseFloat(inp.value) || 0));
      subtotal += s;
      tr.querySelector('.sub').textContent = fmt(s);
      const stock = parseFloat(tr.dataset.stock);
      const aviso = tr.querySelector('.stock-info');
      aviso.textContent = c > stock ? 'Supera el stock disponible' : '';
      aviso.classList.toggle('neg', c > stock);
    });
    const desc = parseFloat(document.getElementById('descuento').value) || 0;
    const total = Math.max(r2(subtotal - desc), 0);
    document.getElementById('t-sub').textContent = fmt(subtotal);
    document.getElementById('t-total').textContent = fmt(total);
    totalUsd = total;
    actualizarPlazo();
  }

  /** Plazo de crédito (solo lectura): el fijo del cliente o el automático por monto. */
  function actualizarPlazo() {
    const opt = document.getElementById('cliente').selectedOptions[0];
    const fijo = opt ? (parseInt(opt.dataset.dias, 10) || 0) : 0;
    const auto = totalUsd < 250 ? 15 : (totalUsd < 600 ? 20 : 30);
    document.getElementById('plazo-texto').textContent = fijo > 0 ? `${fijo} días (fijado por el administrador)` : `${auto} días`;
  }

  /** Muestra u oculta plazo y cuota inicial según contado o crédito. */
  function actualizarPago() {
    const cred = esCredito();
    ['campo-dias', 'campo-inicial', 'aviso-credito'].forEach(id => { document.getElementById(id).style.display = cred ? '' : 'none'; });
    if (!cred) document.getElementById('cuota-inicial').value = '0';
  }

  filas.forEach(tr => {
    const cantidad = tr.querySelector('.cant');
    cantidad.addEventListener('input', recalcular);
    cantidad.addEventListener('change', () => {
      if (cantidad.value === '0') {
        tr.querySelector('[name="producto_id"]').click();
      }
    });
    tr.querySelector('.precio').addEventListener('input', () => { tr.dataset.editado = '1'; recalcular(); });
  });
  form.querySelectorAll('[name=tipo_pago]').forEach(r => r.addEventListener('change', actualizarPago));
  document.getElementById('descuento').addEventListener('input', recalcular);
  document.getElementById('cliente').addEventListener('change', actualizarPlazo);

  // El servidor exige cliente para la venta; el presupuesto puede generarse sin cliente.
  recalcular();
  actualizarPago();
})();
