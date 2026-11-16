const $ = selector => document.querySelector(selector);
const $$ = selector => [...document.querySelectorAll(selector)];

const state = {
  view: 'basic',
  basic: null,
  schedule: null,
  status: null,
  saveTimer: null,
  saving: false,
  dirty: false,
  dirtyKinds: new Set(),
  searchHits: [],
  searchIndex: -1,
  searchTimer: null,
  scheduleSearchHits: [],
  scheduleSearchIndex: -1,
  cards: [],
  selectedCard: '',
  scheduleWeek: 0,
  scheduleSelectMode: false,
  scheduleSelection: new Set(),
  lastScheduleCell: null,
  versions: [],
  selectedVersion: null,
  agentStream: null,
  agentQuestion: null,
  activeCell: null,
  user: null,
  authMode: 'login',
  accessCode: localStorage.getItem('dd_access_code') || '',
};

const SLOTS = ['上午', '中午', '下午', '晚上', '需要配合'];
const WEEKDAYS = ['周日', '周一', '周二', '周三', '周四', '周五', '周六'];

const esc = value => String(value ?? '')
  .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
  .replace(/"/g, '&quot;');

function safeSnippet(value) {
  return esc(value).replace(/&lt;mark&gt;/g, '<mark>').replace(/&lt;\/mark&gt;/g, '</mark>');
}

const uid = prefix => `${prefix}_${Math.random().toString(16).slice(2, 10)}`;

function toast(text) {
  const el = $('#toast');
  el.textContent = text;
  el.classList.remove('hidden');
  clearTimeout(el._timer);
  el._timer = setTimeout(() => el.classList.add('hidden'), 2600);
}

function api(path, {method = 'GET', body, headers = {}} = {}) {
  const options = {method, headers: {...headers}};
  if (state.accessCode) options.headers['X-Access-Code'] = state.accessCode;
  if (body !== undefined) {
    options.headers['Content-Type'] = 'application/json';
    options.body = JSON.stringify(body);
  }
  return fetch(path, options).then(async response => {
    if (response.status === 401) {
      const data = await response.json().catch(() => ({}));
      if (data.code === 'AUTH_REQUIRED') {
        showAuth();
        const error = new Error('请先登录');
        error.status = 401;
        error.code = data.code;
        throw error;
      }
      if (data.code === 'ACCESS_CODE') {
        const code = window.prompt('请输入访问码');
        if (code) {
          state.accessCode = code;
          localStorage.setItem('dd_access_code', code);
          return api(path, {method, body, headers});
        }
      }
      const error = new Error(data.error || '请求失败');
      error.status = 401;
      throw error;
    }
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      const error = new Error(data.error || `请求失败（${response.status}）`);
      error.payload = data;
      error.status = response.status;
      throw error;
    }
    return data;
  });
}

function showAuth() {
  document.body.classList.add('auth-mode');
  $('#authScreen').classList.remove('hidden');
  $('#appLayout').classList.add('hidden');
  $('#logoutBtn').classList.add('hidden');
  setTimeout(() => $('#authUsername')?.focus(), 40);
}

function hideAuth() {
  document.body.classList.remove('auth-mode');
  $('#authScreen').classList.add('hidden');
  $('#appLayout').classList.remove('hidden');
  $('#logoutBtn').classList.remove('hidden');
}

function renderUser() {
  $('#userLabel').textContent = state.user ? state.user.username : '';
}

function setAuthMode(mode) {
  state.authMode = mode;
  $('#authTabLogin').classList.toggle('active', mode === 'login');
  $('#authTabRegister').classList.toggle('active', mode === 'register');
  $('#authConfirmRow').classList.toggle('hidden', mode !== 'register');
  $('#authSubmit').textContent = mode === 'login' ? '登录' : '注册并进入';
  $('#authError').textContent = '';
}

async function submitAuth() {
  const username = $('#authUsername').value.trim();
  const password = $('#authPassword').value;
  const errorEl = $('#authError');
  errorEl.textContent = '';
  if (!username || !password) {
    errorEl.textContent = '请输入账号和密码';
    return;
  }
  if (state.authMode === 'register' && password !== $('#authPassword2').value) {
    errorEl.textContent = '两次密码不一致';
    return;
  }
  try {
    const result = await api(`/api/auth/${state.authMode}`, {method: 'POST', body: {username, password}});
    state.user = result.user;
    renderUser();
    hideAuth();
    await loadAll();
    switchView('basic');
    toast(`欢迎，${state.user.username}`);
  } catch (error) {
    errorEl.textContent = error.message;
  }
}

async function logout() {
  try { await api('/api/auth/logout', {method: 'POST'}); } catch (error) { /* ignore */ }
  state.user = null;
  state.basic = null;
  state.schedule = null;
  state.searchHits = [];
  state.scheduleSearchHits = [];
  renderUser();
  showAuth();
}

function fileToBase64(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result).split(',')[1] || '');
    reader.onerror = () => reject(new Error('读取文件失败'));
    reader.readAsDataURL(file);
  });
}

const addDays = (date, n) => {
  const value = new Date(`${date}T12:00:00Z`);
  value.setUTCDate(value.getUTCDate() + n);
  return value.toISOString().slice(0, 10);
};
const weekdayOf = date => WEEKDAYS[new Date(`${date}T12:00:00Z`).getUTCDay()];
const monthDay = date => date ? `${Number(date.slice(5, 7))}月${Number(date.slice(8, 10))}日` : '';
const today = () => new Date().toISOString().slice(0, 10);

function setSaveState(text, tone = 'neutral') {
  const el = $('#saveState');
  el.textContent = text;
  el.className = `pill ${tone}`;
}

async function loadAll() {
  state.status = await api('/api/status');
  [state.basic, state.schedule] = await Promise.all([
    api('/api/documents/basic'),
    api('/api/documents/schedule'),
  ]);
  state.dirtyKinds.clear();
  state.dirty = false;
  state.scheduleSelection.clear();
  state.lastScheduleCell = null;
  fillBasicFieldFilter();
  renderAll();
  loadEmailSettings();
  loadCards();
  state.scheduleSearchHits = [];
  setSaveState(state.dirty ? '未保存' : '已保存', state.dirty ? 'amber' : 'neutral');
}

function renderAll() {
  renderBasic();
  renderSchedule();
  renderVersions();
  if (state.view === 'settings') {
    loadEmailSettings();
  }
}

function switchView(view) {
  state.view = view;
  $$('#views button').forEach(button => button.classList.toggle('active', button.dataset.view === view));
  $$('.view').forEach(section => section.classList.toggle('active', section.id === `view-${view}`));
  if (view === 'cards') loadCards();
  if (view === 'versions') loadVersions();
  if (view === 'settings') {
    loadEmailSettings();
  }
}

/* ---------- basic table ---------- */

function fillBasicFieldFilter() {
  const select = $('#basicFieldFilter');
  const current = select.value;
  select.innerHTML = '<option value="">全部字段</option>' + state.basic.fields
    .filter(field => !field.hidden)
    .map(field => `<option value="${esc(field.key)}">${esc(field.label)}</option>`)
    .join('');
  select.value = current;
}

function renderBasic() {
  if (!state.basic) return;
  const fields = state.basic.fields.filter(field => !field.hidden);
  const rows = state.basic.rows;
  const hitRows = new Set(state.searchHits.map(hit => hit.rowId));
  const filtering = $('#basicFilter')?.checked && state.searchHits.length > 0;
  const visibleRows = filtering ? rows.filter(row => hitRows.has(row.id)) : rows;
  const head = [
    '<thead><tr><th class="row-index">#</th>',
    ...fields.map(field => `<th data-field="${esc(field.key)}"><span class="th-inner"><span>${esc(field.label)}</span><span class="field-type">${esc(field.type === 'textarea' ? '多行' : field.type === 'date' ? '日期' : '')}</span></span></th>`),
    '<th style="width:34px"></th></tr></thead>',
  ].join('');
  const body = visibleRows.map((row, index) => {
    const cells = fields.map(field => {
      const hit = state.searchHits.some(item => item.rowId === row.id && item.field === field.key);
      return `<td data-row="${esc(row.id)}" data-field="${esc(field.key)}" class="${hit ? 'search-hit' : ''}"><div class="cell" contenteditable="true" spellcheck="false">${esc(row[field.key] || '')}</div></td>`;
    }).join('');
    return `<tr data-row="${esc(row.id)}"><td class="row-index">${index + 1}</td>${cells}<td class="row-actions"><button type="button" data-delete-row="${esc(row.id)}" title="删除这一行">×</button></td></tr>`;
  }).join('');
  $('#basicGrid').innerHTML = head + `<tbody>${body || `<tr><td colspan="${fields.length + 2}" style="padding:18px;color:var(--muted);">还没有数据，点「加行」或「导入」。</td></tr>`}</tbody>`;
  const invalid = rows.filter(row => !String(row.person || '').trim()).length;
  $('#basicMeta').textContent = `${rows.length} 行 · ${fields.length} 个显示字段 · 版本 v${state.basic.version}${invalid ? ` · ${invalid} 行缺人员` : ''}`;
}

