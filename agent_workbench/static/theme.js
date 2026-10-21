// Apply the saved theme before the first paint, including on a slow catalog scan.
try {
  const theme = localStorage.getItem('awb-theme');
  if (theme === 'dark' || theme === 'light') document.documentElement.dataset.theme = theme;
} catch {
  /* The Marvis-inspired light theme is the default without browser storage. */
}
