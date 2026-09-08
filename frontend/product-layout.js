(function () {
  const initialize = () => {
    const source = new URLSearchParams(location.search).get('source');
    if (document.body.classList.contains('workspace-page') && source === 'link') {
      document.getElementById('linkTab')?.click();
    }
    const tabs = document.querySelector('.account-section-nav');
    if (tabs) {
      const compact = matchMedia('(max-width: 860px)');
      const update = () => tabs.setAttribute('aria-orientation', compact.matches ? 'horizontal' : 'vertical');
      update();compact.addEventListener?.('change', update);
    }
  };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', initialize, {once:true});
  else initialize();
})();
