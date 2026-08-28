(function () {
  const STORAGE_KEY = "lecturesift-theme";
  const root = document.documentElement;
  const systemTheme = window.matchMedia("(prefers-color-scheme: light)");

  const savedTheme = (() => {
    try {
      const value = localStorage.getItem(STORAGE_KEY);
      return value === "light" || value === "dark" ? value : "";
    } catch {
      return "";
    }
  })();

  let activeTheme = savedTheme || (systemTheme.matches ? "light" : "dark");
  let renderToggle = () => {};

  const applyTheme = theme => {
    activeTheme = theme;
    root.dataset.theme = theme;
    root.style.colorScheme = theme;
    const themeColor = document.querySelector('meta[name="theme-color"]');
    if (themeColor) themeColor.content = theme === "light" ? "#f4f7fc" : "#061022";
  };

  applyTheme(activeTheme);

  const setupToggle = () => {
    if (!document.querySelector('meta[name="theme-color"]')) {
      const themeColor = document.createElement("meta");
      themeColor.name = "theme-color";
      document.head.append(themeColor);
      applyTheme(activeTheme);
    }

    const header = document.querySelector(".topbar,.legal-topbar,.auth-topbar");
    if (!header || header.querySelector(".theme-toggle")) return;

    const translate = (key, fallback) => window.LectureSiftI18n?.t?.(key, fallback) || fallback;
    const button = document.createElement("button");
    button.type = "button";
    button.className = "theme-toggle";
    button.innerHTML = '<svg class="theme-toggle-sun" viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="4"></circle><path d="M12 2v2M12 20v2M4.93 4.93l1.42 1.42M17.65 17.65l1.42 1.42M2 12h2M20 12h2M4.93 19.07l1.42-1.42M17.65 6.35l1.42-1.42"></path></svg><svg class="theme-toggle-moon" viewBox="0 0 24 24" aria-hidden="true"><path d="M20.2 15.2A8.5 8.5 0 0 1 8.8 3.8 8.5 8.5 0 1 0 20.2 15.2Z"></path></svg><span class="theme-toggle-label"></span>';

    const render = () => {
      const switchToLight = activeTheme === "dark";
      const action = translate(switchToLight ? "theme.switchToLight" : "theme.switchToDark", switchToLight ? "Açık temaya geç" : "Koyu temaya geç");
      button.dataset.mode = activeTheme;
      button.setAttribute("aria-label", action);
      button.setAttribute("title", action);
      button.setAttribute("aria-pressed", String(activeTheme === "light"));
      button.querySelector(".theme-toggle-label").textContent = translate(activeTheme === "light" ? "theme.light" : "theme.dark", activeTheme === "light" ? "Açık" : "Koyu");
    };
    renderToggle = render;

    button.addEventListener("click", () => {
      const nextTheme = activeTheme === "light" ? "dark" : "light";
      try { localStorage.setItem(STORAGE_KEY, nextTheme); } catch {}
      applyTheme(nextTheme);
      render();
      window.dispatchEvent(new CustomEvent("lecturesift:theme-change", {detail: {theme: nextTheme}}));
    });

    const publicTools = header.querySelector(".public-header-tools");
    if (publicTools) publicTools.insertBefore(button, publicTools.firstChild);
    else header.insertBefore(button, header.querySelector(".language-switcher,.back-link") || null);
    render();
  };

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", setupToggle, {once: true});
  else setupToggle();

  systemTheme.addEventListener?.("change", event => {
    let hasSavedTheme = false;
    try { hasSavedTheme = Boolean(localStorage.getItem(STORAGE_KEY)); } catch {}
    if (!hasSavedTheme) {
      applyTheme(event.matches ? "light" : "dark");
      renderToggle();
    }
  });
})();
