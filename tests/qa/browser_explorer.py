"""Click every control of every dashboard page and report what looks wrong.

Run it as `python -m tests.qa.browser_explorer [output-folder]`. It starts the browser
fixture (a dashboard with a fake network executor) and drives Chromium through each page at a
desktop and a phone width. It records console errors, uncaught page errors, failed requests,
server errors, layout overflow, controls without an accessible name, focus that cannot move on,
text that stays on "Loading" and focus that is lost when a question is answered. Anything wrong
is written to report.json with a screenshot.
The posted JSON bodies are saved to seeds.json as real payloads for the API fuzz tests.
"""

import json
import os
import re
import selectors
import subprocess
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

PAGES = ("devices", "profiles", "schedules", "router", "traffic", "measurements", "activity",
         "access", "diagnostics", "updates")
VIEWPORTS = ((1440, 900), (375, 812))
CONTROLS = ("button, a[href], input:not([type=hidden]), select, textarea, summary, "
            "[role=button], [role=tab], [role=switch], [role=checkbox], [tabindex='0']")
# Controls the explorer must not press: they would leave the fixture's page.
SKIP_TEXT = re.compile(r"^(download|export|backup)\b", re.I)
LOADING = re.compile(r"(loading|checking|please wait|reading|scanning|working|waiting)\b[^.]{0,40}(\.\.\.|…)", re.I)

DESCRIBE = """
(el) => {
  const text = (el.innerText || el.value || '').trim().replace(/\\s+/g, ' ').slice(0, 60);
  return [el.tagName.toLowerCase(), el.id || '', el.getAttribute('aria-label') || '', text].join('|');
}
"""

VISIBLE_CONTROLS = """
(selector) => {
  const out = [];
  for (const el of document.querySelectorAll(selector)) {
    const style = getComputedStyle(el);
    const box = el.getBoundingClientRect();
    if (style.visibility === 'hidden' || style.display === 'none' || box.width === 0 || box.height === 0) continue;
    if (el.closest('[hidden], [inert]')) continue;
    if (el.disabled) continue;
    out.push(el);
  }
  window.__qa = out;
  return out.length;
}
"""

NAMELESS = """
() => {
  const bad = [];
  for (const el of document.querySelectorAll('button, a[href], input:not([type=hidden]), select, textarea, summary, [role=button], [role=tab], [role=switch]')) {
    const style = getComputedStyle(el);
    const box = el.getBoundingClientRect();
    if (style.visibility === 'hidden' || style.display === 'none' || box.width === 0 || el.closest('[hidden]')) continue;
    let name = el.getAttribute('aria-label') || '';
    const by = el.getAttribute('aria-labelledby');
    if (!name && by) name = by.split(/\\s+/).map(id => (document.getElementById(id) || {}).textContent || '').join(' ');
    if (!name && el.labels && el.labels.length) name = Array.from(el.labels).map(l => l.textContent).join(' ');
    if (!name) name = el.getAttribute('title') || '';
    if (!name && ['button', 'a', 'summary'].includes(el.tagName.toLowerCase())) name = el.textContent || '';
    if (!name && el.tagName.toLowerCase() === 'input' && ['button', 'submit', 'reset'].includes(el.type)) name = el.value;
    if (!name) { const img = el.querySelector('img[alt]'); if (img) name = img.alt; }
    if (!name && el.getAttribute('role') && el.textContent) name = el.textContent;
    if (!name.trim()) bad.push(el.outerHTML.slice(0, 160));
  }
  return bad;
}
"""

