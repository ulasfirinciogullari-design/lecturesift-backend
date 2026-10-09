/* Retained lesson content is fetched only after an administrator opens it. */
let adminWorkOwner = null;
let adminWorkScopedJobs = [];
let adminWorkState = {loading:false, error:''};
let adminWorkListVersion = 0;
let adminWorkDetailVersion = 0;

function resetAdminWork() {
  adminWorkOwner = null;
  adminWorkScopedJobs = [];
  adminWorkState = {loading:false, error:''};
  adminWorkListVersion += 1;
  adminWorkDetailVersion += 1;
  document.getElementById('adminJobDialogBody')?.replaceChildren();
  document.getElementById('adminJobScope')?.replaceChildren();
}

function adminJobsPath() {
  return `/billing/admin/jobs?limit=250${adminWorkOwner ? `&owner_id=${encodeURIComponent(adminWorkOwner.id)}` : ''}`;
}

async function showAdminUserWork(user = null) {
  adminWorkOwner = user;
  adminWorkScopedJobs = [];
  admin$('adminUserDialog')?.close();
  admin$('adminJobStatus').value = 'all';
  admin$('adminJobSearch').value = '';
  const scope = admin$('adminJobScope');
  scope.replaceChildren();
  if (user) {
    const label = document.createElement('span');
    label.textContent = `${user.name || user.email} · Kullanıcının dersleri`;
    const clear = document.createElement('button');
    clear.type = 'button';
    clear.className = 'admin-action';
    clear.textContent = 'Tüm kullanıcılar';
    clear.addEventListener('click', () => showAdminUserWork());
    scope.append(label, clear);
  }
  activateAdminView('jobs', {focus:true});
  return refreshAdminWorkList();
}

async function refreshAdminWorkList() {
  const user = adminWorkOwner;
  const version = ++adminWorkListVersion;
  const path = adminJobsPath();
  adminWorkState = {loading:true, error:''};
  adminWorkList([]);
  try {
    const body = await adminRequest(path);
    if (version !== adminWorkListVersion || path !== adminJobsPath()) return;
    if (!Array.isArray(body.jobs)) throw new Error('Ders listesi doğrulanamadı.');
    adminWorkState = {loading:false, error:''};
    if (user) adminWorkScopedJobs = body.jobs;
    else adminState.jobs = body.jobs;
    adminLoadedSections.add('jobs');
    adminLoadErrors.delete('jobs');
    applyAdminFilters();
  } catch (error) {
    if (version !== adminWorkListVersion) return;
    adminWorkState = {loading:false, error:error.message};
    adminWorkList([]);
    adminNotice(error.message, true);
  }
}

function adminWorkStatusLabel(job) {
  if (job.status === 'error' && ['LS-BILL-10', 'LS-GUEST-04'].includes(job.error_code)) return 'Kullanım sınırı';
  return adminStatusLabel(job.status);
}

