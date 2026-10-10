// Device speed test (docs/implementation-plan-device-speed-test-2026-10-09.md).
// Self-contained: a "Test speed" button joins each connected device row through
// connectedRowHooks and everything else lives in one dialog this script makes.
const speedTestState = {
  dialog: null, device: null, meta: null, run: null, results: [], editing: null, pollTimer: null, serial: 0
};
const SPEED_TEST_FIELDS = {
  download_busy_kbit: ['Download, average while busy', 'kbit'],
  download_p90_kbit: ['Download, 90th percentile', 'kbit'],
  download_peak_kbit: ['Download, peak', 'kbit'],
  upload_busy_kbit: ['Upload, average while busy', 'kbit'],
  upload_p90_kbit: ['Upload, 90th percentile', 'kbit'],
  upload_peak_kbit: ['Upload, peak', 'kbit'],
  rtt_median_ms: ['Round trip Pi to device, median', 'ms'],
  loss_percent: ['Echo loss Pi to device', '%'],
  reported_download_mbit: ['Device page: download', 'Mbit'],
  reported_upload_mbit: ['Device page: upload', 'Mbit'],
  reported_latency_ms: ['Device page: latency', 'ms'],
  reported_jitter_ms: ['Device page: jitter', 'ms']
};
const SPEED_TEST_REPORTED = [
  ['download_mbit', 'Download (Mbit/s)'], ['upload_mbit', 'Upload (Mbit/s)'],
  ['latency_ms', 'Latency (ms)'], ['jitter_ms', 'Jitter (ms)']
];

function speedTestNode(tag, text, className) {
  const node = document.createElement(tag);
  if (text !== undefined && text !== null) node.textContent = text;
  if (className) node.className = className;
  return node;
}

function speedTestButton(label, id, onClick) {
  const button = speedTestNode('button', label);
  button.type = 'button';
  if (id) button.id = id;
  button.addEventListener('click', async () => {
    setButtonBusy(button, true);
    try { await onClick(button); } catch (error) { speedTestSay(error.message, 'error'); }
    finally { if (button.isConnected) setButtonBusy(button, false); }
  });
  return button;
}

function speedTestRate(kbit) {
  if (kbit === null || kbit === undefined) return '—';
  return kbit >= 1000 ? (kbit / 1000).toFixed(2) + ' Mbit/s' : Math.round(kbit) + ' kbit/s';
}

function speedTestValue(value, unit) {
  if (value === null || value === undefined) return '—';
  if (unit === 'kbit') return speedTestRate(value);
  if (unit === 'Mbit') return value + ' Mbit/s';
  return value + (unit === '%' ? '%' : ' ' + unit);
}

function speedTestWhen(seconds) {
  return new Date(seconds * 1000).toLocaleString([], {dateStyle: 'short', timeStyle: 'short'});
}

function speedTestSay(message, kind) {
  const line = element('speed-test-announce');
  if (line) line.textContent = message;
  if (kind === 'error') showMessage(message, 'error');
}

function speedTestQr(provider) {
  const ns = 'http://www.w3.org/2000/svg';
  const size = provider.qr_size;
  const margin = 4;
  const svg = document.createElementNS(ns, 'svg');
  svg.setAttribute('viewBox', '0 0 ' + (size + 2 * margin) + ' ' + (size + 2 * margin));
  svg.setAttribute('role', 'img');
  svg.setAttribute('aria-label', 'QR code for ' + provider.url);
  svg.setAttribute('class', 'speed-test-qr');
  svg.setAttribute('shape-rendering', 'crispEdges');
  const back = document.createElementNS(ns, 'rect');
  back.setAttribute('width', String(size + 2 * margin));
  back.setAttribute('height', String(size + 2 * margin));
  back.setAttribute('fill', '#ffffff');
  const path = document.createElementNS(ns, 'path');
  let d = '';
  provider.qr_rows.forEach((hex, y) => {
    const bits = parseInt(hex, 16);
    for (let x = 0; x < size; x += 1) {
      if (Math.floor(bits / Math.pow(2, size - 1 - x)) % 2) d += 'M' + (x + margin) + ' ' + (y + margin) + 'h1v1h-1z';
    }
  });
  path.setAttribute('d', d);
  path.setAttribute('fill', '#000000');
  svg.append(back, path);
  return svg;
}

