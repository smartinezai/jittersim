// Drafts live only in this page. Credentials are never retained in drafts.
const formDrafts = new Map();
let draftSequence = 0;

function draftFields(form) {
  return Array.from(form.querySelectorAll('input, select, textarea, [data-day]'))
    .filter(field => field.hasAttribute('data-day') || !['password', 'file', 'hidden', 'submit', 'button'].includes(field.type));
}

function rememberDraft(form) {
  if (!form || !form.id) return;
  const fields = draftFields(form).map((field, index) => ({
    key: field.id || String(index),
    value: field.value,
    checked: field.checked,
    selected: field.multiple ? Array.from(field.selectedOptions, option => option.value) : null,
    pressed: field.getAttribute('aria-pressed')
  }));
  formDrafts.set(form.id, { sequence: ++draftSequence, fields });
  renderDraftNotice();
}

// Call this after the page itself sets the fields of a form. A draft that stays
// behind puts the old text back at the next refresh.
function clearDraft(id) {
  formDrafts.delete(id);
  renderDraftNotice();
}

function restoreDrafts() {
  formDrafts.forEach((draft, id) => {
    const form = element(id);
    if (!form) return;
    const fields = draftFields(form);
    draft.fields.forEach(saved => {
      const field = fields.find((candidate, index) => (candidate.id || String(index)) === saved.key);
      if (!field) return;
      if (saved.selected) {
        Array.from(field.options).forEach(option => { option.selected = saved.selected.includes(option.value); });
      } else if (field.tagName === 'SELECT' && !Array.from(field.options).some(option => option.value === saved.value)) {
        const missing = new Option(saved.value + ' (no longer available)', saved.value);
        field.add(missing);
        field.value = saved.value;
      } else field.value = saved.value;
      if (saved.checked !== undefined) field.checked = saved.checked;
      if (saved.pressed !== null) field.setAttribute('aria-pressed', saved.pressed);
    });
  });
  renderDraftNotice();
}

function renderDraftNotice() {
  const notice = element('draft-notice');
  if (!notice) return;
  notice.hidden = formDrafts.size === 0;
  element('draft-count').textContent = formDrafts.size + ' unfinished form' + (formDrafts.size === 1 ? '' : 's') +
    '. Apply uses saved settings. Your edits stay here until saved or discarded.';
}

function formError(form, message) {
  if (!form) return;
  let note = form.querySelector('.form-error');
  if (!note) {
    note = document.createElement('p');
    note.className = 'form-error';
    note.setAttribute('role', 'alert');
    note.tabIndex = -1;
    form.append(note);
  }
  note.textContent = message;
  note.focus();
}

function guardDraft(id, continueEditing) {
  if (!formDrafts.has(id)) return false;
  confirmWorkflow('Replace the unfinished edits in this form?').then(confirmed => {
    if (!confirmed) return;
    formDrafts.delete(id); renderDraftNotice(); continueEditing();
  }).catch(error => showMessage(error.message, 'error'));
  return true;
}

async function copyReport(text, label) {
  try {
    await navigator.clipboard.writeText(text);
    showMessage(label + ' copied.', 'success');
  } catch (error) {
    const fallback = element('report-copy-fallback');
    fallback.hidden = false;
    fallback.value = text;
    fallback.focus();
    fallback.select();
    showMessage('Select and copy the text below. Clipboard access is unavailable.', 'success');
  }
}

function duplicateProfile(profile) {
  if (guardDraft('profile-form', () => duplicateProfile(profile))) return;
  stopEditing('profile');
  fillProfileValues(profile);
  let id = profile.id.slice(0, 48) + '-copy';
  let index = 2;
  while (profiles.some(item => item.id === id)) id = profile.id.slice(0, 44) + '-copy-' + index++;
  element('profile-id').value = id;
  element('profile-id').readOnly = false;
  element('profile-name').value = profile.name.slice(0, 95) + ' copy';
  showTab('profiles');
  rememberDraft(element('profile-form'));
  element('profile-name').focus();
}

function installDraftControls() {
  const notice = document.createElement('aside');
  notice.id = 'draft-notice';
  notice.className = 'workflow-notice';
  notice.hidden = true;
  const text = document.createElement('span');
  text.id = 'draft-count';
  const discard = document.createElement('button');
  discard.type = 'button';
  discard.textContent = 'Discard unfinished edits';
  discard.addEventListener('click', async () => {
    if (!await confirmWorkflow('Discard all unfinished form edits? Saved settings will stay unchanged.')) return;
    const forms = Array.from(formDrafts.keys());
    formDrafts.clear();
    forms.forEach(id => element(id).reset());
    if (forms.includes('site-rule-form')) element('site-rule-id').value = '';
    ['profile', 'device', 'schedule', 'group', 'scenario'].forEach(prefix => {
      if (element(prefix + '-id')) stopEditing(prefix);
    });
    renderDraftNotice();
    await refresh();
  });
  notice.append(text, discard);
  noticeAnchor().before(notice);
  const fallback = document.createElement('textarea');
  fallback.id = 'report-copy-fallback';
  fallback.setAttribute('aria-label', 'Report text to copy');
  fallback.readOnly = true;
  fallback.hidden = true;
  tabPanel('measurements').append(fallback);
  document.addEventListener('input', event => {
    if (event.target.type === 'password') return;
    event.target.removeAttribute('aria-invalid');
    const error = event.target.parentElement.querySelector('.field-error');
    if (error) {
      event.target.setAttribute('aria-describedby', (event.target.getAttribute('aria-describedby') || '').split(' ').filter(id => id !== error.id).join(' '));
      error.remove();
    }
    rememberDraft(event.target.closest('form'));
  });
  document.addEventListener('change', event => rememberDraft(event.target.closest('form')));
  document.addEventListener('click', event => {
    if (event.target.closest('[data-day], #scenario-add-step, #scenario-steps button, #mac-add-allowed, #mac-add-blocked')) {
      const form = event.composedPath().find(node => node.tagName === 'FORM');
      rememberDraft(form);
    }
  });
  document.addEventListener('invalid', event => {
    const field = event.target;
    field.setAttribute('aria-invalid', 'true');
    let note = field.parentElement.querySelector('.field-error');
    if (!note) {
      note = document.createElement('small');
      note.className = 'field-error';
      note.id = (field.id || 'field-' + ++draftSequence) + '-error';
      field.after(note);
      field.setAttribute('aria-describedby', [field.getAttribute('aria-describedby'), note.id].filter(Boolean).join(' '));
    }
    note.textContent = field.validationMessage;
  }, true);
  window.addEventListener('beforeunload', event => {
    if (formDrafts.size) { event.preventDefault(); event.returnValue = ''; }
  });
}