function adminWorkList(jobs) {
  if (adminWorkState.loading || adminWorkState.error) {
    const target = admin$('adminJobs');
    target.textContent = adminWorkState.loading ? 'Dersler yükleniyor…' : adminWorkState.error;
    if (adminWorkState.error) {
      const retry = document.createElement('button');
      retry.type = 'button';
      retry.className = 'admin-action';
      retry.textContent = 'Tekrar dene';
      retry.addEventListener('click', () => showAdminUserWork(adminWorkOwner));
      target.append(retry);
    }
    return;
  }
  const rows = jobs.map(job => `<tr><td data-label="Ders"><strong>${adminEscape(job.title || 'Ders analizi')}</strong><br><small>${adminEscape(job.job_id)}</small><br><small>${adminEscape(job.source_file_count ? `${job.source_file_count} kaynak dosya` : '')}</small></td><td data-label="Kullanıcı"><strong>${adminEscape(job.owner_name || '')}</strong><br><small>${adminEscape(job.owner_email || job.owner_id || 'Misafir/hesapsız')}</small></td><td data-label="Durum"><span class="status-pill ${job.status === 'done' ? 'paid' : ''}">${adminEscape(adminWorkStatusLabel(job))}</span><div class="admin-progress"><span style="width:${Math.max(0, Math.min(100, Number(job.percent || 0)))}%"></span></div><small>%${Number(job.percent || 0)} · ${adminEscape(job.stage || '—')}</small>${job.status === 'error' && job.public_error ? `<p>${adminEscape(job.public_error)}</p>` : ''}</td><td data-label="Başlangıç">${adminEscape(adminRelativeDate(job.created))}</td><td data-label="İşlem"><button class="admin-action" type="button" data-job-open="${adminEscape(job.job_id)}">İncele</button>${job.status === 'error' && job.error_code ? `<p>${adminEscape(job.error_code)}</p>` : ''}</td></tr>`).join('');
  admin$('adminJobs').innerHTML = `<table class="admin-table admin-record-table"><thead><tr><th>Ders / kaynaklar</th><th>Kullanıcı</th><th>Durum ve ilerleme</th><th>Başlangıç</th><th>İşlem</th></tr></thead><tbody>${rows || '<tr><td colspan="5">Bu filtreye uygun ders bulunamadı.</td></tr>'}</tbody></table>`;
  admin$('adminJobs').querySelectorAll('[data-job-open]').forEach(button => button.addEventListener('click', () => openAdminJob(button.dataset.jobOpen)));
}

function appendAdminWorkSection(root, title, content, open = false) {
  if (!content) return;
  const section = document.createElement('details');
  section.className = 'admin-work-section';
  section.open = open;
  const heading = document.createElement('summary');
  heading.textContent = title;
  const body = document.createElement('div');
  body.className = 'admin-work-text';
  body.textContent = content;
  section.append(heading, body);
  root.append(section);
}

function adminWorkText(value) {
  if (Array.isArray(value)) return value.map(adminWorkText).filter(Boolean).join('\n\n');
  if (value && typeof value === 'object') return Object.entries(value).map(([key, item]) => `${key}: ${adminWorkText(item)}`).join('\n');
  return value == null ? '' : String(value);
}

