/* Increment/decrement controls for editable sale quantities. */
(() => {
  document.querySelectorAll('[data-quantity-control]').forEach(control => {
    const input = control.querySelector('input[type="number"]');
    if (!input) return;

    control.querySelectorAll('[data-quantity-step]').forEach(button => {
      button.addEventListener('click', () => {
        const minimum = input.min === '' ? -Infinity : Math.ceil(Number(input.min));
        const maximum = input.max === '' ? Infinity : Math.floor(Number(input.max));
        const current = Number.parseFloat(input.value);
        const value = Number.isFinite(current) ? Math.round(current) : (button.dataset.quantityStep === '1' ? minimum : 0);
        const next = Math.min(maximum, Math.max(minimum, value + Number(button.dataset.quantityStep)));
        input.value = String(next);
        input.dispatchEvent(new Event('input', { bubbles: true }));
        input.dispatchEvent(new Event('change', { bubbles: true }));
      });
    });
  });
})();
