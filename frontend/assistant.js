(function () {
  if (document.querySelector('.assistant-launch') || /\/admin(?:\.html)?$/.test(location.pathname)) return;
  const API = 'https://api.lecturesift.com';
  const t = key => window.LectureSiftAssistantCopy.t(key);
  const language = () => window.LectureSiftI18n?.language || document.documentElement.lang || 'tr';
  const token = () => localStorage.getItem('lecturesift-billing-token') || '';
  const path = value => window.LectureSiftI18n?.localizedPath?.(language(), value) || value;
  const css = document.createElement('link'); css.rel = 'stylesheet'; css.href = '/assistant.css?v=1'; document.head.append(css);
  const launch = document.createElement('button'); launch.className = 'assistant-launch'; launch.textContent = t('title'); launch.type = 'button'; launch.setAttribute('aria-haspopup', 'dialog'); document.body.append(launch);
  const dialog = document.createElement('dialog'); dialog.className = 'assistant-dialog'; dialog.setAttribute('aria-labelledby', 'assistantTitle');
  dialog.innerHTML = '<div class="assistant-layout"><header class="assistant-header"><h2 id="assistantTitle"></h2><button type="button" class="assistant-close">×</button></header><div class="assistant-messages" role="log" aria-live="polite"></div><p class="assistant-status" role="status"></p><details class="assistant-details"><summary></summary><p></p></details><form class="assistant-compose"><div class="assistant-attachment" hidden><span></span><button type="button">×</button></div><textarea maxlength="3000" required></textarea><div class="assistant-toolbar"><button type="button" class="assistant-attach">＋</button><button type="button" class="assistant-clear"></button><button type="submit"></button></div><input type="file" accept="image/jpeg,image/png,image/webp,video/mp4,video/webm,video/quicktime" hidden></form></div>';
  document.body.append(dialog);
  const $ = selector => dialog.querySelector(selector);
  $('#assistantTitle').textContent = t('title'); $('.assistant-close').setAttribute('aria-label',t('close'));
  $('textarea').placeholder = t('ask'); $('textarea').setAttribute('aria-label',t('ask'));
  $('.assistant-clear').textContent = t('clear'); $('button[type=submit]').textContent=t('send');
  $('.assistant-attach').setAttribute('aria-label',t('attach')); $('.assistant-details summary').textContent=t('credits');
  $('.assistant-details p').textContent = t('rules') + ' ' + t('media');
  $('.assistant-attachment button').setAttribute('aria-label',t('close'));
  let history = [], attachment = null, sessionToken = token(), busy = false, available = false, trialCount = 0, pending = null;
  const setStatus = text => { $('.assistant-status').textContent = text; };
  async function request(endpoint, data, auth = true) {
    const headers = {'Content-Type':'application/json'};
    if (auth && token()) headers.Authorization = `Bearer ${token()}`;
    const response = await fetch(API + endpoint, {method:data ? 'POST':'GET', headers, body:data ? JSON.stringify(data):undefined, signal:AbortSignal.timeout(65000)});
    const body = await response.json();
    if (!response.ok) { const error = new Error(body.detail?.code || 'LS-ASSIST-07'); error.status=response.status; throw error; }
    return body;
  }
  const safeActions = {workspace:'/workspace.html',plans:'/plans.html',account:'/account.html',support:'/support.html',register:'/register.html',features:'/features.html',privacy:'/privacy.html'};
  function addMessage(text, role = 'assistant', action = 'none') {
    const node = document.createElement('div'); node.className=`assistant-message ${role}`; node.textContent=text;
    if (safeActions[action]) {
      const link=document.createElement('a'); link.className='assistant-action'; link.href=path(safeActions[action]);
      link.textContent = action === 'register' ? t('signup') : `${t('apply')} · ${window.LectureSiftI18n?.t?.('nav.'+action, action) || action}`; node.append(link);
    } else if (['light','dark'].includes(action)) {
      const button=document.createElement('button'); button.type='button'; button.className='assistant-action'; button.textContent=t('apply');
      button.addEventListener('click',()=>{ if (document.documentElement.dataset.theme !== action) document.querySelector('.theme-toggle')?.click(); button.disabled=true; }); node.append(button);
    }
    $('.assistant-messages').append(node); node.scrollIntoView({block:'nearest'});
    return node;
  }
  function reset() { history=[]; pending=null; attachment=null; $('.assistant-attachment').hidden=true; $('.assistant-messages').replaceChildren(); $('textarea').value=''; setStatus(''); }
  function setBusy(value) { busy=value; dialog.querySelectorAll('form button').forEach(button=>{button.disabled=value;}); $('textarea').disabled=value; }
  launch.addEventListener('click',async()=>{
    if (sessionToken !== token()) { reset(); sessionToken=token(); }
    dialog.showModal(); $('textarea').focus();
    if (!$('.assistant-messages').children.length) addMessage(t('welcome'), 'assistant', token() ? 'workspace' : 'register');
    try {
      const offers=await request('/assistant/catalog', null, false); available=offers.available===true;
      if (!available) { setStatus(t('unavailable')); return; }
      if (token()) { const wallet=await request('/assistant/wallet'); setStatus(`${wallet.balance} · ${t('credits')}`); }
      else setStatus(t('trial'));
    } catch { available=false; setStatus(t('unavailable')); }
  });
  $('.assistant-close').addEventListener('click',()=>dialog.close());
  dialog.addEventListener('close',()=>launch.focus());
  $('.assistant-clear').addEventListener('click',()=>{reset();$('textarea').focus();});
  $('.assistant-attachment button').addEventListener('click',()=>{attachment=null;$('.assistant-attachment').hidden=true;});
  $('.assistant-attach').addEventListener('click',()=>{
    if (!token()) { addMessage(t('signup'),'assistant','register'); return; }
    if (!available) { setStatus(t('unavailable')); return; }
    $('input[type=file]').click();
  });
  const waitEvent = (element, event) => new Promise((resolve,reject)=>{
    const timer=setTimeout(()=>finish(new Error('media timeout')),15000);
    const ok=()=>finish(); const bad=()=>finish(new Error('media decode'));
    function finish(error) {clearTimeout(timer);element.removeEventListener(event,ok);element.removeEventListener('error',bad);error?reject(error):resolve();}
    element.addEventListener(event,ok,{once:true});element.addEventListener('error',bad,{once:true});
  });
  function frame(element, width, height) {
    const canvas=document.createElement('canvas');const scale=Math.min(1,512/Math.max(width,height));canvas.width=Math.max(1,Math.round(width*scale));canvas.height=Math.max(1,Math.round(height*scale));
    canvas.getContext('2d').drawImage(element,0,0,canvas.width,canvas.height);return canvas.toDataURL('image/jpeg',.8);
  }
  async function prepare(file) {
    const video=file.type.startsWith('video/');
    if (file.size > (video ? 50 : 10)*1024*1024) throw new Error('media size');
    const url=URL.createObjectURL(file); let element;
    try {
      if (!video) {element=new Image();const ready=waitEvent(element,'load');element.src=url;await ready;return {images:[frame(element,element.naturalWidth,element.naturalHeight)],media_kind:'image'};}
      element=document.createElement('video');element.muted=true;element.preload='auto';element.playsInline=true;
      const ready=waitEvent(element,'loadeddata');element.src=url;await ready;
      if (!Number.isFinite(element.duration) || element.duration<=0 || element.duration>60) throw new Error('media duration');
      const images=[];
      for (const fraction of [.1,.5,.9]) {const seek=waitEvent(element,'seeked');element.currentTime=element.duration*fraction;await seek;images.push(frame(element,element.videoWidth,element.videoHeight));}
      return {images,media_kind:'video_frames'};
    } finally { if(video&&element){element.pause();element.removeAttribute('src');element.load();} URL.revokeObjectURL(url); }
  }
  $('input[type=file]').addEventListener('change',async event=>{
    const file=event.target.files[0];event.target.value='';if(!file)return;
    setBusy(true);setStatus(t('waiting'));
    try {attachment=await prepare(file);$('.assistant-attachment span').textContent=file.name;$('.assistant-attachment').hidden=false;setStatus(t('media'));}
    catch {attachment=null;$('.assistant-attachment').hidden=true;setStatus(t('media'));}
    finally {setBusy(false);}
  });
  $('form').addEventListener('submit',async event=>{
    event.preventDefault();if(busy)return;
    if(sessionToken!==token()){reset();sessionToken=token();setStatus(t('signup'));return;}
    const message=$('textarea').value.trim();if(!message)return;
    if(!available){addMessage(t('unavailable'),'assistant',token()?'workspace':'register');return;}
    if(!token()&&trialCount>=3){addMessage(t('signup'),'assistant','register');return;}
    setBusy(true);setStatus(t('waiting'));addMessage(message,'user');
    try {
      let answer;
      if(!token()) {answer=await request('/assistant/trial',{message:message.slice(0,500),language:language()},false);trialCount++;}
      else {
        const lessonId=new URLSearchParams(location.search).get('job') || '';
        const payload={message,language:language(),currency:localStorage.getItem('lecturesift-currency')||'USD',history:history.slice(-6),lesson_id:lessonId,...(attachment||{images:[],media_kind:'none'})};
        const signature=JSON.stringify(payload);
        if(!pending || pending.signature!==signature) pending={signature,payload:{request_id:crypto.randomUUID(),...payload}};
        answer=await request('/assistant/chat',pending.payload);
      }
      if(sessionToken!==token()){reset();sessionToken=token();return;}
      addMessage(answer.answer,'assistant',answer.action);
      pending=null;
      history.push({role:'user',content:message.slice(0,2000)},{role:'assistant',content:answer.answer.slice(0,2000)});
      history=history.slice(-6);$('textarea').value='';attachment=null;$('.assistant-attachment').hidden=true;
      setStatus(token()?`${answer.balance ?? ''} · ${t('credits')} (−${answer.charged_credits || 0})`:t('signup'));
    } catch(error) {
      if(error.status && error.status!==409) pending=null;
      const text=!error.status||error.status===409?t('uncertain'):error.status===402?t('empty'):error.status===401?t('signup'):error.message==='LS-ASSIST-01'?t('unavailable'):t('error');
      addMessage(text,'assistant',error.status===402?'plans':error.status===401||!token()?'register':'none');setStatus('');
    } finally {setBusy(false);$('textarea').focus();}
  });
})();