function updateCell(rowId, fieldKey, value) {
  const row = state.basic.rows.find(item => item.id === rowId);
  if (!row || String(row[fieldKey] || '') === value) return;
  row[fieldKey] = value;
  markDirty('basic');
}

function markDirty(kind = 'basic') {
  state.dirty = true;
  state.dirtyKinds.add(kind);
  setSaveState('未保存', 'amber');
  clearTimeout(state.saveTimer);
  state.saveTimer = setTimeout(() => saveCurrent(false), 900);
}

function updateSheetMeta() {
  if (state.basic) {
    const fieldCount = state.basic.fields.filter(field => !field.hidden).length;
    $('#basicMeta').textContent = `${state.basic.rows.length} 行 · ${fieldCount} 个显示字段 · 版本 v${state.basic.version}`;
  }
  if (state.schedule) {
    $('#scheduleMeta').textContent = `${state.schedule.meta.startDate || ''} 起 · ${Number(state.schedule.meta.weeks || 2)} 周 · ${state.schedule.rows.length} 条 · v${state.schedule.version}`;
  }
}

async function saveCurrent(manual = false, force = false) {
  if (!state.basic || !state.schedule) return;
  if (state.saving) {
    if (manual) toast('正在保存，请稍候');
    return;
  }
  const manualKind = state.view === 'schedule' ? 'schedule' : 'basic';
  const kinds = manual
    ? [...new Set([...state.dirtyKinds, manualKind])]
    : [...state.dirtyKinds];
  if (!kinds.length) return;
  state.saving = true;
  try {
    for (const kind of kinds) {
      const doc = kind === 'schedule' ? state.schedule : state.basic;
      const body = {
        baseVersion: force ? (doc.version || 0) : doc.version,
        fields: kind === 'basic' ? state.basic.fields : undefined,
        rows: kind === 'basic' ? state.basic.rows : state.schedule.rows,
        meta: kind === 'schedule' ? state.schedule.meta : undefined,
        reason: manual ? '手动保存' : '自动保存',
      };
      const updated = await api(`/api/documents/${kind}`, {method: 'PATCH', body});
      if (kind === 'basic') state.basic = updated;
      else state.schedule = updated;
      state.dirtyKinds.delete(kind);
    }
    state.dirty = state.dirtyKinds.size > 0;
    setSaveState(state.dirty ? '未保存' : '已保存', state.dirty ? 'amber' : 'green');
    updateSheetMeta();
    if (manual) toast('已保存');
  } catch (error) {
    if (error.status === 409) {
      openConflict(kinds[0], error.payload);
    } else {
      toast(error.message);
      setSaveState('保存失败', 'red');
    }
  } finally {
    state.saving = false;
  }
}

function openConflict(kind, payload) {
  const current = payload.current;
  const changes = payload.diff || [];
  setSaveState('版本冲突', 'red');
  openModal('版本冲突', `
    <p>这份文档在别处被修改过。当前服务端版本 v${current.version}，你的修改没有覆盖它。</p>
    <ul class="diff-list">${changes.slice(0, 12).map(change => `<li>${esc(describeChange(change))}</li>`).join('') || '<li>内容差异</li>'}</ul>
    <p class="hint">可以重新载入服务端版本，或强制用你当前页面上的版本覆盖。</p>
  `, [
    {label: '重新载入服务端', className: 'ghost small', action: async () => { closeModal(); await refreshDocument(kind); }},
    {label: '用我的版本覆盖', className: 'primary small', action: async () => { closeModal(); await forceSave(kind); }},
  ]);
}

function describeChange(change) {
  if (change.type === 'fields') return '表头发生变化';
  if (change.type === 'meta') return '行程表头信息发生变化';
  if (change.type === 'row_added') return `新增一行：${change.rowId}`;
  if (change.type === 'row_deleted') return `删除一行：${change.rowId}`;
  if (change.type === 'row_updated') return `更新一行 ${change.rowId}：${(change.fields || []).join('、')}`;
  return change.type;
}

async function forceSave(kind) {
  const doc = kind === 'basic' ? state.basic : state.schedule;
  try {
    const updated = await api(`/api/documents/${kind}`, {
      method: 'PATCH',
      body: {
        baseVersion: (await api(`/api/documents/${kind}`)).version,
        fields: kind === 'basic' ? doc.fields : undefined,
        rows: kind === 'basic' ? doc.rows : doc.rows,
        meta: kind === 'schedule' ? doc.meta : undefined,
        reason: '冲突后覆盖',
      },
    });
    if (kind === 'basic') state.basic = updated; else state.schedule = updated;
    state.dirty = false;
    state.dirtyKinds.clear();
    setSaveState('已保存', 'green');
    renderAll();
    toast('已用当前页面版本覆盖');
  } catch (error) {
    toast(error.message);
  }
}

async function refreshDocument(kind) {
  const doc = await api(`/api/documents/${kind}`);
  if (kind === 'basic') state.basic = doc; else state.schedule = doc;
  state.dirty = false;
  state.dirtyKinds.clear();
  setSaveState('已保存', 'green');
  renderAll();
  toast('已载入服务端版本');
}

/* ---------- basic interactions ---------- */

function fillActiveCell(value) {
  if (!state.activeCell) return false;
  const row = state.basic.rows.find(item => item.id === state.activeCell.rowId);
  const field = state.basic.fields.find(item => item.key === state.activeCell.field);
  if (!row || !field) return false;
  row[field.key] = value;
  markDirty('basic');
  renderBasic();
  toast(`已填入「${field.label}」`);
  return true;
}

function addRow() {
  const row = {id: uid('r')};
  state.basic.fields.forEach(field => (row[field.key] = ''));
  state.basic.rows.unshift(row);
  markDirty('basic');
  renderBasic();
  const cell = document.querySelector(`[data-row="${row.id}"] .cell`);
  if (cell) cell.focus();
}

function deleteRow(rowId) {
  const row = state.basic.rows.find(item => item.id === rowId);
  if (!row) return;
  if (!window.confirm(`删除「${row.person || '这一行'}」？`)) return;
  state.basic.rows = state.basic.rows.filter(item => item.id !== rowId);
  markDirty('basic');
  renderBasic();
}

function openAddField() {
  openModal('增加字段', `
    <label class="field-label">字段名<input id="newFieldLabel" placeholder="例如：公司" /></label>
    <label class="field-label">类型<select id="newFieldType"><option value="text">单行文本</option><option value="textarea">多行文本</option><option value="date">日期</option></select></label>
  `, [
    {label: '取消', className: 'ghost small', action: closeModal},
    {label: '添加', className: 'primary small', action: () => {
      const label = $('#newFieldLabel').value.trim();
      if (!label) return toast('字段名不能为空');
      if (state.basic.fields.some(field => field.label === label)) return toast('字段名已存在');
      state.basic.fields.push({key: uid('f'), label, type: $('#newFieldType').value, required: false, builtin: false, hidden: false, width: 18});
      state.basic.rows.forEach(row => (row[state.basic.fields.at(-1).key] = ''));
      markDirty('basic');
      closeModal();
      fillBasicFieldFilter();
      renderBasic();
    }},
  ]);
  setTimeout(() => $('#newFieldLabel')?.focus(), 30);
}

function openManageFields() {
  const list = state.basic.fields.map(field => `
    <div class="field-row" data-field="${esc(field.key)}" style="display:grid;grid-template-columns:1fr 100px 70px 34px;gap:6px;align-items:center;margin-bottom:6px;">
      <input data-label value="${esc(field.label)}" ${field.key === 'person' ? 'disabled' : ''} />
      <select data-type ${field.key === 'person' ? 'disabled' : ''}>
        <option value="text" ${field.type === 'text' ? 'selected' : ''}>单行</option>
        <option value="textarea" ${field.type === 'textarea' ? 'selected' : ''}>多行</option>
        <option value="date" ${field.type === 'date' ? 'selected' : ''}>日期</option>
      </select>
      <label class="check"><input type="checkbox" data-hidden ${field.hidden ? 'checked' : ''}/> 隐藏</label>
      <button type="button" class="icon-btn" data-remove-field ${field.key === 'person' ? 'disabled' : ''}>×</button>
    </div>`).join('');
  openModal('字段管理', `<p class="hint">人员字段是必填字段，不能删除。删除其他字段会同时删除该列数据。</p>${list}`, [
    {label: '关闭', className: 'ghost small', action: closeModal},
    {label: '保存字段', className: 'primary small', action: () => {
      const next = [];
      $$('#modalBody .field-row').forEach(row => {
        const key = row.dataset.field;
        const label = row.querySelector('[data-label]').value.trim();
        const type = row.querySelector('[data-type]').value;
        const hidden = row.querySelector('[data-hidden]').checked;
        if (!label) return;
        next.push({...state.basic.fields.find(field => field.key === key), label, type, hidden});
      });
      if (new Set(next.map(field => field.label)).size !== next.length) return toast('字段名不能重复');
      const keys = new Set(next.map(field => field.key));
      const removed = state.basic.fields.filter(field => !keys.has(field.key));
      if (removed.length && !window.confirm(`删除字段会同时删除这些列的数据：${removed.map(field => field.label).join('、')}。继续？`)) return;
      state.basic.fields = next;
      state.basic.rows.forEach(row => {
        Object.keys(row).forEach(key => {
          if (key !== 'id' && !keys.has(key)) delete row[key];
        });
        next.forEach(field => { if (!(field.key in row)) row[field.key] = ''; });
      });
      markDirty('basic');
      closeModal();
      fillBasicFieldFilter();
      renderBasic();
    }},
  ]);
  $$('#modalBody [data-remove-field]').forEach(button => {
    button.addEventListener('click', () => {
      const key = button.closest('.field-row').dataset.field;
      if (key === 'person') return;
      button.closest('.field-row').remove();
    });
  });
}

