import {mkdirSync, writeFileSync} from 'node:fs';
import {resolve} from 'node:path';
import assert from 'node:assert/strict';
import {Browser} from 'file:///C:/Users/zpq99/.agents/skills/design-taste-frontend/scripts/lib/cdp.mjs';

const base = process.env.AGENT2_URL || 'http://127.0.0.1:9870';
const out = resolve('test-results', `ui-${Date.now()}`);
mkdirSync(out, {recursive: true});
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));

const browser = await Browser.launch({width: 1440, height: 1000});
const page = await browser.newPage({width: 1440, height: 1000});
const checks = [];
const wait = async (expr, timeout = 20000) => {
  const started = Date.now();
  while (Date.now() - started < timeout) {
    if (await page.eval(expr).catch(() => false)) return;
    await sleep(120);
  }
  throw new Error(`等待超时：${expr}`);
};
const click = async selector => {
  await page.eval(s => document.querySelector(s)?.scrollIntoView({block: 'center'}), selector);
  await sleep(120);
  const point = await page.eval(s => {
    const element = document.querySelector(s);
    if (!element) return null;
    const rect = element.getBoundingClientRect();
    return {x: rect.x + rect.width / 2, y: rect.y + rect.height / 2};
  }, selector);
  if (!point) throw new Error(`找不到 ${selector}`);
  await page.send('Input.dispatchMouseEvent', {type: 'mousePressed', x: point.x, y: point.y, button: 'left', clickCount: 1});
  await page.send('Input.dispatchMouseEvent', {type: 'mouseReleased', x: point.x, y: point.y, button: 'left', clickCount: 1});
  await sleep(220);
};
const key = async (key, code, vk, modifiers = 0) => {
  await page.send('Input.dispatchKeyEvent', {type: 'keyDown', key, code, windowsVirtualKeyCode: vk, modifiers});
  await page.send('Input.dispatchKeyEvent', {type: 'keyUp', key, code, windowsVirtualKeyCode: vk, modifiers});
  await sleep(40);
};
const shot = name => page.screenshot({path: `${out}/${name}.jpg`, quality: 86, fullPage: true});

