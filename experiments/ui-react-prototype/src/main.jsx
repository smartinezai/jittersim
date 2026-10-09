// Devices page prototype. It reuses the class names and the CSS of the real dashboard so that
// the comparison with the vanilla page is about the code, not the paint. Fixture data only.
import { render } from "preact";
import { useState, useMemo } from "preact/hooks";

const PROFILES = [
  { id: "normal", name: "Normal connection", summary: "no impairment" },
  { id: "slow", name: "Slow connection", summary: "↓ 300 ms" },
  { id: "lossy", name: "Hotel Wi-Fi", summary: "↓ 8% loss · ↑ 120 ms jitter" },
];
const CLIENTS = [
  { ip: "192.168.50.184", mac: "02:00:00:00:00:01", profile: "slow", seen: "12 s ago" },
  { ip: "192.168.50.201", mac: "02:00:00:00:00:02", profile: "", seen: "just now" },
  { ip: "192.168.50.77", mac: "02:00:00:00:00:03", profile: "", seen: "41 s ago", protected: true },
];

function Client({ client, onChange }) {
  const [pending, setPending] = useState(client.profile);
  const dirty = pending !== client.profile;
  return (
    <div class={"connected-row" + (client.protected ? " protected" : "")}>
      <span class="connected-dot" aria-hidden="true" />
      <div>
        <p class="connected-address">
          {client.ip}
          {client.protected && <span class="protected-badge">Protected</span>}
        </p>
        <p class="connected-meta">{client.mac} · seen {client.seen}</p>
      </div>
      <span class="connected-state">{client.profile ? "limited" : "normal"}</span>
      <div class="connected-controls">
        <select
          aria-label={"Profile for " + client.ip}
          disabled={client.protected}
          value={pending}
          onChange={(event) => setPending(event.currentTarget.value)}
        >
          <option value="">No profile</option>
          {PROFILES.map((p) => <option value={p.id}>{p.name}</option>)}
        </select>
        <button class="primary" type="button" disabled={client.protected || !dirty} onClick={() => onChange(client.ip, pending)}>
          Save
        </button>
      </div>
    </div>
  );
}

function App() {
  const [clients, setClients] = useState(CLIENTS);
  const [empty, setEmpty] = useState(false);
  const shown = empty ? [] : clients;
  const limited = useMemo(() => shown.filter((c) => c.profile).length, [shown]);
  const save = (ip, profile) => setClients(clients.map((c) => (c.ip === ip ? { ...c, profile } : c)));
  return (
    <section class="card connected-card" aria-labelledby="clients-title">
      <div class="section-heading">
        <h2 id="clients-title">Connected clients</h2>
        <div class="connected-actions">
          <span class="count-badge">{shown.length} connected</span>
          <button type="button" onClick={() => setEmpty(!empty)}>{empty ? "Show devices" : "Show empty state"}</button>
        </div>
      </div>
      <p class="connected-refresh-status">{limited} of {shown.length} devices have a profile.</p>
      {shown.length === 0 ? (
        <p class="device-empty">No devices on the test Wi-Fi yet. Connect a phone or laptop to see it here.</p>
      ) : (
        <div class="connected-list">
          {shown.map((c) => <Client key={c.ip} client={c} onChange={save} />)}
        </div>
      )}
    </section>
  );
}

render(<App />, document.getElementById("root"));