async function openAdminJob(jobId) {
  const version = ++adminWorkDetailVersion;
  const dialog = admin$('adminJobDialog');
  const root = admin$('adminJobDialogBody');
  admin$('adminJobDialogTitle').textContent = 'Ders ayrıntısı';
  root.textContent = 'Ders ve çıktıları yükleniyor…';
  if (!dialog.open) dialog.showModal();
  try {
    const body = await adminRequest(`/billing/admin/jobs/${encodeURIComponent(jobId)}`, {timeoutMs:45000});
    if (version !== adminWorkDetailVersion || !dialog.open) return;
    const job = body.job;
    const result = body.result;
    admin$('adminJobDialogTitle').textContent = result?.title || job.title || 'Ders ayrıntısı';
    root.innerHTML = `<div class="admin-detail-summary"><article><small>Kullanıcı</small><strong>${adminEscape(job.owner_email || job.owner_id || 'Misafir/hesapsız')}</strong></article><article><small>Durum</small><strong>${adminEscape(adminWorkStatusLabel(job))} · %${Number(job.percent || 0)}</strong></article><article><small>Başlangıç</small><strong>${adminEscape(adminDate(job.created))}</strong></article><article><small>Kaynak</small><strong>${Number(job.source_file_count || 0)} dosya · ${(Number(job.file_size_bytes || 0) / 1048576).toLocaleString(adminLocale(), {maximumFractionDigits:1})} MB</strong></article><article><small>Gereken kullanım hakkı</small><strong>${job.billable_minutes == null ? 'Henüz hesaplanmadı' : `${adminEscape(job.billable_minutes)} dakika`}</strong><small>Hesaptan düşülen miktarı göstermez.</small></article>${job.document_words != null ? `<article><small>Belge uzunluğu</small><strong>${Number(job.document_words).toLocaleString(adminLocale())} kelime</strong></article>` : ''}<article><small>Çıktı dili</small><strong>${adminEscape(job.options?.output_language || '—')}</strong></article></div>`;
    const note = document.createElement('p');
    note.className = 'empty-copy';
    note.textContent = 'Saklanan ders çıktıları gösterilir. Kaynak dosyalar işlem sonunda temizlenebilir; silinmiş veya süresi dolmuş dersler burada bulunmaz. İçerik incelemeleri yönetici işlem kaydına eklenir.';
    root.append(note);
    if (!result) {
      appendAdminWorkSection(root, 'İşlem bilgisi', body.result_message || (job.status === 'error' && (job.public_error || job.error_code)) || 'Dersin çıktıları henüz hazır değil.', true);
      return;
    }
    const downloads = document.createElement('div');
    downloads.className = 'admin-work-downloads';
    for (const artifact of result.artifacts || []) {
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'admin-action';
      button.textContent = `${artifact.label || artifact.file} indir`;
      button.addEventListener('click', async () => {
        button.disabled = true;
        try {
          const blob = await adminRequest(`/billing/admin/jobs/${encodeURIComponent(jobId)}/artifacts/${encodeURIComponent(artifact.file)}`, {binary:true, timeoutMs:120000});
          if (!dialog.open || version !== adminWorkDetailVersion) return;
          const url = URL.createObjectURL(blob);
          const link = document.createElement('a');
          link.href = url;
          link.download = artifact.file;
          document.body.append(link);
          link.click();
          link.remove();
          setTimeout(() => URL.revokeObjectURL(url), 30000);
        } catch (error) { if (dialog.open && version === adminWorkDetailVersion) adminNotice(error.message, true); }
        finally { button.disabled = false; }
      });
      downloads.append(button);
    }
    root.append(downloads);
    appendAdminWorkSection(root, 'Kaynak dosya adları', (result.source_files || []).join('\n'));
    appendAdminWorkSection(root, 'Özet', result.summary, true);
    appendAdminWorkSection(root, 'Önemli noktalar', adminWorkText(result.key_points));
    appendAdminWorkSection(root, 'Kavramlar', (result.important_terms || []).map(item => `${item.term || ''}: ${item.definition || ''}`).join('\n\n'));
    appendAdminWorkSection(root, 'Ders notları', (result.notes || []).map(item => [item.heading, item.content, ...(item.bullets || [])].filter(Boolean).join('\n')).join('\n\n'));
    appendAdminWorkSection(root, 'Sınava hazırlık', adminWorkText(result.exam_focus));
    const quiz = (result.quiz || []).map((item, index) => `${index + 1}. ${item.question || ''}\n${(item.options || []).map((option, i) => `${i + 1}) ${option}`).join('\n')}\nCevap: ${Number.isInteger(item.answer_index) && item.options?.[item.answer_index] != null ? item.options[item.answer_index] : item.answer || '—'}\n${item.explanation || ''}`).join('\n\n');
    appendAdminWorkSection(root, 'Quiz ve cevaplar', quiz);
    appendAdminWorkSection(root, 'Bilgi kartları', (result.flashcards || []).map(item => `${item.front || ''}\n${item.back || ''}`).join('\n\n'));
    appendAdminWorkSection(root, result.transcript_truncated ? 'Transkript (önizleme; tam metin çıktı dosyasında)' : 'Transkript', result.transcript);
    if (!result.summary && !result.transcript && !downloads.children.length) appendAdminWorkSection(root, 'Çıktı bilgisi', 'Bu derste seçilen metin çıktısı veya indirilebilir dosya bulunmuyor.', true);
  } catch (error) {
    if (version !== adminWorkDetailVersion || !dialog.open) return;
    root.textContent = error.message;
    const retry = document.createElement('button');
    retry.type = 'button';
    retry.className = 'admin-action';
    retry.textContent = 'Tekrar dene';
    retry.addEventListener('click', () => openAdminJob(jobId));
    root.append(retry);
  }
}