/* ---------- schedule ---------- */

function scheduleItemsFor(date, slot) {
  return (state.schedule?.rows || []).filter(item => item.date === date && item.slot === slot);
}

function visibleScheduleDates() {
  const start = state.schedule?.meta?.startDate || today();
  const weeks = Number(state.schedule?.meta?.weeks || 2);
  state.scheduleWeek = Math.max(0, Math.min(weeks - 1, Number(state.scheduleWeek) || 0));
  return Array.from({length: 7}, (_, index) => addDays(start, state.scheduleWeek * 7 + index));
}

function weekStartOf(date) {
  const weekday = new Date(`${date}T12:00:00Z`).getUTCDay();
  return addDays(date, -weekday);
}

function currentWeekStart() {
  return weekStartOf(today());
}

function nextWeekStart() {
  return addDays(currentWeekStart(), 7);
}

function setScheduleWeek(mode) {
  const meta = state.schedule.meta;
  const start = meta.startDate || today();
  const weeks = Number(meta.weeks || 2);
  const target = mode === 'next' ? nextWeekStart() : currentWeekStart();
  const delta = Math.round((new Date(`${target}T12:00:00Z`) - new Date(`${start}T12:00:00Z`)) / 86400000);
  if (delta >= 0 && delta % 7 === 0 && delta / 7 < weeks) {
    state.scheduleWeek = delta / 7;
  } else {
    state.scheduleWeek = mode === 'next' ? Math.min(1, weeks - 1) : 0;
  }
  renderSchedule();
}

function renderSchedule() {
  if (!state.schedule) return;
  const start = state.schedule.meta.startDate || today();
  $('#scheduleStart').value = start;
  const dates = visibleScheduleDates();
  const holidays = state.schedule.meta.holidays || {};
  const shownWeekStart = dates[0];
  const thisWeek = currentWeekStart();
  const followWeek = nextWeekStart();
  $('#weekThis').classList.toggle('active', shownWeekStart === thisWeek);
  $('#weekNext').classList.toggle('active', shownWeekStart === followWeek);
  const totalWeeks = Number(state.schedule.meta.weeks || 2);
  $('#weekNext').disabled = totalWeeks < 2;
  $('#weekPrev').disabled = state.scheduleWeek <= 0;
  $('#weekAfter').disabled = state.scheduleWeek >= totalWeeks - 1;
  let html = '<table><thead>';
  html += '<tr><th style="width:72px">时段</th>' + dates.map(date => `<th>${esc(weekdayOf(date))}<br><span class="hint">${esc(monthDay(date))}</span></th>`).join('') + '</tr></thead><tbody>';
  html += `<tr class="meta-row"><td class="slot-label">节假日</td>${dates.map(date => `<td>${esc(holidays[date] || '')}</td>`).join('')}</tr>`;
  for (const slot of SLOTS) {
    html += `<tr><td class="slot-label">${esc(slot)}</td>`;
    for (const date of dates) {
      const items = scheduleItemsFor(date, slot);
      const text = items.map(item => item.text).join('\n');
      html += `<td data-date="${esc(date)}" data-slot="${esc(slot)}"><div class="cell" contenteditable="true" spellcheck="false">${esc(text)}</div></td>`;
    }
    html += '</tr>';
  }
  html += '</tbody></table>';
  $('#scheduleGrid').innerHTML = html;
  const label = shownWeekStart === thisWeek ? '本周' : shownWeekStart === followWeek ? '下周' : '行程';
  $('#scheduleMeta').textContent = `${label} · ${monthDay(dates[0])}–${monthDay(dates[6])} · ${state.schedule.rows.length} 条 · v${state.schedule.version}`;
  $('#multiSelectBtn').classList.toggle('active', state.scheduleSelectMode);
  $('#multiSelectBtn').textContent = state.scheduleSelectMode ? '结束多选' : '多选';
  renderPending();
}

function updateScheduleCell(date, slot, text) {
  const lines = String(text).split('\n').map(line => line.trim()).filter(Boolean);
  const existing = scheduleItemsFor(date, slot);
  const next = [];
  lines.forEach((line, index) => {
    const previous = existing[index];
    next.push({
      id: previous?.id || uid('s'),
      date,
      slot,
      text: line,
      ...(previous?.rowId ? {rowId: previous.rowId} : {}),
    });
  });
  state.schedule.rows = state.schedule.rows
    .filter(item => !(item.date === date && item.slot === slot))
    .concat(next);
  markDirty('schedule');
  $('#scheduleMeta').textContent = `${state.schedule.meta.startDate} 起 · ${Number(state.schedule.meta.weeks || 2)} 周 · ${state.schedule.rows.length} 条 · v${state.schedule.version}`;
}

const STATUS_LABELS = {
  ready: '可写入',
  needs_time: '无时间，放需要配合',
  ambiguous_time: '时间待确认',
  needs_date: '缺日期',
  conflict_date: '多个日期',
};

function previewEvidence(item) {
  return (item.evidence || [])
    .map(entry => `${entry.label}：${entry.raw}`)
    .filter((value, index, list) => list.indexOf(value) === index)
    .slice(0, 3)
    .join('；');
}

function scheduleKey(date, slot) {
  return `${date}|${slot}`;
}

function toggleScheduleCell(date, slot) {
  const key = scheduleKey(date, slot);
  if (state.scheduleSelection.has(key)) state.scheduleSelection.delete(key);
  else state.scheduleSelection.add(key);
  const cell = document.querySelector(`#scheduleGrid td[data-date="${date}"][data-slot="${slot}"]`);
  if (cell) cell.classList.toggle('selected', state.scheduleSelection.has(key));
  const count = state.scheduleSelection.size;
  $('#clearCellsBtn').title = count ? `清除选中 ${count} 个格子的行程内容` : '清除选中格子的行程内容';
}

function clearSelectedSchedule() {
  let targets = [...state.scheduleSelection].map(key => {
    const [date, slot] = key.split('|');
    return {date, slot};
  });
  if (!targets.length && state.lastScheduleCell) targets = [state.lastScheduleCell];
  const cells = targets.map(target => {
    const items = scheduleItemsFor(target.date, target.slot);
    return {...target, items};
  }).filter(cell => cell.items.length);
  if (!cells.length) {
    toast('选中的格子里没有行程内容');
    return;
  }
  const list = cells.map(cell => `<li>${esc(monthDay(cell.date))} ${esc(cell.slot)}：${cell.items.map(item => esc(item.text)).join('；')}</li>`).join('');
  openModal(`清除 ${cells.length} 个格子的行程？`, `
    <p>以下 <b>${cells.length}</b> 个格子里的行程内容都会被清除：</p>
    <ul class="diff-list">${list}</ul>
    <p class="hint">清除后会自动保存，可到「版本」里恢复。基础信息表不会被修改。</p>
  `, [
    {label: '取消', className: 'ghost small', action: closeModal},
    {label: '确认清除', className: 'primary small', action: () => {
      const keys = new Set(cells.map(cell => scheduleKey(cell.date, cell.slot)));
      state.schedule.rows = state.schedule.rows.filter(item => !keys.has(scheduleKey(item.date, item.slot)));
      state.scheduleSelection.clear();
      state.lastScheduleCell = null;
      markDirty('schedule');
      closeModal();
      renderSchedule();
      toast(`已清除 ${cells.length} 个格子，可在版本中恢复`);
    }},
  ]);
}

