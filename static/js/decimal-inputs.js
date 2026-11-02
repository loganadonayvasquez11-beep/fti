/* Keep monetary number fields numeric while editing; the server validates them again. */
(() => {
  document.querySelectorAll('input[data-decimal-input]').forEach(input => {
    input.addEventListener('keydown', event => {
      if (event.key.length === 1 && !/[0-9.,]/.test(event.key)) event.preventDefault();
    });
    input.addEventListener('paste', event => {
      const pasted = event.clipboardData.getData('text').trim();
      if (!/^(?:\d+(?:[.,]\d{0,2})?|[.,]\d{1,2})$/.test(pasted)) event.preventDefault();
    });
  });
})();
