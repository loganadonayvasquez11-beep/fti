/* Keep monetary number fields numeric while editing; the server validates them again. */
(() => {
  const sanitizeDecimal = value => {
    const cleaned = value.replace(/[^0-9,\.]/g, '').replace(',', '.');
    const parts = cleaned.split('.');
    if (parts.length > 2) return parts[0] + '.' + parts.slice(1).join('');
    return cleaned;
  };

  document.querySelectorAll('input[data-decimal-input]').forEach(input => {
    if (!input.dataset.decimalInputBound) {
      input.dataset.decimalInputBound = '1';
      input.addEventListener('keydown', event => {
        if (['Backspace', 'Tab', 'Delete', 'ArrowLeft', 'ArrowRight', 'Home', 'End', 'Escape', 'Enter'].includes(event.key)
            || event.ctrlKey || event.metaKey || event.altKey) return;
        if (event.key.length !== 1 || !/[0-9.,]/.test(event.key)) event.preventDefault();
      });
      input.addEventListener('paste', event => {
        const pasted = event.clipboardData.getData('text').trim();
        if (!/^(?:\d+(?:[.,]\d{0,2})?|[.,]\d{1,2})$/.test(pasted)) event.preventDefault();
      });
      input.addEventListener('input', () => {
        if (input.type === 'text') input.value = sanitizeDecimal(input.value);
      });
    }
  });

  document.querySelectorAll('input[data-integer-input]').forEach(input => {
    if (!input.dataset.integerInputBound) {
      input.dataset.integerInputBound = '1';
      const sanitize = () => {
        input.value = input.value.replace(/\D/g, '');
      };
      input.addEventListener('keydown', event => {
        if (['Backspace', 'Tab', 'Delete', 'ArrowLeft', 'ArrowRight', 'Home', 'End', 'Escape'].includes(event.key)
            || event.ctrlKey || event.metaKey || event.altKey) return;
        if (event.key.length === 1 && !/[0-9]/.test(event.key)) event.preventDefault();
      });
      input.addEventListener('paste', event => {
        const pasted = event.clipboardData.getData('text').trim();
        if (!/^\d*$/.test(pasted)) event.preventDefault();
      });
      input.addEventListener('input', sanitize);
    }
  });
})();
