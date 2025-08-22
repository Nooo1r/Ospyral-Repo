document.addEventListener('DOMContentLoaded', () => {
  const toggleButton = document.getElementById('menu-toggle');
  const sidebar = document.getElementById('sidebar');
  const logo = document.querySelector('.rotating-icon');

  document.addEventListener('mousemove', (e) => {
    const screenWidth = window.innerWidth;
    const mouseX = e.clientX;
    if (mouseX > screenWidth - 100) toggleButton.style.right = '10px';
    else if (!sidebar.classList.contains('active')) toggleButton.style.right = '-60px';
  });

  toggleButton?.addEventListener('click', (e) => {
    e.stopPropagation();
    const expanded = toggleButton.getAttribute('aria-expanded') === 'true';
    toggleButton.setAttribute('aria-expanded', (!expanded).toString());
    sidebar.classList.toggle('active');
    if (logo) {
      logo.classList.remove('spin-once');
      void logo.offsetWidth;
      logo.classList.add('spin-once');
    }
  });

  document.addEventListener('click', (e) => {
    const isInside = sidebar.contains(e.target) || toggleButton.contains(e.target);
    if (!isInside && sidebar.classList.contains('active')) {
      sidebar.classList.remove('active');
      toggleButton.style.right = '-60px';
    }
  });

  // Простая логика модалки
  document.addEventListener('click', (e) => {
    const openBtn = e.target.closest('[data-modal="logoutModal"]');
    if (openBtn) document.getElementById('logoutModal')?.setAttribute('aria-hidden', 'false');
    if (e.target.matches('[data-close]')) e.target.closest('.modal')?.setAttribute('aria-hidden', 'true');
  });
});
