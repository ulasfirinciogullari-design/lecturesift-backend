const CONTACT_API = "https://api.lecturesift.com";
const contactForm = document.getElementById("contactForm");
const contactStatus = document.getElementById("contactStatus");
const contactText = (key, fallback) => window.LectureSiftI18n?.t(key, fallback) || fallback;

function contactErrorMessage(response, body) {
  const message = typeof body?.detail === "string" ? body.detail : body?.detail?.message;
  // Translate known API validation messages while preserving other actionable details.
  if (typeof message === "string" && message.trim()) {
    return window.LectureSiftI18n?.exact(message) || message;
  }
  if (response.status === 429) return contactText("contact.rateLimited", "Çok fazla deneme yapıldı. Lütfen biraz sonra tekrar dene.");
  if (response.status === 422) return contactText("contact.invalidForm", "Formdaki bilgileri kontrol edip tekrar dene.");
  return contactText("contact.failed", "Mesaj gönderilemedi.");
}

contactForm?.addEventListener("submit", async event => {
  event.preventDefault();
  if (!contactForm.reportValidity()) return;
  const button = contactForm.querySelector('button[type="submit"]');
  const data = new FormData(contactForm);
  button.disabled = true;
  contactStatus.hidden = false;
  contactStatus.textContent = contactText("contact.sending", "Mesajın güvenli biçimde gönderiliyor…");
  let failureMessage = contactText("contact.failed", "Mesaj gönderilemedi.");
  try {
    const response = await fetch(`${CONTACT_API}/contact/messages`, {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({
        name: String(data.get("name") || "").trim(),
        email: String(data.get("email") || "").trim(),
        topic: String(data.get("topic") || "").trim(),
        message: String(data.get("message") || "").trim(),
        order_reference: String(data.get("order_reference") || "").trim(),
      }),
    });
    const body = await response.json().catch(() => ({}));
    if (!response.ok) {
      failureMessage = contactErrorMessage(response, body);
      throw new Error(failureMessage);
    }
    contactStatus.textContent = contactText("contact.received", "Mesajın alındı. Destek ekibi en kısa sürede e-posta adresinden dönecek.");
    if (typeof body?.reference === "string" && body.reference) {
      contactStatus.textContent += ` ${contactText("contact.reference", "Takip numaran:")} ${body.reference}`;
    }
    contactForm.reset();
  } catch {
    contactStatus.textContent = `${failureMessage} ${contactText("contact.emailFallback", "Dilersen support@lecturesift.com adresine e-posta gönderebilirsin.")}`;
  } finally {
    button.disabled = false;
  }
});
