import {Browser} from 'file:///C:/Users/zpq99/.agents/skills/design-taste-frontend/scripts/lib/cdp.mjs';

const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
const browser = await Browser.launch({width: 1440, height: 1000});
const page = await browser.newPage({width: 1440, height: 1000});
const wait = async (expression, timeout = 25000) => {
  const started = Date.now();
  while (Date.now() - started < timeout) {
    if (await page.eval(expression).catch(() => false)) return;
    await sleep(150);
  }
  throw new Error(`等待超时：${expression}`);
};
const click = async selector => {
  await page.eval(s => document.querySelector(s)?.click(), selector);
  await sleep(300);
};

try {
  await page.goto('http://127.0.0.1:9870/');
  if (await page.eval(() => !document.querySelector('#authScreen').classList.contains('hidden'))) {
    await click('#authTabRegister');
    await page.eval(() => {
      document.querySelector('#authUsername').value = `rs_${Date.now()}`;
      document.querySelector('#authPassword').value = 'rspassword1';
      document.querySelector('#authPassword2').value = 'rspassword1';
    });
    await click('#authSubmit');
    await wait("!document.querySelector('#appLayout').classList.contains('hidden')");
  }
  await wait("document.querySelector('#basicGrid')");
  await page.eval(() => (window.confirm = () => true));
  await click('#demoBtn');
  await sleep(900);
  await click('[data-view=schedule]');
  await page.eval(() => {
    const input = document.querySelector('#agentInstruction');
    input.value = '把周二下午的拜访改到周四上午';
    input.dispatchEvent(new Event('input', {bubbles: true}));
  });
  await click('#agentReschedule');
  const asked = [];
  for (let i = 0; i < 5; i++) {
    await wait("!document.querySelector('#agentQuestion').classList.contains('hidden') || !document.querySelector('#agentSummary').classList.contains('hidden')");
    if (await page.eval(() => !document.querySelector('#agentSummary').classList.contains('hidden'))) break;
    asked.push(await page.eval(() => document.querySelector('#agentQuestion h3').textContent));
    await page.eval(() => document.querySelector('#agentQuestion .option').click());
    await sleep(500);
  }
  await wait("!document.querySelector('#agentSummary').classList.contains('hidden')");
  const summary = await page.eval(() => document.querySelector('#agentSummary').textContent);
  console.log(JSON.stringify({asked, summary, errors: page.consoleErrors}, null, 2));
  await page.screenshot({path: 'test-results/reschedule.jpg', quality: 86, fullPage: true});
} finally {
  await browser.close();
}
