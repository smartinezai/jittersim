// Trust this Pi: a device installs the local certificate once to stop browser warnings.
// Names here stay neutral. Anyone on the test Wi-Fi can read this page before signing in.
function fillTrustSteps(id, steps) {
  element(id).replaceChildren(...steps.map(text => {
    const item = document.createElement('li');
    item.textContent = text;
    return item;
  }));
}

async function loadTrustDetails() {
  const status = element('trust-status');
  let data;
  try {
    data = await request('/api/trust', 'GET');
  } catch (error) {
    status.textContent = 'Could not load the certificate details: ' + error.message;
    return;
  }
  element('trust-address').textContent = data.address;
  fillTrustSteps('trust-steps-mac', data.steps.mac);
  fillTrustSteps('trust-steps-iphone', data.steps.iphone);
  element('trust-fingerprint').textContent = data.fingerprint || 'not available';
  element('trust-download-profile').disabled = !data.available;
  element('trust-download-crt').disabled = !data.available;
  status.textContent = data.available
    ? 'Ready. The certificate only lets this device trust the Pi dashboard.'
    : 'The certificate is not available yet. Run the installer on the Pi again.';
}

async function downloadTrustFile(path, fileName, button) {
  const status = element('trust-status');
  const token = element('admin-token').value.trim();
  button.disabled = true;
  try {
    const response = await fetch(path, {
      headers: { Authorization: 'Bearer ' + token }, cache: 'no-store'
    });
    if (!response.ok) {
      const problem = await response.json().catch(() => ({}));
      throw new Error(problem.error || 'The download was refused.');
    }
    const url = URL.createObjectURL(await response.blob());
    const link = document.createElement('a');
    link.href = url;
    link.download = fileName;
    link.click();
    window.setTimeout(() => URL.revokeObjectURL(url), 1000);
    status.textContent = 'Downloaded ' + fileName + '. Follow the steps for your device.';
  } catch (error) {
    status.textContent = error.message;
  } finally {
    button.disabled = false;
  }
}

element('trust-download-profile').addEventListener('click', event =>
  downloadTrustFile('/api/trust/profile.mobileconfig', 'local-dashboard.mobileconfig', event.currentTarget));
element('trust-download-crt').addEventListener('click', event =>
  downloadTrustFile('/api/trust/ca.crt', 'local-dashboard.crt', event.currentTarget));