installDraftControls();

function textNode(tag, text, className) {
  const node = document.createElement(tag);
  node.textContent = text;
  if (className) node.className = className;
  return node;
}
function actionButton(label, action) {
  const button = textNode('button', label);
  button.type = 'button';
  button.addEventListener('click', async () => {
    setButtonBusy(button, true);
    try { await action(button); } catch (error) { showMessage(error.message, 'error'); }
    finally {
      setButtonBusy(button, false);
      if (button.id === 'test-session-finish') button.disabled = Boolean(activeTestRecord && activeTestRecord.ended_at !== null);
      if (typeof button.afterAction === 'function') button.afterAction();
    }
  });
  return button;
}
function workflowCard(id, title, tab, markup) {
  const card = document.createElement('section');
  card.id = id;
  card.className = 'card wide';
  const heading = textNode('h2', title);
  card.append(heading);
  // Only fixed application markup is passed here. Saved data uses textContent.
  const content = document.createElement('div');
  content.innerHTML = markup;
  card.append(content);
  tabPanel(tab).append(card);
  return card;
}

const failedReads = new Map();
const readTimes = new Map();
let lastSuccessfulRead = null;
function recordRead(path, error) {
  if (error && error.status && error.status < 500) return;
  // While the Pi restarts its Wi-Fi the reconnect banner explains it. No second notice.
  if (error && error.connectionLost && wifiReconnectActive()) return;
  if (error) {
    if (failedReads.size >= 64) failedReads.delete(failedReads.keys().next().value);
    failedReads.set(path, error.message);
  } else {
    failedReads.delete(path); lastSuccessfulRead = new Date();
    if (readTimes.size >= 64) readTimes.delete(readTimes.keys().next().value);
    readTimes.set(path, lastSuccessfulRead);
  }
  const notice = element('connection-notice');
  if (!notice) return;
  notice.hidden = failedReads.size === 0;
  const previous = readTimes.get(failedReads.keys().next().value);
  notice.textContent = failedReads.size
    ? 'Some information is out of date. ' + Array.from(failedReads.values())[0] +
      (previous ? ' Last updated: ' + previous.toLocaleTimeString() + '.' : '') + ' Retrying automatically.'
    : '';
}
function renderSystemHealth(data) {
  const system = data.system || {};
  const values = [
    'Dashboard connected',
    data.helper_configured ? (data.helper_reachable ? 'Network helper connected' : 'Network helper unavailable') : 'Configuration only',
    'Version: ' + (system.version || 'Unknown'),
    system.disk_free_bytes === null || system.disk_free_bytes === undefined ? 'Disk space unavailable' :
      'Free disk: ' + (system.disk_free_bytes / 1073741824).toFixed(1) + ' GB',
    system.temperature_celsius == null ? null : 'Temperature: ' + system.temperature_celsius.toFixed(1) + ' °C',
    system.uptime_seconds == null ? null : 'Pi uptime: ' + Math.floor(system.uptime_seconds / 3600) + ' hours',
    'Checked: ' + new Date().toLocaleTimeString()
  ];
  if (!releaseState && system.version) element('sidebar-version').textContent = 'Version ' + system.version;
  element('health-details').replaceChildren(...values.filter(Boolean).map(value => textNode('p', value)));
  const uplink = textNode('p', element('uplink-state').textContent === 'Unavailable' ? 'Ethernet information unavailable' : 'Ethernet: ' + element('uplink-state').textContent);
  uplink.id = 'health-uplink'; element('health-details').append(uplink);
  if (data.helper_configured && !data.helper_reachable) recordRead('/helper', new Error('The dashboard is reachable but its network helper is unavailable.'));
  else { failedReads.delete('/helper'); recordRead('/api/health', null); }
}

