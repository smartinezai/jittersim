// Access restrictions are saved separately from experiment effects.
let policyVersion = null;
let accessState = null;
let siteRules = [];
let siteVersion = null;
let accessRead = null;
let accessReadAgain = false;

function openAccessRules() {
  showTab('access');
  element('access-summary').focus();
}

function updateAccessForms(data) {
  if (!formDrafts.has('policy-form')) {
    policyVersion = data.version;
    element('access-enabled').checked = data.options.enabled;
    element('access-admission').checked = data.options.reject_wifi_joins;
    element('dns-failure-percent').value = data.options.dns_failure_percent ?? 0;
  }
  siteRules = data.site_blocks || [];
  if (!formDrafts.has('site-rule-form')) siteVersion = data.version;
  populateAccessTargets(); renderSiteRules();
}

function populateAccessTargets() {
  const select = element('site-rule-target');
  const selected = select.value;
  const options = [new Option('Everyone on the test Wi-Fi', 'everyone')];
  devices.forEach(device => options.push(new Option(devicePickerLabel(device), 'device:' + device.id)));
  groups.forEach(group => options.push(new Option('Group: ' + group.name, 'group:' + group.id)));
  select.replaceChildren(...options);
  if (options.some(option => option.value === selected)) select.value = selected;
}

async function loadAccessStatus() {
  if (accessRead) {
    // A read already in flight may predate a save. Read once more after it.
    accessReadAgain = true;
    return accessRead;
  }
  accessRead = (async () => {
    do {
      accessReadAgain = false;
      await readAccessStatusOnce();
    } while (accessReadAgain);
  })();
  try { await accessRead; } finally { accessRead = null; }
}

async function readAccessStatusOnce() {
  try {
    accessState = await request('/api/access', 'GET');
    const summary = element('access-summary');
    summary.textContent = !accessState.available ? 'Configuration only. Install the current networking helper on the Pi to enforce access rules.' :
      accessState.error || (accessState.applied ? (accessState.enabled ?
        'Saved access rules are active. Resetting an experiment keeps these rules.' : 'Household access rules are disabled.') :
        'Saved rules differ from the rules currently applied. Apply access rules to update the network.');
    summary.dataset.state = accessState.error ? 'error' : accessState.applied ? 'applied' : 'pending';
    element('apply-access').disabled = !accessState.available;
    element('normal-access-button').disabled = !helperConfigured;
    element('normal-access-notice').hidden = !accessState.normal_access;
    element('disable-access').disabled = !accessState.available || !accessState.enabled;
    element('access-admission').disabled = !accessState.wifi_admission_available;
    const savedChance = accessState.saved_policy?.options?.dns_failure_percent ?? 0;
    const appliedChance = accessState.policy?.options?.dns_failure_percent;
    const dnsState = element('dns-failure-status');
    dnsState.textContent = appliedChance === undefined
      ? 'Applied DNS failure chance: unavailable.'
      : accessState.applied
        ? (accessState.enabled
          ? 'Applied DNS failure chance: ' + appliedChance + '%.'
          : 'Saved DNS failure chance: ' + savedChance + '%. Household access rules are disabled, so DNS failures are off.')
        : 'Saved DNS failure chance: ' + savedChance + '%. Applied: ' + appliedChance + '%. Apply access rules to change it.';
    element('admission-note').textContent = accessState.wifi_admission_available ?
      'Supported by this access point. Blocked devices are disconnected and refused when they try to rejoin.' :
      'MAC filtering blocks traffic after a device joins. This Wi-Fi setup cannot refuse joins.';
    renderAccessDevices();
    renderDeviceCards();
  } catch (error) {
    if (accessState) accessState.available = false;
    renderAccessDevices();
    element('access-summary').textContent = 'Access status is out of date. ' + error.message;
    element('access-summary').dataset.state = 'error';
    element('apply-access').disabled = true;
    element('normal-access-notice').hidden = true;
    element('disable-access').disabled = true;
    throw error;
  }
}

