(() => {
  'use strict';
  const root = document.getElementById('workspaceLibraryPanel');
  if (!root) return;
  const $ = id => document.getElementById(id);
  const t = (key, vars = {}) => Object.entries(vars).reduce((s, [k, v]) => s.replace(`{${k}}`, v), window.LectureSiftI18n?.t(key) || key);
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'})[c]);
  const locale = () => window.LectureSiftI18n?.locale || 'tr-TR';
  const workspacePath = () => window.LectureSiftI18n?.localizedPath?.(window.LectureSiftI18n.language, '/workspace.html') || '/workspace.html';
  let state = {folders: [], jobs: []};
  let folder = 'all';
  let busy = false;
  let loaded = false;
  const notice = (message, error = false) => {
    $('libraryNotice').textContent = message;
    $('libraryNotice').hidden = !message;
    $('libraryNotice').classList.toggle('error', error);
  };
  async function request(path, options = {}) {
    const token = localStorage.getItem('lecturesift-billing-token');
    if (!token) throw new Error(t('library.login'));
    const response = await fetch(`https://api.lecturesift.com${path}`, {...options, headers: {Authorization:`Bearer ${token}`, 'Content-Type':'application/json'}, cache:'no-store'});
    const body = await response.json().catch(() => null);
    if (!response.ok) throw new Error(body?.detail?.message || t('library.error'));
    if (!body || typeof body !== 'object' || Array.isArray(body)) throw new Error(t('library.error'));
    return body;
  }
  const date = seconds => new Intl.DateTimeFormat(locale(), {dateStyle:'medium'}).format(new Date(Number(seconds) * 1000));
  function render() {
    const folders = [{id:'all', name:t('library.all')}, {id:'', name:t('library.root')}, ...state.folders];
    if (!folders.some(item => item.id === folder)) folder = 'all';
    $('libraryFolders').innerHTML = folders.map(item => `<button type="button" data-library-folder="${esc(item.id)}" aria-pressed="${item.id === folder}"><span aria-hidden="true">${item.id === 'all' ? '▤' : '▱'}</span>${esc(item.name)}<small>${state.jobs.filter(job => item.id === 'all' || (job.folder_id || '') === item.id).length}</small></button>`).join('');
    $('libraryFolderActions').hidden = !folder || folder === 'all';
    const needle = $('librarySearch').value.trim().toLocaleLowerCase(locale());
    const jobs = state.jobs.filter(job => (folder === 'all' || (job.folder_id || '') === folder) && (job.title || t('history.studyPack')).toLocaleLowerCase(locale()).includes(needle));
    $('libraryCount').textContent = t('library.count', {count:new Intl.NumberFormat(locale()).format(jobs.length)});
    if (jobs.length && jobs.every(job => Number.isFinite(job.stored_bytes))) {
      $('libraryCount').textContent += ` · ${new Intl.NumberFormat(locale(), {maximumFractionDigits:1}).format(jobs.reduce((n, job) => n + job.stored_bytes, 0) / 1048576)} MB`;
    }
    $('libraryLessons').innerHTML = jobs.map(job => {
      const label = job.title || t(job.job_type === 'audio_export' ? 'history.audioExport' : 'history.studyPack');
      const status = t(job.status === 'done' ? 'history.ready' : job.status === 'error' ? 'history.failed' : 'history.processing');
      const choices = [{id:'', name:t('library.root')}, ...state.folders].map(item => `<option value="${esc(item.id)}" ${item.id === (job.folder_id || '') ? 'selected' : ''}>${esc(item.name)}</option>`).join('');
      return `<article class="library-lesson"><div class="library-lesson-icon" aria-hidden="true">${job.job_type === 'audio_export' ? '♫' : '▤'}</div><div class="library-lesson-copy"><h2>${esc(label)}</h2><p><time>${esc(date(job.created))}</time><span class="library-status ${job.status === 'done' ? 'ready' : ''}">${esc(status)}</span></p><small>${esc(t('library.expires', {date:date(job.expires_at)}))}</small></div><div class="library-lesson-actions"><label><span class="sr-only">${esc(t('library.move'))}</span><select data-library-move="${esc(job.job_id)}">${choices}</select></label><div>${job.status !== 'error' ? `<a class="library-open" href="${esc(workspacePath())}?job=${encodeURIComponent(job.job_id)}#study">${esc(t('history.open'))} ↗</a>` : ''}<button class="library-delete" type="button" data-library-delete="${esc(job.job_id)}" ${job.can_delete ? '' : 'disabled'}>${esc(t('library.delete'))}</button></div></div></article>`;
    }).join('') || `<div class="library-empty"><span aria-hidden="true">▤</span><p>${esc(t('library.empty'))}</p><a href="${esc(workspacePath())}?source=upload">${esc(t('redesign.new'))} →</a></div>`;
  }
  async function load() {
    const next = await request('/library');
    const object = value => value !== null && typeof value === 'object' && !Array.isArray(value);
    const optionalText = value => value == null || typeof value === 'string';
    const timestamp = value => typeof value === 'number' && Number.isFinite(new Date(value * 1000).getTime());
    if (!Array.isArray(next.folders) || !next.folders.every(item => object(item) && typeof item.id === 'string' && item.id && typeof item.name === 'string')
      || !Array.isArray(next.jobs) || !next.jobs.every(job => object(job) && typeof job.job_id === 'string' && job.job_id
        && optionalText(job.title) && optionalText(job.folder_id) && optionalText(job.job_type)
        && typeof job.status === 'string' && typeof job.can_delete === 'boolean'
        && timestamp(job.created) && timestamp(job.expires_at)
        && (job.stored_bytes == null || (typeof job.stored_bytes === 'number' && Number.isFinite(job.stored_bytes) && job.stored_bytes >= 0)))) {
      throw new Error(t('library.error'));
    }
    state = next;
    loaded = true;
    render();
  }
  async function refreshAfterChange() {
    // The write has already succeeded. Keep that confirmed state visible even
    // if the follow-up read fails, so the user is not prompted to repeat it.
    render();
    try { await load(); }
    catch { notice(t('library.refreshError'), true); return false; }
  }
  async function run(action, message = '') {
    if (busy) return;
    busy = true;
    root.setAttribute('aria-busy', 'true');
    notice('');
    try { const refreshed = await action(); if (message && refreshed !== false) notice(message); }
    catch (error) { notice(error.message || t('library.error'), true); }
    finally { busy = false; root.removeAttribute('aria-busy'); }
  }
  async function saveFolder(rename = false) {
    if (busy) return;
    const name = prompt(t('library.folderName'), rename ? state.folders.find(item => item.id === folder)?.name || '' : '');
    if (!name?.trim()) return;
    await run(async () => {
      const body = await request(rename ? `/library/folders/${encodeURIComponent(folder)}` : '/library/folders', {method:rename ? 'PATCH' : 'POST', body:JSON.stringify({name:name.trim()})});
      if (!body.folder || typeof body.folder.id !== 'string' || typeof body.folder.name !== 'string') throw new Error(t('library.error'));
      state.folders = [...state.folders.filter(item => item.id !== body.folder.id), body.folder];
      folder = body.folder.id;
      return refreshAfterChange();
    }, t('library.saved'));
  }
  $('librarySearch').addEventListener('input', render);
  $('libraryRefresh').addEventListener('click', () => run(load));
  $('libraryNewFolder').addEventListener('click', () => saveFolder());
  $('libraryRename').addEventListener('click', () => saveFolder(true));
  $('libraryDeleteFolder').addEventListener('click', () => {
    if (busy || !folder || folder === 'all' || !confirm(t('library.confirmFolder'))) return;
    run(async () => {
      await request(`/library/folders/${encodeURIComponent(folder)}`, {method:'DELETE'});
      state.folders = state.folders.filter(item => item.id !== folder);
      state.jobs = state.jobs.map(job => job.folder_id === folder ? {...job, folder_id:null} : job);
      folder = '';
      return refreshAfterChange();
    }, t('library.deleted'));
  });
  $('libraryFolders').addEventListener('click', event => {
    const button = event.target.closest('[data-library-folder]');
    if (button && !busy) { folder = button.dataset.libraryFolder; render(); }
  });
  $('libraryLessons').addEventListener('change', event => {
    const input = event.target.closest('[data-library-move]');
    if (!input) return;
    if (busy) { render(); return; }
    const jobId = input.dataset.libraryMove, folderId = input.value || null;
    run(async () => {
      try {
        await request(`/library/lessons/${encodeURIComponent(jobId)}`, {method:'PATCH', body:JSON.stringify({folder_id:folderId})});
        state.jobs = state.jobs.map(job => job.job_id === jobId ? {...job, folder_id:folderId} : job);
        return refreshAfterChange();
      }
      catch (error) { render(); throw error; }
    }, t('library.saved'));
  });
  $('libraryLessons').addEventListener('click', event => {
    const button = event.target.closest('[data-library-delete]');
    if (!button || busy || !confirm(t('library.confirmDelete'))) return;
    run(async () => {
      const jobId = button.dataset.libraryDelete;
      await request(`/library/lessons/${encodeURIComponent(jobId)}`, {method:'DELETE'});
      state.jobs = state.jobs.filter(job => job.job_id !== jobId);
      return refreshAfterChange();
    }, t('library.deleted'));
  });
  document.addEventListener('lecturesift:library-open', () => run(load));
  document.addEventListener('lecturesift:language', () => { if (loaded) render(); });
  if (location.hash === '#library') run(load);
})();