function speedTestProvider(id) {
  const providers = speedTestState.meta ? speedTestState.meta.providers : [];
  return providers.find((provider) => provider.id === id) || providers[0];
}

function speedTestDialog() {
  if (speedTestState.dialog) return speedTestState.dialog;
  const dialog = speedTestNode('dialog', null, 'speed-test-dialog');
  dialog.id = 'speed-test-dialog';
  dialog.setAttribute('aria-labelledby', 'speed-test-title');
  const title = speedTestNode('h2', 'Speed test');
  title.id = 'speed-test-title';
  title.tabIndex = -1;
  const announce = speedTestNode('p', '', 'speed-test-announce');
  announce.id = 'speed-test-announce';
  announce.setAttribute('role', 'status');

  const start = speedTestNode('section', null, 'speed-test-start');
  start.id = 'speed-test-start';
  start.append(speedTestNode('p', 'Start a measurement on the Pi, then run the speed test on the device itself. ' +
    'The Pi counts what goes through this device\'s queues once per second.', 'speed-test-help'));
  const providerLabel = speedTestNode('label', 'Speed test site');
  const provider = speedTestNode('select');
  provider.id = 'speed-test-provider';
  providerLabel.append(provider);
  const durationLabel = speedTestNode('label', 'Length in seconds');
  const duration = speedTestNode('input');
  duration.id = 'speed-test-duration';
  duration.type = 'number';
  duration.inputMode = 'numeric';
  durationLabel.append(duration);
  const labelLabel = speedTestNode('label', 'Label (optional)');
  const label = speedTestNode('input');
  label.id = 'speed-test-label';
  label.type = 'text';
  label.maxLength = 80;
  label.placeholder = 'For example: No limits';
  labelLabel.append(label);
  start.append(providerLabel, durationLabel, labelLabel, speedTestButton('Start measuring', 'speed-test-begin', speedTestStart));

  const running = speedTestNode('section', null, 'speed-test-running');
  running.id = 'speed-test-running';
  running.hidden = true;
  const progress = speedTestNode('p', '', 'speed-test-progress');
  progress.id = 'speed-test-progress';
  const live = speedTestNode('p', '', 'speed-test-live');
  live.id = 'speed-test-live';
  const source = speedTestNode('p', '', 'speed-test-source');
  source.id = 'speed-test-source';
  const open = speedTestNode('p');
  open.id = 'speed-test-open';
  const qr = speedTestNode('div', null, 'speed-test-qr-box');
  qr.id = 'speed-test-qr-box';
  const actions = speedTestNode('div', null, 'speed-test-actions');
  actions.append(
    speedTestButton('Finish now', 'speed-test-finish', () => speedTestEnd('finish')),
    speedTestButton('Cancel test', 'speed-test-cancel', () => speedTestEnd('cancel'))
  );
  running.append(progress, open, qr, live, source, actions);

  const results = speedTestNode('section', null, 'speed-test-results');
  results.id = 'speed-test-results';
  const historyHeading = speedTestNode('h3', 'Results for this device');
  const history = speedTestNode('div', null, 'speed-test-history');
  history.id = 'speed-test-history';
  const editor = speedTestNode('div', null, 'speed-test-editor');
  editor.id = 'speed-test-editor';
  editor.hidden = true;
  const compareHeading = speedTestNode('h3', 'Compare before and after');
  const compare = speedTestNode('div', null, 'speed-test-compare');
  const baseline = speedTestNode('select');
  baseline.id = 'speed-test-baseline';
  const after = speedTestNode('select');
  after.id = 'speed-test-after';
  const baselineLabel = speedTestNode('label', 'Before');
  baselineLabel.append(baseline);
  const afterLabel = speedTestNode('label', 'After');
  afterLabel.append(after);
  compare.append(baselineLabel, afterLabel, speedTestButton('Compare', 'speed-test-compare-button', speedTestCompare));
  const comparison = speedTestNode('div', null, 'speed-test-comparison');
  comparison.id = 'speed-test-comparison';
  const exports = speedTestNode('div', null, 'speed-test-actions');
  exports.append(
    speedTestButton('Export CSV', 'speed-test-export-csv', () => speedTestExport('csv')),
    speedTestButton('Export JSON', 'speed-test-export-json', () => speedTestExport('json'))
  );
  results.append(historyHeading, history, editor, compareHeading, compare, comparison, exports);

  const close = speedTestButton('Close', 'speed-test-close', () => dialog.close());
  close.className = 'speed-test-close';
  const note = speedTestNode('p', 'Closing this window keeps a running measurement going on the Pi.', 'speed-test-help');
  dialog.append(title, announce, start, running, results, note, close);
  dialog.addEventListener('close', () => {
    speedTestStopPolling();
    speedTestRestoreFocus();
  });
  document.body.append(dialog);
  speedTestState.dialog = dialog;
  return dialog;
}

