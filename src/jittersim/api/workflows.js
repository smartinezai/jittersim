// Drafts live only in this page. Credentials are never retained in drafts.
const formDrafts = new Map();
let draftSequence = 0;
let draftTokenRevision = null;
const draftForms = {
  'everyone-else-form': ['Everyone else'],
  'unaffected-form': ['Protected device'],
  'profile-form': ['Profile', 'profile-name'],
  'device-form': ['Device assignment', 'device-name'],
  'group-form': ['Device group', 'group-name'],
  'schedule-form': ['Scheduled profile'],
  'timer-form': ['Profile timer'],
  'scenario-start-form': ['Run a scenario'],
  'scenario-form': ['Saved scenario', 'scenario-name'],
  'progression-form': ['Progression mode'],
  'data-limit-form': ['Device data limit'],
  'group-data-limit-form': ['Shared group data limit'],
  'service-list-form': ['Service list', 'service-list-name'],
  'service-assignment-form': ['Browser service profile'],
  'service-quota-form': ['Service data limit'],
  'time-quota-form': ['Service time limit'],
  'access-point-form': ['Pi access-point settings'],
  'traffic-rate-form': ['ISP traffic limits'],
  'policy-form': ['Network access rules'],
  'workspace-timer-form': ['Device timer'],
  'workspace-scenario-form': ['Device scenario'],
  'test-session-form': ['New test session', 'test-session-name'],
  'test-session-edit': ['Test notes'],
  'site-rule-form': ['Site blocking rule', 'site-rule-name']
};

function draftLabel(form) {
  const description = draftForms[form.id];
  const name = description ? description[0] : form.closest('.card')?.querySelector('h2')?.textContent || 'Form';
  let context = description && description[1] ? element(description[1])?.value : '';
  if (form.id.startsWith('workspace-')) context = element('workspace-title').textContent;
  if (form.id === 'test-session-edit') context = element('test-session-heading').textContent;
  context = String(context || '').trim().replace(/\s+/g, ' ');
  return name + (context ? ': ' + (context.length > 100 ? context.slice(0, 100) + '…' : context) : '');
}

function draftFields(form) {
  return Array.from(form.querySelectorAll('input, select, textarea, [data-day]'))
    .filter(field => field.hasAttribute('data-day') || !['password', 'file', 'hidden', 'submit', 'button'].includes(field.type));
}

function rememberDraft(form) {
  if (!form || !form.id || form.id === 'admin-token-form') return;
  const fields = draftFields(form).map((field, index) => ({
    key: field.id || String(index),
    value: field.value,
    checked: field.checked,
    selected: field.multiple ? Array.from(field.selectedOptions, option => option.value) : null,
    pressed: field.getAttribute('aria-pressed')
  }));
  const draft = { sequence: ++draftSequence, fields, label: draftLabel(form) };
  if (typeof adminTokenRevision !== 'undefined') draftTokenRevision = adminTokenRevision;
  if (form.id.startsWith('workspace-')) draft.deviceId = workspaceDevice;
  if (form.id === 'test-session-edit') draft.sessionId = activeTestRecord?.id;
  formDrafts.set(form.id, draft);
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
        if (field.hasAttribute('data-require-available-option')) field.value = '';
        else {
          const missing = new Option(saved.value + ' (no longer available)', saved.value);
          field.add(missing);
          field.value = saved.value;
        }
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
  if (typeof adminTokenVerified !== 'undefined' && !adminTokenVerified) {
    notice.hidden = true;
    element('draft-links')?.replaceChildren();
    return;
  }
  notice.hidden = formDrafts.size === 0;
  element('draft-count').textContent = formDrafts.size + ' unfinished form' + (formDrafts.size === 1 ? '' : 's') +
    '. Apply uses saved settings. Your edits stay here until saved or discarded.';
  const links = element('draft-links');
  if (!links) return;
  const buttons = new Map();
  Array.from(links.children).forEach(button => {
    if (!formDrafts.has(button.dataset.draftForm)) button.remove();
    else buttons.set(button.dataset.draftForm, button);
  });
  formDrafts.forEach((draft, id) => {
    let button = buttons.get(id);
    if (!button) {
      button = document.createElement('button');
      button.type = 'button';
      button.dataset.draftForm = id;
      button.addEventListener('click', () => reviewDraft(id));
      links.append(button);
    }
    const label = 'Review ' + draft.label;
    if (button.textContent !== label) button.textContent = label;
  });
}