OVERFLOW = """
() => {
  const width = document.documentElement.clientWidth;
  const out = [];
  if (document.documentElement.scrollWidth > width + 1) out.push('page scrolls sideways: scrollWidth ' + document.documentElement.scrollWidth + ' > ' + width);
  for (const el of document.querySelectorAll('body *')) {
    const style = getComputedStyle(el);
    if (style.display === 'none' || style.visibility === 'hidden' || style.position === 'fixed') continue;
    const box = el.getBoundingClientRect();
    if (box.width === 0 || box.height === 0) continue;
    if (el.closest('[hidden]')) continue;
    if (box.right > width + 1 || box.left < -1) {
      // An element that sits inside a scrolling box is fine.
      let clipped = false;
      for (let parent = el.parentElement; parent && parent !== document.body; parent = parent.parentElement) {
        const overflow = getComputedStyle(parent).overflowX;
        if (overflow === 'auto' || overflow === 'scroll' || overflow === 'hidden') { clipped = true; break; }
      }
      if (!clipped) out.push(el.tagName.toLowerCase() + (el.id ? '#' + el.id : '') + '.' + String(el.className).slice(0, 40) + ' [' + Math.round(box.left) + ',' + Math.round(box.right) + ']');
    }
  }
  return out.slice(0, 12);
}
"""

SMELLS = """
() => {
  const found = [];
  const clone = document.body.cloneNode(true);
  clone.querySelectorAll('pre, code, script, style, [hidden]').forEach(node => node.remove());
  const text = clone.textContent || '';
  for (const word of ['undefined', '[object Object]', 'NaN', 'null', 'Infinity', 'Traceback']) {
    const at = text.indexOf(word);
    if (at >= 0) found.push(word + ': ...' + text.slice(Math.max(0, at - 30), at + 40).replace(/\\s+/g, ' ') + '...');
  }
  return found;
}
"""

STUCK = """
() => {
  const out = [];
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_ELEMENT);
  for (let el = walker.nextNode(); el; el = walker.nextNode()) {
    if (el.children.length) continue;
    const style = getComputedStyle(el);
    const box = el.getBoundingClientRect();
    if (style.display === 'none' || style.visibility === 'hidden' || box.width === 0 || el.closest('[hidden]')) continue;
    const text = (el.textContent || '').trim();
    if (text && text.length < 160) out.push(el.tagName.toLowerCase() + (el.id ? '#' + el.id : '') + ': ' + text);
  }
  return out;
}
"""