let workspaceDevice = null;
let workspaceRequest = null;
let previousWorkspaceTraffic = null;
function renderDeviceCards() {
  const container = element('device-cards');
  if (!container) return;
  container.replaceChildren();
  devices.forEach(device => {
    const state = Object.prototype.hasOwnProperty.call(deviceConditions.devices, device.id) ? deviceConditions.devices[device.id] : null;
    const card = document.createElement('article');
    card.className = 'device-card';
    const profile = deviceProfileCell(device);
    // The name is the title and the address is small text under it.
    card.append(textNode('h3', deviceDisplayName(device)), textNode('p', device.ipv4_address, 'form-note'),
      textNode('p', profile[0]), textNode('p', profile[1], 'form-note'),
      textNode('p', deviceAccessText(device), 'device-access-status'),
      textNode('p', [].concat(deviceStateCell(device)).join(' · ')),
      actionButton('Open ' + deviceTitle(device), () => openDevice(device.id)));
    container.append(card);
  });
  const others = everyoneElseRow();
  if (others) {
    const card = document.createElement('article'); card.className='device-card';
    card.append(textNode('h3', 'Everyone else'), textNode('p', others.cells[2].join(' · ')),
      actionButton('Edit everyone else', () => element('everyone-else-profile').focus()));
    container.append(card);
  }
  if (workspaceDevice) renderWorkspaceState();
}
function renderWorkspaceState() {
  const device = devices.find(item => item.id === workspaceDevice);
  if (!device) return;
  const state = Object.prototype.hasOwnProperty.call(deviceConditions.devices, device.id) ? deviceConditions.devices[device.id] : null;
  element('workspace-title').textContent = deviceDisplayName(device);
  element('workspace-state').textContent = [].concat(deviceIsPaused(device) ? deviceStateCell(device) : appliedProfileCell(profileName(device.profile_id), state)).join(' · ');
  element('workspace-access').textContent = deviceAccessText(device);
  element('workspace-address').textContent = device.ipv4_address + ' · ' + (device.mac_address || 'No saved MAC') + ' · Saved default: ' + profileName(device.profile_id);
}
async function openDevice(id) {
  if (workspaceDevice !== id && ['workspace-timer-form', 'workspace-scenario-form'].some(key => formDrafts.has(key))) {
    if (!await confirmWorkflow('Discard unfinished controls for the previous device?')) return;
    ['workspace-timer-form', 'workspace-scenario-form'].forEach(key => { formDrafts.delete(key); element(key).reset(); });
    renderDraftNotice();
  }
  workspaceDevice = id;
  previousWorkspaceTraffic = null;
  showTab('devices');
  element('device-workspace').hidden = false;
  populateWorkflowMenus();
  renderWorkspaceState();
  await loadWorkspace();
  const title = element('workspace-title');
  title.style.scrollMarginTop = '16px';
  title.focus({preventScroll:true}); title.scrollIntoView({block:'start'});
}
async function loadWorkspace() {
  if (!workspaceDevice || workspaceRequest || currentPage() !== 'devices') return;
  const id = workspaceDevice;
  workspaceRequest = (async () => {
    const data = await request('/api/device-workspace/' + encodeURIComponent(id), 'GET');
    const traffic = await request('/api/traffic', 'GET');
    if (id !== workspaceDevice) return;
    const list = element('workspace-runs');
    list.replaceChildren();
    data.timers.forEach(timer => list.append(textNode('p', 'Timer: ' + timer.profile_name + ' · ' + formatTimerRemaining(timer.remaining_seconds)),
      actionButton('Stop timer', async button => { await cancelTimer(timer.id, button); await loadConditions(); })));
    data.runs.forEach(run => list.append(textNode('p', 'Scenario: ' + (run.scenario_name || run.scenario_id)),
      actionButton('Stop scenario', async () => { await request('/api/scenarios/stop', 'POST', {id:run.id}); refreshAfterSuccess('Scenario stopped.'); })));
    data.progressions.forEach(run => list.append(textNode('p', 'Progression: ' + run.start_profile_name + ' → ' + run.end_profile_name),
      actionButton('Stop progression', button => stopProgression(run.id, button))));
    if (!list.children.length) list.append(textNode('p', 'No temporary runs.'));
    const usage = element('workspace-usage');
    usage.replaceChildren();
    [...data.data_limits, ...data.group_data_limits, ...data.service_quotas].forEach(limit => usage.append(textNode('p',
      (limit.name || (limit.group_id ? 'Shared data limit' : 'Device data limit')) + ': ' +
      (limit.used_bytes / 1048576).toFixed(1) + ' / ' + (limit.limit_bytes / 1048576).toFixed(1) + ' MB' + (limit.enabled ? '' : ' · Disabled'))));
    data.time_quotas.forEach(limit => usage.append(textNode('p', limit.name + ': ' + Math.floor(limit.used_seconds / 60) + ' / ' + Math.floor(limit.limit_seconds / 60) + ' minutes')));
    if (!usage.children.length) usage.append(textNode('p', 'No usage limits for this device.'));
    const timeline = element('workspace-timeline');
    const entries = data.timeline.changes.map(change => textNode('li', new Date(change.at * 1000).toLocaleString() +
      ' · ' + change.profile + ' · ' + change.reason + (change.overridden_schedules.length ? ' · Takes priority over: ' + change.overridden_schedules.join(', ') : '')));
    timeline.replaceChildren(...entries.slice(0,6));
    element('workspace-timeline-later').replaceChildren(...entries.slice(6));
    element('workspace-later').hidden = entries.length <= 6;
    element('timeline-note').textContent = (deviceConditions.applied ? '' : 'Saved rules preview. These settings are not confirmed active. ') + data.timeline.note + (data.timeline.truncated ? ' Showing the first 32 changes.' : ' Next 24 hours.');
    const counters = traffic.devices && traffic.devices[id];
    const now = traffic.sampled_at;
    let text = 'Waiting for traffic samples.';
    if (counters && previousWorkspaceTraffic && now > previousWorkspaceTraffic.at) {
      const seconds = now - previousWorkspaceTraffic.at;
      const upload = Math.max(0, counters.upload_bytes - previousWorkspaceTraffic.counters.upload_bytes) * 8 / seconds / 1000000;
      const download = Math.max(0, counters.download_bytes - previousWorkspaceTraffic.counters.download_bytes) * 8 / seconds / 1000000;
      text = 'At the Pi: ↑ ' + upload.toFixed(2) + ' Mbit/s · ↓ ' + download.toFixed(2) + ' Mbit/s. Measured before any profile effects.';
    }
    if (counters) previousWorkspaceTraffic = {at:now,counters};
    element('workspace-traffic').textContent = text;
  })();
  try { await workspaceRequest; } finally { workspaceRequest = null; }
}
function populateWorkflowMenus() {
  [['workspace-profile', profiles], ['workspace-scenario', savedScenarios]].forEach(([id, items]) => {
    const select = element(id);
    if (!select) return;
    const chosen = select.value;
    select.replaceChildren(...items.map(item => new Option(item.name, item.id)));
    if (items.some(item => item.id === chosen)) select.value = chosen;
  });
  const target = element('test-session-target');
  const chosen = target.value;
  target.replaceChildren(...scheduleTargetOptions().map(item => new Option(item.label, item.value)));
  if (Array.from(target.options).some(option => option.value === chosen)) target.value = chosen;
}

