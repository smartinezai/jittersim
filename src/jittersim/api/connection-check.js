// Connection check (docs/implementation-plan-connection-check-2026-10-17.md).
// Self-contained like speed-test.js: a "Check connection" button joins each connected device row
// through connectedRowHooks and everything else lives in one dialog this script makes. It reuses
// the speed test window's small helpers (speedTestNode, speedTestWhen) and its styles.
const connCheckState = {
  dialog: null, device: null, meta: null, run: null, results: [], shown: null,
  pollTimer: null, serial: 0, deleting: null, focusAction: null
};
const connCheckLockClosingDialogs = new WeakSet();

function connCheckButton(label, id, onClick) {
  const button = speedTestNode('button', label);
  button.type = 'button';
  if (id) button.id = id;
  button.addEventListener('click', async () => {
    const serial = connCheckState.serial;
    setButtonBusy(button, true);
    try { await onClick(button); }
    catch (error) {
      if (serial === connCheckState.serial) connCheckError(error.message);
    }
    finally { if (button.isConnected) setButtonBusy(button, false); }
  });
  return button;
}

function connCheckSay(message) {
  const line = element('conn-check-announce');
  if (line) line.textContent = message;
  const alert = element('conn-check-error');
  if (alert) alert.textContent = '';
}

function connCheckError(message) {
  const alert = element('conn-check-error');
  if (alert) alert.textContent = message;
}

function connCheckDialog() {
  if (connCheckState.dialog) return connCheckState.dialog;
  const dialog = speedTestNode('dialog', null, 'speed-test-dialog conn-check-dialog');
  dialog.id = 'conn-check-dialog';
  dialog.setAttribute('aria-labelledby', 'conn-check-title');
  const title = speedTestNode('h2', 'Check connection');
  title.id = 'conn-check-title';
  title.tabIndex = -1;
  const announce = speedTestNode('p', '', 'speed-test-announce');
  announce.id = 'conn-check-announce';
  announce.setAttribute('role', 'status');
  const alert = speedTestNode('p', '', 'conn-check-error');
  alert.id = 'conn-check-error';
  alert.setAttribute('role', 'alert');

  const start = speedTestNode('section', null, 'conn-check-start');
  start.id = 'conn-check-start';
  start.append(speedTestNode('p',
    'The Pi sends small ping packets to this device and counts the answers. It compares the loss, ' +
    'round trip, jitter, duplicates and order with the profile the Pi applied to the device. ' +
    'Only the download side of a profile can be checked this way: use Test speed for the upload. ' +
    'Wake the device first (turn on the screen), because a sleeping phone answers slowly.', 'speed-test-help'));
  const probesLabel = speedTestNode('label', 'Number of probes (10 per second)');
  const probes = speedTestNode('input');
  probes.id = 'conn-check-probes';
  probes.type = 'number';
  probes.inputMode = 'numeric';
  probesLabel.append(probes);
  const hint = speedTestNode('p', '', 'speed-test-help');
  hint.id = 'conn-check-hint';
  const labelLabel = speedTestNode('label', 'Label (optional)');
  const label = speedTestNode('input');
  label.id = 'conn-check-label';
  label.type = 'text';
  label.maxLength = 80;
  label.placeholder = 'For example: Before the new profile';
  labelLabel.append(label);
  start.append(probesLabel, hint, labelLabel, connCheckButton('Start check', 'conn-check-begin', connCheckStart));

  const running = speedTestNode('section', null, 'conn-check-running');
  running.id = 'conn-check-running';
  running.hidden = true;
  const progress = speedTestNode('p', '', 'speed-test-progress');
  progress.id = 'conn-check-progress';
  const source = speedTestNode('p', '', 'speed-test-help');
  source.id = 'conn-check-source';
  const actions = speedTestNode('div', null, 'speed-test-actions');
  actions.append(connCheckButton('Cancel check', 'conn-check-cancel', connCheckCancel));
  running.append(progress, source, actions);

  const result = speedTestNode('section', null, 'conn-check-result');
  result.id = 'conn-check-result';
  result.hidden = true;

  const results = speedTestNode('section', null, 'conn-check-results');
  results.id = 'conn-check-results';
  const historyHeading = speedTestNode('h3', 'Checks for this device');
  const history = speedTestNode('div', null, 'conn-check-history');
  history.id = 'conn-check-history';
  const exports = speedTestNode('div', null, 'speed-test-actions');
  exports.append(
    connCheckButton('Export CSV', 'conn-check-export-csv', () => connCheckExport('csv')),
    connCheckButton('Export JSON', 'conn-check-export-json', () => connCheckExport('json'))
  );
  results.append(historyHeading, history, exports);

  const close = connCheckButton('Close', 'conn-check-close', () => dialog.close());
  close.className = 'speed-test-close';
  const note = speedTestNode('p', 'Closing this window keeps a running check going on the Pi.', 'speed-test-help');
  dialog.append(title, announce, alert, start, running, result, results, note, close);
  dialog.addEventListener('close', () => {
    connCheckStopPolling();
    if (connCheckLockClosingDialogs.has(dialog)) {
      connCheckLockClosingDialogs.delete(dialog);
      return;
    }
    connCheckRestoreFocus();
  });
  document.body.append(dialog);
  connCheckState.dialog = dialog;
  return dialog;
}