async function buildSchedule() {
  try {
    const result = await api('/api/schedule/extract', {method: 'POST', body: {baseDate: today()}});
    const items = result.items || [];
    if (!items.length) {
      openModal('没有可生成的行程', `<p>没有在基本信息表中找到日期信息。</p><p class="hint">日期可以写在任意字段里，例如「明天下午三点」「后天上午」「11月18日」。</p>`, [
        {label: '关闭', className: 'ghost small', action: closeModal},
      ]);
      return;
    }
    const range = result.range || {};
    const rows = items.map((item, index) => {
      const status = STATUS_LABELS[item.status] || item.status;
      const selected = item.status === 'ready' || item.status === 'needs_time' ? 'checked' : '';
      const dateValue = item.date || '';
      const slotOptions = ['<option value="">选择时段</option>', ...SLOTS.map(slot => `<option value="${esc(slot)}" ${slot === item.slot ? 'selected' : ''}>${esc(slot)}</option>`)].join('');
      return `<tr data-preview="${index}" data-row-id="${esc(item.rowId)}">
        <td style="text-align:center;"><input type="checkbox" data-pick ${selected} /></td>
        <td>${esc(item.person || '—')}</td>
        <td><input type="date" data-date value="${esc(dateValue)}" /></td>
        <td><select data-slot>${slotOptions}</select></td>
        <td><input type="time" data-time value="${esc(item.time || '')}" /></td>
        <td><input data-text value="${esc(item.text || '')}" /></td>
        <td><span class="chip ${item.status === 'ready' ? 'add' : item.status === 'needs_time' ? '' : 'warn'}">${esc(status)}</span><br><span class="hint">${esc(previewEvidence(item))}</span>${item.dates?.length > 1 ? `<br><span class="hint">候选：${item.dates.map(esc).join(' / ')}</span>` : ''}${item.rawTime ? `<br><span class="hint">原文：${esc(item.rawTime)}</span>` : ''}</td>
      </tr>`;
    }).join('');
    const issueRows = (result.issues || []).map(issue => `<li>${esc(issue.person || '未命名')}：${esc(issue.message || issue.status)}</li>`).join('');
    openModal('生成行程预览', `
      <p>当前基本信息表可生成 <b>${items.length}</b> 条。请确认日期、时段、时间与文字，未确认的不会写入。</p>
      <p class="hint">${range.expanded ? `写入后行程将自动扩展到 ${range.weeks} 周（${esc(range.startDate)} 起）。` : '写入后保留现有行程，同一天同一时段的内容会追加换行。'}</p>
      ${issueRows ? `<p class="hint">需要确认：</p><ul class="diff-list">${issueRows}</ul>` : ''}
      <div style="max-height:48vh;overflow:auto;">
        <table class="preview-table preview-schedule">
          <thead><tr><th style="width:34px;">选</th><th>人员</th><th style="width:130px;">日期</th><th style="width:110px;">时段</th><th style="width:100px;">时间</th><th>行程文本</th><th>状态 / 来源</th></tr></thead>
          <tbody>${rows}</tbody>
        </table>
      </div>
    `, [
      {label: '取消', className: 'ghost small', action: closeModal},
      {label: '写入选中行程', className: 'primary small', action: applySchedulePreview},
    ], true);
  } catch (error) {
    toast(error.message);
  }
}

async function applySchedulePreview() {
  const items = [];
  let invalid = '';
  $$('#modalBody [data-preview]').forEach(row => {
    if (!row.querySelector('[data-pick]').checked) return;
    const date = row.querySelector('[data-date]').value;
    const slot = row.querySelector('[data-slot]').value;
    const time = row.querySelector('[data-time]').value;
    const text = row.querySelector('[data-text]').value.trim();
    const person = row.querySelector('td:nth-child(2)')?.textContent || '';
    if (!date) {
      invalid = `${person} 缺少日期`;
      return;
    }
    if (!slot) {
      invalid = `${person} 缺少时段`;
      return;
    }
    if (!text) {
      invalid = `${person} 缺少行程文本`;
      return;
    }
    items.push({rowId: row.dataset.rowId, date, slot, time, text});
  });
  if (invalid) return toast(invalid);
  if (!items.length) return toast('请至少勾选一条');
  try {
    const result = await api('/api/schedule/apply', {
      method: 'POST',
      body: {items, baseVersion: state.schedule.version},
    });
    state.schedule = result.document;
    state.dirty = false;
    state.dirtyKinds.clear();
    closeModal();
    const start = state.schedule.meta.startDate;
    const first = items[0].date;
    const delta = Math.round((new Date(`${first}T12:00:00Z`) - new Date(`${start}T12:00:00Z`)) / 86400000);
    state.scheduleWeek = Math.max(0, Math.min(Number(state.schedule.meta.weeks || 2) - 1, Math.floor(delta / 7)));
    setSaveState('已保存', 'green');
    renderSchedule();
    toast(`已写入 ${result.added} 条${result.range?.weeks > 2 ? `，行程扩展到 ${result.range.weeks} 周` : ''}`);
  } catch (error) {
    toast(error.message);
  }
}


function renderPending() {
  const el = $('#pendingPanel');
  if (!state.pending || !state.pending.length) {
    el.classList.add('hidden');
    return;
  }
  el.classList.remove('hidden');
  el.innerHTML = `<h3>待定时段</h3><p class="hint">这些行只有日期，没有明确时段；不会自动猜。</p><ul>${state.pending.map(item => `<li>${esc(item.date)} · ${esc(item.person || '')} · ${esc(item.text || '')}</li>`).join('')}</ul>`;
}

/* ---------- search ---------- */

function scheduleSearch() {
  clearTimeout(state.searchTimer);
  state.searchTimer = setTimeout(async () => {
    const query = $('#basicSearch').value.trim();
    if (!query) {
      state.searchHits = [];
      state.searchIndex = -1;
      $('#basicSearchResults').classList.add('hidden');
      $('#basicSearchMeta').textContent = '';
      renderBasic();
      return;
    }
    try {
      const result = await api(`/api/documents/basic/search?q=${encodeURIComponent(query)}&field=${encodeURIComponent($('#basicFieldFilter').value)}`);
      state.searchHits = result.hits || [];
      state.searchIndex = state.searchHits.length ? 0 : -1;
      $('#basicSearchMeta').textContent = `${result.total} 条 · ${result.mode}`;
      renderBasic();
      renderBasicSearchResults();
      focusBasicHit();
    } catch (error) {
      toast(error.message);
    }
  }, 220);
}

function renderBasicSearchResults() {
  const panel = $('#basicSearchResults');
  if (!state.searchHits.length) {
    panel.innerHTML = '<p class="hint">没有匹配结果。</p>';
    panel.classList.remove('hidden');
    return;
  }
  panel.classList.remove('hidden');
  panel.innerHTML = `<h3>搜索结果</h3>` + state.searchHits.map((hit, index) => {
    const row = state.basic.rows.find(item => item.id === hit.rowId);
    const field = state.basic.fields.find(item => item.key === hit.field);
    return `<div class="search-hit-row" data-hit="${index}"><span class="field">${esc(field?.label || hit.field)}</span><span>${safeSnippet(hit.snippet)}</span><span class="hint">第 ${state.basic.rows.indexOf(row) + 1} 行</span></div>`;
  }).join('');
  $$('#basicSearchResults .search-hit-row').forEach(item => {
    item.addEventListener('click', () => {
      state.searchIndex = Number(item.dataset.hit);
      focusBasicHit();
      renderBasicSearchResults();
    });
  });
}

function focusBasicHit() {
  const hit = state.searchHits[state.searchIndex];
  if (!hit) return;
  const cell = document.querySelector(`[data-row="${hit.rowId}"][data-field="${hit.field}"]`);
  if (!cell) return;
  cell.scrollIntoView({behavior: 'smooth', block: 'center', inline: 'center'});
  cell.classList.add('search-current');
  setTimeout(() => cell.classList.remove('search-current'), 1400);
}

function moveBasicHit(delta) {
  if (!state.searchHits.length) return;
  state.searchIndex = (state.searchIndex + delta + state.searchHits.length) % state.searchHits.length;
  focusBasicHit();
  renderBasicSearchResults();
}

function scheduleSearchNow() {
  clearTimeout(state.searchTimer);
  state.searchTimer = setTimeout(async () => {
    const query = $('#scheduleSearch').value.trim();
    if (!query) {
      state.scheduleSearchHits = [];
      $('#scheduleSearchMeta').textContent = '';
      $$('#scheduleGrid .cell').forEach(cell => cell.classList.remove('search-current'));
      return;
    }
    try {
      const result = await api(`/api/documents/schedule/search?q=${encodeURIComponent(query)}`);
      state.scheduleSearchHits = result.hits || [];
      state.scheduleSearchIndex = state.scheduleSearchHits.length ? 0 : -1;
      $('#scheduleSearchMeta').textContent = `${result.total} 条 · ${result.mode}`;
      focusScheduleHit();
    } catch (error) {
      toast(error.message);
    }
  }, 220);
}

function focusScheduleHit() {
  const hit = state.scheduleSearchHits[state.scheduleSearchIndex];
  if (!hit) return;
  const item = state.schedule.rows.find(entry => entry.id === hit.rowId);
  if (!item) return;
  const start = state.schedule?.meta?.startDate || today();
  const delta = Math.round((new Date(`${item.date}T12:00:00Z`) - new Date(`${start}T12:00:00Z`)) / 86400000);
  const weeks = Number(state.schedule?.meta?.weeks || 2);
  if (delta >= 0 && delta < weeks * 7) {
    const week = Math.floor(delta / 7);
    if (week !== state.scheduleWeek) {
      state.scheduleWeek = week;
      renderSchedule();
    }
  }
  setTimeout(() => {
    const cell = document.querySelector(`#scheduleGrid td[data-date="${item.date}"][data-slot="${item.slot}"]`);
    if (!cell) return;
    cell.scrollIntoView({behavior: 'smooth', block: 'center', inline: 'center'});
    const inner = cell.querySelector('.cell');
    if (inner) inner.classList.add('search-current');
    setTimeout(() => inner?.classList.remove('search-current'), 1600);
  }, 60);
}

