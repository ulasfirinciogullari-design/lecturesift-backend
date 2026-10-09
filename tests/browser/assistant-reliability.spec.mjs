import {test, expect} from './fixtures.mjs';

const cors={
  'Access-Control-Allow-Origin':'http://127.0.0.1:4173',
  'Access-Control-Allow-Methods':'GET,POST,OPTIONS',
  'Access-Control-Allow-Headers':'authorization,content-type',
};
const offers={available:true,image:{available:true,credits:200},packs:[]};
const reply=(answer='Current session reply',balance=998)=>({answer,action:'none',balance,charged_credits:2});
const flushUI=page=>page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));

async function setup(page, wallet) {
  await page.addInitScript(()=>localStorage.setItem('lecturesift-billing-token','synthetic-owner-a'));
  await page.route('https://api.lecturesift.com/assistant/catalog*',route=>route.fulfill({status:200,headers:cors,json:offers}));
  await page.route('https://api.lecturesift.com/assistant/wallet',async route=>{
    if(route.request().method()==='OPTIONS'){await route.fulfill({status:204,headers:cors});return;}
    if(wallet){await wallet(route);return;}
    const balance=route.request().headers().authorization==='Bearer synthetic-owner-b'?500:1000;
    await route.fulfill({status:200,headers:cors,json:{balance}});
  });
  await page.goto('/en/assistant.html');
  await page.locator('[data-consent="essential"]').click();
  await expect(page.locator('.assistant-balance')).toHaveText('Credits left: 1,000');
}

async function changeOwner(page) {
  await page.evaluate(()=>{
    localStorage.setItem('lecturesift-billing-token','synthetic-owner-b');
    document.dispatchEvent(new Event('lecturesift:assistant-open'));
  });
  await expect(page.locator('.assistant-balance')).toHaveText('Credits left: 500');
}

for(const outcome of ['success','failure']) {
  test(`a delayed old-session ${outcome} cannot expose data or unlock a new request`,async({page})=>{
    const pending=[];
    await page.route('https://api.lecturesift.com/assistant/chat',async route=>{
      if(route.request().method()==='OPTIONS'){await route.fulfill({status:204,headers:cors});return;}
      const response=await new Promise(resolve=>pending.push({resolve,payload:route.request().postDataJSON(),owner:route.request().headers().authorization}));
      await route.fulfill({headers:cors,...response});
    });
    await setup(page);
    const input=page.locator('.assistant-compose textarea'),send=page.locator('.assistant-compose button[type=submit]');
    await input.fill('Private question from owner A');await send.click();
    await expect.poll(()=>pending.length).toBe(1);
    await changeOwner(page);
    await input.fill('Question from owner B');await send.click();
    await expect.poll(()=>pending.length).toBe(2);
    const oldResponse=page.waitForResponse(response=>response.url().endsWith('/assistant/chat')&&response.request().headers().authorization==='Bearer synthetic-owner-a');
    pending[0].resolve(outcome==='success'
      ? {status:200,json:reply('Private answer belonging to owner A',12)}
      : {status:402,json:{detail:{code:'LS-ASSIST-03'}}});
    await oldResponse;await flushUI(page);
    await expect(input).toBeDisabled();
    await expect(send).toBeDisabled();
    await expect(page.locator('.assistant-balance')).toHaveText('Credits left: 500');
    await expect(page.locator('.assistant-messages')).not.toContainText('owner A');
    await expect(page.locator('.assistant-message-charge')).toHaveCount(0);
    pending[1].resolve({status:200,json:reply('Answer belonging to owner B',498)});
    await expect(input).toBeEnabled();
    await expect(page.locator('.assistant-balance')).toHaveText('Credits left: 498');
    await expect(page.locator('.assistant-messages')).toContainText('Answer belonging to owner B');
    expect(pending[1].payload.history).toEqual([]);
    expect(pending[1].owner).toBe('Bearer synthetic-owner-b');
  });
}