function reviewDraft(id) {
  const draft = formDrafts.get(id);
  const form = element(id);
  if (!draft || !form ||
      (draft.deviceId && !devices.some(device => device.id === draft.deviceId)) ||
      (draft.sessionId && activeTestRecord?.id !== draft.sessionId)) {
    showMessage('This unfinished form is no longer available. Your edits are still kept here.', 'error');
    return;
  }
  if (draft.deviceId) {
    workspaceDevice = draft.deviceId;
    element('device-workspace').hidden = false;
    renderWorkspaceState();
  }
  if (draft.sessionId) element('test-session-details').hidden = false;
  setControlsOpen(false, false);
  revealElement(form);
  const field = draftFields(form).find(candidate => !candidate.disabled && !candidate.readOnly && candidate.getClientRects().length);
  const target = field || form;
  if (!field) form.tabIndex = -1;
  revealElement(target);
  target.focus({preventScroll: true});
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

// True when the text is on the clipboard. False when the browser refused and the text is offered to select.
async function copyReport(text, label) {
  try {
    await navigator.clipboard.writeText(text);
    showMessage(label + ' copied.', 'success');
    return true;
  } catch (error) {
    const fallback = element('report-copy-fallback');
    fallback.hidden = false;
    fallback.value = text;
    fallback.focus();
    fallback.select();
    showMessage('Select and copy the text below. Clipboard access is unavailable.', 'success');
    return false;
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
  const links = document.createElement('div');
  links.id = 'draft-links';
  links.setAttribute('role', 'group');
  links.setAttribute('aria-label', 'Unfinished forms');
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
  notice.append(text, links, discard);
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
  privateDataLockHooks.push(() => {
    if (draftTokenRevision !== adminTokenRevision) formDrafts.clear();
    renderDraftNotice();
  });
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
  if (system.version) knownHealthVersion = system.version;
  const version = installedReleaseLabel();
  if (version) element('sidebar-version').textContent = 'Version ' + version;
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
    const card = document.createElement('article');
    card.className = 'device-card';
    card.dataset.key = device.id;
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
    const card = document.createElement('article'); card.className='device-card'; card.dataset.key='everyone-else';
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
  ['workspace-runs', 'workspace-usage', 'workspace-timeline'].forEach(key => delete element(key).dataset.renderKey);
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
    // The graph page reads the counters every 2 seconds, so a fresh reading is reused here.
    const traffic = await readTraffic(1500);
    if (id !== workspaceDevice) return;
    const list = element('workspace-runs');
    replaceIfChanged(list, JSON.stringify([data.timers, data.runs, data.progressions]), () => {
      list.replaceChildren();
      data.timers.forEach(timer => list.append(textNode('p', 'Timer: ' + timer.profile_name + ' · ' + formatTimerRemaining(timer.remaining_seconds)),
        actionButton('Stop timer', async button => { await cancelTimer(timer.id, button); await loadConditions(); })));
      data.runs.forEach(run => list.append(textNode('p', 'Scenario: ' + (run.scenario_name || run.scenario_id)),
        actionButton('Stop scenario', async () => { await request('/api/scenarios/stop', 'POST', {id:run.id}); refreshAfterSuccess('Scenario stopped.'); })));
      data.progressions.forEach(run => list.append(textNode('p', 'Progression: ' + run.start_profile_name + ' → ' + run.end_profile_name),
        actionButton('Stop progression', button => stopProgression(run.id, button))));
      if (!list.children.length) list.append(textNode('p', 'No temporary runs.'));
    });
    const usage = element('workspace-usage');
    replaceIfChanged(usage, JSON.stringify([data.data_limits, data.group_data_limits, data.service_quotas, data.time_quotas]), () => {
      usage.replaceChildren();
      [...data.data_limits, ...data.group_data_limits, ...data.service_quotas].forEach(limit => usage.append(textNode('p',
        (limit.name || (limit.group_id ? 'Shared data limit' : 'Device data limit')) + ': ' +
        (limit.used_bytes / 1048576).toFixed(1) + ' / ' + (limit.limit_bytes / 1048576).toFixed(1) + ' MB' + (limit.enabled ? '' : ' · Disabled'))));
      data.time_quotas.forEach(limit => usage.append(textNode('p', limit.name + ': ' + Math.floor(limit.used_seconds / 60) + ' / ' + Math.floor(limit.limit_seconds / 60) + ' minutes')));
      if (!usage.children.length) usage.append(textNode('p', 'No usage limits for this device.'));
    });
    const timeline = element('workspace-timeline');
    replaceIfChanged(timeline, JSON.stringify(data.timeline), () => {
      const entries = data.timeline.changes.map(change => textNode('li', new Date(change.at * 1000).toLocaleString() +
        ' · ' + change.profile + ' · ' + change.reason + (change.overridden_schedules.length ? ' · Takes priority over: ' + change.overridden_schedules.join(', ') : '')));
      timeline.replaceChildren(...entries.slice(0,6));
      element('workspace-timeline-later').replaceChildren(...entries.slice(6));
      element('workspace-later').hidden = entries.length <= 6;
    });
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
  const attached = measurementRuns;
  const result = await request('/api/test-sessions/save', 'POST', {
    id:activeTestRecord.id, revision:activeTestRecord.revision, notes:element('test-session-notes').value,
    finish, ...(attach ? {measurements:attached} : {})
  });
  if (attach) keptMeasurementRuns = attached;
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


// One accessible question for every destructive or restarting action. The one-argument form keeps
// the generic Continue button. Options: confirmLabel (the verb on the button), danger (red button
// for deletes), title (a heading that names the action), returnFocus (the control to focus again
// when it is not the one that has focus now). Escape and Cancel answer false, and focus goes back
// to the control that opened the question.
let workflowConfirmSerial = 0;
async function confirmWorkflow(message, options) {
  const settings = options || {};
  const opener = settings.returnFocus || document.activeElement;
  // A list that the page polls may be drawn again while the question is open, and then the control
  // that asked is gone. Its place in the list says which control is its twin.
  const anchor = rowAnchor(opener);
  const serial = ++workflowConfirmSerial;
  const dialog = document.createElement('dialog');
  dialog.className = 'workflow-confirm';
  const text = textNode('p', message); text.id = 'workflow-confirm-text-' + serial;
  if (settings.title) {
    const title = textNode('h2', settings.title); title.id = 'workflow-confirm-title-' + serial;
    dialog.setAttribute('aria-labelledby', title.id);
    dialog.setAttribute('aria-describedby', text.id);
    dialog.append(title);
  } else dialog.setAttribute('aria-labelledby', text.id);
  dialog.append(text);
  const answer = await new Promise(resolve => {
    const choice = (label, value, className) => {
      const button = textNode('button', label, className); button.type = 'button';
      button.addEventListener('click', () => dialog.close(value));
      return button;
    };
    const cancel = choice('Cancel', 'cancel');
    const proceed = choice(settings.confirmLabel || 'Continue', 'confirm', settings.danger ? 'danger' : '');
    dialog.append(cancel, proceed);
    // Escape closes the dialog without a return value, which counts as Cancel.
    dialog.addEventListener('close', () => resolve(dialog.returnValue === 'confirm'), {once:true});
    document.body.append(dialog); dialog.showModal(); cancel.focus();
  });
  dialog.remove();
  restoreWorkflowFocus(opener, anchor);
  return answer;
}
function restoreWorkflowFocus(opener, anchor) {
  if (!opener || opener === document.body) return;
  if (!opener.isConnected) {
    const twin = anchor && controlAtAnchor(anchor);
    if (twin && !twin.disabled) twin.focus({preventScroll:true});
    else if (anchor) focusListInstead(anchor.scope);
    return;
  }
  if (!opener.disabled) { opener.focus({preventScroll:true}); return; }
  // The opener is busy while its own action runs (see actionButton). Give it focus when it is
  // enabled again, unless the person has already moved on.
  const watcher = new MutationObserver(() => {
    if (opener.disabled) return;
    watcher.disconnect();
    const active = document.activeElement;
    if (!active || active === document.body) opener.focus({preventScroll:true});
  });
  watcher.observe(opener, {attributes:true, attributeFilter:['disabled']});
  window.setTimeout(() => watcher.disconnect(), 120000);
}

// The row that asked is gone (removed while the question was open): its list is the nearest place.
function focusListInstead(listId) {
  const list = document.getElementById(listId);
  const active = document.activeElement;
  if (!list || !list.isConnected || list.closest('[hidden]') || (active && active !== document.body)) return;
  if (!list.hasAttribute('tabindex')) list.setAttribute('tabindex', '-1');
  list.focus({preventScroll:true});
}

let releaseState = null;
let releasePollAt = 0;
let autoCheckStagedUpdates = null;
let knownHealthVersion = null;
let lastConfirmedInstalledVersion = null;
let updateWatch = null;
let updateFollowing = false;
let updateCheckRunning = false;
let updateRetryOwner = null;
let updateOutcome = '';
let updateAccessRevision = 0;
const updateReads = new Map();
const updateReadRevisions = {release:0, online:0, preference:0};
let onlineSettingsWrite = null;
let preferenceWrite = null;
const RELEASE_BUSY_PHASES = ['queued','preparing','stopping','backing-up','installing','checking','rolling-back'];
const PHASE_TEXT = {
  queued: 'The update is queued.',
  preparing: 'Preparing and checking the release while services remain available.',
  stopping: 'Stopping services before the settings backup.',
  'backing-up': 'Backing up the installation and settings.',
  installing: 'Installing the new release.',
  checking: 'Restarting services and checking that everything works.',
  'rolling-back': 'Restoring the previous installation.'
};
const updateSleep = ms => new Promise(resolve => setTimeout(resolve, ms));
function updateContext() { return {token: adminTokenRevision, access: updateAccessRevision}; }
function updateContextCurrent(context) {
  return context.token === adminTokenRevision && context.access === updateAccessRevision && adminTokenVerified &&
    (!context.kind || context.generation === updateReadRevisions[context.kind]);
}
function sameUpdateContext(first, second) {
  return first.token === second.token && first.access === second.access;
}
function updateRead(kind, context, read) {
  if (!updateContextCurrent(context)) return Promise.resolve(null);
  const readContext = {...context, kind, generation:updateReadRevisions[kind]};
  const active = updateReads.get(kind);
  if (active) {
    if (sameUpdateContext(active.context, readContext) && active.context.generation === readContext.generation) return active.promise;
    // Wait for older physical work then start a read after the invalidating write.
    return active.promise.catch(() => null).then(() => {
      if (!updateContextCurrent(readContext)) return null;
      return updateRead(kind, context, read);
    });
  }
  const owner = {context:readContext, promise:null};
  owner.promise = Promise.resolve().then(() => updateContextCurrent(readContext) ? read(readContext) : null).finally(() => {
    if (updateReads.get(kind) === owner) updateReads.delete(kind);
  });
  updateReads.set(kind, owner);
  return owner.promise;
}
function identifiedUpdate(state) {
  return Boolean(state && typeof state.request_id === 'string' && state.request_id.length === 32 && /^[0-9a-f]{32}$/.test(state.request_id) &&
    ['install','rollback'].includes(state.request_kind));
}
function retainedRetry(state) {
  const retry = state && state.retry;
  return identifiedUpdate(state) && retry && typeof retry.id === 'string' && retry.id.length === 64 && /^[0-9a-f]{64}$/.test(retry.id) &&
    retry.request_id === state.request_id && retry.kind === state.request_kind &&
    (retry.version === null || (typeof retry.version === 'string' && retry.version.length <= 64)) &&
    typeof retry.recovering === 'boolean' ? retry : null;
}
function watchUpdate(state, context, kind, label) {
  return {context, kind, label,
    requestId:identifiedUpdate(state) ? state.request_id : null,
    requestKind:identifiedUpdate(state) ? (kind === 'restore' ? 'rollback' : 'install') : null};
}
function releaseOperationBusy() {
  return Boolean(releaseState && RELEASE_BUSY_PHASES.includes(releaseState.phase));
}
function updatesPageActive() { return !document.hidden && currentPage() === 'updates'; }
function releaseLabel(state) { return (state && (state.tag || state.available)) || ''; }
function installedReleaseLabel() {
  const label = releaseState && releaseState.installed;
  return label && !/^Unversioned(?: installation)?$/i.test(label) ? label : lastConfirmedInstalledVersion || knownHealthVersion;
}
function stagedReleaseReady(state) {
  return Boolean(state && state.available && state.available !== state.installed && state.installable === true);
}
async function pollReleaseProgress() {
  if (!updatesPageActive() || !adminTokenVerified) return releaseState;
  const reads = [loadReleaseStatus(), loadOnlineStatus()];
  if (autoCheckStagedUpdates === null) reads.push(loadUpdateCheckPreference());
  await Promise.allSettled(reads);
  if (updateWatch) followUpdate();
  return releaseState;
}
function updateProgressText(failedRead) {
  if (updateOutcome) return updateOutcome;
  if (!updateWatch) return '';
  if (failedRead) return 'The dashboard is restarting. This page reconnects by itself.';
  return PHASE_TEXT[releaseState && releaseState.phase] || 'Working on the ' + updateWatch.kind + '.';
}
function renderUpdates(failedRead) {
  const state = releaseState;
  const busy = Boolean(updateWatch) || releaseOperationBusy();
  const working = !adminTokenVerified || busy || updateCheckRunning || onlinePolling || Boolean(updateRetryOwner);
  const ready = adminTokenVerified && stagedReleaseReady(state);
  const installed = installedReleaseLabel();
  const blocked = state && state.available && state.installable === false && state.reason;
  if (installed) element('sidebar-version').textContent = 'Version ' + installed;
  element('update-installed').textContent = installed || 'Unknown';
  element('update-available').textContent = !state ? 'Unknown. Check for updates to try again.'
    : ready ? releaseLabel(state) : blocked ? releaseLabel(state) + ' (cannot be installed)' : 'No newer release staged';
  element('update-signer').textContent = ready && state.signed_by ? state.signed_by : 'Not applicable';
  element('update-recovery').textContent = !state || typeof state.rollback_available !== 'boolean' ? 'Unknown'
    : state.rollback_available ? 'Kept. Restore previous installation is available.'
    : state.recovery_problem ? 'Could not be checked. Read the update log on the Pi.'
    : 'Made automatically before the next install.';
  const notes = ready && typeof state.notes === 'string' ? state.notes.trim().slice(0, 16384) : '';
  element('update-notes').textContent = notes;
  element('update-notes-box').hidden = !notes;
  element('release-install').textContent = ready ? 'Install ' + releaseLabel(state) : 'Install update';
  element('update-last-checked').textContent = onlineView && onlineView.checked_at
    ? new Date(onlineView.checked_at * 1000).toLocaleString() : 'Unknown';
  element('release-check').disabled = working;
  element('release-install').disabled = working || !ready;
  element('release-install').classList.toggle('primary', ready && !working);
  element('release-rollback').disabled = working || !(state && state.rollback_available);
  const retry = retainedRetry(state);
  element('release-retry').hidden = !retry;
  element('release-retry').disabled = working || !retry;
  const retryReason = state && !retry && typeof state.retry_unavailable_reason === 'string'
    ? state.retry_unavailable_reason.slice(0, 500) : '';
  element('update-retry-status').textContent = retryReason;
  element('update-retry-status').hidden = !retryReason;
  const progress = updateProgressText(failedRead);
  element('update-progress').hidden = !progress;
  // The progress line speaks (role="status"), so it is only written when its words change.
  if (element('update-progress').textContent !== progress) element('update-progress').textContent = progress;
  element('sidebar-update-link').hidden = !ready;
  element('sidebar-update-link').textContent = ready ? 'Update available: ' + releaseLabel(state) : '';
  element('update-available-badge').hidden = !ready;
  const nav = element('updates-tab-link');
  if (ready) nav.setAttribute('aria-label', 'Updates, update available');
  else nav.removeAttribute('aria-label');
}
function loadReleaseStatus({force = false} = {}) {
  const context = updateContext();
  if (!force && releaseState && Date.now() - releasePollAt < 5000) return Promise.resolve(releaseState);
  return updateRead('release', context, async context => {
    if (!updateContextCurrent(context)) return null;
    releasePollAt = Date.now();
    if (!updateWatch) element('release-status').textContent = 'Checking for a staged release on this Pi…';
    try {
      const state = await request('/api/updates', 'GET');
      if (!updateContextCurrent(context)) return null;
      releaseState = state;
      if (state.installed && !/^Unversioned(?: installation)?$/i.test(state.installed)) lastConfirmedInstalledVersion = state.installed;
      const signer = state.available && state.signed_by ? ' (signed by ' + state.signed_by + ')' : '';
      const blocked = state.available && state.installable === false && state.reason;
      element('release-status').textContent = 'Installed: ' + (installedReleaseLabel() || 'Unknown') +
        ' · Available: ' + (state.available ? state.available + signer : 'None staged') +
        ' · ' + (blocked ? state.reason : state.message || 'Status read.');
      if (releaseOperationBusy() && !updateWatch) {
        updateWatch = watchUpdate(state, context, state.request_kind === 'rollback' || (!identifiedUpdate(state) && state.phase === 'rolling-back') ? 'restore' : 'install', state.available || '');
        updateOutcome = '';
      }
      renderUpdates();
      return state;
    } catch (error) {
      if (!updateContextCurrent(context)) return null;
      releasePollAt = 0;
      if (updateWatch) {
        element('release-status').textContent = 'Waiting for the dashboard to restart. This page checks again shortly.';
        renderUpdates(true);
      } else {
        releaseState = null;
        element('release-status').textContent = 'Staged release status is unknown. Check for updates to try again.';
        renderUpdates();
      }
      throw error;
    }
  });
}
async function followUpdate() {
  if (updateFollowing || !updateWatch) return;
  const watch = updateWatch;
  const context = watch.context || updateContext();
  updateFollowing = true;
  const started = Date.now();
  try {
    while (updateWatch === watch && updateContextCurrent(context) && Date.now() - started < 15 * 60 * 1000) {
      if (updatesPageActive()) {
        try {
          const state = await loadReleaseStatus({force: true});
          if (!updateContextCurrent(context) || updateWatch !== watch) return;
          if (!state) break;
          const phase = state.phase;
          const sameRequest = !watch.requestId || (identifiedUpdate(state) &&
            state.request_id === watch.requestId && state.request_kind === watch.requestKind);
          if (!sameRequest) {
            updateOutcome = 'The requested update was not confirmed. The Pi now reports a different or unidentified operation.';
            updateWatch = null;
          } else if (phase === 'complete' && watch.kind === 'install' && watch.label && state.installed === watch.label) {
            updateOutcome = 'Updated to ' + watch.label + '.';
            updateWatch = null;
          } else if (phase === 'rolled-back' && watch.kind === 'restore' && state.installed) {
            updateOutcome = watch.requestId
              ? 'Previous installation restored. Installed version: ' + state.installed + '.'
              : 'The Pi reports rollback status. This restore request is not confirmed. Installed version: ' + state.installed + '.';
            updateWatch = null;
          } else if (['complete','rolled-back','failed'].includes(phase)) {
            updateOutcome = 'The requested update was not confirmed. ' + (state.message || '') +
              ' Installed version: ' + (state.installed || 'Unknown') + '.';
            updateWatch = null;
          }
          renderUpdates();
        } catch (error) {
          if (!updateContextCurrent(context) || updateWatch !== watch) return;
          renderUpdates(true);
        }
      }
      if (updateWatch === watch) await updateSleep(2000);
    }
  } finally {
    updateFollowing = false;
    if (updateContextCurrent(context) && updateWatch === watch) {
      updateOutcome = 'The update result is still unknown. Check the installed version or the Pi.';
      updateWatch = null;
      renderUpdates();
    }
  }
}
function loadUpdateCheckPreference({afterWrite = false} = {}) {
  if (preferenceWrite && !afterWrite) return Promise.resolve(null);
  const context = updateContext();
  return updateRead('preference', context, async context => {
    const toggle = element('check-staged-updates-on-open');
    const state = element('update-preference-status');
    try {
      const preference = await request('/api/update-preferences', 'GET');
      if (!updateContextCurrent(context)) return null;
      if (typeof preference.check_staged_updates_on_open !== 'boolean') throw new Error('Invalid saved preference.');
      autoCheckStagedUpdates = preference.check_staged_updates_on_open;
      toggle.checked = autoCheckStagedUpdates;
      toggle.disabled = Boolean(preferenceWrite);
      state.textContent = autoCheckStagedUpdates ? 'Automatic checking is on.' : 'Automatic checking is off. Use Check for updates whenever you want.';
      return preference;
    } catch (error) {
      if (!updateContextCurrent(context)) return null;
      autoCheckStagedUpdates = null;
      toggle.disabled = true;
      state.textContent = 'The saved preference is unknown. Open Updates or check again to retry.';
      return null;
    }
  });
}
function installUpdateCheckPreference() {
  const toggle = element('check-staged-updates-on-open');
  const state = element('update-preference-status');
  toggle.addEventListener('change', async () => {
    const context = updateContext();
    if (!updateContextCurrent(context) || autoCheckStagedUpdates === null || preferenceWrite) return;
    const nextValue = toggle.checked;
    const owner = {};
    preferenceWrite = owner;
    updateReadRevisions.preference += 1;
    toggle.disabled = true;
    state.textContent = 'Saving automatic-check preference…';
    try {
      const result = await request('/api/update-preferences', 'POST', {check_staged_updates_on_open: nextValue});
      if (!updateContextCurrent(context)) return;
      if (!result.saved || result.check_staged_updates_on_open !== nextValue) throw new Error('Saved value was not confirmed.');
      updateReadRevisions.preference += 1;
      autoCheckStagedUpdates = nextValue;
      state.textContent = nextValue ? 'Automatic checking is on.' : 'Automatic checking is off.';
      if (nextValue) await loadReleaseStatus({force: true}).catch(() => {});
    } catch (error) {
      if (!updateContextCurrent(context)) return;
      updateReadRevisions.preference += 1;
      autoCheckStagedUpdates = null;
      state.textContent = 'The save result is unknown. Reading the current setting without repeating the save.';
      const current = await loadUpdateCheckPreference({afterWrite:true});
      if (updateContextCurrent(context) && !current) state.textContent = 'The save result is unknown. Check the current setting before trying again.';
    } finally {
      if (preferenceWrite === owner) preferenceWrite = null;
      if (updateContextCurrent(context)) toggle.disabled = autoCheckStagedUpdates === null;
    }
  });
}
let onlineView = null;
let onlinePolling = false;
function onlineSummary(view) {
  if (!view) return 'Online check status is unknown. Check again to retry.';
  if (view.state === 'none') return 'No online check has run yet.';
  if (view.state === 'unavailable') return view.message || 'Online checks are not available.';
  if (view.state === 'running') return 'Checking GitHub…';
  const when = view.checked_at ? ' (' + new Date(view.checked_at * 1000).toLocaleString() + ')' : '';
  return (view.result === 'failed' ? 'The check failed: ' : '') + (view.message || 'The check finished.') + when;
}
function showOnline(view) {
  onlineView = view;
  const ready = adminTokenVerified && view && view.state !== 'unavailable';
  let text = onlineSummary(view);
  const noToken = Boolean(ready && view.token_set === false);
  if (noToken) text = 'No GitHub token is saved on the Pi. Check for updates looks at releases already staged on the Pi.';
  element('online-check-status').textContent = text;
  if (noToken) element('github-access-hint').textContent = 'To let the Pi look at GitHub, follow the setup command in The GitHub token below.';
  element('github-access-hint').hidden = !noToken;
  const settings = ready && view.settings;
  for (const [id, name] of [['online-daily-check','daily_check'],['online-install-automatically','install_automatically']]) {
    const box = element(id);
    box.disabled = !settings || Boolean(onlineSettingsWrite);
    if (settings) box.checked = settings[name] === true;
  }
  renderUpdates();
}
function loadOnlineStatus({afterWrite = false} = {}) {
  if (onlineSettingsWrite && !afterWrite) return Promise.resolve(null);
  const context = updateContext();
  return updateRead('online', context, async context => {
    try {
      const view = await request('/api/updates/check-online', 'GET');
      if (!updateContextCurrent(context)) return null;
      showOnline(view);
      return view;
    } catch (error) {
      if (!updateContextCurrent(context)) return null;
      showOnline(null);
      return null;
    }
  });
}
async function runOnlineCheck({force = false} = {}) {
  if (onlinePolling) return;
  const context = updateContext();
  if (!updateContextCurrent(context)) return;
  onlinePolling = true;
  const settingsGeneration = updateReadRevisions.online;
  try {
    let view = await request('/api/updates/check-online', 'POST', {force});
    if (!updateContextCurrent(context)) return;
    if (settingsGeneration !== updateReadRevisions.online) {
      view = {...view, settings:onlineView && onlineView.settings, token_set:onlineView && onlineView.token_set};
    }
    showOnline(view);
    const started = Date.now();
    while (view.state === 'running' && updateContextCurrent(context) && Date.now() - started < 8 * 60 * 1000) {
      await updateSleep(2000);
      if (!updateContextCurrent(context)) return;
      if (!updatesPageActive()) continue;
      view = await loadOnlineStatus();
      if (!updateContextCurrent(context)) return;
      if (!view) break;
    }
    if (updateContextCurrent(context) && view && view.state === 'done') await loadReleaseStatus({force: true}).catch(() => {});
  } catch (error) {
    if (!updateContextCurrent(context)) return;
    showOnline(null);
    element('online-check-status').textContent = 'The check result is unknown. Reading status will not repeat the check.';
  } finally {
    onlinePolling = false;
    if (updateContextCurrent(context)) renderUpdates();
  }
}
async function checkForUpdates() {
  const context = updateContext();
  if (!updateContextCurrent(context) || updateCheckRunning || onlinePolling) return;
  updateOutcome = '';
  updateCheckRunning = true;
  renderUpdates();
  try {
    if (!onlineView) await loadOnlineStatus();
    if (!updateContextCurrent(context)) return;
    if (!onlineView || (onlineView.state !== 'unavailable' && typeof onlineView.token_set !== 'boolean')) throw new Error('Online status is unknown. Check again to retry.');
    if (onlineView.state !== 'unavailable' && onlineView.token_set === true) await runOnlineCheck({force: true});
    else await loadReleaseStatus({force: true});
  } finally {
    if (updateContextCurrent(context)) { updateCheckRunning = false; renderUpdates(); }
  }
}
async function installUpdate() {
  const context = updateContext();
  if (!updateContextCurrent(context) || !stagedReleaseReady(releaseState)) return;
  const version = releaseState.available;
  const label = releaseLabel(releaseState);
  const signer = releaseState.signed_by ? ' It is signed by ' + releaseState.signed_by + '.' : '';
  if (!await confirmWorkflow('Install ' + label + '?' + signer + ' The installation and settings are backed up first and restored automatically if the Pi is not healthy afterwards. The dashboard and test network will restart. Keep an independent management connection available.')) return;
  if (!updateContextCurrent(context) || !stagedReleaseReady(releaseState) || releaseState.available !== version) return;
  updateOutcome = '';
  updateReadRevisions.release += 1;
  const queued = await request('/api/updates/install', 'POST', {version, confirm: true});
  if (!updateContextCurrent(context)) return;
  updateReadRevisions.release += 1;
  updateWatch = watchUpdate(queued.status || queued, context, 'install', version);
  renderUpdates();
  followUpdate();
}
async function restoreUpdate() {
  const context = updateContext();
  if (!updateContextCurrent(context) || !releaseState || !releaseState.rollback_available) return;
  if (!await confirmWorkflow('Restore the previous installation and settings backup? Changes after that backup will be replaced.')) return;
  if (!updateContextCurrent(context) || !releaseState || !releaseState.rollback_available) return;
  updateOutcome = '';
  updateReadRevisions.release += 1;
  const queued = await request('/api/updates/rollback', 'POST', {confirm: true});
  if (!updateContextCurrent(context)) return;
  updateReadRevisions.release += 1;
  updateWatch = watchUpdate(queued.status || queued, context, 'restore', '');
  renderUpdates();
  followUpdate();
}
async function retryUpdate() {
  const context = updateContext();
  const chosen = retainedRetry(releaseState);
  if (!updateContextCurrent(context) || !chosen || updateRetryOwner || updateWatch || releaseOperationBusy()) return;
  const owner = {context};
  updateRetryOwner = owner;
  renderUpdates();
  try {
    if (!await confirmWorkflow('Retry this failed ' + (chosen.kind === 'rollback' ? 'restore' : 'update') +
      ' using its saved request and recovery copy? ' +
      (chosen.kind === 'rollback' ? 'Changes after that backup will be replaced. ' :
        chosen.recovering ? 'If recovery restores the saved backup, later changes will be replaced. ' : '') +
      'The dashboard and test network may restart. Keep an independent management connection available.')) return;
    if (!updateContextCurrent(context)) return;
    const current = await loadReleaseStatus({force:true});
    if (!updateContextCurrent(context)) return;
    const admission = retainedRetry(current);
    if (!admission || admission.id !== chosen.id || admission.request_id !== chosen.request_id ||
        admission.kind !== chosen.kind || releaseOperationBusy()) {
      updateOutcome = 'The failed update changed. Read its current status before retrying.';
      return;
    }
    updateReadRevisions.release += 1;
    let queued = null;
    try {
      const reply = await request('/api/updates/retry', 'POST', {retry_id:chosen.id});
      if (!updateContextCurrent(context)) return;
      queued = reply && reply.status;
    } catch (error) {
      if (!updateContextCurrent(context)) return;
      updateOutcome = 'The retry result is unknown. Reading status without repeating the retry.';
    }
    if (!updateContextCurrent(context)) return;
    updateReadRevisions.release += 1;
    if (!identifiedUpdate(queued) || queued.request_id !== chosen.request_id || queued.request_kind !== chosen.kind) {
      queued = await loadReleaseStatus({force:true}).catch(() => null);
      if (!updateContextCurrent(context)) return;
    }
    if (!identifiedUpdate(queued) || queued.request_id !== chosen.request_id || queued.request_kind !== chosen.kind) {
      updateOutcome = 'The retry was not confirmed. Read the current operation before trying again.';
      return;
    }
    updateOutcome = '';
    updateWatch = watchUpdate(queued, context, chosen.kind === 'rollback' ? 'restore' : 'install', chosen.version || '');
    await followUpdate();
  } catch (error) {
    if (updateContextCurrent(context)) updateOutcome = 'The retry was not confirmed. Check its current status before trying again.';
  } finally {
    if (updateRetryOwner === owner) updateRetryOwner = null;
    if (updateContextCurrent(context)) renderUpdates();
  }
}
function installOnlineControls() {
  const status = element('online-setting-status');
  const save = async (box, name) => {
    const context = updateContext();
    if (!updateContextCurrent(context) || !onlineView || !onlineView.settings || onlineSettingsWrite) return;
    const next = box.checked;
    const previous = onlineView.settings[name];
    const owner = {};
    onlineSettingsWrite = owner;
    updateReadRevisions.online += 1;
    for (const id of ['online-daily-check','online-install-automatically']) element(id).disabled = true;
    try {
      if (name === 'install_automatically' && next && !await confirmWorkflow('Install automatically will install every new signed release without asking. A leaked release signing key together with GitHub write access could then change this Pi without anyone pressing a button. Turn it on?')) {
        if (updateContextCurrent(context)) box.checked = previous;
        return;
      }
      if (!updateContextCurrent(context)) return;
      status.textContent = 'Saving…';
      const body = {[name]: next};
      if (name === 'install_automatically' && next) body.confirm = true;
      const view = await request('/api/updates/online-settings', 'POST', body);
      if (!updateContextCurrent(context)) return;
      updateReadRevisions.online += 1;
      if (!view.settings || view.settings[name] !== next) throw new Error('Saved value was not confirmed.');
      showOnline({...onlineView, settings: view.settings, token_set: view.token_set});
      status.textContent = 'Saved.';
    } catch (error) {
      if (!updateContextCurrent(context)) return;
      updateReadRevisions.online += 1;
      showOnline(null);
      status.textContent = 'The save result is unknown. Reading the current setting without repeating the save.';
      const current = await loadOnlineStatus({afterWrite:true});
      if (!updateContextCurrent(context)) return;
      status.textContent = current && current.settings
        ? 'Current setting on the Pi: ' + (current.settings[name] ? 'on.' : 'off.')
        : 'The save result is unknown. Check the current setting before trying again.';
    } finally {
      if (onlineSettingsWrite === owner) onlineSettingsWrite = null;
      if (updateContextCurrent(context)) {
        for (const id of ['online-daily-check','online-install-automatically']) element(id).disabled = !(onlineView && onlineView.settings);
      }
    }
  };
  element('online-daily-check').addEventListener('change', event => save(event.target, 'daily_check'));
  element('online-install-automatically').addEventListener('change', event => save(event.target, 'install_automatically'));
}
async function loadUpdateStateAfterSignIn() {
  const context = updateContext();
  await Promise.allSettled([loadOnlineStatus(), loadUpdateCheckPreference()]);
  if (!updateContextCurrent(context)) return;
  if (autoCheckStagedUpdates || updatesPageActive()) await loadReleaseStatus({force: true}).catch(() => {});
}
function clearUpdatePrivateState() {
  updateAccessRevision += 1;
  releaseState = null;
  lastConfirmedInstalledVersion = null;
  releasePollAt = 0;
  onlineView = null;
  autoCheckStagedUpdates = null;
  updateWatch = null;
  updateOutcome = '';
  updateCheckRunning = false;
  for (const id of ['online-daily-check','online-install-automatically','check-staged-updates-on-open']) {
    element(id).checked = false;
    element(id).disabled = true;
  }
  for (const id of ['release-install','release-rollback','release-check','release-retry']) {
    element(id).disabled = true;
    element(id).removeAttribute('aria-busy');
  }
  element('release-retry').hidden = true;
  element('update-retry-status').textContent = '';
  element('update-retry-status').hidden = true;
  for (const id of ['online-check-status','release-status','update-preference-status']) element(id).textContent = 'Sign in to read update status.';
  // This line only answers a save, so it stays empty. The sign-in text would still show after signing in.
  element('online-setting-status').textContent = '';
  element('github-access-hint').textContent = '';
  element('github-access-hint').hidden = true;
  element('sidebar-update-link').textContent = '';
  element('sidebar-update-link').hidden = true;
  element('update-available-badge').hidden = true;
  element('update-progress').hidden = true;
  element('update-progress').textContent = '';
  element('updates-tab-link').removeAttribute('aria-label');
  element('update-installed').textContent = knownHealthVersion || 'Unknown';
  element('update-available').textContent = 'Sign in to read update status.';
  element('update-signer').textContent = 'Sign in to read update status.';
  element('update-recovery').textContent = 'Unknown';
  element('update-notes').textContent = '';
  element('update-notes-box').hidden = true;
  element('release-install').textContent = 'Install update';
  element('update-last-checked').textContent = 'Unknown';
}
privateDataLockHooks.push(clearUpdatePrivateState);
function wireReleaseControls() {
  const buttons = [
    ['release-check','Check for updates',checkForUpdates],
    ['release-install','Install update',installUpdate],
    ['release-rollback','Restore previous installation',restoreUpdate],
    ['release-retry','Retry failed update',retryUpdate]
  ].map(([id,label,action]) => {
    const button = textNode('button', label);
    button.type = 'button';
    button.id = id;
    button.addEventListener('click', async () => {
      const context = updateContext();
      if (button.disabled || !updateContextCurrent(context)) return;
      setButtonBusy(button, true);
      try { await action(); }
      catch (error) { if (updateContextCurrent(context)) showMessage(error.message, 'error'); }
      finally {
        if (updateContextCurrent(context)) { setButtonBusy(button, false); renderUpdates(); }
      }
    });
    return button;
  });
  buttons[1].disabled = true;
  buttons[2].disabled = true;
  buttons[3].disabled = true;
  buttons[3].hidden = true;
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
      <div><dt>Recovery copy</dt><dd id="update-recovery">Unknown</dd></div>
    </dl>
    <details id="update-notes-box" hidden><summary>Release notes</summary><pre id="update-notes" class="update-notes" tabindex="0" role="region" aria-label="Release notes text"></pre></details>
    <p id="release-status">Check for an approved release on this Pi.</p>
    <p id="online-check-status" class="form-note">Loading the online check…</p>
    <p id="github-access-hint" class="form-note" hidden></p>
    <div id="release-actions"></div>
    <p id="update-retry-status" class="form-note" hidden></p>
    <p id="update-progress" class="update-progress" role="status" hidden></p>
    <h3 class="online-heading">Settings</h3>
    <label class="update-auto-check"><input id="online-daily-check" type="checkbox" disabled><span>Check automatically every day</span></label>
    <label class="update-auto-check"><input id="online-install-automatically" type="checkbox" disabled><span>Install automatically</span></label>
    <p class="form-note">Install automatically lets the daily check install a new signed release without anyone pressing Install, so it needs Check automatically every day to be on. Check for updates and the terminal checks never install on their own. It uses the same checks and the same recovery copy as a manual install. Both settings are off until you turn them on.</p>
    <p id="online-setting-status" class="form-note" role="status"></p>
    <details><summary>The GitHub token</summary><p>The repository is private, so the Pi needs a read-only token for this one repository. Create it on GitHub (Settings, Developer settings, Fine-grained tokens, only this repository, Contents read-only, with an expiry date). Then run <code id="update-token-command">the setup command shown after sign-in</code> on the Pi, paste the token and press Ctrl-D. This page never asks for the token and never shows it.</p></details>
    <label class="update-auto-check"><input id="check-staged-updates-on-open" type="checkbox" disabled><span>Look for a release staged on this Pi when the dashboard opens</span></label>
    <p id="update-preference-status" class="form-note update-preference-status">Loading the saved preference…</p>
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
