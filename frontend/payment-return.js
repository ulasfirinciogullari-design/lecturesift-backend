(() => {
  const API = "https://lecturesift-backend.onrender.com";
  const TOKEN_KEY = "lecturesift-billing-token";
  const params = new URLSearchParams(location.search);
  const status = params.get("status") || "unknown";
  const order = params.get("order") || "";
  const $ = id => document.getElementById(id);

  $("paymentOrder").textContent = order ? `Sipariş: ${order}` : "";
  if (window.parent && window.parent !== window) {
    window.parent.postMessage({type:"lecturesift-payment-return",status,order}, location.origin);
  }

  async function poll(attempt = 0) {
    const token = localStorage.getItem(TOKEN_KEY) || "";
    if (!token || !order || attempt > 30) {
      $("paymentTitle").textContent = status === "failed" ? "Ödeme tamamlanamadı" : "Ödeme bildirimi bekleniyor";
      $("paymentText").textContent = status === "failed" ? "Kart ekranı başarısız sonuç döndürdü. Kesin durum hesabındaki ödeme geçmişinde gösterilir." : "Kesin doğrulama henüz tamamlanmadı. Hesabındaki ödeme geçmişinden kontrol edebilirsin.";
      document.querySelector(".spinner")?.remove();
      return;
    }
    try {
      const response = await fetch(`${API}/billing/purchases/${encodeURIComponent(order)}`, {headers:{Authorization:`Bearer ${token}`},cache:"no-store"});
      const body = await response.json().catch(() => ({}));
      if (response.ok) {
        const verified = body.purchase?.status;
        if (verified === "paid") {
          $("paymentTitle").textContent = "Ödeme doğrulandı";
          $("paymentText").textContent = "Plan, dakika ve indirme hakların hesabına tanımlandı.";
          document.querySelector(".spinner")?.remove();
          return;
        }
        if (["failed","review_required"].includes(verified)) {
          $("paymentTitle").textContent = verified === "review_required" ? "Ödeme incelemede" : "Ödeme tamamlanamadı";
          $("paymentText").textContent = verified === "review_required" ? "Ödeme tutarı güvenlik kontrolüne alındı. Destek ekibi sipariş referansıyla inceleyecek." : "Ödeme başarısız oldu. Başka bir ödeme yöntemiyle yeniden deneyebilirsin.";
          document.querySelector(".spinner")?.remove();
          return;
        }
      }
    } catch {}
    setTimeout(() => poll(attempt + 1), 2000);
  }

  poll();
})();