test('a delayed wallet snapshot cannot restore credits consumed by a newer reply',async({page})=>{
  let walletCalls=0,releaseWallet;
  await page.route('https://api.lecturesift.com/assistant/chat',async route=>{
    if(route.request().method()==='OPTIONS'){await route.fulfill({status:204,headers:cors});return;}
    await route.fulfill({status:200,headers:cors,json:reply()});
  });
  await setup(page,async route=>{
    walletCalls++;
    if(walletCalls===2)await new Promise(resolve=>{releaseWallet=resolve;});
    await route.fulfill({status:200,headers:cors,json:{balance:1000}});
  });
  await page.evaluate(()=>document.dispatchEvent(new Event('lecturesift:assistant-open')));
  await expect.poll(()=>Boolean(releaseWallet)).toBe(true);
  await page.locator('.assistant-compose textarea').fill('Explain this lesson');
  await page.locator('.assistant-compose button[type=submit]').click();
  await expect(page.locator('.assistant-balance')).toHaveText('Credits left: 998');
  const snapshot=page.waitForResponse(response=>response.url().endsWith('/assistant/wallet'));
  releaseWallet();await snapshot;await flushUI(page);
  await expect(page.locator('.assistant-balance')).toHaveText('Credits left: 998');
});

for(const outcome of ['gateway','malformed','confirmed-no-charge']) {
  test(`manual retry after ${outcome} retains the correct billing identity`,async({page})=>{
    const requests=[];
    await page.route('https://api.lecturesift.com/assistant/chat',async route=>{
      if(route.request().method()==='OPTIONS'){await route.fulfill({status:204,headers:cors});return;}
      requests.push(route.request().postDataJSON());
      const first=outcome==='gateway'?{status:502,json:{message:'Upstream unavailable'}}
        :outcome==='malformed'?{status:200,json:{answer:null,action:'none',charged_credits:2,balance:998}}
          :{status:503,json:{detail:{code:'LS-ASSIST-07'}}};
      await route.fulfill({headers:cors,...(requests.length===1?first:{status:200,json:reply('Recovered reply')})});
    });
    await setup(page);
    const input=page.locator('.assistant-compose textarea'),send=page.locator('.assistant-compose button[type=submit]');
    await input.fill('Explain this once');await send.click();
    await expect.poll(()=>requests.length).toBe(1);
    await expect(input).toBeEnabled();
    await expect(input).toHaveValue('Explain this once');
    await expect(page.locator('.assistant-message-charge')).toHaveCount(0);
    await send.click();
    await expect(page.locator('.assistant-messages')).toContainText('Recovered reply');
    expect(requests).toHaveLength(2);
    if(outcome==='confirmed-no-charge')expect(requests[1].request_id).not.toBe(requests[0].request_id);
    else expect(requests[1]).toEqual(requests[0]);
    await expect(page.locator('.assistant-message-charge')).toHaveCount(1);
    await expect(page.locator('.assistant-balance')).toHaveText('Credits left: 998');
  });
}

test('an incomplete image response retains the paid generation identity for manual recovery',async({page})=>{
  const requests=[];
  let syntheticImage;
  await page.route('https://api.lecturesift.com/assistant/image',async route=>{
    if(route.request().method()==='OPTIONS'){await route.fulfill({status:204,headers:cors});return;}
    requests.push(route.request().postDataJSON());
    await route.fulfill({status:200,headers:cors,json:{
      kind:'image',image:requests.length===1?null:syntheticImage,
      balance:800,charged_credits:200,action:'none',
    }});
  });
  await setup(page);
  syntheticImage=await page.evaluate(()=>{const canvas=document.createElement('canvas');canvas.width=canvas.height=32;return canvas.toDataURL('image/jpeg');});
  await page.locator('[data-mode=image]').click();
  const input=page.locator('.assistant-compose textarea'),send=page.locator('.assistant-compose button[type=submit]');
  await input.fill('An educational diagram');await send.click();
  await expect.poll(()=>requests.length).toBe(1);
  await expect(input).toBeEnabled();
  await expect(page.locator('.assistant-message-charge')).toHaveCount(0);
  await send.click();
  await expect(page.locator('.assistant-message img')).toBeVisible();
  await expect(page.locator('.assistant-balance')).toHaveText('Credits left: 800');
  expect(requests).toHaveLength(2);
  expect(requests[1]).toEqual(requests[0]);
  await expect(page.locator('.assistant-message-charge')).toHaveCount(1);
});

