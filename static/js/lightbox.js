/* Visor de imágenes: al hacer clic en la foto de un producto (stock, pedidos para armar, nueva venta, carrito,
   confirmación) se abre ampliada para ver el detalle. Se cierra con la X, con Esc o tocando fuera de la imagen. */
(() => {
  const fondo = document.createElement('div');
  fondo.className = 'lb-fondo';
  fondo.hidden = true;
  fondo.setAttribute('role', 'dialog');
  fondo.setAttribute('aria-modal', 'true');
  fondo.innerHTML = '<button type="button" class="lb-cerrar" aria-label="Cerrar imagen">✕</button><img alt=""><div class="lb-pie"></div>';
  document.body.appendChild(fondo);
  const img = fondo.querySelector('img');
  const pie = fondo.querySelector('.lb-pie');

  function abrir(origen) {
    img.src = origen.currentSrc || origen.src;
    img.alt = origen.alt || '';
    pie.textContent = origen.alt || '';
    fondo.hidden = false;
    document.body.style.overflow = 'hidden';
    fondo.querySelector('.lb-cerrar').focus();
  }
  function cerrar() {
    fondo.hidden = true;
    img.removeAttribute('src');
    document.body.style.overflow = '';
  }

  // Captura: se atiende antes que otros clics (p. ej. el clic en la fila que marca el producto en Nueva venta).
  document.addEventListener('click', e => {
    const foto = e.target.closest('img.pfoto, img.producto-miniatura');
    if (foto && !fondo.contains(foto)) { e.preventDefault(); e.stopPropagation(); abrir(foto); return; }
    if (!fondo.hidden && (e.target === fondo || e.target.closest('.lb-cerrar'))) { e.stopPropagation(); cerrar(); }
  }, true);
  document.addEventListener('keydown', e => { if (e.key === 'Escape' && !fondo.hidden) cerrar(); });
})();