function speedTestFocusTitle() {
  // The sections swap in place, so the focused control can vanish. Start again at the title.
  const title = element('speed-test-title');
  if (title && speedTestState.dialog && speedTestState.dialog.open) title.focus({preventScroll: true});
}

function speedTestRestoreFocus() {
  // The device list redraws every few seconds, so the button that opened the window may be gone.
  const device = speedTestState.device;
  const rows = device ? document.querySelectorAll('.connected-row[data-mac-address]') : [];
  const row = Array.from(rows).find((item) => item.dataset.macAddress === device.mac_address);
  const live = row && row.querySelector('.speed-test-open-button:not([disabled])');
  const fallback = document.querySelector('[data-page-link="devices"]');
  const target = live || fallback;
  if (target && target.isConnected) target.focus({preventScroll: true});
}

async function speedTestOpen(device) {
  const dialog = speedTestDialog();
  speedTestStopPolling();
  speedTestState.serial += 1;
  speedTestState.device = device;
  speedTestState.run = null;
  speedTestState.editing = null;
  element('speed-test-title').textContent = 'Speed test: ' + (device.display_name || device.ipv4_address || device.mac_address);
  element('speed-test-announce').textContent = '';
  element('speed-test-comparison').replaceChildren();
  element('speed-test-editor').hidden = true;
  element('speed-test-running').hidden = true;
  element('speed-test-start').hidden = false;
  element('speed-test-results').hidden = false;
  if (!dialog.open) dialog.showModal();
  speedTestFocusTitle();
  await speedTestLoad();
}

async function speedTestLoad() {
  const device = speedTestState.device;
  const serial = speedTestState.serial;
  const data = await request('/api/speed-tests?mac=' + encodeURIComponent(device.mac_address), 'GET');
  if (serial !== speedTestState.serial) return;
  const firstLoad = !speedTestState.meta;
  speedTestState.meta = data;
  const select = element('speed-test-provider');
  if (!select.options.length) {
    data.providers.forEach((provider) => select.append(new Option(provider.name, provider.id)));
    select.value = data.default_provider;
  }
  const duration = element('speed-test-duration');
  duration.min = String(data.limits.min_seconds);
  duration.max = String(data.limits.max_seconds);
  if (firstLoad || !duration.value) duration.value = String(data.limits.default_seconds);
  speedTestRenderHistory(data.results);
  if (data.active.length && !speedTestState.run) {
    speedTestState.run = data.active[0];
    speedTestShowRunning(data.active[0]);
    speedTestPoll();
  }
}

async function speedTestStart() {
  const device = speedTestState.device;
  const duration = Number(element('speed-test-duration').value);
  const run = await request('/api/speed-tests/start', 'POST', {
    mac_address: device.mac_address,
    provider: element('speed-test-provider').value,
    duration_seconds: Number.isInteger(duration) ? duration : speedTestState.meta.limits.default_seconds,
    label: element('speed-test-label').value
  });
  speedTestState.run = run;
  speedTestShowRunning(run);
  speedTestSay('Measuring. Open the speed test on the device now.');
  speedTestPoll();
}

function speedTestShowRunning(run) {
  // While measuring only the link, the code and the live numbers are shown.
  element('speed-test-start').hidden = true;
  element('speed-test-results').hidden = true;
  element('speed-test-running').hidden = false;
  const provider = speedTestProvider(run.provider);
  const open = element('speed-test-open');
  const link = speedTestNode('a', provider.url);
  link.href = provider.url;
  link.target = '_blank';
  link.rel = 'noopener noreferrer';
  link.id = 'speed-test-link';
  open.replaceChildren('On ' + run.device_name + ', open ', link, ' (' + provider.name + ') and run the test. ', speedTestNode('small', provider.note));
  element('speed-test-qr-box').replaceChildren(speedTestQr(provider));
  speedTestRenderRunning(run);
  speedTestFocusTitle();
}

