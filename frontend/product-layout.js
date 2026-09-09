(function () {
  const initialize = () => {
    const workspaceTabs = [...document.querySelectorAll('[data-workspace-tab]')];
    if (workspaceTabs.length) {
      const selectMode = (mode, {focus = false, updateUrl = false} = {}) => {
        for (const tab of workspaceTabs) {
          const active = tab.dataset.workspaceTab === mode;
          tab.setAttribute('aria-selected', String(active));
          tab.tabIndex = active ? 0 : -1;
          document.getElementById(tab.getAttribute('aria-controls')).hidden = !active;
          if (active && focus) tab.focus();
        }
        document.querySelectorAll('[data-workspace-link]').forEach(link => {
          const active = link.dataset.workspaceLink === mode;
          link.classList.toggle('active', active);
          if (active) link.setAttribute('aria-current', 'page');
          else link.removeAttribute('aria-current');
        });
        if (updateUrl) history.replaceState(null, '', `${location.pathname}${location.search}#${mode}`);
        if (mode === 'assistant') document.dispatchEvent(new Event('lecturesift:assistant-open'));
        if (mode === 'library') document.dispatchEvent(new Event('lecturesift:library-open'));
      };
      workspaceTabs.forEach((tab, index) => {
        tab.addEventListener('click', () => selectMode(tab.dataset.workspaceTab, {updateUrl:true}));
        tab.addEventListener('keydown', event => {
          if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
          event.preventDefault();
          const direction = document.documentElement.dir === 'rtl' ? -1 : 1;
          const next = event.key === 'Home' ? 0 : event.key === 'End' ? workspaceTabs.length - 1
            : (index + (event.key === 'ArrowRight' ? direction : -direction) + workspaceTabs.length) % workspaceTabs.length;
          selectMode(workspaceTabs[next].dataset.workspaceTab, {focus:true, updateUrl:true});
        });
      });
      const fromHash = () => selectMode(location.hash === '#assistant' ? 'assistant' : location.hash === '#library' ? 'library' : 'study');
      window.addEventListener('hashchange', fromHash);
      fromHash();
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
