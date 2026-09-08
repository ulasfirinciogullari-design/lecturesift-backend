/* A local, illustrative sample. No account, API request or user data involved. */
(function () {
  const host = document.querySelector('.home-page [data-study-demo]');
  if (!host) return;
  const text = (key, fallback = '') => window.LectureSiftI18n?.t(`homeDemo.${key}`, fallback) || fallback;
  host.querySelectorAll('[data-demo-copy]').forEach(node => { node.textContent = text(node.dataset.demoCopy, node.textContent); });
  const tabs = [...host.querySelectorAll('[role="tab"]')];
  const activate = tab => {
    tabs.forEach(item => {
      const active = item === tab;
      item.setAttribute('aria-selected', String(active));
      item.tabIndex = active ? 0 : -1;
      document.getElementById(item.getAttribute('aria-controls')).hidden = !active;
    });
  };
  let answered = false;
  // Delegation survives translated child text and replacement sample buttons.
  host.addEventListener('click', event => {
    const tab = event.target.closest('[role="tab"]');
    if (tab && host.contains(tab)) { activate(tab); return; }
    const option = event.target.closest('[data-demo-answer]');
    const feedback = document.getElementById('demoFeedback');
    const reset = document.getElementById('demoReset');
    if (option && host.contains(option) && !answered) {
      answered = true;
      const correct = option.dataset.demoAnswer === '0';
      host.querySelectorAll('[data-demo-answer]').forEach(button => {
        button.disabled = true;
        if (button.dataset.demoAnswer === '0') button.classList.add('is-correct');
      });
      option.classList.add(correct ? 'is-correct' : 'is-wrong');
      option.setAttribute('aria-pressed', 'true');
      feedback.textContent = `${correct ? '✓' : '✕'} ${text(correct ? 'correct' : 'incorrect')} · ${text('a')}`;
      reset.hidden = false;
    } else if (event.target.closest('#demoReset')) {
      answered = false;
      host.querySelectorAll('[data-demo-answer]').forEach(button => {
        button.disabled = false;
        button.classList.remove('is-correct', 'is-wrong');
        button.removeAttribute('aria-pressed');
      });
      feedback.textContent = '';
      reset.hidden = true;
      host.querySelector('[data-demo-answer]').focus();
    }
  });
  host.addEventListener('keydown', event => {
    const current = tabs.indexOf(event.target);
    if (current < 0 || !['ArrowLeft','ArrowRight','Home','End'].includes(event.key)) return;
    event.preventDefault();
    const rtl = document.documentElement.dir === 'rtl';
    const step = (event.key === 'ArrowRight' ? 1 : -1) * (rtl ? -1 : 1);
    const next = event.key === 'Home' ? 0 : event.key === 'End' ? tabs.length - 1 : (current + step + tabs.length) % tabs.length;
    activate(tabs[next]);
    tabs[next].focus();
  });
})();