class Explorer:
    def __init__(self, base, output):
        self.base = base
        self.output = Path(output)
        self.output.mkdir(parents=True, exist_ok=True)
        self.findings = []
        self.seeds = {}
        self.requests_seen = {}
        self.external_links = set()
        self.client_errors = {}
        self.context_label = ""

    def note(self, kind, detail, page=None, shot=None):
        entry = {"where": self.context_label, "kind": kind, "detail": detail}
        if page is not None and shot:
            path = self.output / (re.sub(r"[^a-z0-9]+", "-", f"{self.context_label}-{kind}-{shot}".lower()) + ".png")
            try:
                page.screenshot(path=str(path))
                entry["screenshot"] = path.name
            except Exception as error:  # a page that is closing has nothing to capture
                entry["screenshot_error"] = str(error)[:80]
        self.findings.append(entry)

    def attach(self, page):
        def on_console(message):
            if message.type == "error":
                self.note("console-error", message.text[:300])

        def on_pageerror(error):
            self.note("uncaught-exception", str(error)[:300])

        def on_failed(request):
            failure = request.failure or ""
            if "ERR_ABORTED" in failure:
                return
            self.note("request-failed", f"{request.method} {request.url} {failure}")

        def on_response(response):
            request = response.request
            path = response.url.replace(self.base, "")
            key = (request.method, path.split("?")[0], response.status)
            self.requests_seen[key] = self.requests_seen.get(key, 0) + 1
            if request.method == "POST":
                try:
                    body = request.post_data
                    if body:
                        self.seeds.setdefault(path.split("?")[0], [])
                        if body not in self.seeds[path.split("?")[0]] and len(self.seeds[path.split("?")[0]]) < 12:
                            self.seeds[path.split("?")[0]].append(body)
                except Exception:
                    pass
            if response.status >= 500:
                self.note("server-error", f"{request.method} {path} -> {response.status}")
            elif response.status >= 400:
                self.client_errors[f"{request.method} {path.split('?')[0]} {response.status}"] = self.client_errors.get(f"{request.method} {path.split('?')[0]} {response.status}", 0) + 1
                ctype = response.headers.get("content-type", "")
                if "json" not in ctype and path.startswith("/api/"):
                    self.note("non-json-error", f"{request.method} {path} -> {response.status} {ctype}")

        page.on("console", on_console)
        page.on("pageerror", on_pageerror)
        page.on("requestfailed", on_failed)
        page.on("response", on_response)
        page.on("dialog", lambda dialog: dialog.accept())
        page.on("download", lambda download: download.cancel())

    def unlock(self, page):
        phone = page.viewport_size["width"] <= 768
        toggle = page.locator("#controls-toggle")
        if phone and toggle.is_visible() and toggle.get_attribute("aria-expanded") != "true":
            toggle.click()
        page.locator("#open-admin-credentials").click()
        page.locator("#admin-token").fill("browser-test")
        page.locator("#check-admin-token").click()
        page.locator("#private-dashboard").wait_for(state="visible", timeout=10000)
        page.locator("#remember-admin-token").check()
        page.locator("#close-credentials").click()
        if phone and toggle.is_visible() and toggle.get_attribute("aria-expanded") == "true":
            toggle.click()

    def goto_page(self, page, name):
        page.evaluate("(name) => { window.location.hash = '#' + name; }", name)
        page.wait_for_timeout(250)

    def check_layout(self, page, name, shot):
        for line in page.evaluate(OVERFLOW):
            self.note("overflow", line, page, f"{name}-{shot}")
        for html in page.evaluate(NAMELESS):
            self.note("unlabeled-control", html, page, f"{name}-{shot}")

    def check_focus(self, page, name):
        """Tab through the page and report focus that stops moving or lands outside the page."""

        page.evaluate("document.activeElement && document.activeElement.blur()")
        last = None
        stuck = 0
        distinct = set()
        sequence = []
        for step in range(160):
            page.keyboard.press("Tab")
            current = page.evaluate(
                "() => { const a = document.activeElement; return a ? a.tagName + '#' + (a.id || '') + '|' + (a.getAttribute('aria-label') || a.textContent || '').trim().slice(0, 30) : 'none'; }"
            )
            sequence.append(current)
            distinct.add(current)
            if current == last:
                stuck += 1
                if stuck >= 3:
                    self.note("focus-trap", f"focus stays on {current} after {stuck} Tab presses", page, f"{name}-tab")
                    break
            else:
                stuck = 0
            last = current
        if len(distinct) < 3:
            self.note("focus-few-stops", f"only {len(distinct)} distinct focus targets in 160 Tab presses: {sorted(distinct)[:5]}", page, f"{name}-tab")
        # A focused control must show where the focus is.
        invisible = page.evaluate(
            """() => { const a = document.activeElement; if (!a || a === document.body) return null;
              const s = getComputedStyle(a); const noOutline = s.outlineStyle === 'none' || parseFloat(s.outlineWidth) === 0;
              return noOutline && s.boxShadow === 'none' ? a.tagName + '#' + a.id : null; }"""
        )
        if invisible:
            self.note("focus-not-visible", f"{invisible} shows no focus ring when focused by Tab", page, f"{name}-ring")

    def check_stuck(self, page, name):
        texts = [text for text in page.evaluate(STUCK) if LOADING.search(text)]
        if texts:
            # Wait out slow reads before calling it stuck.
            page.wait_for_timeout(5000)
            texts = [text for text in page.evaluate(STUCK) if LOADING.search(text)]
            for text in texts:
                self.note("stuck-loading", text, page, f"{name}-loading")

    def click_everything(self, page, name):
        page.evaluate(VISIBLE_CONTROLS, CONTROLS)
        descriptors = page.evaluate("() => window.__qa.map(el => [el.tagName.toLowerCase(), el.id || '', el.getAttribute('aria-label') || '', (el.innerText || el.value || '').trim().replace(/\\s+/g, ' ').slice(0, 60)].join('|'))")
        done = set()
        tally = {}
        clicked = 0
        for descriptor in descriptors:
            tally[descriptor] = tally.get(descriptor, 0) + 1
            token = (descriptor, tally[descriptor])
            if token in done:
                continue
            done.add(token)
            kind, _id, _label, text = descriptor.split("|", 3)
            if SKIP_TEXT.match(text) or (kind == "a" and "download" in text.lower()):
                continue
            if text.lower().startswith("skip to"):
                continue  # visually hidden until focused: the Tab check covers it
            self.goto_page(page, name)
            if page.locator("#credentials-panel").is_visible():
                page.locator("#close-credentials").click()
            page.evaluate(VISIBLE_CONTROLS, CONTROLS)
            occurrence = tally[descriptor]
            index = page.evaluate(
                """([descriptor, occurrence]) => { let seen = 0;
                  for (let i = 0; i < window.__qa.length; i++) { const el = window.__qa[i];
                    const d = [el.tagName.toLowerCase(), el.id || '', el.getAttribute('aria-label') || '', (el.innerText || el.value || '').trim().replace(/\\s+/g, ' ').slice(0, 60)].join('|');
                    if (d === descriptor && ++seen === occurrence) return i; }
                  return -1; }""",
                [descriptor, occurrence],
            )
            if index < 0:
                continue
            before = len(self.findings)
            try:
                handle = page.evaluate_handle("(i) => window.__qa[i]", index).as_element()
                if kind == "input" and "file" in (handle.get_attribute("type") or ""):
                    continue
                if kind in ("input", "textarea"):
                    kind_type = handle.get_attribute("type") or "text"
                    if kind_type in ("checkbox", "radio"):
                        handle.click(timeout=2000)
                    elif kind_type not in ("button", "submit", "reset", "image"):
                        handle.click(timeout=2000)
                        handle.fill("x" * 40 if kind_type == "text" else "1", timeout=2000)
                    else:
                        handle.click(timeout=2000)
                elif kind == "select":
                    options = handle.evaluate("el => Array.from(el.options).map(o => o.value)")
                    for option in options[:6]:
                        handle.select_option(option, timeout=2000)
                else:
                    href = handle.get_attribute("href") if kind == "a" else None
                    if href and re.match(r"https?://", href) and not href.startswith(self.base):
                        self.external_links.add(href)
                        continue
                    handle.click(timeout=2000)
                clicked += 1
            except Exception as error:
                message = str(error).splitlines()[0][:140]
                if "intercepts pointer events" in str(error) or "not visible" in message or "outside of the viewport" in message:
                    self.note("click-blocked", f"{descriptor}: {message}", page, f"{name}-{clicked}")
                else:
                    self.note("click-failed", f"{descriptor}: {message}", page)
            page.wait_for_timeout(350)
            if not page.url.startswith(self.base):
                self.note("left-the-dashboard", f"{descriptor} navigated to {page.url[:80]}", page, f"{name}-{clicked}")
                page.goto(self.base)
                page.wait_for_timeout(800)
                if page.locator("#open-admin-credentials").is_visible() and not page.locator("#private-dashboard").is_visible():
                    self.unlock(page)
            for word in page.evaluate(SMELLS):
                self.note("text-smell", word, page, f"{name}-smell")
            # A dialog or sheet that opened must close with Escape.
            dialog = page.locator("[role=dialog]:visible, dialog[open]")
            if dialog.count():
                page.keyboard.press("Escape")
                page.wait_for_timeout(250)
                if page.evaluate("() => !document.querySelector('dialog[open]') && document.activeElement === document.body"):
                    # A question that is answered must give focus back to the control that asked.
                    self.note("focus-lost-after-dialog", f"focus is on the page body after Escape closed the question opened by {descriptor}", page, f"{name}-focus")
                if dialog.count() and dialog.first.is_visible():
                    # A pinned help tip takes the first Escape. A second one must close the dialog.
                    page.keyboard.press("Escape")
                    page.wait_for_timeout(250)
                if dialog.count() and dialog.first.is_visible():
                    self.note("dialog-stays-open", f"Escape did not close the dialog opened by {descriptor}", page, f"{name}-dialog")
                    # leave it: reload so the next control is reachable
                    page.reload()
                    page.wait_for_timeout(500)
            if len(self.findings) > before:
                for finding in self.findings[before:]:
                    finding.setdefault("after_click", descriptor)
        return clicked, len(descriptors)

    def run_viewport(self, playwright, width, height, click=True):
        browser = playwright.chromium.launch(executable_path=os.environ.get("JITTERSIM_BROWSER_EXECUTABLE"))
        context = browser.new_context(viewport={"width": width, "height": height},
                                      has_touch=width < 500, is_mobile=width < 500)
        page = context.new_page()
        self.context_label = f"{width}px"
        self.attach(page)
        page.goto(self.base)
        page.wait_for_timeout(500)
        self.check_layout(page, "signin", "layout")
        self.unlock(page)
        page.wait_for_timeout(1000)
        summary = {}
        for name in PAGES:
            self.context_label = f"{width}px/{name}"
            self.goto_page(page, name)
            page.wait_for_timeout(800)
            self.check_layout(page, name, "layout")
            self.check_stuck(page, name)
            for word in page.evaluate(SMELLS):
                self.note("text-smell", word, page, f"{name}-smell")
            self.check_focus(page, name)
            clicked, found = self.click_everything(page, name) if click else (0, 0)
            self.goto_page(page, name)
            page.wait_for_timeout(500)
            self.check_layout(page, name, "after")
            summary[name] = {"controls_found": found, "clicked": clicked}
        # one more pass with the viewport rotated to the other phone orientation is not needed
        context.close()
        browser.close()
        return summary