function renderSiteRules() {
  const container = element('site-rule-list');
  container.replaceChildren();
  if (!siteRules.length) container.append(textNode('p', 'No named site rules yet. Add a site or a collection of domains for a service.'));
  siteRules.forEach(rule => {
    const row = textNode('article', '', 'access-rule-row');
    const owner = rule.device_id ? (devices.some(d => d.id === rule.device_id)
      ? deviceTitle(devices.find(d => d.id === rule.device_id)) : rule.device_id) :
      rule.group_id ? 'Group: ' + (groups.find(g => g.id === rule.group_id)?.name || rule.group_id) : 'Everyone';
    const copy = textNode('div', '');
    copy.append(textNode('h3', rule.name), textNode('p', owner + ' · ' + (rule.enabled ? 'Enabled' : 'Disabled')),
      textNode('p', rule.domains.join(', ') + ' · Includes subdomains', 'access-domains'));
    const actions = textNode('div', '', 'access-actions');
    actions.append(actionButton('Edit ' + rule.name, () => editSiteRule(rule)),
      actionButton((rule.enabled ? 'Disable ' : 'Enable ') + rule.name, async () => {
        await request('/api/site-blocks', 'POST', {version:accessState.version, rule:{...rule, enabled:!rule.enabled}});
        await refresh(); showMessage('Site rule saved. Apply access rules to enforce the change.', 'success');
      }), actionButton('Delete ' + rule.name, async () => {
        if (!await confirmWorkflow('Delete the site rule “' + rule.name + '”? It remains active until the saved access rules are applied.')) return;
        await request('/api/site-blocks/delete', 'POST', {id:rule.id, version:accessState.version});
        await refresh();
      }));
    row.append(copy, actions); container.append(row);
  });
}

function editSiteRule(rule) {
  if (guardDraft('site-rule-form', () => editSiteRule(rule))) return;
  element('site-rule-id').value = rule.id;
  element('site-rule-name').value = rule.name;
  element('site-rule-domains').value = rule.domains.join('\n');
  element('site-rule-target').value = rule.device_id ? 'device:' + rule.device_id : rule.group_id ? 'group:' + rule.group_id : 'everyone';
  element('site-rule-enabled').checked = rule.enabled;
  siteVersion = accessState.version;
  element('site-rule-name').focus();
}

function renderAccessDevices() {
  const container = element('access-device-list');
  const applied = accessState && accessState.policy;
  const policy = applied || accessState?.saved_policy;
  if (!policy) return;
  container.replaceChildren();
  const all = new Map(devices.filter(d => d.mac_address).map(d => [d.mac_address, {...d, name: deviceTitle(d)}]));
  (lastConnectedDevices?.devices || []).forEach(d => {
    if (d.mac_address && !all.has(d.mac_address)) all.set(d.mac_address, {...d, name:d.display_name || d.hostname || d.mac_address});
  });
  if (!all.size) container.append(textNode('p', 'Saved or connected devices with a MAC address appear here. Use the manual lists above for an offline device.'));
  all.forEach((device, mac) => {
    const blocked = policy.options.enabled && (policy.mac.mode === 'denylist' ? policy.mac.denylist.includes(mac) :
      policy.mac.mode === 'allowlist' && !policy.mac.allowlist.includes(mac));
    const row = textNode('div', '', 'access-rule-row');
    const copy = textNode('div', '');
    copy.append(textNode('strong', device.name || mac), textNode('p', mac + ' · ' + (applied ? '' : 'Saved: ') +
      (blocked ? 'Network access blocked' : 'Network access allowed')));
    const button = actionButton((blocked ? 'Allow ' : 'Block ') + (device.name || mac), async () => {
      if (formDrafts.has('policy-form') || formDrafts.has('site-rule-form')) throw new Error('Save or discard unfinished access-rule forms first.');
      if (!await confirmWorkflow((blocked ? 'Allow' : 'Block') + ' network access for ' + (device.name || mac) + '? This takes effect immediately.')) return;
      await request('/api/access/device', 'POST', {version:accessState.version, mac_address:mac, blocked:!blocked});
      await refresh();
    });
    button.disabled = !accessState.available;
    row.append(copy, button); container.append(row);
  });
}

function deviceAccessText(device) {
  if (!accessState || !accessState.available) return 'Household access status unavailable';
  const policy = accessState.policy;
  if (!policy) return 'Household access rules have not been applied';
  if (accessState.error) return accessState.error;
  const pending = accessState.applied ? '' : ' · Saved changes waiting for Apply';
  if (!policy.options.enabled) return 'Household access rules disabled' + pending;
  const mac = device.mac_address;
  const blocked = policy.mac.mode === 'allowlist' ? !policy.mac.allowlist.includes(mac) :
    policy.mac.mode === 'denylist' && policy.mac.denylist.includes(mac);
  if (blocked) return 'Network access blocked by the MAC ' + (policy.mac.mode === 'allowlist' ? 'allow list' : 'block list') + pending;
  const client = policy.clients.find(item => item.id === device.id);
  const rules = policy.blocks.filter(rule => rule.enabled && ((!rule.device_id && !rule.group_id) || client?.block_ids.includes(rule.id)));
  const descriptions = rules.map(rule => rule.name);
  if (policy.websites.mode === 'allow_only' || policy.websites.blocked_domains.length) descriptions.unshift('Global website rules');
  return (descriptions.length ? 'Site restrictions: ' + descriptions.join(', ') : 'Network access allowed') + pending;
}