test('valid Unicode replies are delivered and history never cuts a code point in half',async({page})=>{
  const answer='A'+'🧠'.repeat(2500),requests=[];
  await page.route('https://api.lecturesift.com/assistant/chat',async route=>{
    if(route.request().method()==='OPTIONS'){await route.fulfill({status:204,headers:cors});return;}
    requests.push(route.request().postDataJSON());
    await route.fulfill({status:200,headers:cors,json:reply(requests.length===1?answer:'Follow-up reply')});
  });
  await setup(page);
  const input=page.locator('.assistant-compose textarea'),send=page.locator('.assistant-compose button[type=submit]');
  await input.fill('Explain with symbols');await send.click();
  await expect(input).toBeEnabled();
  await expect(input).toHaveValue('');
  await expect(page.locator('.assistant-messages')).toContainText(answer);
  await input.fill('Continue');await send.click();
  await expect(page.locator('.assistant-messages')).toContainText('Follow-up reply');
  expect(requests).toHaveLength(2);
  expect(requests[1].history.at(-1).content).toBe('A'+'🧠'.repeat(1999));
});

test('a prepared attachment is discarded if its account changes before decoding completes',async({page})=>{
  // Control only the asynchronous decode boundary. The canvas is synthetic;
  // this test sends no customer media and makes no real assistant request.
  await page.addInitScript(()=>{
    window.Image=function(){
      const canvas=document.createElement('canvas');canvas.width=canvas.height=1;
      canvas.naturalWidth=canvas.naturalHeight=1;
      Object.defineProperty(canvas,'src',{set(){window.releasePreparedImage=()=>canvas.dispatchEvent(new Event('load'));}});
      return canvas;
    };
  });
  const requests=[];
  await page.route('https://api.lecturesift.com/assistant/chat',async route=>{
    if(route.request().method()==='OPTIONS'){await route.fulfill({status:204,headers:cors});return;}
    requests.push(route.request().postDataJSON());
    await route.fulfill({status:200,headers:cors,json:reply('Owner B reply',498)});
  });
  await setup(page);
  await page.locator('.assistant-compose input[type=file]').setInputFiles({name:'private-owner-a.png',mimeType:'image/png',buffer:Buffer.from('synthetic decode fixture')});
  await expect.poll(()=>page.evaluate(()=>typeof window.releasePreparedImage)).toBe('function');
  await changeOwner(page);
  await page.evaluate(()=>window.releasePreparedImage());await flushUI(page);
  await expect(page.locator('.assistant-attachment')).toBeHidden();
  await page.locator('.assistant-compose textarea').fill('Only my question');
  await page.locator('.assistant-compose button[type=submit]').click();
  await expect(page.locator('.assistant-messages')).toContainText('Owner B reply');
  expect(requests[0].images).toEqual([]);
  expect(requests[0].media_kind).toBe('none');
});

test('a storage account change immediately removes the old conversation',async({page})=>{
  await page.route('https://api.lecturesift.com/assistant/chat',async route=>{
    if(route.request().method()==='OPTIONS'){await route.fulfill({status:204,headers:cors});return;}
    await route.fulfill({status:200,headers:cors,json:reply('Private saved answer')});
  });
  await setup(page);
  await page.locator('.assistant-compose textarea').fill('My question');
  await page.locator('.assistant-compose button[type=submit]').click();
  await expect(page.locator('.assistant-messages')).toContainText('Private saved answer');
  await page.evaluate(()=>{
    localStorage.removeItem('lecturesift-billing-token');
    window.dispatchEvent(new StorageEvent('storage',{key:'lecturesift-billing-token'}));
  });
  await expect(page.locator('.assistant-message')).toHaveCount(0);
  await expect(page.locator('.assistant-balance')).not.toContainText('998');
});
