// Address changes: saved devices that came back with a new private Wi-Fi address.
// The server finds them and checks every answer again. This file only shows the
// suggestions and the history and sends Yes, No, Not this device and Undo.
const ADDRESS_FOLLOW_POLL_MS = 15000;
let addressFollowLoadedAt = 0;
let addressFollowLoading = false;
let addressFollowData = null;

function addressFollowNode(tag, text, className) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  if (className) node.className = className;
  return node;
}

function addressFollowButton(label, className) {
  const button = addressFollowNode("button", label, className);
  button.type = "button";
  return button;
}

function clearAddressFollow() {
  addressFollowData = null;
  addressFollowLoadedAt = 0;
  const list = element("address-follow-list");
  list.replaceChildren(document.createTextNode("Check the admin token to look for address changes."));
  delete list.dataset.renderKey;
  const history = element("address-follow-history");
  history.replaceChildren(addressFollowNode("p", "No moves yet.", "form-note"));
  delete history.dataset.renderKey;
  element("address-follow-mode").disabled = true;
  element("address-follow-save").disabled = true;
  element("status-address-follow").hidden = true;
  element("status-address-follow-link").textContent = "";
}

function addressFollowMoves(item) {
  const parts = [];
  if (item.moves.profile) parts.push("its profile");
  if (item.moves.name) parts.push("its name");
  if (item.moves.protection) parts.push("its protection");
  return parts.length ? parts.join(" and ") : "its saved entries";
}

function addressFollowStays(item) {
  const notes = [];
  if (item.stays.dashboard_access) {
    notes.push("Its saved dashboard access stays with the old address. Open the dashboard on that " +
      "device and use the move under Dashboard access.");
  }
  if (item.stays.mac_list) {
    notes.push("The old address is on the MAC address list of the access rules. Change that list by hand.");
  }
  return notes;
}

function addressFollowQuestion(item) {
  return "This looks like " + item.name + " (new private address " + item.new_mac + "). Move its settings?";
}

function renderAddressFollowSuggestions(data) {
  const list = element("address-follow-list");
  const suggestions = data.suggestions || [];
  replaceIfChanged(list, JSON.stringify([data.mode, suggestions]), () => {
    list.replaceChildren();
    if (data.mode === "off") {
      list.appendChild(addressFollowNode("p", "Off. Choose Ask or Automatic to look for address changes.", "form-note"));
      return;
    }
    if (!suggestions.length) {
      list.appendChild(addressFollowNode("p", "No saved device has come back with a new address.", "form-note"));
      return;
    }
    suggestions.forEach((item) => {
      const row = addressFollowNode("div", undefined, "address-follow-row");
      row.dataset.key = item.old_mac + "|" + item.new_mac;
      row.appendChild(addressFollowNode("p", addressFollowQuestion(item), "address-follow-question"));
      const evidence = addressFollowNode("ul", undefined, "address-follow-evidence");
      (item.evidence || []).forEach((sentence) => evidence.appendChild(addressFollowNode("li", sentence)));
      row.appendChild(evidence);
      row.appendChild(addressFollowNode("p", "Yes moves " + addressFollowMoves(item) + " to the new address.", "form-note"));
      addressFollowStays(item).forEach((note) => row.appendChild(addressFollowNode("p", note, "form-note")));
      if (item.automatic_waiting) row.appendChild(addressFollowNode("p", item.automatic_waiting + " It waits for you.", "form-note"));
      const actions = addressFollowNode("div", undefined, "connected-actions");
      const yes = addressFollowButton("Yes, move its settings", "primary");
      const no = addressFollowButton("No");
      const never = addressFollowButton("Not this device");
      yes.setAttribute("aria-label", "Yes, move the settings of " + item.name);
      no.setAttribute("aria-label", "No, not now for " + item.name);
      never.setAttribute("aria-label", "Not " + item.name + ", hide this suggestion");
      yes.addEventListener("click", () => acceptAddressFollow(item, yes));
      no.addEventListener("click", () => dismissAddressFollow(item, "later", no));
      never.addEventListener("click", () => dismissAddressFollow(item, "never", never));
      actions.append(yes, no, never);
      row.appendChild(actions);
      list.appendChild(row);
    });
  });
}

function addressFollowWhen(seconds) {
  return new Date(seconds * 1000).toLocaleString("en-GB", {dateStyle: "medium", timeStyle: "short"});
}

function renderAddressFollowHistory(data) {
  const history = element("address-follow-history");
  const entries = data.history || [];
  replaceIfChanged(history, JSON.stringify(entries), () => {
    history.replaceChildren();
    if (!entries.length) {
      history.appendChild(addressFollowNode("p", "No moves yet.", "form-note"));
      return;
    }
    const list = addressFollowNode("ul", undefined, "address-follow-history");
    entries.forEach((entry) => {
      const row = addressFollowNode("li");
      row.dataset.key = String(entry.id);
      const moved = [entry.moved_profile ? "profile" : null, entry.moved_label ? "name" : null,
        entry.moved_protection ? "protection" : null].filter(Boolean).join(", ");
      const how = entry.mode === "automatic" ? "Automatic" : "After Yes";
      const state = entry.undone_at ? " · Undone" : entry.replaced
        ? " · Replaced by a move under Dashboard access" : "";
      row.appendChild(addressFollowNode("span", addressFollowWhen(entry.happened_at) + " · " + entry.name +
        ": " + entry.old_mac + " to " + entry.new_mac + " · " + moved + " · " + how + state + " "));
      if (entry.can_undo) {
        const undo = addressFollowButton("Undo");
        undo.setAttribute("aria-label", "Undo the move of " + entry.name);
        undo.addEventListener("click", () => undoAddressFollow(entry, undo));
        row.appendChild(undo);
      }
      list.appendChild(row);
    });
    history.appendChild(list);
  });
}

