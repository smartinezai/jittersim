// The forced test network window: the router's Wi-Fi is off and the Pi's test Wi-Fi takes its name.
const WINDOW_DAYS = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday'];
let forcedWindowData = null;
let forcedWindowRequest = null;

function windowClock(minute) {
  return String(Math.floor(minute / 60)).padStart(2, '0') + ':' + String(minute % 60).padStart(2, '0');
}
function windowText(item) {
  return WINDOW_DAYS[item.start_day] + ' ' + windowClock(item.start_minute) + ' to '
    + WINDOW_DAYS[item.end_day] + ' ' + windowClock(item.end_minute);
}
// An end at 00:00 means the end of the day before so Friday to Monday 00:00 is Friday to Sunday 24:00.
function windowFromForm(values) {
  const [startHour, startMinute] = values.startTime.split(':').map(Number);
  const [endHour, endMinute] = values.endTime.split(':').map(Number);
  let endDay = Number(values.endDay);
  let end = endHour * 60 + endMinute;
  if (end === 0) { endDay = (endDay + 6) % 7; end = 1440; }
  return {
    id: values.id, start_day: Number(values.startDay), start_minute: startHour * 60 + startMinute,
    end_day: endDay, end_minute: end, enabled: true,
  };
}

function renderForcedWindows(data) {
  forcedWindowData = data;
  const card = element('forced-window-card');
  const banner = element('forced-window-banner');
  card.hidden = !data.available;
  banner.hidden = !data.notice;
  banner.textContent = data.notice || '';
  const state = data.state || {};
  const status = element('forced-window-status');
  status.textContent = data.notice
    || (state.error ? state.error : (state.problem ? 'The last window did not run: ' + state.problem : 'No window is running.'));
  status.className = state.error || state.problem ? 'form-note forced-window-problem' : 'form-note';
  element('forced-window-end').hidden = !(state.phase && state.phase !== 'idle');
  element('forced-window-login-status').textContent = data.login_saved
    ? 'A router login is saved on the Pi. It cannot be read back.'
    : (data.login_saved === false ? 'No router login is saved. A window cannot start without one.' : 'The saved login could not be checked.');
  element('forced-window-login-remove').disabled = !data.login_saved;
  element('forced-window-zone').textContent = data.time_zone ? 'Times use the Pi\'s clock (' + data.time_zone + ').' : '';
  const list = element('forced-window-list');
  list.replaceChildren();
  if (!data.windows.length) list.append(textNode('li', 'No windows saved yet.', 'form-note'));
  data.windows.forEach(item => {
    const row = document.createElement('li');
    row.className = 'forced-window-row';
    row.append(textNode('span', item.id + ': ' + windowText(item) + (item.enabled ? '' : ' (off)')));
    const toggle = actionButton(item.enabled ? 'Turn off' : 'Turn on', async () => {
      await request('/api/forced-windows', 'POST', {...item, enabled: !item.enabled});
      await loadForcedWindows();
    });
    const remove = actionButton('Delete', async () => {
      if (!await confirmWorkflow('Delete the window ' + item.id + '?')) return;
      await request('/api/forced-windows/delete', 'POST', {id: item.id});
      await loadForcedWindows();
    });
    toggle.setAttribute('aria-label', (item.enabled ? 'Turn off ' : 'Turn on ') + item.id);
    remove.setAttribute('aria-label', 'Delete ' + item.id);
    row.append(toggle, remove);
    list.append(row);
  });
}

async function loadForcedWindows() {
  if (forcedWindowRequest) return forcedWindowRequest;
  forcedWindowRequest = request('/api/forced-windows', 'GET').then(renderForcedWindows).finally(() => { forcedWindowRequest = null; });
  return forcedWindowRequest;
}