function installAccessControls() {
  const panic = actionButton('Normal access', async () => {
    panic.disabled = true;
    try {
      await request('/api/normal-access', 'POST', {});
      await refresh();
      showMessage('Normal access restored. All profiles and blocking are off. Saved settings are kept.', 'success');
    } finally { panic.disabled = false; }
  });
  panic.id = 'normal-access-button'; panic.classList.add('panic-button');
  panic.title = 'Panic button: immediately turn off all profiles and household blocking';
  panic.setAttribute('aria-describedby', 'normal-access-help');
  const panicHelp = textNode('span', 'Panic button. Turn off all profiles, schedules and household blocking immediately. Keep saved settings.', 'sr-only');
  panicHelp.id = 'normal-access-help';
  const notice = textNode('p', 'Normal access is on. Profiles, automation and household blocking are off. Re-enable them explicitly when you are ready.', 'normal-access-notice');
  notice.id = 'normal-access-notice'; notice.hidden = true; notice.setAttribute('role', 'status');
  noticeAnchor().before(notice);
  const card = element('policy-form').closest('section');
  const summary = textNode('p', 'Checking access rules…', 'access-summary');
  summary.id = 'access-summary'; summary.tabIndex = -1; summary.setAttribute('role', 'status');
  card.querySelector('h2').after(summary);
  const options = document.createElement('div'); options.className = 'access-options';
  options.innerHTML = `<label class="quota-check"><input id="access-enabled" type="checkbox" checked>Enable household access rules</label>
    <p class="form-note">Household rules filter test Wi-Fi DNS through the Pi's own resolver and stay on after Reset. A Pi upgraded from the Pi line starts with them off.</p>
    <label class="quota-check"><input id="access-admission" type="checkbox">Also refuse blocked devices when they join Wi-Fi</label>
    <p id="admission-note" class="form-note"></p>
    <label>DNS failure chance (0 to 100%)
      <input id="dns-failure-percent" type="number" min="0" max="100" step="1" value="0" required>
    </label>
    <p class="form-note">Each new DNS request from test Wi-Fi can get a quick connection error. Device and browser DNS caches may hide failures on later page visits. At 100%, test Wi-Fi name lookups will fail. This setting does not affect management traffic.</p>
    <p id="dns-failure-status" class="form-note" role="status">Checking the applied DNS failure chance…</p>`;
  element('policy-form').prepend(options);
  card.querySelector('.policy-note').textContent = 'These rules persist after an experiment resets. Domain blocks and global blocked domains include subdomains while household access rules are on. VPNs, encrypted DNS over HTTPS and another internet connection can bypass network-only filtering. Existing direct connections or a device’s cached DNS answer can continue until it looks up the name again. The managed browser proxy also checks hostnames and closes tunnels when rules change.';
  const actions = textNode('div', '', 'access-actions');
  const apply = actionButton('Apply access rules', async () => {
    if (formDrafts.has('policy-form') || formDrafts.has('site-rule-form')) throw new Error('Save or discard unfinished access-rule edits before applying.');
    if (accessState.saved_policy?.options?.enabled &&
        accessState.saved_policy.options.dns_failure_percent === 100 &&
        !await confirmWorkflow('A 100% DNS failure chance will make every new test-network name lookup fail. Apply this setting?')) return;
    await request('/api/access/apply', 'POST', {version:accessState.version});
    await loadAccessStatus(); showMessage('Access rules applied. Experiment settings were preserved.', 'success');
  }); apply.id = 'apply-access'; apply.classList.add('primary');
  const disable = actionButton('Disable household restrictions', async () => {
    if (!await confirmWorkflow('Disable all household device and site restrictions? Saved lists will remain available. Experiment profiles can still restrict traffic.')) return;
    await request('/api/access/disable', 'POST', {version:accessState.version, confirmed:true}); await refresh();
  }); disable.id = 'disable-access';
  actions.append(apply, disable, panic, panicHelp); card.append(actions);
  const devicesList = textNode('div', ''); devicesList.id = 'access-device-list';
  card.append(textNode('h3', 'Device access'), devicesList);
  const sites = document.createElement('section'); sites.className = 'card';
  sites.innerHTML = `<h2>Block sites and services</h2><p>A service can contain several domains. These restrictions apply independently of profiles.</p>
    <form id="site-rule-form"><input id="site-rule-id" type="hidden">
      <div class="fields"><label>Rule name<input id="site-rule-name" required maxlength="100" placeholder="Streaming on the kids’ devices"></label>
      <label>Applies to<select id="site-rule-target" aria-label="Applies to"></select></label></div>
      <label>Domains to block<textarea id="site-rule-domains" required placeholder="example.com&#10;media.example.net"></textarea></label>
      <p class="form-note">Enter domain names separated by commas or new lines. Each name includes all its subdomains. Up to 64 domains per rule and 512 across 32 rules.</p>
      <label class="quota-check"><input id="site-rule-enabled" type="checkbox" checked>Rule enabled</label>
      <div class="access-actions"><button type="submit" class="primary">Save site rule</button><button type="button" id="site-rule-new">Clear form</button></div>
    </form><div id="site-rule-list"></div>`;
  tabPanel('access').append(sites);
  element('site-rule-new').addEventListener('click', () => {
    const clear = () => { element('site-rule-form').reset(); element('site-rule-id').value = ''; siteVersion = accessState?.version; };
    if (!guardDraft('site-rule-form', clear)) clear();
  });
  element('site-rule-form').addEventListener('submit', async event => {
    event.preventDefault();
    try {
      const target = element('site-rule-target').value;
      const id = element('site-rule-id').value || 'block-' + Date.now().toString(36) + '-' + Math.random().toString(36).slice(2,8);
      const rule = {id, name:element('site-rule-name').value.trim(), enabled:element('site-rule-enabled').checked,
        domains:Array.from(new Set(element('site-rule-domains').value.split(/[\s,]+/).map(d => d.trim().toLowerCase()).filter(Boolean))),
        device_id:target.startsWith('device:') ? target.slice(7) : null, group_id:target.startsWith('group:') ? target.slice(6) : null};
      await request('/api/site-blocks', 'POST', {version:siteVersion, rule});
      element('site-rule-id').value = id;
      await refresh(); showMessage('Site rule saved. Apply access rules to enforce it.', 'success');
    } catch (error) { formError(element('site-rule-form'), error.message); showMessage(error.message, 'error'); }
  });
  const shortcut = actionButton('Manage device and site access', openAccessRules);
  shortcut.classList.add('access-shortcut'); tabPanel('devices').prepend(shortcut);
}
installAccessControls();