function moveScheduleHit(delta) {
  if (!state.scheduleSearchHits.length) return;
  state.scheduleSearchIndex = (state.scheduleSearchIndex + delta + state.scheduleSearchHits.length) % state.scheduleSearchHits.length;
  focusScheduleHit();
}

/* ---------- cards ---------- */

async function loadCards() {
  if (!state.schedule) return;
  const start = state.schedule.meta.startDate || today();
  const from = $('#cardsFrom')?.value || start;
  const to = $('#cardsTo')?.value || addDays(start, Number(state.schedule.meta.weeks || 2) * 7 - 1);
  if ($('#cardsFrom')) $('#cardsFrom').value = from;
  if ($('#cardsTo')) $('#cardsTo').value = to;
  try {
    const result = await api(`/api/schedule/cards?from_date=${encodeURIComponent(from)}&to_date=${encodeURIComponent(to)}`);
    state.cards = result.cards || [];
    if (!state.selectedCard || !state.cards.some(card => card.date === state.selectedCard)) {
      state.selectedCard = state.cards[0]?.date || '';
    }
    renderCards();
  } catch (error) {
    toast(error.message);
  }
}

function renderCards() {
  const list = $('#cardList');
  if (!state.cards.length) {
    list.innerHTML = '<p class="hint" style="padding:10px;">所选范围没有安排，所以不生成卡片。</p>';
    $('#cardPreview').innerHTML = '<p class="hint">没有卡片。</p>';
    return;
  }
  list.innerHTML = state.cards.map(card => `<div class="card-date ${card.date === state.selectedCard ? 'active' : ''}" data-card="${esc(card.date)}"><span><b>${esc(monthDay(card.date))}</b> ${esc(card.weekday)}</span><span class="hint">${card.items.length} 项</span></div>`).join('');
  $$('#cardList .card-date').forEach(item => item.addEventListener('click', () => {
    state.selectedCard = item.dataset.card;
    renderCards();
  }));
  $('#cardPreview').innerHTML = state.selectedCard
    ? `<img src="/api/cards/${state.selectedCard}.png?v=${Date.now()}" alt="${esc(state.selectedCard)} 卡片" />`
    : '<p class="hint">选择日期查看卡片。</p>';
}

/* ---------- versions ---------- */

async function loadVersions() {
  const kind = $('#versionKind')?.value || 'basic';
  try {
    const result = await api(`/api/documents/${kind}/versions`);
    state.versions = result.versions || [];
    renderVersions();
  } catch (error) {
    toast(error.message);
  }
}

function renderVersions() {
  if (!state.versions.length) {
    $('#versionList').innerHTML = '<p class="hint" style="padding:10px;">暂无版本。</p>';
    return;
  }
  $('#versionList').innerHTML = state.versions.map(version => `<div class="version-item ${version.version === state.selectedVersion ? 'active' : ''}" data-version="${version.version}"><b>v${version.version}</b> · ${esc(version.reason || '')}<br><span class="hint">${esc(version.created_at || version.createdAt || '')}</span></div>`).join('');
  $$('#versionList .version-item').forEach(item => item.addEventListener('click', () => viewVersion(Number(item.dataset.version))));
}

async function viewVersion(versionNumber) {
  const kind = $('#versionKind').value;
  try {
    const snapshot = await api(`/api/documents/${kind}/versions/${versionNumber}`);
    state.selectedVersion = versionNumber;
    renderVersions();
    if (kind === 'basic') {
      const fields = snapshot.fields || [];
      const rows = snapshot.rows || [];
      $('#versionDetail').innerHTML = `<div class="panel-head" style="padding:0 0 10px;border:0;"><h2>版本 v${versionNumber}</h2><span class="hint">${esc(snapshot.reason || '')}</span></div>
        <table class="snapshot-table"><thead><tr>${fields.map(field => `<th>${esc(field.label)}</th>`).join('')}</tr></thead><tbody>${rows.slice(0, 80).map(row => `<tr data-version-row="${esc(fields.map(field => row[field.key] || '').join(' '))}">${fields.map(field => `<td>${esc(row[field.key] || '')}</td>`).join('')}</tr>`).join('')}</tbody></table>
        <div class="card-actions"><button class="ghost small" id="compareVersion" type="button">与当前对比</button><button class="primary small" id="restoreVersion" type="button">恢复到这个版本</button></div>`;
    } else {
      const items = snapshot.rows || [];
      $('#versionDetail').innerHTML = `<div class="panel-head" style="padding:0 0 10px;border:0;"><h2>版本 v${versionNumber}</h2><span class="hint">${esc(snapshot.reason || '')}</span></div>
        <table class="snapshot-table"><thead><tr><th>日期</th><th>时段</th><th>内容</th></tr></thead><tbody>${items.slice(0, 100).map(item => `<tr data-version-row="${esc(item.date + ' ' + item.slot + ' ' + item.text)}"><td>${esc(item.date)}</td><td>${esc(item.slot)}</td><td>${esc(item.text)}</td></tr>`).join('')}</tbody></table>
        <div class="card-actions"><button class="ghost small" id="compareVersion" type="button">与当前对比</button><button class="primary small" id="restoreVersion" type="button">恢复到这个版本</button></div>`;
    }
    const filter = $('#versionSearch');
    filter.value = '';
    filter.addEventListener('input', () => {
      const query = filter.value.trim().toLowerCase();
      $$('#versionDetail [data-version-row]').forEach(row => {
        row.classList.toggle('hidden', Boolean(query) && !row.dataset.versionRow.toLowerCase().includes(query));
      });
    });
    $('#compareVersion')?.addEventListener('click', () => compareVersion(kind, snapshot));
    $('#restoreVersion')?.addEventListener('click', async () => {
      if (!window.confirm(`恢复到 v${versionNumber}？会生成一个新版本。`)) return;
      try {
        const doc = await api(`/api/documents/${kind}/versions/${versionNumber}/restore`, {method: 'POST'});
        if (kind === 'basic') state.basic = doc; else state.schedule = doc;
        state.dirty = false;
        setSaveState('已保存', 'green');
        renderAll();
        toast(`已恢复到 v${versionNumber}`);
      } catch (error) {
        toast(error.message);
      }
    });
  } catch (error) {
    toast(error.message);
  }
}

function compareVersion(kind, snapshot) {
  const current = kind === 'basic' ? state.basic : state.schedule;
  const lines = [];
  if (kind === 'basic') {
    const oldRows = new Map((snapshot.rows || []).map(row => [row.id, row]));
    const newRows = new Map((current.rows || []).map(row => [row.id, row]));
    const oldLabels = (snapshot.fields || []).map(field => field.label);
    const newLabels = (current.fields || []).map(field => field.label);
    if (JSON.stringify(oldLabels) !== JSON.stringify(newLabels)) lines.push(`字段变化：${oldLabels.length} → ${newLabels.length}`);
    let added = 0, removed = 0, changed = 0;
    for (const id of newRows.keys()) if (!oldRows.has(id)) added++;
    for (const id of oldRows.keys()) if (!newRows.has(id)) removed++;
    for (const [id, row] of newRows) {
      const old = oldRows.get(id);
      if (!old) continue;
      if (Object.keys(row).some(key => key !== 'id' && String(row[key] || '') !== String(old[key] || ''))) changed++;
    }
    lines.push(`行变化：新增 ${added}，删除 ${removed}，修改 ${changed}`);
  } else {
    const oldItems = new Set((snapshot.rows || []).map(item => `${item.date}|${item.slot}|${item.text}`));
    const newItems = new Set((current.rows || []).map(item => `${item.date}|${item.slot}|${item.text}`));
    let added = 0, removed = 0;
    for (const item of newItems) if (!oldItems.has(item)) added++;
    for (const item of oldItems) if (!newItems.has(item)) removed++;
    lines.push(`行程变化：新增 ${added}，删除 ${removed}`);
  }
  openModal(`版本 v${snapshot.version ?? ''} 对比当前`, `<ul class="diff-list">${lines.map(line => `<li>${esc(line)}</li>`).join('')}</ul>`, [{label: '关闭', className: 'ghost small', action: closeModal}]);
}

/* ---------- settings ---------- */


async function loadEmailSettings() {
  try {
    const settings = await api('/api/email/settings');
    $('#emailRecipients').value = (settings.recipients || []).join(', ');
    $('#emailTime').value = settings.sendTime || '07:30';
    $('#emailIncludeContact').checked = settings.includeContact !== false;
    $('#emailEnabled').checked = Boolean(settings.enabled);
    $('#smtpHost').value = settings.smtpHost || '';
    $('#smtpPort').value = settings.smtpPort || '465';
    $('#smtpUser').value = settings.smtpUser || '';
    $('#smtpFrom').value = settings.smtpFrom || '';
    $('#smtpSsl').checked = settings.smtpSsl !== false;
    $('#smtpPass').value = '';
    $('#smtpPass').placeholder = settings.smtpPassSet ? '已保存，留空不修改' : 'QQ邮箱授权码';
    const logs = await api('/api/email/logs');
    $('#emailLogs').innerHTML = (logs.logs || []).slice(0, 12).map(log => `<div>${esc(log.date)} · ${esc(log.recipient)} · ${esc(log.status)} ${log.error ? `· ${esc(log.error)}` : ''}</div>`).join('') || '<p class="hint">还没有发送记录。</p>';
    if (state.status) {
      const configured = Boolean(settings.smtpHost) || state.status.smtpConfigured;
      $('#smtpState').textContent = configured ? 'SMTP 已配置' : '未配置 SMTP（预览模式）';
      $('#smtpState').className = `pill ${configured ? 'green' : 'amber'}`;
    }
  } catch (error) {
    toast(error.message);
  }
}

