const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const test = require('node:test');
const sourcePath = process.env.SPEED_TEST_SOURCE || require('node:path').resolve(__dirname, '../../src/jittersim/api/speed-test.js');
const source = fs.readFileSync(sourcePath, 'utf8');
const turn = () => new Promise(resolve => setImmediate(resolve));

function harness(fetcher) {
  const timers = new Map();
  let nextTimer = 0;
  const context = vm.createContext({
    AbortController, DOMException, encodeURIComponent, WeakSet,
    privateDataLockHooks: [], connectedRowHooks: [],
    element: () => ({value: 'fake-local-test-token'}),
    performance: {now: () => 0},
    fetch: fetcher,
    window: {
      setTimeout: (callback, milliseconds) => {
        timers.set(++nextTimer, {callback, milliseconds}); return nextTimer;
      },
      clearTimeout: timer => timers.delete(timer)
    }
  });
  vm.runInContext(source, context);
  return {context, timers, state: vm.runInContext('speedTestState', context),
    fire: () => { assert.equal(timers.size, 1); [...timers.values()][0].callback(); }};
}

function stalled(signal) {
  return new Promise((resolve, reject) => {
    signal.addEventListener('abort', () => reject(new DOMException('Stopped', 'AbortError')), {once: true});
  });
}

test('header wait has a deadline from before fetch and releases its controller', async () => {
  let signal;
  const h = harness((_url, options) => { signal = options.signal; return stalled(signal); });
  const result = h.context.speedTestPull({id: 'r'}, 'shaped', 8, 0);
  assert.equal([...h.timers.values()][0].milliseconds, 10_000);
  h.fire();
  assert.equal(await result, null);
  assert.equal(signal.aborted, true);
  assert.equal(h.timers.size, 0);
  assert.equal(h.state.streamController, null);
});

test('body wait has a deadline and cancels its reader', async () => {
  let canceled = 0;
  const h = harness((_url, {signal}) => Promise.resolve({ok: true, body: {getReader: () => ({
    read: () => stalled(signal), cancel: () => { canceled++; return Promise.resolve(); }
  })}}));
  const result = h.context.speedTestPull({id: 'r'}, 'shaped', 8, 0);
  await turn();
  h.fire();
  assert.equal(await result, null);
  assert.equal(canceled, 1);
  assert.equal(h.timers.size, 0);
});

test('a deadline after measured chunks keeps their speed', async () => {
  let reads = 0;
  let now = 0;
  const h = harness((_url, {signal}) => Promise.resolve({ok: true, body: {getReader: () => ({
    read: () => {
      reads++;
      if (reads < 3) { now = reads === 1 ? 1100 : 2100; return Promise.resolve({done: false, value: new Uint8Array(1000)}); }
      return stalled(signal);
    }, cancel: () => Promise.resolve()
  })}}));
  h.context.performance.now = () => now;
  const result = h.context.speedTestPull({id: 'r'}, 'shaped', 8, 0);
  await turn();
  assert.equal(reads, 3);
  h.fire();
  assert.equal(await result, 8);
});

test('explicit cancellation rejects instead of continuing the dashboard flow', async () => {
  const h = harness((_url, {signal}) => stalled(signal));
  const result = h.context.speedTestPull({id: 'r'}, 'reference', 3, 0);
  h.context.speedTestAbortDownload();
  await assert.rejects(result, {name: 'AbortError'});
  assert.equal(h.state.streamController, null);
  assert.equal(h.timers.size, 0);
});

test('private data invalidation aborts a stalled download', async () => {
  const h = harness((_url, {signal}) => stalled(signal));
  const result = h.context.speedTestPull({id: 'r'}, 'shaped', 8, 0);
  h.context.speedTestClearPrivateState();
  await assert.rejects(result, {name: 'AbortError'});
  assert.equal(h.state.serial, 1);
  assert.equal(h.state.run, null);
});

test('opening another device aborts the previous browser download', async () => {
  const h = harness((_url, {signal}) => stalled(signal));
  const result = h.context.speedTestPull({id: 'r'}, 'shaped', 8, 0);
  vm.runInContext(`speedTestDialog = () => ({open: true}); speedTestClearResults = () => {};
    speedTestFocusTitle = () => {}; speedTestLoad = async () => {};`, h.context);
  await h.context.speedTestOpen({mac_address: '02:00:00:00:00:02'});
  await assert.rejects(result, {name: 'AbortError'});
  assert.equal(h.state.serial, 1);
});

