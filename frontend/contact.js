const CONTACT_API = "https://lecturesift-backend.onrender.com";
const contactForm = document.getElementById("contactForm");
const contactStatus = document.getElementById("contactStatus");

contactForm?.addEventListener("submit", async event => {
  event.preventDefault();
  if (!contactForm.reportValidity()) return;
  const button = contactForm.querySelector('button[type="submit"]');
  const data = new FormData(contactForm);
  button.disabled = true;
  contactStatus.hidden = false;
  contactStatus.textContent = "Mesajın güvenli biçimde gönderiliyor…";
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
    if (!response.ok) throw new Error(body?.detail?.message || "Mesaj gönderilemedi.");
    contactStatus.textContent = `${body.message} Takip numaran: ${body.reference}`;
    contactForm.reset();
  } catch (error) {
    contactStatus.textContent = `${error.message} Dilersen support@lecturesift.com adresine e-posta gönderebilirsin.`;
  } finally {
    button.disabled = false;
  }
});
