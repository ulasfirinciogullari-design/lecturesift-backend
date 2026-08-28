(function () {
  const API = "https://lecturesift-backend.onrender.com";
  const card = document.querySelector(".legal-card");
  if (!card || document.querySelector("[data-legal-operator]")) return;
  const i18n = window.LectureSiftI18n;
  const t = (key, fallback) => i18n?.t(key, fallback) || fallback;
  const ensureDistanceSalesLink = host => {
    if (!host || host.querySelector('a[href*="distance-sales"]')) return;
    const link = document.createElement("a");
    link.href = i18n?.localizedPath?.(i18n.language, "/distance-sales.html") || "/distance-sales.html";
    link.textContent = t("legal.distanceSales", "Mesafeli Satış Sözleşmesi");
    host.append(link);
  };
  ensureDistanceSalesLink(document.querySelector(".legal-nav"));
  ensureDistanceSalesLink(document.querySelector(".legal-footer nav"));
  const addRow = (list, label, value, href = "") => {
    if (!value) return;
    const row = document.createElement("div");
    const term = document.createElement("dt");
    const detail = document.createElement("dd");
    term.textContent = label;
    if (href) {
      const link = document.createElement("a");
      link.href = href;
      link.textContent = value;
      detail.append(link);
    } else {
      detail.textContent = value;
    }
    row.append(term, detail);
    list.append(row);
  };
  fetch(`${API}/billing/operator`, {cache:"no-store"})
    .then(response => response.ok ? response.json() : null)
    .then(operator => {
      if (!operator?.configured) return;
      const section = document.createElement("section");
      section.dataset.legalOperator = "true";
      section.className = "legal-operator";
      const heading = document.createElement("h2");
      heading.textContent = t("legal.operatorTitle", "Hizmet sağlayıcı bilgileri");
      const list = document.createElement("dl");
      addRow(list, t("legal.operatorName", "Unvan / ad"), operator.operator_name);
      addRow(list, t("legal.address", "Açık adres"), operator.address);
      addRow(list, t("legal.country", "Ülke"), operator.country);
      addRow(list, t("legal.phone", "Telefon"), operator.phone, `tel:${operator.phone}`);
      addRow(list, t("legal.email", "E-posta"), operator.email, `mailto:${operator.email}`);
      addRow(list, t("legal.taxId", "Vergi kimliği"), operator.tax_id);
      addRow(list, t("legal.registrationId", "Sicil / kayıt"), operator.registration_id);
      addRow(list, t("legal.mersisId", "MERSİS numarası"), operator.mersis_id);
      addRow(list, t("legal.tradeRegistry", "Ticaret sicili"), operator.trade_registry);
      addRow(list, t("legal.kepAddress", "KEP adresi"), operator.kep_address, operator.kep_address ? `mailto:${operator.kep_address}` : "");
      addRow(list, t("legal.chamberName", "Meslek odası"), operator.chamber_name);
      section.append(heading, list);
      card.prepend(section);
    })
    .catch(() => {});
})();