test('Cancel sends one cancel request and no automatic Finish request', async () => {
  const h = harness((_url, {signal}) => stalled(signal));
  const requests = [];
  h.context.request = async path => { requests.push(path); return {}; };
  vm.runInContext('speedTestSay = () => {}; speedTestFinished = async () => { speedTestState.run = null; };', h.context);
  const run = {id: 'r'};
  h.state.run = run;
  h.state.meta = {limits: {dashboard_parts: {reference: 3, shaped: 8}}};
  const dashboard = h.context.speedTestDashboard(run, 0);
  await h.context.speedTestEnd('cancel');
  await dashboard;
  assert.deepEqual(requests, ['/api/speed-tests/cancel']);
});

test('Cancel during a stalled error body sends no automatic Finish request', async () => {
  const h = harness((_url, {signal}) => Promise.resolve({
    ok: false, json: () => stalled(signal)
  }));
  const requests = [];
  let releaseCancel;
  h.context.request = path => {
    requests.push(path);
    if (path.endsWith('/cancel')) {
      return new Promise(resolve => { releaseCancel = resolve; });
    }
    return Promise.resolve({});
  };
  vm.runInContext('speedTestSay = () => {}; speedTestFinished = async () => { speedTestState.run = null; };', h.context);
  const run = {id: 'r'};
  h.state.run = run;
  h.state.meta = {limits: {dashboard_parts: {reference: 3, shaped: 8}}};
  const dashboard = h.context.speedTestDashboard(run, 0);
  await turn();
  const ending = h.context.speedTestEnd('cancel');
  // The server has not confirmed cancellation yet, so an automatic Finish could still save.
  await turn();
  releaseCancel({});
  await ending;
  await dashboard;
  assert.deepEqual(requests, ['/api/speed-tests/cancel']);
});

test('closing the dialog leaves the measurement download running', async () => {
  let signal;
  const h = harness((_url, options) => { signal = options.signal; return stalled(signal); });
  function node() {
    const listeners = {};
    return {
      open: true,
      setAttribute() {}, append() {},
      addEventListener: (name, listener) => { listeners[name] = listener; },
      close() { this.open = false; listeners.close(); }
    };
  }
  h.context.document = {createElement: node, body: {append() {}}};
  vm.runInContext('speedTestRestoreFocus = () => {};', h.context);
  const dialog = h.context.speedTestDialog();
  const result = h.context.speedTestPull({id: 'r'}, 'shaped', 8, 0);
  dialog.close();
  assert.equal(signal.aborted, false);
  assert.equal(h.timers.size, 1);
  h.context.speedTestAbortDownload();
  await assert.rejects(result, {name: 'AbortError'});
});

test('a shaped header timeout preserves the completed reference speed', async () => {
  let requestCount = 0;
  let now = 0;
  let reads = 0;
  const h = harness((_url, {signal}) => {
    if (++requestCount === 2) return stalled(signal);
    return Promise.resolve({ok: true, body: {getReader: () => ({
      read: () => {
        reads++;
        now = reads === 1 ? 1100 : 3100;
        return Promise.resolve({done: false, value: new Uint8Array(1000)});
      }, cancel: () => Promise.resolve()
    })}});
  });
  h.context.performance.now = () => now;
  const run = {id: 'r'};
  h.state.run = run;
  h.state.meta = {limits: {dashboard_parts: {reference: 3, shaped: 8}}};
  let saved;
  h.context.saveResult = (_action, extra) => { saved = extra; };
  vm.runInContext('speedTestSay = () => {}; speedTestEnd = async (action, extra) => saveResult(action, extra);', h.context);
  const dashboard = h.context.speedTestDashboard(run, 0);
  await turn();
  assert.equal(requestCount, 2);
  h.fire();
  await dashboard;
  assert.equal(saved.browser.reference_kbit, 4);
  assert.equal(saved.browser.download_kbit, null);
  assert.equal(h.timers.size, 0);
});