def main(output="qa-browser", click=True):
    process = subprocess.Popen(
        [sys.executable, "-m", "tests.browser.fixture"], stdout=subprocess.PIPE, text=True,
        env={**os.environ, "JITTERSIM_BROWSER_TEST_SEED": "1", "JITTERSIM_BROWSER_TEST_SCHEDULER": "1",
             "JITTERSIM_BROWSER_TEST_OBSERVATIONS": "1"},
    )
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            if not selector.select(timeout=20):
                raise RuntimeError("browser fixture did not start")
            port = int(process.stdout.readline().strip())
        explorer = Explorer(f"http://127.0.0.1:{port}", output)
        summaries = {}
        with sync_playwright() as playwright:
            for width, height in VIEWPORTS:
                started = time.time()
                summaries[f"{width}px"] = explorer.run_viewport(playwright, width, height, click)
                summaries[f"{width}px"]["seconds"] = round(time.time() - started)
        report = {"summary": summaries, "findings": explorer.findings,
                  "requests": {f"{m} {p} {s}": c for (m, p, s), c in sorted(explorer.requests_seen.items())},
                  "client_errors": explorer.client_errors, "external_links": sorted(explorer.external_links)}
        Path(output, "report.json").write_text(json.dumps(report, indent=1))
        Path(output, "seeds.json").write_text(json.dumps(explorer.seeds, indent=1))
        return report
    finally:
        process.terminate()
        process.wait(timeout=10)


if __name__ == "__main__":
    arguments = [argument for argument in sys.argv[1:] if not argument.startswith("--")]
    result = main(arguments[0] if arguments else "qa-browser", click="--no-click" not in sys.argv)
    print(json.dumps(result["summary"], indent=1))
    kinds = {}
    for finding in result["findings"]:
        kinds[finding["kind"]] = kinds.get(finding["kind"], 0) + 1
    print(kinds)