async function saveEmailSettings() {
  try {
    const payload = {
      recipients: $('#emailRecipients').value,
      sendTime: $('#emailTime').value || '07:30',
      includeContact: $('#emailIncludeContact').checked,
      enabled: $('#emailEnabled').checked,
      smtpHost: $('#smtpHost').value,
      smtpPort: $('#smtpPort').value || '465',
      smtpUser: $('#smtpUser').value,
      smtpFrom: $('#smtpFrom').value,
      smtpSsl: $('#smtpSsl').checked,
    };
    const password = $('#smtpPass').value.trim();
    if (password) payload.smtpPass = password;
    const settings = await api('/api/email/settings', {method: 'PATCH', body: payload});
    $('#emailRecipients').value = (settings.recipients || []).join(', ');
    toast('邮件设置已保存');
    await loadEmailSettings();
  } catch (error) {
    toast(error.message);
  }
}

async function testEmail() {
  try {
    await saveEmailSettings();
    const recipient = $('#emailRecipients').value.split(/[,，;；\s]+/).filter(Boolean)[0] || '';
    const result = await api('/api/email/test', {method: 'POST', body: {recipient}});
    toast(`测试邮件已发送到 ${result.recipient}`);
  } catch (error) {
    toast(error.message);
  }
}

async function previewEmail() {
  try {
    const day = state.selectedCard || today();
    const result = await api(`/api/email/preview?day=${encodeURIComponent(day)}`);
    openModal(`邮件预览 · ${result.subject}`, `<iframe style="width:100%;height:60vh;border:1px solid var(--line);border-radius:8px;background:#fff;" srcdoc="${esc(result.html)}"></iframe>`, [
      {label: '关闭', className: 'ghost small', action: closeModal},
    ]);
  } catch (error) {
    toast(error.message);
  }
}

async function sendEmailNow() {
  try {
    await saveEmailSettings();
    const day = state.selectedCard || today();
    const result = await api('/api/email/send', {method: 'POST', body: {date: day}});
    if (result.mode === 'preview') {
      openModal('邮件已生成预览', `<p>${esc(result.message)}</p><p class="hint">${esc(result.files?.dir || '')}</p>`, [{label: '关闭', className: 'ghost small', action: closeModal}]);
    } else {
      toast(result.mode === 'empty' ? result.message : '发送请求已完成');
    }
    loadEmailSettings();
  } catch (error) {
    toast(error.message);
  }
}

/* ---------- modal ---------- */

function openModal(title, html, actions = [], wide = false) {
  $('#modalTitle').textContent = title;
  document.querySelector('.modal-card').classList.toggle('modal-wide', wide);
  $('#modalBody').innerHTML = html;
  $('#modalActions').innerHTML = '';
  actions.forEach(action => {
    const button = document.createElement('button');
    button.className = action.className || 'ghost small';
    button.textContent = action.label;
    button.addEventListener('click', action.action);
    $('#modalActions').appendChild(button);
  });
  $('#modal').classList.remove('hidden');
}
function closeModal() {
  $('#modal').classList.add('hidden');
}

/* ---------- import ---------- */

function openImport() {
  openModal('导入 Excel', `
    <div class="field-label">文件<input id="importFile" type="file" accept=".xlsx" /></div>
    <div class="two-col">
      <label class="field-label">导入到
        <select id="importKind"><option value="basic">基本信息表</option><option value="schedule">行程安排</option></select>
      </label>
      <label class="field-label">模式
        <select id="importMode">
          <option value="header">1. 表头导入</option>
          <option value="data">2. 整表导入</option>
          <option value="full" selected>3. 整张表含表头导入</option>
        </select>
      </label>
    </div>
    <div class="two-col">
      <label class="field-label">表头行<input id="importHeaderRow" type="number" value="1" min="1" /></label>
      <label class="field-label">数据起始行<input id="importDataStart" type="number" value="1" min="1" /></label>
      <label class="field-label">重复策略
        <select id="importStrategy"><option value="update">更新已有行</option><option value="skip">跳过重复</option><option value="append">追加为新行</option></select>
      </label>
    </div>
    <div id="importPreview" class="hint">选择文件后先预览。</div>
  `, [
    {label: '关闭', className: 'ghost small', action: closeModal},
    {label: '预览', className: 'ghost small', action: previewImport},
    {label: '确认导入', className: 'primary small', action: commitImport},
  ]);
  $('#importKind').addEventListener('change', () => {
    const schedule = $('#importKind').value === 'schedule';
    $('#importMode').disabled = schedule;
    if (schedule) $('#importPreview').textContent = '行程安排按日期与时段识别，提交时可选替换或合并。';
  });
  $('#importFile').addEventListener('change', () => {
    const file = $('#importFile').files[0];
    if (file) $('#importPreview').textContent = `已选择 ${file.name}，点「预览」查看差异。`;
  });
}

let pendingImport = null;

async function previewImport() {
  const file = $('#importFile').files[0];
  if (!file) return toast('请选择 .xlsx 文件');
  try {
    const bytes = await fileToBase64(file);
    const upload = await api('/api/import/upload', {method: 'POST', body: {name: file.name, bytes}});
    pendingImport = upload;
    const kind = $('#importKind').value;
    const options = {
      mode: $('#importMode').value,
      headerRow: Number($('#importHeaderRow').value || 1),
      dataStartRow: Number($('#importDataStart').value || 1),
    };
    const preview = await api('/api/import/preview', {method: 'POST', body: {importId: upload.importId, kind, options}});
    pendingImport.preview = preview;
    pendingImport.kind = kind;
    pendingImport.options = options;
    if (kind === 'schedule') {
      $('#importPreview').innerHTML = `<p>识别到 <b>${preview.count}</b> 条行程，起始 ${esc(preview.meta.startDate)}。</p>
        <label class="check"><input id="importReplace" type="checkbox" /> 替换窗口内现有行程（不勾选则追加）</label>
        <p class="hint">样例：</p><ul class="diff-list">${preview.sample.slice(0, 8).map(item => `<li>${esc(item.date)} ${esc(item.slot)} · ${esc(item.text)}</li>`).join('')}</ul>`;
    } else {
      const schema = preview.schema;
      $('#importPreview').innerHTML = `<div class="chip-row">${(schema.added || []).map(item => `<span class="chip add">新增 ${esc(item.label)}</span>`).join('')}${(schema.aliases || []).map(item => `<span class="chip">${esc(item.label)} → ${esc(item.key)}</span>`).join('')}${(schema.missing || []).map(item => `<span class="chip warn">缺 ${esc(item.label)}</span>`).join('')}</div>
        <p>数据行 ${preview.rows.total}，可导入 ${preview.rows.valid}，跳过 ${preview.rows.invalid}；新增 ${preview.rows.counts.new}，更新 ${preview.rows.counts.update}，重复 ${preview.rows.counts.duplicate}。</p>
        ${preview.warnings?.length ? `<p class="hint">${preview.warnings.slice(0, 4).map(esc).join('；')}</p>` : ''}
        <table class="preview-table"><thead><tr><th>状态</th>${preview.headers.slice(0, 8).map(header => `<th>${esc(header)}</th>`).join('')}</tr></thead><tbody>${preview.rows.sample.slice(0, 8).map(row => `<tr><td>${esc(row.status)}</td>${Object.values(row.values || {}).slice(0, 8).map(value => `<td>${esc(value)}</td>`).join('')}</tr>`).join('')}</tbody></table>`;
    }
  } catch (error) {
    toast(error.message);
  }
}

async function commitImport() {
  if (!pendingImport?.importId) return toast('请先预览');
  try {
    const kind = $('#importKind').value;
    const options = {
      mode: $('#importMode').value,
      headerRow: Number($('#importHeaderRow').value || 1),
      dataStartRow: Number($('#importDataStart').value || 1),
      strategy: $('#importStrategy').value,
      replace: kind === 'schedule' ? Boolean($('#importReplace')?.checked) : false,
    };
    const result = await api('/api/import/commit', {method: 'POST', body: {importId: pendingImport.importId, kind, options}});
    closeModal();
    state.dirty = false;
    setSaveState('已保存', 'green');
    await loadAll();
    toast(kind === 'schedule' ? `导入 ${result.count} 条行程` : `导入完成：新增 ${result.stats.added}，更新 ${result.stats.updated}，跳过 ${result.stats.skipped}`);
  } catch (error) {
    toast(error.message);
  }
}