function speedTestRenderRunning(run) {
  element('speed-test-progress').textContent = run.state === 'running'
    ? 'Measuring: ' + run.seconds_left + ' s left' : 'Measurement ended';
  element('speed-test-source').textContent = run.source_label + (run.source_note ? '. ' + run.source_note : '');
  element('speed-test-live').textContent = run.source === 'none' ? 'The Pi has no counter for this device.' :
    'Now: download ' + speedTestRate(run.live.download.kbit) + ', upload ' + speedTestRate(run.live.upload.kbit) +
    ' · Peak: download ' + speedTestRate(run.live.download.peak_kbit) + ', upload ' + speedTestRate(run.live.upload.peak_kbit);
}

function speedTestStopPolling() {
  if (speedTestState.pollTimer !== null) window.clearTimeout(speedTestState.pollTimer);
  speedTestState.pollTimer = null;
}

async function speedTestPoll() {
  speedTestStopPolling();
  const run = speedTestState.run;
  const serial = speedTestState.serial;
  if (!run || !speedTestState.dialog.open) return;
  let status;
  try {
    status = await request('/api/speed-tests/status?run=' + encodeURIComponent(run.id), 'GET');
  } catch (error) {
    if (serial !== speedTestState.serial || speedTestState.run !== run) return;
    if (error.status === 404) {
      // The dashboard restarted or forgot the run, so there is nothing left to watch.
      await speedTestFinished({result: null, ended_reason: 'This test is no longer running.'});
      return;
    }
    speedTestState.pollTimer = window.setTimeout(speedTestPoll, 2000);
    return;
  }
  if (serial !== speedTestState.serial || speedTestState.run !== run) return;
  speedTestRenderRunning(status);
  if (status.state === 'running') {
    speedTestState.pollTimer = window.setTimeout(speedTestPoll, 1000);
    return;
  }
  speedTestFinished(status);
}

async function speedTestEnd(action) {
  const run = speedTestState.run;
  if (!run) return;
  speedTestStopPolling();
  let status;
  try {
    status = await request('/api/speed-tests/' + action, 'POST', {run_id: run.id});
  } catch (error) {
    if (speedTestState.run !== run) throw error;
    if (error.status === 404) {
      await speedTestFinished({result: null, ended_reason: 'This test is no longer running.'});
      return;
    }
    // Already ended (409), busy (503) or no answer: ask again what the run did and keep watching.
    await speedTestPoll();
    if (error.status === 409) return;
    throw error;
  }
  await speedTestFinished(status);
}

async function speedTestFinished(status) {
  speedTestState.run = null;
  element('speed-test-running').hidden = true;
  element('speed-test-start').hidden = false;
  element('speed-test-results').hidden = false;
  speedTestSay(status.result ? 'Speed test saved.' : (status.ended_reason || 'Speed test ended.'));
  speedTestFocusTitle();
  await speedTestLoad();
  if (status.result) speedTestEdit(status.result);
}

function speedTestDirection(result, direction) {
  const values = (result.summary || {})[direction] || {};
  if (values.verdict === 'not-measured') return ['Not measured', ''];
  const labels = speedTestState.meta.verdict_labels;
  return [speedTestRate(values.busy_average_kbit), labels[values.verdict] || ''];
}