try {
  await page.goto(base);
  if (await page.eval(() => !document.querySelector('#authScreen').classList.contains('hidden'))) {
    await click('#authTabRegister');
    await page.eval(() => {
      document.querySelector('#authUsername').value = `ui_${Date.now()}`;
      document.querySelector('#authPassword').value = 'uipassword1';
      document.querySelector('#authPassword2').value = 'uipassword1';
    });
    await click('#authSubmit');
    await wait("!document.querySelector('#appLayout').classList.contains('hidden')");
    checks.push('注册并登录');
  }
  await wait("document.querySelector('#basicGrid')");
  await page.eval(() => (window.confirm = () => true));
  await shot('01-basic');
  checks.push('基本信息表打开');

  await click('#demoBtn');
  await wait("document.querySelectorAll('#basicGrid tbody tr').length >= 3");
  await shot('02-demo');
  checks.push('示例数据载入');

  await click('#basicSearch');
  for (const ch of '王建国') {
    await page.send('Input.insertText', {text: ch});
    await sleep(20);
  }
  await wait("document.querySelector('#basicSearchMeta').textContent.includes('条')");
  assert.ok((await page.eval(() => document.querySelector('#basicSearchMeta').textContent)).includes('条'));
  await shot('03-search');
  checks.push('全字段搜索');

  await page.eval(() => {
    const cell = document.querySelector('#basicGrid tbody tr td[data-field=person] .cell');
    cell.focus();
  });
  await key('a', 'KeyA', 65, 2);
  await page.send('Input.insertText', {text: '王建国测试'});
  await page.eval(() => document.querySelector('.sheet-title h2').click());
  await sleep(1600);
  assert.equal(await page.eval(() => document.querySelector('#saveState').textContent), '已保存');
  await page.goto(base);
  await wait("document.querySelector('#basicGrid tbody tr')");
  assert.equal(await page.eval(() => document.querySelector('#basicGrid tbody tr td[data-field=person] .cell').textContent), '王建国测试');
  checks.push('编辑与自动保存持久化');

  await click('[data-view=schedule]');
  await wait("document.querySelectorAll('#scheduleGrid td[data-date]').length > 0");
  assert.equal(await page.eval(() => document.querySelectorAll('#scheduleGrid td[data-date]').length), 35);
  await shot('04-schedule');
  await click('#weekNext');
  await wait("document.querySelector('#scheduleMeta').textContent.includes('下周')");
  assert.equal(await page.eval(() => document.querySelector('#weekNext').classList.contains('active')), true);
  assert.equal(await page.eval(() => document.querySelectorAll('#scheduleGrid td[data-date]').length), 35);
  await shot('04b-schedule-next');
  await click('#weekThis');
  await wait("document.querySelector('#scheduleMeta').textContent.includes('本周')");
  checks.push('两周行程视图与本周/下周切换');

  await click('#buildScheduleBtn');
  await wait("document.querySelector('#modalTitle').textContent.includes('生成行程预览')");
  assert.ok((await page.eval(() => document.querySelectorAll('#modalBody [data-preview]').length)) >= 1);
  await shot('04a-extract-preview');
  await page.eval(() => document.querySelector('#modalClose').click());
  checks.push('从任意字段生成行程预览');

  await click('[data-agent=build_schedule]');
  await wait("!document.querySelector('#agentQuestion').classList.contains('hidden')");
  await shot('05-agent-question');
  await click('#agentQuestion .option');
  await wait("!document.querySelector('#agentSummary').classList.contains('hidden')");
  assert.ok((await page.eval(() => document.querySelector('#agentSummary').textContent)).includes('已交付'));
  assert.ok((await page.eval(() => document.querySelectorAll('#agentTrace .line').length)) > 2);
  await shot('06-agent-done');
  checks.push('任务助手生成行程 + 确认 + 执行记录');

  await click('#multiSelectBtn');
  await page.eval(() => {
    const cell = [...document.querySelectorAll('#scheduleGrid td[data-date]')].find(td => td.querySelector('.cell')?.textContent.trim());
    cell?.click();
  });
  await click('#clearCellsBtn');
  await wait("document.querySelector('#modalTitle').textContent.includes('清除')");
  assert.ok((await page.eval(() => document.querySelector('#modalTitle').textContent)).includes('个格子'));
  await shot('06b-clear-confirm');
  await page.eval(() => [...document.querySelectorAll('#modalActions button')].find(button => button.textContent === '取消').click());
  await click('#multiSelectBtn');
  checks.push('多选与清除确认');

  await click('[data-view=cards]');
  await wait("document.querySelector('#cardPreview img') || document.querySelector('#cardPreview .hint')");
  await sleep(700);
  assert.ok((await page.eval(() => document.querySelectorAll('#cardList .card-date').length)) >= 1);
  await shot('07-cards');
  checks.push('卡片浏览');

  await click('[data-view=settings]');
  await wait("document.querySelector('#smtpHost')");
  await page.eval(() => { document.querySelector('.smtp-box').open = true; });
  assert.equal(await page.eval(() => Boolean(document.querySelector('#smtpHost'))), true);
  await shot('08-settings');
  checks.push('邮件与 SMTP 设置');

  await page.setViewport({width: 390, height: 844});
  assert.equal(await page.eval(() => document.documentElement.scrollWidth > innerWidth), false);
  await shot('09-mobile');
  checks.push('390px 无横向溢出');

  assert.equal(page.consoleErrors.length, 0, page.consoleErrors.join('\n'));
  writeFileSync(`${out}/results.json`, JSON.stringify({passed: true, checks, consoleErrors: page.consoleErrors}, null, 2));
  console.log(JSON.stringify({passed: true, out, checks}, null, 2));
} finally {
  await browser.close();
}