/* ---------- voice / OCR ---------- */

function openVoice() {
  openModal('语音录入', `
    <p class="hint">录音后在本机转写，再交给助手抽取并确认写入。</p>
    <div class="card-actions"><button id="recordBtn" class="primary small" type="button">开始录音</button><span id="recordState" class="hint">未开始</span></div>
    <label class="field-label" style="margin-top:12px;">转写文字<textarea id="voiceText" placeholder="也可以直接粘贴/输入文字"></textarea></label>
    <div class="card-actions"><button id="voiceToCell" class="ghost small" type="button">填入当前单元格</button><button id="voiceToAgent" class="primary small" type="button">交给助手写入</button></div>
  `, [{label: '关闭', className: 'ghost small', action: closeModal}]);
  let recorder = null;
  let chunks = [];
  $('#recordBtn').addEventListener('click', async () => {
    if (recorder && recorder.state === 'recording') {
      recorder.stop();
      return;
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({audio: true});
      recorder = new MediaRecorder(stream);
      chunks = [];
      recorder.ondataavailable = event => chunks.push(event.data);
      recorder.onstop = async () => {
        stream.getTracks().forEach(track => track.stop());
        $('#recordState').textContent = '识别中…';
        const blob = new Blob(chunks, {type: recorder.mimeType || 'audio/webm'});
        const buffer = await blob.arrayBuffer();
        const bytes = new Uint8Array(buffer);
        let binary = '';
        for (let i = 0; i < bytes.length; i += 8192) binary += String.fromCharCode(...bytes.subarray(i, i + 8192));
        const base64 = btoa(binary);
        try {
          const result = await api('/api/ai/transcribe', {method: 'POST', body: {bytes: base64, kind: 'audio'}});
          $('#voiceText').value = result.text || '';
          $('#recordState').textContent = '转写完成';
        } catch (error) {
          $('#recordState').textContent = '转写失败，可手动输入';
          toast(error.message);
        }
      };
      recorder.start();
      $('#recordBtn').textContent = '停止录音';
      $('#recordState').textContent = '录音中…';
    } catch (error) {
      toast('无法使用麦克风：' + error.message);
    }
  });
  if (!state.activeCell) $('#voiceToCell').classList.add('hidden');
  $('#voiceToCell').addEventListener('click', () => {
    const text = $('#voiceText').value.trim();
    if (!text) return toast('没有文字');
    if (fillActiveCell(text)) closeModal();
  });
  $('#voiceToAgent').addEventListener('click', () => {
    const text = $('#voiceText').value.trim();
    if (!text) return toast('没有文字');
    closeModal();
    startAgent('voice_row', {text});
  });
}

function openOcr() {
  openModal('名片 / 图片识别', `
    <p class="hint">选择名片图片，本机 OCR 后由助手确认入库。</p>
    <input id="ocrFile" type="file" accept="image/*" capture="environment" />
    <div id="ocrPreview" class="hint" style="margin-top:10px;">选择图片后开始识别。</div>
    <div class="card-actions"><button id="ocrToCell" class="ghost small" type="button">填入当前单元格</button><button id="ocrToAgent" class="primary small" type="button">交给助手入库</button></div>
  `, [{label: '关闭', className: 'ghost small', action: closeModal}]);
  let bytes = '';
  let ocrText = '';
  if (!state.activeCell) $('#ocrToCell').classList.add('hidden');
  $('#ocrFile').addEventListener('change', async () => {
    const file = $('#ocrFile').files[0];
    if (!file) return;
    bytes = await fileToBase64(file);
    $('#ocrPreview').textContent = '识别中…';
    try {
      const result = await api('/api/ai/ocr', {method: 'POST', body: {bytes}});
      ocrText = result.text || '';
      $('#ocrPreview').innerHTML = `<p>${esc(result.contact?.name || '未读到姓名')} · ${esc(result.contact?.phone || '')}</p><pre style="white-space:pre-wrap;font-size:11px;color:var(--muted);">${esc(result.text)}</pre>`;
    } catch (error) {
      $('#ocrPreview').textContent = error.message + '；可直接手动输入。';
    }
  });
  $('#ocrToCell').addEventListener('click', () => {
    if (!ocrText) return toast('还没有识别结果');
    if (fillActiveCell(ocrText)) closeModal();
  });
  $('#ocrToAgent').addEventListener('click', () => {
    if (!bytes) return toast('请选择图片');
    closeModal();
    startAgent('ocr_row', {bytes});
  });
}

/* ---------- agent ---------- */

function renderAgentPlan(plan) {
  if (!plan) return;
  const glyph = {pending: '○', active: '◐', done: '✓', failed: '×'};
  $('#agentPlan').innerHTML = plan.map(item => `<div class="plan-item ${esc(item.status)}"><span class="dot">${glyph[item.status] || '○'}</span><span class="label">${esc(item.label)}${item.note ? `<span class="note">${esc(item.note)}</span>` : ''}</span></div>`).join('');
}

function updateAgentPlan(event) {
  const plan = state.agentPlan || [];
  const item = plan.find(entry => entry.key === event.key);
  if (item) {
    item.status = event.status;
    if (event.note) item.note = event.note;
    renderAgentPlan(plan);
  }
}

function appendTrace(type, text, tone = '') {
  const el = document.createElement('div');
  el.className = `line ${tone}`;
  el.textContent = text;
  $('#agentTrace').appendChild(el);
  $('#agentTrace').scrollTop = $('#agentTrace').scrollHeight;
}

function renderAgentQuestion(question) {
  state.agentQuestion = question;
  const options = (question.options || []).map(option => `<button class="option" type="button" data-agent-value="${esc(option.value)}"><b>${esc(option.label)}</b>${option.description ? `<span class="hint">${esc(option.description)}</span>` : ''}</button>`).join('');
  $('#agentQuestion').classList.remove('hidden');
  $('#agentQuestion').innerHTML = `<h3>${esc(question.title)}</h3><p>${esc(question.detail || '')}</p>${options}${question.allowText ? `<input id="agentText" placeholder="${esc(question.placeholder || '输入回答')}" />` : ''}`;
  $$('#agentQuestion [data-agent-value]').forEach(button => button.addEventListener('click', () => answerAgent(button.dataset.agentValue, '')));
  if (question.allowText) {
    const input = $('#agentText');
    input.addEventListener('keydown', event => {
      if (event.key === 'Enter') answerAgent('', input.value.trim());
    });
    input.focus();
  }
}

async function answerAgent(value, text) {
  if (!state.agentRunId || !state.agentQuestion) return;
  const question = state.agentQuestion;
  state.agentQuestion = null;
  $('#agentQuestion').classList.add('hidden');
  try {
    await api('/api/agent/answer', {method: 'POST', body: {runId: state.agentRunId, questionId: question.id, value, text}});
  } catch (error) {
    toast(error.message);
  }
}

function startAgent(task, input = {}) {
  if (state.agentStream) state.agentStream.close();
  $('#agentTrace').innerHTML = '';
  $('#agentSummary').classList.add('hidden');
  $('#agentQuestion').classList.add('hidden');
  $('#agentPlan').innerHTML = '<p class="hint">启动中…</p>';
  api('/api/agent/run', {method: 'POST', body: {task, input}}).then(result => {
    state.agentRunId = result.runId;
    appendTrace('→', `启动任务：${task}`);
    const stream = new EventSource(`/api/agent/runs/${result.runId}/events?since=0`);
    state.agentStream = stream;
    stream.onmessage = event => {
      let data;
      try { data = JSON.parse(event.data); } catch { return; }
      if (data.type === 'plan') {
        state.agentPlan = data.plan;
        renderAgentPlan(data.plan);
      } else if (data.type === 'plan_step') {
        updateAgentPlan(data);
      } else if (data.type === 'tool_start') {
        appendTrace('·', `调用 ${data.tool}…`);
      } else if (data.type === 'tool_end') {
        appendTrace(data.status === 'ok' ? '✓' : '×', `${data.tool} · ${data.summary || ''} ${data.durationMs ? `(${data.durationMs}ms)` : ''}`, data.status === 'ok' ? 'ok' : 'error');
      } else if (data.type === 'question') {
        appendTrace('?', `等待回答：${data.question.title}`, 'warn');
        renderAgentQuestion(data.question);
      } else if (data.type === 'answer') {
        appendTrace('←', `用户：${data.summary}`, 'ok');
      } else if (data.type === 'done') {
        stream.close();
        state.agentStream = null;
        renderAgentSummary(data.summary, false);
        refreshAllAfterAgent();
      } else if (data.type === 'error') {
        stream.close();
        state.agentStream = null;
        renderAgentSummary({error: data.message}, true);
      }
    };
    stream.onerror = () => {
      if (stream.readyState === EventSource.CLOSED) state.agentStream = null;
    };
  }).catch(error => {
    toast(error.message);
    $('#agentPlan').innerHTML = '';
  });
}

