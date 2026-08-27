(() => {
  const API = "https://lecturesift-backend.onrender.com";
  const esc = value => String(value ?? "").replace(/[&<>"']/g, char => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"})[char]);

  async function load() {
    const card = document.querySelector(".legal-card");
    if (!card) return;
    try {
      const response = await fetch(`${API}/legal/config`, {cache:"no-store"});
      const body = await response.json();
      if (!response.ok) throw new Error("legal config");
      const existing = card.querySelector(".legal-identity-live");
      existing?.remove();
      const section = document.createElement("section");
      section.className = "legal-identity-live";
      if (body.configured) {
        section.innerHTML = `<h2>Hizmet sağlayıcı ve veri sorumlusu</h2><div class="notice"><strong>${esc(body.entity_name)}</strong><br>${esc(body.address)}<br>Vergi/kimlik no: ${esc(body.tax_id)}${body.registry_id ? `<br>Sicil/MERSİS: ${esc(body.registry_id)}` : ""}<br>E-posta: ${esc(body.email)}${body.phone ? `<br>Telefon: ${esc(body.phone)}` : ""}</div>`;
        document.querySelectorAll(".notice").forEach(node => {
          if (node !== section.querySelector(".notice") && /canlı satış|tam unvan|taslak niteliğinde|tamamlanması zorunlu/i.test(node.textContent || "")) {
            node.innerHTML = `<strong>Resmî kimlik bilgileri yapılandırıldı.</strong> Ödeme ekranındaki satıcı bilgileri ve bu sayfadaki bilgiler birlikte geçerlidir.`;
          }
        });
      } else {
        section.innerHTML = `<h2>Hizmet sağlayıcı bilgileri</h2><div class="notice"><strong>Canlı satış henüz kapalıdır.</strong> Resmî unvan, açık adres ve vergi/sicil bilgileri tamamlanmadan LectureSift kartlı tahsilat başlatmaz.</div>`;
      }
      const lead = card.querySelector(".lead") || card.firstElementChild;
      lead?.insertAdjacentElement("afterend", section);
    } catch {
      const note = document.createElement("div");
      note.className = "notice legal-identity-live";
      note.textContent = "Hizmet sağlayıcı bilgileri şu anda doğrulanamadı. Canlı satış bu durumda etkinleştirilmez.";
      card.prepend(note);
    }
  }
  load();
})();
