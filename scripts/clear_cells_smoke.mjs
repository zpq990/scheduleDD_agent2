import {Browser} from 'file:///C:/Users/zpq99/.agents/skills/design-taste-frontend/scripts/lib/cdp.mjs';
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
const b=await Browser.launch({width:1440,height:1000});const p=await b.newPage({width:1440,height:1000});
const wait=async(e,ms=15000)=>{const t=Date.now();while(Date.now()-t<ms){if(await p.eval(e).catch(()=>false))return;await sleep(150)}throw new Error('timeout '+e)};
const click=async s=>{await p.eval(s=>document.querySelector(s)?.click(),s);await sleep(250)};
await p.goto('http://127.0.0.1:9870/');await wait("document.querySelector('#authScreen')");
if(await p.eval(()=>!document.querySelector('#authScreen').classList.contains('hidden'))){
 await click('#authTabRegister');
 await p.eval(()=>{document.querySelector('#authUsername').value='clear_'+Date.now();document.querySelector('#authPassword').value='clearpass1';document.querySelector('#authPassword2').value='clearpass1';});
 await click('#authSubmit');await wait("!document.querySelector('#appLayout').classList.contains('hidden')");
}
await p.eval(()=>window.confirm=()=>true);await click('#demoBtn');await sleep(800);await click('[data-view=schedule]');await sleep(400);
await click('#multiSelectBtn');
const cells=await p.eval(()=>[...document.querySelectorAll('#scheduleGrid td[data-date]')].filter(td=>td.querySelector('.cell').textContent.trim()).map(td=>({d:td.dataset.date,s:td.dataset.slot})));
console.log('nonempty',cells);
await p.eval(c=>{const td=document.querySelector(`#scheduleGrid td[data-date="${c.d}"][data-slot="${c.s}"]`);td.click();},cells[0]);
await p.eval(c=>{const td=document.querySelector(`#scheduleGrid td[data-date="${c.d}"][data-slot="${c.s}"]`);td.click();},cells[1]||cells[0]);
console.log('selected',await p.eval(()=>document.querySelectorAll('#scheduleGrid td.selected').length));
await click('#clearCellsBtn');await wait("document.querySelector('#modalTitle').textContent.includes('清除')");await sleep(300);
console.log('modal',await p.eval(()=>document.querySelector('#modalTitle').textContent));
await p.screenshot({path:'test-results/clear-confirm.jpg',quality:86,fullPage:true});
await p.eval(()=>[...document.querySelectorAll('#modalActions button')].find(b=>b.textContent.includes('确认清除')).click());
await sleep(1200);
console.log('remaining-nonempty',await p.eval(()=>[...document.querySelectorAll('#scheduleGrid td[data-date]')].filter(td=>td.querySelector('.cell').textContent.trim()).length));
console.log('selected-after',await p.eval(()=>document.querySelectorAll('#scheduleGrid td.selected').length));
console.log('errors',p.consoleErrors);
await b.close();