function installForcedWindows() {
  const days = WINDOW_DAYS.map((name, index) => '<option value="' + index + '">' + name + '</option>').join('');
  const card = workflowCard('forced-window-card', 'Forced test network window', 'schedules', `
    <p>During a window the Pi copies the router's Wi-Fi name and password and switches the router's Wi-Fi off. Every device has to join the Pi. When the window ends the Pi gets its own name back and the router's Wi-Fi comes on again. Panic and Reset end a window at once.</p>
    <p id="forced-window-status" class="form-note" role="status" aria-live="polite">Loading…</p>
    <p id="forced-window-zone" class="form-note"></p>
    <div class="fields">
      <label>Router admin password <input id="forced-window-password" type="password" autocomplete="off"></label>
    </div>
    <div class="action-buttons" id="forced-window-login-actions"></div>
    <p id="forced-window-login-status" class="form-note" role="status" aria-live="polite"></p>
    <h3>Windows</h3>
    <ul id="forced-window-list" class="forced-window-list"></ul>
    <form id="forced-window-form">
      <div class="fields">
        <label>Window ID <input id="forced-window-id" required maxlength="64" placeholder="weekend"></label>
        <label>Starts on <select id="forced-window-start-day">${days}</select></label>
        <label>Starts at <input id="forced-window-start-time" type="time" value="18:00" required></label>
        <label>Ends on <select id="forced-window-end-day">${days}</select></label>
        <label>Ends at <input id="forced-window-end-time" type="time" value="00:00" required></label>
      </div>
      <p class="form-note">An end time of 00:00 ends at midnight at the start of that day. A window may run over midnight or over the end of the week.</p>
      <div class="action-buttons" id="forced-window-form-actions"></div>
    </form>`);
  const banner = textNode('p', '', 'forced-window-banner');
  banner.id = 'forced-window-banner';
  banner.setAttribute('role', 'status');
  banner.hidden = true;
  tabPanel('schedules').parentElement.prepend(banner);
  card.hidden = true;
  element('forced-window-start-day').value = '4';
  element('forced-window-end-day').value = '0';
  const save = actionButton('Save login', async () => {
    const password = element('forced-window-password').value;
    if (!password) throw new Error('Enter the router admin password first.');
    await request('/api/forced-windows/login', 'POST', {admin_password: password});
    element('forced-window-password').value = '';
    showMessage('Router login saved on the Pi.', 'success');
    await loadForcedWindows();
  });
  save.id = 'forced-window-login-save';
  const remove = actionButton('Remove login', async () => {
    await request('/api/forced-windows/login/clear', 'POST', {});
    await loadForcedWindows();
    // The button turns itself back on when this handler ends. Draw the card again after that.
    setTimeout(() => renderForcedWindows(forcedWindowData), 0);
  });
  remove.id = 'forced-window-login-remove';
  element('forced-window-login-actions').append(save, remove);
  const submit = actionButton('Save window', async () => {
    const form = element('forced-window-form');
    if (!form.reportValidity()) return;
    const values = {
      id: element('forced-window-id').value, startDay: element('forced-window-start-day').value,
      startTime: element('forced-window-start-time').value, endDay: element('forced-window-end-day').value,
      endTime: element('forced-window-end-time').value,
    };
    await request('/api/forced-windows', 'POST', windowFromForm(values));
    form.reset();
    element('forced-window-start-day').value = '4';
    element('forced-window-end-day').value = '0';
    await loadForcedWindows();
  });
  submit.id = 'forced-window-save';
  const end = actionButton('End window now', async () => {
    if (!await confirmWorkflow('End the window now? The router\'s Wi-Fi comes back on.')) return;
    await request('/api/forced-windows/end', 'POST', {});
    await loadForcedWindows();
  });
  end.id = 'forced-window-end';
  end.hidden = true;
  element('forced-window-form-actions').append(submit, end);
  element('forced-window-form').addEventListener('submit', event => { event.preventDefault(); submit.click(); });
  loadForcedWindows().catch(() => {});
  window.setInterval(() => { loadForcedWindows().catch(() => {}); }, 5000);
}
installForcedWindows();
