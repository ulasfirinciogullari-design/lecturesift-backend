(function () {
  if (document.querySelector('.assistant-surface') || /\/admin(?:\.html)?$/.test(location.pathname)) return;
  const pageRoot = document.querySelector('[data-assistant-root]');
  const API = 'https://api.lecturesift.com';
  const t = key => window.LectureSiftAssistantCopy.t(key);
  const language = () => window.LectureSiftI18n?.language || document.documentElement.lang || 'tr';
  const token = () => localStorage.getItem('lecturesift-billing-token') || '';
  const path = value => window.LectureSiftI18n?.localizedPath?.(language(), value) || value;
  const format = (key, count) => t(key).replace('{count}', Number(count).toLocaleString(language()));
  const css = document.createElement('link'); css.rel = 'stylesheet'; css.href = '/assistant.css?v=4'; document.head.append(css);
  const spark = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="m12 3 2.7 6.3L21 12l-6.3 2.7L12 21l-2.7-6.3L3 12l6.3-2.7Z"/></svg>';
  const launch = document.createElement('button'); launch.className = 'assistant-launch'; launch.innerHTML = spark; const launchLabel=document.createElement('span');launchLabel.textContent=t('nav');launch.append(launchLabel);launch.setAttribute('aria-label',t('title')); launch.type = 'button'; launch.setAttribute('aria-haspopup', 'dialog'); if(!pageRoot)document.body.append(launch);
  const positionLaunch = () => {
    const banner=document.querySelector('.consent-banner:not([hidden])');
    launch.style.bottom=banner ? `${Math.max(22,window.innerHeight-banner.getBoundingClientRect().top+12)}px` : '';
  };
  let observedBanner=null;
  const consentResize=new ResizeObserver(positionLaunch);
  const observeConsent = () => {
    const banner=document.querySelector('.consent-banner');
    if(banner && banner!==observedBanner){consentResize.disconnect();consentResize.observe(banner);observedBanner=banner;}
    requestAnimationFrame(positionLaunch);
  };
  document.addEventListener('lecturesift:consent-ready',observeConsent);
  document.addEventListener('lecturesift:consent',observeConsent);
  window.addEventListener('resize',positionLaunch);observeConsent();
  const dialog = document.createElement(pageRoot ? 'section' : 'dialog'); dialog.className = `assistant-surface ${pageRoot ? 'assistant-page-chat' : 'assistant-dialog'}`; dialog.setAttribute('aria-labelledby', 'assistantTitle');
  dialog.innerHTML = `<div class="assistant-layout">
    <header class="assistant-header"><span class="assistant-emblem">${spark}</span><div class="assistant-heading"><h2 id="assistantTitle"></h2></div><button type="button" class="assistant-clear"></button><a class="assistant-expand" href="#"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M14 4h6v6M20 4l-8 8M10 4H4v16h16v-6"/></svg></a><button type="button" class="assistant-close">×</button></header>
    <div class="assistant-credit-bar"><span class="assistant-balance" aria-live="polite"></span><a></a></div>
    <div class="assistant-messages" role="log" aria-live="polite" tabindex="0"></div><div class="assistant-suggestions"></div><p class="assistant-status" role="status"></p>
    <form class="assistant-compose"><div class="assistant-mode" role="group" hidden><button type="button" data-mode="chat" aria-pressed="true"></button><button type="button" data-mode="image" aria-pressed="false"></button></div>
    <div class="assistant-attachment" hidden><span></span><button type="button">×</button></div><textarea maxlength="3000" required rows="2"></textarea>
    <div class="assistant-toolbar"><button type="button" class="assistant-attach">＋ <span></span></button><button type="submit"></button></div><input type="file" accept="image/jpeg,image/png,image/webp,video/mp4,video/webm,video/quicktime" hidden><p class="assistant-limit"></p></form></div>`;
  (pageRoot || document.body).append(dialog);
  const $ = selector => dialog.querySelector(selector);
  $('#assistantTitle').textContent = t(pageRoot?'chatmode':'title'); $('.assistant-close').setAttribute('aria-label',t('close'));
  $('.assistant-limit').textContent=t(token()?'usage':'trialnote');
  $('.assistant-balance').textContent=t('limited');
  $('.assistant-credit-bar a').textContent=t(token()?'buy':'signup');
  $('.assistant-credit-bar a').href=token()&&pageRoot?'#assistantCreditShop':path(token()?'/plans.html#assistantCredits':'/register.html');
  $('.assistant-expand').href=path('/assistant.html');$('.assistant-expand').setAttribute('aria-label',t('openpage'));$('.assistant-expand').title=t('openpage');
  $('.assistant-expand').hidden=Boolean(pageRoot);$('.assistant-close').hidden=Boolean(pageRoot);
  $('textarea').placeholder = t('ask'); $('textarea').setAttribute('aria-label',t('ask'));
  $('.assistant-clear').textContent = t('clear'); $('button[type=submit]').textContent=t('send');
  $('.assistant-attach').setAttribute('aria-label',t('attach'));$('.assistant-attach').title=t('attach'); $('.assistant-attach span').textContent=t('attachshort');
  $('.assistant-attachment button').setAttribute('aria-label',t('close'));
  $('.assistant-mode').setAttribute('aria-label',t('mode'));
  $('.assistant-mode [data-mode=chat]').textContent=t('chatmode');
  let imageCredits=0, mode='chat';
  let history = [], attachment = null, sessionToken = token(), busy = false, available = false, trialCount = 0, pending = null;
  const setStatus = text => { $('.assistant-status').textContent = text; };
  const updateBalance = value => {if(Number.isFinite(value)&&value>=0)$('.assistant-balance').textContent=format('balance',value);};
  for(const key of ['suggeststudy','suggestaccount','suggestinvite']) {
    const button=document.createElement('button');button.type='button';button.textContent=t(key);
    button.addEventListener('click',()=>{if(busy)return;$('textarea').value=t(key);$('textarea').focus();});
    $('.assistant-suggestions').append(button);
  }
  async function request(endpoint, data, auth = true) {
    const headers = {'Content-Type':'application/json'};
    if (auth && token()) headers.Authorization = `Bearer ${token()}`;
    const response = await fetch(API + endpoint, {method:data ? 'POST':'GET', headers, body:data ? JSON.stringify(data):undefined, signal:AbortSignal.timeout(endpoint==='/assistant/image'?110000:65000)});
    const body = await response.json();
    if (!response.ok) { const error = new Error(body.detail?.code || 'LS-ASSIST-07'); error.status=response.status; throw error; }
    return body;
  }
  const safeActions = {workspace:'/workspace.html',plans:'/plans.html',account:'/account.html',support:'/support.html',register:'/register.html',features:'/features.html',privacy:'/privacy.html'};
  function addMessage(text, role = 'assistant', action = 'none') {
    const node = document.createElement('div'); node.className=`assistant-message ${role}`; node.textContent=text;
    if (safeActions[action]) {
      const link=document.createElement('a'); link.className='assistant-action'; link.href=path(safeActions[action]);
      link.textContent = action === 'register' ? t('signup') : t('action'+action); node.append(link);
    } else if (['light','dark'].includes(action)) {
      const button=document.createElement('button'); button.type='button'; button.className='assistant-action'; button.textContent=t('apply');
      button.addEventListener('click',()=>{ if (document.documentElement.dataset.theme !== action) document.querySelector('.theme-toggle')?.click(); button.disabled=true; }); node.append(button);
    }
    const messages=$('.assistant-messages');messages.append(node);messages.scrollTop=messages.scrollHeight;
    return node;
  }
  function reset() { mode='chat';updateMode();history=[]; pending=null; attachment=null; $('.assistant-attachment').hidden=true; $('.assistant-messages').replaceChildren();$('.assistant-suggestions').hidden=false; $('textarea').value=''; setStatus(''); }
  function syncSession() {
    reset();sessionToken=token();available=false;
    $('.assistant-balance').textContent=t('limited');
    $('.assistant-limit').textContent=t(token()?'usage':'trialnote');
    $('.assistant-credit-bar a').textContent=t(token()?'buy':'signup');
    $('.assistant-credit-bar a').href=token()&&pageRoot?'#assistantCreditShop':path(token()?'/plans.html#assistantCredits':'/register.html');
    $('.assistant-mode').hidden=true;
  }
  function setBusy(value) { busy=value; dialog.querySelectorAll('form button,.assistant-clear').forEach(button=>{button.disabled=value;}); $('textarea').disabled=value; }
  function updateMode() {
    const creating=mode==='image';
    dialog.querySelectorAll('[data-mode]').forEach(button=>button.setAttribute('aria-pressed',String(button.dataset.mode===mode)));
    $('.assistant-attach').hidden=creating;
    $('textarea').placeholder=t(creating?'imageprompt':'ask');
    $('textarea').setAttribute('aria-label',t(creating?'imageprompt':'ask'));
    $('textarea').maxLength=creating?1000:3000;
    if(creating){attachment=null;$('.assistant-attachment').hidden=true;}
  }
  dialog.querySelectorAll('[data-mode]').forEach(button=>button.addEventListener('click',()=>{if(busy)return;mode=button.dataset.mode;pending=null;updateMode();$('textarea').focus();}));
  function selectedCurrency() {
    const locales=window.LECTURESIFT_LOCALE_DATA;
    const saved=localStorage.getItem('lecturesift-currency');
    if(locales?.currencies.includes(saved))return saved;
    const region=(localStorage.getItem('lecturesift-country')||navigator.language.split('-')[1]||'').toUpperCase();
    return locales?.currencyForCountry[region]||'USD';
  }
  function renderCreditShop(offers) {
    document.querySelectorAll('[data-assistant-credit-shop]').forEach(shop=>{
      shop.replaceChildren();
      const heading=document.createElement('h2');heading.textContent=t('buy');shop.append(heading);
      if(offers.available!==true){const note=document.createElement('p');note.textContent=t('unavailable');shop.append(note);return;}
      const list=document.createElement('div');list.className='assistant-shop-packs';
      const allowed={ai_1000:1000,ai_3000:3000,ai_10000:10000};
      for(const pack of offers.packs||[]) {
        if(allowed[pack.code]!==pack.credits||!Number.isInteger(pack.amount_minor)||pack.amount_minor<=0||!window.LECTURESIFT_LOCALE_DATA?.currencies.includes(pack.currency))continue;
        const link=document.createElement('a');link.className='assistant-shop-pack';
        link.href=path('/plans.html')+'?plan='+encodeURIComponent(pack.code)+'&interval=one_time#assistantCredits';
        const count=document.createElement('strong');count.textContent=format('packcount',pack.credits);
        const price=document.createElement('span');const zero=['JPY','KRW'].includes(pack.currency);
        price.textContent=new Intl.NumberFormat(language(),{style:'currency',currency:pack.currency,maximumFractionDigits:zero?0:2}).formatToParts(pack.amount_minor/(zero?1:100)).map(part=>part.type==='currency'?(window.LECTURESIFT_LOCALE_DATA.currencySymbols[pack.currency]||part.value):part.value).join('');
        const arrow=document.createElement('span');arrow.textContent='↗';arrow.setAttribute('aria-hidden','true');link.append(count,price,arrow);list.append(link);
      }
      shop.append(list);
      const note=document.createElement('p');note.className='assistant-shop-note';note.textContent=t('topupshort');shop.append(note);
    });
  }
  async function openAssistant() {
    if (sessionToken !== token()) syncSession();
    const openingSession=sessionToken;
    if(!pageRoot){dialog.showModal();$('textarea').focus();}
    if (!$('.assistant-messages').children.length) addMessage(t(token()?'welcomeowned':'welcome'), 'assistant', token() ? 'workspace' : 'register');
    try {
      const offers=await request('/assistant/catalog'+(pageRoot?'?currency='+encodeURIComponent(selectedCurrency()):''), null, false);
      if(openingSession!==token()){syncSession();return;}
      available=offers.available===true;
      renderCreditShop(offers);
      imageCredits=offers.image?.credits||0;
      $('.assistant-mode').hidden=!(available&&offers.image?.available===true&&token());
      $('.assistant-mode [data-mode=image]').textContent=`${t('imagemode')} · ${imageCredits} ${t('credits')}`;
      if($('.assistant-mode').hidden){mode='chat';updateMode();}
      if (!available) { setStatus(t('unavailable')); return; }
      if (token()) { const wallet=await request('/assistant/wallet');if(openingSession!==token()){syncSession();return;}updateBalance(wallet.balance);setStatus(''); }
      else setStatus(t('trial'));
    } catch { available=false; setStatus(t('unavailable')); }
  }
  launch.addEventListener('click',openAssistant);
  if(pageRoot) {
    document.addEventListener('lecturesift:assistant-open',openAssistant);
    if(!pageRoot.hasAttribute('data-assistant-lazy') || !pageRoot.closest('[hidden]'))openAssistant();
  }
  else {$('.assistant-close').addEventListener('click',()=>dialog.close());dialog.addEventListener('close',()=>launch.focus());}
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
    try {attachment=await prepare(file);$('.assistant-attachment span').textContent=file.name;$('.assistant-attachment').hidden=false;setStatus(attachment.media_kind==='video_frames'?t('media'):'');}
    catch {attachment=null;$('.assistant-attachment').hidden=true;setStatus(t('media'));}
    finally {setBusy(false);}
  });
  $('form').addEventListener('submit',async event=>{
    event.preventDefault();if(busy)return;
    if(sessionToken!==token()){syncSession();setStatus(t('signup'));return;}
    const message=$('textarea').value.trim();if(!message)return;
    if(!available){addMessage(t('unavailable'),'assistant',token()?'workspace':'register');return;}
    if(!token()&&trialCount>=3){addMessage(t('signup'),'assistant','register');return;}
    const creating=token()&&mode==='image';
    if(creating&&new TextEncoder().encode(message).length>1000){setStatus(t('shortprompt'));return;}
    setBusy(true);$('.assistant-suggestions').hidden=true;setStatus(t('waiting'));addMessage(message,'user');
    try {
      let answer;
      if(!token()) {answer=await request('/assistant/trial',{message:message.slice(0,500),language:language()},false);trialCount++;}
      else if(creating) {
        const signature='image:'+message;
        if(!pending||pending.signature!==signature)pending={signature,payload:{request_id:crypto.randomUUID(),prompt:message}};
        answer=await request('/assistant/image',pending.payload);
      }
      else {
        const lessonId=new URLSearchParams(location.search).get('job') || '';
        const payload={message,language:language(),currency:selectedCurrency(),history:history.slice(-6),lesson_id:lessonId,...(attachment||{images:[],media_kind:'none'})};
        const signature=JSON.stringify(payload);
        if(!pending || pending.signature!==signature) pending={signature,payload:{request_id:crypto.randomUUID(),...payload}};
        answer=await request('/assistant/chat',pending.payload);
      }
      if(sessionToken!==token()){syncSession();return;}
      if(answer.kind==='image'&&/^data:image\/jpeg;base64,[A-Za-z0-9+/=]+$/.test(answer.image||'')&&answer.image.length<=1500100){
        const node=addMessage(t('generated'));
        const picture=document.createElement('img');picture.src=answer.image;picture.alt=message;picture.width=1024;picture.height=1024;node.append(picture);
        const download=document.createElement('a');download.className='assistant-action';download.href=answer.image;download.download='lecturesift-image.jpg';download.textContent=t('downloadimage');node.append(download);
        const messages=$('.assistant-messages');messages.scrollTop=messages.scrollHeight;
        answer.answer=t('generated');
      }else addMessage(answer.answer,'assistant',answer.action);
      pending=null;
      history.push({role:'user',content:message.slice(0,2000)},{role:'assistant',content:answer.answer.slice(0,2000)});
      history=history.slice(-6);$('textarea').value='';attachment=null;$('.assistant-attachment').hidden=true;
      updateBalance(answer.balance);
      setStatus(token()?'':t('signup'));
    } catch(error) {
      if(error.status && error.status!==409) pending=null;
      const text=!error.status||error.status===409?t('uncertain'):error.status===402?t('empty'):error.status===401?t('signup'):error.message==='LS-ASSIST-01'?t('unavailable'):t('error');
      addMessage(text,'assistant',error.status===402?'plans':error.status===401||!token()?'register':'none');setStatus('');
    } finally {setBusy(false);$('textarea').focus();}
  });
})();
