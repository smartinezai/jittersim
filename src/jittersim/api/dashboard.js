
    const element = (id) => document.getElementById(id);

    let pendingFormSubmitButton = null;
    let messageSerial = 0;

    function setupSidebarActionHelp() {
      const tooltip = document.createElement("div");
      tooltip.id = "sidebar-action-help";
      tooltip.className = "sidebar-action-help";
      tooltip.setAttribute("role", "tooltip");
      tooltip.hidden = true;
      document.body.appendChild(tooltip);
      let active = null;
      let hideTimer = null;

      function cancelHide() {
        clearTimeout(hideTimer);
        hideTimer = null;
      }

      function hide() {
        cancelHide();
        if (active) active.removeAttribute("aria-describedby");
        active = null;
        tooltip.hidden = true;
        window.removeEventListener("scroll", dismissOnScroll, true);
        window.removeEventListener("resize", hide);
        if (window.visualViewport) {
          window.visualViewport.removeEventListener("resize", hide);
          window.visualViewport.removeEventListener("scroll", dismissOnScroll);
        }
      }

      function dismissOnScroll(event) {
        if (event.target === tooltip) return;
        if (active && active.matches(":hover, :focus")) position();
        else hide();
      }

      function scheduleHide() {
        cancelHide();
        hideTimer = setTimeout(() => {
          if (active && (active.matches(":hover, :focus") || tooltip.matches(":hover"))) return;
          hide();
        }, 150);
      }

      function position() {
        const anchor = active.getBoundingClientRect();
        const viewport = window.visualViewport;
        const leftEdge = viewport ? viewport.offsetLeft : 0;
        const topEdge = viewport ? viewport.offsetTop : 0;
        const width = viewport ? viewport.width : window.innerWidth;
        const height = viewport ? viewport.height : window.innerHeight;
        const margin = Math.min(16, width / 4, height / 4);
        const gap = 8;
        if (anchor.bottom <= topEdge || anchor.top >= topEdge + height) {
          hide();
          return;
        }
        tooltip.style.width = Math.min(352, width - margin * 2) + "px";
        tooltip.style.maxHeight = (height - margin * 2) + "px";
        tooltip.hidden = false;
        const box = tooltip.getBoundingClientRect();
        const rightEdge = leftEdge + width - margin;
        const bottomEdge = topEdge + height - margin;
        let left = anchor.right + gap;
        let top = anchor.top;
        if (left + box.width > rightEdge) {
          left = anchor.right - box.width;
          top = anchor.bottom + gap;
          if (top + box.height > bottomEdge) top = anchor.top - gap - box.height;
        }
        tooltip.style.left = Math.max(leftEdge + margin, Math.min(left, rightEdge - box.width)) + "px";
        tooltip.style.top = Math.max(topEdge + margin, Math.min(top, bottomEdge - box.height)) + "px";
      }

      function show(trigger) {
        hide();
        active = trigger;
        tooltip.textContent = trigger.dataset.help;
        tooltip.hidden = false;
        position();
        if (!active) return;
        trigger.setAttribute("aria-describedby", tooltip.id);
        window.addEventListener("scroll", dismissOnScroll, true);
        window.addEventListener("resize", hide);
        if (window.visualViewport) {
          window.visualViewport.addEventListener("resize", hide);
          window.visualViewport.addEventListener("scroll", dismissOnScroll);
        }
      }

      document.querySelectorAll(".sidebar-action-row .help-icon").forEach((trigger) => {
        trigger.addEventListener("mouseenter", () => show(trigger));
        trigger.addEventListener("mouseleave", scheduleHide);
        trigger.addEventListener("focus", () => show(trigger));
        trigger.addEventListener("blur", scheduleHide);
      });
      tooltip.addEventListener("mouseenter", cancelHide);
      tooltip.addEventListener("mouseleave", scheduleHide);
      document.addEventListener("keydown", (event) => {
        if (event.key === "Escape") hide();
      });
      document.addEventListener("pointerdown", (event) => {
        if (active && !active.contains(event.target) && !tooltip.contains(event.target)) hide();
      });
    }

    setupSidebarActionHelp();

    // Move focus to the main content without adding #content to the address, which the page navigation owns.
    document.querySelector(".skip-link").addEventListener("click", (event) => {
      event.preventDefault();
      document.getElementById("content").focus();
    });

    function optionalNumber(id) {
      const value = element(id).value.trim();
      return value === "" ? null : Number(value);
    }

    function commaList(id) {
      return element(id).value.split(",").map((value) => value.trim()).filter(Boolean);
    }

    function featureRandomness(prefix, feature) {
      const variation = element(prefix + "-" + feature + "-random-variation");
      const enabled = element(prefix + "-" + feature + "-random-enabled");
      const mode = element(prefix + "-" + feature + "-random-mode");
      const result = variation && enabled ? {
        variation_percent: Number(variation.value),
        enabled_probability: Number(enabled.value)
      } : { variation_percent: 0, enabled_probability: 100 };
      if (mode && mode.value === "range") {
        delete result.variation_percent;
        result.mode = "range";
        result.minimum = optionalNumber(prefix + "-" + feature + "-random-minimum");
        result.maximum = optionalNumber(prefix + "-" + feature + "-random-maximum");
      }
      return result;
    }

    function impairment(prefix) {
      return {
        delay_ms: Number(element(prefix + "-delay").value),
        jitter_ms: Number(element(prefix + "-jitter").value),
        loss_percent: Number(element(prefix + "-loss").value),
        loss_burst_packets: Number(element(prefix + "-loss-burst").value),
        bandwidth_kbit: optionalNumber(prefix + "-bandwidth"),
        reorder_percent: Number(element(prefix + "-reorder").value),
        duplicate_percent: Number(element(prefix + "-duplicate").value),
        corrupt_percent: Number(element(prefix + "-corrupt").value),
        delay_randomness: featureRandomness(prefix, "delay"),
        jitter_randomness: featureRandomness(prefix, "jitter"),
        loss_randomness: featureRandomness(prefix, "loss"),
        bandwidth_randomness: featureRandomness(prefix, "bandwidth"),
        reorder_randomness: featureRandomness(prefix, "reorder"),
        duplicate_randomness: featureRandomness(prefix, "duplicate"),
        corrupt_randomness: featureRandomness(prefix, "corrupt")
      };
    }

    function timeToMinute(id) {
      const parts = element(id).value.split(":").map(Number);
      return parts[0] * 60 + parts[1];
    }

    function selectedScheduleDays() {
      return Array.from(document.querySelectorAll(".day-toggle[aria-pressed='true']"))
        .map((button) => Number(button.dataset.day));
    }

    function syncScheduleDays() {
      const days = selectedScheduleDays();
      element("schedule-days").value = days.join(",");
      return days;
    }

    document.querySelectorAll(".day-toggle").forEach((button) => {
      button.addEventListener("click", () => {
        const selected = button.getAttribute("aria-pressed") !== "true";
        button.setAttribute("aria-pressed", String(selected));
        syncScheduleDays();
      });
    });

    const defaultScheduleDays = syncScheduleDays();

    // Resetting the form sets its fields back but not the day buttons, which are not fields.
    element("schedule-form").addEventListener("reset", () => {
      document.querySelectorAll(".day-toggle").forEach((button) => {
        button.setAttribute("aria-pressed", String(defaultScheduleDays.includes(Number(button.dataset.day))));
      });
      syncScheduleDays();
    });

    // True while a person is using something inside the container, a focused button, field or
    // link or an open menu. Drawing it again then would take the focus away or swallow a click.
    function interactionInside(container) {
      const active = document.activeElement;
      if (active && active !== document.body && container.contains(active)) return true;
      return !!container.querySelector("details[open], [aria-expanded='true']");
    }

    // Draws a container again only when what it shows changed and nobody is using it. The key
    // stays as it was when the drawing is put off, so the next poll tries again.
    function replaceIfChanged(container, key, draw) {
      if (container.dataset.renderKey === key || interactionInside(container)) return false;
      container.dataset.renderKey = key;
      draw();
      return true;
    }

    // A list drawn again takes the control a person stands on with it. Rows carry data-key and a
    // control keeps its name, so the same control of the same row gets focus back. Focus that went
    // elsewhere is not moved, and a row that never comes back ends the search after 1.5 s.
    const ROW_CONTROLS = "button, a[href], input[type='checkbox'], summary";

    function controlName(node) {
      return node.getAttribute("aria-label") || node.textContent.trim() || node.id || node.tagName;
    }

    function sameControls(row, name) {
      return Array.from(row.querySelectorAll(ROW_CONTROLS)).filter((node) => controlName(node) === name);
    }

    // Where a control sits in a list that is drawn again: the list, the key of its row and which of
    // the row's controls with that name it is. The same place in the new drawing is the same control.
    function rowAnchor(node) {
      const row = node && node.closest && node.closest("[data-key]");
      const scope = row && row.parentElement.closest("[id]");
      const name = scope && node.matches(ROW_CONTROLS) && controlName(node);
      return name ? { scope: scope.id, key: row.dataset.key, name, index: sameControls(row, name).indexOf(node) } : null;
    }

    function controlAtAnchor(anchor) {
      const scope = document.getElementById(anchor.scope);
      const row = scope && Array.from(scope.querySelectorAll("[data-key]")).find((item) => item.dataset.key === anchor.key);
      const same = row ? sameControls(row, anchor.name) : [];
      return same[Math.min(anchor.index, same.length - 1)] || null;
    }

    function keepFocusWhenListsAreDrawn() {
      let standing = null;
      document.addEventListener("focusin", ({ target }) => {
        const anchor = rowAnchor(target);
        standing = anchor ? { ...anchor, control: target, until: 0 } : null;
      });
      // Chromium reports a removed control's focus as leaving. One still in the page afterwards left on purpose.
      document.addEventListener("focusout", ({ target }) => window.queueMicrotask(() => {
        if (standing && target === standing.control && target.isConnected) standing = null;
      }));
      new MutationObserver(() => {
        if (!standing || standing.control.isConnected) return;
        standing.until = standing.until || performance.now() + 1500;
        if (document.activeElement !== document.body || performance.now() > standing.until) {
          standing = null;
          return;
        }
        const target = controlAtAnchor(standing);
        if (target && !target.disabled) target.focus({ preventScroll: true });
      }).observe(document.body, { childList: true, subtree: true });
    }

    keepFocusWhenListsAreDrawn();

    const PI_NOT_ANSWERING = "Pi not answering, retrying";

    const REQUEST_TIMEOUT_MS = 15000;

    const SLOW_REQUEST_TIMEOUT_MS = 45000;
    const SPEED_TEST_START_TIMEOUT_MS = 75000;

    // Applying, resetting and anything that talks to the router or Wi-Fi can take a while.
    const SLOW_REQUEST_PATHS = /^\/api\/(apply|reset|normal-access|general-profile|router|wifi|release|import)/;

    function requestTimeoutMs(path, method) {
      if ((path === "/api/speed-tests/start" || path === "/api/connection-checks/start") && method === "POST") {
        return SPEED_TEST_START_TIMEOUT_MS;
      }
      const slow = SLOW_REQUEST_PATHS.test(path) && (method !== "GET" || /^\/api\/(router|wifi)/.test(path));
      return slow ? SLOW_REQUEST_TIMEOUT_MS : REQUEST_TIMEOUT_MS;
    }

    // The requests the Pi has not answered in time. The note stays until each has been answered.
    const unansweredPaths = new Set();

    function showPiNotAnswering() {
      const summary = document.getElementById("runtime-summary");
      if (!summary) return;
      if (unansweredPaths.size) {
        summary.textContent = PI_NOT_ANSWERING;
        summary.className = "runtime-summary unknown";
      } else if (summary.textContent === PI_NOT_ANSWERING) {
        summary.textContent = "";
        renderRuntimeText();
      }
    }

    function notePiNotAnswering(path) {
      unansweredPaths.add(path);
      showPiNotAnswering();
    }

    function notePiAnswered(path) {
      // A completed read establishes reachability, not the outcome of an uncertain save.
      // Read failures retain their individual retry notes until their own read succeeds.
      if (path === "/api/health") {
        for (const pending of unansweredPaths) {
          if (uncertainWritePaths.has(pending)) unansweredPaths.delete(pending);
        }
      }
      const changed = unansweredPaths.delete(path);
      if (changed || path === "/api/health") showPiNotAnswering();
    }

    const uncertainWritePaths = new Set();

    // The saved paths since the last refresh decide which parts of the page are read again.
    const recentWrites = new Set();

    async function request(path, method, body, options = {}) {
      const headers = {};
      const token = element("admin-token").value.trim();
      const tokenRevision = adminTokenRevision;
      if (method === "GET" && path !== "/api/health" && path !== "/api/wifi-operation/request-id" && !adminTokenVerified && !adminTokenChecking) {
        throw new Error("Token changed. Choose Check token to load.");
      }
      if (method !== "GET" && !token) {
        openCredentials("admin");
        if (pendingFormSubmitButton) setButtonBusy(pendingFormSubmitButton, false);
        pendingFormSubmitButton = null;
        throw new Error("Enter the admin token first.");
      }
      if (token) headers.Authorization = "Bearer " + token;
      if (body !== undefined) headers["Content-Type"] = "application/json";
      const submitButton = method === "GET" ? null : pendingFormSubmitButton;
      const submitForm = submitButton && submitButton.form;
      const submitOwner = {};
      if (submitButton) submitButton.requestOwner = submitOwner;
      // The workflow helpers live in a separate file that the server adds to the page.
      const drafts = typeof formDrafts === "undefined" ? null : formDrafts;
      const submittedDraft = submitForm && drafts ? drafts.get(submitForm.id) : null;
      if (method !== "GET") pendingFormSubmitButton = null;
      const controller = new AbortController();
      let timedOut = false;
      const abortRequest = () => controller.abort();
      if (options.signal) {
        if (options.signal.aborted) controller.abort();
        else options.signal.addEventListener("abort", abortRequest, {once: true});
      }
      const timeout = setTimeout(() => { timedOut = true; controller.abort(); },
        options.timeoutMs || requestTimeoutMs(path, method));
      try {
        let response;
        let data;
        try {
          response = await fetch(path, {
            method: method,
            headers: headers,
            body: body === undefined ? undefined : JSON.stringify(body),
            cache: "no-store",
            signal: controller.signal
          });
          data = await response.json();
        } catch (networkError) {
          // A dropped Wi-Fi shows up here. Say so plainly and keep the sign-in untouched.
          throw typeof lostConnectionError === "function" ? lostConnectionError(networkError) : networkError;
        }
        if (options.isResponseCurrent && !options.isResponseCurrent()) {
          throw new Error("Request is no longer current.");
        }
        if (tokenRevision !== adminTokenRevision) {
          throw new Error("Token changed. Choose Check token to load.");
        }
        if (!response.ok) {
          // Just after a Wi-Fi restart the Pi may not have matched this Mac to its connection yet.
          const waitingForRejoin = data.registration_required && typeof wifiReconnectActive === "function" && wifiReconnectActive();
          if (data.registration_required && !waitingForRejoin) lockDashboardAccess(data.error);
          const applyHint = data.error === "network helper apply failed" &&
            typeof data.hint === "string"
            ? "Network helper apply failed. " + data.hint
            : data.error || response.statusText;
          if (data.access_choice_required === true && path === "/api/unaffected-devices/delete" &&
              body && body.dashboard_access === undefined && tokenRevision === adminTokenRevision) {
            // Removing protection would also lock this device out. Never do that silently.
            // The question can stay open for a while, so the request's own time limit ends first.
            clearTimeout(timeout);
            const choice = await chooseProtectedAccess();
            if (choice) return request(path, method, {...body, dashboard_access: choice}, options);
          }
          const error = new Error(applyHint);
          error.status = response.status;
          error.code = data.code;
          error.profile = data.profile;
          if (typeof data.retry_after === "number") error.retryAfter = data.retry_after;
          if (waitingForRejoin) error.transient = true;
          throw error;
        }
        notePiAnswered(path);
        if (method !== "GET") recentWrites.add(path);
        if (response.status !== 202 && submitForm && drafts && drafts.get(submitForm.id) === submittedDraft) {
          drafts.delete(submitForm.id);
          const formNote = submitForm.querySelector(".form-error");
          if (formNote) formNote.remove();
          if (typeof renderDraftNotice === "function") renderDraftNotice();
        }
        if (method === "GET" && typeof recordRead === "function") recordRead(path, null);
        return data;
      } catch (error) {
        if (options.isResponseCurrent && !options.isResponseCurrent()) throw error;
        if (timedOut && tokenRevision === adminTokenRevision) {
          error = typeof lostConnectionError === "function"
            ? lostConnectionError(new Error(PI_NOT_ANSWERING)) : new Error(PI_NOT_ANSWERING);
          error.timeout = true;
          if (method !== "GET") {
            if (uncertainWritePaths.size >= 64) uncertainWritePaths.delete(uncertainWritePaths.values().next().value);
            uncertainWritePaths.add(path);
            error.message += ". The change's result is unknown. Check its saved settings before trying again.";
          }
          notePiNotAnswering(path);
        }
        if (tokenRevision === adminTokenRevision && method === "GET" && typeof recordRead === "function") recordRead(path, error);
        if (tokenRevision === adminTokenRevision && typeof formError === "function") formError(submitForm, error.message);
        throw error;
      } finally {
        clearTimeout(timeout);
        if (options.signal) options.signal.removeEventListener("abort", abortRequest);
        if (submitButton && submitButton.requestOwner === submitOwner) {
          delete submitButton.requestOwner;
          setButtonBusy(submitButton, false);
        }
      }
    }

    async function show(endpoint, elementId, key) {
      const data = await request(endpoint, "GET");
      element(elementId).textContent = JSON.stringify(data[key], null, 2);
    }

    const adminTokenHelp = element("admin-token-help");

    // Nothing here may name the product: this text shows before anyone has signed in. The setup
    // notes hold the exact command.
    adminTokenHelp.textContent =
      "The token is stored on the Pi. The setup notes show how to read it there.\n" +
      "Copy it into this field. Keep the token private.";

    // The product name, the command name and the default Wi-Fi name come from a private route
    // after the token is accepted. Until then the page stays neutral.
    let brandInfo = null;
    let brandRequestSerial = 0;

    function applyBrand(signedIn) {
      const name = signedIn && brandInfo ? brandInfo.name : "";
      document.title = name || "Sign in";
      element("brand-title").textContent = name || "Network lab";
      element("brand-sidebar").setAttribute("aria-label", name || "Network lab");
      element("measure-command").textContent = signedIn && brandInfo
        ? brandInfo.command + " measure --label normal" : "the measure command";
      const updateTokenCommand = element("update-token-command");
      if (updateTokenCommand) updateTokenCommand.textContent = signedIn && brandInfo
        ? "sudo " + brandInfo.command + " update set-token" : "the setup command shown after sign-in";
      const ssid = element("access-point-ssid");
      if (signedIn && brandInfo && !ssid.value && document.activeElement !== ssid) {
        ssid.value = brandInfo.default_ssid;
      }
      if (!signedIn) {
        if (brandInfo && ssid.value === brandInfo.default_ssid) ssid.value = "";
        brandInfo = null;
        brandRequestSerial += 1;
      }
    }

    async function loadBrand() {
      const tokenRevision = adminTokenRevision;
      const serial = ++brandRequestSerial;
      try {
        const data = await request("/api/brand", "GET");
        if (serial !== brandRequestSerial || tokenRevision !== adminTokenRevision) return;
        brandInfo = data;
        applyBrand(adminTokenVerified);
      } catch (error) {
        if (serial !== brandRequestSerial || tokenRevision !== adminTokenRevision) return;
        applyBrand(false);
      }
    }

    const privateDataPanelIds = [
      "profiles-json", "devices-json", "plan", "state", "policies",
      "connected-devices", "unaffected-devices", "uplink-details", "general-profile-status",
      "saved-profiles", "device-table", "diagnostics-json", "action-history",
      "random-website-status", "pattern-history-body", "pattern-history-status"
    ];

    const privateDataLockHooks = [];

    function setDashboardUnlocked(allowed) {
      element("private-dashboard").hidden = !allowed;
      element("dashboard-access-list").hidden = !allowed;
      if (typeof applyBrand === "function") applyBrand(allowed);
      element("trust-pi").hidden = !allowed;
      element("one-address").hidden = !allowed;
      element("easy-address").hidden = allowed;
      if (allowed && typeof loadTrustDetails === "function") loadTrustDetails();
      document.body.classList.toggle("dashboard-locked", !allowed);
      element("load-pattern-history").disabled = !allowed;
    }

    function lockDashboardAccess(message) {
      adminTokenVerified = false;
      setDashboardUnlocked(false);
      element("dashboard-access-list").replaceChildren();
      clearAddressMovePanels();
      element("dashboard-access-status").textContent = message;
      setPrivateDataPanels(message);
      stopTrafficSampling(message);
    }

    const addressMoveDismissed = new Set();
    let addressMoveOffer = null;
    let addressTokenRevision = null;
    let addressUiGeneration = 0;

    function clearAddressMovePanels() {
      addressUiGeneration += 1;
      addressTokenRevision = null;
      addressMoveOffer = null;
      addressMoveDismissed.clear();
      ["address-move-card", "address-type-note", "address-move-replaced", "address-help"]
        .forEach((id) => { element(id).hidden = true; });
      element("address-move-picker").replaceChildren();
      setButtonBusy(element("address-move-confirm"), false);
      element("address-move-replaced").replaceChildren();
      element("address-move-text").textContent = "";
      element("address-type-note").textContent = "";
    }

    function beginAddressChange() {
      if (!addressResponseCurrent(adminTokenRevision, addressUiGeneration)) return null;
      adminTokenRevision += 1;
      addressTokenRevision = adminTokenRevision;
      if (typeof cancelWifiRecovery === "function") cancelWifiRecovery();
      adminTokenVerified = false;
      adminTokenChecking = false;
      setDashboardUnlocked(false);
      stopTrafficSampling("Device access is changing. Checking again after the saved change.");
      return {revision: adminTokenRevision, generation: addressUiGeneration};
    }

    function addressResponseCurrent(revision, generation) {
      return revision === adminTokenRevision && generation === addressUiGeneration &&
        addressTokenRevision === revision && !adminTokenRejected && !adminTokenRefused &&
        Boolean(element("admin-token").value.trim());
    }

    privateDataLockHooks.push(() => {
      if (addressTokenRevision !== adminTokenRevision || adminTokenRejected || adminTokenRefused ||
          !element("admin-token").value.trim()) clearAddressMovePanels();
    });


    function addressMoveSentence(candidate, current) {
      const needsAllow = !current.registered && candidate.registered;
      const needsProtect = !current.protected && candidate.protected;
      return "This looks like a new address for one of your devices. " + (
        needsAllow && needsProtect ? "Allow this device and keep it protected?"
        : needsAllow ? "Allow this device?" : "Keep this device protected?");
    }

    function renderAddressMoves(data) {
      addressMoveOffer = data;
      const current = data.current;
      const note = element("address-type-note");
      note.hidden = !current;
      if (current) {
        note.textContent = current.private_address
          ? "This device uses a private Wi-Fi address (" + current.mac_address + "). It can change, and a changed address is not recognised until you move it here."
          : "This device uses a fixed Wi-Fi address (" + current.mac_address + ").";
      }
      const card = element("address-move-card");
      const candidates = current ? data.candidates : [];
      card.hidden = !data.offer || !candidates.length || addressMoveDismissed.has(current.mac_address);
      if (!card.hidden) {
        const picker = element("address-move-picker");
        const previous = picker.value;
        picker.replaceChildren();
        candidates.forEach((candidate) => {
          const option = document.createElement("option");
          option.value = candidate.mac_address;
          option.textContent = candidate.name + " (" + [
            candidate.registered ? "allowed" : null, candidate.protected ? "protected" : null
          ].filter(Boolean).join(" and ") + ")";
          picker.appendChild(option);
        });
        if (candidates.some((candidate) => candidate.mac_address === previous)) picker.value = previous;
        const showSentence = () => {
          const chosen = candidates.find((candidate) => candidate.mac_address === picker.value) || candidates[0];
          element("address-move-text").textContent = addressMoveSentence(chosen, current);
        };
        picker.onchange = showSentence;
        showSentence();
      }
      const replaced = element("address-move-replaced");
      replaced.replaceChildren();
      replaced.hidden = !data.replaced.length;
      if (!replaced.hidden) {
        const heading = document.createElement("h3");
        heading.textContent = "Replaced addresses";
        replaced.appendChild(heading);
        data.replaced.forEach((move) => {
          const row = document.createElement("p");
          const label = document.createElement("span");
          label.textContent = move.name + (move.recovery_required ? " · needs recovery from " : " · replaced ") + move.old_mac + " ";
          const undo = document.createElement("button");
          undo.type = "button";
          undo.textContent = move.recovery_required ? "Finish reversing move" : "Undo";
          undo.addEventListener("click", async () => {
            if (!await confirmFromButton(undo, "Reverse the move for " + move.name + "? Access or protection that came " +
                "from this move will return to " + move.old_mac + " if it has not changed since then.",
                {title: "Reverse this move?", confirmLabel: "Reverse move"})) return;
            const change = beginAddressChange();
            if (!change) {
              setButtonBusy(undo, false);
              return;
            }
            const {revision, generation} = change;
            try {
              await request("/api/dashboard-access/move-undo", "POST", {from_mac: move.old_mac});
              if (!addressResponseCurrent(revision, generation)) return;
              showMessage("Move undone. " + move.name + " uses its old address again.", "success");
              await checkAdminToken();
            } catch (error) {
              if (!addressResponseCurrent(revision, generation)) return;
              await checkAdminToken();
              if (revision === adminTokenRevision) showMessage(error.message, "error");
            } finally {
              if (addressResponseCurrent(revision, generation)) setButtonBusy(undo, false);
            }
          });
          row.append(label, undo);
          replaced.appendChild(row);
        });
      }
    }

    async function loadAddressMoves() {
      const revision = adminTokenRevision;
      const generation = addressUiGeneration;
      try {
        const data = await request("/api/dashboard-access/moves", "GET");
        if (addressResponseCurrent(revision, generation)) renderAddressMoves(data);
      } catch (error) {
        if (addressResponseCurrent(revision, generation)) clearAddressMovePanels();
      }
    }

    element("address-move-dismiss").addEventListener("click", () => {
      if (addressMoveOffer && addressMoveOffer.current) addressMoveDismissed.add(addressMoveOffer.current.mac_address);
      element("address-move-card").hidden = true;
    });

    element("address-move-confirm").addEventListener("click", async () => {
      const button = element("address-move-confirm");
      const picker = element("address-move-picker");
      const change = beginAddressChange();
      if (!change) return;
      const {revision, generation} = change;
      const name = picker.selectedOptions.length ? picker.selectedOptions[0].textContent : "the device";
      setButtonBusy(button, true);
      try {
        await request("/api/dashboard-access/move", "POST", {from_mac: picker.value});
        if (!addressResponseCurrent(revision, generation)) return;
        showMessage("Moved. This device now takes over " + name + ". You can undo this under Dashboard access.", "success");
        await checkAdminToken();
      } catch (error) {
        if (!addressResponseCurrent(revision, generation)) return;
        await checkAdminToken();
        if (revision !== adminTokenRevision) return;
        element("address-move-text").textContent = error.message;
        showMessage(error.message, "error");
      } finally {
        if (addressResponseCurrent(revision, generation)) setButtonBusy(button, false);
      }
    });

    async function chooseProtectedAccess() {
      if (await confirmWorkflow("This is the device you are using. It opens the dashboard only because it is protected. " +
          "Keep dashboard access for it as a separate saved grant? Choose Cancel to decide about removing it.",
          {title: "Keep dashboard access?", confirmLabel: "Keep access"})) return "keep";
      if (await confirmWorkflow("Stop protecting this device and remove its dashboard access? It then needs " +
          "Allow this device or the management network to sign in again.",
          {title: "Stop protecting this device?", confirmLabel: "Stop protecting", danger: true})) return "remove";
      return null;
    }

    function accessReason(grants) {
      const manual = grants.includes("manual");
      const protection = grants.includes("protection");
      if (manual && protection) return "Dashboard access comes from a saved grant and from protection. The admin token is still required.";
      if (protection) return "Dashboard access comes from protecting this device. The admin token is still required.";
      if (manual) return "Dashboard access comes from a saved grant. The admin token is still required.";
      return "Dashboard access is available.";
    }

    async function loadDashboardAccess() {
      const data = await request("/api/dashboard-access/status", "GET");
      element("allow-dashboard-device").disabled = !data.can_register;
      element("dashboard-access-status").textContent = data.allowed
        ? accessReason(Array.isArray(data.grants) ? data.grants : [])
        : data.can_register ? "Token accepted. Choose Allow this device to save dashboard access."
        : "Token accepted but this device could not be verified. Wait a few seconds and check again.";
      setDashboardUnlocked(data.allowed);
      addressTokenRevision = adminTokenRevision;
      element("address-help").hidden = false;
      await loadAddressMoves();
      return data;
    }

    let dashboardAccessListSerial = 0;

    async function loadDashboardAccessList() {
      const container = element("dashboard-access-list");
      // An older read that answers late must not replace a newer list.
      const serial = ++dashboardAccessListSerial;
      try {
        const data = await request("/api/dashboard-access", "GET");
        if (serial !== dashboardAccessListSerial) return;
        container.replaceChildren();
        const sync = data.protected_access || {};
        if (sync.synced === false) {
          const failed = document.createElement("p");
          failed.className = "form-note";
          failed.setAttribute("role", "alert");
          failed.textContent = "Dashboard access for protected devices is not confirmed. " + (sync.error || "") +
            " A device that is no longer protected may still open the dashboard until Retry succeeds. ";
          const retry = document.createElement("button");
          retry.type = "button";
          retry.textContent = "Retry protected access";
          retry.addEventListener("click", async () => {
            setButtonBusy(retry, true);
            try {
              await request("/api/dashboard-access/sync-protected", "POST", {});
              showMessage("Dashboard access for protected devices is confirmed.", "success");
            } catch (error) { showMessage(error.message, "error"); }
            finally { setButtonBusy(retry, false); await loadDashboardAccessList(); }
          });
          failed.appendChild(retry);
          container.appendChild(failed);
        }
        (sync.not_eligible || []).forEach((item) => {
          const note = document.createElement("p");
          note.className = "form-note";
          note.textContent = "Protected " + item.mac_address + " cannot open the dashboard: " + item.reason;
          container.appendChild(note);
        });
        if (!data.devices.length) container.append("No devices have saved Pi Wi-Fi access.");
        data.devices.forEach((device) => {
          const row = document.createElement("p");
          const label = document.createElement("span");
          const saved = device.manual !== false;
          const reason = device.replaced_by ? "" : saved && device.protected ? " · saved grant and protection"
            : device.protected ? " · from protection" : " · saved grant";
          label.textContent = (device.name || "Device") + " · " + device.mac_address + reason +
            (device.replaced_by ? " · replaced by " + device.replaced_by : "") + " ";
          row.appendChild(label);
          if (!saved) {
            const note = document.createElement("span");
            note.className = "form-note";
            note.textContent = "Choose Stop protecting under Protected devices to remove this access.";
            row.appendChild(note);
            container.appendChild(row);
            return;
          }
          const remove = document.createElement("button");
          remove.type = "button";
          remove.textContent = device.protected ? "Remove saved grant" : "Remove";
          remove.addEventListener("click", async () => {
            if (!await confirmFromButton(remove, device.protected
                ? "Remove the saved grant for " + (device.name || device.mac_address) +
                  "? It stays allowed while it is protected."
                : "Remove dashboard access for " + (device.name || device.mac_address) +
                  "? Removing this device returns it to sign-in.",
                {title: device.protected ? "Remove the saved grant?" : "Remove dashboard access?",
                  confirmLabel: device.protected ? "Remove saved grant" : "Remove access", danger: true})) return;
            try {
              await request("/api/dashboard-access/remove", "POST", {mac_address: device.mac_address});
              await checkAdminToken();
            } catch (error) { showMessage(error.message, "error"); }
            finally { setButtonBusy(remove, false); }
          });
          row.appendChild(remove);
          container.appendChild(row);
        });
      } catch (error) { if (serial === dashboardAccessListSerial) container.textContent = error.message; }
    }

    element("allow-dashboard-device").addEventListener("click", async () => {
      const button = element("allow-dashboard-device");
      setButtonBusy(button, true);
      try {
        await request("/api/dashboard-access/register-self", "POST", {
          name: element("dashboard-access-name").value
        });
        await checkAdminToken();
      } catch (error) {
        element("dashboard-access-status").textContent = error.message;
        showMessage(error.message, "error");
      } finally { setButtonBusy(button, false); }
    });

    function setPrivateDataPanels(message, state = "Check token") {
      if (typeof invalidateAdminTools === "function") invalidateAdminTools();
      privateDataPanelIds.forEach((panelId) => {
        element(panelId).textContent = message;
      });
      privateDataLockHooks.forEach((hook) => hook());
      element("connected-count").textContent = state;
      element("connected-refresh-status").textContent =
        "Refreshes every 5 seconds once the admin token is accepted.";
      element("uplink-state").textContent = state;
      element("runtime-summary").textContent = message;
      element("runtime-summary").className = "runtime-summary unknown";
      // The panels go blank whenever the token is not trusted, so the impact card follows.
      if (typeof syncImpact === "function") syncImpact();
      renderSidebarStatus();
    }

    async function loadPrivateDataPanel(panelId, load) {
      try {
        await load();
      } catch (error) {
        element(panelId).textContent = "Could not load: " + error.message;
      }
    }

    let helperConfigured = false;

    let helperKnown = false;

    let adminTokenVerified = false;

    let adminTokenRevision = 0;

    let adminTokenChecking = false;

    let adminTokenRejected = false;

    let adminTokenRefused = false;

    let routerPasswordRevision = 0;

    let routerPasswordVerified = false;

    let routerPasswordRejected = false;

    let routerPasswordChecking = false;

    // Until then the Pi refuses another router password check (see tickRouterCheckWait).
    let routerCheckWaitUntil = 0;
    let routerCheckWaitTimer = null;

    let routerActionPending = false;
    let routerWifiEnabled = null;

    let routerWifiCheckedAt = null;

    let routerWifiPolling = false;


    let routerWifiRemoteBusy = false;

    let routerWifiRemoteChanging = false;

    let routerWifiError = "";

    let routerWifiMinimumCheck = 0;

    let credentialOpener = null;

    const REMEMBER_HOUR_MS = 60 * 60 * 1000;

    // The hours a token can be remembered for. The router password always keeps 24 hours.
    const REMEMBER_CHOICES = { 24: "24 hours", 168: "7 days", 720: "30 days" };

    const REMEMBER_CHOICE_KEY = "jittersim.remembered.duration";

    const REMEMBERED = {
      admin: {
        key: "jittersim.remembered.admin-token", box: "remember-admin-token",
        note: "remember-admin-token-note", input: "admin-token", noun: "token"
      },
      router: {
        key: "jittersim.remembered.router-password", box: "remember-router-password",
        note: "remember-router-password-note", input: "router-admin-password", noun: "password"
      }
    };

    const expiredNotice = { admin: false, router: false };

    const ownedRemembered = { admin: null, router: null };

    let rememberedCredentialsReady = false;

    function updateCredentialStatus() {
      const admin = element("admin-credential-state");
      admin.textContent = adminTokenChecking ? "Checking..." :
        adminTokenVerified ? "Accepted" : adminTokenRejected ? "Rejected" : element("admin-token").value.trim() ? "Check needed" : "Needed";
      admin.className = "credential-state" + (adminTokenVerified ? " accepted" : "");
      const router = element("router-credential-state");
      router.textContent = routerActionPending ? "Working..." :
        routerPasswordChecking ? "Checking..." :
        routerPasswordVerified ? "Accepted" :
        routerPasswordRejected ? "Rejected" :
        element("router-admin-password").value ? "Entered" : "Not entered";
      router.className = "credential-state" + (routerPasswordVerified ? " accepted" : "");
      syncRememberedCredentials();
      renderRouterWifi();
    }

    function openCredentials(kind, opener) {
      const panel = element("credentials-panel");
      if (opener || panel.hidden) credentialOpener = opener || document.activeElement;
      panel.hidden = false;
      ["open-admin-credentials", "open-router-credentials", "open-router-credentials-inline"].forEach((id) => {
        element(id).setAttribute("aria-expanded", "true");
      });
      const field = element(kind === "router" ? "router-admin-password" : "admin-token");
      field.focus({preventScroll: true});
      // A short window shows the Check button below the field too when both fit.
      element(kind === "router" ? "check-router-password" : "check-admin-token")
        .scrollIntoView({block: "nearest", inline: "nearest"});
      field.scrollIntoView({block: "nearest", inline: "nearest"});
    }

    function requireAdminToken(opener) {
      if (element("admin-token").value.trim()) return true;
      showMessage("Enter the admin token first.", "error");
      openCredentials("admin", opener);
      return false;
    }

    function closeCredentials() {
      element("credentials-panel").hidden = true;
      ["open-admin-credentials", "open-router-credentials", "open-router-credentials-inline"].forEach((id) => {
        element(id).setAttribute("aria-expanded", "false");
      });
      if (credentialOpener && credentialOpener.isConnected) credentialOpener.focus({preventScroll: true});
    }

    function updateCredentialViewport() {
      const view = window.visualViewport;
      const height = view ? view.height : window.innerHeight;
      const top = view ? view.offsetTop : 0;
      const style = document.documentElement.style;
      style.setProperty("--credential-viewport-height", height + "px");
      style.setProperty("--credential-viewport-top", top + "px");
      style.setProperty("--credential-viewport-bottom", Math.max(0, window.innerHeight - top - height) + "px");
    }

    ["open-admin-credentials", "open-router-credentials", "open-router-credentials-inline"].forEach((id) => {
      element(id).addEventListener("click", (event) => openCredentials(id.includes("router") ? "router" : "admin", event.currentTarget));
    });

    element("close-credentials").addEventListener("click", closeCredentials);

    document.addEventListener("keydown", (event) => {
      // An open question owns Escape: it answers no and leaves this panel as it is.
      if (event.key === "Escape" && !element("credentials-panel").hidden && !document.querySelector("dialog[open]")) {
        event.preventDefault();
        closeCredentials();
      }
    });

    element("clear-admin-token").addEventListener("click", () => {
      element("admin-token").value = "";
      element("admin-token").dispatchEvent(new Event("input"));
      element("admin-token").focus({preventScroll: true});
    });

    element("clear-router-password").addEventListener("click", () => {
      element("router-admin-password").value = "";
      element("router-admin-password").dispatchEvent(new Event("input"));
      element("router-admin-password").focus({preventScroll: true});
    });

    element("router-admin-password").addEventListener("input", () => {
      routerPasswordRevision += 1;
      routerWifiEnabled = null;
      routerWifiCheckedAt = null;
      routerWifiError = "";
      routerWifiMinimumCheck = 0;
      routerWifiRemoteBusy = false;
      routerWifiRemoteChanging = false;
      routerPasswordVerified = false;
      routerPasswordRejected = false;
      if (!routerActionPending) element("router-action-message").textContent = "";
      updateCredentialStatus();
    });

    window.addEventListener("resize", updateCredentialViewport);

    if (window.visualViewport) {
      window.visualViewport.addEventListener("resize", updateCredentialViewport);
      window.visualViewport.addEventListener("scroll", updateCredentialViewport);
    }

    function savedCredential(kind) {
      try {
        const saved = JSON.parse(window.localStorage.getItem(REMEMBERED[kind].key));
        if (
          saved && typeof saved.value === "string" && saved.value &&
          Number.isFinite(saved.expires)
        ) return saved;
      } catch (error) {
        // Storage is blocked or the entry is damaged, so treat it as not saved.
      }
      return null;
    }

    // The hours a person chose to remember the token for. Anything else means 24.
    function rememberHours(kind) {
      if (kind !== "admin") return 24;
      const hours = Number(element("remember-admin-duration").value);
      return REMEMBER_CHOICES[hours] ? hours : 24;
    }

    // An entry for 24 hours keeps the shape it always had. A longer one adds its hours so the
    // note that says when it ended can name them.
    function savedHours(saved) {
      return saved && REMEMBER_CHOICES[saved.hours] ? saved.hours : 24;
    }

    function saveCredential(kind, value) {
      try {
        const hours = rememberHours(kind);
        const entry = { value: value, expires: Date.now() + hours * REMEMBER_HOUR_MS };
        if (hours !== 24) entry.hours = hours;
        window.localStorage.setItem(REMEMBERED[kind].key, JSON.stringify(entry));
        return true;
      } catch (error) {
        return false;
      }
    }

    function forgetCredential(kind) {
      try {
        window.localStorage.removeItem(REMEMBERED[kind].key);
      } catch (error) {
        // Nothing was saved that could be removed.
      }
    }

    // Longer than a day needs the date too: a weekday alone could mean this week or next month.
    function rememberedUntil(expires, long) {
      const options = long
        ? { weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }
        : { weekday: "short", hour: "2-digit", minute: "2-digit" };
      return new Date(expires).toLocaleString("en-GB", options);
    }

    // renew names the credential a person has just checked again. If it was accepted its
    // 24 hours start again. Automatic checks never pass it so a remembered entry still ends.
    function syncRememberedCredentials(renew) {
      if (!rememberedCredentialsReady) return;
      for (const kind of Object.keys(REMEMBERED)) {
        const config = REMEMBERED[kind];
        const box = element(config.box);
        const field = element(config.input);
        const value = kind === "admin" ? field.value.trim() : field.value;
        const accepted = kind === "admin" ? adminTokenVerified : routerPasswordVerified;
        const refused = kind === "admin" ? adminTokenRefused : routerPasswordRejected;
        let saved = savedCredential(kind);
        let note = "";
        if (saved && saved.expires <= Date.now()) {
          expiredNotice[kind] = REMEMBER_CHOICES[savedHours(saved)];
          forgetCredential(kind);
          saved = null;
          ownedRemembered[kind] = null;
          box.checked = false;
        } else if (!box.checked || !value) {
          if (saved && saved.value === ownedRemembered[kind]) {
            forgetCredential(kind);
            saved = null;
          }
          ownedRemembered[kind] = null;
        } else if (refused) {
          if (saved && saved.value === value) {
            forgetCredential(kind);
            saved = null;
          }
          ownedRemembered[kind] = null;
          note = "Not remembered: the " + config.noun + " was rejected.";
        } else if (accepted && (!saved || saved.value !== value || renew === kind)) {
          if (saveCredential(kind, value)) {
            saved = savedCredential(kind);
            ownedRemembered[kind] = value;
          } else {
            note = "This browser blocked saving, so it will be forgotten on reload.";
          }
        }
        if (!note && !box.checked && expiredNotice[kind]) {
          note = "Forgot the saved " + config.noun + " after " + expiredNotice[kind] + ".";
        } else if (!note && box.checked) {
          note = saved && saved.value === value
            ? "Remembered until " + rememberedUntil(saved.expires, savedHours(saved) > 24) + "."
            : "Will be remembered for " + REMEMBER_CHOICES[rememberHours(kind)] + " once accepted.";
        }
        const noteLine = element(config.note);
        // A status line speaks whenever it is written, so it is only written when its words change.
        if (noteLine.textContent !== note) noteLine.textContent = note;
      }
    }

    function restoreRememberedCredentials() {
      let restoredAdmin = false;
      // The last choice made in this browser. A saved token below shows its own hours instead.
      try {
        const choice = Number(window.localStorage.getItem(REMEMBER_CHOICE_KEY));
        if (REMEMBER_CHOICES[choice]) element("remember-admin-duration").value = String(choice);
      } catch (error) {
        // Storage is blocked, so the default of 24 hours stays.
      }
      for (const kind of Object.keys(REMEMBERED)) {
        const config = REMEMBERED[kind];
        const saved = savedCredential(kind);
        if (saved && saved.expires > Date.now()) {
          element(config.input).value = saved.value;
          element(config.box).checked = true;
          if (kind === "admin") element("remember-admin-duration").value = String(savedHours(saved));
          ownedRemembered[kind] = saved.value;
          if (kind === "admin") restoredAdmin = true;
        } else if (!saved) {
          forgetCredential(kind);
        }
      }
      rememberedCredentialsReady = true;
      updateCredentialStatus();
      // Only the admin token is checked on load: a wrong router password can lock the router.
      return restoredAdmin ? checkAdminToken() : undefined;
    }

    Object.keys(REMEMBERED).forEach((kind) => {
      element(REMEMBERED[kind].box).addEventListener("change", () => {
        expiredNotice[kind] = false;
        updateCredentialStatus();
      });
    });

    // The choice belongs to this browser. A token that is remembered already starts its new time now.
    element("remember-admin-duration").addEventListener("change", () => {
      try {
        window.localStorage.setItem(REMEMBER_CHOICE_KEY, String(rememberHours("admin")));
      } catch (error) {
        // The choice is used for this visit only.
      }
      expiredNotice.admin = false;
      // A shorter time cuts what is saved at once, even if the token is not checked again.
      // A longer one only counts once the token has been accepted.
      const saved = savedCredential("admin");
      if (
        saved && saved.value === ownedRemembered.admin &&
        saved.expires > Date.now() + rememberHours("admin") * REMEMBER_HOUR_MS
      ) saveCredential("admin", saved.value);
      syncRememberedCredentials("admin");
    });

    window.addEventListener("storage", (event) => {
      let changed = false;
      for (const kind of Object.keys(REMEMBERED)) {
        const removed = event.newValue === null &&
          (event.key === null || event.key === REMEMBERED[kind].key);
        if (removed && ownedRemembered[kind] !== null) {
          ownedRemembered[kind] = null;
          element(REMEMBERED[kind].box).checked = false;
          expiredNotice[kind] = false;
          changed = true;
        }
      }
      if (changed) updateCredentialStatus();
    });

    window.setInterval(syncRememberedCredentials, 60000);

    let profiles = [];

    let generalProfileState = null;

    let lastRuntimeState = null;

    let serviceListsState = [];

    let serviceListsLoaded = false;

    let profilesLoaded = false;

    let savedProfileBusy = null;

    let unaffectedMacs = null;

    let accessRulesSaved = null;

    let savedDevicesLoaded = false;

    let groupsLoaded = false;

    let applyBusy = false;

    let networkActionBusy = false;

    let networkActionRevision = 0;

    let groups = [];

    let savedDevices = [];

    let connectedClients = [];

    let lastConnectedData = null;

    let connectedRenderKey = null;

    let connectedClientsAvailable = false;

    const connectedDrafts = new Map();

    const clearConnectedDrafts = new Set();

    let observedDomainsByDevice = {};
    // The names a service or time limit was saved with, by form. The Pi keeps them for that limit after
    // their observations expire, so the picker has to keep offering them or an edit would drop them.
    let savedQuotaDomains = {};
    privateDataLockHooks.push(() => { savedQuotaDomains = {}; });

    let observedNames = new Set();

    const openObservationGroups = new Set();

    const observationProfileChoice = new Map();

    let randomWebsiteAnalyticsRequestVersion = 0;

    let connectedDevicesRequestInFlight = false;

    let observationRequestInFlight = false;

    const TRAFFIC_POLL_INTERVAL_MS = 2000;

    const TRAFFIC_MAX_POINTS = 30;

    const TRAFFIC_WINDOW_MS = 60000;

    let trafficPollTimer = null;

    let trafficRequestInFlight = false;

    let trafficSamplingAllowed = false;

    let previousTrafficSample = null;

    let trafficSamples = [];

    let currentIspTrafficLimits = { upload_kbit: null, download_kbit: 100000 };

    let latestTrafficReferences = {
      isp_upload: null,
      isp_download: 100,
      profile_upload: null,
      profile_download: null
    };

    const TRAFFIC_SERIES = [
      { field: "isp_upload", label: "ISP upload plan", color: "#b8a0ff", dash: [8, 6], step: true },
      { field: "isp_download", label: "ISP download plan", color: "#7bdce5", dash: [8, 6], step: true },
      { field: "profile_upload", label: "Device upload limit", color: "#ffa76c", dash: [2, 5], step: true },
      { field: "profile_download", label: "Device download limit", color: "#edc36a", dash: [2, 5], step: true },
      { field: "upload", label: "Upload traffic", color: "#70b7ff", dash: [], step: false },
      { field: "download", label: "Download traffic", color: "#70d7a9", dash: [], step: false }
    ];
    const TRAFFIC_INSPECTION_HELP = "Hover a line or focus its legend label to inspect it. Click or tap a label to keep it highlighted. Escape clears the highlight.";
    let trafficProfileScope = null;
    let trafficReferencesReadAt = null;
    let trafficInspection = { pointer: null, legend: null, pinned: null };
    let trafficChartRedraw = null;

    function showMessage(message, kind) {
      messageSerial += 1;
      const messageElement = element("message");
      messageElement.textContent = message;
      messageElement.className = "action-message " + (kind || "");
      if (kind !== "working" && pendingFormSubmitButton) {
        setButtonBusy(pendingFormSubmitButton, false);
        pendingFormSubmitButton = null;
      }
      // The toast is the one place a result shows and the one place a screen reader announces it.
      // The message line above stays only as the last text for scripts and tests.
      showToast(message, kind || "info");
    }

    function resetTrafficHistory() {
      previousTrafficSample = null;
      trafficSamples = [];
      trafficInspection = { pointer: null, legend: null, pinned: null };
      [
        "traffic-upload-rate",
        "traffic-download-rate",
        "traffic-upload-packets",
        "traffic-download-packets",
        "traffic-drop-rate",
        "traffic-queued"
      ].forEach((id) => { element(id).textContent = "—"; });
      drawTrafficChart();
    }

    function stopTrafficSampling(message) {
      latestTrafficReading = null;
      trafficSamplingAllowed = false;
      clearTrafficTimer();
      resetTrafficHistory();
      element("traffic-status").textContent = message;
    }

    function formatTrafficRate(megabitsPerSecond) {
      if (megabitsPerSecond < 1) {
        return (megabitsPerSecond * 1000).toFixed(1) + " kbit/s";
      }
      return megabitsPerSecond.toFixed(2) + " Mbit/s";
    }

    function trafficRateMbit(kbit) {
      return kbit === null || kbit === undefined ? null : kbit / 1000;
    }

    function trafficRateFromInput(id) {
      const raw = element(id).value.trim();
      if (raw === "") return null;
      const rate = Number(raw);
      const tenths = Math.round(rate * 10);
      if (!Number.isFinite(rate) || rate < 0.1 || rate > 1000 || Math.abs(rate * 10 - tenths) > 1e-7) {
        throw new Error("Enter a rate from 0.1 to 1000 Mbit/s in 0.1 Mbit/s steps, or leave it empty.");
      }
      return tenths * 100;
    }

    function setTrafficRateInputs(settings) {
      currentIspTrafficLimits = {
        upload_kbit: settings.upload_kbit,
        download_kbit: settings.download_kbit
      };
      latestTrafficReferences.isp_upload = trafficRateMbit(settings.upload_kbit);
      latestTrafficReferences.isp_download = trafficRateMbit(settings.download_kbit);
      element("traffic-isp-upload").value = settings.upload_kbit === null
        ? ""
        : (settings.upload_kbit / 1000).toFixed(1);
      element("traffic-isp-download").value = settings.download_kbit === null
        ? ""
        : (settings.download_kbit / 1000).toFixed(1);
      drawTrafficChart();
    }

    async function loadTrafficSettings() {
      const settings = await request("/api/traffic-settings", "GET");
      setTrafficRateInputs(settings);
    }

    function formatPacketRate(packetsPerSecond) {
      return new Intl.NumberFormat(undefined, { maximumFractionDigits: 1 }).format(packetsPerSecond) + " pkt/s";
    }

    function selectedTrafficTotals(deviceCounters) {
      const selectedDevice = element("traffic-device").value;
      // "All assigned devices" means the saved devices; everyone else is its own choice.
      const counters = selectedDevice === "@all"
        ? Object.keys(deviceCounters)
          .filter((id) => id !== EVERYONE_ELSE_TRAFFIC)
          .map((id) => deviceCounters[id])
        : deviceCounters[selectedDevice] ? [deviceCounters[selectedDevice]] : [];
      if (!counters.length) return null;
      return counters.reduce((totals, current) => {
        ["upload_bytes", "download_bytes", "upload_packets", "download_packets"].forEach((field) => {
          totals[field] += current[field];
        });
        return totals;
      }, { upload_bytes: 0, download_bytes: 0, upload_packets: 0, download_packets: 0 });
    }

    function deviceDisplayName(device) {
      return device.display_name || device.name || device.assigned_device_name ||
        device.ipv4_address || "Unknown device";
    }

    // The name in a line of text such as a list row or a question. An unnamed device keeps its
    // address there so that two of them never look the same.
    function deviceTitle(device) {
      const name = deviceDisplayName(device);
      return name === "Unknown device" && device.ipv4_address
        ? name + " (" + device.ipv4_address + ")"
        : name;
    }

    function devicePickerLabel(device) {
      return device.ipv4_address
        ? deviceDisplayName(device) + " (" + device.ipv4_address + ")"
        : deviceDisplayName(device);
    }

    function updateTrafficDeviceOptions(devices) {
      const select = element("traffic-device");
      const selected = select.value;
      select.replaceChildren();
      const allOption = document.createElement("option");
      allOption.value = "@all";
      const generalActive = generalProfileState && generalProfileState.active;
      allOption.textContent = generalActive ? "All affected devices (shared)" : "All assigned devices";
      select.appendChild(allOption);
      (generalActive ? [] : devices).forEach((device) => {
        const option = document.createElement("option");
        option.value = device.id;
        option.textContent = devicePickerLabel(device);
        select.appendChild(option);
      });
      if (!generalActive && everyoneElseInLiveTraffic()) {
        const option = document.createElement("option");
        option.value = EVERYONE_ELSE_TRAFFIC;
        option.textContent = "Everyone else";
        select.appendChild(option);
      }
      const kept = Array.from(select.options).some((option) => option.value === selected);
      select.value = kept ? selected : "@all";
      // The last sample belongs to the choice that went, so start a fresh graph.
      if (!kept && selected) resetTrafficView();
    }

    function trafficSeriesName(series, scope) {
      if (!series.field.startsWith("profile_")) return series.label;
      const direction = series.field.endsWith("upload") ? "upload" : "download";
      const prefix = scope === "shared" ? "Shared profile" : scope === "everyone" ? "Everyone else" : "Device";
      return prefix + " " + direction + " limit";
    }

    function trafficSeriesGeometry(samples, references, plot, scaleMax, now, scope) {
      return TRAFFIC_SERIES.map((definition) => {
        const series = { ...definition, label: trafficSeriesName(definition, scope), points: [], segments: [] };
        let previous = null;
        samples.forEach((sample) => {
          const value = sample[series.field];
          if (!Number.isFinite(value)) {
            previous = null;
            return;
          }
          const point = {
            x: plot.right - (plot.right - plot.left) * (now - sample.time) / TRAFFIC_WINDOW_MS,
            y: plot.bottom - (plot.bottom - plot.top) * value / scaleMax,
            value: value,
            time: sample.time
          };
          series.points.push(point);
          if (previous) {
            if (series.step) {
              const corner = { ...previous, x: point.x };
              series.segments.push({ from: previous, to: corner });
              series.segments.push({ from: corner, to: point });
            } else {
              series.segments.push({ from: previous, to: point });
            }
          }
          previous = point;
        });
        if (!samples.length && series.step && Number.isFinite(references[series.field])) {
          const value = references[series.field];
          const y = plot.bottom - (plot.bottom - plot.top) * value / scaleMax;
          const point = { x: plot.right, y: y, value: value, time: null };
          series.points.push(point);
          series.segments.push({ from: { ...point, x: plot.left }, to: point });
        }
        return series;
      }).filter((series) => series.points.length);
    }

    function trafficSegmentDistance(pointer, segment) {
      const dx = segment.to.x - segment.from.x;
      const dy = segment.to.y - segment.from.y;
      const length = dx * dx + dy * dy;
      const fraction = length ? Math.max(0, Math.min(1,
        ((pointer.x - segment.from.x) * dx + (pointer.y - segment.from.y) * dy) / length)) : 0;
      const x = segment.from.x + fraction * dx;
      const y = segment.from.y + fraction * dy;
      return { distance: Math.hypot(pointer.x - x, pointer.y - y), fraction: fraction };
    }

    function trafficLineHits(series, pointer, plot) {
      if (!pointer || pointer.x < plot.left || pointer.x > plot.right ||
          pointer.y < plot.top - 8 || pointer.y > plot.bottom + 8) return [];
      const hits = series.map((line) => {
        let best = { field: line.field, distance: Infinity, point: null };
        line.points.forEach((point) => {
          const distance = Math.hypot(pointer.x - point.x, pointer.y - point.y);
          if (distance < best.distance) best = { field: line.field, distance: distance, point: point };
        });
        line.segments.forEach((segment) => {
          const hit = trafficSegmentDistance(pointer, segment);
          if (hit.distance <= best.distance) {
            best = { field: line.field, distance: hit.distance,
              point: hit.fraction < 0.5 ? segment.from : segment.to };
          }
        });
        return best;
      });
      const nearest = Math.min(...hits.map((hit) => hit.distance));
      return hits.filter((hit) => hit.distance <= 8 && hit.distance <= nearest + 2);
    }

    function trafficAge(time, now) {
      if (time === null) return "not checked yet";
      const seconds = Math.max(0, Math.round((now - time) / 1000));
      return seconds === 0 ? "less than 1 second ago" : seconds + (seconds === 1 ? " second ago" : " seconds ago");
    }

    function trafficLineDetail(series, point, now) {
      const value = formatTrafficRate(point.value);
      if (!series.step) return series.label + ": " + value + " · measured at the Pi over the interval ending " + trafficAge(point.time, now);
      const kind = series.field.startsWith("isp_") ? "ISP plan reference" : "applied profile limit";
      const age = point.time === null ? "last read " + trafficAge(trafficReferencesReadAt, now)
        : "recorded " + trafficAge(point.time, now);
      const current = Number.isFinite(latestTrafficReferences[series.field]) ? "" : " · no current limit";
      return series.label + ": " + value + " · " + kind + " · " + age + current;
    }

    function drawTrafficKey(canvas, series, pixelRatio) {
      const width = 36;
      const height = 12;
      const scaledWidth = Math.round(width * pixelRatio);
      const scaledHeight = Math.round(height * pixelRatio);
      if (canvas.width !== scaledWidth || canvas.height !== scaledHeight) {
        canvas.width = scaledWidth;
        canvas.height = scaledHeight;
      }
      const context = canvas.getContext("2d");
      if (!context) return;
      context.setTransform(pixelRatio, 0, 0, pixelRatio, 0, 0);
      context.clearRect(0, 0, width, height);
      context.strokeStyle = series.color;
      context.lineWidth = 2.5;
      context.lineCap = "round";
      context.setLineDash(series.dash);
      context.beginPath();
      context.moveTo(2, height / 2);
      context.lineTo(width - 2, height / 2);
      context.stroke();
    }

    function requestTrafficChartDraw() {
      if (trafficChartRedraw !== null) return;
      trafficChartRedraw = window.requestAnimationFrame(() => {
        trafficChartRedraw = null;
        drawTrafficChart();
      });
    }

    function setupTrafficChartInspection() {
      element("traffic-chart").addEventListener("pointermove", (event) => {
        if (event.pointerType === "touch") return;
        trafficInspection.pointer = { clientX: event.clientX, clientY: event.clientY };
        trafficInspection.legend = null;
        requestTrafficChartDraw();
      });
      element("traffic-chart").addEventListener("pointerleave", () => {
        trafficInspection.pointer = null;
        requestTrafficChartDraw();
      });
      document.querySelectorAll(".traffic-legend button[data-traffic-series]").forEach((button) => {
        const inspect = () => {
          trafficInspection.legend = button.dataset.trafficSeries;
          trafficInspection.pointer = null;
          requestTrafficChartDraw();
        };
        button.addEventListener("pointerenter", (event) => { if (event.pointerType !== "touch") inspect(); });
        button.addEventListener("focus", inspect);
        button.addEventListener("pointerleave", () => {
          if (document.activeElement !== button && trafficInspection.legend === button.dataset.trafficSeries) {
            trafficInspection.legend = null;
            requestTrafficChartDraw();
          }
        });
        button.addEventListener("blur", () => {
          if (trafficInspection.legend === button.dataset.trafficSeries) trafficInspection.legend = null;
          requestTrafficChartDraw();
        });
        button.addEventListener("click", () => {
          const field = button.dataset.trafficSeries;
          trafficInspection.pinned = trafficInspection.pinned === field ? null : field;
          trafficInspection.legend = null;
          trafficInspection.pointer = null;
          requestTrafficChartDraw();
        });
      });
      document.addEventListener("keydown", (event) => {
        // An open question owns Escape: it answers no and leaves the highlight as it is.
        if (event.key !== "Escape" || element("page-traffic").hidden || document.querySelector("dialog[open]")) return;
        trafficInspection = { pointer: null, legend: null, pinned: null };
        requestTrafficChartDraw();
      });
    }

    function resetTrafficView() {
      latestTrafficReferences.profile_upload = null;
      latestTrafficReferences.profile_download = null;
      trafficProfileScope = null;
      resetTrafficHistory();
    }

    function drawTrafficChart() {
      const canvas = element("traffic-chart");
      const bounds = canvas.getBoundingClientRect();
      if (!bounds.width || !bounds.height) return;
      const pixelRatio = window.devicePixelRatio || 1;
      const width = Math.round(bounds.width * pixelRatio);
      const height = Math.round(bounds.height * pixelRatio);
      if (canvas.width !== width || canvas.height !== height) {
        canvas.width = width;
        canvas.height = height;
      }
      const context = canvas.getContext("2d");
      if (!context) return;
      context.setTransform(pixelRatio, 0, 0, pixelRatio, 0, 0);
      context.clearRect(0, 0, bounds.width, bounds.height);
      const now = performance.now();
      const visibleSamples = trafficSamples.filter((sample) => now - sample.time <= TRAFFIC_WINDOW_MS);
      const graphValues = visibleSamples.flatMap((sample) => TRAFFIC_SERIES.map((series) => sample[series.field]))
        .filter((value) => Number.isFinite(value));
      graphValues.push(...Object.values(latestTrafficReferences).filter((value) => Number.isFinite(value)));
      const scaleMax = Math.pow(10, Math.ceil(Math.log10(Math.max(0.1, ...graphValues))));
      context.font = "12px system-ui, sans-serif";
      const tickLabels = [0, 1, 2, 3, 4].map((tick) => formatTrafficRate(scaleMax * tick / 4));
      const labelWidth = Math.max(...tickLabels.map((label) => context.measureText(label).width));
      const plot = { left: Math.max(62, Math.ceil(labelWidth) + 16), right: bounds.width - 14,
        top: 16, bottom: bounds.height - 34 };
      const plotWidth = plot.right - plot.left;
      const plotHeight = plot.bottom - plot.top;
      context.textBaseline = "middle";
      for (let tick = 0; tick <= 4; tick += 1) {
        const y = plot.bottom - plotHeight * tick / 4;
        context.strokeStyle = "#263c56";
        context.lineWidth = 1;
        context.beginPath();
        context.moveTo(plot.left, y);
        context.lineTo(plot.right, y);
        context.stroke();
        context.fillStyle = "#91a8c1";
        context.textAlign = "right";
        context.fillText(tickLabels[tick], plot.left - 8, y);
      }
      [0, 0.5, 1].forEach((fraction) => {
        const x = plot.left + plotWidth * fraction;
        context.fillStyle = "#91a8c1";
        context.textAlign = fraction === 0 ? "left" : fraction === 1 ? "right" : "center";
        context.textBaseline = "alphabetic";
        context.fillText(fraction === 1 ? "now" : "−" + Math.round((1 - fraction) * TRAFFIC_WINDOW_MS / 1000) + "s", x, bounds.height - 8);
      });
      const series = trafficSeriesGeometry(visibleSamples, latestTrafficReferences, plot, scaleMax, now, trafficProfileScope);
      const present = new Set(series.map((line) => line.field));
      ["legend", "pinned"].forEach((key) => {
        if (!present.has(trafficInspection[key])) trafficInspection[key] = null;
      });
      const pointer = trafficInspection.pointer ? {
        x: trafficInspection.pointer.clientX - bounds.left,
        y: trafficInspection.pointer.clientY - bounds.top
      } : null;
      const hits = trafficLineHits(series, pointer, plot);
      const selected = trafficInspection.legend ? [trafficInspection.legend]
        : hits.length ? hits.map((hit) => hit.field)
        : trafficInspection.pinned ? [trafficInspection.pinned] : [];
      document.querySelectorAll(".traffic-legend button[data-traffic-series]").forEach((button) => {
        const line = series.find((item) => item.field === button.dataset.trafficSeries);
        button.hidden = !line;
        button.setAttribute("aria-pressed", String(trafficInspection.pinned === button.dataset.trafficSeries));
        button.classList.toggle("traffic-highlighted", selected.includes(button.dataset.trafficSeries));
        button.classList.toggle("traffic-dimmed", !!selected.length && !selected.includes(button.dataset.trafficSeries));
        if (!line) return;
        const label = button.querySelector("span");
        if (label.textContent !== line.label) label.textContent = line.label;
        const latest = line.points[line.points.length - 1];
        button.setAttribute("aria-label", trafficLineDetail(line, latest, now) + ". Keep this line highlighted.");
        drawTrafficKey(button.querySelector("canvas"), line, pixelRatio);
      });
      const detail = element("traffic-chart-detail");
      if (selected.length > 1) {
        detail.textContent = "Overlapping lines: " + selected.map((field) => {
          const line = series.find((item) => item.field === field);
          const point = hits.find((hit) => hit.field === field).point;
          return trafficLineDetail(line, point, now);
        }).join(" | ") + ". Choose a legend label to inspect one.";
      } else if (selected.length) {
        const line = series.find((item) => item.field === selected[0]);
        const hit = !trafficInspection.legend && hits.find((item) => item.field === line.field);
        detail.textContent = trafficLineDetail(line, hit ? hit.point : line.points[line.points.length - 1], now);
      } else {
        detail.textContent = TRAFFIC_INSPECTION_HELP;
      }
      const ordered = [...series.filter((line) => !selected.includes(line.field)),
        ...series.filter((line) => selected.includes(line.field))];
      ordered.forEach((line) => {
        const highlighted = selected.includes(line.field);
        context.strokeStyle = selected.length && !highlighted ? "#64748b" : line.color;
        context.globalAlpha = selected.length && !highlighted ? 0.4 : 1;
        context.lineWidth = highlighted ? 3.5 : line.step ? 1.8 : 2.5;
        context.lineJoin = "round";
        context.lineCap = "round";
        context.setLineDash(line.dash);
        context.beginPath();
        line.segments.forEach((segment) => {
          context.moveTo(segment.from.x, segment.from.y);
          context.lineTo(segment.to.x, segment.to.y);
        });
        context.stroke();
        context.setLineDash([]);
        line.points.forEach((point) => {
          if (line.points.length !== 1 && line.segments.some((segment) => segment.from === point || segment.to === point)) return;
          context.beginPath();
          context.arc(point.x, point.y, highlighted ? 3 : 1.8, 0, Math.PI * 2);
          context.fillStyle = context.strokeStyle;
          context.fill();
        });
      });
      context.globalAlpha = 1;
      context.setLineDash([]);
      if (!visibleSamples.length) {
        context.fillStyle = "#91a8c1";
        context.textAlign = "center";
        context.textBaseline = "middle";
        context.fillText("Waiting for traffic samples...", plot.left + plotWidth / 2, plot.top + plotHeight / 2);
      }
    }

    function trafficReferencesFromResponse(data) {
      const ispLimits = data.isp_limits || currentIspTrafficLimits;
      if (data.isp_limits) {
        currentIspTrafficLimits = {
          upload_kbit: data.isp_limits.upload_kbit,
          download_kbit: data.isp_limits.download_kbit
        };
      }
      const selectedDevice = element("traffic-device").value;
      if (data.general_profile_limits && selectedDevice === "@all") {
        element("traffic-profile-status").textContent =
          "Shared profile limit and traffic for affected devices only. Protected traffic is excluded.";
        return {
          isp_upload: trafficRateMbit(ispLimits.upload_kbit),
          isp_download: trafficRateMbit(ispLimits.download_kbit),
          profile_upload: trafficRateMbit(data.general_profile_limits.upload_kbit),
          profile_download: trafficRateMbit(data.general_profile_limits.download_kbit)
        };
      }
      const limitKey = selectedDevice === EVERYONE_ELSE_TRAFFIC
        ? EVERYONE_ELSE_QUEUE_ID
        : selectedDevice;
      const deviceLimits = selectedDevice === "@all" || !data.profile_limits_available
        ? null
        : (data.profile_limits || {})[limitKey];
      const profileAvailable = selectedDevice !== "@all" &&
        data.profile_limits_available === true && deviceLimits !== undefined;
      element("traffic-profile-status").textContent = selectedDevice === "@all"
        ? "Select one device to see its applied default queue limit. Service-specific limits are not shown."
        : !data.profile_limits_available
          ? "No plan is running yet. Choose Start to see current default queue limits."
          : !profileAvailable
            ? "The selected device has no applied default queue limits available."
            : deviceLimits.upload_kbit === null && deviceLimits.download_kbit === null
              ? "The selected device has no default queue bandwidth cap. Service-specific queues may use other limits."
              : "The profile line is the applied default queue limit. Service-specific queues may use other limits.";
      return {
        isp_upload: trafficRateMbit(ispLimits.upload_kbit),
        isp_download: trafficRateMbit(ispLimits.download_kbit),
        profile_upload: profileAvailable ? trafficRateMbit(deviceLimits.upload_kbit) : null,
        profile_download: profileAvailable ? trafficRateMbit(deviceLimits.download_kbit) : null
      };
    }

    // The counters are only worth reading while somebody can see the graph.
    function trafficGraphShown() {
      return !document.hidden && currentPage() === "traffic";
    }

    function clearTrafficTimer() {
      if (trafficPollTimer !== null) window.clearTimeout(trafficPollTimer);
      trafficPollTimer = null;
    }

    function scheduleTrafficPoll() {
      clearTrafficTimer();
      if (!trafficSamplingAllowed || !trafficGraphShown()) return;
      trafficPollTimer = window.setTimeout(async () => {
        trafficPollTimer = null;
        try {
          await pollTraffic();
        } finally {
          scheduleTrafficPoll();
        }
      }, TRAFFIC_POLL_INTERVAL_MS);
    }

    // One reading of the counters serves the graph and the device workspace alike. Asking
    // again within maxAgeMs gives the reading already in hand, and asking while one is on its
    // way waits for that one.
    let latestTrafficReading = null;

    let trafficReadingInFlight = null;

    function readTraffic(maxAgeMs) {
      const revision = adminTokenRevision;
      if (!adminTokenVerified) return Promise.reject(new Error("Check token to read traffic."));
      if (latestTrafficReading && latestTrafficReading.revision === revision &&
          Date.now() - latestTrafficReading.at < maxAgeMs) {
        return Promise.resolve(latestTrafficReading.data);
      }
      if (trafficReadingInFlight) {
        return trafficReadingInFlight.then(data => {
          if (revision !== adminTokenRevision || !adminTokenVerified) throw new Error("Token changed. Check token again.");
          if (!latestTrafficReading || latestTrafficReading.revision !== revision) throw new Error("Traffic belongs to an earlier sign-in. Check again.");
          return data;
        });
      }
      trafficReadingInFlight = request("/api/traffic", "GET").then(data => {
        if (revision !== adminTokenRevision || !adminTokenVerified) throw new Error("Token changed. Check token again.");
        latestTrafficReading = {data, at: Date.now(), revision};
        return data;
      }).finally(() => { trafficReadingInFlight = null; });
      return trafficReadingInFlight;
    }

    let trafficFailures = 0;

    // The graph and the numbers stay as they are for two failed readings in a row. The third
    // clears them, because by then they no longer show what is happening.
    const TRAFFIC_FAILURES_BEFORE_CLEARING = 3;

    async function pollTraffic() {
      if (trafficRequestInFlight || !trafficSamplingAllowed || !trafficGraphShown()) return;
      const revision = adminTokenRevision;
      trafficRequestInFlight = true;
      try {
        const data = await readTraffic(0);
        trafficFailures = 0;
        if (revision !== adminTokenRevision || !adminTokenVerified || !trafficSamplingAllowed) return;
        if (!data.available) {
          stopTrafficSampling(data.message || "Live traffic counters are unavailable in this mode.");
          return;
        }
        const selectedDevice = element("traffic-device").value;
        const scope = data.general_profile_limits && selectedDevice === "@all" ? "shared"
          : selectedDevice === EVERYONE_ELSE_TRAFFIC ? "everyone" : "device";
        if (trafficProfileScope !== null && trafficProfileScope !== scope) resetTrafficHistory();
        trafficProfileScope = scope;
        trafficReferencesReadAt = performance.now();
        latestTrafficReferences = trafficReferencesFromResponse(data);
        const counters = mergedTrafficCounters(data);
        const totals = selectedTrafficTotals(counters.devices);
        const queueTotals = selectedQueueTotals(counters.queues);
        if (!totals) {
          resetTrafficHistory();
          element("traffic-status").textContent = "No counters yet. Assign a profile and choose Start.";
          return;
        }
        const sampledAt = performance.now();
        if (previousTrafficSample) {
          const elapsedSeconds = (sampledAt - previousTrafficSample.time) / 1000;
          const fields = Object.keys(totals);
          const countersReset = fields.some((field) => totals[field] < previousTrafficSample.totals[field]);
          if (countersReset || elapsedSeconds <= 0) {
            resetTrafficHistory();
            element("traffic-status").textContent = "Counters changed. Starting a fresh graph...";
          } else {
            const upload = Math.max(0, totals.upload_bytes - previousTrafficSample.totals.upload_bytes) * 8 / elapsedSeconds / 1000000;
            const download = Math.max(0, totals.download_bytes - previousTrafficSample.totals.download_bytes) * 8 / elapsedSeconds / 1000000;
            const uploadPackets = Math.max(0, totals.upload_packets - previousTrafficSample.totals.upload_packets) / elapsedSeconds;
            const downloadPackets = Math.max(0, totals.download_packets - previousTrafficSample.totals.download_packets) / elapsedSeconds;
            trafficSamples.push({
              time: sampledAt,
              upload: upload,
              download: download,
              ...latestTrafficReferences
            });
            trafficSamples = trafficSamples
              .filter((sample) => sampledAt - sample.time <= TRAFFIC_WINDOW_MS)
              .slice(-TRAFFIC_MAX_POINTS);
            element("traffic-upload-rate").textContent = formatTrafficRate(upload);
            element("traffic-download-rate").textContent = formatTrafficRate(download);
            element("traffic-upload-packets").textContent = formatPacketRate(uploadPackets);
            element("traffic-download-packets").textContent = formatPacketRate(downloadPackets);
            // Drops restart from zero when a plan is applied.
            const dropped = Math.max(0, queueTotals.dropped - previousTrafficSample.queues.dropped);
            element("traffic-drop-rate").textContent = formatPacketRate(dropped / elapsedSeconds);
            element("traffic-queued").textContent = queueTotals.queued + " packets";
            element("traffic-status").textContent = "Sampling live traffic every 2 seconds.";
          }
        } else {
          element("traffic-status").textContent = "Collecting the first sample...";
        }
        previousTrafficSample = { time: sampledAt, totals: totals, queues: queueTotals };
        drawTrafficChart();
      } catch (error) {
        if (revision !== adminTokenRevision || !adminTokenVerified || !trafficSamplingAllowed) return;
        if (error.status === 401) {
          resetTrafficHistory();
          stopTrafficSampling("The admin token was rejected. Check it again to restart live traffic sampling.");
          return;
        }
        trafficFailures += 1;
        if (trafficFailures >= TRAFFIC_FAILURES_BEFORE_CLEARING) {
          resetTrafficHistory();
          element("traffic-status").textContent =
            "Live traffic update failed: " + error.message + ". Retrying in 2 seconds.";
        } else {
          element("traffic-status").textContent =
            "Updating… the last reading is kept while the Pi catches up.";
        }
      } finally {
        trafficRequestInFlight = false;
      }
    }

    async function startTrafficSampling() {
      trafficSamplingAllowed = true;
      trafficFailures = 0;
      clearTrafficTimer();
      resetTrafficHistory();
      if (!trafficGraphShown()) {
        element("traffic-status").textContent = document.hidden
          ? "Sampling will start when this tab is visible."
          : "Sampling starts when the Traffic page is open.";
        return;
      }
      await pollTraffic();
      scheduleTrafficPoll();
    }

    // Called when the page changes or the tab is hidden or shown.
    function syncTrafficSampling() {
      if (!trafficSamplingAllowed) return;
      if (!trafficGraphShown()) {
        if (trafficPollTimer === null && previousTrafficSample === null) return;
        clearTrafficTimer();
        resetTrafficHistory();
        element("traffic-status").textContent = document.hidden
          ? "Sampling paused while this tab is hidden."
          : "Sampling starts when the Traffic page is open.";
        return;
      }
      if (trafficPollTimer !== null || trafficRequestInFlight) return;
      element("traffic-status").textContent = "Resuming live traffic sampling...";
      pollTraffic().then(scheduleTrafficPoll);
    }

    function refreshAfterSuccess(message) {
      showMessage(message, "success");
      const written = Array.from(recentWrites);
      recentWrites.clear();
      refresh(readsAfterWrites(written)).then(() => {
        if (adminTokenVerified && helperConfigured && !trafficSamplingAllowed) {
          return startTrafficSampling();
        }
      }).catch((error) => {
        showMessage(message + " Dashboard refresh failed: " + error.message, "error");
      });
    }

    function setButtonBusy(button, busy) {
      button.disabled = busy;
      if (busy) button.setAttribute("aria-busy", "true");
      else button.removeAttribute("aria-busy");
    }

    // Ask before a button's action with the one in-page dialog. The button stays busy while the
    // question is open, so a second click cannot open another one. Cancel and Escape make it
    // usable again and focus returns to it. After a yes the caller owns the busy state.
    async function confirmFromButton(button, message, options) {
      setButtonBusy(button, true);
      let confirmed = false;
      try { confirmed = await confirmWorkflow(message, {...options, returnFocus: button}); }
      finally { if (!confirmed) setButtonBusy(button, false); }
      return confirmed;
    }

    let toastTimer = null;
    let toastDismissDelay = 0;
    let toastReturnFocus = null;

    function cancelToastDismissal() {
      if (toastTimer !== null) window.clearTimeout(toastTimer);
      toastTimer = null;
    }

    function scheduleToastDismissal() {
      cancelToastDismissal();
      const toast = element("toast");
      if (!toastDismissDelay || toast.hidden || toast.contains(document.activeElement) || toast.matches(":hover")) return;
      toastTimer = window.setTimeout(() => {
        toastTimer = null;
        if (!toast.contains(document.activeElement) && !toast.matches(":hover")) toast.hidden = true;
      }, toastDismissDelay);
    }

    // Say a result once. Errors go to the alert region and everything else to the status region.
    // Both are always in the page and neither changes its aria-live, so a screen reader that
    // already watches them hears each write. Polled lists never come through here.
    function announce(message, assertive) {
      const speaking = element(assertive ? "action-alert" : "action-announcer");
      const quiet = element(assertive ? "action-announcer" : "action-alert");
      if (quiet.textContent) quiet.textContent = "";
      speaking.textContent = message;
    }

    function showToast(message, kind) {
      const toast = element("toast");
      const icon = element("toast-icon");
      cancelToastDismissal();
      if (!toast.contains(document.activeElement)) toastReturnFocus = document.activeElement;
      element("toast-message").textContent = message;
      toast.className = "toast " + (kind || "working");
      announce(message, kind === "error");
      icon.textContent = kind === "success" ? "✓" : kind === "error" ? "!" : "";
      toast.hidden = false;
      toastDismissDelay = kind === "working" || kind === "error" ? 0 : 6000;
      scheduleToastDismissal();
    }

    function dismissToast() {
      cancelToastDismissal();
      const toast = element("toast");
      const focused = toast.contains(document.activeElement);
      toast.hidden = true;
      if (focused) {
        const target = toastReturnFocus && toastReturnFocus.isConnected &&
          !toastReturnFocus.disabled && toastReturnFocus.getClientRects().length &&
          (toastReturnFocus.tabIndex >= 0 || toastReturnFocus.hasAttribute("tabindex") || toastReturnFocus.isContentEditable)
          ? toastReturnFocus : element("brand-title");
        if (!target.hasAttribute("tabindex") && target === element("brand-title")) target.setAttribute("tabindex", "-1");
        target.focus({preventScroll: true});
      }
    }

    element("toast").addEventListener("pointerenter", cancelToastDismissal);
    element("toast").addEventListener("pointerleave", scheduleToastDismissal);
    element("toast").addEventListener("focusin", cancelToastDismissal);
    element("toast").addEventListener("focusout", () => window.setTimeout(scheduleToastDismissal, 0));

    document.addEventListener("submit", (event) => {
      // Checking the token has its own progress note and never saves anything.
      if (event.target.id === "admin-token-form") return;
      const label = event.submitter ? event.submitter.textContent.trim() : "Save";
      const saving = label.toLowerCase().startsWith("save ")
        ? "Saving " + label.slice(5) + "..."
        : label + " in progress...";
      if (event.submitter) {
        pendingFormSubmitButton = event.submitter;
        setButtonBusy(event.submitter, true);
      }
      showMessage(saving, "working");
    }, true);

    let wifiResetPending = false;
    let wifiChangePending = false;

    function renderMode(data) {
      helperConfigured = data.helper_configured;
      helperKnown = true;
      renderRuntimeText();
      const badge = element("mode-badge");
      const notice = element("mode-notice");
      badge.textContent = helperConfigured ? "Hardware mode" : "Configuration only";
      badge.className = "badge " + (helperConfigured ? "hardware" : "configuration");
      notice.textContent = helperConfigured
        ? "Hardware mode is active. Network actions can update the live test network."
        : "Configuration-only mode is active. Changes are saved but live network actions are unavailable.";
      element("apply-button").disabled = networkActionBusy || !helperConfigured;
      element("reset-button").disabled = networkActionBusy || !helperConfigured;
      element("panic-button").disabled = !helperConfigured;
      element("scan-observations").disabled = !helperConfigured;
      element("reset-wifi-name").disabled = !helperConfigured || wifiResetPending;
      updateGeneralProfileControls();
    }

    function updateGeneralProfileControls() {
      const select = element("general-profile-select");
      select.disabled = networkActionBusy || !helperConfigured || profiles.length === 0;
      element("general-profile-apply").disabled = select.disabled || !select.value;
      element("general-profile-stop").disabled = networkActionBusy || !helperConfigured ||
        !generalProfileState || !generalProfileState.profile;
      renderSavedProfiles();
      if (typeof renderActiveProfileControl === "function") renderActiveProfileControl();
    }

    const IMPACT_WINDOW_SECONDS = 60;

    const IMPACT_MIN_PACKETS = 50;

    const IMPACT_CAP_RATIO = 0.9;

    const IMPACT_MAX_SAMPLES = 13;

    const IMPACT_MIN_EXPECTED_DROPS = 3;

    const IMPACT_MIN_QUEUE_WAIT_MS = 10;

    const IMPACT_NOT_MEASURED = " · configured, not measured";

    function impactSample(entry, nowSeconds) {
      return {
        t: nowSeconds,
        packets: entry.packets,
        drops: entry.drops,
        bytes: entry.bytes,
        overlimits: entry.overlimits,
        backlog_bytes: entry.backlog_bytes,
        qlen: entry.qlen || 0
      };
    }

    function impactQueueReplaced(previous, entry, sample) {
      const last = previous.samples[previous.samples.length - 1];
      return !last || sample.t <= last.t || sample.packets < last.packets ||
        sample.drops < last.drops || sample.bytes < last.bytes ||
        previous.latest.generation !== entry.generation ||
        previous.latest.device_id !== entry.device_id ||
        previous.latest.direction !== entry.direction ||
        JSON.stringify(previous.latest.impairment) !== JSON.stringify(entry.impairment);
    }

    function recordImpactSample(history, response, nowSeconds) {
      const next = {};
      (response.queues || []).forEach((entry) => {
        const key = entry.interface + "/" + entry.handle;
        const sample = impactSample(entry, nowSeconds);
        const previous = history[key];
        const kept = previous && !impactQueueReplaced(previous, entry, sample)
          ? previous.samples : [];
        next[key] = {latest: entry, samples: kept.concat([sample]).slice(-IMPACT_MAX_SAMPLES)};
      });
      return next;
    }

    function impactHasNoImpairment(impairment) {
      return !impairment.delay_ms && !impairment.jitter_ms && !impairment.loss_percent &&
        !impairment.bandwidth_kbit && !impairment.reorder_percent &&
        !impairment.duplicate_percent && !impairment.corrupt_percent;
    }

    function formatImpactRate(kbit) {
      return kbit >= 1000 ? (Math.round(kbit / 10) / 100) + " Mbit/s" : kbit + " kbit/s";
    }

    function impactIntervals(used) {
      const intervals = [];
      for (let index = 1; index < used.length; index += 1) {
        const before = used[index - 1];
        const after = used[index];
        const seconds = after.t - before.t;
        if (seconds <= 0) continue;
        intervals.push({
          offered: after.packets - before.packets + after.drops - before.drops,
          kbit: (after.bytes - before.bytes) * 8 / 1000 / seconds
        });
      }
      return intervals;
    }

    function impactVerdict(impairment, offered, packets, drops, capReached) {
      if (drops > 0 || capReached) return "affected";
      if (offered === 0 && packets === 0) return "idle";
      const hasLoss = impairment.loss_percent > 0;
      if (!hasLoss && !impairment.bandwidth_kbit) return "unmeasured";
      if (offered < IMPACT_MIN_PACKETS ||
          (hasLoss && offered * impairment.loss_percent / 100 < IMPACT_MIN_EXPECTED_DROPS)) {
        return "too-few";
      }
      return "clean";
    }

    function impactLossLine(impairment, drops, offered, lossPercent) {
      const head = impairment.loss_percent > 0
        ? "Loss: set " + impairment.loss_percent + "%"
        : impairment.bandwidth_kbit ? "Dropped (queue full at the speed cap)" : "Dropped (queue full)";
      return head + " · actual " + lossPercent + "% (" + drops + " of " + offered +
        " packets dropped)" + (impairment.duplicate_percent > 0
          ? " (may read low because duplicated packets are counted)" : "");
    }

    function impactRow(key, entry) {
      const latest = entry.latest;
      const impairment = latest.impairment;
      const row = {
        key: key,
        name: latest.name,
        direction: latest.direction,
        configured: impairment,
        windowSeconds: 0,
        offered: 0,
        packets: 0,
        drops: 0,
        lossPercent: 0,
        rateKbit: 0,
        peakKbit: 0,
        capReached: false,
        queueWaitMs: null,
        verdict: "collecting",
        lines: ["Collecting…"]
      };
      const newest = entry.samples[entry.samples.length - 1];
      const used = entry.samples.filter((sample) => newest.t - sample.t <= IMPACT_WINDOW_SECONDS);
      const first = used[0];
      const seconds = newest.t - first.t;
      if (used.length < 2 || seconds <= 0) return row;
      const packets = newest.packets - first.packets;
      const drops = newest.drops - first.drops;
      // Drops are counted when a packet arrives and packets when they leave. A packet that
      // still waits in the queue has arrived so the queue length change is added.
      const waiting = newest.qlen - first.qlen;
      const offered = Math.max(0, packets + drops + waiting);
      const intervals = impactIntervals(used);
      const limit = impairment.bandwidth_kbit;
      const noDelay = !impairment.delay_ms && !impairment.jitter_ms;
      // Without a delay a backlog of at least 10 ms of cap time can only come from the cap.
      const backlogSeen = !!limit && noDelay && used.some((sample) =>
        Math.round(sample.backlog_bytes * 8 / limit) >= IMPACT_MIN_QUEUE_WAIT_MS);
      const capReached = !!limit && (backlogSeen || intervals.some((interval) =>
        interval.offered > 0 && Math.round(interval.kbit) >= IMPACT_CAP_RATIO * limit));
      row.windowSeconds = seconds;
      row.offered = offered;
      row.packets = packets;
      row.drops = drops;
      row.lossPercent = offered ? Math.round(drops * 1000 / offered) / 10 : 0;
      row.rateKbit = Math.round((newest.bytes - first.bytes) * 8 / 1000 / seconds);
      row.peakKbit = Math.round(Math.max(0, ...intervals.map((interval) => interval.kbit)));
      row.capReached = capReached;
      row.queueWaitMs = limit && noDelay && capReached
        ? Math.round(newest.backlog_bytes * 8 / limit) : null;
      row.verdict = impactVerdict(impairment, offered, packets, drops, capReached);
      row.lines = [];
      if (row.verdict === "unmeasured") {
        row.lines.push(packets + (packets === 1 ? " packet" : " packets") + " passed in the last minute");
      }
      if (impairment.loss_percent > 0 || drops > 0) {
        row.lines.push(impactLossLine(impairment, drops, offered, row.lossPercent));
      }
      if (limit) {
        row.lines.push("Speed: set " + formatImpactRate(limit) + " · using " +
          formatImpactRate(row.rateKbit) + " · peak " + formatImpactRate(row.peakKbit) +
          (capReached ? " · cap reached" : " · below the cap"));
      }
      if (row.queueWaitMs !== null && row.queueWaitMs >= IMPACT_MIN_QUEUE_WAIT_MS) {
        row.lines.push("Queue wait now: about " + row.queueWaitMs + " ms");
      }
      if (impairment.delay_ms > 0 || impairment.jitter_ms > 0) {
        row.lines.push("Delay: set " + impairment.delay_ms + " ms" +
          (impairment.jitter_ms > 0 ? " ± " + impairment.jitter_ms : "") + IMPACT_NOT_MEASURED);
      }
      [["reorder_percent", "Reorder"], ["duplicate_percent", "Duplicate"],
       ["corrupt_percent", "Corrupt"]].forEach((item) => {
        if (impairment[item[0]] > 0) {
          row.lines.push(item[1] + ": set " + impairment[item[0]] + "%" + IMPACT_NOT_MEASURED);
        }
      });
      return row;
    }

    function impactRows(history) {
      return Object.keys(history)
        .filter((key) => !impactHasNoImpairment(history[key].latest.impairment))
        .map((key) => impactRow(key, history[key]))
        .sort((a, b) => a.name.localeCompare(b.name) ||
          (a.direction === b.direction ? 0 : a.direction === "upload" ? -1 : 1));
    }
    // end of the impact functions

    const IMPACT_POLL_INTERVAL_MS = 5000;

    const IMPACT_UNREADABLE = "Impact could not be read right now.";

    const IMPACT_VERDICT_LABELS = {
      affected: "Affected in the last minute",
      idle: "No traffic in the last minute",
      "too-few": "Too few packets to judge",
      clean: "No drops or cap hit seen",
      unmeasured: "Effect not measured here",
      collecting: "Collecting…"
    };

    let impactHistory = {};

    let impactPollTimer = null;

    let impactRequestInFlight = false;

    let impactGeneration = 0;

    let impactLastGoodAt = null;

    let impactRowParts = new Map();

    let impactVerdicts = {};

    let impactShowsRows = false;

    function impactVisible() {
      return document.body.dataset.section === "diagnostics" && !document.hidden;
    }

    function setImpactText(node, text) {
      if (node.textContent !== text) node.textContent = text;
    }

    function announceImpact(text) {
      element("impact-status").textContent = text;
    }

    function showImpactMessage(message, state) {
      const list = element("impact-list");
      if (impactShowsRows || list.textContent !== message) list.textContent = message;
      impactShowsRows = false;
      impactRowParts = new Map();
      impactVerdicts = {};
      setImpactText(element("impact-state"), state);
      element("impact-note").hidden = true;
      element("impact-protected").hidden = true;
      if (element("impact-status").textContent !== message) announceImpact(message);
    }

    function stopImpact(message, state) {
      if (impactPollTimer !== null) {
        window.clearInterval(impactPollTimer);
        impactPollTimer = null;
      }
      impactGeneration += 1;
      impactHistory = {};
      impactLastGoodAt = null;
      showImpactMessage(message, state);
    }

    function createImpactRowParts() {
      const item = document.createElement("div");
      item.className = "impact-row";
      const head = document.createElement("div");
      head.className = "impact-head";
      const name = document.createElement("span");
      name.className = "impact-name";
      const direction = document.createElement("span");
      direction.className = "impact-direction";
      const chip = document.createElement("span");
      head.append(name, direction, chip);
      const lines = document.createElement("ul");
      lines.className = "impact-lines";
      item.append(head, lines);
      return {item: item, name: name, direction: direction, chip: chip, lines: lines};
    }

    function updateImpactRow(parts, row) {
      setImpactText(parts.name, row.name);
      setImpactText(parts.direction, row.direction === "upload" ? "Upload" : "Download");
      const chipClass = "impact-chip " + row.verdict;
      if (parts.chip.className !== chipClass) parts.chip.className = chipClass;
      setImpactText(parts.chip, IMPACT_VERDICT_LABELS[row.verdict]);
      const lines = row.verdict === "collecting" ? [] : row.lines;
      lines.forEach((text, index) => {
        if (!parts.lines.children[index]) parts.lines.appendChild(document.createElement("li"));
        setImpactText(parts.lines.children[index], text);
      });
      while (parts.lines.children.length > lines.length) {
        parts.lines.children[parts.lines.children.length - 1].remove();
      }
      parts.lines.hidden = lines.length === 0;
    }

    function impactAnnouncements(rows) {
      const spoken = [];
      rows.forEach((row) => {
        const before = impactVerdicts[row.key];
        if (before !== undefined && before !== row.verdict && row.verdict !== "collecting") {
          spoken.push(row.name + " " + row.direction + ": " + IMPACT_VERDICT_LABELS[row.verdict]);
        }
      });
      impactVerdicts = {};
      rows.forEach((row) => { impactVerdicts[row.key] = row.verdict; });
      return spoken;
    }

    function renderImpactRows(rows) {
      const list = element("impact-list");
      if (!impactShowsRows) {
        list.textContent = "";
        impactShowsRows = true;
      }
      const wanted = new Set(rows.map((row) => row.key));
      impactRowParts.forEach((parts, key) => {
        if (!wanted.has(key)) {
          parts.item.remove();
          impactRowParts.delete(key);
        }
      });
      rows.forEach((row, index) => {
        let parts = impactRowParts.get(row.key);
        if (!parts) {
          parts = createImpactRowParts();
          impactRowParts.set(row.key, parts);
        }
        updateImpactRow(parts, row);
        if (list.children[index] !== parts.item) {
          list.insertBefore(parts.item, list.children[index] || null);
        }
      });
    }

    function renderImpact(protectedNames) {
      const rows = impactRows(impactHistory);
      if (rows.length) {
        renderImpactRows(rows);
        const spoken = impactAnnouncements(rows);
        if (spoken.length) announceImpact(spoken.join(". "));
      } else {
        showImpactMessage(
          "Nothing is slowed down right now. Apply a profile to a device to see its impact here.",
          "Live");
      }
      setImpactText(element("impact-state"), "Live");
      element("impact-note").hidden = false;
      const protectedLine = element("impact-protected");
      protectedLine.hidden = protectedNames.length === 0;
      setImpactText(protectedLine, protectedNames.length
        ? "Protected (not affected): " + protectedNames.join(", ") : "");
    }

    function pauseImpact(message) {
      const age = impactLastGoodAt === null ? Infinity : (Date.now() - impactLastGoodAt) / 1000;
      if (age > IMPACT_WINDOW_SECONDS) {
        impactHistory = {};
        impactLastGoodAt = null;
        showImpactMessage(message, "Update paused");
        return;
      }
      setImpactText(element("impact-state"), "Update paused");
    }

    async function loadImpact() {
      if (impactRequestInFlight || !adminTokenVerified || !impactVisible()) return;
      impactRequestInFlight = true;
      const generation = impactGeneration;
      try {
        const data = await request("/api/impact", "GET");
        if (generation !== impactGeneration) return;
        if (data.paused) {
          pauseImpact(data.message || IMPACT_UNREADABLE);
          return;
        }
        if (!data.available) {
          impactHistory = {};
          impactLastGoodAt = null;
          showImpactMessage(data.message || "Impact is not available.", "Unavailable");
          return;
        }
        impactHistory = recordImpactSample(impactHistory, data, data.sampled_at);
        impactLastGoodAt = Date.now();
        renderImpact(Array.isArray(data.protected) ? data.protected : []);
      } catch (error) {
        if (generation !== impactGeneration) return;
        if (error.status === 401) {
          stopImpact("Check the admin token to see the impact.", "Check token");
        } else {
          pauseImpact(IMPACT_UNREADABLE);
        }
      } finally {
        impactRequestInFlight = false;
      }
    }

    function syncImpact() {
      if (!adminTokenVerified) {
        stopImpact("Check the admin token to see the impact.", "Check token");
        return;
      }
      if (!helperConfigured) {
        stopImpact("Impact needs the Pi helper.", "Unavailable");
        return;
      }
      if (!impactVisible()) {
        if (impactPollTimer !== null) {
          window.clearInterval(impactPollTimer);
          impactPollTimer = null;
        }
        return;
      }
      if (impactPollTimer === null) {
        impactPollTimer = window.setInterval(loadImpact, IMPACT_POLL_INTERVAL_MS);
        loadImpact();
      }
    }
    // end of the impact card

    function sidebarStatus(input) {
      const visible = !!input.adminTokenVerified;
      const loaded = input.loaded || {};
      const ready = ["runtime", "general", "clients", "profiles", "lists"]
        .every((name) => loaded[name] === true);
      if (!ready) {
        return {
          visible,
          loading: true,
          general: {text: "Loading…", tone: "muted"},
          devices: {summary: "", items: [], more: 0},
          lists: {text: ""}
        };
      }
      const state = input.runtimeState || "unknown";
      const running = input.running === true;
      const generalState = input.generalState || null;
      const generalProfile = generalState && generalState.profile ? generalState.profile : null;
      const profiles = input.profiles || [];
      const clients = input.clients || [];
      const serviceLists = input.serviceLists || [];
      const steady = running && (state === "applied" || state === "unknown");
      // A device-only start runs its own devices. Everyone and the rest stay paused.
      const deviceOnly = Array.isArray(input.deviceOnly) ? input.deviceOnly : [];
      const generalInEffect = !!generalProfile && steady && deviceOnly.length === 0 &&
        (generalState.active === true || state === "unknown");
      const generalName = generalProfile ? generalProfile.name || generalProfile.id : "";

      let general = {text: "Everyone: none", tone: "muted"};
      if (generalProfile) {
        general = generalInEffect
          ? {text: "Everyone: " + generalName + " · active", tone: "on"}
          : {
              text: "Everyone: " + generalName + " · " +
                (state === "reset" || deviceOnly.length ? "paused" : "saved, not applied"),
              tone: "muted"
            };
      }

      const quiet = (summary) => ({summary, items: [], more: 0});
      let devices;
      if (state === "reset") {
        devices = quiet("Profiles off");
      } else if (state === "applying" || state === "resetting") {
        devices = quiet("Changing…");
      } else if (state === "error") {
        devices = quiet(input.lastAction === "reset"
          ? "Last reset failed. The Pi may still be shaping."
          : "Last network action failed");
      } else if (!running) {
        devices = quiet("Not running");
      } else if (input.clientsAvailable !== true) {
        devices = quiet("Connected devices unavailable");
      } else {
        const nameOf = (client) => client.display_name || client.mac_address || "Unknown device";
        const profileNameOf = (id) => {
          const match = profiles.find((profile) => profile.id === id);
          return match ? match.name || id : id || "";
        };
        const affected = generalInEffect
          ? clients
              .filter((client) => !client.unaffected && client.association_verified !== false)
              .map((client) => ({name: nameOf(client), profile: generalName}))
          : clients
              .filter((client) => !client.unaffected && client.assigned_device_id &&
                client.assigned_effective_state !== "unverified" &&
                client.assigned_effective_state !== "protected" &&
                (deviceOnly.length === 0 || deviceOnly.includes(client.assigned_device_id)))
              .map((client) => ({
                name: nameOf(client),
                profile: profileNameOf(client.group_profile_id || client.assigned_profile_id)
              }));
        const unassigned = !generalInEffect && clients.length > 0 &&
          !clients.some((client) => client.assigned_device_id);
        devices = {
          summary: affected.length
            ? affected.length + (affected.length === 1 ? " device affected" : " devices affected")
            : unassigned ? "No device has its own profile" : "No connected device is affected",
          items: affected.slice(0, 5),
          more: Math.max(0, affected.length - 5)
        };
      }

      const enabledLists = steady && deviceOnly.length === 0
        ? serviceLists.filter((serviceList) => serviceList.enabled).map((serviceList) => serviceList.name)
        : [];
      return {
        visible,
        loading: false,
        general,
        devices,
        lists: {
          text: enabledLists.length
            ? "Service lists: " + enabledLists.join(" · ") + " (every unprotected device)"
            : ""
        }
      };
    }

    function clearSidebarInputs() {
      lastRuntimeState = null;
      generalProfileState = null;
      lastConnectedData = null;
      connectedClients = [];
      connectedClientsAvailable = false;
      serviceListsState = [];
      serviceListsLoaded = false;
      profilesLoaded = false;
      unaffectedMacs = null;
      accessRulesSaved = null;
      savedDevicesLoaded = false;
      groupsLoaded = false;
    }

    function renderSidebarStatus() {
      const view = sidebarStatus({
        adminTokenVerified,
        loaded: {
          runtime: lastRuntimeState !== null,
          general: generalProfileState !== null,
          clients: lastConnectedData !== null,
          profiles: profilesLoaded,
          lists: serviceListsLoaded
        },
        runtimeState: lastRuntimeState ? lastRuntimeState.observed_state : null,
        running: !!lastRuntimeState && lastRuntimeState.running === true,
        lastAction: lastRuntimeState ? lastRuntimeState.last_action : null,
        deviceOnly: lastRuntimeState ? lastRuntimeState.device_only : null,
        generalState: generalProfileState,
        profiles,
        clients: connectedClients,
        clientsAvailable: lastConnectedData !== null && connectedClientsAvailable,
        serviceLists: serviceListsState
      });
      element("sidebar-status").hidden = !view.visible;
      renderApplySummary();
      renderApplyButton();
      if (typeof renderActiveProfileControl === "function") renderActiveProfileControl();
      const generalLine = element("status-general");
      const devicesBlock = element("status-devices");
      const devicesPlain = element("status-devices-plain");
      const devicesSummary = element("status-devices-summary");
      const list = element("status-devices-list");
      const more = element("status-devices-more");
      const listsLine = element("status-lists");
      if (!view.visible) {
        // Nothing private stays in the page while the dashboard is locked.
        clearSidebarInputs();
        generalLine.textContent = "";
        generalLine.className = "status-line";
        devicesBlock.hidden = true;
        devicesPlain.hidden = true;
        devicesPlain.textContent = "";
        devicesSummary.textContent = "";
        list.replaceChildren();
        more.hidden = true;
        more.textContent = "";
        listsLine.hidden = true;
        listsLine.textContent = "";
        return;
      }
      generalLine.textContent = view.general.text;
      generalLine.className = "status-line" + (view.general.tone === "on" ? " status-on" : "");
      // Only a list of devices opens and closes. Anything else is one plain line.
      const hasItems = view.devices.items.length > 0;
      devicesBlock.hidden = !hasItems;
      devicesSummary.textContent = hasItems ? view.devices.summary : "";
      devicesPlain.hidden = hasItems || view.loading;
      devicesPlain.textContent = hasItems || view.loading ? "" : view.devices.summary;
      list.replaceChildren(...view.devices.items.map((item) => {
        const row = document.createElement("li");
        const name = document.createElement("span");
        name.className = "status-device-name";
        name.textContent = item.name;
        row.appendChild(name);
        if (item.profile) {
          const profile = document.createElement("span");
          profile.className = "status-device-profile";
          profile.textContent = item.profile;
          row.appendChild(profile);
        }
        return row;
      }));
      more.hidden = view.devices.more === 0;
      more.textContent = view.devices.more ? "+" + view.devices.more + " more" : "";
      listsLine.hidden = !view.lists.text;
      listsLine.textContent = view.lists.text;
    }

    function applySummary(input) {
      const count = (number, one, many) => number + " " + (number === 1 ? one : many);
      const parts = [];
      if (input.generalProfileName) {
        // The server runs the enabled service lists together with the general profile.
        parts.push('Profile "' + input.generalProfileName + '" for every unprotected device');
      } else {
        if (input.devices > 0) {
          parts.push(count(input.devices, "device", "devices") + " with a profile");
        }
        if (input.groups > 0) parts.push(count(input.groups, "group", "groups"));
      }
      if (input.serviceLists > 0) {
        parts.push(count(input.serviceLists, "service list", "service lists"));
      }
      if (input.unverifiedDevices > 0) {
        parts.push(count(input.unverifiedDevices,
          "saved device profile needs an identity check",
          "saved device profiles need an identity check"));
      }
      if (input.protected > 0) parts.push(protectedDevicesText(input.protected));
      return parts.length ? parts.join(" · ") : "No device profiles or service lists saved yet";
    }

    function protectedDevicesText(count) {
      return count === 1
        ? "1 protected device stays untouched"
        : count + " protected devices stay untouched";
    }

    function hasProfilesOrLists(input) {
      return !!input.generalProfileName || input.devices > 0 || input.groups > 0 ||
        input.serviceLists > 0;
    }

    function savedLine(input) {
      if (hasProfilesOrLists(input) || input.protected > 0 || input.unverifiedDevices > 0) {
        return "Saved: " + applySummary(input);
      }
      // A start also enforces the access rules so an empty line must not hide them.
      const nothing = applySummary({});
      return input.accessRules ? nothing + ". Access rules are saved too." : nothing;
    }

    function skippedDeviceProfilesText(number) {
      const one = number === 1;
      return number + (one ? " saved device profile was" : " saved device profiles were") +
        " skipped because " + (one ? "its device" : "their devices") +
        " could not be identified. Reconnect " + (one ? "that device" : "those devices") +
        " and check " + (one ? "its saved profile." : "their saved profiles.");
    }

    function startedText(input, wasRunning) {
      const lead = "Started";
      if (hasProfilesOrLists(input)) {
        const applied = applySummary({...input, unverifiedDevices: 0});
        return lead + ": " + applied + "." + (input.unverifiedDevices > 0
          ? " " + skippedDeviceProfilesText(input.unverifiedDevices) : "");
      }
      const sentences = [lead + "."];
      if (input.protected > 0) sentences.push(protectedDevicesText(input.protected) + ".");
      sentences.push(input.unverifiedDevices > 0
        ? skippedDeviceProfilesText(input.unverifiedDevices)
        : "No device profiles or service lists are saved yet.");
      if (input.accessRules) sentences.push("Access rules apply.");
      return sentences.join(" ");
    }

    function runtimeStatusText(state, running, context) {
      const options = context || {};
      if (options.helperConfigured === false) {
        return {text: "Needs the Pi helper to run.", tone: "muted"};
      }
      if (state === "applying") return {text: "Applying…", tone: "working"};
      if (state === "resetting") return {text: "Resetting…", tone: "working"};
      const deviceOnly = Array.isArray(options.deviceOnly) ? options.deviceOnly.length : 0;
      if (running === true && deviceOnly > 0 && (state === "applied" || state === "unknown")) {
        return {
          text: "Running for " + deviceOnly + (deviceOnly === 1 ? " device" : " devices") +
            " only. Everyone and other saved settings stay paused. Start resumes them all.",
          tone: "ok"
        };
      }
      if (running === true) {
        if (state === "applied") {
          return {
            text: "Running. Your saved settings are applied. New changes apply on their own.",
            tone: "ok"
          };
        }
        if (state === "unknown") {
          return {text: "Running. Your latest change applies on its own.", tone: "ok"};
        }
        if (state === "error") return {text: "The last apply failed. Check the Pi.", tone: "error"};
      } else {
        if (state === "reset") {
          return {text: "Off. No traffic shaping is running.", tone: "muted"};
        }
        if (state === "unknown") {
          return {text: "Nothing is running. Choose Start.", tone: "pending"};
        }
        if (state === "error") {
          return options.lastAction === "reset"
            ? {
                text: "The last reset failed. The Pi may still be shaping. Check it.",
                tone: "error"
              }
            : {text: "The last start failed. Check the Pi and try again.", tone: "error"};
        }
        if (state === "applied") {
          return {text: "Applied. Waiting for the scheduler.", tone: "muted"};
        }
      }
      return {text: "State unknown.", tone: "muted"};
    }

    // One word for one idea: Start both begins the saved settings and applies them again.
    function applyButtonLabel(state, running) {
      return "Start";
    }

    function currentApplyLabel() {
      const known = adminTokenVerified && !!lastRuntimeState;
      return applyButtonLabel(
        known ? lastRuntimeState.observed_state : null,
        known && lastRuntimeState.running === true
      );
    }

    function setTextIfChanged(node, text) {
      if (node.textContent !== text) node.textContent = text;
    }

    function renderApplyButton() {
      if (applyBusy) return;
      setTextIfChanged(element("apply-button"), currentApplyLabel());
    }

    function applyInputsLoaded() {
      return savedDevicesLoaded && groupsLoaded && unaffectedMacs !== null && serviceListsLoaded &&
        generalProfileState !== null && accessRulesSaved !== null;
    }

    function renderApplySummary() {
      const line = element("apply-summary");
      let text = "";
      if (adminTokenVerified && applyInputsLoaded()) {
        const generalProfile = generalProfileState.profile ? generalProfileState.profile : null;
        const protectedMacs = new Set(unaffectedMacs);
        const affected = savedDevices.filter(
          (device) => device.effective_state !== "protected" &&
            device.effective_state !== "unverified" &&
            (!device.mac_address || !protectedMacs.has(device.mac_address))
        );
        const unverified = savedDevices.filter(
          (device) => device.effective_state === "unverified" &&
            (!device.mac_address || !protectedMacs.has(device.mac_address))
        );
        const affectedIds = new Set(affected.map((device) => device.id));
        text = savedLine({
          devices: affected.length,
          unverifiedDevices: unverified.length,
          groups: groups.filter(
            (group) => (group.device_ids || []).some((id) => affectedIds.has(id))
          ).length,
          serviceLists: serviceListsState.filter((serviceList) => serviceList.enabled).length,
          protected: unaffectedMacs.length,
          generalProfileName: generalProfile ? generalProfile.name || generalProfile.id : "",
          accessRules: accessRulesSaved
        });
      }
      setTextIfChanged(line, text);
      if (line.hidden !== (text === "")) line.hidden = text === "";
    }

    function renderRuntimeText() {
      if (!lastRuntimeState) return;
      const status = runtimeStatusText(
        lastRuntimeState.observed_state,
        lastRuntimeState.running === true,
        {
          lastAction: lastRuntimeState.last_action,
          deviceOnly: lastRuntimeState.device_only,
          helperConfigured: helperKnown ? helperConfigured : true
        }
      );
      const summary = element("runtime-summary");
      if (unansweredPaths.size) return;
      setTextIfChanged(summary, status.text);
      const className = "runtime-summary " + lastRuntimeState.observed_state + " tone-" + status.tone;
      if (summary.className !== className) summary.className = className;
    }

    function renderRuntimeSummary(state) {
      lastRuntimeState = state;
      renderRuntimeText();
      renderSidebarStatus();
    }

    function connectedRowState(device, context) {
      const protectedRow = !!device.unaffected;
      const disableProfile = protectedRow || !device.ipv4_address || !device.association_verified;
      const disableApply = protectedRow || !context.selectedProfile || !context.helperConfigured ||
        context.profileCount === 0 || !device.ipv4_address || !device.association_verified;
      const disableGroupSelect = protectedRow || !context.helperConfigured ||
        !device.association_verified || !device.ipv4_address || context.groupCount === 0;
      const disableGroupButton = disableGroupSelect || !context.selectedGroup ||
        context.selectedGroup === device.group_id;
      return {
        protectedRow,
        rowClass: "connected-row" + (protectedRow ? " protected" : ""),
        badge: protectedRow ? "Protected" : "",
        disableProfile,
        disableApply,
        disableGroupSelect,
        disableGroupButton,
        protectionLabel: protectedRow ? "Stop protecting" : "Protect"
      };
    }

    let lastConnectedDevices = null;

    const connectedRenderHooks = [];

    const connectedRowHooks = [];

    function profileOptionLabels(items) {
      const counts = new Map();
      items.forEach((profile) => counts.set(profile.name, (counts.get(profile.name) || 0) + 1));
      return new Map(items.map((profile) => [profile.id, counts.get(profile.name) > 1
        ? profile.name + " (" + profile.id + ")" : profile.name]));
    }

    function renderConnectedDevices(data) {
      const profileLabels = profileOptionLabels(profiles);
      connectedRenderKey = null;
      lastConnectedDevices = data;
      const container = element("connected-devices");
      const count = element("connected-count");
      const expandedLabels = new Set(
        Array.from(container.querySelectorAll(".connected-label-editor[open]"))
          .map((editor) => editor.dataset.macAddress)
      );
      container.querySelectorAll(".connected-row[data-mac-address]").forEach((row) => {
        if (clearConnectedDrafts.has(row.dataset.macAddress)) {
          connectedDrafts.delete(row.dataset.macAddress);
          return;
        }
        const draft = connectedDrafts.get(row.dataset.macAddress) || {};
        row.querySelectorAll("[data-draft][data-dirty]").forEach((field) => { draft[field.dataset.draft] = field.value; });
        connectedDrafts.set(row.dataset.macAddress, draft);
      });
      clearConnectedDrafts.clear();
      container.replaceChildren();
      const devices = data.devices || [];
      connectedClients = devices;
      lastConnectedData = data;
      connectedClientsAvailable = data.available !== false &&
        data.association_status_available !== false;
      renderSidebarStatus();
      const currentMacs = new Set(devices.map((device) => device.mac_address));
      for (const mac of connectedDrafts.keys()) if (!currentMacs.has(mac)) connectedDrafts.delete(mac);
      updateGroupMemberOptions();
      const deviceTypeNames = {
        unknown: "Unknown",
        phone: "Phone",
        tablet: "Tablet",
        laptop: "Laptop",
        desktop: "Desktop",
        tv_console: "TV or console",
        other: "Other"
      };
      // A status line: the same words written again are read out again.
      if (!data.available) {
        setTextIfChanged(count, "Unavailable");
        const empty = document.createElement("p");
        empty.className = "device-empty";
        empty.textContent = data.message || "Connected-client information is unavailable.";
        container.appendChild(empty);
      } else {
        const connectionLabel = data.association_status_available ? " connected" : " visible";
        setTextIfChanged(count, devices.length + connectionLabel);
        if (!data.association_status_available) {
          const warning = document.createElement("p");
          warning.className = "device-empty";
          warning.textContent =
            "Wi-Fi connection status is unavailable. Listed IP neighbors may be disconnected " +
            "and profile actions are disabled.";
          container.appendChild(warning);
        }
      }
      if (data.available && devices.length === 0) {
        const empty = document.createElement("p");
        empty.className = "device-empty";
        empty.textContent = data.association_status_available
          ? "No devices are connected to the test Wi-Fi."
          : "No recent IP neighbors are visible.";
        container.appendChild(empty);
      }
      devices.forEach((device) => {
        const row = document.createElement("div");
        row.className = "connected-row";
        row.dataset.key = device.mac_address;
        row.dataset.macAddress = device.mac_address;
        const draft = connectedDrafts.get(device.mac_address) || {};
        const dot = document.createElement("span");
        dot.className = "connected-dot";
        const details = document.createElement("div");
        const address = document.createElement("p");
        address.className = "connected-address";
        address.textContent = device.display_name || device.ipv4_address || device.mac_address;
        const metadata = document.createElement("p");
        metadata.className = "connected-meta";
        const metadataParts = [device.ipv4_address || "IP address pending", device.mac_address];
        if (device.signal_dbm !== undefined && device.signal_dbm !== null) {
          metadataParts.push("Signal " + device.signal_dbm + " dBm");
        }
        metadata.textContent = metadataParts.join(" · ");
        const deviceType = document.createElement("p");
        deviceType.className = "connected-meta";
        const typeName = deviceTypeNames[device.device_type] || "Unknown";
        deviceType.textContent = "Type: " + typeName + (
          device.device_type_source === "hostname suggestion" ? " (suggested)" : ""
        );
        const assignmentInfo = document.createElement("p");
        assignmentInfo.className = "connected-meta";
        const savedProfileName = (id) => {
          const found = profiles.find((candidate) => candidate.id === id);
          return found ? found.name : id;
        };
        assignmentInfo.textContent = device.unaffected ? "Protected from all profiles and quotas · Traffic is not monitored" :
          device.general_profile_name ? "Same as everyone: " + device.general_profile_name +
            " · Shared speed limit" :
          "Profile: " + (device.assigned_profile_id ? savedProfileName(device.assigned_profile_id) : "None") +
          (device.group_name ? " · Group: " + device.group_name + (device.group_profile_id ? " (override: " + savedProfileName(device.group_profile_id) + ")" : "") : "");
        details.append(address, metadata, deviceType, assignmentInfo);
        connectedRowHooks.forEach((hook) => hook(device, details));
        if (device.hostname) {
          const hostname = document.createElement("p");
          hostname.className = "connected-meta";
          hostname.textContent = "Network name: " + device.hostname;
          details.appendChild(hostname);
        }
        const labelEditor = document.createElement("details");
        labelEditor.className = "connected-label-editor";
        labelEditor.dataset.macAddress = device.mac_address;
        labelEditor.open = expandedLabels.has(device.mac_address);
        const labelSummary = document.createElement("summary");
        labelSummary.textContent = "Edit name and type";
        const labelForm = document.createElement("div");
        labelForm.className = "connected-label-form";
        const nameLabel = document.createElement("label");
        nameLabel.textContent = "Display name";
        const nameInput = document.createElement("input");
        nameInput.type = "text";
        nameInput.maxLength = 64;
        nameInput.dataset.draft = "name";
        nameInput.value = draft.name ?? (device.saved_display_name || device.hostname || "");
        nameInput.placeholder = "Optional name";
        nameLabel.appendChild(nameInput);
        const typeLabel = document.createElement("label");
        typeLabel.textContent = "Device type";
        const typeSelect = document.createElement("select");
        typeSelect.setAttribute(
          "aria-label",
          "Device type for " + (device.display_name || device.mac_address)
        );
        Object.entries(deviceTypeNames).forEach(([value, name]) => {
          const option = document.createElement("option");
          option.value = value;
          option.textContent = name;
          option.selected = value === device.device_type;
          typeSelect.appendChild(option);
        });
        typeSelect.dataset.draft = "type";
        if (draft.type !== undefined) typeSelect.value = draft.type;
        typeLabel.appendChild(typeSelect);
        const saveLabelButton = document.createElement("button");
        saveLabelButton.type = "button";
        saveLabelButton.textContent = "Save details";
        saveLabelButton.addEventListener("click", () => {
          saveConnectedDeviceLabel(device, nameInput, typeSelect, saveLabelButton);
        });
        labelForm.append(nameLabel, typeLabel, saveLabelButton);
        if (device.has_saved_label) {
          const clearLabelButton = document.createElement("button");
          clearLabelButton.type = "button";
          clearLabelButton.textContent = "Clear saved details";
          clearLabelButton.addEventListener("click", () => {
            clearConnectedDeviceLabel(device, clearLabelButton);
          });
          labelForm.appendChild(clearLabelButton);
        }
        labelEditor.append(labelSummary, labelForm);
        details.appendChild(labelEditor);
        const state = document.createElement("span");
        state.className = "connected-state";
        state.textContent = device.association_verified ? "Connected" : "Status unknown";
        const controls = document.createElement("div");
        controls.className = "connected-controls";
        const profileSelect = document.createElement("select");
        profileSelect.setAttribute(
          "aria-label",
          "Profile for " + (device.display_name || device.mac_address)
        );
        profileSelect.dataset.draft = "profile";
        const emptyOption = document.createElement("option");
        emptyOption.value = "";
        emptyOption.textContent = profiles.length ? "Choose profile..." : "No profiles saved";
        profileSelect.appendChild(emptyOption);
        profiles.forEach((profile) => {
          const option = document.createElement("option");
          option.value = profile.id;
          option.textContent = profileLabels.get(profile.id);
          option.selected = profile.id === device.assigned_profile_id;
          profileSelect.appendChild(option);
        });
        if (draft.profile !== undefined) profileSelect.value = draft.profile;
        const applyButton = document.createElement("button");
        applyButton.type = "button";
        applyButton.textContent = device.assigned_profile_id ? "Apply profile" : "Assign profile";
        applyButton.addEventListener("click", () => {
          applyProfileToDevice(device, profileSelect.value, applyButton);
        });
        const groupSelect = document.createElement("select");
        groupSelect.dataset.draft = "group";
        groupSelect.setAttribute("aria-label", "Group for " + (device.display_name || device.mac_address));
        const groupPlaceholder = document.createElement("option");
        groupPlaceholder.value = "";
        groupPlaceholder.textContent = groups.length ? "Choose group..." : "Create a group on the Profiles page";
        groupSelect.appendChild(groupPlaceholder);
        groups.forEach((group) => {
          const option = document.createElement("option");
          option.value = group.id;
          option.textContent = group.name;
          option.disabled = (!!device.group_id && device.group_id !== group.id) || (!device.assigned_device_id && !group.profile_id);
          option.selected = group.id === device.group_id;
          groupSelect.appendChild(option);
        });
        if (draft.group !== undefined) groupSelect.value = draft.group;
        const groupButton = document.createElement("button");
        groupButton.type = "button";
        groupButton.textContent = "Add to group";
        const rowContext = () => ({
          helperConfigured,
          profileCount: profiles.length,
          groupCount: groups.length,
          selectedProfile: profileSelect.value,
          selectedGroup: groupSelect.value
        });
        const rowState = connectedRowState(device, rowContext());
        row.className = rowState.rowClass;
        if (rowState.badge) {
          const protectedBadge = document.createElement("span");
          protectedBadge.className = "protected-badge";
          protectedBadge.textContent = rowState.badge;
          address.append(" ", protectedBadge);
        }
        profileSelect.disabled = rowState.disableProfile;
        applyButton.disabled = rowState.disableApply;
        profileSelect.addEventListener("change", () => {
          applyButton.disabled = connectedRowState(device, rowContext()).disableApply;
        });
        const syncGroup = () => {
          const selected = groups.find((group) => group.id === groupSelect.value);
          groupButton.disabled = connectedRowState(device, rowContext()).disableGroupButton ||
            !selected || (!device.assigned_device_id && !selected.profile_id);
        };
        groupSelect.disabled = rowState.disableGroupSelect;
        groupSelect.addEventListener("change", syncGroup);
        syncGroup();
        groupButton.addEventListener("click", () => connectedControlAction(
          "/api/connected-devices/group", {group_id: groupSelect.value, ipv4_address: device.ipv4_address, mac_address: device.mac_address},
          groupButton, "Group membership saved and applied."
        ));
        const protectionButton = document.createElement("button");
        protectionButton.type = "button";
        protectionButton.textContent = rowState.protectionLabel;
        protectionButton.setAttribute("aria-label", protectionButton.textContent + " for " + (device.display_name || device.mac_address));
        protectionButton.addEventListener("click", () => connectedControlAction(
          device.unaffected ? "/api/unaffected-devices/delete" : "/api/unaffected-devices",
          {mac_address: device.mac_address, display_name: device.display_name || ""}, protectionButton,
          device.unaffected ? "Stopped protecting this device. Saved profiles can apply again." : "Protected. Profiles no longer affect this device."
        ));
        controls.append(profileSelect, applyButton, groupSelect, groupButton, protectionButton);
        if (rowState.protectedRow) {
          [profileSelect, applyButton, groupSelect, groupButton].forEach((control) => {
            control.setAttribute("aria-disabled", "true");
          });
        }
        [...labelForm.querySelectorAll("[data-draft]"), profileSelect, groupSelect].forEach((field) => {
          if (draft[field.dataset.draft] !== undefined) field.dataset.dirty = "true";
          field.addEventListener("input", () => { field.dataset.dirty = "true"; });
          field.addEventListener("change", () => { field.dataset.dirty = "true"; });
        });
        row.append(dot, details, state, controls);
        container.appendChild(row);
      });
      renderSavedConnectedDeviceLabels(
        container,
        data.saved_labels || [],
        new Set(devices.map((device) => device.mac_address)),
        deviceTypeNames
      );
      connectedRenderHooks.forEach((hook) => hook(data));
    }

    function renderSavedConnectedDeviceLabels(container, labels, connectedMacAddresses, typeNames) {
      const disconnectedLabels = labels.filter(
        (label) => !connectedMacAddresses.has(label.mac_address)
      );
      if (disconnectedLabels.length === 0) return;
      const section = document.createElement("div");
      section.className = "saved-device-labels";
      const heading = document.createElement("h3");
      heading.textContent = "Saved labels for devices not listed above";
      const note = document.createElement("p");
      note.className = "device-empty";
      note.textContent = "Remove old labels here if a device's private Wi-Fi address changed.";
      section.append(heading, note);
      disconnectedLabels.forEach((label) => {
        const row = document.createElement("div");
        row.className = "connected-row saved-device-label-row";
        const details = document.createElement("div");
        const name = document.createElement("p");
        name.className = "connected-address";
        name.textContent = label.display_name || label.mac_address;
        const mac = document.createElement("p");
        mac.className = "connected-meta";
        mac.textContent = label.mac_address;
        details.append(name, mac);
        const type = document.createElement("span");
        type.className = "connected-state";
        type.textContent = typeNames[label.device_type] || "Unknown";
        const removeButton = document.createElement("button");
        removeButton.type = "button";
        removeButton.textContent = "Remove saved label";
        removeButton.addEventListener("click", () => {
          clearConnectedDeviceLabel(label, removeButton);
        });
        row.append(details, type, removeButton);
        section.appendChild(row);
      });
      container.appendChild(section);
    }

    async function saveConnectedDeviceLabel(device, nameInput, typeSelect, button) {
      setButtonBusy(button, true);
      try {
        await request("/api/connected-device-labels", "POST", {
          mac_address: device.mac_address,
          display_name: nameInput.value,
          device_type: typeSelect.value
        });
        showMessage("Saved device name and type.", "success");
        await loadConnectedDevices();
      } catch (error) {
        showMessage(error.message, "error");
      } finally {
        setButtonBusy(button, false);
      }
    }

    async function clearConnectedDeviceLabel(device, button) {
      setButtonBusy(button, true);
      try {
        await request("/api/connected-device-labels/delete", "POST", {
          mac_address: device.mac_address
        });
        showMessage("Removed saved device details.", "success");
        await loadConnectedDevices();
      } catch (error) {
        showMessage(error.message, "error");
      } finally {
        setButtonBusy(button, false);
      }
    }

    function renderUplink(data) {
      const container = element("uplink-details");
      const state = element("uplink-state");
      const note = element("uplink-note");
      const routerLink = element("router-settings-link");
      container.replaceChildren();
      if (!data.available || !data.uplink) {
        state.textContent = "Unavailable";
        const empty = document.createElement("p");
        empty.className = "device-empty";
        empty.textContent = data.message || "Upstream connection information is unavailable.";
        container.appendChild(empty);
        note.textContent = "The Pi can use the TG3442DE controls in hardware mode. Keep the Pi connected by Ethernet and enter the router password only when you use a router action.";
        return;
      }
      state.textContent = "Detected on " + (data.uplink.interface || "Ethernet");
      [
        ["Pi interface", data.uplink.interface || "Unknown"],
        ["Pi Ethernet connection", data.uplink.connection_name || "Unknown"],
        ["Router gateway", data.uplink.gateway || "Unknown"]
      ].forEach(([label, value]) => {
        const item = document.createElement("div");
        item.className = "uplink-value";
        const labelElement = document.createElement("span");
        labelElement.textContent = label;
        const valueElement = document.createElement("strong");
        valueElement.textContent = value;
        item.append(labelElement, valueElement);
        container.appendChild(item);
      });
      if (data.router && data.router.settings_url) {
        routerLink.href = data.router.settings_url;
      }
      note.textContent = "The router controls below use the TG3442DE local interface. Keep Ethernet connected so the Pi remains reachable if router Wi-Fi is turned off.";
    }

    async function applyProfileToDevice(device, profileId, button) {
      if (networkActionBusy) return;
      if (!profileId) {
        showMessage("Choose a profile before applying it.", "error");
        return;
      }
      const revision = adminTokenRevision;
      const intent = ++actionIntentRevision;
      const current = () => revision === adminTokenRevision && adminTokenVerified && intent === actionIntentRevision;
      setActionBusy(true, "/api/apply");
      setButtonBusy(button, true);
      showMessage("Applying " + profileName(profileId) + " to " + (device.display_name || device.ipv4_address) + "...", "working");
      try {
        const result = await request("/api/connected-devices/apply-profile", "POST", {
          ipv4_address: device.ipv4_address,
          mac_address: device.mac_address,
          profile_id: profileId
        });
        if (!current()) return;
        clearConnectedDrafts.add(device.mac_address);
        refreshAfterSuccess((result.message || "Profile saved and applied.") + (result.warning ? " " + result.warning : ""));
      } catch (error) {
        if (!current()) return;
        showMessage(error.message, "error");
        await loadConnectedDevices();
      } finally {
        if (revision === adminTokenRevision && adminTokenVerified) {
          setButtonBusy(button, false);
          setActionBusy(false, "/api/apply");
        } else { runningActions.delete("/api/apply"); networkActionBusy = runningActions.size > 0; }
      }
    }

    async function connectedControlAction(path, payload, button, success) {
      setButtonBusy(button, true);
      showMessage("Saving network settings...", "working");
      try {
        const result = await request(path, "POST", payload);
        clearConnectedDrafts.add(payload.mac_address);
        refreshAfterSuccess((result.live_applied ? success : result.message || "Settings saved. Apply them in hardware mode.") + (result.warning ? " " + result.warning : ""));
      } catch (error) {
        showMessage(error.message, "error");
        await Promise.all([loadUnaffectedDevices(), loadConnectedDevices()]);
      } finally {
        setButtonBusy(button, false);
      }
    }

    let adBlockingOperation = 0;
    let adBlockingBusy = false;

    function setAdBlockingBusy(busy) {
      adBlockingBusy = busy;
      document.querySelectorAll("#unaffected-devices input[data-ad-mac], #ad-blocking-form button").forEach((control) => {
        control.disabled = busy || !adminTokenVerified;
      });
    }

    async function loadUnaffectedDevices() {
      const container = element("unaffected-devices");
      const tokenRevision = adminTokenRevision;
      const operation = adBlockingOperation;
      try {
        const data = await request("/api/unaffected-devices", "GET");
        if (tokenRevision !== adminTokenRevision || !adminTokenVerified || operation !== adBlockingOperation) return null;
        const ads = data.ad_blocking;
        const service = ads.service;
        if (!adBlockingBusy) {
          setTextIfChanged(element("ad-blocking-status"), service.paused
            ? "Ad blocking is paused. Choose Start to resume."
            : service.message || (service.ready ? "Ad blocking is active." : "Ad blocking is off."));
        }
        if (document.activeElement !== element("ad-blocking-allowances")) {
          element("ad-blocking-allowances").value = ads.allowed_domains.join("\n");
        }
        unaffectedMacs = data.devices.map((device) => device.mac_address);
        renderApplySummary();
        if (container.contains(document.activeElement) || adBlockingBusy) {
          if (!adBlockingBusy) {
            const saved = new Map(data.devices.map((device) => [device.mac_address, device]));
            container.querySelectorAll("input[data-ad-mac]").forEach((control) => {
              const device = saved.get(control.dataset.adMac);
              if (device) control.checked = device.ad_blocking;
              else control.closest(".connected-row").remove();
            });
          }
          setAdBlockingBusy(adBlockingBusy);
          return data;
        }
        container.replaceChildren();
        if (!data.devices.length) {
          container.textContent = "No devices are protected. Choose Protect beside a connected client.";
        }
        data.devices.forEach((device) => {
          const row = document.createElement("div");
          row.className = "connected-row saved-device-label-row";
          row.dataset.key = device.mac_address;
          const name = document.createElement("div");
          const title = document.createElement("p");
          title.className = "connected-address";
          title.textContent = device.display_name || device.mac_address;
          const mac = document.createElement("p");
          mac.className = "connected-meta";
          mac.textContent = device.mac_address;
          name.append(title, mac);
          const button = document.createElement("button");
          button.type = "button";
          button.textContent = "Stop protecting";
          button.setAttribute("aria-label", "Stop protecting for " + (device.display_name || device.mac_address));
          button.addEventListener("click", () => connectedControlAction("/api/unaffected-devices/delete", {mac_address: device.mac_address}, button, "Protection and this device’s ad-blocking choice were removed. Saved profiles can apply again."));
          const actions = document.createElement("div");
          actions.className = "connected-actions";
          const adsLabel = document.createElement("label");
          adsLabel.className = "ad-blocking-choice";
          const adsSwitch = document.createElement("input");
          adsSwitch.type = "checkbox";
          adsSwitch.dataset.adMac = device.mac_address;
          adsSwitch.checked = device.ad_blocking;
          adsSwitch.setAttribute("aria-label", "Block ads for " + (device.display_name || device.mac_address));
          adsSwitch.addEventListener("change", () => saveAdBlocking({mac_address: device.mac_address, enabled: adsSwitch.checked}, adsSwitch));
          adsLabel.append(adsSwitch, document.createTextNode(" Block ads"));
          actions.append(adsLabel, button);
          row.append(name, actions);
          container.appendChild(row);
        });
        setAdBlockingBusy(adBlockingBusy);
        return data;
      } catch (error) {
        if (tokenRevision !== adminTokenRevision || !adminTokenVerified || operation !== adBlockingOperation) return null;
        element("ad-blocking-status").textContent = "Saved ad-blocking choices could not be confirmed. Check the connection and refresh.";
        if (!container.contains(document.activeElement) && !adBlockingBusy) {
          container.textContent = "Could not load protection: " + error.message;
        }
        return null;
      }
    }

    async function saveAdBlocking(payload, control) {
      if (adBlockingBusy || !adminTokenVerified) return;
      const operation = ++adBlockingOperation;
      const tokenRevision = adminTokenRevision;
      const notice = messageSerial;
      const current = () => operation === adBlockingOperation && tokenRevision === adminTokenRevision && adminTokenVerified;
      const hadFocus = document.activeElement === control;
      setAdBlockingBusy(true);
      let result = null;
      let error = null;
      try {
        result = await request("/api/ad-blocking", "POST", payload);
      } catch (failure) {
        error = failure;
      } finally {
        if (current()) {
          setAdBlockingBusy(false);
          if (hadFocus && control.isConnected && document.activeElement === document.body) control.focus({preventScroll: true});
          const saved = await loadUnaffectedDevices();
          if (current() && messageSerial === notice) {
            if (error) {
              const checked = saved && payload.mac_address
                ? " Saved Block ads is " + (saved.ad_blocking.mac_addresses.includes(payload.mac_address) ? "on." : "off.")
                : saved ? " Saved choices were checked." : " Saved choices could not be confirmed. Refresh after reconnecting.";
              showMessage("The change reply was not confirmed." + checked, "error");
            } else if (result) {
              showMessage(result.message, "success");
            }
          }
        }
      }
    }

    privateDataLockHooks.push(() => {
      adBlockingOperation += 1;
      adBlockingBusy = false;
      setAdBlockingBusy(false);
      element("ad-blocking-allowances").value = "";
      element("ad-blocking-status").textContent = "";
    });

    let editingGroupId = null;
    let groupSubmitting = false;
    let groupSaveError = "";
    const groupSelectedMembers = new Set();
    let groupMemberChoices = new Map();

    function groupFormProblem() {
      if (!adminTokenVerified || !savedDevicesLoaded || !groupsLoaded || !profilesLoaded) {
        return "Device, group and profile details are unavailable. Check your access or refresh the dashboard.";
      }
      if (editingGroupId && !groups.some((group) => group.id === editingGroupId)) {
        return "This group no longer exists. Cancel these edits and create a new group.";
      }
      const profileId = element("group-profile").value;
      if (profileId && !profiles.some((profile) => profile.id === profileId)) {
        return "The selected profile is no longer available. Choose another profile.";
      }
      if (!groupSelectedMembers.size) {
        return editingGroupId ? "A group needs at least one device. Delete the group to remove it." : "Select at least one device.";
      }
      if (groupSelectedMembers.size > 15) return "Select at most 15 devices.";
      for (const key of groupSelectedMembers) {
        const choice = groupMemberChoices.get(key);
        if (!choice || choice.unavailable) return "A selected device is unavailable. Remove it or refresh to verify it again.";
        if (choice.otherGroup) return "A selected device already belongs to " + choice.otherGroup + ". Remove it from that group first.";
        if (choice.live && !profileId) return "Choose a group profile to add a device without a saved profile. It will also become the device's saved profile.";
      }
      return "";
    }

    function updateGroupFormControls() {
      const count = groupSelectedMembers.size;
      // These two speak (role="status"), so they are only written when their words change.
      const memberCount = element("group-member-count");
      if (memberCount.textContent !== count + " of 15 selected") memberCount.textContent = count + " of 15 selected";
      const problem = groupFormProblem();
      const validation = element("group-validation");
      if (validation.textContent !== (problem || groupSaveError)) validation.textContent = problem || groupSaveError;
      element("group-save").disabled = groupSubmitting || Boolean(problem);
      element("group-cancel-edit").disabled = groupSubmitting;
      element("group-profile-help").textContent = element("group-profile").value
        ? "This replaces members' saved profiles while the group applies. Other active settings may take priority."
        : "Choose one profile for the group or keep each device's saved profile.";
      element("group-devices").querySelectorAll("input").forEach((input) => {
        const choice = groupMemberChoices.get(input.value);
        input.checked = groupSelectedMembers.has(input.value);
        input.disabled = groupSubmitting || (!input.checked && (count >= 15 || Boolean(choice.otherGroup) || choice.unavailable));
      });
    }

    function groupMemberOptions() {
      const membership = new Map();
      groups.forEach((group) => {
        group.device_ids.forEach((id) => membership.set(id, group));
      });
      const choices = new Map();
      const profileLabels = profileOptionLabels(profiles);
      savedDevices.forEach((device) => {
        const group = membership.get(device.id);
        choices.set(device.id, {
          name: devicePickerLabel(device),
          otherGroup: group && group.id !== editingGroupId ? group.name : null,
          detail: unaffectedMacs && unaffectedMacs.includes(device.mac_address)
            ? "Protected from profiles" : "Saved profile: " + (profileLabels.get(device.profile_id) || device.profile_id)
        });
      });
      if (connectedClientsAvailable) connectedClients.filter((device) =>
        !device.assigned_device_id && device.association_verified && device.ipv4_address && !device.unaffected
      ).forEach((device) => {
        choices.set("live:" + device.mac_address, {
          name: (device.display_name || device.mac_address) + " (" + device.ipv4_address + ")",
          detail: "Connected · Needs a profile", live: true
        });
      });
      groupSelectedMembers.forEach((key) => {
        if (!choices.has(key)) choices.set(key, {
          name: groupMemberChoices.get(key)?.name || key,
          detail: "Unavailable · Uncheck to remove", unavailable: true
        });
      });
      return choices;
    }

    function updateGroupMemberOptions() {
      const container = element("group-devices");
      const choices = groupMemberOptions();
      groupMemberChoices = choices;
      const rows = new Map(Array.from(container.querySelectorAll("label"), (row) => [row.querySelector("input").value, row]));
      rows.forEach((row, key) => { if (!choices.has(key)) row.remove(); });
      choices.forEach((choice, key) => {
        let row = rows.get(key);
        if (!row) {
          row = document.createElement("label");
          row.className = "group-device-choice";
          const input = document.createElement("input");
          input.type = "checkbox";
          input.value = key;
          input.id = "group-member-" + encodeURIComponent(key);
          input.addEventListener("change", () => {
            groupSaveError = "";
            if (input.checked) groupSelectedMembers.add(key);
            else groupSelectedMembers.delete(key);
            updateGroupFormControls();
          });
          const text = document.createElement("span");
          text.append(document.createElement("strong"), document.createElement("small"));
          row.append(input, text);
          container.appendChild(row);
        }
        row.querySelector("strong").textContent = choice.name;
        row.querySelector("small").textContent = choice.otherGroup
          ? "Already in " + choice.otherGroup + ". Remove it from that group first." : choice.detail;
      });
      let empty = container.querySelector(".group-empty");
      if (!choices.size) {
        if (!empty) {
          empty = document.createElement("p");
          empty.className = "group-empty form-note";
          container.appendChild(empty);
        }
        empty.textContent = savedDevicesLoaded ? "No eligible devices. Save a device or connect an unprotected device to the Pi's Wi-Fi." : "Loading devices…";
      } else if (empty) empty.remove();
      updateGroupFormControls();
    }

    function resetGroupEditing() {
      editingGroupId = null;
      groupSaveError = "";
      groupSelectedMembers.clear();
      groupMemberChoices.clear();
      element("group-id").readOnly = false;
      element("group-save").textContent = "Save group";
      element("group-edit-status").hidden = true;
      element("group-edit-status").textContent = "";
      element("group-cancel-edit").hidden = true;
      element("group-removal-help").hidden = true;
      updateGroupMemberOptions();
    }

    element("group-form").addEventListener("reset", () => queueMicrotask(resetGroupEditing));
    element("group-form").addEventListener("input", (event) => {
      if (event.target.type === "checkbox") return;
      groupSaveError = "";
      updateGroupFormControls();
    });
    element("group-profile").addEventListener("change", updateGroupFormControls);
    element("group-cancel-edit").addEventListener("click", () => stopEditing("group"));
    privateDataLockHooks.push(() => {
      savedDevices = [];
      groups = [];
      profiles = [];
      connectedClients = [];
      connectedClientsAvailable = false;
      savedDevicesLoaded = false;
      groupsLoaded = false;
      profilesLoaded = false;
      element("group-list").replaceChildren();
      element("group-form").reset();
      resetGroupEditing();
      element("group-profile").replaceChildren(new Option("Keep each device's own profile", ""));
      if (typeof clearDraft === "function") clearDraft("group-form");
    });

    // A busy helper answers 502 for a moment. The list stays as it was, with a note, until the
    // third answer in a row fails or the server itself says the information is unavailable.
    const CONNECTED_FAILURES_BEFORE_UNAVAILABLE = 3;

    let connectedFailures = 0;

    async function loadConnectedDevices() {
      if (connectedDevicesRequestInFlight) return null;
      const container = element("connected-devices");
      // Typing, choosing from a menu or pressing a button must not be interrupted.
      if (interactionInside(container)) return null;
      const revision = adminTokenRevision;
      connectedDevicesRequestInFlight = true;
      let data;
      let keptLastList = false;
      try {
        const [connectedResult, labelsResult] = await Promise.allSettled([
          request("/api/connected-devices", "GET"),
          request("/api/connected-device-labels", "GET")
        ]);
        if (revision !== adminTokenRevision || !adminTokenVerified) return null;
        const savedLabels = labelsResult.status === "fulfilled"
          ? labelsResult.value.labels || []
          : lastConnectedData && lastConnectedData.saved_labels || [];
        if (connectedResult.status === "rejected") {
          connectedFailures += 1;
          const haveList = !!lastConnectedData && lastConnectedData.available !== false;
          if (haveList && connectedFailures < CONNECTED_FAILURES_BEFORE_UNAVAILABLE) {
            data = lastConnectedData;
            keptLastList = true;
          } else {
            data = {
              available: false,
              devices: [],
              saved_labels: savedLabels,
              message: connectedResult.reason.message
            };
          }
        } else {
          connectedFailures = 0;
          connectedDataHooks.forEach((hook) => hook(connectedResult.value));
          data = { ...connectedResult.value, saved_labels: savedLabels };
        }
        if (!keptLastList) {
          // The same answer again changes nothing on screen, so the rows are left alone.
          const key = JSON.stringify(data);
          if (key !== connectedRenderKey && !interactionInside(container)) {
            renderConnectedDevices(data);
            connectedRenderKey = key;
          }
        }
      } catch (error) {
        if (revision !== adminTokenRevision || !adminTokenVerified) return null;
        data = { available: false, devices: [], message: error.message };
        connectedRenderKey = null;
        renderConnectedDevices(data);
      } finally {
        connectedDevicesRequestInFlight = false;
      }
      if (revision !== adminTokenRevision || !adminTokenVerified) return null;
      element("connected-refresh-status").textContent = keptLastList
        ? "Updating… showing the last list while the Pi catches up."
        : data.available
        ? "Updated " + new Date().toLocaleTimeString("en-GB") + ". Refreshes every 5 seconds."
        : "Could not update: " + (data.message || "connected-client information is unavailable");
      return data;
    }

    const connectedDataHooks = [];

    async function checkForDevices() {
      const button = element("check-devices");
      if (!requireAdminToken(button)) return;
      if (adminTokenChecking) {
        showMessage("The admin token is still being checked. Devices load when it finishes.");
        return;
      }
      if (!adminTokenVerified) {
        await checkAdminToken();
        return;
      }
      setButtonBusy(button, true);
      button.textContent = "Checking...";
      try {
        const data = await loadConnectedDevices();
        if (data === null) {
          showMessage("A device check is already running.");
        } else if (!data.available) {
          showMessage(data.message || "Connected-client information is unavailable.", "error");
        } else {
          const count = data.devices.length;
          const found = count === 1 ? "1 device found." : count + " devices found.";
          showMessage(count === 0 ? "No devices found." : found, "success");
        }
      } finally {
        setButtonBusy(button, false);
        button.textContent = "Check for devices";
      }
    }

    element("check-devices").addEventListener("click", checkForDevices);

    async function loadUplink() {
      try {
        renderUplink(await request("/api/uplink", "GET"));
      } catch (error) {
        recordRead("/api/uplink", error);
      }
    }

    function domainSearchUrl(domain) {
      return "https://duckduckgo.com/?q=" + encodeURIComponent(domain);
    }

    function domainHint(observation) {
      const label = observation.label || {};
      const apps = observation.seen_with || [];
      return {
        text: apps.length ? "Seen with " + apps.join(" and ") + " (guess)" : "",
        searchable: label.kind === "unknown" || label.kind === "cdn"
      };
    }

    function blockButtonState(observation) {
      const domain = observation.domain;
      if (observation.globally_blocked === true) {
        return {
          label: "Unblock",
          ariaLabel: "Unblock " + domain,
          path: "/api/observations/remove-global-block",
          busy: "Removing " + domain + " from the global block list...",
          done: "Removed " + domain + " from the global block list. " + ENFORCE_NOTE
        };
      }
      return {
        label: "Block globally",
        ariaLabel: "Block globally " + domain,
        path: "/api/observations/add-global-block",
        busy: "Adding " + domain + " to the global block list...",
        done: "Added " + domain + " to the global block list. " + ENFORCE_NOTE
      };
    }

    function profileButtonState(domain, profile) {
      let label = "Add to profile";
      let disabled = false;
      if (!profile) {
        disabled = true;
      } else if ((profile.website_domains || []).indexOf(domain) !== -1) {
        label = "Already in " + profile.name;
        disabled = true;
      }
      return { label: label, disabled: disabled, ariaLabel: label + " " + domain };
    }

    // Adding a name to a profile that does not allow only listed sites yet turns it into an
    // allow list, so every other site stops working for devices on it. The page asks first.
    function needsAllowOnlyConfirm(profile) {
      return Boolean(profile) && profile.website_mode !== "allow_only";
    }

    function allowOnlyConfirmText(subject, profile) {
      return "Adding " + subject + " turns the profile " + profile.name + " into an allow list. " +
        "Every other site will stop working for devices on that profile. Continue?";
    }

    // Callers wait for this only when needsAllowOnlyConfirm says so, so a profile that
    // already is an allow list adds its names at once and with no pause.
    async function askAllowOnly(subject, profile) {
      try {
        return await confirmWorkflow(allowOnlyConfirmText(subject, profile));
      } catch (error) {
        showMessage(error.message, "error");
        return false;
      }
    }

    function showProfileAddError(error, profileId) {
      if (error.status === 409 && error.code === "allow_only_confirmation_required" &&
          error.profile && error.profile.id === profileId) {
        const index = profiles.findIndex((profile) => profile.id === profileId);
        if (index !== -1) profiles[index] = error.profile;
      }
      showMessage(error.message, "error");
    }

    const ENFORCE_NOTE = "It applies on its own while profiles are running. " +
      "After Reset or Panic choose Start. After Panic also turn household blocking back on.";

    function queryCountText(count) {
      return count + (count === 1 ? " query" : " queries");
    }

    const GROUP_ACTION_MAX_NAMES = 64;

    const RISKY_BLOCK_NOTE = "Blocking a whole group of system or content delivery names could cut " +
      "the device off the internet. Block single names instead.";

    function isRiskyGroupKind(kind) {
      return kind === "system" || kind === "cdn";
    }

    function groupObservations(domains) {
      const groups = new Map();
      (domains || []).forEach((observation) => {
        const group = observation.group || { key: "domain:" + observation.domain, name: observation.domain };
        if (!groups.has(group.key)) {
          groups.set(group.key, {
            key: group.key, name: group.name, kind: group.kind || "", names: [], totalQueries: 0, single: true
          });
        }
        const entry = groups.get(group.key);
        if (!isRiskyGroupKind(entry.kind) && isRiskyGroupKind(group.kind)) entry.kind = group.kind;
        entry.names.push(observation);
        entry.totalQueries += Number(observation.query_count) || 0;
        entry.single = entry.names.length === 1;
      });
      return Array.from(groups.values()).sort((a, b) => {
        if (a.totalQueries !== b.totalQueries) return b.totalQueries - a.totalQueries;
        const first = a.name.toLowerCase();
        const second = b.name.toLowerCase();
        return first < second ? -1 : first > second ? 1 : 0;
      });
    }

    function groupBlockAction(group) {
      const blocked = group.names.filter((observation) => observation.globally_blocked === true);
      const open = group.names.filter((observation) => observation.globally_blocked !== true);
      const domains = (list) => list.map((observation) => observation.domain);
      const unblocking = blocked.length > 0 && open.length === 0;
      const action = unblocking
        ? {
            label: "Unblock all",
            path: blockButtonState({ globally_blocked: true }).path,
            domains: domains(blocked)
          }
        : {
            label: blocked.length > 0 ? "Block remaining" : "Block all",
            path: blockButtonState({ globally_blocked: false }).path,
            domains: domains(open)
          };
      action.ariaLabel = action.label + " " + groupSubject(group, action.domains.length);
      const risky = isRiskyGroupKind(group.kind);
      const limitNote = groupLimitNote(action.domains.length);
      const reason = (risky && !unblocking) ? RISKY_BLOCK_NOTE : limitNote;
      action.disabled = reason !== "";
      action.title = reason || action.ariaLabel;
      // The visible line under the header. It stays for a risky group and explains the button.
      action.note = risky ? RISKY_BLOCK_NOTE : limitNote;
      return action;
    }

    function groupUnblockAction(group) {
      if (!isRiskyGroupKind(group.kind)) return null;
      const blocked = group.names
        .filter((observation) => observation.globally_blocked === true)
        .map((observation) => observation.domain);
      if (blocked.length === 0 || blocked.length === group.names.length) return null;
      const text = "Unblock blocked names: " + groupSubject(group, blocked.length);
      const note = groupLimitNote(blocked.length);
      return {
        label: "Unblock blocked names",
        path: blockButtonState({ globally_blocked: true }).path,
        domains: blocked,
        disabled: note !== "",
        ariaLabel: text,
        title: note || text
      };
    }

    function groupProfileAction(group, profile) {
      const count = group.names.length;
      if (!profile) {
        const text = "Add all to profile: " + groupSubject(group, count) + " (no profile available)";
        return { label: "Add all to profile", disabled: true, domains: [], ariaLabel: text, title: text };
      }
      const missing = group.names
        .map((observation) => observation.domain)
        .filter((domain) => !profileButtonState(domain, profile).disabled);
      if (missing.length === 0) {
        const text = "All already in " + profile.name + ": " + groupSubject(group, count);
        return {
          label: "All already in " + profile.name,
          disabled: true,
          domains: [],
          ariaLabel: text,
          title: text
        };
      }
      const text = "Add all to profile: " + groupSubject(group, missing.length) + " to " + profile.name;
      const note = groupLimitNote(missing.length);
      return {
        label: "Add all to profile",
        disabled: note !== "",
        domains: missing,
        ariaLabel: text,
        title: note || text
      };
    }

    function groupCategoriesUniform(group) {
      const category = (observation) => (observation.label && observation.label.category) || "";
      return group.names.every((observation) => category(observation) === category(group.names[0]));
    }

    function rowLabelParts(parts, groupName, categoriesUniform) {
      const repeated = Boolean(groupName) && parts.labelName === groupName;
      return {
        showName: Boolean(parts.labelName) && !repeated,
        showCategory: Boolean(parts.category) && !(repeated && categoriesUniform)
      };
    }

    function groupNamesText(count) {
      return count + (count === 1 ? " name" : " names");
    }

    function groupSubject(group, count) {
      return count + " " + group.name + (count === 1 ? " name" : " names");
    }

    function isUnblockAction(action) {
      return action.path === blockButtonState({ globally_blocked: true }).path;
    }

    function groupBlockMessages(group, action) {
      const subject = groupSubject(group, action.domains.length);
      if (isUnblockAction(action)) {
        return {
          busy: "Removing " + subject + " from the global block list...",
          done: "Removed " + subject + " from the global block list. " + ENFORCE_NOTE
        };
      }
      return {
        busy: "Adding " + subject + " to the global block list...",
        done: "Added " + subject + " to the global block list. " + ENFORCE_NOTE
      };
    }

    function groupProfileMessages(group, action) {
      const subject = groupSubject(group, action.domains.length);
      return {
        busy: "Adding " + subject + " to the profile...",
        done: "Added " + subject + " to the profile."
      };
    }

    function groupBlockedText(group) {
      const blocked = group.names.filter((observation) => observation.globally_blocked === true).length;
      if (blocked === 0) return "";
      return blocked === group.names.length ? "All blocked" : blocked + " blocked";
    }

    function groupLimitNote(count) {
      if (count <= GROUP_ACTION_MAX_NAMES) return "";
      return "This group needs " + count + " names changed and one step takes at most " +
        GROUP_ACTION_MAX_NAMES + ". Use the buttons on the names instead.";
    }

    function observationGroupOpenKey(address, groupKey) {
      return address + "|" + groupKey;
    }

    function observationGroupDomId(address, groupKey) {
      const encoded = Array.from(observationGroupOpenKey(address, groupKey)).map((character) =>
        /[A-Za-z0-9-]/.test(character) ? character : "_" + character.codePointAt(0).toString(16) + "_"
      );
      return "observation-group-" + encoded.join("");
    }

    function chosenObservationProfile(choices, deviceKey, profiles, assignedId) {
      const ids = (profiles || []).map((profile) => profile.id);
      const remembered = choices.get(deviceKey);
      if (remembered !== undefined && ids.indexOf(remembered) !== -1) return remembered;
      if (assignedId && ids.indexOf(assignedId) !== -1) return assignedId;
      return ids.length > 0 ? ids[0] : "";
    }

    function domainRowParts(observation) {
      const label = observation.label || {};
      const hint = domainHint(observation);
      return {
        labelName: label.name || "",
        category: label.category || "",
        kind: label.kind || "",
        domain: observation.domain,
        hintText: hint.text,
        searchUrl: hint.searchable ? domainSearchUrl(observation.domain) : "",
        searchLabel: hint.searchable
          ? "Search the web for " + observation.domain + " (opens a new tab)"
          : ""
      };
    }

    function renderObservations(data) {
      const container = element("observations");
      const count = element("observation-count");
      observedDomainsByDevice = {};
      observedNames = new Set();
      container.replaceChildren();
      if (!data.available) {
        count.textContent = "Unavailable";
        const empty = document.createElement("p");
        empty.className = "device-empty";
        empty.textContent = data.message || "Traffic discovery is unavailable.";
        container.appendChild(empty);
        return;
      }
      const devices = data.devices || [];
      count.textContent = (data.total_domains || 0) + " domains";
      if (devices.length === 0) {
        const empty = document.createElement("p");
        empty.className = "device-empty";
        // After an upgrade cleared the list the page says so until the first new name arrives.
        empty.textContent = (data.list_cleared && data.list_cleared.message)
          || "No DNS observations yet. Connect a device and run a scan.";
        container.appendChild(empty);
        return;
      }
      devices.forEach((device) => {
        (device.domains || []).forEach((observation) => observedNames.add(observation.domain));
        if (device.assigned_device_id) {
          observedDomainsByDevice[device.assigned_device_id] = (device.domains || [])
            .map((observation) => observation.domain);
        }
        const card = document.createElement("div");
        card.className = "observation-device";
        const heading = document.createElement("div");
        heading.className = "observation-device-heading";
        const title = document.createElement("h3");
        title.textContent = deviceDisplayName(device);
        const address = document.createElement("p");
        address.textContent = device.ipv4_address;
        heading.append(title, address);
        const targetProfile = document.createElement("select");
        targetProfile.setAttribute("aria-label", "Profile for discovered domains");
        const chosenProfileId = chosenObservationProfile(
          observationProfileChoice,
          device.ipv4_address,
          profiles,
          device.assigned_profile_id
        );
        profiles.forEach((profile) => {
          const option = document.createElement("option");
          option.value = profile.id;
          option.textContent = "Add to " + profile.name;
          option.selected = profile.id === chosenProfileId;
          targetProfile.appendChild(option);
        });
        const profileSyncs = [];
        const syncProfileButtons = () => profileSyncs.forEach((sync) => sync());
        targetProfile.addEventListener("change", () => {
          observationProfileChoice.set(device.ipv4_address, targetProfile.value);
          syncProfileButtons();
        });
        const targetRow = document.createElement("div");
        targetRow.className = "observation-actions";
        targetRow.append(targetProfile);
        card.append(heading, targetRow);
        const list = document.createElement("ul");
        list.className = "observation-domain-list";
        // One name. A row of a group also gets its block button in blockSyncs so the group can
        // refresh it and calls afterBlockChange when its own block state changes. A row in a
        // group skips what its label line only repeats from the group header.
        const buildRow = (observation, blockSyncs, afterBlockChange, inGroup) => {
          const item = document.createElement("li");
          item.className = "observation-domain";
          item.dataset.key = device.ipv4_address + "|" + observation.domain;
          const domain = document.createElement("div");
          domain.className = "observation-domain-name";
          const parts = domainRowParts(observation);
          const labelShown = rowLabelParts(
            parts,
            inGroup ? inGroup.name : "",
            inGroup ? inGroup.categoriesUniform : false
          );
          if (labelShown.showName || labelShown.showCategory) {
            const labelLine = document.createElement("div");
            labelLine.className = "observation-label";
            if (labelShown.showName) {
              const labelName = document.createElement("strong");
              labelName.textContent = parts.labelName;
              labelLine.appendChild(labelName);
            }
            if (labelShown.showCategory) {
              const category = document.createElement("span");
              category.className = "observation-category";
              category.dataset.kind = parts.kind;
              category.textContent = parts.category;
              labelLine.appendChild(category);
            }
            domain.appendChild(labelLine);
          }
          const host = document.createElement("div");
          host.className = "observation-domain-host";
          const hostName = document.createElement("span");
          hostName.textContent = parts.domain;
          const badge = document.createElement("span");
          badge.className = "observation-badge";
          badge.textContent = "Blocked";
          host.append(hostName, badge);
          domain.appendChild(host);
          const hintLine = document.createElement("div");
          hintLine.className = "observation-hint-line";
          if (parts.hintText) {
            const hintText = document.createElement("span");
            hintText.className = "observation-hint";
            hintText.textContent = parts.hintText;
            hintLine.appendChild(hintText);
          }
          if (parts.searchUrl) {
            const link = document.createElement("a");
            link.className = "observation-search-link";
            link.href = parts.searchUrl;
            link.target = "_blank";
            link.rel = "noopener noreferrer";
            link.setAttribute("aria-label", parts.searchLabel);
            link.textContent = "What is this?";
            hintLine.appendChild(link);
          }
          if (hintLine.childNodes.length > 0) domain.appendChild(hintLine);
          const queryCount = document.createElement("span");
          queryCount.className = "observation-domain-count";
          queryCount.textContent = queryCountText(observation.query_count);
          const profileButton = document.createElement("button");
          profileButton.type = "button";
          profileButton.className = "observation-profile-button";
          const syncProfileButton = () => {
            // A request in flight keeps the button disabled. Its finally block syncs again.
            if (profileButton.getAttribute("aria-busy") === "true") return;
            const selectedProfile = profiles.find((profile) => profile.id === targetProfile.value);
            const state = profileButtonState(observation.domain, selectedProfile);
            profileButton.textContent = state.label;
            profileButton.title = state.label;
            profileButton.setAttribute("aria-label", state.ariaLabel);
            profileButton.disabled = state.disabled;
          };
          profileSyncs.push(syncProfileButton);
          syncProfileButton();
          profileButton.addEventListener("click", () => addDomainToProfile(
            observation.domain,
            targetProfile.value,
            profileButton,
            syncProfileButtons
          ));
          const blockButton = document.createElement("button");
          blockButton.type = "button";
          const syncBlockButton = () => {
            blockButton.textContent = blockButtonState(observation).label;
            blockButton.setAttribute("aria-label", blockButtonState(observation).ariaLabel);
            badge.hidden = observation.globally_blocked !== true;
          };
          syncBlockButton();
          blockSyncs.push(syncBlockButton);
          const onBlockChange = () => {
            syncBlockButton();
            if (afterBlockChange) afterBlockChange();
          };
          blockButton.addEventListener("click", () => changeGlobalBlock(
            observation,
            blockButton,
            onBlockChange
          ));
          item.append(domain, queryCount, profileButton, blockButton);
          return item;
        };
        // Two or more names of one app. Closed by default. The header holds a toggle button and
        // the action buttons side by side. The action buttons act on every name of the group.
        const buildGroup = (group) => {
          const item = document.createElement("li");
          item.className = "observation-group-item";
          item.dataset.key = observationGroupOpenKey(device.ipv4_address, group.key);
          const wrapper = document.createElement("div");
          wrapper.className = "observation-group";
          const openKey = observationGroupOpenKey(device.ipv4_address, group.key);
          const header = document.createElement("div");
          header.className = "observation-group-header";
          const body = document.createElement("div");
          body.className = "observation-group-body";
          body.id = observationGroupDomId(device.ipv4_address, group.key);
          const toggle = document.createElement("button");
          toggle.type = "button";
          toggle.className = "observation-group-toggle";
          toggle.setAttribute("aria-controls", body.id);
          const setOpen = (open) => {
            toggle.setAttribute("aria-expanded", open ? "true" : "false");
            body.hidden = !open;
            if (open) openObservationGroups.add(openKey);
            else openObservationGroups.delete(openKey);
          };
          setOpen(openObservationGroups.has(openKey));
          toggle.addEventListener("click", () => setOpen(body.hidden));
          const chevron = document.createElement("span");
          chevron.className = "observation-group-chevron";
          chevron.setAttribute("aria-hidden", "true");
          const title = document.createElement("span");
          title.className = "observation-group-title";
          const groupName = document.createElement("strong");
          groupName.className = "observation-group-name";
          groupName.textContent = group.name;
          const meta = document.createElement("span");
          meta.className = "observation-group-meta";
          const nameCount = document.createElement("span");
          nameCount.className = "observation-group-count";
          nameCount.textContent = groupNamesText(group.names.length);
          const groupBadge = document.createElement("span");
          groupBadge.className = "observation-badge";
          meta.append(nameCount, groupBadge);
          title.append(groupName, meta);
          const totalQueries = document.createElement("span");
          totalQueries.className = "observation-domain-count";
          totalQueries.textContent = queryCountText(group.totalQueries);
          toggle.append(chevron, title, totalQueries);
          const groupProfileButton = document.createElement("button");
          groupProfileButton.type = "button";
          groupProfileButton.className = "observation-group-profile-button";
          const groupBlockButton = document.createElement("button");
          groupBlockButton.type = "button";
          groupBlockButton.className = "observation-group-block-button";
          const groupUndoButton = document.createElement("button");
          groupUndoButton.type = "button";
          groupUndoButton.className = "observation-group-unblock-button";
          groupUndoButton.hidden = true;
          const groupNote = document.createElement("p");
          groupNote.className = "observation-group-note";
          groupNote.id = body.id + "-note";
          groupNote.hidden = true;
          const syncGroupProfile = () => {
            // A request in flight keeps the button disabled. Its finally block syncs again.
            if (groupProfileButton.getAttribute("aria-busy") === "true") return;
            const selectedProfile = profiles.find((profile) => profile.id === targetProfile.value);
            const action = groupProfileAction(group, selectedProfile);
            groupProfileButton.textContent = action.label;
            groupProfileButton.title = action.title;
            groupProfileButton.setAttribute("aria-label", action.ariaLabel);
            groupProfileButton.disabled = action.disabled;
          };
          const syncGroupBlock = () => {
            const blockedText = groupBlockedText(group);
            groupBadge.textContent = blockedText;
            groupBadge.hidden = blockedText === "";
            const action = groupBlockAction(group);
            groupNote.textContent = action.note;
            groupNote.hidden = action.note === "";
            if (groupBlockButton.getAttribute("aria-busy") !== "true") {
              groupBlockButton.textContent = action.label;
              groupBlockButton.title = action.title;
              groupBlockButton.setAttribute("aria-label", action.ariaLabel);
              groupBlockButton.disabled = action.disabled;
              if (action.disabled && action.note) {
                groupBlockButton.setAttribute("aria-describedby", groupNote.id);
              } else {
                groupBlockButton.removeAttribute("aria-describedby");
              }
            }
            const undo = groupUnblockAction(group);
            groupUndoButton.hidden = undo === null;
            if (undo !== null && groupUndoButton.getAttribute("aria-busy") !== "true") {
              groupUndoButton.textContent = undo.label;
              groupUndoButton.title = undo.title;
              groupUndoButton.setAttribute("aria-label", undo.ariaLabel);
              groupUndoButton.disabled = undo.disabled;
            }
          };
          const rowSyncs = [];
          const syncRowsAndHeader = () => {
            rowSyncs.forEach((sync) => sync());
            syncGroupBlock();
          };
          profileSyncs.push(syncGroupProfile);
          syncGroupProfile();
          syncGroupBlock();
          groupProfileButton.addEventListener("click", () => addGroupToProfile(
            group,
            profiles.find((profile) => profile.id === targetProfile.value),
            groupProfileButton,
            syncProfileButtons
          ));
          groupBlockButton.addEventListener("click", () => changeGroupBlock(
            group,
            groupBlockButton,
            syncRowsAndHeader
          ));
          groupUndoButton.addEventListener("click", () => changeGroupBlock(
            group,
            groupUndoButton,
            syncRowsAndHeader,
            groupUnblockAction
          ));
          header.append(toggle, groupProfileButton, groupBlockButton, groupUndoButton);
          const rows = document.createElement("ul");
          rows.className = "observation-domain-list";
          const inGroup = { name: group.name, categoriesUniform: groupCategoriesUniform(group) };
          group.names.forEach((observation) => {
            rows.appendChild(buildRow(observation, rowSyncs, syncGroupBlock, inGroup));
          });
          body.appendChild(rows);
          wrapper.append(header, groupNote, body);
          item.appendChild(wrapper);
          return item;
        };
        groupObservations(device.domains).forEach((group) => {
          list.appendChild(group.single ? buildRow(group.names[0], [], null) : buildGroup(group));
        });
        card.append(list);
        container.appendChild(card);
      });
    }

    async function addDomainToProfile(domain, profileId, button, onChange) {
      if (!profileId) return;
      const profile = profiles.find((item) => item.id === profileId);
      const confirmAllowOnly = needsAllowOnlyConfirm(profile);
      if (confirmAllowOnly && !await askAllowOnly(domain, profile)) return;
      setButtonBusy(button, true);
      showMessage("Adding " + domain + " to the profile...", "working");
      try {
        const result = await request("/api/observations/add-to-profile", "POST", {
          domain: domain,
          profile_id: profileId,
          confirm_allow_only: confirmAllowOnly
        });
        const index = profiles.findIndex((profile) => profile.id === profileId);
        if (index !== -1 && result.profile) profiles[index] = result.profile;
        refreshAfterSuccess("Added " + domain + " to the profile.");
      } catch (error) {
        showProfileAddError(error, profileId);
      } finally {
        setButtonBusy(button, false);
        if (onChange) onChange();
      }
    }

    async function changeGlobalBlock(observation, button, onChange) {
      const state = blockButtonState(observation);
      setButtonBusy(button, true);
      showMessage(state.busy, "working");
      try {
        await request(state.path, "POST", { domain: observation.domain });
        observation.globally_blocked = observation.globally_blocked !== true;
        refreshAfterSuccess(state.done);
      } catch (error) {
        showMessage(error.message, "error");
      } finally {
        setButtonBusy(button, false);
        onChange();
      }
    }

    async function changeGroupBlock(group, button, onChange, pickAction) {
      const action = (pickAction || groupBlockAction)(group);
      if (!action || action.disabled || action.domains.length === 0) return;
      const messages = groupBlockMessages(group, action);
      setButtonBusy(button, true);
      showMessage(messages.busy, "working");
      try {
        await request(action.path, "POST", { domains: action.domains });
        const blocked = !isUnblockAction(action);
        group.names.forEach((observation) => {
          if (action.domains.indexOf(observation.domain) !== -1) observation.globally_blocked = blocked;
        });
        refreshAfterSuccess(messages.done);
      } catch (error) {
        showMessage(error.message, "error");
      } finally {
        setButtonBusy(button, false);
        onChange();
      }
    }

    async function addGroupToProfile(group, profile, button, onChange) {
      const action = groupProfileAction(group, profile);
      if (action.disabled) return;
      const messages = groupProfileMessages(group, action);
      const confirmAllowOnly = needsAllowOnlyConfirm(profile);
      if (confirmAllowOnly &&
          !await askAllowOnly(groupSubject(group, action.domains.length), profile)) return;
      setButtonBusy(button, true);
      showMessage(messages.busy, "working");
      try {
        const result = await request("/api/observations/add-to-profile", "POST", {
          domains: action.domains,
          profile_id: profile.id,
          confirm_allow_only: confirmAllowOnly
        });
        const index = profiles.findIndex((item) => item.id === profile.id);
        if (index !== -1 && result.profile) profiles[index] = result.profile;
        refreshAfterSuccess(messages.done);
      } catch (error) {
        showProfileAddError(error, profile.id);
      } finally {
        setButtonBusy(button, false);
        if (onChange) onChange();
      }
    }

    async function loadObservations() {
      try {
        renderObservations(await request("/api/observations", "GET"));
      } catch (error) {
        renderObservations({ available: false, devices: [], message: error.message });
      }
      renderServiceListObserved();
    }

    function formatQuotaBytes(bytes) {
      if (bytes >= 1073741824) return (bytes / 1073741824).toFixed(2) + " GB";
      if (bytes >= 1048576) return (bytes / 1048576).toFixed(1) + " MB";
      if (bytes >= 1024) return (bytes / 1024).toFixed(1) + " KB";
      return bytes + " bytes";
    }

    function quotaResetText(quota) {
      if (quota.reset_mode === "interval") {
        const minutes = Math.round(quota.reset_interval_seconds / 60);
        return "resets every " + minutes + (minutes === 1 ? " minute" : " minutes");
      }
      if (quota.reset_mode === "daily") {
        return "resets daily at " + (quota.reset_times || []).map((minute) =>
          String(Math.floor(minute / 60)).padStart(2, "0") + ":" +
          String(minute % 60).padStart(2, "0")
        ).join(", ");
      }
      return "manual reset";
    }

    function quotaSchedulePayload(prefix) {
      const mode = element(prefix + "-reset-mode").value;
      let intervalSeconds = 0;
      let resetTimes = [];
      if (mode === "interval") {
        intervalSeconds = Number(element(prefix + "-interval").value) * 60;
      } else if (mode === "daily") {
        resetTimes = element(prefix + "-times").value.split(",")
          .map((value) => value.trim())
          .filter(Boolean)
          .map((value) => {
            const match = /^(\d{1,2}):(\d{2})$/.exec(value);
            if (!match || Number(match[1]) > 23 || Number(match[2]) > 59) {
              throw new Error("Reset times must use HH:MM format.");
            }
            return Number(match[1]) * 60 + Number(match[2]);
          });
        if (!resetTimes.length) throw new Error("Add at least one daily reset time.");
      }
      return {
        reset_mode: mode,
        reset_interval_seconds: intervalSeconds,
        reset_times: resetTimes
      };
    }

    function syncQuotaReset(prefix) {
      const mode = element(prefix + "-reset-mode").value;
      element(prefix + "-interval-field").hidden = mode !== "interval";
      element(prefix + "-times-field").hidden = mode !== "daily";
      // The browser skips a disabled field when it checks the form. An out-of-range value left
      // in a hidden field would otherwise stop the save without a word.
      element(prefix + "-interval").disabled = mode !== "interval";
      element(prefix + "-times").disabled = mode !== "daily";
    }

    function setQuotaAmount(prefix, bytes) {
      const unit = bytes >= 1073741824 && bytes % 1073741824 === 0
        ? 1073741824
        : 1048576;
      element(prefix + "-unit").value = String(unit);
      element(prefix + "-amount").value = Math.max(1, Math.round(bytes / unit));
    }

    function quotaRowProgress(quota) {
      return Math.min(100, Math.round((quota.used_bytes / quota.limit_bytes) * 100));
    }

    function createQuotaRow({ quota, service }) {
      const row = document.createElement("div");
      row.className = "quota-row" + (quota.reached ? " reached" : "");
      row.dataset.key = service === "group" ? quota.group_id : service ? quota.id : quota.device_id;
      const heading = document.createElement("div");
      heading.className = "quota-row-heading";
      const title = document.createElement("strong");
      title.textContent = service === "group"
        ? quota.group_name + " · " + quota.profile_name
        : service
          ? quota.name + " · " + quota.device_name
          : quota.device_name + " · " + quota.profile_name;
      const status = document.createElement("span");
      status.className = "quota-status";
      status.textContent = quota.reached ? "Limit reached" : quotaResetText(quota);
      heading.append(title, status);
      const detail = document.createElement("span");
      detail.textContent = formatQuotaBytes(quota.used_bytes) + " used of " +
        formatQuotaBytes(quota.limit_bytes) + " · " + formatQuotaBytes(quota.remaining_bytes) + " left" +
        (service === true ? " · weight " + (quota.weight_percent / 100).toFixed(1) + "×" : "");
      const meter = document.createElement("div");
      meter.className = "quota-meter";
      const meterValue = document.createElement("span");
      meterValue.style.width = quotaRowProgress(quota) + "%";
      meter.appendChild(meterValue);
      const actions = document.createElement("div");
      actions.className = "quota-actions";
      const editButton = document.createElement("button");
      editButton.type = "button";
      editButton.textContent = "Edit";
      editButton.addEventListener("click", () => service === "group"
        ? editGroupDataLimit(quota)
        : service
          ? editServiceQuota(quota)
          : editDataLimit(quota));
      const resetButton = document.createElement("button");
      resetButton.type = "button";
      resetButton.textContent = "Reset usage";
      resetButton.addEventListener("click", () => resetQuota(quota, service, resetButton));
      const deleteButton = document.createElement("button");
      deleteButton.type = "button";
      deleteButton.textContent = "Delete";
      deleteButton.addEventListener("click", () => {
        if (service === "group") {
          deleteItem("/api/group-data-limits/delete", { group_id: quota.group_id },
            "Shared limit deleted.", deleteButton);
        } else if (service) {
          deleteItem("/api/service-quotas/delete", { id: quota.id },
            "Service limit deleted.", deleteButton);
        } else {
          deleteItem("/api/data-limits/delete", { device_id: quota.device_id },
            "Device limit deleted.", deleteButton);
        }
      });
      actions.append(editButton, resetButton, deleteButton);
      // Only a service limit has website names. A shared group limit would stop the whole list here.
      if (service === true) {
        const domains = document.createElement("span");
        domains.textContent = "Domains: " + quota.domains.join(", ");
        actions.append(domains);
      }
      row.append(heading, detail, meter, actions);
      return row;
    }

    function renderDataLimits(data) {
      const limits = data.data_limits || [];
      const container = element("data-limit-list");
      element("data-limit-count").textContent = limits.length + (limits.length === 1 ? " limit" : " limits");
      container.replaceChildren();
      if (!limits.length) {
        container.textContent = data.error || "No device limits saved.";
        return;
      }
      limits.forEach((quota) => container.appendChild(createQuotaRow({ quota, service: false })));
    }

    function renderGroups(data) {
      groups = data.groups || [];
      groupsLoaded = !data.error;
      updateGroupMemberOptions();
      renderApplySummary();
      const container = element("group-list");
      container.replaceChildren();
      if (!groups.length) {
        container.textContent = data.error || "No groups saved.";
        return;
      }
      groups.forEach((group) => {
        const row = document.createElement("div");
        row.className = "quota-row";
        row.dataset.key = group.id;
        const heading = document.createElement("div");
        heading.className = "quota-row-heading";
        const title = document.createElement("strong");
        title.textContent = group.name + " · " + group.id;
        const profile = document.createElement("span");
        profile.className = "quota-status";
        profile.textContent = group.profile_name
          ? "Profile: " + group.profile_name
          : "Each device keeps its profile";
        heading.append(title, profile);
        const members = document.createElement("span");
        members.textContent = group.device_ids.length + (group.device_ids.length === 1 ? " device: " : " devices: ") +
          group.devices.map((device) => deviceDisplayName(device)).join(", ");
        const actions = document.createElement("div");
        actions.className = "quota-actions";
        const editButton = document.createElement("button");
        editButton.type = "button";
        editButton.textContent = "Edit devices";
        editButton.addEventListener("click", () => editGroup(group));
        const deleteButton = document.createElement("button");
        deleteButton.type = "button";
        deleteButton.textContent = "Delete group";
        deleteButton.addEventListener("click", () => deleteGroup(group, deleteButton));
        actions.append(editButton, deleteButton);
        row.append(heading, members, actions);
        container.appendChild(row);
      });
    }

    function renderGroupDataLimits(data) {
      const limits = data.group_data_limits || [];
      const container = element("group-data-limit-list");
      element("group-data-limit-count").textContent = limits.length +
        (limits.length === 1 ? " group limit" : " group limits");
      container.replaceChildren();
      if (!limits.length) {
        container.textContent = data.error || "No shared group limits saved.";
        return;
      }
      limits.forEach((quota) => container.appendChild(createQuotaRow({ quota, service: "group" })));
    }

    function renderServiceQuotas(data) {
      const quotas = data.service_quotas || [];
      const container = element("service-quota-list");
      element("service-quota-count").textContent = quotas.length + (quotas.length === 1 ? " service limit" : " service limits");
      container.replaceChildren();
      if (!quotas.length) {
        container.textContent = data.error || "No service limits saved.";
        return;
      }
      quotas.forEach((quota) => container.appendChild(createQuotaRow({ quota, service: true })));
    }

    let serviceCatalogue = [];

    function renderServiceCatalogue() {
      const container = element("service-list-catalogue");
      const previous = Array.from(container.querySelectorAll("input"));
      const active = previous.includes(document.activeElement) ? document.activeElement : null;
      const activeIndex = previous.indexOf(active);
      const existing = new Map(previous.map((input) => [input.value, input.parentElement]));
      const retained = new Set(serviceCatalogue.map((service) => service.key));
      previous.forEach((input) => {
        if (!retained.has(input.value)) input.parentElement.remove();
      });
      let legend = container.querySelector("legend");
      if (!legend) {
        legend = document.createElement("legend");
        legend.textContent = "Services";
        container.prepend(legend);
      }
      serviceCatalogue.forEach((service, index) => {
        let label = existing.get(service.key);
        if (!label) {
          label = document.createElement("label");
          label.className = "quota-check";
          const input = document.createElement("input");
          input.type = "checkbox";
          input.value = service.key;
          label.append(input, document.createTextNode(""));
        }
        const title = service.domains.join(", ");
        if (label.title !== title) label.title = title;
        const name = " " + service.name;
        if (label.lastChild.textContent !== name) label.lastChild.textContent = name;
        const position = container.children[index + 1] || null;
        if (position !== label) container.insertBefore(label, position);
      });
      if (active) {
        const current = Array.from(container.querySelectorAll("input"));
        const target = active.isConnected ? active :
          current[Math.min(activeIndex, current.length - 1)] || element("service-list-hostnames");
        if (document.activeElement !== target) target.focus({ preventScroll: true });
      }
    }

    function renderServiceListObserved() {
      const select = element("service-list-observed");
      const previous = select.value;
      const names = Array.from(observedNames).sort();
      const retained = new Set(["", ...names]);
      const existing = new Map(Array.from(select.options).map((option) => [option.value, option]));
      Array.from(select.options).forEach((option) => {
        if (!retained.has(option.value)) option.remove();
      });
      ["", ...names].forEach((name, index) => {
        let option = existing.get(name);
        if (!option) {
          option = document.createElement("option");
          option.value = name;
        }
        const text = name || (names.length ? "Choose a name" : "No names seen yet");
        if (option.textContent !== text) option.textContent = text;
        const position = select.options[index] || null;
        if (position !== option) select.insertBefore(option, position);
      });
      select.value = retained.has(previous) ? previous : "";
    }

    function renderServiceLists(data) {
      const lists = data.service_lists || [];
      serviceListsState = lists;
      serviceListsLoaded = true;
      renderSidebarStatus();
      const container = element("service-list-list");
      element("service-list-count").textContent = lists.length +
        (lists.length === 1 ? " service list" : " service lists");
      container.replaceChildren();
      if (!lists.length) {
        container.textContent = data.error || "No service lists saved.";
        return;
      }
      lists.forEach((serviceList) => {
        const row = document.createElement("div");
        row.className = "quota-row";
        row.dataset.key = serviceList.id;
        const heading = document.createElement("div");
        heading.className = "quota-heading";
        const title = document.createElement("strong");
        title.textContent = serviceList.name + " (" + serviceList.id + ")";
        const detail = document.createElement("span");
        detail.textContent = "All affected devices → " + serviceList.profile_name +
          (!serviceList.enabled ? " · disabled" : serviceList.outage_now ? " · random outage now" : "");
        heading.append(title, detail);
        const names = serviceCatalogue
          .filter((service) => serviceList.services.includes(service.key))
          .map((service) => service.name)
          .concat(serviceList.hostnames);
        const services = document.createElement("span");
        services.textContent = names.join(", ");
        const actions = document.createElement("div");
        actions.className = "quota-actions";
        const editButton = document.createElement("button");
        editButton.type = "button";
        editButton.textContent = "Edit";
        editButton.addEventListener("click", () => editServiceList(serviceList));
        const deleteButton = document.createElement("button");
        deleteButton.type = "button";
        deleteButton.textContent = "Delete";
        deleteButton.addEventListener("click", async () => {
          setButtonBusy(deleteButton, true);
          try {
            await request("/api/service-lists/delete", "POST", { id: serviceList.id });
            refreshAfterSuccess("Service list deleted.");
          } catch (error) {
            showMessage(error.message, "error");
          } finally {
            setButtonBusy(deleteButton, false);
          }
        });
        actions.append(editButton, deleteButton);
        row.append(heading, services, actions);
        container.appendChild(row);
      });
    }

    function editServiceList(serviceList) {
      element("service-list-id").value = serviceList.id;
      element("service-list-name").value = serviceList.name;
      element("service-list-profile").value = serviceList.profile_id;
      element("service-list-hostnames").value = serviceList.hostnames.join("\n");
      element("service-list-enabled").checked = serviceList.enabled;
      element("service-list-catalogue").querySelectorAll("input").forEach((input) => {
        input.checked = serviceList.services.includes(input.value);
      });
      revealElement(element("service-list-form"));
    }

    function renderServiceAssignments(data) {
      const assignments = data.service_assignments || [];
      const container = element("service-assignment-list");
      element("service-assignment-count").textContent = assignments.length +
        (assignments.length === 1 ? " service profile" : " service profiles");
      container.replaceChildren();
      if (!assignments.length) {
        container.textContent = data.error || "No browser service profiles saved.";
        return;
      }
      assignments.forEach((assignment) => {
        const row = document.createElement("div");
        row.className = "quota-row";
        row.dataset.key = assignment.id;
        const heading = document.createElement("div");
        heading.className = "quota-heading";
        const title = document.createElement("strong");
        title.textContent = assignment.name + " (" + assignment.id + ")";
        const detail = document.createElement("span");
        detail.textContent = assignment.device_name + " → " + assignment.profile_name +
          (assignment.enabled ? "" : " · disabled");
        heading.append(title, detail);
        const domains = document.createElement("span");
        domains.textContent = assignment.domains.join(", ");
        const actions = document.createElement("div");
        actions.className = "quota-actions";
        const editButton = document.createElement("button");
        editButton.type = "button";
        editButton.textContent = "Edit";
        editButton.addEventListener("click", () => editServiceAssignment(assignment));
        const deleteButton = document.createElement("button");
        deleteButton.type = "button";
        deleteButton.textContent = "Delete";
        deleteButton.addEventListener("click", async () => {
          setButtonBusy(deleteButton, true);
          try {
            await request("/api/service-assignments/delete", "POST", { id: assignment.id });
            refreshAfterSuccess("Browser service profile deleted.");
          } catch (error) {
            showMessage(error.message, "error");
          } finally {
            setButtonBusy(deleteButton, false);
          }
        });
        actions.append(editButton, deleteButton);
        row.append(heading, domains, actions);
        container.appendChild(row);
      });
    }

    function editServiceAssignment(assignment) {
      if (typeof guardDraft === "function" && guardDraft("service-assignment-form", () => editServiceAssignment(assignment))) return;
      element("service-assignment-id").value = assignment.id;
      element("service-assignment-name").value = assignment.name;
      element("service-assignment-device").value = assignment.device_id;
      element("service-assignment-profile").value = assignment.profile_id;
      element("service-assignment-domains").value = assignment.domains.join("\n");
      element("service-assignment-enabled").checked = assignment.enabled;
      revealElement(element("service-assignment-form"));
    }

    function renderTimeQuotas(data) {
      const quotas = data.time_quotas || [];
      const container = element("time-quota-list");
      element("time-quota-count").textContent = quotas.length + (quotas.length === 1 ? " time limit" : " time limits");
      container.replaceChildren();
      if (!quotas.length) {
        container.textContent = data.error || "No time limits saved.";
        return;
      }
      quotas.forEach((quota) => container.appendChild(createTimeQuotaRow(quota)));
    }

    function populateQuotaDomains(prefix, selectedDomains) {
      const select = element(prefix + "-domains");
      const device = element(prefix + "-device").value;
      const observed = observedDomainsByDevice[device] || [];
      if (selectedDomains) savedQuotaDomains[prefix] = { device: device, domains: selectedDomains };
      // A limit being edited keeps the names it was saved with while its device is chosen.
      const saved = savedQuotaDomains[prefix] && savedQuotaDomains[prefix].device === device
        ? savedQuotaDomains[prefix].domains : [];
      const domains = observed.concat(saved.filter((domain) => !observed.includes(domain)));
      select.replaceChildren();
      if (!domains.length) {
        const option = document.createElement("option");
        option.textContent = "No observed domains for this device";
        option.disabled = true;
        select.appendChild(option);
        return;
      }
      domains.forEach((domain) => {
        const option = document.createElement("option");
        option.value = domain;
        option.textContent = observed.includes(domain) ? domain : domain + " (saved, not seen lately)";
        option.selected = saved.includes(domain);
        select.appendChild(option);
      });
    }

    function populateServiceQuotaDomains(selectedDomains) {
      populateQuotaDomains("service-quota", selectedDomains);
    }

    function populateTimeQuotaDomains(selectedDomains) {
      populateQuotaDomains("time-quota", selectedDomains);
    }

    function editDataLimit(quota) {
      if (typeof guardDraft === "function" && guardDraft("data-limit-form", () => editDataLimit(quota))) return;
      element("data-limit-device").value = quota.device_id;
      element("data-limit-profile").value = quota.profile_id;
      setQuotaAmount("data-limit", quota.limit_bytes);
      element("data-limit-reset-mode").value = quota.reset_mode;
      element("data-limit-interval").value = Math.max(1, Math.round(quota.reset_interval_seconds / 60));
      element("data-limit-times").value = (quota.reset_times || []).map((minute) =>
        String(Math.floor(minute / 60)).padStart(2, "0") + ":" + String(minute % 60).padStart(2, "0")
      ).join(", ");
      element("data-limit-enabled").checked = quota.enabled;
      syncQuotaReset("data-limit");
      revealElement(element("data-limit-form"));
    }

    function editGroup(group) {
      if (groupSubmitting) {
        showMessage("Wait for the group save to finish before editing another group.", "working");
        return;
      }
      if (typeof guardDraft === "function" && guardDraft("group-form", () => editGroup(group))) return;
      editingGroupId = group.id;
      groupSaveError = "";
      element("group-id").value = group.id;
      element("group-id").readOnly = true;
      element("group-id").dataset.typed = "true";
      element("group-name").value = group.name;
      element("group-profile").value = group.profile_id || "";
      if (group.profile_id && !profiles.some((profile) => profile.id === group.profile_id)) {
        element("group-profile").add(new Option(group.profile_id + " (no longer available)", group.profile_id));
        element("group-profile").value = group.profile_id;
      }
      groupSelectedMembers.clear();
      group.device_ids.forEach((id) => groupSelectedMembers.add(id));
      element("group-save").textContent = "Save changes";
      element("group-edit-status").textContent = "Editing " + group.name;
      element("group-edit-status").hidden = false;
      element("group-cancel-edit").hidden = false;
      element("group-removal-help").hidden = false;
      updateGroupMemberOptions();
      revealElement(element("group-form"));
    }

    async function deleteGroup(group, button) {
      if (groupSubmitting) {
        showMessage("Wait for the group save to finish before deleting a group.", "working");
        return;
      }
      if (!await confirmFromButton(button, "Delete " + group.name + "? Members keep their saved profiles. The group's shared data limit, schedules and running scenarios are deleted.",
          {title: "Delete this group?", confirmLabel: "Delete group", danger: true})) return;
      try {
        if (groupSubmitting) {
          showMessage("Wait for the group save to finish before deleting a group.", "working");
          return;
        }
        await request("/api/groups/delete", "POST", { id: group.id });
        if (editingGroupId === group.id) stopEditing("group");
        refreshAfterSuccess("Device group deleted. Saved device profiles remain.");
      } catch (error) {
        showMessage(error.message, "error");
      } finally {
        setButtonBusy(button, false);
      }
    }

    function editGroupDataLimit(quota) {
      if (typeof guardDraft === "function" && guardDraft("group-data-limit-form", () => editGroupDataLimit(quota))) return;
      element("group-data-limit-group").value = quota.group_id;
      element("group-data-limit-profile").value = quota.profile_id;
      setQuotaAmount("group-data-limit", quota.limit_bytes);
      element("group-data-limit-reset-mode").value = quota.reset_mode;
      element("group-data-limit-interval").value = Math.max(1, Math.round(quota.reset_interval_seconds / 60));
      element("group-data-limit-times").value = (quota.reset_times || []).map((minute) =>
        String(Math.floor(minute / 60)).padStart(2, "0") + ":" +
        String(minute % 60).padStart(2, "0")
      ).join(", ");
      element("group-data-limit-enabled").checked = quota.enabled;
      syncQuotaReset("group-data-limit");
      revealElement(element("group-data-limit-form"));
    }

    function editServiceQuota(quota) {
      if (typeof guardDraft === "function" && guardDraft("service-quota-form", () => editServiceQuota(quota))) return;
      element("service-quota-id").value = quota.id;
      element("service-quota-id").dataset.typed = "true";
      element("service-quota-name").value = quota.name;
      element("service-quota-device").value = quota.device_id;
      element("service-quota-profile").value = quota.profile_id;
      setQuotaAmount("service-quota", quota.limit_bytes);
      element("service-quota-weight").value = (quota.weight_percent / 100).toFixed(1);
      element("service-quota-reset-mode").value = quota.reset_mode;
      element("service-quota-interval").value = Math.max(1, Math.round(quota.reset_interval_seconds / 60));
      element("service-quota-times").value = (quota.reset_times || []).map((minute) =>
        String(Math.floor(minute / 60)).padStart(2, "0") + ":" + String(minute % 60).padStart(2, "0")
      ).join(", ");
      element("service-quota-enabled").checked = quota.enabled;
      populateServiceQuotaDomains(quota.domains);
      syncQuotaReset("service-quota");
      revealElement(element("service-quota-form"));
    }

    function formatQuotaDuration(seconds) {
      if (seconds >= 3600 && seconds % 3600 === 0) return Math.floor(seconds / 3600) + (seconds === 3600 ? " hour" : " hours");
      if (seconds >= 60 && seconds % 60 === 0) return Math.floor(seconds / 60) + (seconds === 60 ? " minute" : " minutes");
      return seconds + (seconds === 1 ? " second" : " seconds");
    }

    function editTimeQuota(quota) {
      if (typeof guardDraft === "function" && guardDraft("time-quota-form", () => editTimeQuota(quota))) return;
      element("time-quota-id").value = quota.id;
      element("time-quota-id").dataset.typed = "true";
      element("time-quota-name").value = quota.name;
      element("time-quota-device").value = quota.device_id;
      element("time-quota-profile").value = quota.profile_id;
      const unit = quota.limit_seconds >= 3600 && quota.limit_seconds % 3600 === 0 ? 3600 : 60;
      element("time-quota-unit").value = String(unit);
      element("time-quota-amount").value = Math.max(1, Math.round(quota.limit_seconds / unit));
      element("time-quota-window").value = Math.max(1, Math.round(quota.window_duration_seconds / 60));
      element("time-quota-reset-mode").value = quota.reset_mode;
      element("time-quota-interval").value = Math.max(1, Math.round(quota.reset_interval_seconds / 60));
      element("time-quota-times").value = (quota.reset_times || []).map((minute) =>
        String(Math.floor(minute / 60)).padStart(2, "0") + ":" +
        String(minute % 60).padStart(2, "0")
      ).join(", ");
      element("time-quota-enabled").checked = quota.enabled;
      populateTimeQuotaDomains(quota.domains);
      syncQuotaReset("time-quota");
      revealElement(element("time-quota-form"));
    }

    async function timeQuotaAction(quota, action, button) {
      setButtonBusy(button, true);
      showMessage(action === "start" ? "Starting the time limit..." : "Stopping the time limit...", "working");
      try {
        const result = await request("/api/time-quotas/" + action, "POST", { id: quota.id });
        refreshAfterSuccess(
          result.live_applied
            ? (action === "start" ? "Time quota timer started and applied." : "Time quota updated and applied.")
            : (action === "start" ? "Time quota timer started." : "Time quota updated.")
        );
      } catch (error) {
        showMessage(error.message, "error");
      } finally {
        setButtonBusy(button, false);
      }
    }

    async function resetTimeQuota(quota, button) {
      setButtonBusy(button, true);
      showMessage("Resetting time limit usage...", "working");
      try {
        await request("/api/time-quotas/reset", "POST", { id: quota.id });
        showMessage("Time quota usage reset.", "success");
        loadQuotas();
      } catch (error) {
        showMessage(error.message, "error");
      } finally {
        setButtonBusy(button, false);
      }
    }

    function createTimeQuotaRow(quota) {
      const row = document.createElement("div");
      row.className = "quota-row" + (quota.reached ? " reached" : "");
      row.dataset.key = quota.id;
      const heading = document.createElement("div");
      heading.className = "quota-row-heading";
      const title = document.createElement("strong");
      title.textContent = quota.name + " · " + quota.device_name;
      const status = document.createElement("span");
      status.className = "quota-status";
      status.textContent = quota.reached
        ? "Limit reached"
        : (quota.running ? formatQuotaDuration(quota.window_remaining_seconds) + " left in timer" : "Timer stopped");
      heading.append(title, status);
      const detail = document.createElement("span");
      detail.textContent = formatQuotaDuration(quota.used_seconds) + " used of " +
        formatQuotaDuration(quota.limit_seconds) + " · timer window " +
        formatQuotaDuration(quota.window_duration_seconds) + " · " + quotaResetText(quota);
      const meter = document.createElement("div");
      meter.className = "quota-meter";
      const meterValue = document.createElement("span");
      meterValue.style.width = Math.min(100, Math.round((quota.used_seconds / quota.limit_seconds) * 100)) + "%";
      meter.appendChild(meterValue);
      const actions = document.createElement("div");
      actions.className = "quota-actions";
      const editButton = document.createElement("button");
      editButton.type = "button";
      editButton.textContent = "Edit";
      editButton.addEventListener("click", () => editTimeQuota(quota));
      const timerButton = document.createElement("button");
      timerButton.type = "button";
      timerButton.textContent = quota.running ? "Stop timer" : "Start timer";
      timerButton.addEventListener("click", () => timeQuotaAction(quota, quota.running ? "stop" : "start", timerButton));
      const resetButton = document.createElement("button");
      resetButton.type = "button";
      resetButton.textContent = "Reset usage";
      resetButton.addEventListener("click", () => resetTimeQuota(quota, resetButton));
      const deleteButton = document.createElement("button");
      deleteButton.type = "button";
      deleteButton.textContent = "Delete";
      deleteButton.addEventListener("click", () => deleteItem(
        "/api/time-quotas/delete", { id: quota.id }, "Time limit deleted.", deleteButton
      ));
      const domains = document.createElement("span");
      domains.textContent = "Domains: " + quota.domains.join(", ");
      actions.append(editButton, timerButton, resetButton, deleteButton, domains);
      row.append(heading, detail, meter, actions);
      return row;
    }

    async function resetQuota(quota, service, button) {
      setButtonBusy(button, true);
      showMessage("Resetting quota usage...", "working");
      try {
        const path = service === "group"
          ? "/api/group-data-limits/reset"
          : service ? "/api/service-quotas/reset" : "/api/data-limits/reset";
        const body = service === "group"
          ? { group_id: quota.group_id }
          : service ? { id: quota.id } : { device_id: quota.device_id };
        await request(path, "POST", body);
        showMessage("Quota usage reset.", "success");
        loadQuotas();
      } catch (error) {
        showMessage(error.message, "error");
      } finally {
        setButtonBusy(button, false);
      }
    }

    async function loadQuotas() {
      try {
        const [dataLimits, groupDataLimits, serviceQuotas, timeQuotas] = await Promise.all([
          request("/api/data-limits", "GET"),
          request("/api/group-data-limits", "GET"),
          request("/api/service-quotas", "GET"),
          request("/api/time-quotas", "GET")
        ]);
        renderDataLimits(dataLimits);
        renderGroupDataLimits(groupDataLimits);
        renderServiceQuotas(serviceQuotas);
        renderTimeQuotas(timeQuotas);
      } catch (error) {
        recordRead("/api/data-limits", error);
      }
    }

    async function loadServiceAssignments() {
      try {
        renderServiceAssignments(await request("/api/service-assignments", "GET"));
      } catch (error) {
        recordRead("/api/service-assignments", error);
      }
    }

    async function loadServiceLists() {
      try {
        const data = await request("/api/service-lists", "GET");
        serviceCatalogue = data.catalogue || [];
        renderServiceCatalogue();
        renderServiceLists(data);
      } catch (error) {
        renderServiceLists({ service_lists: [], error: error.message });
      }
      renderServiceListObserved();
    }

    async function scanObservations() {
      if (!helperConfigured || observationRequestInFlight) return;
      observationRequestInFlight = true;
      const scanButton = element("scan-observations");
      setButtonBusy(scanButton, true);
      scanButton.textContent = "Scanning...";
      showMessage("Sampling DNS queries from the test network...", "working");
      try {
        const result = await request("/api/observations/scan", "POST", {});
        showMessage(
          "Traffic scan complete: " + result.captured_observations + " domain observations captured.",
          "success"
        );
        loadObservations();
      } catch (error) {
        showMessage(error.message, "error");
      } finally {
        observationRequestInFlight = false;
        scanButton.disabled = !helperConfigured;
        scanButton.removeAttribute("aria-busy");
        scanButton.textContent = "Scan now";
      }
    }

    async function loadHealth() {
      const data = await request("/api/health", "GET");
      renderMode(data);
      // The system details need the admin token. The public check only has the mode.
      if (adminTokenVerified) data.system = (await request("/api/system-health", "GET")).system;
      renderSystemHealth(data);
    }

    let profilesRequestRevision = 0;

    function setDeviceProfileAvailability(message, available = false, retry = false) {
      element("device-profile").disabled = !available;
      const save = element("save-device");
      save.disabled = !available || save.getAttribute("aria-busy") === "true";
      const note = element("device-profile-note");
      note.textContent = message;
      note.hidden = !message;
      element("device-profile-refresh").hidden = !retry;
    }

    privateDataLockHooks.push(() => {
      profilesRequestRevision += 1;
      element("device-profile").replaceChildren();
      element("device-profile").value = "";
      setDeviceProfileAvailability("Check the admin token to load saved profiles.");
    });

    async function loadProfiles() {
      const revision = ++profilesRequestRevision;
      setDeviceProfileAvailability("Loading saved profiles...");
      let data;
      try {
        data = await request("/api/profiles", "GET");
      } catch (error) {
        if (revision !== profilesRequestRevision) return;
        profilesLoaded = false;
        updateGroupFormControls();
        setDeviceProfileAvailability("Could not load profiles. Choose Refresh profiles to try again.", false, true);
        throw error;
      }
      if (revision !== profilesRequestRevision) return;
      profiles = data.profiles;
      const profileLabels = profileOptionLabels(profiles);
      profilesLoaded = true;
      renderSidebarStatus();
      element("profiles-json").textContent = JSON.stringify(profiles, null, 2);
      const emptyLabels = {
        "group-profile": "Keep each device's own profile",
        "general-profile-select": "Choose a saved profile",
        "device-profile": profiles.length ? "Choose a saved profile" : "No saved profiles",
        "everyone-else-profile": "None: normal connection"
      };
      [
        "schedule-profile",
        "timer-profile",
        "progression-start-profile",
        "progression-end-profile",
        "data-limit-profile",
        "service-quota-profile",
        "service-assignment-profile",
        "service-list-profile",
        "time-quota-profile",
        "group-profile",
        "group-data-limit-profile",
        "general-profile-select",
        "device-profile",
        "everyone-else-profile"
      ].forEach((selectId) => {
        const profileSelect = element(selectId);
        const previousSelection = profileSelect.value;
        profileSelect.replaceChildren();
        if (emptyLabels[selectId]) {
          const option = document.createElement("option");
          option.value = "";
          option.textContent = emptyLabels[selectId];
          profileSelect.appendChild(option);
        }
        profiles.forEach((profile) => {
          const option = document.createElement("option");
          option.value = profile.id;
          option.textContent = profileLabels.get(profile.id);
          profileSelect.appendChild(option);
        });
        // Rebuilding the menu must not change a choice being edited.
        const known = profiles.some((profile) => profile.id === previousSelection) ||
          (previousSelection === "" && emptyLabels[selectId] !== undefined);
        if (known) profileSelect.value = previousSelection;
        else if (selectId === "group-profile" && previousSelection) {
          profileSelect.add(new Option(previousSelection + " (no longer available)", previousSelection));
          profileSelect.value = previousSelection;
        } else if (selectId === "general-profile-select" || selectId === "device-profile") profileSelect.value = "";
        if (selectId === "device-profile") {
          setDeviceProfileAvailability(!profiles.length
            ? "No saved profiles. Save a profile above first."
            : previousSelection && !profileSelect.value
              ? "The selected profile is no longer available. Choose another profile."
              : "", profiles.length > 0);
        }
      });
      updateGroupFormControls();
      profilesLoadedHooks.forEach((hook) => hook());
      updateGeneralProfileControls();
    }

    const profilesLoadedHooks = [];

    async function loadGeneralProfile() {
      const data = await request("/api/general-profile", "GET");
      const previousId = generalProfileState && generalProfileState.profile && generalProfileState.profile.id;
      generalProfileState = data;
      renderSidebarStatus();
      if (data.profile && previousId !== data.profile.id) {
        element("general-profile-select").value = data.profile.id;
      }
      const status = element("general-profile-status");
      status.textContent = !data.profile
        ? profiles.length ? "Nothing set for everyone. Saved device rules apply when profiles are on." : "Create a saved profile on the Profiles page to use this control."
        : data.active ? "Active: " + data.profile.name + ". Applies to every unprotected device."
        : data.state.observed_state === "reset" ? "Paused: " + data.profile.name + ". Choose Apply to all or Start to resume."
        : data.state.observed_state === "error" ? "Saved: " + data.profile.name + ". Network update failed. Retry Apply to all."
        : "Saved: " + data.profile.name + ". Apply to all to start.";
      updateTrafficDeviceOptions(savedDevices);
      updateGeneralProfileControls();
    }

    async function loadDevicesForSchedule() {
      let data;
      try {
        data = await request("/api/devices", "GET");
      } catch (error) {
        savedDevicesLoaded = false;
        updateGroupFormControls();
        throw error;
      }
      savedDevices = data.devices;
      savedDevicesLoaded = true;
      renderApplySummary();
      updateGroupMemberOptions();
      [
        "timer-device",
        "progression-device",
        "data-limit-device",
        "service-quota-device",
        "service-assignment-device",
        "time-quota-device"
      ].forEach((selectId) => {
        const deviceSelect = element(selectId);
        const selected = new Set(Array.from(deviceSelect.selectedOptions || [], (option) => option.value));
        deviceSelect.replaceChildren();
        data.devices.forEach((device) => {
          const option = document.createElement("option");
          option.value = device.id;
          option.textContent = devicePickerLabel(device);
          deviceSelect.appendChild(option);
          if (selected.size || deviceSelect.multiple) option.selected = selected.has(device.id);
        });
      });
      savedDevicesHooks.forEach((hook) => hook(data.devices));
      updateTrafficDeviceOptions(data.devices);
      populateServiceQuotaDomains();
      populateTimeQuotaDomains();
    }

    const savedDevicesHooks = [];

    async function loadGroups() {
      try {
        const data = await request("/api/groups", "GET");
        renderGroups(data);
        groupsLoadedHooks.forEach((hook) => hook());
        if (lastConnectedData && !element("connected-devices").contains(document.activeElement)) renderConnectedDevices(lastConnectedData);
        const groupSelect = element("group-data-limit-group");
        const selectedGroup = groupSelect.value;
        groupSelect.replaceChildren();
        groups.forEach((group) => {
          const option = document.createElement("option");
          option.value = group.id;
          option.textContent = group.name + " (" + group.device_ids.length +
            (group.device_ids.length === 1 ? " device)" : " devices)");
          option.selected = group.id === selectedGroup;
          groupSelect.appendChild(option);
        });
      } catch (error) {
        renderGroups({ groups: [], error: error.message });
        element("group-data-limit-group").replaceChildren();
      }
    }

    const groupsLoadedHooks = [];

    function renderSchedules(schedules) {
      const container = element("schedule-list");
      container.replaceChildren();
      if (!schedules.length) {
        container.textContent = "No schedules yet.";
        return;
      }
      schedules.forEach((schedule) => {
        const row = document.createElement("div");
        row.className = "schedule-row";
        row.dataset.key = schedule.id;
        const text = document.createElement("div");
        const name = document.createElement("span");
        name.textContent =
          scheduleTargetName(schedule) + " → " + profileName(schedule.profile_id);
        const time = document.createElement("small");
        time.textContent = formatDays(schedule.days_of_week) + " · " +
          formatMinute(schedule.start_minute) + "–" + formatMinute(schedule.end_minute) +
          " · " + schedule.id + " · " + (schedule.enabled ? "Enabled" : "Disabled");
        text.append(name, time);
        const actions = document.createElement("div");
        actions.className = "row-actions";
        const editButton = document.createElement("button");
        editButton.type = "button";
        editButton.textContent = "Edit";
        editButton.addEventListener("click", () => editSchedule(schedule));
        const deleteButton = document.createElement("button");
        deleteButton.type = "button";
        deleteButton.textContent = "Delete";
        deleteButton.addEventListener("click", () => deleteItem(
          "/api/schedules/delete", { id: schedule.id }, "Schedule deleted.", deleteButton
        ));
        actions.append(editButton, deleteButton);
        row.append(text, actions);
        container.appendChild(row);
      });
    }

    async function loadSchedules() {
      const data = await request("/api/schedules", "GET");
      savedSchedules = data.schedules;
      element("schedule-clock").textContent = "Schedule times use the Pi clock: " +
        data.local_time.replace("T", " ") + " (" + data.time_zone + ").";
      renderSchedules(data.schedules);
      // An everyone-else schedule adds Everyone else to Live traffic.
      updateTrafficDeviceOptions(devices);
    }

    function formatTimerRemaining(seconds) {
      const minutes = Math.floor(seconds / 60);
      const remainingSeconds = seconds % 60;
      if (minutes === 0) return remainingSeconds + (remainingSeconds === 1 ? " second" : " seconds");
      if (remainingSeconds === 0) return minutes + (minutes === 1 ? " minute" : " minutes");
      return minutes + "m " + remainingSeconds + "s";
    }

    function renderTimers(data) {
      const container = element("timer-list");
      const count = element("timer-count");
      const timers = data.timers || [];
      container.replaceChildren();
      count.textContent = timers.length + (timers.length === 1 ? " active timer" : " active timers");
      if (timers.length === 0) {
        container.textContent = "No active timers.";
        return;
      }
      timers.forEach((timer) => {
        const row = document.createElement("div");
        row.className = "timer-row";
        row.dataset.key = timer.id;
        const details = document.createElement("div");
        details.className = "timer-details";
        const title = document.createElement("strong");
        title.textContent = timer.device_name + " → " + timer.profile_name;
        const remaining = document.createElement("span");
        remaining.textContent = formatTimerRemaining(timer.remaining_seconds) + " remaining";
        details.append(title, remaining);
        const cancelButton = document.createElement("button");
        cancelButton.type = "button";
        cancelButton.textContent = "Cancel";
        cancelButton.addEventListener("click", () => cancelTimer(timer.id, cancelButton));
        row.append(details, cancelButton);
        container.appendChild(row);
      });
    }

    async function loadTimers() {
      try {
        renderTimers(await request("/api/timers", "GET"));
      } catch (error) {
        recordRead("/api/timers", error);
      }
    }

    async function cancelTimer(timerId, button) {
      setButtonBusy(button, true);
      showMessage("Cancelling the timer...", "working");
      try {
        const result = await request("/api/timers/cancel", "POST", { id: timerId });
        refreshAfterSuccess(
          result.live_applied ? "Timer cancelled and the normal profile is active." : "Timer cancelled.",
        );
      } catch (error) {
        showMessage(error.message, "error");
      } finally {
        setButtonBusy(button, false);
      }
    }

    function renderProgressions(data) {
      const container = element("progression-list");
      const count = element("progression-count");
      const progressions = data.progressions || [];
      container.replaceChildren();
      count.textContent = progressions.length + (
        progressions.length === 1 ? " active progression" : " active progressions"
      );
      if (progressions.length === 0) {
        container.textContent = "No active progressions.";
        return;
      }
      progressions.forEach((progression) => {
        const row = document.createElement("div");
        row.className = "progression-row";
        row.dataset.key = progression.id;
        const heading = document.createElement("div");
        heading.className = "progression-row-heading";
        const title = document.createElement("strong");
        title.textContent = progression.device_name + " · " +
          progression.start_profile_name + " → " + progression.end_profile_name;
        const status = document.createElement("span");
        const signal = progression.mode === "bytes"
          ? formatQuotaBytes(progression.traffic_bytes) + " / " + formatQuotaBytes(progression.traffic_target_bytes)
          : progression.mode === "traffic"
            ? progression.traffic_queries + " DNS queries"
            : "time based";
        status.textContent = progression.progress_percent + "% · " + signal + " · " +
          formatTimerRemaining(progression.remaining_seconds) + " left";
        heading.append(title, status);
        const meter = document.createElement("div");
        meter.className = "progression-meter";
        const meterValue = document.createElement("span");
        meterValue.style.width = progression.progress_percent + "%";
        meter.appendChild(meterValue);
        const stopButton = document.createElement("button");
        stopButton.type = "button";
        stopButton.textContent = "Stop and recover";
        stopButton.addEventListener("click", () => stopProgression(progression.id, stopButton));
        row.append(heading, meter, stopButton);
        container.appendChild(row);
      });
    }

    async function loadProgressions() {
      try {
        renderProgressions(await request("/api/progressions", "GET"));
      } catch (error) {
        recordRead("/api/progressions", error);
      }
    }

    async function stopProgression(progressionId, button) {
      setButtonBusy(button, true);
      showMessage("Stopping the progression and restoring the normal profile...", "working");
      try {
        const result = await request("/api/progressions/stop", "POST", { id: progressionId });
        refreshAfterSuccess(
          result.live_applied
            ? "Progression stopped and the normal profile is active."
            : "Progression stopped."
        );
      } catch (error) {
        showMessage(error.message, "error");
      } finally {
        setButtonBusy(button, false);
      }
    }

    function syncProgressionMode() {
      const trafficMode = element("progression-mode").value === "traffic";
      const bytesMode = element("progression-mode").value === "bytes";
      element("progression-traffic-field").hidden = !trafficMode;
      element("progression-bytes-field").hidden = !bytesMode;
      element("progression-traffic-target").required = trafficMode;
      element("progression-bytes-target").required = bytesMode;
      // A disabled field is skipped when the browser checks the form, so a hidden target that is
      // out of range cannot stop the save without a word.
      element("progression-traffic-target").disabled = !trafficMode;
      element("progression-bytes-target").disabled = !bytesMode;
    }

    async function loadPolicies() {
      const data = await request("/api/policies", "GET");
      element("policies").textContent = JSON.stringify(data, null, 2);
      if (!formDrafts.has("policy-form")) {
      element("mac-mode").value = data.mac.mode;
      element("mac-allowlist").value = data.mac.allowlist.join(", ");
      element("mac-denylist").value = data.mac.denylist.join(", ");
      element("website-mode").value = data.websites.mode;
      element("website-allowlist").value = data.websites.allowed_domains.join(", ");
      element("website-blocklist").value = data.websites.blocked_domains.join(", ");
      }
      accessRulesSaved = data.websites.mode === "allow_only" ||
        data.websites.blocked_domains.length > 0 || data.mac.mode !== "disabled";
      renderApplySummary();
      updateAccessForms(data);
      await loadAccessStatus();
    }

    async function loadState() {
      const revision = networkActionRevision;
      const data = await request("/api/state", "GET");
      if (revision !== networkActionRevision) return;
      element("state").textContent = JSON.stringify(data.state, null, 2);
      renderRuntimeSummary(data.state);
      runtimeStateHooks.forEach((hook) => hook(data.state));
    }

    const runtimeStateHooks = [];

    function formatUtcDay(timestamp) {
      return new Date(timestamp * 1000).toLocaleDateString(undefined, {
        day: "numeric",
        month: "short",
        year: "numeric",
        timeZone: "UTC"
      });
    }

    function renderRandomWebsiteAnalytics(data) {
      const rows = element("random-website-rows");
      rows.replaceChildren();
      const deviceNames = new Map((data.rows || []).map((item) =>
        [item.device_id, {id: item.device_id, name: item.display_name || item.device_name}]));
      const deviceLabels = profileOptionLabels(Array.from(deviceNames.values()));
      (data.rows || []).forEach((item) => {
        const row = document.createElement("tr");
        const device = document.createElement("td");
        device.textContent = deviceLabels.get(item.device_id);
        const domain = document.createElement("td");
        domain.className = "analytics-domain";
        domain.textContent = item.domain;
        const kept = document.createElement("td");
        kept.textContent = Number(item.kept_count).toLocaleString();
        const removed = document.createElement("td");
        removed.textContent = Number(item.removed_count).toLocaleString();
        const activeDays = document.createElement("td");
        activeDays.textContent = Number(item.days_with_events).toLocaleString();
        row.append(device, domain, kept, removed, activeDays);
        rows.appendChild(row);
      });
      const status = element("random-website-status");
      const oldestDay = data.oldest_available_day_utc;
      const history = oldestDay === null
        ? "No history has been saved yet."
        : "Oldest saved result: " + formatUtcDay(oldestDay) + " UTC. Older history may have expired or been trimmed.";
      status.className = "analytics-status";
      if (data.truncated) {
        status.textContent = "Showing the 5,000 highest-count device and website totals. Choose one device or a shorter range. " + history;
      } else if (!data.rows.length) {
        status.textContent = "No scheduled randomized website choices were recorded in this range. " + history;
      } else {
        status.textContent = data.rows.length + " device and website totals. " + history;
      }
    }

    function clearAdminToolsUi() {
      randomWebsiteAnalyticsRequestVersion += 1;
      element("random-website-rows").replaceChildren();
      const devices = element("random-website-device");
      devices.replaceChildren();
      const allDevices = document.createElement("option");
      allDevices.value = "";
      allDevices.textContent = "All devices";
      devices.appendChild(allDevices);
      element("random-website-status").className = "analytics-status";
    }

    privateDataLockHooks.push(clearAdminToolsUi);

    async function loadRandomWebsiteAnalytics() {
      const revision = adminTokenRevision;
      const generation = adminToolsGeneration;
      const current = () => revision === adminTokenRevision && generation === adminToolsGeneration &&
        (adminTokenVerified || adminTokenChecking);
      const status = element("random-website-status");
      const rows = element("random-website-rows");
      if (!element("admin-token").value.trim()) {
        randomWebsiteAnalyticsRequestVersion += 1;
        rows.replaceChildren();
        status.className = "analytics-status";
        status.textContent = "Enter the admin token to view randomized website choices.";
        return;
      }
      const requestVersion = ++randomWebsiteAnalyticsRequestVersion;
      status.className = "analytics-status";
      status.textContent = "Loading randomized website choices...";
      try {
        const deviceData = await request("/api/devices", "GET");
        if (!current()) return {ok:false};
        const deviceSelect = element("random-website-device");
        const selectedDevice = deviceSelect.value;
        deviceSelect.replaceChildren();
        const allDevices = document.createElement("option");
        allDevices.value = "";
        allDevices.textContent = "All devices";
        deviceSelect.appendChild(allDevices);
        const deviceLabels = profileOptionLabels((deviceData.devices || []).map((device) =>
          ({id: device.id, name: deviceDisplayName(device)})));
        (deviceData.devices || []).forEach((device) => {
          const option = document.createElement("option");
          option.value = device.id;
          option.textContent = deviceLabels.get(device.id);
          option.selected = device.id === selectedDevice;
          deviceSelect.appendChild(option);
        });
        const parameters = new URLSearchParams({
          days: element("random-website-days").value,
          device_id: deviceSelect.value
        });
        const data = await request("/api/random-website-analytics?" + parameters.toString(), "GET");
        if (current() && requestVersion === randomWebsiteAnalyticsRequestVersion) {
          renderRandomWebsiteAnalytics(data);
          return {ok:true};
        }
      } catch (error) {
        if (!current() || requestVersion !== randomWebsiteAnalyticsRequestVersion) return {ok:false};
        rows.replaceChildren();
        status.className = "analytics-status error";
        status.textContent = "Could not load randomized website history: " + error.message;
        return {ok:false};
      }
    }

    let adminToolsPhysicalRead = null;

    async function loadAdminTools() {
      const revision = adminTokenRevision;
      const generation = adminToolsGeneration;
      const active = adminToolsPhysicalRead;
      if (active) {
        if (active.revision === revision && active.generation === generation) return active.promise;
        return active.promise.catch(() => null).then(() => {
          if (revision !== adminTokenRevision || generation !== adminToolsGeneration) return {ok:false,stale:true};
          return loadAdminTools();
        });
      }
      const owner = {revision, generation, promise:null};
      owner.promise = Promise.resolve().then(() => fetchAdminTools(owner)).finally(() => {
        if (adminToolsPhysicalRead === owner) adminToolsPhysicalRead = null;
      });
      adminToolsPhysicalRead = owner;
      return owner.promise;
    }

    async function fetchAdminTools(owner) {
      const {revision, generation} = owner;
      const current = () => revision === adminTokenRevision && generation === adminToolsGeneration &&
        (adminTokenVerified || adminTokenChecking);
      if (!current()) return {ok:false,stale:true};
      if (!element("admin-token").value.trim()) {
        element("diagnostics-json").textContent = "Enter the admin token to inspect diagnostics.";
        element("action-history").textContent = "Enter the admin token to view recent actions.";
        randomWebsiteAnalyticsRequestVersion += 1;
        element("random-website-rows").replaceChildren();
        element("random-website-status").textContent = "Enter the admin token to view randomized website choices.";
        element("random-website-status").className = "analytics-status";
        return { ok: false, message: "Enter the admin token first." };
      }
      try {
        const data = await request("/api/diagnostics", "GET");
        if (!current()) return {ok:false,stale:true};
        element("diagnostics-json").textContent = JSON.stringify(data, null, 2);
      } catch (error) {
        if (!current()) return {ok:false,stale:true};
        element("diagnostics-json").textContent = error.message;
        element("action-history").textContent = error.message;
        randomWebsiteAnalyticsRequestVersion += 1;
        element("random-website-rows").replaceChildren();
        element("random-website-status").textContent = error.message;
        element("random-website-status").className = "analytics-status error";
        return { ok: false, message: error.message, status: error.status,
                 connectionLost: typeof isConnectionLost === "function" && isConnectionLost(error) };
      }
      let actionHistoryLoaded = true;
      try {
        const data = await request("/api/action-history", "GET");
        if (!current()) return {ok:false,stale:true};
        element("action-history").textContent = data.actions.length
          ? data.actions.map((action) => new Date(action.happened_at * 1000).toLocaleString() +
              " — " + action.action + " — " + action.status_code).join("\n")
          : "No admin actions have been recorded.";
      } catch (error) {
        if (!current()) return {ok:false,stale:true};
        element("action-history").textContent = error.message;
        actionHistoryLoaded = false;
      }
      const analytics = await loadRandomWebsiteAnalytics();
      if (!current()) return {ok:false,stale:true};
      return {
        ok: true,
        refreshComplete: actionHistoryLoaded && Boolean(analytics && analytics.ok),
        message: actionHistoryLoaded
          ? "Token accepted. Diagnostics loaded."
          : "Token accepted. Diagnostics loaded, but action history could not be loaded."
      };
    }

    async function checkAdminToken() {
      const button = element("check-admin-token");
      const status = element("token-check-status");
      const tokenRevision = adminTokenRevision;
      setDashboardUnlocked(false);
      element("allow-dashboard-device").disabled = true;
      if (!element("admin-token").value.trim()) {
        adminTokenVerified = false;
        setPrivateDataPanels("Enter the admin token and choose Check token to load.");
        status.textContent = "Enter the admin token first.";
        status.className = "token-check-status error";
        showMessage("Enter the admin token first.", "error");
        updateCredentialStatus();
        openCredentials("admin");
        return;
      }
      adminTokenChecking = true;
      adminTokenRejected = false;
      adminTokenRefused = false;
      adminTokenVerified = false;
      updateCredentialStatus();
      setPrivateDataPanels("Checking the admin token...", "Checking...");
      button.disabled = true;
      button.setAttribute("aria-busy", "true");
      button.textContent = "Checking...";
      status.textContent = "Checking the token and loading diagnostics...";
      showMessage("Checking the admin token...", "working");
      const checkingMessageSerial = messageSerial;
      try {
        const access = await loadDashboardAccess();
        if (tokenRevision !== adminTokenRevision) return;
        if (!access.allowed) {
          adminTokenVerified = false;
          setPrivateDataPanels("Allow this device to use dashboard controls.");
          status.textContent = access.can_register ? "Token accepted. Choose Allow this device."
            : "Token accepted but device verification is pending. Wait a few seconds and check again.";
          status.className = "token-check-status success";
          // The status line is not read out. The toast says it and replaces the working message.
          showMessage(status.textContent, "info");
          return;
        }
        const result = await loadAdminTools();
        if (tokenRevision !== adminTokenRevision) return;
        if (result.connectionLost) throw lostConnectionError(new Error(result.message));
        if (result.ok) {
          adminTokenVerified = true;
          // Remember an accepted token now. The refresh below can take seconds and a
          // reload in between would otherwise forget it.
          updateCredentialStatus();
          if (typeof loadTrustDetails === "function") loadTrustDetails();
          await refresh();
          if (tokenRevision !== adminTokenRevision) return;
          if (!adminTokenVerified) {
            // A read during the refresh found this device's dashboard access gone.
            const locked = new Error(element("dashboard-access-status").textContent ||
              "Allow this device to use the dashboard.");
            locked.status = 403;
            throw locked;
          }
          if (typeof loadUpdateStateAfterSignIn === "function") await loadUpdateStateAfterSignIn();
          if (tokenRevision !== adminTokenRevision || !adminTokenVerified) return;
          await loadTrafficSettings();
          if (tokenRevision !== adminTokenRevision) return;
          await startTrafficSampling();
          if (tokenRevision !== adminTokenRevision) return;
          if (typeof syncImpact === "function") syncImpact();
          if (typeof loadBrand === "function") loadBrand();
          if (typeof resumeWifiChangeNotice === "function") resumeWifiChangeNotice();
          if (typeof scheduleProfileRangePreview === "function") scheduleProfileRangePreview();
        } else {
          adminTokenVerified = false;
          adminTokenRejected = true;
          adminTokenRefused = result.status === 401;
          setPrivateDataPanels("Token check failed: " + result.message);
        }
        status.textContent = result.ok ? result.message : "Token check failed: " + result.message;
        status.className = "token-check-status " + (result.ok ? "success" : "error");
        // The dashboard unlocks while the check is still loading, so the person can save
        // something before it ends. A success text must not replace that newer message.
        // The token status above always shows the result, and a failure always shows.
        if (!result.ok || messageSerial === checkingMessageSerial) {
          showMessage(status.textContent, result.ok ? "success" : "error");
        }
      } catch (error) {
        if (tokenRevision !== adminTokenRevision) return;
        adminTokenVerified = false;
        // A dropped connection says nothing about the token. It is kept and checked again
        // as soon as the Pi answers, so a Wi-Fi restart never looks like a sign-out.
        const connectionLost = typeof isConnectionLost === "function" && isConnectionLost(error);
        const helperBusy = error.code === "helper-busy";
        adminTokenRejected = !connectionLost && !helperBusy;
        adminTokenRefused = error.status === 401;
        setDashboardUnlocked(false);
        element("allow-dashboard-device").disabled = true;
        setPrivateDataPanels(connectionLost || helperBusy ? error.message : "Token check failed: " + error.message);
        status.textContent = connectionLost ? error.message + " Your token is kept and checked again when the Pi answers."
          : helperBusy ? "Device verification is busy. Your token is kept. Choose Check token to try again."
          : "Token check failed: " + error.message;
        status.className = "token-check-status " + (helperBusy ? "" : "error");
        showMessage(status.textContent, connectionLost || helperBusy ? "info" : "error");
        if (connectionLost && typeof recheckTokenWhenPiAnswers === "function") recheckTokenWhenPiAnswers();
      } finally {
        if (tokenRevision !== adminTokenRevision) return;
        button.disabled = false;
        button.removeAttribute("aria-busy");
        button.textContent = "Check token";
        adminTokenChecking = false;
        updateCredentialStatus();
      }
    }

    // Which parts the next refresh reads: undefined for none waiting, null for everything.
    let refreshWanted;

    async function refresh(only) {
      invalidateAdminTools();
      const scope = only === undefined ? null : only;
      refreshWanted = refreshWanted === undefined || refreshWanted === null || scope === null
        ? (refreshWanted === undefined ? scope : null)
        : new Set([...refreshWanted, ...scope]);
      refreshAgain = true;
      if (dashboardRefresh) return dashboardRefresh;
      dashboardRefresh = (async () => {
        if (dashboardPoll) await dashboardPoll;
        do {
          refreshAgain = false;
          const wanted = refreshWanted;
          refreshWanted = undefined;
          try {
            await refreshDashboard(wanted);
          } catch (error) {
            refreshWanted = refreshWanted === undefined ? wanted
              : refreshWanted === null || wanted === null ? null
              : new Set([...refreshWanted, ...wanted]);
            throw error;
          } finally { restoreDrafts(); }
        } while (refreshAgain);
      })();
      try {
        await dashboardRefresh;
      } finally {
        dashboardRefresh = null;
      }
    }

    // Panic can start while another change is still running, so every running action is
    // remembered by its path and the page is busy until the last one finishes.
    const runningActions = new Set();
    let actionIntentRevision = 0;

    function setActionBusy(busy, path) {
      if (busy) runningActions.add(path);
      else runningActions.delete(path);
      busy = runningActions.size > 0;
      networkActionBusy = busy;
      if (busy) networkActionRevision += 1;
      const applyButton = element("apply-button");
      const resetButton = element("reset-button");
      applyButton.disabled = busy || !helperConfigured;
      resetButton.disabled = busy || !helperConfigured;
      applyBusy = runningActions.has("/api/apply");
      applyButton.setAttribute("aria-busy", String(applyBusy));
      resetButton.setAttribute("aria-busy", String(runningActions.has("/api/reset")));
      applyButton.textContent = applyBusy ? "Applying…" : currentApplyLabel();
      resetButton.textContent = runningActions.has("/api/reset") ? "Resetting..." : "Reset network";
      const panicButton = element("panic-button");
      const panicking = runningActions.has("/api/normal-access");
      // Panic stays available: it is the way out when a change goes wrong.
      panicButton.disabled = !helperConfigured;
      panicButton.setAttribute("aria-busy", String(panicking));
      element("panic-state").textContent = panicking ? "Working..." : "Turn profiles and blocking off";
      updateGeneralProfileControls();
      const generalBusy = Array.from(runningActions).some((running) => running.startsWith("/api/general-profile"));
      ["general-profile-apply", "general-profile-stop"].forEach((id) => {
        element(id).setAttribute("aria-busy", String(generalBusy));
      });
    }

    const PANIC_QUEUED = "Panic is sent now. It runs as soon as the Pi can take it";

    const PANIC_RETRYING = "The Pi has not taken Panic yet, trying again";

    // Panic has a reserved request worker but still depends on helper availability.
    // Retry at most three times after an unknown timeout or a temporary busy reply.
    // Repeating Panic releases the network again. Never repeat an ordinary save here.
    const PANIC_RETRY_STATUSES = [429, 503];

    const PANIC_ATTEMPTS = 3;

    // The dashboard answers Panic within 48 seconds: the device check, the helper and
    // the saves (docs/architecture.md, "Panic time budget"). Wait at least that long.
    const PANIC_REQUEST_TIMEOUT_MS = 50000;

    async function requestPanic(path) {
      const revision = adminTokenRevision;
      for (let attempt = 1; ; attempt += 1) {
        if (revision !== adminTokenRevision || !adminTokenVerified) throw new Error("Panic sign-in changed. Check token again.");
        try {
          return await request(path, "POST", {}, {timeoutMs: PANIC_REQUEST_TIMEOUT_MS});
        } catch (error) {
          const again = error.timeout === true || PANIC_RETRY_STATUSES.includes(error.status);
          if (!again || attempt >= PANIC_ATTEMPTS) throw error;
          showMessage(PANIC_RETRYING, "working");
          await new Promise((resolve) => window.setTimeout(resolve, 1000));
        }
      }
    }

    async function runAction(path, successMessage, workingMessage) {
      const panic = path === "/api/normal-access";
      if (runningActions.has(path) || (networkActionBusy && !panic)) return;
      if (!helperConfigured) {
        showMessage("Network actions are unavailable in configuration-only mode.", "error");
        return;
      }
      const revision = adminTokenRevision;
      const intent = ++actionIntentRevision;
      const current = () => revision === adminTokenRevision && adminTokenVerified && intent === actionIntentRevision;
      const wasRunning = !!lastRuntimeState && lastRuntimeState.running === true;
      const queued = panic && networkActionBusy;
      setActionBusy(true, path);
      showMessage(queued ? PANIC_QUEUED : workingMessage, "working");
      try {
        const data = panic ? await requestPanic(path) : await request(path, "POST", {});
        if (!current()) return;
        if (data.state) renderRuntimeSummary(data.state);
        const done = typeof successMessage === "function"
          ? successMessage(data, wasRunning) : successMessage;
        refreshAfterSuccess(done + (data.warning ? " " + data.warning : ""));
      } catch (error) {
        if (!current()) return;
        showMessage(error.message, "error");
        // A failed action can still have changed the state so the page reads it again.
        loadPrivateDataPanel("state", loadState);
      } finally {
        if (revision === adminTokenRevision && adminTokenVerified) setActionBusy(false, path);
        else { runningActions.delete(path); networkActionBusy = runningActions.size > 0; }
      }
    }

    function appliedMessage(data, wasRunning) {
      const applied = data.applied;
      if (!applied) {
        return "Started with your saved settings.";
      }
      return startedText({
        devices: applied.devices,
        unverifiedDevices: Array.isArray(data.unverified_device_ids)
          ? data.unverified_device_ids.length : 0,
        groups: applied.groups,
        serviceLists: applied.service_lists,
        protected: applied.protected,
        generalProfileName: applied.general_profile,
        accessRules: applied.access_rules === true
      }, wasRunning);
    }

    element("apply-button").addEventListener("click", () => {
      runAction("/api/apply", appliedMessage, "Applying your saved settings…");
    });

    element("reset-button").addEventListener("click", () => {
      runAction("/api/reset", "Network reset successfully.", "Resetting network...");
    });

    // Panic is the full way back: it stops the profiles like Reset and turns
    // household blocking off as well. Only the drop that keeps the private resolver
    // port closed stays. Saved settings stay.
    element("panic-button").addEventListener("click", () => {
      runAction(
        "/api/normal-access",
        "All profiles and household blocking are off. Choose Start to run the profiles again. Turn blocking back on under Access rules.",
        "Turning everything off..."
      );
    });

    async function runGeneralProfileAction(stop, rowProfileId) {
      if (networkActionBusy || !helperConfigured) return;
      const profileId = rowProfileId || element("general-profile-select").value;
      if (!stop && !profileId) return;
      const path = stop ? "/api/general-profile/stop" : "/api/general-profile";
      const revision = adminTokenRevision;
      const intent = ++actionIntentRevision;
      const current = () => revision === adminTokenRevision && adminTokenVerified && intent === actionIntentRevision;
      savedProfileBusy = rowProfileId ? { id: rowProfileId, stop: stop } : null;
      setActionBusy(true, path);
      showMessage(stop ? "Stopping the profile for everyone..." : "Applying the profile to all unprotected devices...", "working");
      try {
        const result = await request(path, "POST", stop ? {} : {profile_id: profileId});
        if (!current()) return;
        // While the network is stopped, Stop only saves the removal and says so.
        refreshAfterSuccess(stop
          ? (result && result.live_applied === false && result.message
              ? result.message
              : "Stopped the profile for everyone. Saved device rules resumed.")
          : "Profile applied for everyone. Protected devices keep normal access.");
      } catch (error) {
        if (!current()) return;
        showMessage(error.message, "error");
      } finally {
        if (current()) await loadPrivateDataPanel("general-profile-status", loadGeneralProfile);
        if (revision === adminTokenRevision && adminTokenVerified) {
          savedProfileBusy = null;
          setActionBusy(false, path);
        } else { runningActions.delete(path); networkActionBusy = runningActions.size > 0; }
      }
    }

    element("general-profile-select").addEventListener("change", updateGeneralProfileControls);

    element("general-profile-apply").addEventListener("click", () => runGeneralProfileAction(false));

    element("general-profile-stop").addEventListener("click", () => runGeneralProfileAction(true));

    element("scan-observations").addEventListener("click", scanObservations);

    // Active profile control in the sidebar: it names the general profile above the Start button
    // and applies or stops one with the same requests and messages as the saved profiles list.
    // The owner's choice stays until the saved general profile changes, here or elsewhere.
    // Starting or pausing the same saved profile never replaces a pending choice.
    let activeProfilePick = null;
    let activeProfileSeen = null;

    function activeProfileView(input) {
      const list = input.profiles || [];
      const state = input.generalState || null;
      const general = state && state.profile ? state.profile : null;
      const known = (id) => list.some((profile) => profile.id === id);
      const activeId = general && state.active === true ? general.id : "";
      const savedId = general && known(general.id) ? general.id : "";
      const pick = input.pick;
      // Nothing private stays in the page while the dashboard is locked.
      const shown = input.adminTokenVerified ? list : [];
      const value = !input.adminTokenVerified ? ""
        : typeof pick === "string" && (pick === "" || known(pick)) ? pick : savedId;
      const stop = value !== "" && value === activeId;
      const reason = !input.adminTokenVerified ? "Check the admin token first."
        : !input.helperConfigured ? "Needs the Pi helper to run."
        : !list.length ? "No saved profiles yet." : "";
      return {
        options: [{value: "", label: "None selected"}].concat(
          shown.map((profile) => ({value: profile.id, label: profile.name || profile.id}))),
        value: value,
        blocked: reason !== "",
        busy: input.busy === true,
        button: {
          label: stop ? "Stop" : "Apply to all devices",
          stop: stop,
          disabled: reason !== "" || (!stop && value === "")
        },
        note: reason || "Protected devices stay untouched.",
        profilesLink: reason === "No saved profiles yet."
      };
    }

    function renderActiveProfileControl() {
      const state = generalProfileState;
      const seen = state && state.profile ? state.profile.id : "";
      if (seen !== activeProfileSeen) {
        activeProfileSeen = seen;
        activeProfilePick = null;
      }
      const view = activeProfileView({
        adminTokenVerified: adminTokenVerified, helperConfigured: helperConfigured,
        busy: networkActionBusy, profiles: profiles, generalState: state, pick: activeProfilePick
      });
      const select = element("active-profile-select");
      const signature = JSON.stringify(view.options);
      if (select.dataset.signature !== signature) {
        select.dataset.signature = signature;
        select.replaceChildren(...view.options.map((option) => {
          const node = document.createElement("option");
          node.value = option.value;
          node.textContent = option.label;
          return node;
        }));
      }
      select.value = view.value;
      select.disabled = view.blocked || view.busy;
      const button = element("active-profile-button");
      button.textContent = view.button.label;
      button.className = view.button.stop ? "" : "primary";
      button.dataset.stop = String(view.button.stop);
      button.disabled = view.button.disabled;
      // While an action runs the button stays focusable so that keyboard focus survives.
      if (view.busy && !view.button.disabled) button.setAttribute("aria-disabled", "true");
      else button.removeAttribute("aria-disabled");
      const running = view.busy && savedProfileBusy && savedProfileBusy.id === view.value &&
        savedProfileBusy.stop === view.button.stop;
      button.setAttribute("aria-busy", String(Boolean(running)));
      const note = element("active-profile-note");
      if (note.textContent !== view.note) note.textContent = view.note;
      // The link is its own line under the note so that the two never overlap.
      element("active-profile-link").hidden = !view.profilesLink;
    }

    // Set here and not in the markup so that the sidebar links stay the nine page links.
    element("active-profile-link").href = "#profiles";
    element("active-profile-link").addEventListener("click", (event) => followTabLink(event, "profiles"));

    element("active-profile-select").addEventListener("change", (event) => {
      activeProfilePick = event.target.value;
      renderActiveProfileControl();
    });

    element("active-profile-button").addEventListener("click", () => {
      const button = element("active-profile-button");
      if (button.disabled || button.getAttribute("aria-disabled") === "true") return;
      const stop = button.dataset.stop === "true";
      const profileId = element("active-profile-select").value;
      if (!stop && !profileId) return;
      runGeneralProfileAction(stop, profileId);
    });
    // end of the active profile control

    function profileSummary(profile) {
      const upload = profileDirectionSummary(profile.upload);
      const download = profileDirectionSummary(profile.download);
      const parts = [upload === download ? "↕ " + upload : "↑ " + upload + " · ↓ " + download];
      if (profile.homepage_bounce_percent > 0) parts.push("homepage bounce " + profile.homepage_bounce_percent + "%");
      if (profile.random_mode === "fresh") parts.push("fresh pattern");
      if (profile.outage_probability > 0) parts.push("outages " + profile.outage_probability + "%");
      if (profile.website_mode === "allow_only") {
        const sites = profile.website_domains.length;
        parts.push("allow-only " + sites + (sites === 1 ? " site" : " sites"));
      }
      return parts.join(" · ");
    }

    function profileDirectionSummary(direction) {
      const parts = [];
      if (direction.delay_ms > 0 || direction.jitter_ms > 0) {
        parts.push(direction.delay_ms + " ms" + (direction.jitter_ms > 0 ? " ±" + direction.jitter_ms : ""));
      }
      if (direction.loss_percent > 0) parts.push(direction.loss_percent + "% loss");
      if (direction.bandwidth_kbit) {
        parts.push(direction.bandwidth_kbit >= 1000
          ? String(Math.round(direction.bandwidth_kbit / 10) / 100) + " Mbit/s"
          : direction.bandwidth_kbit + " kbit/s");
      }
      if (direction.reorder_percent > 0) parts.push("reorder " + direction.reorder_percent + "%");
      if (direction.duplicate_percent > 0) parts.push("duplicate " + direction.duplicate_percent + "%");
      if (direction.corrupt_percent > 0) parts.push("corrupt " + direction.corrupt_percent + "%");
      return parts.length ? parts.join(" · ") : "no impairment";
    }

    function fillProfileForm(profile) {
      const set = (id, value) => {
        element(id).value = value === null || value === undefined ? "" : String(value);
      };
      set("profile-id", profile.id);
      set("profile-name", profile.name);
      set("profile-seed", profile.random_seed);
      set("profile-random-mode", profile.random_mode || "repeatable");
      set("profile-reroll", profile.reroll_interval_seconds);
      set("profile-reroll-variation", profile.reroll_interval_randomness.variation_percent);
      set("profile-homepage-chance", profile.homepage_bounce_percent || 0);
      set("profile-outage-probability", profile.outage_probability);
      set("profile-outage-duration", profile.outage_duration_seconds);
      set("profile-outage-interval", profile.outage_interval_seconds);
      set("profile-website-mode", profile.website_mode);
      set("profile-website-domains", profile.website_domains.join(", "));
      set("profile-website-enabled", profile.website_randomness.enabled_probability);
      ["upload", "download"].forEach((prefix) => {
        const direction = profile[prefix];
        set(prefix + "-delay", direction.delay_ms);
        set(prefix + "-jitter", direction.jitter_ms);
        set(prefix + "-loss", direction.loss_percent);
        set(prefix + "-loss-burst", direction.loss_burst_packets || 0);
        set(prefix + "-bandwidth", direction.bandwidth_kbit);
        set(prefix + "-reorder", direction.reorder_percent);
        set(prefix + "-duplicate", direction.duplicate_percent);
        set(prefix + "-corrupt", direction.corrupt_percent);
        ["delay", "jitter", "loss", "bandwidth", "reorder", "duplicate", "corrupt"].forEach((feature) => {
          const randomness = direction[feature + "_randomness"];
          set(prefix + "-" + feature + "-random-variation", randomness.variation_percent);
          set(prefix + "-" + feature + "-random-enabled", randomness.enabled_probability);
          set(prefix + "-" + feature + "-random-mode", randomness.mode || "relative");
          set(prefix + "-" + feature + "-random-minimum", randomness.minimum);
          set(prefix + "-" + feature + "-random-maximum", randomness.maximum);
        });
      });
      if (typeof scheduleProfileRangePreview === "function") scheduleProfileRangePreview();
    }

    let patternHistoryRequest = 0;
    privateDataLockHooks.push(() => {
      patternHistoryRequest += 1;
      element("pattern-history-body").replaceChildren();
      element("pattern-history-status").textContent = "";
      element("load-pattern-history").disabled = true;
    });

    element("load-pattern-history").addEventListener("click", async () => {
      const button = element("load-pattern-history");
      const status = element("pattern-history-status");
      if (!adminTokenVerified || element("private-dashboard").hidden || button.disabled) return;
      const requestId = ++patternHistoryRequest;
      const tokenRevision = adminTokenRevision;
      const isCurrent = () => requestId === patternHistoryRequest &&
        tokenRevision === adminTokenRevision && adminTokenVerified &&
        !element("private-dashboard").hidden;
      button.disabled = true;
      status.textContent = "Loading pattern choices...";
      try {
        const data = await request("/api/pattern-history", "GET", undefined, {isResponseCurrent: isCurrent});
        if (!isCurrent()) return;
        const body = element("pattern-history-body");
        body.replaceChildren();
        data.events.forEach((event) => {
          const row = document.createElement("tr");
          const choice = event.kind === "reroll" ? "new settings" : event.kind.replaceAll("-", " ");
          const condition = event.outage_active ? "Disconnected until " + new Date(event.outage_until * 1000).toLocaleTimeString() : profileDirectionSummary(event.download);
          [new Date(event.happened_at * 1000).toLocaleString(), event.scope,
            choice, event.seed, condition].forEach((value) => {
            const cell = document.createElement("td");
            cell.textContent = value;
            row.appendChild(cell);
          });
          body.appendChild(row);
        });
        status.textContent = data.events.length ? "Latest choices loaded." : "No pattern choices recorded yet.";
      } catch (error) {
        if (!isCurrent()) return;
        if (error.status === 401 || error.status === 403) {
          lockDashboardAccess(error.message);
          return;
        }
        status.textContent = error.message;
      } finally {
        if (isCurrent()) button.disabled = false;
      }
    });

    function profileFormPayload(base) {
      const keep = (field) => base
        ? Object.assign({}, base[field])
        : { variation_percent: 0, enabled_probability: 100 };
      return {
        id: element("profile-id").value,
        name: element("profile-name").value,
        upload: impairment("upload"),
        download: impairment("download"),
        random_seed: optionalNumber("profile-seed"),
        random_mode: element("profile-random-mode").value,
        reroll_interval_seconds: Number(element("profile-reroll").value),
        reroll_interval_randomness: {
          ...keep("reroll_interval_randomness"),
          variation_percent: Number(element("profile-reroll-variation").value),
          enabled_probability: base ? base.reroll_interval_randomness.enabled_probability : 100
        },
        homepage_bounce_percent: Number(element("profile-homepage-chance").value),
        outage_probability: Number(element("profile-outage-probability").value),
        outage_duration_seconds: Number(element("profile-outage-duration").value),
        outage_interval_seconds: Number(element("profile-outage-interval").value),
        outage_duration_randomness: keep("outage_duration_randomness"),
        outage_interval_randomness: keep("outage_interval_randomness"),
        website_mode: element("profile-website-mode").value,
        website_domains: commaList("profile-website-domains"),
        website_randomness: {
          ...keep("website_randomness"),
          variation_percent: base ? base.website_randomness.variation_percent : 0,
          enabled_probability: Number(element("profile-website-enabled").value)
        }
      };
    }

    let editingProfile = null;

    function setProfileFormTitle(text) {
      const title = element("profile-form-title");
      (title.querySelector(".card-toggle") || title).textContent = text;
    }

    function startEditingProfile(profile) {
      editingProfile = profile;
      fillProfileForm(profile);
      element("profile-id").readOnly = true;
      setProfileFormTitle("Edit profile");
      element("new-profile").hidden = false;
      revealElement(element("profile-form-title"));
      element("profile-name").focus({ preventScroll: true });
    }

    function resetProfileEditing() {
      editingProfile = null;
      element("profile-id").readOnly = false;
      setProfileFormTitle("Save profile");
      element("new-profile").hidden = true;
    }

    let profileEditRequest = 0;

    async function editSavedProfile(profileId) {
      if (typeof guardDraft === "function" && guardDraft("profile-form", () => editSavedProfile(profileId))) return;
      const thisRequest = ++profileEditRequest;
      try {
        await loadProfiles();
      } catch (error) {
        if (thisRequest === profileEditRequest) showMessage(error.message, "error");
        return;
      }
      if (thisRequest !== profileEditRequest) return;
      const current = profiles.find((item) => item.id === profileId);
      if (!current) {
        renderSavedProfiles();
        showMessage("That profile no longer exists. The list was refreshed.", "error");
        return;
      }
      // The browser reads JSON numbers as doubles so a seed above 2^53 - 1 would be saved back changed.
      if (current.random_seed !== null && current.random_seed !== undefined &&
          !Number.isSafeInteger(current.random_seed)) {
        showMessage(
          "This profile has a random seed too large to edit here. Change it through the API or the CLI.",
          "error"
        );
        return;
      }
      startEditingProfile(current);
    }

    function renderSavedProfiles() {
      if (!adminTokenVerified) return;
      const container = element("saved-profiles");
      const general = generalProfileState && generalProfileState.profile;
      const generalId = general ? general.id : null;
      const generalActive = Boolean(general && generalProfileState.active);
      const generalBadge = generalActive ? "General · active"
        : generalProfileState && generalProfileState.state &&
          generalProfileState.state.observed_state === "reset" ? "General · paused"
        : "General · saved";
      const noHelper = !helperConfigured;
      const actionsBusy = networkActionBusy;
      const signature = JSON.stringify([
        profiles.map((profile) => [profile.id, profile.name, profileSummary(profile)]),
        generalId, generalActive, generalBadge, noHelper, actionsBusy, savedProfileBusy
      ]);
      if (container.dataset.signature === signature && container.children.length) return;
      container.dataset.signature = signature;
      const focused = document.activeElement;
      const focusedKey = focused && container.contains(focused) && focused.dataset.profileId
        ? focused.dataset.profileId + "|" + focused.dataset.action : null;
      const buttons = new Map();
      if (!profiles.length) {
        const empty = document.createElement("p");
        empty.className = "device-empty";
        empty.textContent = "No saved profiles yet. Create one below.";
        container.replaceChildren(empty);
        return;
      }
      container.replaceChildren(...profiles.map((profile) => {
        const row = document.createElement("div");
        row.className = "saved-profile-row";
        row.dataset.key = profile.id;
        row.dataset.profileId = profile.id;
        const text = document.createElement("div");
        const heading = document.createElement("p");
        heading.className = "saved-profile-name";
        const name = document.createElement("strong");
        name.textContent = profile.name;
        const id = document.createElement("span");
        id.className = "saved-profile-id";
        id.textContent = profile.id;
        heading.append(name, id);
        if (profile.id === generalId) {
          const badge = document.createElement("span");
          badge.className = "count-badge general-badge";
          badge.textContent = generalBadge;
          heading.append(badge);
        }
        const summary = document.createElement("p");
        summary.className = "saved-profile-summary";
        summary.textContent = profileSummary(profile);
        text.append(heading, summary);
        const actions = document.createElement("div");
        actions.className = "saved-profile-actions";
        const edit = document.createElement("button");
        edit.type = "button";
        edit.textContent = "Edit";
        edit.dataset.profileId = profile.id;
        edit.dataset.action = "edit";
        edit.setAttribute("aria-label", "Edit " + profile.name);
        edit.addEventListener("click", () => editSavedProfile(profile.id));
        const stopping = profile.id === generalId && generalActive;
        const apply = document.createElement("button");
        apply.type = "button";
        apply.className = stopping ? "" : "primary";
        apply.textContent = stopping ? "Stop" : "Apply to all";
        apply.dataset.profileId = profile.id;
        apply.dataset.action = "general";
        apply.setAttribute("aria-label", (stopping ? "Stop " : "Apply to all: ") + profile.name);
        // Without the helper the button is plainly disabled. While an action runs it stays
        // focusable (aria-disabled) so that keyboard focus survives the redraw. The action
        // function ignores clicks in both states.
        apply.disabled = noHelper;
        if (actionsBusy && !noHelper) apply.setAttribute("aria-disabled", "true");
        apply.addEventListener("click", () => runGeneralProfileAction(stopping, profile.id));
        if (savedProfileBusy && savedProfileBusy.id === profile.id && savedProfileBusy.stop === stopping) {
          apply.setAttribute("aria-busy", "true");
        }
        const duplicate = document.createElement("button");
        duplicate.type = "button";
        duplicate.textContent = "Duplicate";
        duplicate.dataset.profileId = profile.id;
        duplicate.dataset.action = "duplicate";
        duplicate.setAttribute("aria-label", "Duplicate " + profile.name);
        duplicate.addEventListener("click", () => duplicateProfile(profile));
        const remove = document.createElement("button");
        remove.type = "button";
        remove.textContent = "Delete";
        remove.dataset.profileId = profile.id;
        remove.dataset.action = "delete";
        remove.setAttribute("aria-label", "Delete " + profile.name);
        remove.addEventListener("click", () => deleteProfile(profile, remove));
        buttons.set(profile.id + "|edit", edit);
        buttons.set(profile.id + "|general", apply);
        buttons.set(profile.id + "|duplicate", duplicate);
        buttons.set(profile.id + "|delete", remove);
        actions.append(edit, apply, duplicate, remove);
        row.append(text, actions);
        return row;
      }));
      // A rebuild drops the focused button so put focus back on its replacement.
      if (focusedKey && buttons.has(focusedKey)) buttons.get(focusedKey).focus();
    }

    let homepageUiGeneration = 0;
    let homepageRequestOwner = null;
    let homepageRequestPending = false;
    let homepageCodeTimer = null;

    function clearHomepagePairingUi() {
      homepageUiGeneration += 1;
      homepageRequestOwner = null;
      homepageRequestPending = false;
      clearTimeout(homepageCodeTimer);
      homepageCodeTimer = null;
      element("homepage-code").textContent = "";
      element("homepage-extension-id").value = "";
      element("homepage-pages").value = "";
      element("homepage-create-code").disabled = false;
      element("homepage-revoke").disabled = false;
    }

    privateDataLockHooks.push(clearHomepagePairingUi);

    function homepageRequestCurrent(owner) {
      return owner === homepageRequestOwner && owner.revision === adminTokenRevision &&
        owner.generation === homepageUiGeneration && adminTokenVerified &&
        !element("private-dashboard").hidden;
    }

    function beginHomepageRequest() {
      if (homepageRequestPending || !adminTokenVerified || element("private-dashboard").hidden) return null;
      const owner = {revision: adminTokenRevision, generation: homepageUiGeneration,
        started: performance.now()};
      homepageRequestOwner = owner;
      homepageRequestPending = true;
      clearTimeout(homepageCodeTimer);
      homepageCodeTimer = null;
      element("homepage-code").textContent = "";
      element("homepage-create-code").disabled = true;
      element("homepage-revoke").disabled = true;
      return owner;
    }

    function finishHomepageRequest(owner) {
      if (!homepageRequestCurrent(owner)) return;
      homepageRequestPending = false;
      element("homepage-create-code").disabled = false;
      element("homepage-revoke").disabled = false;
    }

    element("homepage-create-code").addEventListener("click", async () => {
      const owner = beginHomepageRequest();
      if (!owner) return;
      const output = element("homepage-code");
      try {
        const result = await request("/api/homepage/pairing-code", "POST", {
          extension_id: element("homepage-extension-id").value.trim(),
          pages: element("homepage-pages").value.split(/\n/).map(value => value.trim()).filter(Boolean)
        });
        if (!homepageRequestCurrent(owner)) return;
        if (!result || typeof result.code !== "string" || !/^[a-f0-9]{32}$/.test(result.code) ||
            !Number.isInteger(result.expires_in) || result.expires_in <= 0 || result.expires_in > 120) {
          throw new Error("Pairing unavailable. Create a new code.");
        }
        const remaining = result.expires_in * 1000 - (performance.now() - owner.started);
        if (remaining <= 0) {
          output.textContent = "Code expired.";
          return;
        }
        output.textContent = "Pair within two minutes: " + result.code;
        homepageCodeTimer = setTimeout(() => {
          if (!homepageRequestCurrent(owner)) return;
          output.textContent = "Code expired.";
          homepageCodeTimer = null;
        }, remaining);
      } catch (error) {
        if (homepageRequestCurrent(owner)) showMessage(error.message, "error");
      } finally { finishHomepageRequest(owner); }
    });

    element("homepage-revoke").addEventListener("click", async () => {
      const owner = beginHomepageRequest();
      if (!owner) return;
      try {
        await request("/api/homepage/revoke", "POST", {});
        if (!homepageRequestCurrent(owner)) return;
        element("homepage-code").textContent = "All homepage pairings revoked.";
      } catch (error) {
        if (homepageRequestCurrent(owner)) showMessage(error.message, "error");
      } finally { finishHomepageRequest(owner); }
    });
    element("profile-form").addEventListener("submit", async (event) => {
      event.preventDefault();
      try {
        const body = profileFormPayload(editingProfile);
        await request("/api/profiles", "POST", body);
        refreshAfterSuccess("Profile saved.");
      } catch (error) {
        showMessage(error.message, "error");
      }
    });

    element("new-profile").addEventListener("click", () => {
      element("profile-form").reset();
      resetProfileEditing();
      element("profile-id").focus();
    });

    privateDataLockHooks.push(() => {
      element("profile-form").reset();
      resetProfileEditing();
    });


    const profileRangeFeatures = ["delay", "jitter", "loss", "bandwidth", "reorder", "duplicate", "corrupt"];
    let profileRangeTimer = null;
    let profileRangeRequest = 0;
    let profileRangeValues = null;

    function profileRangeText(value, unit) {
      if (value === null) return "no bandwidth limit";
      return new Intl.NumberFormat(undefined, { maximumFractionDigits: 2 }).format(value)
        + (unit === "%" ? "%" : " " + unit);
    }

    // Uploads never duplicate or reorder toward the household router (see the
    // renderer's upstream_safe_impairment), so say so while those are set.
    function updateUpstreamNote() {
      const value = (feature) => Number(element("upload-" + feature).value) > 0 ||
        (element("upload-" + feature + "-random-mode").value === "range" &&
         Number(element("upload-" + feature + "-random-maximum").value) > 0);
      element("upload-upstream-note").hidden = !["reorder", "duplicate"].some(value);
    }

    function updateProfileRangeControls() {
      updateUpstreamNote();
      const off = Number(element("profile-reroll").value) <= 0;
      element("profile-reroll-variation").disabled = off;
      element("profile-website-enabled").disabled = off;
      ["upload", "download"].forEach((direction) => {
        profileRangeFeatures.forEach((feature) => {
          const stem = direction + "-" + feature + "-random";
          const modeControl = element(stem + "-mode");
          const mode = modeControl.value;
          const custom = mode === "range";
          const minimum = element(stem + "-minimum");
          const maximum = element(stem + "-maximum");
          if (custom && modeControl.dataset.previousMode !== "range"
              && minimum.value === "" && maximum.value === "") {
            const cached = profileRangeValues && profileRangeValues[direction][feature];
            const base = element(direction + "-" + feature).value;
            const matching = cached && cached.mode === "relative" && cached.base === (base === "" ? null : Number(base))
              && cached.variation_percent === Number(element(stem + "-variation").value);
            if (matching && cached.minimum !== null) {
              minimum.value = cached.minimum;
              maximum.value = cached.maximum;
            } else if (base !== "") {
              minimum.value = base;
              maximum.value = base;
            }
          }
          modeControl.dataset.previousMode = mode;
          minimum.required = custom;
          maximum.required = custom;
          element(stem + "-bounds").hidden = !custom;
          element(stem + "-relative-label").hidden = custom;
          ["mode", "variation", "enabled", "minimum", "maximum"].forEach((field) => {
            element(stem + "-" + field).disabled = off || (field === "variation" && custom)
              || ((field === "minimum" || field === "maximum") && !custom);
          });
          minimum.setCustomValidity(custom && Number(minimum.value) > Number(maximum.value)
            ? "Minimum must not exceed maximum." : "");
        });
      });
    }

    async function refreshProfileRangePreview(requestNumber) {
      if (!adminTokenVerified) return;
      try {
        const data = await request("/api/profiles/ranges", "POST", {
          upload: impairment("upload"), download: impairment("download")
        });
        if (requestNumber !== profileRangeRequest || !adminTokenVerified) return;
        profileRangeValues = data.ranges;
        const off = Number(element("profile-reroll").value) <= 0;
        ["upload", "download"].forEach((direction) => {
          profileRangeFeatures.forEach((feature) => {
            const value = data.ranges[direction][feature];
            const output = element(direction + "-" + feature + "-random-preview");
            const base = profileRangeText(value.base, value.unit);
            let message;
            if (off) {
              message = "Rerolls off. Base value: " + base + ". Saved randomness returns when rerolls are enabled.";
            } else if (value.enabled_probability === 0 || value.minimum === null) {
              message = "Starts at " + base + ". After the first reroll: off.";
            } else {
              const range = value.minimum === value.maximum
                ? "fixed at " + profileRangeText(value.minimum, value.unit)
                : profileRangeText(value.minimum, value.unit) + "–" + profileRangeText(value.maximum, value.unit);
              message = "Starts at " + base + ". After the first reroll: " + range + ". "
                + value.enabled_probability + "% chance active per interval. Step "
                + profileRangeText(value.step, value.unit) + ".";
              if (value.minimum === value.maximum && element(direction + "-" + feature + "-random-mode").value !== "range"
                  && Number(element(direction + "-" + feature + "-random-variation").value) > 0) {
                message += " Variation is too small to change this value.";
              }
            }
            if (feature === "reorder") message += " Applies only while delay is above 0 ms.";
            if (output.textContent !== message) output.textContent = message;
          });
        });
        element("profile-range-status").textContent = off
          ? "Rerolls are off for ordinary profiles. Saved settings are preserved."
          : "Value ranges updated.";
      } catch (error) {
        if (requestNumber !== profileRangeRequest || !adminTokenVerified) return;
        element("profile-range-status").textContent = "Check the value range settings.";
        ["upload", "download"].forEach((direction) => {
          profileRangeFeatures.forEach((feature) => {
            element(direction + "-" + feature + "-random-preview").textContent = "Check the values: " + error.message;
          });
        });
      }
    }

    function scheduleProfileRangePreview() {
      updateProfileRangeControls();
      if (!adminTokenVerified) {
        element("profile-range-status").textContent = "Sign in to preview value ranges.";
        return;
      }
      element("profile-range-status").textContent = "Updating value ranges…";
      ["upload", "download"].forEach((direction) => {
        profileRangeFeatures.forEach((feature) => {
          element(direction + "-" + feature + "-random-preview").textContent = "Updating value range…";
        });
      });
      clearTimeout(profileRangeTimer);
      const requestNumber = ++profileRangeRequest;
      profileRangeTimer = setTimeout(() => refreshProfileRangePreview(requestNumber), 250);
    }

    element("profile-form").addEventListener("input", scheduleProfileRangePreview);
    element("profile-form").addEventListener("change", scheduleProfileRangePreview);
    element("profile-form").addEventListener("reset", () => {
      profileRangeValues = null;
      ++profileRangeRequest;
      clearTimeout(profileRangeTimer);
      setTimeout(scheduleProfileRangePreview, 0);
    });
    scheduleProfileRangePreview();

    element("device-form").addEventListener("submit", async (event) => {
      event.preventDefault();
      try {
        const profileSelect = element("device-profile");
        if (!adminTokenVerified || profileSelect.disabled) {
          throw new Error(element("device-profile-note").textContent || "Load saved profiles first.");
        }
        if (!profiles.some((profile) => profile.id === profileSelect.value)) {
          throw new Error("Choose a saved profile for this device.");
        }
        await request("/api/devices", "POST", {
          id: element("device-id").value,
          name: element("device-name").value,
          ipv4_address: element("device-ip").value,
          profile_id: profileSelect.value,
          mac_address: element("device-mac").value.trim() || null,
          ...(editingDeviceSnapshot ? { expected: editingDeviceSnapshot } : {})
        });
        if (!formDrafts.has("device-form")) stopEditing("device");
        refreshAfterSuccess("Device saved.");
      } catch (error) {
        showMessage(error.message, "error");
      } finally {
        element("save-device").disabled = element("device-profile").disabled;
      }
    });

    element("device-profile").addEventListener("change", () => {
      const select = element("device-profile");
      if (!select.disabled && profiles.some((profile) => profile.id === select.value)) {
        setDeviceProfileAvailability("", true);
      }
    });

    element("device-profile-refresh").addEventListener("click", async () => {
      try {
        await loadProfiles();
      } catch (error) {
        showMessage(error.message, "error");
      }
    });

    element("unaffected-form").addEventListener("submit", async (event) => {
      event.preventDefault();
      await connectedControlAction("/api/unaffected-devices", {
        mac_address: element("unaffected-mac").value,
        display_name: element("unaffected-name").value
      }, event.currentTarget.querySelector("button"), "Protection saved and applied.");
    });

    element("ad-blocking-form").addEventListener("submit", async (event) => {
      event.preventDefault();
      const domains = element("ad-blocking-allowances").value.split(/\s+/).filter(Boolean);
      await saveAdBlocking({allowed_domains: domains}, event.currentTarget.querySelector("button"));
    });
    element("ad-blocking-update").addEventListener("click", (event) => {
      saveAdBlocking({update_list: true}, event.currentTarget);
    });

    element("group-form").addEventListener("submit", async (event) => {
      event.preventDefault();
      if (groupSubmitting) return;
      const form = event.currentTarget;
      try {
        const problem = groupFormProblem();
        if (problem) throw new Error(problem);
        const selectedIds = Array.from(groupSelectedMembers);
        const deviceIds = selectedIds.filter((id) => !id.startsWith("live:"));
        const connected = selectedIds.filter((id) => id.startsWith("live:")).map((id) => {
          const client = connectedClients.find((device) => "live:" + device.mac_address === id);
          if (!connectedClientsAvailable || !client || !client.association_verified || !client.ipv4_address || client.unaffected) {
            throw new Error("A selected client is unavailable. Remove it or refresh to verify it again.");
          }
          return {ipv4_address: client.ipv4_address, mac_address: client.mac_address};
        });
        if (!selectedIds.length) throw new Error("Select at least one device for the group.");
        groupSubmitting = true;
        form.querySelectorAll("input, select").forEach((field) => { field.disabled = true; });
        updateGroupFormControls();
        const result = await request("/api/groups", "POST", {
          id: element("group-id").value,
          name: element("group-name").value,
          profile_id: element("group-profile").value || null,
          device_ids: deviceIds,
          connected_clients: connected
        });
        savedDevices.filter((device) => deviceIds.includes(device.id)).forEach((device) => clearConnectedDrafts.add(device.mac_address));
        connected.forEach((device) => clearConnectedDrafts.add(device.mac_address));
        const count = result.group.device_ids.length;
        stopEditing("group");
        refreshAfterSuccess((result.live_applied ? "Device group saved and applied." : result.message || "Device group saved.") +
          " " + count + (count === 1 ? " device." : " devices.") + (result.warning ? " " + result.warning : ""));
      } catch (error) {
        groupSaveError = error.message;
        showMessage(error.message, "error");
        await Promise.all([loadGroups(), loadConnectedDevices()]);
      } finally {
        groupSubmitting = false;
        form.querySelectorAll("input, select").forEach((field) => { field.disabled = false; });
        if (pendingFormSubmitButton === element("group-save")) pendingFormSubmitButton = null;
        element("group-save").removeAttribute("aria-busy");
        updateGroupFormControls();
      }
    });

    element("policy-form").addEventListener("submit", async (event) => {
      event.preventDefault();
      try {
        await request("/api/policies", "POST", {
          version: policyVersion,
          options: {
            enabled: element("access-enabled").checked,
            reject_wifi_joins: element("access-admission").checked,
            dns_failure_percent: element("dns-failure-percent").valueAsNumber
          },
          mac: {
            mode: element("mac-mode").value,
            allowlist: commaList("mac-allowlist"),
            denylist: commaList("mac-denylist")
          },
          websites: {
            mode: element("website-mode").value,
            allowed_domains: commaList("website-allowlist"),
            blocked_domains: commaList("website-blocklist")
          }
        });
        refreshAfterSuccess("Access rules saved. Choose Apply access rules to enforce them.");
      } catch (error) {
        showMessage(error.message, "error");
      }
    });

    element("schedule-form").addEventListener("submit", async (event) => {
      event.preventDefault();
      const days = selectedScheduleDays();
      if (!days.length) {
        showMessage("Choose at least one day for the schedule.", "error");
        return;
      }
      try {
        const payload = {
          id: element("schedule-id").value,
          ...scheduleTargetFields(element("schedule-device").value),
          profile_id: element("schedule-profile").value,
          days_of_week: days,
          start_minute: timeToMinute("schedule-start"),
          end_minute: timeToMinute("schedule-end"),
          enabled: element("schedule-enabled").checked
        };
        const submitter = pendingFormSubmitButton;
        const previewedDraft = formDrafts.get("schedule-form");
        pendingFormSubmitButton = null;
        const preview = await request("/api/schedules/preview", "POST", payload);
        if (preview.overlaps.length && !await confirmWorkflow("Overlapping schedules: " + preview.overlaps.join(", ") + ". " + preview.message + " Save anyway?")) {
          if (submitter) setButtonBusy(submitter, false);
          return;
        }
        if (formDrafts.get("schedule-form") !== previewedDraft) throw new Error("The form changed during the preview. Review it and save again.");
        pendingFormSubmitButton = submitter;
        await request("/api/schedules", "POST", payload);
        refreshAfterSuccess(element("schedule-enabled").checked
          ? "Schedule saved. It follows the Pi clock while the configuration is applied."
          : "Schedule saved as disabled.");
      } catch (error) {
        // The preview runs apart from the Save button, so its refusal has no note in the form yet.
        if (typeof formError === "function") formError(element("schedule-form"), error.message);
        showMessage(error.message, "error");
      } finally {
        if (event.submitter) setButtonBusy(event.submitter, false);
      }
    });

    element("timer-form").addEventListener("submit", async (event) => {
      event.preventDefault();
      try {
        const result = await request("/api/timers", "POST", {
          device_id: element("timer-device").value,
          profile_id: element("timer-profile").value,
          duration_minutes: Number(element("timer-duration").value)
        });
        refreshAfterSuccess(
          result.live_applied
            ? "Timer started and the profile is active now."
            : "Timer saved. Choose Start to start it.",
        );
      } catch (error) {
        showMessage(error.message, "error");
      }
    });

    element("progression-mode").addEventListener("change", syncProgressionMode);

    // A reset puts the menu back after this event, so the fields follow it a moment later.
    element("progression-form").addEventListener("reset", () => window.setTimeout(syncProgressionMode, 0));

    syncProgressionMode();

    element("progression-form").addEventListener("submit", async (event) => {
      event.preventDefault();
      try {
        const mode = element("progression-mode").value;
        const result = await request("/api/progressions", "POST", {
          device_id: element("progression-device").value,
          start_profile_id: element("progression-start-profile").value,
          end_profile_id: element("progression-end-profile").value,
          mode: mode,
          duration_minutes: Number(element("progression-duration").value),
          traffic_target_queries: mode === "traffic"
            ? Number(element("progression-traffic-target").value)
            : 0,
          traffic_target_bytes: mode === "bytes"
            ? Number(element("progression-bytes-target").value) * 1048576
            : 0,
          website_domains: commaList("progression-domains")
        });
        refreshAfterSuccess(
          result.live_applied
            ? "Progression started and is active now."
            : "Progression saved. Choose Start to start it.",
        );
      } catch (error) {
        showMessage(error.message, "error");
      }
    });

    element("data-limit-reset-mode").addEventListener("change", () => syncQuotaReset("data-limit"));

    element("group-data-limit-reset-mode").addEventListener("change", () => syncQuotaReset("group-data-limit"));

    element("service-quota-reset-mode").addEventListener("change", () => syncQuotaReset("service-quota"));

    element("time-quota-reset-mode").addEventListener("change", () => syncQuotaReset("time-quota"));

    ["data-limit", "group-data-limit", "service-quota", "time-quota"].forEach((prefix) => {
      element(prefix + "-form").addEventListener("reset", () => window.setTimeout(() => {
        syncQuotaReset(prefix);
        if (savedQuotaDomains[prefix]) {
          delete savedQuotaDomains[prefix];
          populateQuotaDomains(prefix);
        }
      }, 0));
    });

    element("service-quota-device").addEventListener("change", () => populateServiceQuotaDomains());

    element("time-quota-device").addEventListener("change", () => populateTimeQuotaDomains());

    // Only this button starts a new 24 hours for a remembered token. The check when the page
    // restores a saved token and the other buttons call checkAdminToken directly.
    element("check-admin-token").addEventListener("click", async () => {
      await checkAdminToken();
      syncRememberedCredentials("admin");
    });
    // Enter in the field presses the Check token button, which runs the check above. The form
    // itself never leaves the page.
    element("admin-token-form").addEventListener("submit", (event) => event.preventDefault());
    // A pasted token is checked straight away. Typing it is checked with the button.
    element("admin-token").addEventListener("paste", () => {
      window.setTimeout(() => {
        if (element("admin-token").value.trim() && !adminTokenChecking) element("check-admin-token").click();
      }, 0);
    });
    element("traffic-rate-form").addEventListener("submit", async (event) => {
      event.preventDefault();
      const button = event.currentTarget.querySelector("button[type='submit']");
      const status = element("traffic-rate-status");
      button.disabled = true;
      try {
        const settings = await request("/api/traffic-settings", "POST", {
          upload_kbit: trafficRateFromInput("traffic-isp-upload"),
          download_kbit: trafficRateFromInput("traffic-isp-download")
        });
        setTrafficRateInputs(settings);
        resetTrafficHistory();
        status.textContent = "ISP speed references saved. They do not change traffic shaping.";
        showMessage("ISP speed references saved.", "success");
        if (trafficSamplingAllowed) await pollTraffic();
      } catch (error) {
        status.textContent = error.message;
        showMessage(error.message, "error");
      } finally {
        button.disabled = false;
      }
    });

    element("admin-token").addEventListener("input", () => {
      adminTokenRevision += 1;
      if (typeof cancelWifiRecovery === "function") cancelWifiRecovery();
      adminTokenChecking = false;
      element("check-admin-token").disabled = false;
      element("check-admin-token").removeAttribute("aria-busy");
      adminTokenRejected = false;
      adminTokenRefused = false;
      adminTokenVerified = false;
      routerWifiRemoteBusy = false;
      routerWifiRemoteChanging = false;
      setDashboardUnlocked(false);
      element("allow-dashboard-device").disabled = true;
      updateCredentialStatus();
      setPrivateDataPanels("Token changed. Choose Check token to load.");
      stopTrafficSampling("Token changed. Choose Check token to restart live traffic sampling.");
      element("token-check-status").textContent = "Token changed. Choose Check token to load diagnostics.";
      element("token-check-status").className = "token-check-status";
    });

    element("traffic-device").addEventListener("change", () => {
      resetTrafficView();
      if (!trafficSamplingAllowed) return;
      element("traffic-status").textContent = "Changing the device view...";
      pollTraffic();
    });

    setupTrafficChartInspection();

    document.addEventListener("visibilitychange", () => {
      syncTrafficSampling();
      drawTrafficChart();
    });

    document.addEventListener("visibilitychange", syncImpact);

    window.addEventListener("resize", drawTrafficChart);

    element("toast-dismiss").addEventListener("click", dismissToast);

    element("refresh-diagnostics").addEventListener("click", async (event) => {
      const button = event.currentTarget;
      const tokenRevision = adminTokenRevision;
      button.disabled = true;
      button.setAttribute("aria-busy", "true");
      button.textContent = "Refreshing...";
      showMessage("Refreshing diagnostics...", "working");
      try {
        const result = await loadAdminTools();
        if (tokenRevision !== adminTokenRevision) return;
        if (result.ok) {
          await loadTrafficSettings();
          if (tokenRevision !== adminTokenRevision) return;
          await startTrafficSampling();
          if (tokenRevision !== adminTokenRevision) return;
        }
        element("token-check-status").textContent = result.message;
        element("token-check-status").className = "token-check-status " + (result.ok ? "success" : "error");
        showMessage(result.message, result.ok ? "success" : "error");
      } catch (error) {
        if (tokenRevision !== adminTokenRevision) return;
        element("token-check-status").textContent = error.message;
        element("token-check-status").className = "token-check-status error";
        showMessage(error.message, "error");
      } finally {
        button.disabled = false;
        button.removeAttribute("aria-busy");
        button.textContent = "Refresh";
      }
    });

    element("download-backup").addEventListener("click", async (event) => {
      const button = event.currentTarget;
      const token = element("admin-token").value.trim();
      if (!token) {
        element("action-history").textContent = "Enter the admin token before downloading a backup.";
        showMessage("Enter the admin token before downloading a backup.", "error");
        return;
      }
      setButtonBusy(button, true);
      button.textContent = "Preparing backup...";
      showMessage("Preparing the configuration backup...", "working");
      try {
        const response = await fetch("/api/backup", {
          headers: { Authorization: "Bearer " + token },
          cache: "no-store"
        });
        if (!response.ok) {
          const data = await response.json();
          throw new Error(data.error || response.statusText);
        }
        const url = URL.createObjectURL(await response.blob());
        const link = document.createElement("a");
        link.href = url;
        link.download = "jittersim-backup.db";
        link.click();
        window.setTimeout(() => URL.revokeObjectURL(url), 1_000);
        showMessage("Backup downloaded.", "success");
      } catch (error) {
        element("action-history").textContent = error.message;
        showMessage(error.message, "error");
      } finally {
        setButtonBusy(button, false);
        button.textContent = "Download backup";
      }
    });

    syncQuotaReset("data-limit");

    syncQuotaReset("group-data-limit");

    syncQuotaReset("service-quota");

    syncQuotaReset("time-quota");

    element("data-limit-form").addEventListener("submit", async (event) => {
      event.preventDefault();
      try {
        const result = await request("/api/data-limits", "POST", {
          device_id: element("data-limit-device").value,
          profile_id: element("data-limit-profile").value,
          limit_bytes: Number(element("data-limit-amount").value) * Number(element("data-limit-unit").value),
          enabled: element("data-limit-enabled").checked,
          ...quotaSchedulePayload("data-limit")
        });
        refreshAfterSuccess(
          result.live_applied ? "Device limit saved and applied." : "Device limit saved.",
        );
      } catch (error) {
        showMessage(error.message, "error");
      }
    });

    element("group-data-limit-form").addEventListener("submit", async (event) => {
      event.preventDefault();
      try {
        const result = await request("/api/group-data-limits", "POST", {
          group_id: element("group-data-limit-group").value,
          profile_id: element("group-data-limit-profile").value,
          limit_bytes: Number(element("group-data-limit-amount").value) * Number(element("group-data-limit-unit").value),
          enabled: element("group-data-limit-enabled").checked,
          ...quotaSchedulePayload("group-data-limit")
        });
        refreshAfterSuccess(
          result.live_applied ? "Shared group limit saved and applied." : "Shared group limit saved.",
        );
      } catch (error) {
        showMessage(error.message, "error");
      }
    });

    element("service-list-add-observed").addEventListener("click", () => {
      const name = element("service-list-observed").value;
      if (!name) return;
      const field = element("service-list-hostnames");
      const present = field.value.split(/[\s,]+/).filter(Boolean);
      if (!present.includes(name)) field.value = present.concat(name).join("\n");
    });

    element("service-list-form").addEventListener("submit", async (event) => {
      event.preventDefault();
      try {
        const services = Array.from(
          element("service-list-catalogue").querySelectorAll("input:checked")
        ).map((input) => input.value);
        const hostnames = element("service-list-hostnames").value.split(/[\s,]+/).filter(Boolean);
        if (!services.length && !hostnames.length) {
          throw new Error("Choose at least one service or enter a hostname.");
        }
        const result = await request("/api/service-lists", "POST", {
          id: element("service-list-id").value,
          name: element("service-list-name").value,
          profile_id: element("service-list-profile").value,
          services: services,
          hostnames: hostnames,
          enabled: element("service-list-enabled").checked
        });
        refreshAfterSuccess(result.live_applied ? "Service list saved and applied." : "Service list saved.");
      } catch (error) {
        showMessage(error.message, "error");
      }
    });

    element("service-assignment-form").addEventListener("submit", async (event) => {
      event.preventDefault();
      try {
        const domains = element("service-assignment-domains").value.split(/[\s,]+/).filter(Boolean);
        if (!domains.length) throw new Error("Enter at least one HTTPS hostname.");
        const result = await request("/api/service-assignments", "POST", {
          id: element("service-assignment-id").value,
          name: element("service-assignment-name").value,
          device_id: element("service-assignment-device").value,
          profile_id: element("service-assignment-profile").value,
          domains: domains,
          enabled: element("service-assignment-enabled").checked
        });
        refreshAfterSuccess(
          result.live_applied ? "Browser service profile saved and applied." : "Browser service profile saved."
        );
      } catch (error) {
        showMessage(error.message, "error");
      }
    });

    element("service-quota-form").addEventListener("submit", async (event) => {
      event.preventDefault();
      try {
        const domains = Array.from(element("service-quota-domains").selectedOptions)
          .map((option) => option.value);
        if (!domains.length) throw new Error("Select at least one observed domain.");
        const device = element("service-quota-device").value;
        const result = await request("/api/service-quotas", "POST", {
          id: element("service-quota-id").value,
          name: element("service-quota-name").value,
          device_id: element("service-quota-device").value,
          profile_id: element("service-quota-profile").value,
          domains: domains,
          limit_bytes: Number(element("service-quota-amount").value) * Number(element("service-quota-unit").value),
          weight_percent: Math.round(Number(element("service-quota-weight").value) * 100),
          enabled: element("service-quota-enabled").checked,
          ...quotaSchedulePayload("service-quota")
        });
        savedQuotaDomains["service-quota"] = { device: device, domains: domains };
        refreshAfterSuccess(
          result.live_applied ? "Service limit saved and applied." : "Service limit saved.",
        );
      } catch (error) {
        showMessage(error.message, "error");
      }
    });

    element("time-quota-form").addEventListener("submit", async (event) => {
      event.preventDefault();
      try {
        const domains = Array.from(element("time-quota-domains").selectedOptions)
          .map((option) => option.value);
        if (!domains.length) throw new Error("Select at least one observed domain.");
        const device = element("time-quota-device").value;
        const result = await request("/api/time-quotas", "POST", {
          id: element("time-quota-id").value,
          name: element("time-quota-name").value,
          device_id: element("time-quota-device").value,
          profile_id: element("time-quota-profile").value,
          domains: domains,
          limit_seconds: Number(element("time-quota-amount").value) * Number(element("time-quota-unit").value),
          window_duration_seconds: Number(element("time-quota-window").value) * 60,
          enabled: element("time-quota-enabled").checked,
          ...quotaSchedulePayload("time-quota")
        });
        savedQuotaDomains["time-quota"] = { device: device, domains: domains };
        refreshAfterSuccess(
          result.live_applied ? "Time limit saved and applied." : "Time limit saved. Start its timer from the saved row.",
        );
      } catch (error) {
        showMessage(error.message, "error");
      }
    });

    element("access-point-form").addEventListener("submit", async (event) => {
      event.preventDefault();
      const cancelChange = (text) => {
        if (pendingFormSubmitButton) setButtonBusy(pendingFormSubmitButton, false);
        pendingFormSubmitButton = null;
        showMessage(text);
      };
      if (wifiChangePending) return cancelChange("Another Pi Wi-Fi change is still running.");
      // Held while the question is open so a second save or a name reset cannot start beside it.
      wifiChangePending = true;
      const submittedSsid = element("access-point-ssid").value;
      const submittedPassword = element("access-point-password").value;
      const submittedDraft = formDrafts.get("access-point-form");
      let tokenRevision = adminTokenRevision;
      try {
        if (!await confirmWorkflow(WIFI_RESTART_NOTICE, {title: "Change the Pi Wi-Fi?", confirmLabel: "Change Pi Wi-Fi"})) {
          return cancelChange("Pi Wi-Fi change cancelled.");
        }
        tokenRevision = adminTokenRevision;
        // The Pi confirms first and restarts its Wi-Fi afterwards. The result is read once it is back.
        const outcome = await runWifiChange("/api/access-point", {
          ssid: submittedSsid,
          password: submittedPassword
        }, (operation) => {
          if (tokenRevision !== adminTokenRevision || !operation || operation.state !== "succeeded") return;
          if (formDrafts.get("access-point-form") !== submittedDraft ||
              element("access-point-ssid").value !== submittedSsid ||
              element("access-point-password").value !== submittedPassword) return;
          element("access-point-password").value = "";
          // The saved name is what the field shows now so nothing is left to restore.
          clearDraft("access-point-form");
          showMessage(operation.message, "success");
        });
        if (tokenRevision === adminTokenRevision && outcome && outcome.gaveUp) showMessage("The Pi has not answered yet. See the note on this page.", "error");
      } catch (error) {
        if (tokenRevision === adminTokenRevision) showMessage(error.message, "error");
      } finally {
        wifiChangePending = false;
      }
    });

    element("reset-wifi-name").addEventListener("click", async (event) => {
      if (wifiResetPending || wifiChangePending || !helperConfigured) return;
      const button = event.currentTarget;
      const status = element("wifi-reset-status");
      // Held while the question is open so a second click or a Wi-Fi save cannot start beside it.
      wifiResetPending = true;
      if (!await confirmFromButton(button, "Restore the default Wi-Fi name? The password and security type stay the same. " + WIFI_RESTART_NOTICE,
          {title: "Restore the default Wi-Fi name?", confirmLabel: "Restore name"}) || !helperConfigured) {
        wifiResetPending = false;
        setButtonBusy(button, false);
        button.disabled = !helperConfigured;
        return;
      }
      status.textContent = "Restoring the Pi Wi-Fi name...";
      const submittedDraft = formDrafts.get("access-point-form");
      const submittedSsid = element("access-point-ssid").value;
      const tokenRevision = adminTokenRevision;
      try {
        const outcome = await runWifiChange("/api/access-point/reset-name", {}, (operation) => {
          if (tokenRevision !== adminTokenRevision) return;
          if (operation && operation.state === "succeeded" &&
              formDrafts.get("access-point-form") === submittedDraft &&
              element("access-point-ssid").value === submittedSsid) {
            element("access-point-ssid").value = operation.ssid;
            // The restored name replaces what was typed. Without this the next refresh
            // puts the typed name back and a later save sends it to the access point.
            clearDraft("access-point-form");
          }
          status.textContent = operation ? operation.message : "Check the Pi Wi-Fi settings.";
          showMessage(status.textContent, operation && operation.state === "succeeded" ? "success" : "error");
        });
        if (tokenRevision === adminTokenRevision && outcome && outcome.gaveUp) status.textContent = "The Pi has not answered yet. See the note on this page.";
      } catch (error) {
        if (tokenRevision === adminTokenRevision) status.textContent = error.message;
        if (tokenRevision === adminTokenRevision) showMessage(error.message, "error");
      } finally {
        wifiResetPending = false;
        if (tokenRevision === adminTokenRevision) {
          setButtonBusy(button, false);
          button.disabled = !helperConfigured;
        }
      }
    });

    function renderRouterWifi() {
      const button = element("router-wifi-toggle");
      const status = element("router-wifi-status");
      const password = element("router-admin-password").value;
      const fresh = routerWifiCheckedAt && Date.now() / 1000 - routerWifiCheckedAt < 45;
      const known = typeof routerWifiEnabled === "boolean" && fresh && adminTokenVerified && !routerPasswordRejected;
      const busy = routerActionPending || routerPasswordChecking || routerWifiRemoteBusy || routerWifiPolling;
      const copyButton = element("sync-router-ap");
      copyButton.disabled = !adminTokenVerified || !helperConfigured || routerPasswordRejected || busy;
      copyButton.title = !adminTokenVerified ? "Check the admin token first" :
        !helperConfigured ? "Needs the Pi helper" :
        routerPasswordRejected ? "Router password rejected. Enter it again." : busy ? "Working…" :
        !password ? "Enter the router password first" : "Copy the router Wi-Fi name, password and security to the Pi";
      element("check-router-password").disabled = busy || routerCheckWaiting();
      element("refresh-router-wifi").disabled = !helperConfigured || !adminTokenVerified ||
        !password || routerPasswordRejected || busy;
      button.disabled = true;
      button.title = "Router Wi-Fi switching is disabled for recovery safety. Use the router controls.";
      button.setAttribute("aria-checked", String(known && routerWifiEnabled));
      button.setAttribute("data-unknown", String(!known));
      button.setAttribute("aria-busy", String(busy));
      status.textContent = !adminTokenVerified ? "Check admin token" : !helperConfigured ? "Needs Pi helper" :
        !password ? "Enter router password" : routerPasswordRejected ? "Password rejected" :
        routerActionPending || routerWifiRemoteChanging ? "Changing…" : busy ? "Checking…" :
        known ? (routerWifiEnabled ? "On" : "Off") + " · " + new Date(routerWifiCheckedAt * 1000).toLocaleTimeString([], {hour: "2-digit", minute: "2-digit", second: "2-digit"}) : routerWifiError ? "Status unavailable" : "Check router Wi-Fi";
      status.title = routerWifiError || (known ? "Last checked " + new Date(routerWifiCheckedAt * 1000).toLocaleTimeString() : "Automatic router checks are paused. Choose Check router Wi-Fi.");
    }

    // The Pi spaces router sign-ins apart. When it refuses a password check the button counts
    // the wait down, and the Pi still enforces it after a reload or from another tab.
    function routerCheckWaiting() {
      return Date.now() < routerCheckWaitUntil;
    }

    function tickRouterCheckWait() {
      window.clearTimeout(routerCheckWaitTimer);
      routerCheckWaitTimer = null;
      const left = Math.ceil((routerCheckWaitUntil - Date.now()) / 1000);
      if (!routerPasswordChecking) {
        element("check-router-password").textContent = left > 0 ? "Wait " + left + " s" : "Check password";
      }
      if (left > 0) routerCheckWaitTimer = window.setTimeout(tickRouterCheckWait, 1000);
      renderRouterWifi();
    }

    async function pollRouterWifi() {
      if (document.hidden || !adminTokenVerified || routerPasswordRejected || routerActionPending || routerPasswordChecking || routerWifiPolling) return;
      const password = element("router-admin-password").value;
      if (!password || !helperConfigured) return;
      const passwordRevision = routerPasswordRevision;
      const tokenRevision = adminTokenRevision;
      routerWifiPolling = true;
      renderRouterWifi();
      try {
        const context = wifiRecoveryContext(45000);
        const tokenValid = context.valid;
        context.valid = () => tokenValid() && passwordRevision === routerPasswordRevision;
        let refresh = true;
        for (let attempt = 0; ; attempt++) {
          const response = await boundedRecoveryRead(options => request(
            "/api/router/status", "POST", {admin_password: password, refresh}, options), context);
          if (passwordRevision !== routerPasswordRevision || tokenRevision !== adminTokenRevision) return;
          const result = response.result;
          routerWifiRemoteBusy = result.busy === true;
          routerWifiRemoteChanging = result.changing === true;
          routerWifiEnabled = typeof result.enabled === "boolean" && result.checked_at >= routerWifiMinimumCheck ? result.enabled : null;
          routerWifiCheckedAt = result.checked_at;
          routerWifiError = result.error || "";
          if (!routerWifiRemoteBusy) {
            if (attempt === 0 && routerWifiEnabled === null && !routerWifiError && result.refresh_in > 0) {
              // The Pi spaces router sign-ins apart and did not ask the router this time.
              showMessage("The router was contacted a moment ago. Wait " + result.refresh_in + " second" +
                (result.refresh_in === 1 ? "" : "s") + ", then choose Check router Wi-Fi again.", "info");
            }
            break;
          }
          if (attempt >= 44 || context.now() >= context.deadline) {
            throw new Error("Router check timed out. Wait before checking again.");
          }
          refresh = false;
          await new Promise(resolve => window.setTimeout(resolve, 1000));
          if (passwordRevision !== routerPasswordRevision || tokenRevision !== adminTokenRevision) return;
        }
        if (routerWifiEnabled !== null) {
          routerPasswordVerified = true;
          routerPasswordRejected = false;
        } else if (/^Router login failed/.test(routerWifiError)) {
          routerPasswordVerified = false;
          routerPasswordRejected = true;
        }
      } catch (error) {
        if (passwordRevision !== routerPasswordRevision || tokenRevision !== adminTokenRevision) return;
        routerWifiEnabled = null;
        routerWifiError = error.message;
        routerWifiRemoteBusy = false;
        routerWifiRemoteChanging = false;
      } finally {
        routerWifiPolling = false;
        renderRouterWifi();
      }
    }

    async function routerRequest(path, body) {
      const passwordRevision = routerPasswordRevision;
      const tokenRevision = adminTokenRevision;
      const context = wifiRecoveryContext(210000);
      const tokenValid = context.valid;
      context.valid = () => tokenValid() && passwordRevision === routerPasswordRevision;
      const identifier = await boundedRecoveryRead(options => newWifiRequestId(options), context);
      let response;
      try {
        response = await boundedRecoveryRead(options => request(path, "POST",
          {...body, background: true, request_id: identifier}, options), context);
      } catch (error) {
        if (!context.valid() || (!isConnectionLost(error) && !error.recoveryStopped)) throw error;
        // The write may have happened. Confirm exactly this identifier through read-only polling.
        response = {pending: true, operation_id: identifier};
      }
      while (response.pending) {
        await recoveryPause(1000, context);
        const pendingId = response.operation_id;
        const poll = options => request("/api/router/operation", "POST", {
          admin_password: body.admin_password, operation_id: pendingId
        }, options);
        response = await requestAcrossWifiRestart(poll, context);
      }
      if (passwordRevision !== routerPasswordRevision || tokenRevision !== adminTokenRevision) {
        throw new Error("Credentials changed. Check router state before another change.");
      }
      return response;
    }

    async function routerAction(path, body, message, button) {
      if (routerActionPending || routerPasswordChecking) return;
      if (!requireAdminToken(button)) return;
      const passwordRevision = routerPasswordRevision;
      const tokenRevision = adminTokenRevision;
      const stillCurrent = () => passwordRevision === routerPasswordRevision && tokenRevision === adminTokenRevision;
      const password = element("router-admin-password").value;
      const status = element("router-action-message");
      if (!password) {
        status.textContent = "Enter the router admin password first.";
        element("credential-router-status").textContent = status.textContent;
        showMessage(status.textContent, "error");
        openCredentials("router", button);
        return;
      }
      if (routerPasswordBlocked(button)) return;
      routerActionPending = true;
      if (path === "/api/router/wifi") routerWifiMinimumCheck = Date.now() / 1000;
      ["sync-router-ap", "router-wifi-toggle"].forEach((id) => { element(id).disabled = true; });
      setButtonBusy(button, true);
      updateCredentialStatus();
      status.textContent = message;
      element("credential-router-status").textContent = message;
      showMessage(message, "working");
      try {
        if (path === "/api/router/sync-ap") {
          const previewResponse = await routerRequest("/api/router/preview-ap", { admin_password: password });
          const preview = previewResponse.result;
          if (!stillCurrent()) return;
          status.textContent = "Router Wi-Fi uses " + preview.security + " on " + preview.band + ".";
          const warning = preview.legacy ? " Older WPA is weak security. Copy it only for compatibility testing." : "";
          const copyConfirmed = await confirmWorkflow("Copy " + preview.ssid + " using " + preview.security + " (" + preview.band + ")?" + warning + " The Pi will use the router's Wi-Fi password. " + (typeof WIFI_RESTART_NOTICE === "string" ? WIFI_RESTART_NOTICE : "") + " Keep a separate management connection.",
            {title: "Copy the router Wi-Fi to the Pi?", confirmLabel: "Copy Wi-Fi", returnFocus: button});
          // The page keeps running while the question is open, so the credentials may have changed.
          if (!stillCurrent()) return;
          if (!copyConfirmed) {
            status.textContent = "Wi-Fi security checked. Copy cancelled.";
            showMessage(status.textContent, "success");
            return;
          }
          body = { ...body, expected_fingerprint: preview.fingerprint, confirm_legacy: preview.legacy };
        }
        const response = await routerRequest(path, { admin_password: password, ...body });
        if (!stillCurrent()) return;
        const result = response.result || response;
        if (typeof result.enabled === "boolean") {
          routerWifiEnabled = result.enabled;
          routerWifiCheckedAt = Date.now() / 1000;
          routerWifiError = "";
        }
        status.textContent = result.ssid
          ? "Copied " + result.ssid + " using " + result.security + ". Join with the router's Wi-Fi password."
          : (result.enabled ? "Router Wi-Fi is on." : "Router Wi-Fi is off.");
        if (typeof showWifiResult !== "function") {
          // The reconnect script is not part of this page.
        } else if (result.ssid) {
          showWifiResult({state: "succeeded", kind: "router-copy",
            message: "Copied " + result.ssid + " using " + result.security + ".", ssid: result.ssid});
        } else if (reconnectHappened) {
          showWifiBanner("success", "Router Wi-Fi change finished", [status.textContent].concat(
            result.enabled ? [] : ["If this Mac was using the router's Wi-Fi, join the Pi's test Wi-Fi network."]),
            [{label: "Dismiss", run: hideWifiBanner}]);
        }
        if (typeof clearReconnectFlag === "function") clearReconnectFlag();
        element("credential-router-status").textContent = status.textContent;
        showMessage(status.textContent, "success");
        if (passwordRevision === routerPasswordRevision) {
          routerPasswordVerified = true;
          routerPasswordRejected = false;
        }
      } catch (error) {
        if (!stillCurrent()) return;
        if (path === "/api/router/wifi") { routerWifiEnabled = null; routerWifiError = error.message; }
        status.textContent = error.message;
        element("credential-router-status").textContent = status.textContent;
        showMessage(error.message, "error");
        if (passwordRevision === routerPasswordRevision && isRouterLoginFailure(error)) {
          routerPasswordVerified = false;
          routerPasswordRejected = true;
        }
      } finally {
        if (stillCurrent() && typeof clearReconnectFlag === "function") clearReconnectFlag();
        routerActionPending = false;
        ["sync-router-ap", "router-wifi-toggle"].forEach((id) => { element(id).disabled = false; });
        setButtonBusy(button, false);
        updateCredentialStatus();
      }
    }

    function routerPasswordBlocked(button) {
      if (!routerPasswordRejected) return false;
      const text = "The router rejected this password. Change it before trying again.";
      element("credential-router-status").textContent = text;
      showMessage(text, "error");
      openCredentials("router", button);
      return true;
    }

    function isRouterLoginFailure(error) {
      return error.status === 502 && /^Router login failed/.test(error.message);
    }

    async function checkRouterPassword() {
      if (routerActionPending || routerPasswordChecking) return;
      const button = element("check-router-password");
      const status = element("credential-router-status");
      if (!requireAdminToken(button)) return;
      const passwordRevision = routerPasswordRevision;
      const password = element("router-admin-password").value;
      if (!password) {
        status.textContent = "Enter the router admin password first.";
        showMessage(status.textContent, "error");
        openCredentials("router", button);
        return;
      }
      if (routerPasswordBlocked(button)) return;
      if (routerCheckWaiting()) return;
      const routerButtons = ["sync-router-ap", "router-wifi-toggle"];
      const wasVerified = routerPasswordVerified;
      routerPasswordChecking = true;
      routerPasswordVerified = false;
      routerPasswordRejected = false;
      routerButtons.forEach((id) => { element(id).disabled = true; });
      setButtonBusy(button, true);
      button.textContent = "Checking...";
      updateCredentialStatus();
      status.textContent = "Signing in to the router...";
      showMessage("Checking the router password...", "working");
      try {
        await routerRequest("/api/router/check", { admin_password: password });
        if (passwordRevision !== routerPasswordRevision) return;
        routerPasswordVerified = true;
        // Only Check password calls this so a remembered password gets a new 24 hours.
        syncRememberedCredentials("router");
        status.textContent = "Router password accepted. Nothing on the router was changed.";
        showMessage(status.textContent, "success");
      } catch (error) {
        if (passwordRevision !== routerPasswordRevision) return;
        const tooSoon = error.status === 429 && error.retryAfter > 0;
        routerPasswordRejected = isRouterLoginFailure(error);
        if (tooSoon) {
          // Nothing reached the router, so what the last check said still stands.
          routerPasswordVerified = wasVerified;
          routerCheckWaitUntil = Date.now() + Math.ceil(error.retryAfter) * 1000;
        }
        status.textContent = error.message;
        showMessage(error.message, tooSoon ? "info" : "error");
      } finally {
        routerPasswordChecking = false;
        routerButtons.forEach((id) => { element(id).disabled = false; });
        setButtonBusy(button, false);
        button.textContent = "Check password";
        updateCredentialStatus();
        if (routerCheckWaiting()) tickRouterCheckWait();
      }
    }

    element("check-router-password").addEventListener("click", checkRouterPassword);
    element("refresh-router-wifi").addEventListener("click", pollRouterWifi);

    element("sync-router-ap").addEventListener("click", (event) => {
      routerAction("/api/router/sync-ap", {}, "Checking the router Wi-Fi security...", event.currentTarget);
    });

    // The router Wi-Fi switch is read only (renderRouterWifi keeps it disabled and the Pi refuses
    // router Wi-Fi writes), so it has no click handler and nothing to confirm.

    document.addEventListener("visibilitychange", () => {
      renderRouterWifi();
    });

    element("random-website-device").addEventListener("change", askRandomWebsiteChoices);

    element("random-website-days").addEventListener("change", askRandomWebsiteChoices);

    element("random-website-refresh").addEventListener("click", askRandomWebsiteChoices);

    // The line under the table is no live region, as every save reads it again. A choice made here
    // asks for the answer, so the answer is said once.
    async function askRandomWebsiteChoices() {
      const asking = loadRandomWebsiteAnalytics();
      const version = randomWebsiteAnalyticsRequestVersion;
      await asking;
      // A later choice says its own answer, and the line may be showing its loading note.
      if (version !== randomWebsiteAnalyticsRequestVersion) return;
      const status = element("random-website-status");
      announce(status.textContent, status.classList.contains("error"));
    }

    // Each look is planned when the last one is over, so a slow Pi is never asked twice at
    // once. A hidden tab plans nothing and looks again as soon as it is shown.
    function makePoller(job, delay) {
      let timer = null;
      let running = false;

      function schedule(wait) {
        if (timer !== null) window.clearTimeout(timer);
        timer = null;
        if (document.hidden) return;
        timer = window.setTimeout(run, wait);
      }

      async function run() {
        timer = null;
        if (running || document.hidden) return;
        running = true;
        try {
          await job();
        } catch (error) {
          // The job shows its own errors. The next look is planned all the same.
        } finally {
          running = false;
          schedule(delay);
        }
      }

      document.addEventListener("visibilitychange", () => {
        if (document.hidden) schedule(0);
        else run();
      });
      schedule(delay);
      return run;
    }

    const LIVE_POLL_MS = 5000;

    // Reads the live status once, or only the part named. A hidden tab skips the state.
    function pollLiveStatus(only) {
      if (!adminTokenVerified || networkActionBusy) return Promise.resolve([]);
      const jobs = [];
      if (!only || only === "devices") jobs.push(loadConnectedDevices());
      if (!only || only === "general") jobs.push(loadPrivateDataPanel("general-profile-status", loadGeneralProfile));
      // Another tab or the command line can reset the network so the state is read again.
      if ((!only || only === "state") && !document.hidden) jobs.push(loadPrivateDataPanel("state", loadState));
      return Promise.allSettled(jobs);
    }

    // The three are polled apart so that one the Pi does not answer does not hold back the others.
    makePoller(() => pollLiveStatus("devices"), LIVE_POLL_MS);

    makePoller(() => pollLiveStatus("general"), LIVE_POLL_MS);

    makePoller(() => pollLiveStatus("state"), LIVE_POLL_MS);

    function slugify(value) {
      return value.toLowerCase()
        .replace(/[^a-z0-9]+/g, "-")
        .replace(/^-+/, "")
        .slice(0, 64)
        .replace(/-+$/, "");
    }

    function bindAutoId(nameId, idId) {
      const nameInput = element(nameId);
      const idInput = element(idId);
      idInput.addEventListener("input", () => {
        idInput.dataset.typed = idInput.value ? "true" : "";
      });
      nameInput.addEventListener("input", () => {
        if (idInput.readOnly || idInput.dataset.typed === "true") return;
        idInput.value = slugify(nameInput.value);
      });
    }

    const IMPAIRMENT_FEATURES = [
      "delay", "jitter", "loss", "bandwidth", "reorder", "duplicate", "corrupt"
    ];

    const DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

    function formatBandwidth(kbit) {
      if (kbit < 1000) return kbit + " kbit/s";
      return (kbit / 1000).toFixed(kbit % 1000 ? 1 : 0) + " Mbit/s";
    }

    function describeImpairment(values) {
      const parts = [];
      if (values.delay_ms || values.jitter_ms) {
        const jitter = values.jitter_ms ? " ± " + values.jitter_ms + " ms" : "";
        parts.push(values.delay_ms + " ms" + jitter);
      }
      if (values.loss_percent) {
        const bursts = values.loss_burst_packets > 1
          ? " in bursts of " + values.loss_burst_packets
          : "";
        parts.push(values.loss_percent + "% loss" + bursts);
      }
      if (values.bandwidth_kbit !== null) parts.push(formatBandwidth(values.bandwidth_kbit));
      if (values.reorder_percent) parts.push(values.reorder_percent + "% reorder");
      if (values.duplicate_percent) parts.push(values.duplicate_percent + "% duplicate");
      if (values.corrupt_percent) parts.push(values.corrupt_percent + "% corrupt");
      return parts.length ? parts.join(" · ") : "Unchanged";
    }

    function describeExtras(profile) {
      const parts = [];
      if (profile.reroll_interval_seconds) {
        parts.push("rerolls every " + profile.reroll_interval_seconds + " s");
      }
      if (profile.outage_probability) {
        parts.push(
          profile.outage_probability + "% outage chance, " +
          profile.outage_duration_seconds + " s long"
        );
      }
      if (profile.website_mode === "allow_only") parts.push("allows only listed sites");
      return parts.length ? parts.join(" · ") : "—";
    }

    function hasAdvancedSettings(profile) {
      const advancedImpairment = (values) => Boolean(
        values.reorder_percent || values.duplicate_percent || values.corrupt_percent ||
        IMPAIRMENT_FEATURES.some((feature) => {
          const randomness = values[feature + "_randomness"];
          return randomness.variation_percent !== 0 || randomness.enabled_probability !== 100;
        })
      );
      return Boolean(
        profile.homepage_bounce_percent || profile.reroll_interval_seconds || profile.outage_probability ||
        profile.random_mode === "fresh" || profile.random_seed !== null || profile.website_mode !== "unrestricted" ||
        profile.website_domains.length ||
        advancedImpairment(profile.upload) || advancedImpairment(profile.download)
      );
    }

    function tableCell(value) {
      const cell = document.createElement("td");
      if (Array.isArray(value)) {
        const primary = document.createElement("strong");
        primary.textContent = value[0];
        const secondary = document.createElement("small");
        secondary.textContent = value[1];
        cell.append(primary, secondary);
      } else {
        cell.textContent = value;
      }
      return cell;
    }

    function renderTable(containerId, emptyText, headings, rows) {
      const container = element(containerId);
      container.replaceChildren();
      if (!rows.length) {
        container.textContent = emptyText;
        return;
      }
      const table = document.createElement("table");
      table.className = "data-table";
      const headRow = document.createElement("tr");
      headings.concat([""]).forEach((heading) => {
        const cell = document.createElement("th");
        cell.scope = "col";
        if (heading) {
          cell.textContent = heading;
        } else {
          // The last column holds row buttons. Give it a name for screen readers without showing it.
          const hidden = document.createElement("span");
          hidden.className = "visually-hidden";
          hidden.textContent = "Actions";
          cell.appendChild(hidden);
        }
        headRow.appendChild(cell);
      });
      const head = document.createElement("thead");
      head.appendChild(headRow);
      const body = document.createElement("tbody");
      rows.forEach(({ cells, actions, rowClass, key }) => {
        const row = document.createElement("tr");
        if (rowClass) row.className = rowClass;
        if (key) row.dataset.key = key;
        cells.forEach((value) => row.appendChild(tableCell(value)));
        const actionCell = document.createElement("td");
        const actionGroup = document.createElement("div");
        actionGroup.className = "row-actions";
        actions.forEach(([label, onClick]) => {
          const button = document.createElement("button");
          button.type = "button";
          button.textContent = label;
          button.addEventListener("click", () => onClick(button));
          actionGroup.appendChild(button);
        });
        actionCell.appendChild(actionGroup);
        row.appendChild(actionCell);
        body.appendChild(row);
      });
      table.append(head, body);
      container.appendChild(table);
    }

    function profileName(profileId) {
      const profile = profiles.find((candidate) => candidate.id === profileId);
      return profile ? profile.name : profileId;
    }

    function deviceName(deviceId) {
      const device = devices.find((candidate) => candidate.id === deviceId);
      return device ? deviceTitle(device) : deviceId;
    }

    function scheduleTargetFields(value) {
      if (value === "everyone-else") {
        return { device_id: null, group_id: null, everyone_else: true };
      }
      const separator = value.indexOf(":");
      const kind = value.slice(0, separator);
      const id = value.slice(separator + 1);
      return {
        device_id: kind === "device" ? id : null,
        group_id: kind === "group" ? id : null,
        everyone_else: false
      };
    }

    function scheduleTargetValue(target) {
      if (target.everyone_else) return "everyone-else";
      return target.group_id ? "group:" + target.group_id : "device:" + target.device_id;
    }

    function scheduleTargetName(target) {
      if (target.everyone_else) return "Everyone else";
      if (target.group_id) {
        const group = groups.find((candidate) => candidate.id === target.group_id);
        return "Group " + (group ? group.name : target.group_id);
      }
      return deviceName(target.device_id);
    }

    function scheduleTargetOptions() {
      return devices.map((device) => ({
        group: "Devices",
        value: "device:" + device.id,
        label: devicePickerLabel(device)
      })).concat(
        groups.map((group) => ({ group: "Groups", value: "group:" + group.id, label: group.name })),
        [{ group: null, value: "everyone-else", label: "Everyone else" }]
      );
    }

    function renderTargetMenus() {
      ["schedule-device", "scenario-start-target"].forEach((selectId) => {
        const select = element(selectId);
        const selected = select.value;
        const optionGroups = {};
        select.replaceChildren();
        scheduleTargetOptions().forEach((choice) => {
          const option = document.createElement("option");
          option.value = choice.value;
          option.textContent = choice.label;
          if (choice.group && !optionGroups[choice.group]) {
            optionGroups[choice.group] = document.createElement("optgroup");
            optionGroups[choice.group].label = choice.group;
            select.appendChild(optionGroups[choice.group]);
          }
          (choice.group ? optionGroups[choice.group] : select).appendChild(option);
        });
        // Rebuilding the menu must not change a choice being edited.
        if (Array.from(select.options).some((option) => option.value === selected)) {
          select.value = selected;
        }
      });
    }

    function followEveryoneElseProfile(data) {
      // A schedule can change everyone else's profile at any minute, and the
      // connected-client list brings the current one with every poll.
      if ("everyone_else_profile_id" in data) {
        everyoneElse = { ...everyoneElse, active_profile_id: data.everyone_else_profile_id };
      }
    }

    function everyoneElseAssigned() {
      return Boolean(everyoneElse.profile_id) ||
        savedSchedules.some((schedule) => schedule.everyone_else && schedule.enabled);
    }

    function everyoneElseInLiveTraffic() {
      // A running scenario shapes everyone else too, but only while it runs,
      // so Getting started leaves it out.
      return everyoneElseAssigned() || runningScenarios.some((run) => run.everyone_else);
    }

    function renderProfileTable() {
      renderTable(
        "profile-table",
        "No profiles saved yet.",
        ["Profile", "Upload", "Download", "Extras"],
        profiles.map((profile) => ({
          cells: [
            [profile.name, profile.id],
            describeImpairment(profile.upload),
            describeImpairment(profile.download),
            describeExtras(profile)
          ],
          key: profile.id,
          actions: [
            ["Edit", () => fillProfileForm(profile)],
            ["Duplicate", () => duplicateProfile(profile)],
            ["Delete", (button) => deleteProfile(profile, button)]
          ]
        }))
      );
    }

    const CONDITION_LABELS = { normal: "Normal", degraded: "Degraded", offline: "Offline" };

    function conditionCell(state) {
      if (!state) return "—";
      const reasons = Array.isArray(state.reasons) ? state.reasons : [];
      const label = CONDITION_LABELS[state.condition] || "—";
      return [label, reasons.join("; ") || "No network effects"];
    }

    function deviceConditionCell(deviceId) {
      // Own keys only: a device may be called "constructor".
      const known = Object.prototype.hasOwnProperty.call(deviceConditions.devices, deviceId);
      return conditionCell(known ? deviceConditions.devices[deviceId] : null);
    }

    function appliedProfileCell(savedProfile, state) {
      if (!deviceConditions.applied || !state || !state.source) {
        return [savedProfile, "Saved profile"];
      }
      const source = state.source;
      const names = {
        profile: "Device profile", "group-profile": "Group profile",
        schedule: "Schedule", timer: "Timer", scenario: "Scenario",
        progression: "Progression", "data-limit": "Data limit",
        "group-data-limit": "Shared data limit", none: "Normal connection"
      };
      const details = [names[source.kind] || source.kind, source.label, source.step]
        .filter(Boolean).join(" · ");
      const next = state.next_change_at
        ? " · Next planned change: " + new Date(state.next_change_at).toLocaleString()
        : "";
      return [source.profile || source.label || "Normal connection", details + next];
    }

    function everyoneElseRow() {
      const condition = deviceConditions.everyone_else || null;
      if (!everyoneElse.profile_id && !condition) return null;
      return {
        cells: [
          ["Everyone else", "Test Wi-Fi clients that aren't saved or protected"],
          ["Any address", everyoneElse.unaffected_devices.length + " protected"],
          appliedProfileCell(
            everyoneElse.profile_id ? profileName(everyoneElse.profile_id) : "None", condition
          ),
          conditionCell(condition)
        ],
        actions: [],
        key: "everyone-else"
      };
    }

    // A saved profile that is paused because the device is protected or cannot be
    // checked against the Protected list is never shown as applied or degraded.
    function deviceIsPaused(device) {
      return !!device && (device.effective_state === "protected" || device.effective_state === "unverified");
    }

    function deviceProfileCell(device) {
      if (deviceIsPaused(device)) return [profileName(device.profile_id), "Saved profile · paused"];
      return appliedProfileCell(profileName(device.profile_id),
        Object.prototype.hasOwnProperty.call(deviceConditions.devices, device.id)
          ? deviceConditions.devices[device.id] : null);
    }

    function deviceStateCell(device) {
      if (device.effective_state === "protected") {
        return ["Protected: profile paused", "Protected from profiles · Traffic is not monitored"];
      }
      if (device.effective_state === "unverified") {
        return ["Profile paused", device.effective_note ||
          "This saved device has no MAC address so the Pi cannot check it against the Protected list."];
      }
      return deviceConditionCell(device.id);
    }

    function deviceRowClass(device) {
      return device.effective_state === "protected" ? "protected"
        : device.effective_state === "unverified" ? "paused" : "";
    }

    async function saveObservedMac(device, button) {
      const match = device.protected_match;
      if (!match) return;
      setButtonBusy(button, true);
      try {
        await request("/api/devices", "POST", {
          id: device.id, name: device.name, ipv4_address: device.ipv4_address,
          profile_id: device.profile_id, mac_address: match.mac_address,
          expected: {
            id: device.id, name: device.name, ipv4_address: device.ipv4_address,
            profile_id: device.profile_id, mac_address: device.mac_address
          }
        });
        refreshAfterSuccess("Saved the MAC address. " + (match.name || "This device") + " is now protected.");
      } catch (error) {
        showMessage(error.message, "error");
      } finally {
        setButtonBusy(button, false);
      }
    }

    function renderDeviceTable() {
      const rows = devices.map((device) => ({
        rowClass: deviceRowClass(device),
        key: device.id,
        cells: [
          [deviceDisplayName(device), device.id],
          [device.ipv4_address, device.mac_address || "No MAC address"],
          deviceProfileCell(device),
          deviceStateCell(device)
        ],
        actions: [
          ["Open controls", () => openDevice(device.id)],
          ["Edit", () => fillDeviceForm(device)],
          ...(device.protected_match
            ? [["Save MAC " + device.protected_match.mac_address,
                (button) => saveObservedMac(device, button)]]
            : []),
          ["Delete", (button) => deleteDevice(device, button)]
        ]
      }));
      const others = everyoneElseRow();
      if (others) rows.push(others);
      renderTable(
        "device-table",
        "No devices assigned yet.",
        ["Device", "Address", "Profile", "Condition"],
        rows
      );
      renderDeviceCards();
    }

    function fillImpairment(prefix, values) {
      element(prefix + "-delay").value = values.delay_ms;
      element(prefix + "-jitter").value = values.jitter_ms;
      element(prefix + "-loss").value = values.loss_percent;
      element(prefix + "-loss-burst").value = values.loss_burst_packets || 0;
      element(prefix + "-bandwidth").value =
        values.bandwidth_kbit === null ? "" : values.bandwidth_kbit;
      element(prefix + "-reorder").value = values.reorder_percent;
      element(prefix + "-duplicate").value = values.duplicate_percent;
      element(prefix + "-corrupt").value = values.corrupt_percent;
      IMPAIRMENT_FEATURES.forEach((feature) => {
        const randomness = values[feature + "_randomness"];
        element(prefix + "-" + feature + "-random-variation").value = randomness.variation_percent;
        element(prefix + "-" + feature + "-random-enabled").value = randomness.enabled_probability;
      });
    }

    const DEFAULT_PROFILE_RANDOMNESS = {
      reroll_interval_randomness: { variation_percent: 0, enabled_probability: 100 },
      outage_duration_randomness: { variation_percent: 0, enabled_probability: 100 },
      outage_interval_randomness: { variation_percent: 0, enabled_probability: 100 },
      website_randomness: { variation_percent: 0, enabled_probability: 100 }
    };

    let savedProfileRandomness = DEFAULT_PROFILE_RANDOMNESS;

    function fillProfileValues(profile) {
      savedProfileRandomness = {
        reroll_interval_randomness: profile.reroll_interval_randomness,
        outage_duration_randomness: profile.outage_duration_randomness,
        outage_interval_randomness: profile.outage_interval_randomness,
        website_randomness: profile.website_randomness
      };
      fillImpairment("upload", profile.upload);
      fillImpairment("download", profile.download);
      element("profile-seed").value = profile.random_seed === null ? "" : profile.random_seed;
      element("profile-random-mode").value = profile.random_mode || "repeatable";
      element("profile-reroll").value = profile.reroll_interval_seconds;
      element("profile-reroll-variation").value =
        profile.reroll_interval_randomness.variation_percent;
      element("profile-homepage-chance").value = profile.homepage_bounce_percent || 0;
      element("profile-outage-probability").value = profile.outage_probability;
      element("profile-outage-duration").value = profile.outage_duration_seconds;
      element("profile-outage-interval").value = profile.outage_interval_seconds;
      element("profile-website-mode").value = profile.website_mode;
      element("profile-website-domains").value = profile.website_domains.join(", ");
      element("profile-website-enabled").value = profile.website_randomness.enabled_probability;
      element("profile-form").querySelector("details").open = hasAdvancedSettings(profile);
    }

    const PROFILE_PRESETS = [
      {
        name: "Good 4G",
        upload: { delay_ms: 25, jitter_ms: 5, bandwidth_kbit: 10000 },
        download: { delay_ms: 25, jitter_ms: 5, bandwidth_kbit: 30000 }
      },
      {
        name: "Congested 4G",
        upload: { delay_ms: 70, jitter_ms: 40, loss_percent: 1, bandwidth_kbit: 1000 },
        download: { delay_ms: 70, jitter_ms: 40, loss_percent: 1, bandwidth_kbit: 3000 }
      },
      {
        name: "3G",
        upload: { delay_ms: 100, jitter_ms: 20, bandwidth_kbit: 330 },
        download: { delay_ms: 100, jitter_ms: 20, bandwidth_kbit: 780 }
      },
      {
        name: "EDGE (2G)",
        upload: { delay_ms: 440, jitter_ms: 60, loss_percent: 1, bandwidth_kbit: 200 },
        download: { delay_ms: 400, jitter_ms: 60, loss_percent: 1, bandwidth_kbit: 240 }
      },
      {
        name: "Satellite",
        upload: {
          delay_ms: 300, jitter_ms: 20, loss_percent: 1, loss_burst_packets: 3,
          bandwidth_kbit: 2000
        },
        download: {
          delay_ms: 300, jitter_ms: 20, loss_percent: 1, loss_burst_packets: 3,
          bandwidth_kbit: 10000
        }
      },
      {
        name: "Lossy Wi-Fi",
        upload: { delay_ms: 5, jitter_ms: 15, loss_percent: 5, loss_burst_packets: 3 },
        download: { delay_ms: 5, jitter_ms: 15, loss_percent: 5, loss_burst_packets: 3 }
      },
      {
        name: "Train tunnel",
        upload: {
          delay_ms: 200, jitter_ms: 80, loss_percent: 3, loss_burst_packets: 5, bandwidth_kbit: 500
        },
        download: {
          delay_ms: 200, jitter_ms: 80, loss_percent: 3, loss_burst_packets: 5, bandwidth_kbit: 1000
        },
        outage_probability: 30,
        outage_duration_seconds: 10,
        outage_interval_seconds: 60
      },
      {
        name: "Very bad network",
        upload: {
          delay_ms: 500, jitter_ms: 100, loss_percent: 10, loss_burst_packets: 2,
          bandwidth_kbit: 1000
        },
        download: {
          delay_ms: 500, jitter_ms: 100, loss_percent: 10, loss_burst_packets: 2,
          bandwidth_kbit: 1000
        }
      },
      {
        name: "Offline",
        upload: { loss_percent: 100 },
        download: { loss_percent: 100 }
      }
    ];

    function presetImpairment(values) {
      const impairment = Object.assign({
        delay_ms: 0,
        jitter_ms: 0,
        loss_percent: 0,
        loss_burst_packets: 0,
        bandwidth_kbit: null,
        reorder_percent: 0,
        duplicate_percent: 0,
        corrupt_percent: 0
      }, values);
      IMPAIRMENT_FEATURES.forEach((feature) => {
        impairment[feature + "_randomness"] = { variation_percent: 0, enabled_probability: 100 };
      });
      return impairment;
    }

    function applyPreset(preset) {
      fillProfileValues({
        ...DEFAULT_PROFILE_RANDOMNESS,
        upload: presetImpairment(preset.upload),
        download: presetImpairment(preset.download),
        random_seed: null,
        reroll_interval_seconds: 0,
        outage_probability: preset.outage_probability || 0,
        outage_duration_seconds: preset.outage_duration_seconds || 0,
        outage_interval_seconds: preset.outage_interval_seconds || 0,
        website_mode: "unrestricted",
        website_domains: []
      });
      // Keep the name of a profile that is being edited.
      if (!element("profile-id").readOnly) {
        element("profile-name").value = preset.name;
        element("profile-name").dispatchEvent(new Event("input"));
      }
      showMessage(preset.name + " loaded. Adjust it if needed, then save the profile.", "success");
    }

    function fillDeviceForm(device) {
      if (guardDraft("device-form", () => fillDeviceForm(device))) return;
      stopEditing("device");
      editingDeviceSnapshot = { ...device };
      element("device-id").value = device.id;
      element("device-name").value = device.name;
      element("device-ip").value = device.ipv4_address;
      element("device-profile").value = device.profile_id;
      element("device-mac").value = device.mac_address || "";
      startEditing("device", deviceTitle(device));
    }

    async function deleteItem(path, body, successMessage, button) {
      setButtonBusy(button, true);
      showMessage("Deleting...", "working");
      try {
        await request(path, "POST", body);
        refreshAfterSuccess(successMessage);
      } catch (error) {
        showMessage(error.message, "error");
      } finally {
        setButtonBusy(button, false);
      }
    }

    async function deleteProfile(profile, button) {
      if (!await confirmFromButton(button, "Delete the profile " + profile.name + "?",
          {title: "Delete this profile?", confirmLabel: "Delete profile", danger: true})) return;
      await deleteItem("/api/profiles/delete", { id: profile.id }, "Profile deleted.", button);
    }

    async function deleteDevice(device, button) {
      const question = "Delete " + deviceTitle(device) + "? Its schedules, timers, progressions, " +
        "limits and browser service profiles are deleted too, and so is a group it is the " +
        "only member of.";
      if (!await confirmFromButton(button, question,
          {title: "Delete this device?", confirmLabel: "Delete device", danger: true})) return;
      await deleteItem("/api/devices/delete", { id: device.id }, "Device deleted.", button);
    }

    function formatMinute(minute) {
      const hours = String(Math.floor(minute / 60)).padStart(2, "0");
      return hours + ":" + String(minute % 60).padStart(2, "0");
    }

    function formatDays(days) {
      const sorted = [...days].sort((first, second) => first - second);
      if (sorted.length === 7) return "Every day";
      const ranges = [];
      sorted.forEach((day) => {
        const last = ranges[ranges.length - 1];
        if (last && day === last[1] + 1) last[1] = day;
        else ranges.push([day, day]);
      });
      return ranges.map(([start, end]) => {
        if (start === end) return DAY_NAMES[start];
        return DAY_NAMES[start] + (end === start + 1 ? ", " : "–") + DAY_NAMES[end];
      }).join(", ");
    }

    function editSchedule(schedule) {
      if (guardDraft("schedule-form", () => editSchedule(schedule))) return;
      element("schedule-id").value = schedule.id;
      element("schedule-device").value = scheduleTargetValue(schedule);
      element("schedule-profile").value = schedule.profile_id;
      document.querySelectorAll(".day-toggle").forEach((button) => {
        const selected = schedule.days_of_week.includes(Number(button.dataset.day));
        button.setAttribute("aria-pressed", String(selected));
      });
      syncScheduleDays();
      element("schedule-start").value = formatMinute(schedule.start_minute);
      element("schedule-end").value = formatMinute(schedule.end_minute);
      element("schedule-enabled").checked = schedule.enabled;
      element("schedule-form").scrollIntoView({
        behavior: typeof scrollBehavior === "function" ? scrollBehavior() : "smooth", block: "center"
      });
    }

    let gatewayPersistenceSaved = false;

    let gatewayPersistenceDirty = false;

    let gatewayPersistenceBusy = false;

    let devices = [];

    let editingDeviceSnapshot = null;

    let deviceConditions = { applied: false, devices: {} };

    let everyoneElse = { profile_id: null, unaffected_devices: [] };

    let savedSchedules = [];

    let savedScenarios = [];

    let runningScenarios = [];

    const ACTIVITY_EMPTY_TEXT = "No events yet. Network changes appear here as they happen.";

    let latestActivity = [];

    let activityKey = null;

    let scenarioRunKey = "";

    let scenarioListKey = "";

    let dashboardLoaded = false;

    let gettingStartedKey = "";

    const EVERYONE_ELSE_TRAFFIC = "__everyone_else__";

    const EVERYONE_ELSE_QUEUE_ID = "rule:everyone-else";

    function selectedQueueTotals(queues) {
      const selectedDevice = element("traffic-device").value;
      const figures = selectedDevice === "@all"
        ? Object.keys(queues).filter((id) => id !== EVERYONE_ELSE_TRAFFIC).map((id) => queues[id])
        : queues[selectedDevice] ? [queues[selectedDevice]] : [];
      return figures.reduce((totals, current) => ({
        dropped: totals.dropped + current.upload_dropped + current.download_dropped,
        queued: totals.queued + current.upload_queued + current.download_queued
      }), { dropped: 0, queued: 0 });
    }

    let measurementRuns = [];

    const MEASUREMENT_COLUMNS = [
      "Run", "Target", "Sent", "Loss", "RTT avg", "RTT p95", "Jitter", "Reordered", "Download"
    ];

    const MEASUREMENT_COLORS = ["#70b7ff", "#ffa76c", "#70d7a9", "#edc36a", "#c58cff", "#ff8fa3"];

    const ESCAPED_PIPE = "\x5c|";

    function measurementMilliseconds(value) {
      return value === null || value === undefined ? "—" : value.toFixed(2) + " ms";
    }

    function measurementRow(run) {
      const summary = run.summary;
      return [
        String(run.label ?? ""),
        String(run.target ?? ""),
        String(summary.sent ?? ""),
        (summary.loss_percent ?? 0).toFixed(1) + "%",
        measurementMilliseconds(summary.rtt_avg_ms),
        measurementMilliseconds(summary.rtt_p95_ms),
        measurementMilliseconds(summary.jitter_ms),
        String(summary.reordered ?? 0),
        run.download ? run.download.mbit_per_s.toFixed(2) + " Mbit/s" : "—"
      ];
    }

    function measurementMarkdown(runs) {
      const line = (cells) => "| " + cells.join(" | ") + " |";
      return [
        line(MEASUREMENT_COLUMNS),
        line(MEASUREMENT_COLUMNS.map(() => "---")),
        ...runs.map((run) => line(
          measurementRow(run).map((cell) => cell.split("|").join(ESCAPED_PIPE))
        ))
      ].join("\n") + "\n";
    }

    function validMeasurement(run) {
      const isNumber = (value) => typeof value === "number" && Number.isFinite(value);
      const isCount = (value) => Number.isInteger(value) && value >= 0;
      if (
        !run || typeof run !== "object" || run.format !== "jittersim-measurement" ||
        run.version !== 1 || typeof run.label !== "string" || typeof run.target !== "string"
      ) {
        return false;
      }
      const summary = run.summary;
      if (
        !summary || typeof summary !== "object" || !isCount(summary.sent) ||
        !isNumber(summary.loss_percent) || !isCount(summary.reordered ?? 0) ||
        ["rtt_avg_ms", "rtt_p95_ms", "jitter_ms"].some(
          (key) => summary[key] !== null && summary[key] !== undefined && !isNumber(summary[key])
        )
      ) {
        return false;
      }
      const samples = run.samples ?? [];
      if (
        !Array.isArray(samples) ||
        samples.some((sample) => !sample || typeof sample !== "object" || !isNumber(sample.rtt_ms))
      ) {
        return false;
      }
      const download = run.download;
      return download === undefined || download === null ||
        (typeof download === "object" && isNumber(download.mbit_per_s));
    }

    function drawMeasurementChart() {
      const canvas = element("measurement-chart");
      const bounds = canvas.getBoundingClientRect();
      if (!bounds.width || !bounds.height) return;
      const pixelRatio = window.devicePixelRatio || 1;
      canvas.width = Math.round(bounds.width * pixelRatio);
      canvas.height = Math.round(bounds.height * pixelRatio);
      const context = canvas.getContext("2d");
      if (!context) return;
      context.setTransform(pixelRatio, 0, 0, pixelRatio, 0, 0);
      context.clearRect(0, 0, bounds.width, bounds.height);
      context.font = "12px system-ui, sans-serif";
      context.fillStyle = "#91a8c1";
      const rtts = measurementRuns.flatMap(
        (run) => (run.samples || []).map((sample) => sample.rtt_ms)
      );
      if (!rtts.length) {
        context.textAlign = "center";
        context.textBaseline = "middle";
        const message = measurementRuns.length
          ? "No replies to draw: every ping was lost."
          : "Add result files to draw their round trips.";
        context.fillText(message, bounds.width / 2, bounds.height / 2);
        return;
      }
      const scaleMax = Math.max(...rtts) * 1.1;
      // Enough decimals that the five labels differ, even below a millisecond.
      const decimals = Math.max(0, Math.ceil(-Math.log10(scaleMax / 4)));
      const labels = [0, 1, 2, 3, 4].map((tick) => (scaleMax * tick / 4).toFixed(decimals) + " ms");
      const labelWidth = Math.max(...labels.map((label) => context.measureText(label).width));
      const plot = {
        left: Math.ceil(labelWidth) + 16,
        right: bounds.width - 14,
        top: 16,
        bottom: bounds.height - 30
      };
      context.textBaseline = "middle";
      labels.forEach((label, tick) => {
        const y = plot.bottom - (plot.bottom - plot.top) * tick / 4;
        context.strokeStyle = "#263c56";
        context.lineWidth = 1;
        context.beginPath();
        context.moveTo(plot.left, y);
        context.lineTo(plot.right, y);
        context.stroke();
        context.textAlign = "right";
        context.fillText(label, plot.left - 8, y);
      });
      const longest = Math.max(2, ...measurementRuns.map((run) => (run.samples || []).length));
      measurementRuns.forEach((run, index) => {
        const color = MEASUREMENT_COLORS[index % MEASUREMENT_COLORS.length];
        const points = (run.samples || []).map((sample, position) => [
          plot.left + (plot.right - plot.left) * position / (longest - 1),
          plot.bottom - (plot.bottom - plot.top) * sample.rtt_ms / scaleMax
        ]);
        context.strokeStyle = color;
        context.lineWidth = 2;
        context.beginPath();
        points.forEach(([x, y], position) => {
          if (position === 0) context.moveTo(x, y);
          else context.lineTo(x, y);
        });
        context.stroke();
        // A dot for every reply, so a run with a single reply still shows.
        context.fillStyle = color;
        points.forEach(([x, y]) => {
          context.beginPath();
          context.arc(x, y, 2, 0, 2 * Math.PI);
          context.fill();
        });
      });
      context.fillStyle = "#91a8c1";
      context.textAlign = "center";
      context.textBaseline = "alphabetic";
      context.fillText(
        "Replies in the order they arrived", (plot.left + plot.right) / 2, bounds.height - 8
      );
    }

    function renderMeasurements() {
      const container = element("measurement-table");
      container.replaceChildren();
      element("copy-measurements").disabled = !measurementRuns.length;
      element("clear-measurements").disabled = !measurementRuns.length;
      if (!measurementRuns.length) {
        container.textContent = "Add result files to compare runs.";
      } else {
        const table = document.createElement("table");
        table.className = "data-table";
        const headRow = document.createElement("tr");
        MEASUREMENT_COLUMNS.forEach((heading) => {
          const cell = document.createElement("th");
          cell.scope = "col";
          cell.textContent = heading;
          headRow.appendChild(cell);
        });
        const head = document.createElement("thead");
        head.appendChild(headRow);
        const body = document.createElement("tbody");
        measurementRuns.forEach((run, index) => {
          const row = document.createElement("tr");
          measurementRow(run).forEach((value, column) => {
            const cell = document.createElement("td");
            if (column === 0) {
              // The run's color in the chart.
              const key = document.createElement("i");
              key.className = "measurement-key";
              key.style.background = MEASUREMENT_COLORS[index % MEASUREMENT_COLORS.length];
              cell.appendChild(key);
            }
            cell.append(value);
            row.appendChild(cell);
          });
          body.appendChild(row);
        });
        table.append(head, body);
        container.appendChild(table);
      }
      drawMeasurementChart();
    }

    async function loadMeasurementFiles(files) {
      const loaded = [];
      for (const file of files) {
        let run;
        try {
          run = JSON.parse(await file.text());
        } catch (error) {
          showMessage(file.name + " is not valid JSON.", "error");
          return;
        }
        if (!validMeasurement(run)) {
          showMessage(file.name + " is not a measurement file.", "error");
          return;
        }
        loaded.push(run);
      }
      measurementRuns = measurementRuns.concat(loaded);
      renderMeasurements();
      const added = loaded.length + (loaded.length === 1 ? " run added." : " runs added.");
      showMessage(added, "success");
    }

    function mergedTrafficCounters(data) {
      // Everyone else is one more choice in the menu, under a reserved key.
      const devices = Object.assign({}, data.devices || {});
      const queues = Object.assign({}, data.queues || {});
      if (data.everyone_else) devices[EVERYONE_ELSE_TRAFFIC] = data.everyone_else;
      if (data.everyone_else_queue) queues[EVERYONE_ELSE_TRAFFIC] = data.everyone_else_queue;
      return { devices: devices, queues: queues };
    }

    function gettingStartedSteps(state) {
      const assigned = state.devices > 0 || Boolean(state.everyoneElseProfile);
      const steps = [
        {
          text: "Create a profile, or start from a preset",
          tab: "profiles",
          done: state.profiles > 0
        },
        {
          text: "Connect a device to the test Wi-Fi",
          tab: null,
          // Saved settings count too, so the list doesn't return when clients leave.
          done: state.connected > 0 || assigned
        },
        {
          text: "Give it a profile, or choose one for everyone else",
          tab: "devices",
          done: assigned
        }
      ];
      // Configuration-only mode can't apply anything. Any network action counts,
      // so a later Reset doesn't bring the list back.
      if (state.canApply) {
        steps.push({ text: "Start", tab: null, done: Boolean(state.lastAction) });
      }
      return steps;
    }

    function renderGettingStarted() {
      // Before everything has loaded, saved settings would look missing.
      if (!dashboardLoaded) return;
      const connected = lastConnectedDevices && Array.isArray(lastConnectedDevices.devices)
        ? lastConnectedDevices.devices.length
        : 0;
      const steps = gettingStartedSteps({
        profiles: profiles.length,
        devices: devices.length,
        connected: connected,
        everyoneElseProfile: everyoneElseAssigned(),
        lastAction: lastRuntimeState ? lastRuntimeState.last_action : null,
        canApply: helperConfigured
      });
      element("getting-started").hidden = steps.every((step) => step.done);
      const list = element("getting-started-steps");
      const key = JSON.stringify(steps.map((step) => [step.text, step.done]));
      // Rebuilding on every poll would take keyboard focus away from a link.
      if (key === gettingStartedKey || list.contains(document.activeElement)) return;
      gettingStartedKey = key;
      list.replaceChildren();
      steps.forEach((step, index) => {
        const item = document.createElement("li");
        if (step.done) item.className = "done";
        const mark = document.createElement("span");
        mark.className = "step-mark";
        mark.textContent = step.done ? "✓" : String(index + 1);
        item.appendChild(mark);
        if (step.tab && !step.done) {
          const link = document.createElement("a");
          link.href = "#" + step.tab;
          link.textContent = step.text;
          link.addEventListener("click", (event) => {
            // The list is out of sight once the tab changes, so focus moves with it.
            if (followTabLink(event, step.tab)) tabPanel(step.tab).focus();
          });
          item.appendChild(link);
        } else {
          item.appendChild(document.createTextNode(step.text));
        }
        list.appendChild(item);
      });
    }

    const CONNECTED_PICKERS = ["unaffected-picker", "device-picker", "mac-picker"];

    function connectedDeviceOptions(devices) {
      return (devices || [])
        .filter((device) => device.mac_address)
        .map((device) => ({
          value: device.mac_address,
          label: [
            device.display_name || device.ipv4_address || device.mac_address,
            device.ipv4_address || "no IP address yet",
            device.mac_address
          ].join(" · ")
        }));
    }

    function pickedDeviceFields(device) {
      const name = device.saved_display_name || device.hostname ||
        "Client " + (device.ipv4_address || device.mac_address);
      return {
        name: name,
        ipv4_address: device.ipv4_address || "",
        mac_address: device.mac_address
      };
    }

    function addMacToList(listText, macAddress) {
      const entries = listText.split(",").map((value) => value.trim()).filter(Boolean);
      const listed = entries.some((entry) => entry.toLowerCase() === macAddress.toLowerCase());
      return (listed ? entries : entries.concat([macAddress])).join(", ");
    }

    function renderConnectedPickers() {
      const data = lastConnectedDevices || { available: false, devices: [] };
      const options = connectedDeviceOptions(data.available ? data.devices : []);
      CONNECTED_PICKERS.forEach((pickerId) => {
        const picker = element(pickerId);
        // Rebuilding an open menu would close it under the pointer.
        if (document.activeElement === picker) return;
        const selected = picker.value;
        picker.replaceChildren();
        const placeholder = document.createElement("option");
        placeholder.value = "";
        if (!data.available) placeholder.textContent = "Connected devices are unavailable";
        else if (!options.length) placeholder.textContent = "No devices are connected";
        else placeholder.textContent = "Choose a connected device...";
        picker.appendChild(placeholder);
        options.forEach((choice) => {
          const option = document.createElement("option");
          option.value = choice.value;
          option.textContent = choice.label;
          picker.appendChild(option);
        });
        picker.value = options.some((choice) => choice.value === selected) ? selected : "";
      });
    }

    function pickedConnectedDevice(pickerId) {
      const devices = (lastConnectedDevices && lastConnectedDevices.devices) || [];
      return devices.find((device) => device.mac_address === element(pickerId).value) || null;
    }

    function fillUnaffectedFromPicker() {
      const device = pickedConnectedDevice("unaffected-picker");
      if (!device) return;
      element("unaffected-mac").value = device.mac_address;
      element("unaffected-name").value = device.saved_display_name || device.hostname || "";
    }

    function fillDeviceFromPicker() {
      const device = pickedConnectedDevice("device-picker");
      if (!device) return;
      const fields = pickedDeviceFields(device);
      element("device-name").value = fields.name;
      // The ID follows the name unless it was typed by hand or is being edited.
      element("device-name").dispatchEvent(new Event("input"));
      element("device-ip").value = fields.ipv4_address;
      element("device-mac").value = fields.mac_address;
    }

    function addPickedMac(listId) {
      const device = pickedConnectedDevice("mac-picker");
      if (!device) {
        const available = Boolean(lastConnectedDevices && lastConnectedDevices.available);
        showMessage(available
          ? "Choose a connected device first."
          : "Connected devices are unavailable, so type the address in the list.", "error");
        return;
      }
      element(listId).value = addMacToList(element(listId).value, device.mac_address);
    }

    function connectedTreatment(device) {
      const id = device.assigned_device_id;
      if (typeof deviceConditions !== "undefined" && deviceConditions.applied && id && Object.prototype.hasOwnProperty.call(deviceConditions.devices, id)) {
        return "Active: " + appliedProfileCell(profileName(device.assigned_profile_id), deviceConditions.devices[id]).join(" · ");
      }
      if (device.assigned_profile_id) {
        const saved = "Saved device: " + profileName(device.assigned_profile_id);
        // A saved device keeps its own profile even when its address is listed.
        return device.unaffected ? saved + " (also on the protected list)" : saved;
      }
      if (device.unaffected) return "Protected";
      // The profile everyone else gets this minute, which a schedule can set.
      if (everyoneElse.active_profile_id) {
        return "Everyone else: " + profileName(everyoneElse.active_profile_id);
      }
      return "Normal connection";
    }

    function savedChangeMessage(message, result) {
      if (result.live_applied !== false) return message;
      return helperConfigured
        ? message + " The network is reset, so choose Start to use it."
        : message + " It takes effect when the dashboard runs in hardware mode.";
    }

    async function keepUnaffected(macAddress, name, button) {
      if (button) setButtonBusy(button, true);
      showMessage("Adding " + macAddress + " to the protected list...", "working");
      try {
        const result = await request("/api/unaffected-devices", "POST", {
          mac_address: macAddress,
          name: name
        });
        refreshAfterSuccess(savedChangeMessage(
          "Added " + result.unaffected_device.name + " to the protected list.", result
        ));
        return true;
      } catch (error) {
        showMessage(error.message, "error");
        return false;
      } finally {
        if (button) setButtonBusy(button, false);
      }
    }

    function removeUnaffected(macAddress, button) {
      deleteItem(
        "/api/unaffected-devices/delete",
        { mac_address: macAddress },
        "Removed from the protected list.",
        button
      );
    }

    function rerenderConnectedDevices() {
      // Client labels name the everyone-else profile. Leave an open editor alone.
      const container = element("connected-devices");
      const activeElement = document.activeElement;
      const editing = activeElement && container.contains(activeElement) &&
        activeElement.matches("input, select");
      if (lastConnectedDevices && !editing) renderConnectedDevices(lastConnectedDevices);
    }

    async function loadEveryoneElse() {
      everyoneElse = await request("/api/everyone-else", "GET");
      element("everyone-else-profile").value = everyoneElse.profile_id || "";
      renderDeviceTable();
      rerenderConnectedDevices();
      updateTrafficDeviceOptions(devices);
    }

    function renderGatewayPersistence(data) {
      const toggle = element("gateway-persistence-toggle");
      const saveButton = element("gateway-persistence-save");
      const status = element("gateway-persistence-state");
      gatewayPersistenceSaved = data.persistence_enabled === true;
      if (gatewayPersistenceDirty && toggle.checked === gatewayPersistenceSaved) {
        gatewayPersistenceDirty = false;
      }
      if (!gatewayPersistenceDirty) toggle.checked = gatewayPersistenceSaved;
      saveButton.disabled = gatewayPersistenceBusy || toggle.checked === gatewayPersistenceSaved;
      status.className = "token-check-status";
      if (data.state_error) {
        status.textContent = "The networking helper could not confirm a safe gateway state. Check its service logs.";
        status.classList.add("error");
      } else if (data.outage_stop_latched) {
        status.textContent = "Profiles are stopped after a router outage. Choose Apply after the router is reachable to start them again.";
      } else if (data.recovery_pending) {
        status.textContent = "The router is back. The active profiles are being restored.";
      } else if (data.gateway_state === "unavailable" && data.mode_enabled === true) {
        status.textContent = "The router is unreachable. Active profiles are being kept in place.";
      } else if (data.gateway_state === "unavailable") {
        status.textContent = "The router is unreachable. The helper is applying the saved outage behavior.";
      } else if (data.gateway_state === "available") {
        status.textContent = "Router reachable. Saved mode: " + (gatewayPersistenceSaved ? "on" : "off") + ".";
      } else {
        status.textContent = data.helper_available === false
          ? "Gateway checks are available in hardware mode. The saved choice will be used when hardware mode runs."
          : "Router reachability is unknown. The Pi needs a default gateway before it can check.";
      }
      if (data.helper_synced === false && data.helper_available === true && !data.state_error) {
        status.textContent += " The saved choice has not reached the helper yet.";
      }
    }

    async function loadGatewayPersistence() {
      try {
        renderGatewayPersistence(await request("/api/gateway-persistence", "GET"));
      } catch (error) {
        const status = element("gateway-persistence-state");
        status.textContent = "Gateway persistence status is unavailable: " + error.message;
        status.className = "token-check-status error";
      }
    }

    async function saveGatewayPersistence() {
      const toggle = element("gateway-persistence-toggle");
      const button = element("gateway-persistence-save");
      const status = element("gateway-persistence-state");
      gatewayPersistenceBusy = true;
      setButtonBusy(button, true);
      toggle.disabled = true;
      status.textContent = "Saving router outage behavior...";
      status.className = "token-check-status";
      try {
        const result = await request("/api/gateway-persistence", "POST", {
          persistence_enabled: toggle.checked
        });
        gatewayPersistenceDirty = false;
        renderGatewayPersistence(result);
        status.textContent = result.message;
        status.className = "token-check-status " + (result.helper_synced ? "success" : "error");
        showMessage(result.message, result.helper_synced ? "success" : "error");
      } catch (error) {
        status.textContent = error.message;
        status.className = "token-check-status error";
        showMessage(error.message, "error");
      } finally {
        gatewayPersistenceBusy = false;
        toggle.disabled = false;
        setButtonBusy(button, false);
        button.disabled = !gatewayPersistenceDirty && toggle.checked === gatewayPersistenceSaved;
      }
    }

    function formatDuration(seconds) {
      const hours = Math.floor(seconds / 3600);
      const minutes = Math.floor((seconds % 3600) / 60);
      const rest = seconds % 60;
      return [
        hours ? hours + " h" : "",
        minutes ? minutes + " min" : "",
        rest ? rest + " s" : ""
      ].filter(Boolean).join(" ") || "0 s";
    }

    function scenarioSummary(scenario) {
      const steps = scenario.steps.map((step) =>
        profileName(step.profile_id) + " " + formatDuration(step.duration_seconds)
      ).join(" → ");
      return scenario.loop ? steps + ", repeating" : steps;
    }

    function scenarioRunText(run) {
      const ending = run.loop
        ? "; repeats until stopped"
        : "; ends in " + formatDuration(run.total_remaining_seconds);
      return {
        title: run.target_name + " · " + run.scenario_name,
        detail: "Step " + (run.step_index + 1) + " of " + run.step_count + ": " +
          run.profile_name + ", " + formatDuration(run.step_remaining_seconds) +
          " left in this step" + ending
      };
    }

    function scenarioStepRow(step) {
      const profileLabels = profileOptionLabels(profiles);
      const row = document.createElement("div");
      row.className = "scenario-step";
      const profileLabel = document.createElement("label");
      profileLabel.textContent = "Profile";
      const profileSelect = document.createElement("select");
      profileSelect.className = "scenario-step-profile";
      profileSelect.required = true;
      profiles.forEach((profile) => {
        const option = document.createElement("option");
        option.value = profile.id;
        option.textContent = profileLabels.get(profile.id);
        profileSelect.appendChild(option);
      });
      if (step.profile_id) profileSelect.value = step.profile_id;
      profileLabel.appendChild(profileSelect);
      const fields = [
        ["Minutes", "minutes", 1440, Math.floor(step.duration_seconds / 60)],
        ["Seconds", "seconds", 59, step.duration_seconds % 60]
      ].map(([text, kind, max, value]) => {
        const label = document.createElement("label");
        label.textContent = text;
        const input = document.createElement("input");
        input.className = "scenario-step-" + kind;
        input.type = "number";
        input.min = "0";
        input.max = String(max);
        input.value = String(value);
        label.appendChild(input);
        return label;
      });
      const removeButton = document.createElement("button");
      removeButton.type = "button";
      removeButton.textContent = "Remove step";
      removeButton.addEventListener("click", () => {
        // A scenario needs at least one step.
        if (element("scenario-steps").children.length < 2) return;
        row.remove();
        renumberScenarioSteps();
        element("scenario-add-step").focus();
      });
      const actions = document.createElement("div");
      actions.className = "scenario-step-actions";
      [["up", "Move up"], ["down", "Move down"], ["duplicate", "Duplicate"]].forEach(([action, label]) => {
        const button = document.createElement("button");
        button.type = "button";
        button.dataset.stepAction = action;
        button.textContent = label;
        button.addEventListener("click", () => {
          const container = element("scenario-steps");
          if (action === "up" && row.previousElementSibling) container.insertBefore(row, row.previousElementSibling);
          if (action === "down" && row.nextElementSibling) container.insertBefore(row.nextElementSibling, row);
          if (action === "duplicate" && container.children.length < 20) {
            const index = Array.from(container.children).indexOf(row);
            const copy = scenarioStepRow(scenarioStepsFromForm()[index]);
            row.after(copy);
            copy.querySelector("select").focus();
          }
          renumberScenarioSteps();
        });
        actions.appendChild(button);
      });
      removeButton.dataset.stepAction = "remove";
      actions.appendChild(removeButton);
      row.append(profileLabel, ...fields, actions);
      return row;
    }

    function renumberScenarioSteps() {
      // Every row repeats the same labels, so name each field after its step.
      Array.from(element("scenario-steps").children).forEach((row, index) => {
        const step = "Step " + (index + 1);
        const [profileLabel, minutesLabel, secondsLabel] = row.children;
        profileLabel.children[0].setAttribute("aria-label", step + " profile");
        minutesLabel.children[0].setAttribute("aria-label", step + " minutes");
        secondsLabel.children[0].setAttribute("aria-label", step + " seconds");
        row.querySelectorAll("[data-step-action]").forEach((button) => {
          const action = button.dataset.stepAction;
          button.setAttribute("aria-label", (action === "remove" ? "Remove" : button.textContent) + " step " + (index + 1));
          const count = element("scenario-steps").children.length;
          button.disabled = (action === "up" && index === 0) ||
            (action === "down" && index === count - 1) ||
            (action === "remove" && count === 1) || (action === "duplicate" && count >= 20);
        });
      });
      element("scenario-add-step").disabled = element("scenario-steps").children.length >= 20;
      updateScenarioDuration();
    }

    function updateScenarioDuration() {
      const steps = scenarioStepsFromForm();
      const total = steps.reduce((seconds, step) => seconds + step.duration_seconds, 0);
      setTextIfChanged(element("scenario-duration"), steps.length + (steps.length === 1 ? " step · " : " steps · ") +
        (Number.isFinite(total) ? formatDuration(total) : "Check step durations") +
        (element("scenario-loop").checked ? " per repeat" : " total"));
    }

    function renderScenarioSteps(steps) {
      element("scenario-steps").replaceChildren(...steps.map(scenarioStepRow));
      renumberScenarioSteps();
    }

    function scenarioStepsFromForm() {
      const rows = element("scenario-steps").querySelectorAll(".scenario-step");
      return Array.from(rows).map((row) => ({
        profile_id: row.querySelector(".scenario-step-profile").value,
        duration_seconds: Number(row.querySelector(".scenario-step-minutes").value || 0) * 60 +
          Number(row.querySelector(".scenario-step-seconds").value || 0)
      }));
    }

    function editScenario(scenario) {
      if (guardDraft("scenario-form", () => editScenario(scenario))) return;
      element("scenario-id").value = scenario.id;
      element("scenario-name").value = scenario.name;
      element("scenario-loop").checked = scenario.loop;
      renderScenarioSteps(scenario.steps);
      element("scenario-form").scrollIntoView({
        behavior: typeof scrollBehavior === "function" ? scrollBehavior() : "smooth", block: "center"
      });
    }

    function renderScenarioRuns(runs) {
      const list = element("scenario-run-list");
      const key = runs.map((run) => run.id).join(",");
      element("scenario-count").textContent = runs.length
        ? runs.length + " running"
        : "None running";
      if (key && key === scenarioRunKey) {
        // Update the text in place, so a focused Stop button keeps its focus.
        runs.forEach((run, index) => {
          const text = scenarioRunText(run);
          const row = list.children[index];
          row.querySelector(".timer-details strong").textContent = text.title;
          row.querySelector(".timer-details span").textContent = text.detail;
        });
        return;
      }
      scenarioRunKey = key;
      list.replaceChildren();
      if (!runs.length) {
        list.textContent = "No running scenarios.";
        return;
      }
      runs.forEach((run) => {
        const text = scenarioRunText(run);
        const row = document.createElement("div");
        row.className = "timer-row";
        row.dataset.key = run.id;
        const details = document.createElement("div");
        details.className = "timer-details";
        const title = document.createElement("strong");
        title.textContent = text.title;
        const detail = document.createElement("span");
        detail.textContent = text.detail;
        details.append(title, detail);
        const stopButton = document.createElement("button");
        stopButton.type = "button";
        stopButton.textContent = "Stop";
        stopButton.addEventListener("click", () => stopScenario(run.id, stopButton));
        row.append(details, stopButton);
        list.appendChild(row);
      });
    }

    function renderSavedScenarios() {
      // Polls with the same scenarios leave the list and the open menu alone.
      const key = JSON.stringify([savedScenarios, profiles.map((profile) => profile.name)]);
      if (key === scenarioListKey) return;
      scenarioListKey = key;
      const list = element("scenario-list");
      list.replaceChildren();
      if (!savedScenarios.length) list.textContent = "No scenarios yet.";
      savedScenarios.forEach((scenario) => {
        const row = document.createElement("div");
        row.className = "schedule-row";
        row.dataset.key = scenario.id;
        const text = document.createElement("div");
        const name = document.createElement("span");
        name.textContent = scenario.name;
        const summary = document.createElement("small");
        summary.textContent = scenarioSummary(scenario) + " · " + scenario.id;
        text.append(name, summary);
        const actions = document.createElement("div");
        actions.className = "row-actions";
        const editButton = document.createElement("button");
        editButton.type = "button";
        editButton.textContent = "Edit";
        editButton.addEventListener("click", () => editScenario(scenario));
        const deleteButton = document.createElement("button");
        deleteButton.type = "button";
        deleteButton.textContent = "Delete";
        deleteButton.addEventListener("click", () => deleteItem(
          "/api/scenarios/delete", { id: scenario.id }, "Scenario deleted.", deleteButton
        ));
        actions.append(editButton, deleteButton);
        row.append(text, actions);
        list.appendChild(row);
      });
      const select = element("scenario-start-scenario");
      const selected = select.value;
      select.replaceChildren();
      savedScenarios.forEach((scenario) => {
        const option = document.createElement("option");
        option.value = scenario.id;
        option.textContent = scenario.name;
        select.appendChild(option);
      });
      if (savedScenarios.some((scenario) => scenario.id === selected)) select.value = selected;
    }

    async function loadScenarios() {
      try {
        const data = await request("/api/scenarios", "GET");
        const hadEveryoneElse = runningScenarios.some((run) => run.everyone_else);
        savedScenarios = data.scenarios;
        runningScenarios = data.runs;
        renderSavedScenarios();
        renderScenarioRuns(data.runs);
        // Live traffic offers Everyone else while its scenario runs.
        if (runningScenarios.some((run) => run.everyone_else) !== hadEveryoneElse) {
          updateTrafficDeviceOptions(devices);
        }
      } catch (error) {
        recordRead("/api/scenarios", error);
      }
    }

    async function stopScenario(runId, button) {
      setButtonBusy(button, true);
      showMessage("Stopping the scenario...", "working");
      try {
        const result = await request("/api/scenarios/stop", "POST", { id: runId });
        refreshAfterSuccess(savedChangeMessage("Scenario stopped.", result));
      } catch (error) {
        showMessage(error.message, "error");
      } finally {
        setButtonBusy(button, false);
      }
    }

    function formatActivityTime(seconds) {
      const moment = new Date(seconds * 1000);
      const pad = (value) => String(value).padStart(2, "0");
      return moment.getFullYear() + "-" + pad(moment.getMonth() + 1) + "-" +
        pad(moment.getDate()) + " " + pad(moment.getHours()) + ":" +
        pad(moment.getMinutes()) + ":" + pad(moment.getSeconds());
    }

    function activityMarkdown(events) {
      // Oldest first, as a report reads. A cell can't hold a line break, and a
      // device name must not format the report: backslashes are escaped first,
      // so they can't undo the escapes after them.
      const backslash = String.fromCharCode(92);
      const cell = (text) => ["|", "*", "_", "`", "~", "<", ">", "[", "]"].reduce(
        (escaped, mark) => escaped.split(mark).join(backslash + mark),
        text.split(backslash).join(backslash + backslash)
      ).replace(/\s+/g, " ");
      const zone = Intl.DateTimeFormat().resolvedOptions().timeZone || "local time";
      const rows = events.slice().reverse().map((event) => "| " +
        formatActivityTime(event.happened_at) + " | " + cell(event.message) + " |");
      return ["| Time (" + zone + ") | Event |", "| --- | --- |"].concat(rows).join("\n");
    }

    function filteredActivity() {
      const kind = element("activity-kind").value;
      const search = element("activity-search").value.trim().toLocaleLowerCase();
      return latestActivity.filter((event) => (!kind || event.kind === kind) &&
        (!search || event.message.toLocaleLowerCase().includes(search)));
    }

    function renderActivity(events) {
      latestActivity = events;
      events = filteredActivity();
      element("activity-count").textContent = events.length + " of " + latestActivity.length + " events";
      const empty = element("activity-empty");
      empty.textContent = latestActivity.length ? "No events match these filters." : ACTIVITY_EMPTY_TEXT;
      empty.hidden = events.length > 0;
      const list = element("activity-list");
      list.replaceChildren();
      events.forEach((event) => {
        const item = document.createElement("li");
        const time = document.createElement("time");
        time.dateTime = new Date(event.happened_at * 1000).toISOString();
        time.textContent = formatActivityTime(event.happened_at);
        const message = document.createElement("span");
        message.textContent = event.message;
        item.append(time, message);
        (event.device_ids || []).forEach(id => {
          const device = devices.find(candidate => candidate.id === id);
          if (device) item.append(actionButton("Open " + deviceTitle(device), () => openDevice(id)));
        });
        list.appendChild(item);
      });
    }

    async function loadActivity() {
      try {
        const latest = await request("/api/activity/latest", "GET");
        const key = latest.newest_id + ":" + latest.count;
        // Polls without a new event, or a clear, leave the list and where it is
        // scrolled alone, and skip downloading it again.
        if (key === activityKey) return;
        renderActivity((await request("/api/activity", "GET")).events);
        activityKey = key;
      } catch (error) {
        recordRead("/api/activity/latest", error);
      }
    }

    async function copyActivity() {
      const markdown = activityMarkdown(filteredActivity());
      const box = element("activity-markdown");
      try {
        await navigator.clipboard.writeText(markdown);
        box.hidden = true;
        showMessage("Copied the activity log as Markdown.", "success");
      } catch (error) {
        // Browsers open the clipboard only to secure pages, so offer the text.
        box.value = markdown;
        box.hidden = false;
        box.focus();
        box.select();
        showMessage("The Markdown is selected below. Copy it with Ctrl+C or ⌘C.", "success");
      }
    }

    function downloadActivity() {
      const url = URL.createObjectURL(new Blob([activityMarkdown(filteredActivity())], { type: "text/markdown;charset=utf-8" }));
      const link = document.createElement("a");
      link.href = url;
      link.download = "jittersim-activity.md";
      document.body.appendChild(link);
      link.click();
      link.remove();
      window.setTimeout(() => URL.revokeObjectURL(url), 1000);
    }

    async function clearActivity(button) {
      if (!await confirmFromButton(button, "Clear every event, including those hidden by filters? This can't be undone.",
          {title: "Clear the activity log?", confirmLabel: "Clear all events", danger: true})) return;
      try {
        await request("/api/activity/clear", "POST", {});
        element("activity-markdown").hidden = true;
        await loadActivity();
        showMessage("Activity log cleared.", "success");
      } catch (error) {
        showMessage(error.message, "error");
      } finally {
        setButtonBusy(button, false);
      }
    }

    async function loadConditions() {
      try {
        deviceConditions = await request("/api/conditions", "GET");
        element("device-conditions-note").textContent = deviceConditions.applied
          ? "Profiles and conditions show what the network applies now. Planned times use your browser clock. Traffic limits and outages may change conditions sooner."
          : "Conditions show the saved settings. The network does not apply them yet.";
      } catch (error) {
        recordRead("/api/conditions", error);
        element("device-conditions-note").textContent = "Out of date: " + error.message;
      }
      renderDeviceTable();
      refreshConnectedTreatments();
    }

    let dashboardRefresh = null;

    let dashboardPoll = null;

    let refreshAgain = false;

    const TAB_PAGES = {
      overview: "devices",
      devices: "devices",
      profiles: "profiles",
      rules: "schedules",
      measurements: "measurements",
      setup: "router",
      diagnostics: "diagnostics",
      updates: "updates"
    };

    function tabPanel(name) {
      return element("page-" + (TAB_PAGES[name] || name));
    }

    function currentPage() {
      return document.body.dataset.section || "devices";
    }

    function showTab(name) {
      const page = TAB_PAGES[name] || name;
      showPage(page, true);
      if (window.location.hash !== "#" + page) window.history.replaceState(null, "", "#" + page);
    }

    function showRuleSection(name) {
      showTab(name === "access" ? "access" : "rules");
    }

    function followTabLink(event, name) {
      // A modified click opens the page in a new browser tab instead.
      if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey ||
          event.altKey) {
        return false;
      }
      event.preventDefault();
      const page = TAB_PAGES[name] || name;
      // pushState keeps Back working without jumping to an element with that ID.
      if (window.location.hash !== "#" + page) window.history.pushState(null, "", "#" + page);
      showPage(page, true);
      return true;
    }

    function noticeAnchor() {
      return element("mode-notice");
    }

    // The page is read again in parts. A save names the parts it can have changed, through the
    // paths it wrote, and the page being shown is always read too. Signing in, importing,
    // restoring a version and any save this table does not know read everything.
    const READ_AFTER_WRITE = [
      // Starting, stopping and the general profile change what is running.
      [/^\/api\/(apply|reset|normal-access|general-profile)(?:\/|$)/,
        ["state", "general", "conditions", "plan", "health", "connected", "activity", "uplink"]],
      // A profile is offered as "Add to" on the observed names, so those are read too.
      [/^\/api\/profiles(?:\/|$)/, ["profiles", "general", "plan", "state", "conditions", "everyoneElse",
        "devicesForSchedule", "devices", "scenarios", "serviceAssignments", "observations",
        "activity", "settingsHistory"]],
      // A protected device is no longer watched, so its observed names go with it.
      [/^\/api\/(devices|unaffected-devices|groups|connected-devices)(?:\/|$)/,
        ["devicesForSchedule", "devices", "groups", "plan", "state", "conditions", "connected",
         "unaffected", "observations", "activity", "settingsHistory"]],
      [/^\/api\/(schedules|timers|progressions|scenarios|data-limits|group-data-limits|time-quotas|service-quotas|everyone-else)(?:\/|$)/,
        ["schedules", "timers", "progressions", "scenarios", "quotas", "everyoneElse", "plan",
         "state", "conditions", "devicesForSchedule", "activity", "settingsHistory"]],
      [/^\/api\/(service-lists|service-assignments|observations)(?:\/|$)/,
        ["serviceLists", "serviceAssignments", "profiles", "plan", "observations", "activity",
         "settingsHistory"]]
      // Device names (connected-device-labels) are not listed: they show on most pages, so a
      // save of one reads everything.
    ];

    function readsAfterWrites(paths) {
      if (!paths.length) return null;
      const names = new Set();
      for (const path of paths) {
        const known = READ_AFTER_WRITE.find(([pattern]) => pattern.test(path));
        if (!known) return null;
        known[1].forEach((name) => names.add(name));
      }
      return names;
    }

    // The diagnostics text and the website choices cost several reads and sit on two pages.
    const ADMIN_TOOLS_PAGES = ["traffic", "diagnostics"];

    let adminToolsStale = false;
    let adminToolsGeneration = 0;
    let adminToolsRead = null;

    function invalidateAdminTools() {
      adminToolsGeneration += 1;
      adminToolsStale = true;
    }

    function readAdminToolsOnce() {
      if (adminToolsRead) return adminToolsRead;
      const generation = adminToolsGeneration;
      const revision = adminTokenRevision;
      const pending = (async () => {
        try {
          const result = await loadAdminTools();
          if (revision === adminTokenRevision && adminTokenVerified && generation === adminToolsGeneration) {
            adminToolsStale = !result || result.ok === false || result.refreshComplete === false;
          }
          return result;
        } catch (error) {
          if (revision === adminTokenRevision) adminToolsStale = true;
          throw error;
        } finally {
          if (adminToolsRead === pending) adminToolsRead = null;
        }
      })();
      adminToolsRead = pending;
      return pending;
    }

    async function refreshDashboard(only) {
      // only is null for everything, otherwise the names of the parts to read again.
      const page = currentPage();
      const pageJobs = only
        ? new Set([...pagePollJobs(page), ...(ADMIN_TOOLS_PAGES.includes(page) ? [loadAdminTools] : [])])
        : null;
      const wanted = (name, job) => !only || only.has(name) || pageJobs.has(job);
      const run = (name, job, call) => wanted(name, job) ? call() : null;
      // A save elsewhere leaves them as they were. The first visit to either page reads them.
      if (!wanted("adminTools", loadAdminTools)) adminToolsStale = true;
      if (wanted("profiles", null)) await loadPrivateDataPanel("profiles-json", loadProfiles);
      if (wanted("health", loadHealth)) await loadHealth();
      if (wanted("observations", loadObservations)) await loadObservations();
      // Device and group names are needed by the schedule list and the device table.
      if (wanted("devicesForSchedule", loadDevicesForSchedule)) await loadDevicesForSchedule();
      const results = await Promise.allSettled([
        run("devices", null, () => loadPrivateDataPanel("devices-json", () => show("/api/devices", "devices-json", "devices"))),
        run("plan", null, () => loadPrivateDataPanel("plan", () => show("/api/plan", "plan", "plan"))),
        run("state", null, () => loadPrivateDataPanel("state", loadState)),
        run("general", null, () => loadPrivateDataPanel("general-profile-status", loadGeneralProfile)),
        run("groups", loadGroups, loadGroups),
        run("schedules", loadSchedules, loadSchedules),
        run("timers", loadTimers, loadTimers),
        run("scenarios", loadScenarios, loadScenarios),
        run("activity", loadActivity, loadActivity),
        run("progressions", loadProgressions, loadProgressions),
        run("policies", null, () => loadPrivateDataPanel("policies", loadPolicies)),
        run("connected", loadConnectedDevices, loadConnectedDevices),
        run("unaffected", loadUnaffectedDevices, loadUnaffectedDevices),
        run("uplink", loadUplink, loadUplink),
        run("gateway", loadGatewayPersistence, loadGatewayPersistence),
        run("quotas", loadQuotas, loadQuotas),
        run("serviceAssignments", loadServiceAssignments, loadServiceAssignments),
        run("serviceLists", loadServiceLists, loadServiceLists),
        run("conditions", loadConditions, loadConditions),
        run("everyoneElse", loadEveryoneElse, loadEveryoneElse),
        run("adminTools", loadAdminTools, readAdminToolsOnce),
        run("access", loadDashboardAccessList, loadDashboardAccessList),
        run("testSessions", loadTestSessions, loadTestSessions),
        run("settingsHistory", loadSettingsHistory, loadSettingsHistory)
      ].filter((started) => started !== null));
      const failed = results.find((result) => result.status === "rejected");
      if (failed) throw failed.reason;
      // The saved update preference is read once the token has opened the page.
      // Accepted sign-in owns the first private update reads.
      dashboardLoaded = true;
      renderGettingStarted();
      populateWorkflowMenus();
      populateAccessTargets();
      await loadWorkspace();
    }

    function readOldAdminTools(page) {
      if (!adminToolsStale || !adminTokenVerified || !dashboardLoaded || document.hidden) return;
      if (!ADMIN_TOOLS_PAGES.includes(page)) return;
      return readAdminToolsOnce().catch(() => {});
    }

    function startEditing(prefix, label) {
      element(prefix + "-id").readOnly = true;
      const status = element(prefix + "-edit-status");
      if (status) status.textContent = "Editing " + label + ". Save to update it.";
      const editBar = element(prefix + "-edit-bar");
      if (editBar) editBar.hidden = false;
      revealElement(element(prefix + "-form"));
    }

    function stopEditing(prefix) {
      formDrafts.delete(prefix + "-form");
      renderDraftNotice();
      if (prefix === "device") editingDeviceSnapshot = null;
      element(prefix + "-form").reset();
      if (prefix === "profile") resetProfileEditing();
      else element(prefix + "-id").readOnly = false;
      element(prefix + "-id").dataset.typed = "";
      const editBar = element(prefix + "-edit-bar");
      if (editBar) editBar.hidden = true;
    }

    function describeConnectedRow(device, details) {
      // The device controls and the treatment line from main join the row.
      if (device.assigned_device_id) {
        const open = document.createElement("button");
        open.type = "button";
        open.textContent = "Open device controls";
        open.addEventListener("click", () => openDevice(device.assigned_device_id));
        details.appendChild(open);
      }
      const treatment = document.createElement("p");
      treatment.className = "connected-meta connected-treatment";
      treatment.dataset.mac = device.mac_address;
      treatment.textContent = connectedTreatment(device);
      details.appendChild(treatment);
    }

    function followProfileMenus() {
      // Rebuilding a menu drops its choice, and saving None would clear the profile.
      element("everyone-else-profile").value = everyoneElse.profile_id || "";
      // The step menus list the profiles too; keep the steps being edited.
      const steps = scenarioStepsFromForm();
      renderScenarioSteps(steps.length ? steps : [{ profile_id: "", duration_seconds: 60 }]);
    }

    function followSavedDevices(list) {
      devices = list;
      renderDeviceTable();
      renderTargetMenus();
    }

    // What changes by itself on each page, read every few seconds while the page is shown.
    function pagePollJobs(page) {
      const jobs = [];
      if (page === "devices") jobs.push(loadDevicesForSchedule, loadConditions, loadWorkspace, loadAccessStatus);
      if (page === "schedules") jobs.push(loadTimers, loadScenarios, loadProgressions, loadSchedules, loadQuotas, loadServiceLists);
      if (page === "traffic") jobs.push(loadObservations);
      if (page === "access") jobs.push(loadObservations, loadAccessStatus);
      if (page === "activity") jobs.push(loadActivity);
      if (page === "measurements") jobs.push(loadTestSessions);
      if (page === "router") jobs.push(loadUplink, loadGatewayPersistence);
      if (page === "updates") jobs.push(pollReleaseProgress);
      return jobs;
    }

    async function pollDashboard() {
      if (document.hidden || !adminTokenVerified || !dashboardLoaded || dashboardRefresh || dashboardPoll) return;
      dashboardPoll = (async () => {
        // Only read changing data on the visible page. Do not overwrite forms.
        const jobs = [loadHealth, ...pagePollJobs(currentPage())];
        const results = await Promise.allSettled(jobs.map((job) => job()));
        if (!unansweredPaths.size && results.some((result) => result.status === "rejected")) {
          element("runtime-summary").textContent = "Some status updates could not load. Retrying shortly.";
        }
      })();
      try {
        await dashboardPoll;
      } finally {
        dashboardPoll = null;
      }
    }

    function startDashboard() {
      // The admin token gates everything else, so only the public health check
      // and the remembered credentials run before Check token. The restore runs
      // at once so that a token typed meanwhile is never touched.
      updateCredentialViewport();
      updateCredentialStatus();
      loadHealth().catch((error) => {
        showMessage(error.message, "error");
      });
      return restoreRememberedCredentials();
    }

    [
      ["profile-name", "profile-id"],
      ["device-name", "device-id"],
      ["group-name", "group-id"],
      ["service-quota-name", "service-quota-id"],
      ["time-quota-name", "time-quota-id"]
    ].forEach(([nameId, idId]) => bindAutoId(nameId, idId));

    PROFILE_PRESETS.forEach((preset, index) => {
      const option = document.createElement("option");
      option.value = String(index);
      option.textContent = preset.name;
      element("profile-preset").appendChild(option);
    });

    element("profile-preset").addEventListener("change", (event) => {
      const preset = PROFILE_PRESETS[Number(event.target.value)];
      event.target.value = "";
      if (preset) applyPreset(preset);
    });

    element("everyone-else-form").addEventListener("submit", async (event) => {
      event.preventDefault();
      const profileId = element("everyone-else-profile").value || null;
      try {
        const result = await request("/api/everyone-else", "POST", { profile_id: profileId });
        refreshAfterSuccess(savedChangeMessage(
          profileId
            ? "Everyone else now gets " + profileName(profileId) + "."
            : "Everyone else now gets a normal connection.",
          result
        ));
      } catch (error) {
        showMessage(error.message, "error");
      }
    });

    element("activity-copy").addEventListener("click", copyActivity);

    element("activity-clear").addEventListener("click", (event) => {
      clearActivity(event.currentTarget);
    });

    element("activity-kind").addEventListener("change", () => renderActivity(latestActivity));

    element("activity-search").addEventListener("input", () => renderActivity(latestActivity));

    element("activity-download").addEventListener("click", downloadActivity);

    element("scenario-steps").addEventListener("input", updateScenarioDuration);

    element("scenario-loop").addEventListener("change", updateScenarioDuration);

    element("scenario-add-step").addEventListener("click", () => {
      if (element("scenario-steps").children.length >= 20) return;
      element("scenario-steps").appendChild(
        scenarioStepRow({ profile_id: "", duration_seconds: 60 })
      );
      renumberScenarioSteps();
    });

    element("scenario-form").addEventListener("submit", async (event) => {
      event.preventDefault();
      try {
        const result = await request("/api/scenarios", "POST", {
          id: element("scenario-id").value,
          name: element("scenario-name").value,
          steps: scenarioStepsFromForm(),
          loop: element("scenario-loop").checked
        });
        refreshAfterSuccess(savedChangeMessage("Scenario saved.", result));
      } catch (error) {
        showMessage(error.message, "error");
      }
    });

    element("scenario-start-form").addEventListener("submit", async (event) => {
      event.preventDefault();
      try {
        const result = await request("/api/scenarios/start", "POST", {
          scenario_id: element("scenario-start-scenario").value,
          ...scheduleTargetFields(element("scenario-start-target").value)
        });
        refreshAfterSuccess(savedChangeMessage("Scenario started.", result));
      } catch (error) {
        showMessage(error.message, "error");
      }
    });

    element("profile-form").addEventListener("reset", () => {
      savedProfileRandomness = DEFAULT_PROFILE_RANDOMNESS;
    });

    element("device-cancel-edit").addEventListener("click", () => stopEditing("device"));

    element("export-settings").addEventListener("click", async (event) => {
      const button = event.currentTarget;
      setButtonBusy(button, true);
      showMessage("Preparing the settings export...", "working");
      try {
        const settings = await request("/api/export", "GET");
        const file = new Blob(
          [JSON.stringify(settings, null, 2) + "\n"], { type: "application/json" }
        );
        const url = URL.createObjectURL(file);
        const link = document.createElement("a");
        link.href = url;
        link.download = "jittersim-settings.json";
        link.click();
        window.setTimeout(() => URL.revokeObjectURL(url), 1_000);
        showMessage("Settings exported.", "success");
      } catch (error) {
        showMessage(error.message, "error");
      } finally {
        setButtonBusy(button, false);
      }
    });

    element("import-settings-button").addEventListener("click", () => {
      element("import-settings").click();
    });

    element("measurement-files").addEventListener("change", async (event) => {
      const input = event.currentTarget;
      const files = Array.from(input.files);
      input.value = "";
      if (files.length) await loadMeasurementFiles(files);
    });

    element("copy-measurements").addEventListener("click", () => {
      copyReport(measurementMarkdown(measurementRuns), "Measurement table");
    });

    element("clear-measurements").addEventListener("click", () => {
      measurementRuns = [];
      renderMeasurements();
    });

    window.addEventListener("resize", drawMeasurementChart);

    renderMeasurements();

    element("import-settings").addEventListener("change", async (event) => {
      const input = event.currentTarget;
      const file = input.files[0];
      input.value = "";
      if (!file) return;
      const question = "Replace every profile, device, group, schedule, access rule and limit " +
        "with the settings in " + file.name + "?";
      const importButton = element("import-settings-button");
      if (!await confirmFromButton(importButton, question,
          {title: "Replace all settings?", confirmLabel: "Replace settings", danger: true})) return;
      try {
        showMessage("Importing settings...", "working");
        let settings;
        try {
          settings = JSON.parse(await file.text());
        } catch (error) {
          showMessage(file.name + " is not a JSON settings export.", "error");
          return;
        }
        try {
          const result = await request("/api/import", "POST", settings);
          refreshAfterSuccess(
            result.live_applied ? "Settings imported and applied." : "Settings imported."
          );
        } catch (error) {
          showMessage(error.message, "error");
        }
      } finally {
        setButtonBusy(importButton, false);
      }
    });

    element("gateway-persistence-toggle").addEventListener("change", (event) => {
      gatewayPersistenceDirty = event.currentTarget.checked !== gatewayPersistenceSaved;
      element("gateway-persistence-save").disabled = !gatewayPersistenceDirty || gatewayPersistenceBusy;
    });

    element("gateway-persistence-save").addEventListener("click", saveGatewayPersistence);

    makePoller(pollDashboard, 5000);

    element("unaffected-picker").addEventListener("change", fillUnaffectedFromPicker);

    element("device-picker").addEventListener("change", fillDeviceFromPicker);

    element("mac-add-allowed").addEventListener("click", () => addPickedMac("mac-allowlist"));

    element("mac-add-blocked").addEventListener("click", () => addPickedMac("mac-denylist"));

    document.querySelectorAll("[data-refresh-connected]").forEach((button) => {
      button.addEventListener("click", async () => {
        setButtonBusy(button, true);
        try {
          await loadConnectedDevices();
        } finally {
          setButtonBusy(button, false);
        }
      });
    });

    connectedRowHooks.push(describeConnectedRow);

    connectedRenderHooks.push(() => { renderGettingStarted(); renderConnectedPickers(); });

    connectedDataHooks.push(followEveryoneElseProfile);

    profilesLoadedHooks.push(followProfileMenus);

    runtimeStateHooks.push(() => renderGettingStarted());

    savedDevicesHooks.push(followSavedDevices);

    groupsLoadedHooks.push(renderTargetMenus);

    startDashboard();
