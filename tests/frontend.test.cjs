const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const path = require("node:path");
const source = fs
  .readFileSync(path.join(__dirname, "../docs/main.js"), "utf8")
  .replace(
    /initializeSearchControls\(\);\s*loadDataset\(getSelectedDataset\(\)\);\s*$/,
    "",
  );
function transform(x = 0, y = 0, k = 1) {
  return {
    x,
    y,
    k,
    translate(a, b) {
      return transform(x + k * a, y + k * b, k);
    },
    scale(v) {
      return transform(x, y, k * v);
    },
  };
}
function harness() {
  const elements = {},
    requests = [],
    timers = new Map();
  let nextTimer = 0;
  function element(id) {
    return (elements[id] ||= {
      value: "",
      hidden: true,
      textContent: "",
      innerHTML: "",
      selectedOptions: [{ textContent: "Test division" }],
      addEventListener(name, fn) {
        this[name] = fn;
      },
    });
  }
  const selection = {
    attr(name, value) {
      if (value !== undefined) return this;
      return name === "width" ? 1000 : 760;
    },
    selectAll() {
      return this;
    },
    remove() {
      return this;
    },
    property() {
      return "";
    },
  };
  const context = vm.createContext({
    console,
    Date,
    Set,
    Number,
    Math,
    AbortController,
    setTimeout(fn) {
      let id = ++nextTimer;
      timers.set(id, fn);
      return id;
    },
    clearTimeout(id) {
      timers.delete(id);
    },
    window: { addEventListener() {}, innerWidth: 1440 },
    document: { getElementById: element },
    d3: {
      zoomIdentity: transform(),
      select: () => selection,
      selectAll: () => selection,
      json(url, options) {
        return new Promise((resolve, reject) =>
          requests.push({ url, options, resolve, reject }),
        );
      },
    },
  });
  vm.runInContext(source, context);
  context.drawCurrentDataset = () => {};
  context.renderSearchResults = () => {};
  return { context, requests, timers, element };
}
const dataset = (name) => ({ meta: { division: name }, nodes: [], links: [] });
const settle = () => new Promise((resolve) => setImmediate(resolve));

test("latest division wins even if an older request resolves later", async () => {
  const { context: c, requests: r } = harness();
  c.loadDataset("a");
  c.loadDataset("b");
  assert.ok(r[0].options.signal.aborted);
  r[1].resolve(dataset("B"));
  await settle();
  r[0].resolve(dataset("A"));
  await settle();
  assert.equal(c.activeDataset.meta.division, "B");
});
test("cached selection survives an earlier in-flight response", async () => {
  const { context: c, requests: r } = harness();
  c.datasetCache.b = dataset("B");
  c.loadDataset("a");
  c.loadDataset("b");
  r[0].resolve(dataset("A"));
  await settle();
  assert.equal(c.activeDataset.meta.division, "B");
});
test("failure clears stale data and offers retry", async () => {
  const { context: c, requests: r, element: e } = harness();
  c.activeDataset = dataset("old");
  c.loadDataset("a");
  r[0].reject(new Error("503"));
  await settle();
  assert.equal(c.activeDataset, null);
  assert.equal(e("loadStatus").hidden, false);
  assert.equal(e("retryLoad").hidden, false);
  assert.equal(e("fighterSearch").disabled, true);
  e("retryLoad").click();
  assert.equal(r.length, 2);
});
test("timeout aborts a stalled download and is cleaned up", async () => {
  const { context: c, requests: r, timers, element: e } = harness();
  c.loadDataset("a");
  r[0].options.signal.addEventListener("abort", () =>
    r[0].reject(new Error("aborted")),
  );
  [...timers.values()][0]();
  await settle();
  assert.equal(e("retryLoad").hidden, false);
  assert.equal(timers.size, 0);
});
test("stale failures cannot replace a successful selection", async () => {
  const { context: c, requests: r, element: e } = harness();
  c.loadDataset("a");
  c.loadDataset("b");
  r[1].resolve(dataset("B"));
  await settle();
  r[0].reject(new Error("old failure"));
  await settle();
  assert.equal(e("loadStatus").hidden, true);
});
test("invalid JSON shape is retryable instead of blank graph", async () => {
  const { context: c, requests: r, element: e } = harness();
  c.loadDataset("a");
  r[0].resolve({});
  await settle();
  assert.equal(e("retryLoad").hidden, false);
  assert.equal(c.activeDataset, null);
});
test("map fit centers both a single fighter and multiple fighters", () => {
  const { context: c } = harness();
  const settings = { maxAutoScale: 2, fitPadding: 100 };
  for (const nodes of [
    [{ x: 300, y: 200 }],
    [
      { x: 100, y: 100 },
      { x: 500, y: 500 },
    ],
  ]) {
    const t = c.getAutoFitTransform(nodes, settings);
    const x = nodes.reduce((a, n) => a + n.x, 0) / nodes.length;
    const y = nodes.reduce((a, n) => a + n.y, 0) / nodes.length;
    assert.equal(Math.round(t.x + t.k * x), 500);
    assert.equal(Math.round(t.y + t.k * y), 380);
  }
});
test("historical and missing snapshot dates are explicit", () => {
  const { context: c } = harness();
  const now = Date.parse("2026-10-16");
  assert.match(c.snapshotStatus("2026-05-16", now), /Historical/);
  assert.match(c.snapshotStatus("2026-10-10", now), /Results through/);
  assert.match(c.snapshotStatus(null, now), /unavailable/);
});
test("fighter and bout tooltips escape source text", () => {
  const { context: c } = harness();
  const html = c.buildTooltipHtml({
    label: "<img src=x onerror=alert(1)>",
    countryName: "<b>X</b>",
    fightCount: 2,
    uniqueOpponentCount: 1,
  });
  assert.ok(!html.includes("<img"));
  assert.ok(html.includes("&lt;img"));
  const bout = c.buildLinkTooltipHtml({
    link: {
      source: { label: "A" },
      target: { label: "B" },
      bouts: [{ eventName: "<script>bad</script>" }],
    },
  });
  assert.ok(!bout.includes("<script>"));
  assert.ok(bout.includes("&lt;script&gt;"));
});
test("switching divisions stops the prior force simulation", () => {
  const { context: c } = harness();
  let stopped = false;
  c.currentRenderedGraph = {
    simulation: {
      stop() {
        stopped = true;
      },
    },
  };
  c.loadDataset("a");
  assert.ok(stopped);
});