function speedTestRenderHistory(results) {
  // A new or deleted result resets the comparison to the newest one against the one before it.
  const sameList = results.map((result) => result.id).join() === speedTestState.results.map((result) => result.id).join();
  speedTestState.results = results;
  const box = element('speed-test-history');
  box.replaceChildren();
  if (!results.length) {
    box.append(speedTestNode('p', 'No speed tests saved for this device yet.', 'speed-test-help'));
  } else {
    const table = speedTestNode('table', null, 'data-table speed-test-table');
    const head = speedTestNode('tr');
    ['When', 'Label', 'Profile', 'Download', 'Upload', 'Round trip', ''].forEach((name) => {
      const cell = speedTestNode('th', name);
      cell.scope = 'col';
      if (!name) cell.append(speedTestNode('span', 'Actions', 'visually-hidden'));
      head.append(cell);
    });
    const thead = speedTestNode('thead');
    thead.append(head);
    const body = speedTestNode('tbody');
    results.forEach((result) => {
      const row = speedTestNode('tr');
      row.dataset.resultId = String(result.id);
      const saved = result.profile || {};
      // A schedule, timer, scenario or group profile can control the device instead of its own.
      const control = ['profile', 'none', 'unknown', undefined].includes(saved.applied_by) ? '' :
        ' (' + (saved.applied_by_label || saved.applied_by.replace('-', ' ')) + ')';
      const profile = (saved.profile_name || 'No profile') + control;
      const shared = result.source === 'everyone' || result.source === 'everyone-else' ? ' (shared queue)' : '';
      const latency = (result.summary || {}).latency || {};
      const cells = [
        [speedTestWhen(result.started_at), result.status === 'complete' ? '' : result.status.replace('-', ' ')],
        [result.label || '—', ''],
        [profile + shared, ''],
        speedTestDirection(result, 'download'),
        speedTestDirection(result, 'upload'),
        [latency.rtt_median_ms === null || latency.rtt_median_ms === undefined
          ? (latency.status === 'no-answer' ? 'No answer' : 'Not available') : latency.rtt_median_ms + ' ms',
          [latency.loss_percent ? latency.loss_percent + '% lost' : '',
            latency.shaped === false ? 'unshaped Wi-Fi round trip' : ''].filter(Boolean).join(' · ')]
      ];
      cells.forEach(([main, small]) => {
        const cell = speedTestNode('td');
        cell.append(speedTestNode('strong', main));
        if (small) cell.append(speedTestNode('small', small));
        row.append(cell);
      });
      const actions = speedTestNode('td');
      const group = speedTestNode('div', null, 'row-actions');
      const edit = speedTestButton('Edit', null, () => speedTestEdit(result));
      edit.setAttribute('aria-label', 'Edit result from ' + speedTestWhen(result.started_at));
      const remove = speedTestButton('Delete', null, () => speedTestDelete(result));
      remove.setAttribute('aria-label', 'Delete result from ' + speedTestWhen(result.started_at));
      group.append(edit, remove);
      actions.append(group);
      row.append(actions);
      body.append(row);
    });
    table.append(thead, body);
    const wrap = speedTestNode('div', null, 'speed-test-table-wrap');
    wrap.append(table);
    box.append(wrap);
  }
  ['speed-test-baseline', 'speed-test-after'].forEach((id, index) => {
    const select = element(id);
    const previous = select.value;
    select.replaceChildren(...results.map((result) => new Option(
      '#' + result.id + ' · ' + speedTestWhen(result.started_at) + ' · ' +
      (result.label || (result.profile || {}).profile_name || 'No profile'), String(result.id))));
    const fallback = results.length ? String(results[index === 0 ? Math.min(1, results.length - 1) : 0].id) : '';
    select.value = sameList && results.some((result) => String(result.id) === previous) ? previous : fallback;
  });
  element('speed-test-compare-button').disabled = results.length < 2;
}

function speedTestEdit(result) {
  speedTestState.editing = result.id;
  const editor = element('speed-test-editor');
  editor.hidden = false;
  editor.replaceChildren(speedTestNode('h3', 'Result from ' + speedTestWhen(result.started_at)));
  editor.append(speedTestNode('p', 'Type in what the speed test page showed on the device so it can be compared too.', 'speed-test-help'));
  const label = speedTestNode('label', 'Label');
  const labelInput = speedTestNode('input');
  labelInput.type = 'text';
  labelInput.maxLength = 80;
  labelInput.id = 'speed-test-edit-label';
  labelInput.value = result.label || '';
  label.append(labelInput);
  const grid = speedTestNode('div', null, 'speed-test-grid');
  grid.append(label);
  SPEED_TEST_REPORTED.forEach(([field, text]) => {
    const wrap = speedTestNode('label', text);
    const input = speedTestNode('input');
    input.type = 'number';
    input.min = '0';
    input.step = 'any';
    input.inputMode = 'decimal';
    input.id = 'speed-test-reported-' + field.replace('_', '-');
    const value = (result.reported || {})[field];
    input.value = value === undefined || value === null ? '' : String(value);
    wrap.append(input);
    grid.append(wrap);
  });
  editor.append(grid, speedTestButton('Save result details', 'speed-test-save-reported', async () => {
    const reported = {};
    SPEED_TEST_REPORTED.forEach(([field]) => {
      const raw = element('speed-test-reported-' + field.replace('_', '-')).value.trim();
      if (raw !== '') reported[field] = Number(raw);
    });
    await request('/api/speed-tests/update', 'POST', {
      result_id: result.id, label: element('speed-test-edit-label').value, reported
    });
    speedTestSay('Result details saved.');
    await speedTestLoad();
  }));
}

