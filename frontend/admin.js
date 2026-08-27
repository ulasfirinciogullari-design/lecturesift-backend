(() => {
  const API = "https://lecturesift-backend.onrender.com";
  const KEY = "lecturesift-admin-token";
  const $ = id => document.getElementById(id);
  const esc = value => String(value ?? "").replace(/[&<>"']/g, char => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"})[char]);

  function token() { return sessionStorage.getItem(KEY) || ""; }

  async function api(path, options = {}) {
    const response = await fetch(`${API}${path}`, {
      ...options,
      headers: {"Content-Type":"application/json", Authorization:`Bearer ${token()}`, ...(options.headers || {})},
      cache:"no-store",
    });
    const body = await response.json().catch(() => ({}));
    if (!response.ok) {
      const detail = body.detail || body;
      throw new Error(detail.message || "İşlem tamamlanamadı.");
    }
    return body;
  }

  function formatTry(value) {
    return new Intl.NumberFormat("tr-TR", {style:"currency", currency:"TRY"}).format(Number(value || 0) / 100);
  }

  function status(message, error = false) {
    const node = $("adminLoginStatus");
    node.textContent = message;
    node.classList.toggle("error", error);
    node.hidden = false;
  }

  function renderOrders(items) {
    $("adminOrders").innerHTML = items.length ? items.map(item => `
      <article class="rollout-admin-row"><div><strong>${esc(item.reference)}</strong><br><small>${esc(item.email || "")} · ${esc(item.plan_code)} · ${esc(item.interval)}</small></div><div><strong>${esc(formatTry(item.amount_minor))}</strong><br><small>${esc(item.status)}</small></div><div class="rollout-admin-actions"><button class="approve" data-order="${esc(item.reference)}" data-approve="1">Onayla</button><button class="reject" data-order="${esc(item.reference)}" data-approve="0">Reddet</button></div></article>`).join("") : '<p class="rollout-muted">Bekleyen sipariş yok.</p>';
    $("adminOrders").querySelectorAll("[data-order]").forEach(button => button.onclick = async () => {
      button.disabled = true;
      try {
        await api(`/admin/manual-orders/${encodeURIComponent(button.dataset.order)}/decision`, {method:"POST", body:JSON.stringify({approve:button.dataset.approve === "1"})});
        await load();
      } catch (error) { status(error.message, true); button.disabled = false; }
    });
  }

  function renderRewards(items) {
    $("adminRewards").innerHTML = items.length ? items.map(item => `
      <article class="rollout-admin-row"><div><strong>@${esc(item.handle)}</strong><br><small>${esc(item.email || "")}</small></div><div><strong>+${esc(item.minutes)} dk</strong><br><small>${esc(item.status)}</small></div><div class="rollout-admin-actions"><button class="approve" data-reward="${esc(item.id)}" data-approve="1">Onayla</button><button class="reject" data-reward="${esc(item.id)}" data-approve="0">Reddet</button></div></article>`).join("") : '<p class="rollout-muted">Bekleyen bonus talebi yok.</p>';
    $("adminRewards").querySelectorAll("[data-reward]").forEach(button => button.onclick = async () => {
      button.disabled = true;
      try {
        await api(`/admin/instagram-rewards/${encodeURIComponent(button.dataset.reward)}/decision`, {method:"POST", body:JSON.stringify({approve:button.dataset.approve === "1"})});
        await load();
      } catch (error) { status(error.message, true); button.disabled = false; }
    });
  }

  async function load() {
    if (!token()) return;
    try {
      const [orders, rewards] = await Promise.all([
        api("/admin/manual-orders?status=pending"),
        api("/admin/instagram-rewards?status=pending_verification"),
      ]);
      $("adminLogin").hidden = true;
      $("adminDashboard").hidden = false;
      renderOrders(orders.orders || []);
      renderRewards(rewards.rewards || []);
    } catch (error) {
      sessionStorage.removeItem(KEY);
      $("adminLogin").hidden = false;
      $("adminDashboard").hidden = true;
      status(error.message, true);
    }
  }

  $("adminLoginForm").onsubmit = event => {
    event.preventDefault();
    sessionStorage.setItem(KEY, $("adminToken").value.trim());
    load();
  };
  $("adminRefresh").onclick = load;
  if (token()) load();
})();
