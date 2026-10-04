// Trust this Pi: a device installs the local certificate once to stop browser warnings.
// Names stay neutral. The details and downloads require accepted device access.
function fillTrustSteps(id, steps) {
  element(id).replaceChildren(...steps.map(text => {
    const item = document.createElement('li');
    item.textContent = text;
    return item;
  }));
}

// The one address. The remembered sign-in belongs to one browser address, so a page opened
// at an IP address or an older name offers the one address and says to sign in once there.
function showOneAddress(data) {
  let target;
  try {
    target = new URL(data.address);
  } catch (error) {
    return;
  }
  if (target.protocol !== 'https:') return;
  element('one-address-url').textContent = data.address;
  const current = window.location.hostname.toLowerCase();
  const nudge = element('one-address-nudge');
  nudge.hidden = current === target.hostname.toLowerCase();
  if (!nudge.hidden) {
    element('one-address-current').textContent = window.location.host;
    element('one-address-link').href = target.origin + '/';
  }
  const announce = data.announce || {};
  const problem = element('one-address-problem');
  const healthy = announce.state === 'published' || announce.state === 'partial';
  problem.hidden = healthy || !announce.message;
  problem.textContent = healthy ? '' : announce.message + ' Diagnostics shows what to do.';
}

let trustUiGeneration = 0;
let trustLoad = null;
let trustLoaded = null;
let trustAvailable = false;
const trustDownloads = new Set();

function trustContext() {
  return {revision: adminTokenRevision, generation: trustUiGeneration,
    token: element('admin-token').value.trim()};
}

function trustContextCurrent(context) {
  return context.revision === adminTokenRevision && context.generation === trustUiGeneration &&
    context.token === element('admin-token').value.trim() && Boolean(context.token) &&
    adminTokenVerified && !adminTokenRejected && !adminTokenRefused;
}

function clearTrustDetails() {
  trustUiGeneration += 1;
  trustLoaded = null;
  trustAvailable = false;
  if (trustLoad) trustLoad.controller.abort();
  trustLoad = null;
  trustDownloads.forEach(controller => controller.abort());
  trustDownloads.clear();
  ['trust-address', 'trust-fingerprint', 'trust-status', 'one-address-url',
    'one-address-current', 'one-address-problem'].forEach(id => { element(id).textContent = ''; });
  ['trust-steps-mac', 'trust-steps-iphone'].forEach(id => element(id).replaceChildren());
  element('one-address-link').removeAttribute('href');
  element('one-address-nudge').hidden = true;
  element('one-address-problem').hidden = true;
  element('trust-download-profile').disabled = true;
  element('trust-download-crt').disabled = true;
}

privateDataLockHooks.push(clearTrustDetails);

async function loadTrustDetails() {
  const context = trustContext();
  if (!trustContextCurrent(context)) return;
  if (trustLoaded && trustContextCurrent(trustLoaded)) return;
  if (trustLoad && trustContextCurrent(trustLoad.context)) return;
  const owner = {context, controller: new AbortController()};
  if (trustLoad) trustLoad.controller.abort();
  trustLoad = owner;
  const status = element('trust-status');
  let data;
  try {
    data = await request('/api/trust', 'GET', undefined, {signal: owner.controller.signal});
  } catch (error) {
    if (trustContextCurrent(context)) {
      status.textContent = 'Could not load the certificate details: ' + error.message;
    }
    return;
  } finally {
    if (trustLoad === owner) trustLoad = null;
  }
  if (!trustContextCurrent(context)) return;
  trustLoaded = context;
  trustAvailable = Boolean(data.available);
  element('trust-address').textContent = data.address;
  showOneAddress(data);
  fillTrustSteps('trust-steps-mac', data.steps.mac);
  fillTrustSteps('trust-steps-iphone', data.steps.iphone);
  element('trust-fingerprint').textContent = data.fingerprint || 'not available';
  element('trust-download-profile').disabled = !data.available;
  element('trust-download-crt').disabled = !data.available;
  status.textContent = data.available
    ? 'Ready. The certificate permits local names and addresses in the configured network ranges. Check the fingerprint before installing it.'
    : 'The certificate is not available yet. Run the installer on the Pi again.';
}

async function downloadTrustFile(path, fileName, button) {
  const context = trustContext();
  if (!trustContextCurrent(context) || !trustAvailable || button.disabled) return;
  const status = element('trust-status');
  const controller = new AbortController();
  trustDownloads.add(controller);
  const timeout = window.setTimeout(() => controller.abort(), 15000);
  button.disabled = true;
  try {
    const response = await fetch(path, {
      headers: { Authorization: 'Bearer ' + context.token }, cache: 'no-store', signal: controller.signal
    });
    if (!trustContextCurrent(context)) return;
    if (!response.ok) {
      if (response.status === 401 || response.status === 403) {
        adminTokenRejected = true;
        adminTokenRefused = response.status === 401;
        lockDashboardAccess('Certificate download access was refused. Check the token and device access again.');
      }
      throw new Error('The certificate download was refused.');
    }
    const reader = response.body && response.body.getReader();
    if (!reader) throw new Error('This browser could not read the certificate download.');
    const chunks = [];
    let size = 0;
    try {
      while (true) {
        const part = await reader.read();
        if (!trustContextCurrent(context) || controller.signal.aborted) return;
        if (part.done) break;
        size += part.value.byteLength;
        if (size > 65536) throw new Error('The certificate download is too large.');
        chunks.push(part.value);
      }
    } finally {
      reader.cancel().catch(() => {});
    }
    if (!trustContextCurrent(context) || controller.signal.aborted) return;
    const url = URL.createObjectURL(new Blob(chunks));
    const link = document.createElement('a');
    link.href = url;
    link.download = fileName;
    link.click();
    window.setTimeout(() => URL.revokeObjectURL(url), 1000);
    status.textContent = 'Downloaded ' + fileName + '. Follow the steps for your device.';
  } catch (error) {
    if (trustContextCurrent(context)) {
      status.textContent = controller.signal.aborted ? 'The certificate download timed out. Try again.' : error.message;
    }
  } finally {
    window.clearTimeout(timeout);
    trustDownloads.delete(controller);
    if (trustContextCurrent(context)) button.disabled = !trustAvailable;
  }
}

element('trust-download-profile').addEventListener('click', event =>
  downloadTrustFile('/api/trust/profile.mobileconfig', 'local-dashboard.mobileconfig', event.currentTarget));
element('trust-download-crt').addEventListener('click', event =>
  downloadTrustFile('/api/trust/ca.crt', 'local-dashboard.crt', event.currentTarget));