async function speedTestDelete(result) {
  if (!window.confirm('Delete the speed test result from ' + speedTestWhen(result.started_at) + '? This cannot be undone.')) return;
  await request('/api/speed-tests/delete', 'POST', {result_id: result.id});
  if (speedTestState.editing === result.id) element('speed-test-editor').hidden = true;
  speedTestSay('Result deleted.');
  await speedTestLoad();
}

async function speedTestCompare() {
  const baseline = element('speed-test-baseline').value;
  const after = element('speed-test-after').value;
  const data = await request('/api/speed-tests/compare?baseline=' + encodeURIComponent(baseline) +
    '&result=' + encodeURIComponent(after), 'GET');
  const box = element('speed-test-comparison');
  box.replaceChildren();
  const table = speedTestNode('table', null, 'data-table speed-test-table');
  const head = speedTestNode('tr');
  ['Number', 'Before', 'After', 'Change'].forEach((name) => {
    const cell = speedTestNode('th', name);
    cell.scope = 'col';
    head.append(cell);
  });
  const thead = speedTestNode('thead');
  thead.append(head);
  const body = speedTestNode('tbody');
  data.changes.filter((item) => item.baseline !== null || item.result !== null).forEach((item) => {
    const [name, unit] = SPEED_TEST_FIELDS[item.field] || [item.field, ''];
    const row = speedTestNode('tr');
    const change = item.change === null ? '—' : (item.change > 0 ? '+' : '') + speedTestValue(item.change, unit) +
      (item.percent === null ? '' : ' (' + (item.percent > 0 ? '+' : '') + item.percent + '%)');
    [name, speedTestValue(item.baseline, unit), speedTestValue(item.result, unit), change]
      .forEach((text, index) => {
        const cell = speedTestNode(index === 0 ? 'th' : 'td', text);
        if (index === 0) cell.scope = 'row';
        row.append(cell);
      });
    body.append(row);
  });
  table.append(thead, body);
  const wrap = speedTestNode('div', null, 'speed-test-table-wrap');
  wrap.append(table);
  const labels = speedTestState.meta.verdict_labels;
  const verdicts = speedTestNode('ul', null, 'speed-test-verdicts');
  ['download', 'upload'].forEach((direction) => {
    const pair = data.verdicts[direction];
    verdicts.append(speedTestNode('li', (direction === 'download' ? 'Download' : 'Upload') + ' against the cap: before ' +
      (labels[pair.baseline] || '—') + '; after ' + (labels[pair.result] || '—') + '.'));
  });
  data.warnings.forEach((warning) => verdicts.append(speedTestNode('li', warning, 'speed-test-warning')));
  box.append(wrap, verdicts);
  speedTestSay('Comparison ready.');
}

async function speedTestExport(format) {
  const token = element('admin-token').value.trim();
  const mac = speedTestState.device ? speedTestState.device.mac_address : '';
  const response = await fetch('/api/speed-tests/export?format=' + format + '&mac=' + encodeURIComponent(mac), {
    headers: {Authorization: 'Bearer ' + token}, cache: 'no-store'
  });
  if (!response.ok) throw new Error('The export could not be made.');
  const url = URL.createObjectURL(await response.blob());
  const link = document.createElement('a');
  link.href = url;
  link.download = 'device-speed-tests.' + format;
  document.body.append(link);
  link.click();
  link.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
  speedTestSay('Export downloaded.');
}

connectedRowHooks.push((device, details) => {
  if (!device.mac_address) return;
  const line = speedTestNode('div', null, 'speed-test-line');
  const name = device.display_name || device.ipv4_address || device.mac_address;
  const button = speedTestNode('button', 'Test speed', 'speed-test-open-button');
  button.type = 'button';
  button.setAttribute('aria-label', 'Test speed of ' + name);
  if (device.unaffected) {
    button.disabled = true;
    line.append(button, speedTestNode('small', 'Protected devices are not measured.'));
  } else if (device.association_verified !== true) {
    // Without a confirmed Wi-Fi connection the Pi cannot tell this device from an old address.
    button.disabled = true;
    line.append(button, speedTestNode('small', 'Wi-Fi connection status is unknown, so this device cannot be tested now.'));
  } else {
    button.addEventListener('click', () => {
      speedTestOpen(device).catch((error) => speedTestSay(error.message, 'error'));
    });
    line.append(button);
  }
  details.append(line);
});