function connCheckFocusTitle() {
  // The sections swap in place, so the focused control can vanish. Start again at the title.
  const title = element('conn-check-title');
  if (title && connCheckState.dialog && connCheckState.dialog.open) title.focus({preventScroll: true});
}

function connCheckRestoreFocus() {
  // The device list redraws every few seconds, so the button that opened the window may be gone.
  const device = connCheckState.device;
  const rows = device ? document.querySelectorAll('.connected-row[data-mac-address]') : [];
  const row = Array.from(rows).find((item) => item.dataset.macAddress === device.mac_address);
  const live = row && row.querySelector('.conn-check-open-button:not([disabled])');
  const fallback = document.querySelector('.sidebar-nav a[href="#devices"]');
  const target = live || fallback;
  if (target && target.isConnected) target.focus({preventScroll: true});
}

function connCheckClearPrivateState() {
  connCheckStopPolling();
  connCheckState.serial += 1;
  const dialog = connCheckState.dialog;
  if (dialog) {
    if (dialog.open) {
      connCheckLockClosingDialogs.add(dialog);
      dialog.close();
      const credentials = element('open-admin-credentials');
      if (credentials && credentials.getClientRects().length) {
        credentials.focus({preventScroll: true});
      }
    }
    dialog.remove();
  }
  connCheckState.dialog = null;
  connCheckState.device = null;
  connCheckState.meta = null;
  connCheckState.run = null;
  connCheckState.results = [];
  connCheckState.shown = null;
  connCheckState.deleting = null;
}

privateDataLockHooks.push(connCheckClearPrivateState);

async function connCheckOpen(device) {
  const dialog = connCheckDialog();
  connCheckStopPolling();
  connCheckState.serial += 1;
  connCheckState.device = device;
  connCheckState.run = null;
  connCheckState.shown = null;
  connCheckState.deleting = null;
  element('conn-check-title').textContent = 'Check connection: ' + (device.display_name || device.ipv4_address || device.mac_address);
  element('conn-check-announce').textContent = '';
  element('conn-check-error').textContent = '';
  // Another device's rows must not stay usable while this device's results load (or fail to).
  connCheckState.results = [];
  element('conn-check-history').replaceChildren(speedTestNode('p', 'Loading checks…', 'speed-test-help'));
  element('conn-check-result').hidden = true;
  element('conn-check-result').replaceChildren();
  element('conn-check-running').hidden = true;
  element('conn-check-start').hidden = false;
  element('conn-check-results').hidden = false;
  if (!dialog.open) dialog.showModal();
  connCheckFocusTitle();
  await connCheckLoad();
}

async function connCheckLoad() {
  const device = connCheckState.device;
  const serial = connCheckState.serial;
  const data = await request('/api/connection-checks?mac=' + encodeURIComponent(device.mac_address), 'GET');
  if (serial !== connCheckState.serial) return;
  const firstLoad = !connCheckState.meta;
  connCheckState.meta = data;
  const probes = element('conn-check-probes');
  probes.min = String(data.limits.min_probes);
  probes.max = String(data.limits.max_probes);
  if (firstLoad || !probes.value) probes.value = String(data.limits.default_probes);
  element('conn-check-hint').textContent = 'From ' + data.limits.min_probes + ' to ' + data.limits.max_probes +
    ' probes, ' + data.limits.probes_per_second + ' per second. With 100 probes a set loss of about 5% or more ' +
    'can be told from a healthy link. Smaller shares need more probes.';
  connCheckRenderHistory(data.results);
  if (data.active.length && !connCheckState.run) {
    connCheckState.run = data.active[0];
    connCheckShowRunning(data.active[0]);
    connCheckPoll();
  }
}