function installDeviceWorkflows() {
  const notice = textNode('aside', '', 'workflow-notice');
  notice.id = 'connection-notice'; notice.hidden = true; notice.setAttribute('role', 'status');
  noticeAnchor().before(notice);
  workflowCard('system-health', 'Connection and Pi health', 'overview', '<div id="health-details">Checking…</div>');
  const cards = document.createElement('div'); cards.id = 'device-cards';
  element('device-table').after(cards);
  const workspace = workflowCard('device-workspace', 'Device controls', 'devices', `
    <h3 id="workspace-title" tabindex="-1"></h3><p id="workspace-state"></p><p id="workspace-access" class="device-access-status"></p><p id="workspace-address" class="form-note"></p>
    <p id="workspace-traffic"></p><div id="workspace-runs"></div>
    <form id="workspace-timer-form"><label>Temporary profile<select id="workspace-profile"></select></label>
    <label>Minutes<input id="workspace-minutes" type="number" min="1" max="1440" value="10" required></label>
    <button type="submit">Start device timer</button></form>
    <form id="workspace-scenario-form"><label>Scenario to run<select id="workspace-scenario"></select></label>
    <button type="submit">Start device scenario</button></form>
    <h3>Usage</h3><div id="workspace-usage"></div><h3>Upcoming rules</h3><p id="timeline-note"></p><ol id="workspace-timeline"></ol>
    <details id="workspace-later" hidden><summary>Later changes</summary><ol id="workspace-timeline-later" start="7"></ol></details>`);
  workspace.hidden = true;
  element('device-table').closest('section').after(workspace);
  workspace.append(actionButton('Edit device settings', () => { const device=devices.find(item => item.id === workspaceDevice); if (device) { fillDeviceForm(device); element('device-name').focus(); } }));
  workspace.append(actionButton('Delete this device', async () => {
    const device=devices.find(item => item.id === workspaceDevice);
    if (!device || !await confirmWorkflow('Delete ' + deviceTitle(device) + ' and its schedules, temporary runs and limits? A group with only this device is also deleted.')) return;
    await request('/api/devices/delete', 'POST', {id:device.id});
    workspaceDevice=null; workspace.hidden=true; refreshAfterSuccess('Device deleted.');
  }));
  workspace.append(actionButton('Close device controls', () => { workspace.hidden = true; workspaceDevice = null; }));
  element('workspace-timer-form').addEventListener('submit', async event => {
    event.preventDefault();
    try {
      await request('/api/timers', 'POST', {device_id:workspaceDevice, profile_id:element('workspace-profile').value, duration_minutes:Number(element('workspace-minutes').value)});
      refreshAfterSuccess('Device timer started.');
    } catch (error) { showMessage(error.message, 'error'); }
  });
  element('workspace-scenario-form').addEventListener('submit', async event => {
    event.preventDefault();
    try { await request('/api/scenarios/start', 'POST', {scenario_id:element('workspace-scenario').value,device_id:workspaceDevice}); refreshAfterSuccess('Device scenario started.'); }
    catch (error) { showMessage(error.message, 'error'); }
  });
}
installDeviceWorkflows();

