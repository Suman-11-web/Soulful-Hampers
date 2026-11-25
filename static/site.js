document.querySelectorAll('[data-open]').forEach(button => {
  button.addEventListener('click', () => {
    const dialog = document.getElementById(button.dataset.open);
    dialog.showModal();
    (dialog.querySelector('input') || dialog.querySelector('[data-close]'))?.focus();
  });
});
document.querySelectorAll('dialog').forEach(dialog => {
  dialog.querySelector('[data-close]')?.addEventListener('click', () => dialog.close());
  dialog.addEventListener('click', event => {
    if (event.target !== dialog) return;
    const rect = dialog.getBoundingClientRect();
    if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) dialog.close();
  });
});
document.addEventListener('click', event => {
  document.querySelectorAll('details.drop[open]').forEach(menu => {
    if (!menu.contains(event.target)) menu.open = false;
  });
});
document.addEventListener('keydown', event => {
  if (event.key === 'Escape') document.querySelectorAll('details.drop[open]').forEach(menu => {
    menu.open = false;
    menu.querySelector('summary').focus();
  });
});
document.querySelectorAll('[data-gallery]').forEach(button => {
  button.addEventListener('click', () => {
    document.getElementById('gallery-main').src = button.dataset.gallery;
    document.querySelectorAll('[data-gallery]').forEach(thumb => thumb.setAttribute('aria-pressed', String(thumb === button)));
  });
});
document.querySelectorAll('form[data-confirm]').forEach(form => {
  form.addEventListener('submit', event => {
    if (!window.confirm(form.dataset.confirm)) event.preventDefault();
  });
});
document.querySelectorAll('[data-confirm-click]').forEach(button => {
  button.addEventListener('click', event => {
    if (!window.confirm(button.dataset.confirmClick)) event.preventDefault();
  });
});