async function connCheckStart() {
  const device = connCheckState.device;
  const serial = connCheckState.serial;
  const probes = Number(element('conn-check-probes').value);
  const run = await request('/api/connection-checks/start', 'POST', {
    mac_address: device.mac_address,
    probes: Number.isInteger(probes) ? probes : connCheckState.meta.limits.default_probes,
    label: element('conn-check-label').value
  });
  if (serial !== connCheckState.serial) return;
  connCheckState.run = run;
  connCheckShowRunning(run);
  connCheckSay('Checking. The Pi is sending probes to the device.');
  connCheckPoll();
}

function connCheckShowRunning(run) {
  element('conn-check-start').hidden = true;
  element('conn-check-results').hidden = true;
  element('conn-check-result').hidden = true;
  element('conn-check-running').hidden = false;
  connCheckRenderRunning(run);
  connCheckFocusTitle();
}

function connCheckRenderRunning(run) {
  element('conn-check-progress').textContent = run.state === 'running'
    ? 'Sent ' + run.probes_sent + ' of ' + run.probes_planned + ' probes, ' + run.probes_received + ' answered'
    : 'Check ended';
  element('conn-check-source').textContent = run.source_note || '';
}

function connCheckStopPolling() {
  if (connCheckState.pollTimer !== null) window.clearTimeout(connCheckState.pollTimer);
  connCheckState.pollTimer = null;
}

async function connCheckPoll() {
  connCheckStopPolling();
  const run = connCheckState.run;
  const serial = connCheckState.serial;
  if (!run || !connCheckState.dialog.open) return;
  let status;
  try {
    status = await request('/api/connection-checks/status?run=' + encodeURIComponent(run.id), 'GET');
  } catch (error) {
    if (serial !== connCheckState.serial || connCheckState.run !== run) return;
    if (error.status === 404) {
      // The dashboard restarted or forgot the run, so there is nothing left to watch.
      await connCheckFinished({result: null, ended_reason: 'This check is no longer running.'}, serial);
      return;
    }
    connCheckState.pollTimer = window.setTimeout(connCheckPoll, 2000);
    return;
  }
  if (serial !== connCheckState.serial || connCheckState.run !== run) return;
  connCheckRenderRunning(status);
  if (status.state === 'running') {
    connCheckState.pollTimer = window.setTimeout(connCheckPoll, 1000);
    return;
  }
  connCheckFinished(status, serial);
}

async function connCheckCancel() {
  const run = connCheckState.run;
  if (!run) return;
  const serial = connCheckState.serial;
  connCheckStopPolling();
  let status;
  try {
    status = await request('/api/connection-checks/cancel', 'POST', {run_id: run.id});
  } catch (error) {
    // The window moved on (another device, or a newer run here): this answer is about the past.
    if (serial !== connCheckState.serial || connCheckState.run !== run) return;
    if (error.status === 404) {
      await connCheckFinished({result: null, ended_reason: 'This check is no longer running.'}, serial);
      return;
    }
    // Already ended (409), busy (503) or no answer: ask again what the run did and keep watching.
    await connCheckPoll();
    if (serial !== connCheckState.serial) return;
    if (error.status === 409) return;
    throw error;
  }
  if (serial !== connCheckState.serial || connCheckState.run !== run) return;
  await connCheckFinished(status, serial);
}

function connCheckEndMessage(status) {
  const result = status.result;
  if (!result) return status.ended_reason || 'Check ended.';
  if (result.status === 'changed' || result.status === 'fault') {
    return 'Check saved, but not judged.' + (status.ended_reason ? ' ' + status.ended_reason : '');
  }
  const verdict = (result.summary || {}).verdict_text;
  return 'Check saved.' + (verdict ? ' ' + verdict + '.' : '');
}

async function connCheckFinished(status, serial = connCheckState.serial) {
  if (serial !== connCheckState.serial) return;
  connCheckState.run = null;
  element('conn-check-running').hidden = true;
  element('conn-check-start').hidden = false;
  element('conn-check-results').hidden = false;
  connCheckSay(connCheckEndMessage(status));
  if (status.result) connCheckShowResult(status.result);
  else if (status.ended_reason) connCheckError(status.ended_reason);
  connCheckFocusTitle();
  await connCheckLoad();
}