let activeTestRecord = null;
let activeTestReport = '';
function downloadText(text, filename, type) {
  const url = URL.createObjectURL(new Blob([text], {type}));
  const link = document.createElement('a'); link.href = url; link.download = filename;
  document.body.append(link); link.click(); link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
async function loadTestSessions() {
  const data = await request('/api/test-sessions', 'GET');
  const select = element('test-session-list');
  const previous = select.value;
  select.replaceChildren(new Option('Choose a test session…', ''), ...data.sessions.map(item => new Option(
    item.name + (item.ended_at == null ? ' · Running' : ' · Finished'), item.id)));
  if (data.sessions.some(item => item.id === previous)) select.value = previous;
  const current = data.sessions.find(item => item.ended_at == null);
  element('test-session-running').textContent = current ? 'Recording: ' + current.name : 'No test session recording.';
  element('test-session-start').disabled = Boolean(current);
  populateWorkflowMenus();
}
async function openTestSession(id) {
  if (!id) return;
  if (formDrafts.has('test-session-edit') && (!activeTestRecord || activeTestRecord.id !== id)) {
    if (!await confirmWorkflow('Discard the unfinished notes for the previous session?')) { element('test-session-list').value = activeTestRecord ? activeTestRecord.id : ''; return; }
    formDrafts.delete('test-session-edit');
  }
  const data = await request('/api/test-sessions/' + id, 'GET');
  activeTestRecord = data.session; activeTestReport = data.report;
  element('test-session-details').hidden = false;
  element('test-session-heading').textContent = data.session.name;
  element('test-session-notes').value = data.session.notes;
  element('test-session-summary').textContent = 'Target: ' + (data.session.target_name || data.session.target) + ' · ' +
    data.session.measurements.length + ' measurements · ' + data.session.events.length +
    ' events' + (data.session.events_truncated ? ' · Event limit reached' : '') + ' · Settings captured when this session started.';
  element('test-session-finish').disabled = data.session.ended_at !== null;
  element('test-session-list').value = id;
  restoreDrafts();
}
async function saveTestSession(finish=false, attach=false) {
  if (!activeTestRecord) throw new Error('Choose a test session.');
  const submittedDraft = formDrafts.get('test-session-edit');
  const result = await request('/api/test-sessions/save', 'POST', {
    id:activeTestRecord.id, revision:activeTestRecord.revision, notes:element('test-session-notes').value,
    finish, ...(attach ? {measurements:measurementRuns} : {})
  });
  activeTestRecord = result.session;
  if (formDrafts.get('test-session-edit') === submittedDraft) formDrafts.delete('test-session-edit');
  await loadTestSessions(); await openTestSession(activeTestRecord.id);
  showMessage(finish ? 'Test session finished.' : 'Test session saved.', 'success');
}
let historyPreview = null;
async function loadSettingsHistory() {
  const data = await request('/api/history', 'GET');
  element('settings-history-warning').textContent = data.warning || '';
  const select = element('settings-versions');
  const previous = select.value;
  select.replaceChildren(...data.versions.map(item => new Option(new Date(item.happened_at * 1000).toLocaleString() + ' · ' + item.label, String(item.id))));
  if (Array.from(select.options).some(item => item.value === previous)) select.value = previous;
}
async function previewSettings() {
  historyPreview = await request('/api/history/preview', 'POST', {id:Number(element('settings-versions').value)});
  historyPreview.id = Number(element('settings-versions').value);
  element('settings-preview').textContent = historyPreview.changed_sections.length
    ? 'Changes: ' + historyPreview.changed_sections.join(', ') + '. ' + historyPreview.message
    : 'These settings match the saved configuration.';
  element('restore-version').disabled = !historyPreview.changed_sections.length;
}
function installRecords() {
  workflowCard('test-sessions', 'Test sessions', 'measurements', `
    <p>Record the settings and network events for a repeatable test. Import measurement files above then attach them here. The newest 50 sessions are retained.</p>
    <p id="test-session-running"></p>
    <form id="test-session-form"><label>Test name<input id="test-session-name" maxlength="120" required></label>
    <label>Test target<select id="test-session-target"></select></label><button id="test-session-start" type="submit">Start test session</button></form>
    <label>Saved test sessions<select id="test-session-list"></select></label>
    <div id="test-session-details" hidden><h3 id="test-session-heading"></h3><p id="test-session-summary"></p>
    <form id="test-session-edit"><label>Test notes<textarea id="test-session-notes" maxlength="8000"></textarea></label><button type="submit">Save test notes</button></form>
    <div id="test-session-actions"></div></div>`);
  element('test-session-form').addEventListener('submit', async event => {
    event.preventDefault();
    try {
      const result = await request('/api/test-sessions/start', 'POST', {name:element('test-session-name').value,target:element('test-session-target').value});
      await loadTestSessions(); await openTestSession(result.session.id);
      showMessage('Test session recording started.', 'success');
    } catch (error) { showMessage(error.message, 'error'); }
  });
  element('test-session-list').addEventListener('change', () => openTestSession(element('test-session-list').value).catch(error => showMessage(error.message,'error')));
  element('test-session-edit').addEventListener('submit', event => { event.preventDefault(); saveTestSession().catch(error => showMessage(error.message,'error')); });
  const actions = element('test-session-actions');
  actions.append(actionButton('Attach displayed measurements', () => saveTestSession(false,true)));
  const finish = actionButton('Finish test session', () => saveTestSession(true)); finish.id = 'test-session-finish'; actions.append(finish);
  actions.append(actionButton('Download test report', async () => {
    await openTestSession(activeTestRecord.id); downloadText(activeTestReport, 'jittersim-test-report.md', 'text/markdown');
  }), actionButton('Copy test report', async () => { await openTestSession(activeTestRecord.id); await copyReport(activeTestReport, 'Test report'); }),
  actionButton('Export repeat settings', () => downloadText(JSON.stringify(activeTestRecord.settings,null,2), 'jittersim-repeat-settings.json', 'application/json')));
  workflowCard('settings-history', 'Restore recent settings', 'diagnostics', `
    <p>The newest 20 settings versions are kept. Restoring stops temporary runs and preserves usage for matching limits and activity. Apply failures keep the restored settings for recovery.</p>
    <p id="settings-history-warning" role="status"></p>
    <label>Settings version<select id="settings-versions"></select></label><div id="settings-history-actions"></div><p id="settings-preview" role="status"></p>`);
  const restore = actionButton('Restore this version', async () => {
    if (!historyPreview || !await confirmWorkflow('Restore the previewed settings and stop temporary runs? Usage for matching limits and activity will stay.')) return;
    const result = await request('/api/history/restore','POST',{id:historyPreview.id,current_version:historyPreview.current_version});
    historyPreview = null; element('restore-version').disabled = true;
    refreshAfterSuccess(savedChangeMessage('Settings restored.',result));
  });
  restore.id='restore-version'; restore.disabled=true;
  element('settings-history-actions').append(actionButton('Preview changes', previewSettings),restore);
  element('settings-versions').addEventListener('change', () => { historyPreview=null; restore.disabled=true; });
}
installRecords();


async function confirmWorkflow(message) {
  const dialog = document.createElement('dialog');
  const text = textNode('p', message); text.id = 'workflow-confirm-text';
  dialog.setAttribute('aria-labelledby', text.id);
  dialog.append(text);
  const answer = await new Promise(resolve => {
    const cancel = actionButton('Cancel', () => { dialog.close(); resolve(false); });
    const proceed = actionButton('Continue', () => { dialog.close(); resolve(true); });
    dialog.append(cancel, proceed);
    dialog.addEventListener('cancel', () => resolve(false), {once:true});
    document.body.append(dialog); dialog.showModal(); cancel.focus();
  });
  dialog.remove();
  return answer;
}

let releaseState = null;
let releasePollAt = 0;
let releaseStatusRequest = null;
let autoCheckStagedUpdates = null;
// An install or restore this page follows until it ends. {label, kind: 'install' | 'restore'}
let updateWatch = null;
let updateFollowing = false;
let updateCheckRunning = false;
let updateOutcome = '';
const RELEASE_BUSY_PHASES = ['queued','stopping','backing-up','installing','checking','rolling-back'];
const PHASE_TEXT = {
  queued: 'The update is queued.',
  stopping: 'Stopping services before the backup.',
  'backing-up': 'Backing up the installation and settings.',
  installing: 'Installing the new release.',
  checking: 'Restarting services and checking that everything works.',
  'rolling-back': 'Restoring the previous installation.'
};
const updateSleep = ms => new Promise(resolve => setTimeout(resolve, ms));
function releaseOperationBusy() {
  return Boolean(releaseState && RELEASE_BUSY_PHASES.includes(releaseState.phase));
}
// Only the Updates page asks the Pi about updates. A hidden page or tab stays quiet.
function updatesPageActive() {
  return !document.hidden && currentPage() === 'updates';
}
function releaseLabel(state) {
  return (state && (state.tag || state.available)) || '';
}
function stagedReleaseReady(state) {
  // Only a release staged with a valid signature of a trusted key can be installed.
  return Boolean(state && state.available && state.available !== state.installed && state.installable === true);
}
// The Updates poll follows an install or restore until it finishes.
function pollReleaseProgress() {
  if (releaseOperationBusy() || updateWatch) followUpdate();
  return Promise.resolve(releaseState);
}
function updateProgressText(failedRead) {
  if (updateOutcome) return updateOutcome;
  if (!updateWatch) return '';
  if (failedRead) return 'The dashboard is restarting. This page reconnects by itself.';
  const phase = releaseState && releaseState.phase;
  return PHASE_TEXT[phase] || 'Working on the ' + updateWatch.kind + '.';
}
// One place decides what every control shows, so a button can never be left enabled while
// a request or an install is running.
function renderUpdates(failedRead) {
  const state = releaseState;
  const busy = Boolean(updateWatch) || releaseOperationBusy();
  const working = busy || updateCheckRunning;
  const ready = stagedReleaseReady(state);
  const installed = (state && state.installed) || null;
  const blocked = state && state.available && state.installable === false && state.reason;
  if (installed) element('sidebar-version').textContent = 'Version ' + installed;
  element('update-installed').textContent = installed || 'Unknown';
  element('update-available').textContent = ready ? releaseLabel(state)
    : (blocked ? releaseLabel(state) + ' (cannot be installed)' : 'No newer release found');
  element('update-signer').textContent = ready && state.signed_by ? state.signed_by : 'Not applicable';
  element('update-last-checked').textContent = onlineView && onlineView.checked_at
    ? new Date(onlineView.checked_at * 1000).toLocaleString() : 'Not checked yet';
  element('release-check').disabled = working;
  element('release-install').disabled = working || !ready;
  element('release-install').classList.toggle('primary', ready && !working);
  element('release-rollback').disabled = working || !(state && state.rollback_available);
  const progress = updateProgressText(failedRead);
  element('update-progress').hidden = !progress;
  element('update-progress').textContent = progress;
  element('sidebar-update-link').hidden = !ready;
  element('sidebar-update-link').textContent = ready ? 'Update available: ' + releaseLabel(state) : '';
  element('update-available-badge').hidden = !ready;
  const nav = element('updates-tab-link');
  if (ready) nav.setAttribute('aria-label', 'Updates, update available');
  else nav.removeAttribute('aria-label');
}
async function loadReleaseStatus({force = false} = {}) {
  if (releaseStatusRequest) return releaseStatusRequest;
  if (!force && Date.now() - releasePollAt < 5000) return releaseState;
  releasePollAt = Date.now();
  if (!updateWatch) element('release-status').textContent = 'Checking for a staged release on this Pi…';
  releaseStatusRequest = (async () => {
    try {
      releaseState = await request('/api/updates', 'GET');
      const signer = releaseState.available && releaseState.signed_by ? ' (signed by ' + releaseState.signed_by + ')' : '';
      const blocked = releaseState.available && releaseState.installable === false && releaseState.reason;
      element('release-status').textContent = 'Installed: ' + (releaseState.installed || 'Unknown') +
        ' · Available: ' + (releaseState.available ? releaseState.available + signer : 'None staged') +
        ' · ' + (blocked ? releaseState.reason : releaseState.message);
      // An install started elsewhere, such as Install automatically, is followed too.
      if (releaseOperationBusy() && !updateWatch) {
        updateWatch = {label: releaseLabel(releaseState), kind: releaseState.phase === 'rolling-back' ? 'restore' : 'install'};
        updateOutcome = '';
      }
      renderUpdates();
      return releaseState;
    } catch (error) {
      if (releaseOperationBusy() || updateWatch) {
        // Services restart during an install, so this page waits for them.
        element('release-status').textContent = 'Waiting for the dashboard to restart. This page checks again shortly.';
        renderUpdates(true);
        throw error;
      }
      element('release-status').textContent = 'Could not check for a staged release. Use Check for updates to try again.';
      releaseState = null;
      renderUpdates();
      throw error;
    } finally {
      releaseStatusRequest = null;
    }
  })();
  return releaseStatusRequest;
}
// Follows an install until the new version answers. A restart makes requests fail for a
// few seconds. That is expected, so the loop keeps going and reconnects by itself.
async function followUpdate() {
  if (updateFollowing || !updateWatch) return;
  updateFollowing = true;
  const started = Date.now();
  try {
    while (updateWatch && Date.now() - started < 15 * 60 * 1000) {
      if (updatesPageActive()) {
        try {
          await loadReleaseStatus({force: true});
          const phase = releaseState.phase;
          if (phase === 'complete' && updateWatch.kind === 'install') {
            updateOutcome = 'Updated to ' + updateWatch.label + '.';
            updateWatch = null;
          } else if (phase === 'rolled-back' && updateWatch.kind === 'restore') {
            updateOutcome = 'The previous installation is back.';
            updateWatch = null;
          } else if (phase === 'complete' || phase === 'rolled-back' || phase === 'failed') {
            updateOutcome = 'The update did not finish. ' + (releaseState.message || '') +
              ' The previous installation was kept or restored.';
            updateWatch = null;
          }
          renderUpdates();
        } catch (error) {
          renderUpdates(true);
        }
      }
      if (updateWatch) await updateSleep(2000);
    }
  } finally {
    if (updateWatch) {
      updateOutcome = 'The dashboard did not come back in time. Reload this page, or check the Pi.';
      updateWatch = null;
      renderUpdates();
    }
    updateFollowing = false;
  }
}
async function loadUpdateCheckPreference() {
  const toggle = element('check-staged-updates-on-open');
  const state = element('update-preference-status');
  try {
    const preference = await request('/api/update-preferences', 'GET');
    if (typeof preference.check_staged_updates_on_open !== 'boolean') {
      throw new Error('The saved update preference is invalid.');
    }
    autoCheckStagedUpdates = preference.check_staged_updates_on_open;
    toggle.checked = autoCheckStagedUpdates;
    toggle.disabled = false;
    state.textContent = autoCheckStagedUpdates
      ? 'Automatic checking is on.'
      : 'Automatic checking is off. Use Check for updates whenever you want.';
    if (autoCheckStagedUpdates) loadReleaseStatus().catch(() => {});
    else if (releaseState === null && !releaseStatusRequest) {
      element('release-status').textContent = 'Automatic checking is off. Use Check for updates to check now.';
    }
  } catch (error) {
    autoCheckStagedUpdates = null;
    toggle.disabled = true;
    state.textContent = 'The saved preference could not be read. Automatic checking did not run. Manual checking is still available.';
    element('release-status').textContent = 'Automatic checking did not run because its saved preference is unavailable.';
  }
}
function installUpdateCheckPreference() {
  const toggle = element('check-staged-updates-on-open');
  const state = element('update-preference-status');
  toggle.addEventListener('change', async () => {
    if (autoCheckStagedUpdates === null) return;
    const previousValue = autoCheckStagedUpdates;
    const nextValue = toggle.checked;
    toggle.disabled = true;
    state.textContent = 'Saving automatic-check preference…';
    try {
      const result = await request('/api/update-preferences', 'POST', {
        check_staged_updates_on_open: nextValue,
      });
      if (!result.saved || result.check_staged_updates_on_open !== nextValue) {
        throw new Error('The saved update preference could not be confirmed.');
      }
      autoCheckStagedUpdates = nextValue;
      state.textContent = nextValue
        ? 'Automatic checking is on. Checking for a staged release now.'
        : 'Automatic checking is off. Use Check for updates whenever you want.';
      if (nextValue) loadReleaseStatus({force: true}).catch(() => {});
    } catch (error) {
      toggle.checked = previousValue;
      state.textContent = 'The preference was not saved. The previous setting is still in place.';
      showMessage(error.message, 'error');
    } finally {
      toggle.disabled = false;
    }
  });
}
// The online check. The Pi fetches the release as root. This page only asks for a check, follows
// its job and saves two settings. It never handles the token.
let onlineView = null;
let onlinePolling = false;
function onlineSummary(view) {
  if (!view || view.state === 'none') return 'No online check has run yet.';
  if (view.state === 'unavailable') return view.message || 'Online checks are not available.';
  if (view.state === 'running') return 'Checking GitHub…';
  const when = view.checked_at ? ' (' + new Date(view.checked_at * 1000).toLocaleString() + ')' : '';
  const outcome = view.result === 'failed' ? 'The check failed: ' : '';
  return outcome + (view.message || 'The check finished.') + when;
}
function showOnline(view) {
  onlineView = view;
  const ready = view && view.state !== 'unavailable';
  let text = onlineSummary(view);
  if (ready && view.token_set === false) {
    text = 'No GitHub token is saved on the Pi yet, so Check for updates only looks at releases already staged on the Pi.';
  }
  element('online-check-status').textContent = text;
  const noToken = Boolean(ready && view.token_set === false);
  if (noToken) element('github-access-hint').textContent = 'To let the Pi look at GitHub, run this on the Pi: sudo jittersim update set-token (see The GitHub token below).';
  element('github-access-hint').hidden = !noToken;
  const settings = (view && view.settings) || null;
  for (const [id, name] of [['online-daily-check', 'daily_check'], ['online-install-automatically', 'install_automatically']]) {
    const box = element(id);
    box.disabled = !settings;
    if (settings) box.checked = settings[name] === true;
  }
  renderUpdates();
}
async function runOnlineCheck({force = false} = {}) {
  if (onlinePolling) return;
  onlinePolling = true;
  try {
    let view = await request('/api/updates/check-online', 'POST', {force});
    showOnline(view);
    const started = Date.now();
    while (view.state === 'running' && Date.now() - started < 8 * 60 * 1000) {
      await updateSleep(2000);
      // A hidden page or tab waits without asking the Pi.
      if (!updatesPageActive()) continue;
      view = await request('/api/updates/check-online/' + encodeURIComponent(view.job), 'GET');
      if (view.state === 'unknown') view = await request('/api/updates/check-online', 'GET');
      showOnline(view);
    }
    if (view.state === 'done' && view.result !== 'failed') await loadReleaseStatus({force: true}).catch(() => {});
  } catch (error) {
    element('online-check-status').textContent = 'The online check could not run: ' + error.message;
  } finally {
    onlinePolling = false;
  }
}
async function loadOnlineStatus() {
  try {
    showOnline(await request('/api/updates/check-online', 'GET'));
  } catch (error) {
    element('online-check-status').textContent = 'Online checks are not available on this dashboard.';
  }
}
// Check for updates: GitHub when the Pi has a token, otherwise only the releases staged on the Pi.
async function checkForUpdates() {
  updateOutcome = '';
  updateCheckRunning = true;
  renderUpdates();
  try {
    if (onlineView && onlineView.state !== 'unavailable' && onlineView.token_set === true) {
      await runOnlineCheck({force: true});
    } else {
      await loadReleaseStatus({force: true});
    }
  } finally {
    updateCheckRunning = false;
    renderUpdates();
  }
}
async function installUpdate() {
  if (!stagedReleaseReady(releaseState)) return;
  const label = releaseLabel(releaseState);
  if (!await confirmWorkflow('Install ' + label + '? The dashboard and the test network will drop for a few seconds while it restarts. Keep Ethernet or SSH available in case you need to recover.')) return;
  updateOutcome = '';
  await request('/api/updates/install', 'POST', {version: releaseState.available, confirm: true});
  updateWatch = {label, kind: 'install'};
  renderUpdates();
  followUpdate();
}
async function restoreUpdate() {
  if (!await confirmWorkflow('Restore the previous installation and its settings backup? Changes made since that backup will be replaced. The dashboard and the test network will drop for a few seconds.')) return;
  updateOutcome = '';
  await request('/api/updates/rollback', 'POST', {confirm: true});
  updateWatch = {label: 'the previous installation', kind: 'restore'};
  renderUpdates();
  followUpdate();
}
function installOnlineControls() {
  const status = element('online-setting-status');
  const save = async (box, name) => {
    const previous = !box.checked;
    if (name === 'install_automatically' && box.checked && !await confirmWorkflow('Install automatically will install every new signed release without asking. A leaked release signing key together with GitHub write access could then change this Pi without anyone pressing a button. Turn it on?')) {
      box.checked = false; return;
    }
    box.disabled = true;
    status.textContent = 'Saving…';
    try {
      const body = {[name]: box.checked};
      if (name === 'install_automatically' && box.checked) body.confirm = true;
      const view = await request('/api/updates/online-settings', 'POST', body);
      showOnline({...(onlineView || {}), settings: view.settings, token_set: view.token_set});
      status.textContent = 'Saved.';
    } catch (error) {
      box.checked = previous;
      status.textContent = 'The setting was not saved. The previous setting is still in place.';
    } finally {
      box.disabled = false;
    }
  };
  element('online-daily-check').addEventListener('change', event => save(event.target, 'daily_check'));
  element('online-install-automatically').addEventListener('change', event => save(event.target, 'install_automatically'));
  loadOnlineStatus();
}
function wireReleaseControls() {
  const buttons = [
    ['release-check', 'Check for updates', checkForUpdates],
    ['release-install', 'Install update', installUpdate],
    ['release-rollback', 'Restore previous installation', restoreUpdate]
  ].map(([id, label, action]) => {
    const button = actionButton(label, action);
    button.id = id;
    // actionButton enables a button again when its request ends. The page then decides.
    button.afterAction = () => renderUpdates();
    return button;
  });
  buttons[1].disabled = true;
  buttons[2].disabled = true;
  element('release-actions').append(...buttons);
  installUpdateCheckPreference();
  installOnlineControls();
}
function installReleaseControls() {
  workflowCard('release-controls', 'Updates and recovery', 'updates', `
    <p>Check for a new signed release and install it with one click. An update briefly interrupts the dashboard and the test network. The previous installation and settings are backed up first.</p>
    <dl class="update-facts">
      <div><dt>Installed</dt><dd id="update-installed">Unknown</dd></div>
      <div><dt>Last checked</dt><dd id="update-last-checked">Not checked yet</dd></div>
      <div><dt>Newest release</dt><dd id="update-available">No newer release found</dd></div>
      <div><dt>Signed by</dt><dd id="update-signer">Not applicable</dd></div>
    </dl>
    <p id="release-status" role="status" aria-live="polite">Check for an approved release on this Pi.</p>
    <p id="online-check-status" class="form-note" role="status" aria-live="polite">Loading the online check…</p>
    <p id="github-access-hint" class="form-note" hidden></p>
    <div id="release-actions"></div>
    <p id="update-progress" class="update-progress" role="status" aria-live="polite" hidden></p>
    <h3 class="online-heading">Settings</h3>
    <label class="update-auto-check"><input id="online-daily-check" type="checkbox" disabled><span>Check automatically every day</span></label>
    <label class="update-auto-check"><input id="online-install-automatically" type="checkbox" disabled><span>Install automatically</span></label>
    <p class="form-note">Install automatically installs a new signed release without anyone pressing Install. It uses the same checks and the same recovery copy as a manual install. Both settings are off until you turn them on.</p>
    <p id="online-setting-status" class="form-note" role="status" aria-live="polite"></p>
    <details><summary>The GitHub token</summary><p>The repository is private, so the Pi needs a read-only token for this one repository. Create it on GitHub (Settings, Developer settings, Fine-grained tokens, only this repository, Contents read-only, with an expiry date). Then run <code>sudo jittersim update set-token</code> on the Pi, paste the token and press Ctrl-D. This page never asks for the token and never shows it.</p></details>
    <label class="update-auto-check"><input id="check-staged-updates-on-open" type="checkbox" disabled><span>Look for a release staged on this Pi when the dashboard opens</span></label>
    <p id="update-preference-status" class="form-note update-preference-status" role="status" aria-live="polite">Loading the saved preference…</p>
    <details><summary>How releases reach this private Pi</summary><p>The owner builds and signs a release on the Mac and deploy-from-mac.sh stages it over SSH. The Pi can also fetch the newest signed release from GitHub itself. The Pi stages only a release signed by a key it trusts and newer than the one it runs. The admin token cannot stage a release: it can only install the one already staged. See Signed releases in the deployment guide.</p></details>`);
  wireReleaseControls();
}
installReleaseControls();

function refreshConnectedTreatments() {
  if (!lastConnectedDevices) return;
  const byMac = new Map(lastConnectedDevices.devices.map(device => [device.mac_address, device]));
  document.querySelectorAll('.connected-treatment').forEach(node => {
    const device = byMac.get(node.dataset.mac);
    if (device) node.textContent = connectedTreatment(device);
  });
}
