
    const PAGES = [
      "devices", "profiles", "schedules", "router", "traffic", "measurements", "activity",
      "access", "diagnostics", "updates"
    ];

    function pageFromHash(hash) {
      const id = String(hash || "").replace(/^#/, "");
      return PAGES.indexOf(id) === -1 ? "devices" : id;
    }

    function cardStartsOpen(saved, fallback) {
      if (saved === "open") return true;
      if (saved === "closed") return false;
      return fallback !== "closed";
    }

    // The phone layout of phone.css: a short header, a bar pinned to the bottom of the window
    // with Panic and the router Wi-Fi switch and the rest of the sidebar footer in a sheet.
    const phoneLayout = window.matchMedia("(max-width: 48rem)");
    const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");

    // A smooth scroll only when the reader has not asked for less motion.
    function scrollBehavior(reduce) {
      if (reduce === undefined) reduce = reducedMotion.matches;
      return reduce ? "auto" : "smooth";
    }

    // Where the window scrolls so that a page starts at the top, a small margin above it.
    function pageStartTop(sectionTop, scrolled, margin) {
      return Math.max(0, Math.round(sectionTop + scrolled - margin));
    }

    // Whether a scrolling row or column of links has more to show before or after.
    function moreToShow(offset, visible, total) {
      return { before: offset > 1, after: offset + visible < total - 1 };
    }

    function showPageStart(id) {
      const section = document.getElementById("page-" + id);
      if (!section || !section.getClientRects().length) {
        window.scrollTo(0, 0);
        return;
      }
      window.scrollTo({
        top: pageStartTop(section.getBoundingClientRect().top, window.scrollY, 8), behavior: "auto"
      });
      section.focus({ preventScroll: true });
    }

    function syncNavHint() {
      const nav = document.querySelector(".sidebar-nav");
      const row = nav.querySelector("ul");
      const more = phoneLayout.matches
        ? moreToShow(row.scrollLeft, row.clientWidth, row.scrollWidth)
        : moreToShow(nav.scrollTop, nav.clientHeight, nav.scrollHeight);
      nav.toggleAttribute("data-more-before", more.before);
      nav.toggleAttribute("data-more-after", more.after);
    }

    // Keeps the link of the shown page in view where the links scroll inside themselves.
    function revealNavLink(id) {
      const nav = document.querySelector(".sidebar-nav");
      const row = nav.querySelector("ul");
      const link = nav.querySelector('[data-page-link="' + id + '"]');
      if (!link) return;
      const box = link.getBoundingClientRect();
      if (row.scrollWidth > row.clientWidth + 1) {
        const rowBox = row.getBoundingClientRect();
        row.scrollLeft += box.left - rowBox.left - (rowBox.width - box.width) / 2;
      }
      if (nav.scrollHeight > nav.clientHeight + 1) {
        const navBox = nav.getBoundingClientRect();
        if (box.top < navBox.top || box.bottom > navBox.bottom) {
          nav.scrollTop += box.top - navBox.top - (navBox.height - box.height) / 2;
        }
      }
      syncNavHint();
    }

    const sidebar = document.querySelector(".sidebar");
    const controlsToggle = document.getElementById("controls-toggle");
    const controlsSheet = document.getElementById("sidebar-controls");
    const statusStrip = document.getElementById("status-strip");
    const statusBlock = document.getElementById("sidebar-status");

    function controlsOpen() {
      return sidebar.classList.contains("controls-open");
    }

    function setControlsOpen(open, moveFocus) {
      const wasOpen = controlsOpen();
      sidebar.classList.toggle("controls-open", open);
      document.body.classList.toggle("phone-controls-open", phoneLayout.matches && open);
      controlsToggle.setAttribute("aria-expanded", String(open));
      if (!moveFocus) return;
      if (open) {
        const first = Array.from(controlsSheet.querySelectorAll("select, button"))
          .find((node) => !node.disabled && !node.closest(".safety-bar") && node.getClientRects().length);
        if (first) first.focus({ preventScroll: true });
      } else if (wasOpen) {
        controlsToggle.focus({ preventScroll: true });
      }
    }

    // A tap or focus outside the sheet closes it. The bar, the toggle and what the sheet opens
    // (the credentials panel, a message, a dialog or a field tip) keep it open.
    function keepsControlsOpen(target) {
      return !target || !target.closest || controlsSheet.contains(target) ||
        controlsToggle.contains(target) ||
        Boolean(target.closest("#credentials-panel, #toast, dialog, .field-tooltip"));
    }

    function setStatusOpen(open) {
      sidebar.classList.toggle("status-open", open);
      statusStrip.setAttribute("aria-expanded", String(open));
    }

    function statusStripText(general, devices) {
      return [String(general || ""), devices]
        .filter((part) => part).join(" · ");
    }

    function syncStatusStrip() {
      statusStrip.hidden = statusBlock.hidden;
      if (statusBlock.hidden) setStatusOpen(false);
      const general = document.getElementById("status-general");
      const plain = document.getElementById("status-devices-plain");
      const devices = plain.hidden
        ? document.getElementById("status-devices-summary").textContent
        : plain.textContent;
      const text = statusStripText(general.textContent, devices);
      const label = document.getElementById("status-strip-text");
      if (label.textContent !== text) label.textContent = text;
      statusStrip.classList.toggle("status-on", general.classList.contains("status-on"));
    }

    function showPage(id, userInitiated) {
      document.querySelectorAll("[data-page]").forEach((section) => {
        section.hidden = section.dataset.page !== id;
      });
      document.querySelectorAll("[data-page-link]").forEach((link) => {
        if (link.dataset.pageLink === id) link.setAttribute("aria-current", "page");
        else link.removeAttribute("aria-current");
      });
      document.body.dataset.section = id;
      if (typeof applyBrand === "function") applyBrand(adminTokenVerified);
      if (userInitiated) {
        // On a phone the header stays above the page so the page start is shown and focused.
        if (phoneLayout.matches) {
          setControlsOpen(false, false);
          showPageStart(id);
        } else {
          window.scrollTo(0, 0);
        }
      }
      revealNavLink(id);
      // A chart drawn while its page was hidden has no size, so draw it again.
      if (typeof syncTrafficSampling === "function") syncTrafficSampling();
      if (typeof readOldAdminTools === "function") readOldAdminTools(id);
      if (id === "traffic" && typeof drawTrafficChart === "function") drawTrafficChart();
      if (id === "measurements" && typeof drawMeasurementChart === "function") drawMeasurementChart();
      if (userInitiated && typeof pollDashboard === "function") pollDashboard();
      if (typeof syncImpact === "function") syncImpact();
    }

    // The page carries only the short card name. The browser storage key keeps its old prefix so
    // saved open and closed cards stay as they were.
    function storedCardState(key) {
      try { return window.localStorage.getItem("jittersim.card." + key); } catch (error) { return null; }
    }

    function storeCardState(key, open) {
      try { window.localStorage.setItem("jittersim.card." + key, open ? "open" : "closed"); } catch (error) { /* private window */ }
    }

    function setCardOpen(card, open, remember) {
      const body = card.querySelector(":scope > .card-body");
      const toggle = card.querySelector(".card-toggle");
      if (!body || !toggle) return;
      body.hidden = !open;
      card.classList.toggle("is-collapsed", !open);
      toggle.setAttribute("aria-expanded", String(open));
      if (remember) storeCardState(card.dataset.cardKey, open);
    }

    function enhanceCollapsibleCards() {
      document.querySelectorAll(".card[data-collapse]").forEach((card, index) => {
        const title = card.querySelector("h2");
        if (!title) return;
        let heading = title;
        while (heading.parentElement !== card) heading = heading.parentElement;
        const body = document.createElement("div");
        body.className = "card-body";
        body.id = "card-body-" + index;
        while (heading.nextSibling) body.appendChild(heading.nextSibling);
        card.appendChild(body);
        const toggle = document.createElement("button");
        toggle.type = "button";
        toggle.className = "card-toggle";
        toggle.setAttribute("aria-controls", body.id);
        while (title.firstChild) toggle.appendChild(title.firstChild);
        title.appendChild(toggle);
        card.dataset.cardKey = card.dataset.cardKey || title.textContent.trim().toLowerCase().replace(/[^a-z0-9]+/g, "-");
        toggle.addEventListener("click", () => setCardOpen(card, body.hidden, true));
        setCardOpen(card, cardStartsOpen(storedCardState(card.dataset.cardKey), card.dataset.collapse), false);
      });
    }

    function revealElement(target) {
      const card = target.closest(".card[data-collapse]");
      if (card) setCardOpen(card, true, false);
      const page = target.closest("[data-page]");
      if (page && page.hidden) {
        showPage(page.dataset.page, false);
        window.history.replaceState(null, "", "#" + page.dataset.page);
      }
      const reduceMotion = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
      target.scrollIntoView({ behavior: reduceMotion ? "auto" : "smooth", block: "center" });
    }


    function syncDeviceChip() {
      const chip = document.getElementById("nav-devices-count");
      const match = /^\d+/.exec(document.getElementById("connected-count").textContent.trim());
      chip.hidden = !match;
      chip.textContent = match ? match[0] : "";
    }

    // The page follows the address when Back, Forward or a typed address change it.
    function followLocation() {
      const id = pageFromHash(window.location.hash);
      if (document.body.dataset.section !== id) showPage(id, true);
    }

    enhanceCollapsibleCards();
    showPage(pageFromHash(window.location.hash), false);
    // Page links switch the page themselves. A plain hash jump would scroll to any element
    // with that id and on a phone it would show the header again instead of the page.
    document.querySelectorAll(".sidebar-nav [data-page-link]").forEach((link) => {
      link.addEventListener("click", (event) => followTabLink(event, link.dataset.pageLink));
    });
    window.addEventListener("hashchange", followLocation);
    window.addEventListener("popstate", followLocation);
    new MutationObserver(syncDeviceChip).observe(
      document.getElementById("connected-count"), { childList: true, characterData: true, subtree: true });
    syncDeviceChip();

    controlsToggle.addEventListener("click", () => setControlsOpen(!controlsOpen(), true));
    statusStrip.addEventListener("click", () => {
      setStatusOpen(statusStrip.getAttribute("aria-expanded") !== "true");
    });
    document.addEventListener("keydown", (event) => {
      // An open question owns Escape: it answers no and leaves the Controls sheet as it is.
      if (event.key !== "Escape" || event.defaultPrevented || !controlsOpen() || document.querySelector("dialog[open]")) return;
      event.preventDefault();
      const inside = controlsSheet.contains(document.activeElement) ||
        document.activeElement === controlsToggle;
      setControlsOpen(false, inside);
    });
    document.addEventListener("pointerdown", (event) => {
      if (controlsOpen() && !keepsControlsOpen(event.target)) setControlsOpen(false, false);
    });
    document.addEventListener("focusin", (event) => {
      if (controlsOpen() && !keepsControlsOpen(event.target)) setControlsOpen(false, false);
    });
    const leavePhoneLayout = () => {
      if (!phoneLayout.matches) {
        setControlsOpen(false, false);
        setStatusOpen(false);
      }
      revealNavLink(document.body.dataset.section);
    };
    if (phoneLayout.addEventListener) phoneLayout.addEventListener("change", leavePhoneLayout);
    else phoneLayout.addListener(leavePhoneLayout);
    // A locked dashboard closes the sheet and the status. Unlocking shows the page links again.
    new MutationObserver(() => {
      if (document.body.classList.contains("dashboard-locked")) {
        setControlsOpen(false, false);
        setStatusOpen(false);
      } else {
        revealNavLink(document.body.dataset.section);
      }
    }).observe(document.body, { attributes: true, attributeFilter: ["class"] });
    document.querySelector(".sidebar-nav ul").addEventListener("scroll", syncNavHint, { passive: true });
    document.querySelector(".sidebar-nav").addEventListener("scroll", syncNavHint, { passive: true });
    window.addEventListener("resize", syncNavHint);
    new MutationObserver(syncStatusStrip).observe(statusBlock, {
      subtree: true, childList: true, characterData: true, attributes: true,
      attributeFilter: ["hidden", "class"]
    });
    syncStatusStrip();
    syncNavHint();
  