const impairmentHelp = {
  delay:'Adds this much waiting time in this direction. 100 ms upload plus 100 ms download adds roughly 200 ms to a round trip. 0 adds no delay.',
  jitter:'Varies the waiting time from packet to packet. 20 ms produces an uneven connection. 0 keeps the delay steady. This differs from periodically rerolling the profile.',
  loss:'Drops this percentage of packets on average. 1% loses about 1 in 100 packets. 0 loses none and 100% prevents traffic in this direction.',
  bandwidth:'Limits speed in this direction. 1000 kbit/s is 1 Mbit/s. Leave blank for no JitterSim speed limit. Real network speed can still be lower.',
  reorder:'Lets this percentage of packets arrive out of order when delay is present. It can exercise recovery in an app. 0 keeps packets in order.',
  duplicate:'Sends an extra copy of this percentage of packets. 0 sends no duplicates.',
  corrupt:'Changes data in this percentage of packets. Receivers may discard damaged packets. 0 leaves the contents unchanged.'
};

function installFieldHelp() {
  for (const direction of ['upload', 'download']) for (const [name, help] of Object.entries(impairmentHelp)) {
    const input = element(direction + '-' + name); const label = input.closest('label');
    // The stack's forms already explain these fields.
    if (label.querySelector('.help-icon')) continue;
    const icon = textNode('span', '?', 'help-icon'); icon.dataset.help = help;
    icon.setAttribute('aria-label', direction + ' ' + name + ' help'); label.insertBefore(icon, input);
  }
  for (const [id, help] of Object.entries({
    'profile-seed':'The same seed repeats the sequence of random choices with the same settings and timing. Leave blank for a different sequence.',
    'profile-website-mode':'Allow only listed domains limits a device while this profile runs. Household site blocks are separate and stay active after a profile changes.',
    'profile-website-domains':'These experimental profile rules use resolved server addresses. For persistent hostname and subdomain blocking, use Access rules.',
    'site-rule-domains':'A rule for example.com also blocks video.example.com. It does not block unrelated-example.com. Enter names without https:// or a page path.',
    'website-blocklist':'Block globally on the Traffic page adds names here. While household access rules are on example.com also blocks video.example.com for every test Wi-Fi device. Unblock removes the exact name and its subdomains resolve again.'
  })) {
    const input = element(id);
    if (input.closest('label') && input.closest('label').querySelector('.help-icon')) continue;
    const icon = textNode('span', '?', 'help-icon'); icon.dataset.help = help;
    icon.setAttribute('aria-label', id === 'site-rule-domains' ? 'Blocked domains help' : id === 'website-blocklist' ? 'Global blocked domains help' : id.replaceAll('-', ' ') + ' help'); input.before(icon);
  }
  let active = null; let pinned = false; let closeTimer = null;
  function close() { if (active) { active.tip.hidden = true; active.button.setAttribute('aria-expanded', 'false'); } active = null; pinned = false; }
  function show(item, pin = false) {
    clearTimeout(closeTimer);
    if (active && active !== item) close();
    active = item; pinned = pin; item.tip.hidden = false; item.button.setAttribute('aria-expanded', 'true');
    position(item);
  }
  function position(item) {
    const anchor = item.button.getBoundingClientRect(); const size = item.tip.getBoundingClientRect();
    const field = item.button.closest('label')?.getBoundingClientRect() || anchor;
    item.tip.style.left = Math.max(12, Math.min(anchor.left, innerWidth - size.width - 12)) + 'px';
    item.tip.style.top = Math.max(12, Math.min(anchor.top >= size.height + 20 ? anchor.top - size.height - 8 : field.bottom + 8, innerHeight - size.height - 12)) + 'px';
  }
  document.querySelectorAll('.help-icon[data-help]').forEach((old, index) => {
    // The sidebar's action buttons keep the Pi line's pinned tips next to the sidebar.
    if (old.closest('.sidebar-action-row')) return;
    const button = textNode('button', '?', 'help-icon field-help'); button.type = 'button';
    button.setAttribute('aria-label', old.getAttribute('aria-label') || 'Field help'); button.setAttribute('aria-expanded', 'false');
    const tip = textNode('div', old.dataset.help.replace(/\s+/g, ' ').trim(), 'field-tooltip');
    tip.id = 'field-help-' + index; tip.setAttribute('role', 'tooltip'); tip.hidden = true;
    button.setAttribute('aria-controls', tip.id); button.setAttribute('aria-describedby', tip.id);
    const label = old.closest('label');
    if (label) {
      const copy = label.cloneNode(true); copy.querySelectorAll('input, select, textarea, .help-icon').forEach(node => node.remove());
      const input = label.querySelector('input, select, textarea');
      if (input) { input.setAttribute('aria-label', copy.textContent.replace(/\s+/g, ' ').trim()); input.setAttribute('aria-describedby', tip.id); }
    }
    old.replaceWith(button); document.body.append(tip);
    if (label && button.parentElement === label) {
      const heading = textNode('span', '', 'field-label');
      while (label.firstChild && label.firstChild !== button) heading.append(label.firstChild);
      button.before(heading); heading.append(button);
    }
    const item = {button, tip};
    button.addEventListener('pointerenter', event => { if (event.pointerType === 'mouse' && !pinned) show(item); });
    button.addEventListener('focus', () => show(item));
    button.addEventListener('click', event => { event.preventDefault(); active === item && pinned ? close() : show(item, true); });
    const leave = () => { if (!pinned) closeTimer = setTimeout(() => {
      if (document.activeElement !== button && !tip.matches(':hover')) close();
    }, 120); };
    button.addEventListener('pointerleave', leave); button.addEventListener('blur', leave);
    tip.addEventListener('pointerenter', () => clearTimeout(closeTimer)); tip.addEventListener('pointerleave', leave);
  });
  document.addEventListener('keydown', event => { if (event.key === 'Escape' && active) { close(); event.preventDefault(); } });
  document.addEventListener('pointerdown', event => { if (active && !active.button.contains(event.target) && !active.tip.contains(event.target)) close(); });
  const reposition = () => { if (active) position(active); };
  window.addEventListener('resize', reposition); window.addEventListener('scroll', reposition, true);
}
installFieldHelp();

const profileDirections = textNode('div', '', 'profile-directions');
const profileForm = element('profile-form');
const directionHeadings = Array.from(profileForm.children).filter(node => node.tagName === 'H3');
if (directionHeadings.length === 2) {
  directionHeadings[0].before(profileDirections);
  directionHeadings.forEach(heading => {
    const fields = heading.nextElementSibling;
    const section = textNode('section', '', 'profile-direction');
    const description = textNode('p', heading.textContent === 'Upload' ? 'From the device to the internet' : 'From the internet to the device', 'form-note');
    section.append(heading, description, fields); profileDirections.append(section);
  });
}