function renderAgentSummary(summary, error) {
  const el = $('#agentSummary');
  el.classList.remove('hidden');
  el.className = `agent-summary${error ? ' error' : ''}`;
  if (error) {
    el.innerHTML = `<b>任务失败</b><p>${esc(summary.error || '')}</p>`;
    return;
  }
  if (summary.cancelled) {
    el.innerHTML = `<b>已取消</b><p>${esc(summary.message || '没有写回')}</p>`;
    return;
  }
  if (summary.error) {
    el.innerHTML = `<b>未完成</b><p>${esc(summary.error)}</p>`;
    return;
  }
  const parts = [];
  if (summary.count != null) parts.push(`生成 ${summary.count} 条`);
  if (summary.pending?.length) parts.push(`${summary.pending.length} 条待定时段`);
  if (summary.startDate) parts.push(`起始 ${summary.startDate}`);
  if (summary.from && summary.to) parts.push(`${summary.from.date} ${summary.from.slot} → ${summary.to.date} ${summary.to.slot}`);
  if (summary.text) parts.push(summary.text);
  if (summary.row?.person) parts.push(`已写入 ${summary.row.person}`);
  if (summary.send) parts.push(`发送：${summary.send.mode || ''}`);
  if (summary.subject) parts.push(summary.subject);
  el.innerHTML = `<b>已交付</b><p>${esc(parts.join(' · ') || '完成')}</p>`;
}

async function refreshAllAfterAgent() {
  try {
    [state.basic, state.schedule] = await Promise.all([api('/api/documents/basic'), api('/api/documents/schedule')]);
    state.dirty = false;
    state.dirtyKinds.clear();
    state.scheduleSelection.clear();
    state.lastScheduleCell = null;
    setSaveState('已保存', 'green');
    fillBasicFieldFilter();
    renderAll();
    loadCards();
  } catch (error) {
    toast(error.message);
  }
}

/* ---------- bind ---------- */

function bind() {
  $('#authTabLogin').addEventListener('click', () => setAuthMode('login'));
  $('#authTabRegister').addEventListener('click', () => setAuthMode('register'));
  $('#authSubmit').addEventListener('click', submitAuth);
  $('#authPassword').addEventListener('keydown', event => { if (event.key === 'Enter') submitAuth(); });
  $('#authPassword2').addEventListener('keydown', event => { if (event.key === 'Enter') submitAuth(); });
  $('#logoutBtn').addEventListener('click', logout);
  $$('#views button').forEach(button => button.addEventListener('click', () => switchView(button.dataset.view)));
  $('#basicGrid').addEventListener('focusin', event => {
    const cell = event.target.closest('.cell');
    if (!cell) return;
    const td = cell.closest('td');
    if (td?.dataset.row) state.activeCell = {rowId: td.dataset.row, field: td.dataset.field};
  });
  $('#basicGrid').addEventListener('input', event => {
    const cell = event.target.closest('.cell');
    if (!cell) return;
    const td = cell.closest('td');
    updateCell(td.dataset.row, td.dataset.field, cell.innerText);
  });
  $('#basicGrid').addEventListener('click', event => {
    const button = event.target.closest('[data-delete-row]');
    if (button) deleteRow(button.dataset.deleteRow);
  });
  $('#addRowBtn').addEventListener('click', addRow);
  $('#addFieldBtn').addEventListener('click', openAddField);
  $('#manageFieldsBtn').addEventListener('click', openManageFields);
  $('#basicSearch').addEventListener('input', scheduleSearch);
  $('#basicFieldFilter').addEventListener('change', scheduleSearch);
  $('#basicPrev').addEventListener('click', () => moveBasicHit(-1));
  $('#basicNext').addEventListener('click', () => moveBasicHit(1));
  $('#basicFilter').addEventListener('change', renderBasic);
  $('#basicSaveBtn').addEventListener('click', () => saveCurrent(true));

  $('#scheduleGrid').addEventListener('input', event => {
    const cell = event.target.closest('.cell');
    if (!cell) return;
    const td = cell.closest('td');
    updateScheduleCell(td.dataset.date, td.dataset.slot, cell.innerText);
  });
  $('#scheduleGrid').addEventListener('click', event => {
    const td = event.target.closest('#scheduleGrid td[data-date]');
    if (!td) return;
    const date = td.dataset.date;
    const slot = td.dataset.slot;
    if (state.scheduleSelectMode || event.ctrlKey || event.metaKey) {
      event.preventDefault();
      toggleScheduleCell(date, slot);
      return;
    }
    state.lastScheduleCell = {date, slot};
    if (state.scheduleSelection.size) {
      state.scheduleSelection.clear();
      $$('#scheduleGrid td.selected').forEach(cell => cell.classList.remove('selected'));
      $('#clearCellsBtn').title = '清除选中格子的行程内容';
    }
  });
  $('#buildScheduleBtn').addEventListener('click', buildSchedule);
  $('#multiSelectBtn').addEventListener('click', () => {
    state.scheduleSelectMode = !state.scheduleSelectMode;
    renderSchedule();
  });
  $('#clearCellsBtn').addEventListener('click', clearSelectedSchedule);
  $('#scheduleSaveBtn').addEventListener('click', () => saveCurrent(true));
  $('#weekThis').addEventListener('click', () => setScheduleWeek('this'));
  $('#weekNext').addEventListener('click', () => setScheduleWeek('next'));
  $('#weekPrev').addEventListener('click', () => { if (state.scheduleWeek > 0) { state.scheduleWeek -= 1; renderSchedule(); } });
  $('#weekAfter').addEventListener('click', () => { if (state.scheduleWeek < Number(state.schedule.meta.weeks || 2) - 1) { state.scheduleWeek += 1; renderSchedule(); } });
  $('#scheduleStart').addEventListener('change', () => {
    state.schedule.meta.startDate = $('#scheduleStart').value;
    state.scheduleWeek = 0;
    markDirty('schedule');
    renderSchedule();
  });
  $('#scheduleSearch').addEventListener('input', scheduleSearchNow);
  $('#schedulePrev').addEventListener('click', () => moveScheduleHit(-1));
  $('#scheduleNext').addEventListener('click', () => moveScheduleHit(1));

  $('#loadCardsBtn').addEventListener('click', loadCards);
  $('#downloadCardBtn').addEventListener('click', () => {
    if (state.selectedCard) window.location.href = `/api/cards/${state.selectedCard}.png`;
  });
  $('#versionKind').addEventListener('change', loadVersions);
  $('#refreshVersions').addEventListener('click', loadVersions);

  $('#saveEmailSettings').addEventListener('click', saveEmailSettings);
  $('#previewEmailBtn').addEventListener('click', previewEmail);
  $('#sendEmailBtn').addEventListener('click', sendEmailNow);
  $('#testEmailBtn').addEventListener('click', testEmail);

  $('#demoBtn').addEventListener('click', async () => {
    if (!window.confirm('载入示例会替换当前两份文档，继续？')) return;
    try { await api('/api/demo/load', {method: 'POST'}); await loadAll(); toast('示例已载入'); } catch (error) { toast(error.message); }
  });
  $('#voiceBtn').addEventListener('click', openVoice);
  $('#ocrBtn').addEventListener('click', openOcr);
  $('#importBtn').addEventListener('click', openImport);
  $('#downloadBtn').addEventListener('click', () => {
    if (state.view === 'cards' && state.selectedCard) {
      window.location.href = `/api/cards/${state.selectedCard}.png`;
      return;
    }
    const kind = state.view === 'schedule' ? 'schedule' : 'basic';
    window.location.href = `/api/documents/${kind}/export`;
  });
  $('#modalClose').addEventListener('click', closeModal);
  $('#modal').addEventListener('click', event => { if (event.target.id === 'modal') closeModal(); });

  $$('.agent-tasks button').forEach(button => button.addEventListener('click', () => {
    const task = button.dataset.agent;
    if (task === 'build_schedule') startAgent(task, {startDate: state.schedule.meta.startDate, weeks: 2});
    if (task === 'make_cards') startAgent(task, {from: state.schedule.meta.startDate, to: addDays(state.schedule.meta.startDate, 13)});
    if (task === 'daily_brief') startAgent(task, {date: state.selectedCard || today()});
  }));
  $('#agentReschedule').addEventListener('click', () => {
    const instruction = $('#agentInstruction').value.trim();
    if (!instruction) return toast('请输入调格要求');
    startAgent('reschedule', {instruction});
  });
  $('#agentInstruction').addEventListener('keydown', event => {
    if (event.key === 'Enter') $('#agentReschedule').click();
  });
  $('#agentToggle').addEventListener('click', () => {
    const body = $('#agentBody');
    body.classList.toggle('hidden');
    $('#agentToggle').textContent = body.classList.contains('hidden') ? '+' : '−';
  });
  $('#fileInput').addEventListener('change', event => {
    const file = event.target.files[0];
    if (file) toast(`已选择 ${file.name}；请在导入窗口里预览`);
  });
}

async function init() {
  bind();
  setAuthMode('login');
  try {
    const me = await api('/api/auth/me');
    state.user = me.user;
    renderUser();
    hideAuth();
    await loadAll();
    switchView('basic');
    setSaveState('已保存', 'green');
  } catch (error) {
    if (error.status === 401) showAuth();
    else toast(error.message);
  }
}

init();