function connCheckTable(headings, rows) {
  const table = speedTestNode('table', null, 'data-table speed-test-table');
  const head = speedTestNode('tr');
  headings.forEach((name) => {
    const cell = speedTestNode('th', name);
    cell.scope = 'col';
    if (!name) cell.append(speedTestNode('span', 'Actions', 'visually-hidden'));
    head.append(cell);
  });
  const thead = speedTestNode('thead');
  thead.append(head);
  const body = speedTestNode('tbody');
  rows.forEach((row) => body.append(row));
  table.append(thead, body);
  const wrap = speedTestNode('div', null, 'speed-test-table-wrap');
  wrap.append(table);
  return wrap;
}

function connCheckCell(main, small, tag) {
  const cell = speedTestNode(tag || 'td');
  cell.append(speedTestNode(tag === 'th' ? 'span' : 'strong', main));
  if (small) cell.append(speedTestNode('small', small));
  return cell;
}

function connCheckVerdictText(result) {
  const labels = connCheckState.meta ? connCheckState.meta.verdict_labels : {};
  const summary = result.summary || {};
  return summary.verdict_text || labels[summary.verdict] || 'Not judged';
}

function connCheckShowResult(result) {
  connCheckState.shown = result.id;
  const box = element('conn-check-result');
  box.hidden = false;
  const summary = result.summary || {};
  const probes = summary.probes || {};
  const heading = speedTestNode('h3', 'Result from ' + speedTestWhen(result.started_at));
  heading.id = 'conn-check-result-heading';
  heading.tabIndex = -1;
  const verdict = speedTestNode('p', null, 'conn-check-verdict conn-check-verdict-' + (summary.verdict || 'none'));
  verdict.append(speedTestNode('strong', connCheckVerdictText(result)));
  const saved = result.profile || {};
  const counts = speedTestNode('p', probes.sent + ' probes sent, ' + probes.received + ' answered, ' + probes.lost +
    ' lost. Profile: ' + (saved.profile_name || 'No profile') + (result.label ? '. Label: ' + result.label : '') + '.',
    'speed-test-help');
  const rows = (summary.metrics || []).map((metric) => {
    const row = speedTestNode('tr');
    row.dataset.metric = metric.id;
    const name = connCheckCell(metric.label, '', 'th');
    name.scope = 'row';
    row.append(name, connCheckCell(metric.set, ''), connCheckCell(metric.measured, ''),
      connCheckCell(metric.status_label, metric.sentence));
    return row;
  });
  box.replaceChildren(heading, verdict, counts);
  if (rows.length) box.append(connCheckTable(['Number', 'Profile says', 'Measured', 'Result'], rows));
  const notes = speedTestNode('ul', null, 'speed-test-verdicts');
  (summary.notes || []).forEach((note) => notes.append(speedTestNode('li', note, 'speed-test-help')));
  if (notes.children.length) box.append(notes);
}

