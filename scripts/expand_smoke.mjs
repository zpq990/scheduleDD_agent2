import {Browser} from 'file:///C:/Users/zpq99/.agents/skills/design-taste-frontend/scripts/lib/cdp.mjs';
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
const b=await Browser.launch({width:1440,height:1000});const p=await b.newPage({width:1440,height:1000});
const wait=async(e,ms=15000)=>{const t=Date.now();while(Date.now()-t<ms){if(await p.eval(e).catch(()=>false))return;await sleep(150)}throw new Error('timeout '+e)};
const click=async s=>{await p.eval(s=>document.querySelector(s)?.click(),s);await sleep(300)};
await p.goto('http://127.0.0.1:9870/');await wait("document.querySelector('#authScreen')");
await click('#authTabRegister');
await p.eval(()=>{document.querySelector('#authUsername').value='exp_'+Date.now();document.querySelector('#authPassword').value='expandpass1';document.querySelector('#authPassword2').value='expandpass1';});
await click('#authSubmit');await wait("!document.querySelector('#appLayout').classList.contains('hidden')");
await p.eval(async()=>{
 const doc=await fetch('/api/documents/basic').then(r=>r.json());
 const fields=[
  {key:'person',label:'人员',type:'text',required:true,builtin:true,hidden:false,width:14},
  {key:'contact',label:'联系方式',type:'text',required:false,builtin:true,hidden:false,width:18},
  {key:'address',label:'地址',type:'text',required:false,builtin:true,hidden:false,width:28},
  {key:'note',label:'备注',type:'textarea',required:false,builtin:true,hidden:false,width:36},
  {key:'note2',label:'备注2',type:'textarea',required:false,builtin:false,hidden:false,width:36},
 ];
 const rows=[{id:'rx',person:'跨周',contact:'',address:'虹桥',note:'会议',note2:'12月3日上午10点开会'}];
 await fetch('/api/documents/basic',{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({baseVersion:doc.version,fields,rows})});
});
await p.goto('http://127.0.0.1:9870/');await wait("document.querySelector('#basicGrid')");
await p.eval(()=>document.querySelector('[data-view=schedule]').click());await sleep(400);
await click('#buildScheduleBtn');await wait("document.querySelector('#modalTitle').textContent.includes('生成行程预览')");
await p.eval(()=>[...document.querySelectorAll('#modalActions button')].find(b=>b.textContent.includes('写入选中')).click());await sleep(1200);
console.log('meta',await p.eval(()=>document.querySelector('#scheduleMeta').textContent));
console.log('after disabled',await p.eval(()=>document.querySelector('#weekAfter').disabled));
await click('#weekAfter');await click('#weekAfter');
console.log('meta3',await p.eval(()=>document.querySelector('#scheduleMeta').textContent));
console.log('week active',await p.eval(()=>({this:document.querySelector('#weekThis').classList.contains('active'),next:document.querySelector('#weekNext').classList.contains('active')})));
console.log('cell text',await p.eval(()=>[...document.querySelectorAll('#scheduleGrid td[data-date] .cell')].map(e=>e.textContent).filter(Boolean)));
console.log('errors',p.consoleErrors);
await b.close();
