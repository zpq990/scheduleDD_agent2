import {Browser} from 'file:///C:/Users/zpq99/.agents/skills/design-taste-frontend/scripts/lib/cdp.mjs';
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
const b=await Browser.launch({width:1440,height:1000});const p=await b.newPage({width:1440,height:1000});
const wait=async(e,ms=20000)=>{const t=Date.now();while(Date.now()-t<ms){if(await p.eval(e).catch(()=>false))return;await sleep(150)}throw new Error('timeout '+e)};
const click=async s=>{await p.eval(s=>document.querySelector(s)?.click(),s);await sleep(300)};
await p.goto('http://127.0.0.1:9870/');await wait("document.querySelector('#authScreen')");
if(await p.eval(()=>!document.querySelector('#authScreen').classList.contains('hidden'))){
 await click('#authTabRegister');
 await p.eval(()=>{document.querySelector('#authUsername').value='prev_'+Date.now();document.querySelector('#authPassword').value='previewpass1';document.querySelector('#authPassword2').value='previewpass1';});
 await click('#authSubmit');await wait("!document.querySelector('#appLayout').classList.contains('hidden')");
}
await p.eval(()=>window.confirm=()=>true);await click('#demoBtn');await sleep(800);
await click('[data-view=schedule]');await click('#buildScheduleBtn');
await wait("document.querySelector('#modalTitle').textContent.includes('生成行程预览')");
console.log('rows',await p.eval(()=>document.querySelectorAll('#modalBody [data-preview]').length));
console.log('statuses',await p.eval(()=>[...document.querySelectorAll('#modalBody [data-preview]')].map(r=>({p:r.querySelector('td:nth-child(2)').textContent,s:r.querySelector('td:nth-child(7)').textContent.trim().slice(0,20),date:r.querySelector('[data-date]').value,slot:r.querySelector('[data-slot]').value,text:r.querySelector('[data-text]').value}))));
await p.screenshot({path:'test-results/extract-preview.jpg',quality:86,fullPage:true});
await p.eval(()=>[...document.querySelectorAll('#modalActions button')].find(b=>b.textContent.includes('写入选中')).click());
await sleep(1200);
console.log('modal hidden',await p.eval(()=>document.querySelector('#modal').classList.contains('hidden')));
console.log('cells',await p.eval(()=>document.querySelectorAll('#scheduleGrid td[data-date]').length));
console.log('errors',p.consoleErrors);
await b.close();