function connCheckRenderHistory(results) {
  connCheckState.results = results;
  const box = element('conn-check-history');
  box.replaceChildren();
  if (!results.length) {
    box.append(speedTestNode('p', 'No checks saved for this device yet.', 'speed-test-help'));
    return;
  }
  const rows = results.map((result) => {
    const row = speedTestNode('tr');
    row.dataset.resultId = String(result.id);
    const summary = result.summary || {};
    const probes = summary.probes || {};
    const rtt = summary.rtt || {};
    const when = speedTestWhen(result.started_at);
    const saved = result.profile || {};
    const shared = result.source === 'everyone' || result.source === 'everyone-else' ? ' (shared)' : '';
    row.append(
      connCheckCell(when, result.status === 'complete' ? '' : result.status),
      connCheckCell(result.label || '—', ''),
      connCheckCell((saved.profile_name || 'No profile') + shared, ''),
      connCheckCell(connCheckVerdictText(result), ''),
      connCheckCell(probes.loss_percent === null || probes.loss_percent === undefined ? '—' : probes.loss_percent + '% lost',
        rtt.rtt_median_ms === null || rtt.rtt_median_ms === undefined ? 'no answers' : 'median ' + rtt.rtt_median_ms + ' ms'));
    const actions = speedTestNode('td');
    const group = speedTestNode('div', null, 'row-actions');
    const show = connCheckButton('Show', null, () => connCheckShowSaved(result));
    show.setAttribute('aria-label', 'Show check from ' + when);
    show.dataset.action = 'show';
    group.append(show);
    if (connCheckState.deleting === result.id) {
      const sure = connCheckButton('Delete for good', null, () => connCheckDelete(result));
      sure.setAttribute('aria-label', 'Really delete check from ' + when);
      sure.dataset.action = 'confirm';
      const keep = connCheckButton('Keep', null, async () => {
        connCheckState.deleting = null;
        connCheckState.focusAction = [result.id, 'delete'];
        connCheckRenderHistory(connCheckState.results);
        connCheckSay('Check kept.');
      });
      keep.dataset.action = 'keep';
      group.append(sure, keep);
    } else {
      const remove = connCheckButton('Delete', null, async () => {
        connCheckState.deleting = result.id;
        connCheckState.focusAction = [result.id, 'keep'];
        connCheckRenderHistory(connCheckState.results);
        connCheckSay('Choose Delete for good to remove this check, or Keep.');
      });
      remove.setAttribute('aria-label', 'Delete check from ' + when);
      remove.dataset.action = 'delete';
      group.append(remove);
    }
    actions.append(group);
    row.append(actions);
    return row;
  });
  box.append(connCheckTable(['When', 'Label', 'Profile', 'Verdict', 'Loss and round trip', ''], rows));
  if (connCheckState.focusAction) {
    // The button that was pressed is gone after the redraw: put focus on its safe neighbour.
    const [id, action] = connCheckState.focusAction;
    connCheckState.focusAction = null;
    const target = box.querySelector('tr[data-result-id="' + id + '"] [data-action="' + action + '"]');
    if (target) target.focus({preventScroll: true});
  }
}

function connCheckShowSaved(result) {
  connCheckShowResult(result);
  const heading = element('conn-check-result-heading');
  if (heading) heading.focus({preventScroll: false});
}

async function connCheckDelete(result) {
  const serial = connCheckState.serial;
  await request('/api/connection-checks/delete', 'POST', {result_id: result.id});
  if (serial !== connCheckState.serial) return;
  connCheckState.deleting = null;
  if (connCheckState.shown === result.id) {
    connCheckState.shown = null;
    element('conn-check-result').hidden = true;
    element('conn-check-result').replaceChildren();
  }
  connCheckSay('Check deleted.');
  await connCheckLoad();
  connCheckFocusTitle();
}

async function connCheckExport(format) {
  const serial = connCheckState.serial;
  const token = element('admin-token').value.trim();
  const mac = connCheckState.device ? connCheckState.device.mac_address : '';
  const response = await fetch('/api/connection-checks/export?format=' + format + '&mac=' + encodeURIComponent(mac), {
    headers: {Authorization: 'Bearer ' + token}, cache: 'no-store'
  });
  if (!response.ok) throw new Error('The export could not be made.');
  const blob = await response.blob();
  if (serial !== connCheckState.serial) return;
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = 'connection-checks.' + format;
  document.body.append(link);
  link.click();
  link.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
  connCheckSay('Export downloaded.');
}

connectedRowHooks.push((device, details) => {
  if (!device.mac_address) return;
  const name = device.display_name || device.ipv4_address || device.mac_address;
  const button = speedTestNode('button', 'Check connection', 'conn-check-open-button');
  button.type = 'button';
  button.setAttribute('aria-label', 'Check connection of ' + name);
  // The button joins the line of the Test speed button, or makes its own line.
  let line = details.querySelector('.speed-test-line');
  if (!line) {
    line = speedTestNode('div', null, 'speed-test-line');
    details.append(line);
  }
  if (device.association_verified !== true) {
    // Without a confirmed Wi-Fi connection the Pi cannot tell this device from an old address.
    button.disabled = true;
    line.append(button);
    if (!line.querySelector('small')) {
      line.append(speedTestNode('small', 'Wi-Fi connection status is unknown, so this device cannot be checked now.'));
    }
    return;
  }
  button.addEventListener('click', () => {
    const opening = connCheckOpen(device);
    const serial = connCheckState.serial;
    opening.catch((error) => {
      if (serial === connCheckState.serial) connCheckError(error.message);
    });
  });
  line.append(button);
  if (device.unaffected) {
    line.append(speedTestNode('small', 'A check of a protected device shows the plain Wi-Fi path.'));
  }
});