function renderAddressFollowStatus(data) {
  const count = data && data.mode !== "off" ? (data.suggestions || []).length : 0;
  element("status-address-follow").hidden = count === 0;
  element("status-address-follow-link").textContent = count === 0 ? "" :
    count === 1 ? "1 device has a new address. Review it" : count + " devices have a new address. Review them";
}

function renderAddressFollow(data) {
  addressFollowData = data;
  const mode = element("address-follow-mode");
  if (document.activeElement !== mode) mode.value = data.mode;
  mode.disabled = false;
  element("address-follow-save").disabled = false;
  renderAddressFollowSuggestions(data);
  renderAddressFollowHistory(data);
  renderAddressFollowStatus(data);
}

async function loadAddressFollow() {
  if (addressFollowLoading || !adminTokenVerified) return;
  addressFollowLoading = true;
  const revision = adminTokenRevision;
  try {
    const data = await request("/api/address-follow", "GET");
    if (revision !== adminTokenRevision || !adminTokenVerified) return;
    addressFollowLoadedAt = Date.now();
    renderAddressFollow(data);
  } catch (error) {
    if (revision !== adminTokenRevision || !adminTokenVerified) return;
    const list = element("address-follow-list");
    if (!interactionInside(list)) {
      list.replaceChildren(addressFollowNode("p", "Could not look for address changes: " + error.message, "form-note"));
      delete list.dataset.renderKey;
    }
  } finally {
    addressFollowLoading = false;
  }
}

async function afterAddressFollowChange(message) {
  showMessage(message, "success");
  addressFollowLoadedAt = 0;
  await loadAddressFollow();
  // The row that held the button is gone, so focus goes to the card.
  if (!document.activeElement || document.activeElement === document.body) element("address-follow-card").focus();
}

async function acceptAddressFollow(item, button) {
  const protection = item.moves.protection;
  const message = "Move " + addressFollowMoves(item) + " of " + item.name + " to the new address " +
    item.new_mac + "?" + (protection ? " Protection moves too: the new address is then never shaped " +
    "and can open this dashboard. The admin token is still needed." : "") +
    " Only say yes if this is really that device. You can undo this for 7 days.";
  if (!await confirmFromButton(button, message, {title: "Move " + item.name + "?",
      confirmLabel: "Move settings", danger: protection})) return;
  try {
    const result = await request("/api/address-follow/accept", "POST",
      {old_mac: item.old_mac, new_mac: item.new_mac, move_protection: protection});
    await afterAddressFollowChange("Moved. " + item.name + " uses its new address now. " +
      (result.warning ? result.warning + " " : "") + "Undo is in the address change history.");
  } catch (error) {
    showMessage(error.message, "error");
    addressFollowLoadedAt = 0;
    await loadAddressFollow();
  } finally {
    if (button.isConnected) setButtonBusy(button, false);
  }
}

async function dismissAddressFollow(item, scope, button) {
  setButtonBusy(button, true);
  try {
    await request("/api/address-follow/dismiss", "POST",
      {old_mac: item.old_mac, new_mac: item.new_mac, scope: scope});
    await afterAddressFollowChange(scope === "never"
      ? "Hidden. This address is not suggested as " + item.name + " for 90 days."
      : "Hidden for a day. Nothing was moved.");
  } catch (error) {
    showMessage(error.message, "error");
  } finally {
    if (button.isConnected) setButtonBusy(button, false);
  }
}

async function undoAddressFollow(entry, button) {
  if (!await confirmFromButton(button, "Undo the move of " + entry.name + "? Its settings go back to " +
      entry.old_mac + " if nothing changed since the move.",
      {title: "Undo this move?", confirmLabel: "Undo move"})) return;
  try {
    const result = await request("/api/address-follow/undo", "POST", {id: entry.id});
    await afterAddressFollowChange("Move undone. " + entry.name + " uses its old address again." +
      (result.warning ? " " + result.warning : ""));
  } catch (error) {
    showMessage(error.message, "error");
    addressFollowLoadedAt = 0;
    await loadAddressFollow();
  } finally {
    if (button.isConnected) setButtonBusy(button, false);
  }
}

element("address-follow-save").addEventListener("click", async () => {
  const button = element("address-follow-save");
  const mode = element("address-follow-mode").value;
  if (mode === "automatic" && (!addressFollowData || addressFollowData.mode !== "automatic")) {
    if (!await confirmFromButton(button, "Automatic moves a saved device's profile and name to its new " +
        "address when the evidence is strong. Protection and dashboard access never move automatically. " +
        "Any device can send a copied device name, so a wrong match can move a profile onto " +
        "another device. Every move shows in the history with Undo.",
        {title: "Turn on automatic moves?", confirmLabel: "Turn on"})) return;
  }
  setButtonBusy(button, true);
  try {
    await request("/api/address-follow/settings", "POST", {mode: mode});
    await afterAddressFollowChange(mode === "off" ? "Address changes are off. Saved device names from the Wi-Fi were removed."
      : mode === "automatic" ? "Automatic moves are on for strong evidence."
      : "Address changes ask before moving anything.");
  } catch (error) {
    showMessage(error.message, "error");
  } finally {
    setButtonBusy(button, false);
  }
});

element("status-address-follow-link").addEventListener("click", () => {
  // The page changes on the hash. The card gets focus once it is shown.
  window.setTimeout(() => element("address-follow-card").focus(), 0);
});

connectedDataHooks.push(() => {
  if (Date.now() - addressFollowLoadedAt >= ADDRESS_FOLLOW_POLL_MS) loadAddressFollow();
});
privateDataLockHooks.push(() => {
  if (!adminTokenVerified) clearAddressFollow();
});
