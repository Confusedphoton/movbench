"use strict";
const $ = (id) => document.getElementById(id);
const state = {
  cohort: "",
  tier: "",
  task: "",
  query: "",
  metric: "score",
  view: "overview",
  hidden: new Set(),
  data: null,
  meta: null,
};
const colors = [
  "#c5ac98",
  "#97b5aa",
  "#a5a4d1",
  "#bc9090",
  "#8fabbf",
  "#c2b27c",
  "#b18fad",
  "#93ac86",
];
const tierNames = {
  tiny: "4 KiB",
  small: "64 KiB",
  medium: "1 MiB",
  large: "16 MiB",
};
const tierOrder = Object.keys(tierNames);
const escapeHtml = (value) =>
  String(value ?? "").replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ],
  );
const num = (value, digits = 1) =>
  Number(value).toLocaleString(undefined, {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
const memory = (value) =>
  value >= 1048576
    ? `${num(value / 1048576, 1)} MiB`
    : value >= 1024
      ? `${num(value / 1024, 1)} KiB`
      : `${value} B`;
const color = (name) =>
  colors[
    [...(state.data?.rows || [])]
      .map((r) => r.model)
      .sort()
      .indexOf(name) % colors.length
  ] || colors[0];
let requestNumber = 0;

async function getJson(url) {
  const response = await fetch(url);
  if (!response.ok) throw new Error(`Request failed (${response.status})`);
  return response.json();
}
async function load() {
  const thisRequest = ++requestNumber;
  try {
    const params = new URLSearchParams({
      cohort: state.cohort,
      tier: state.tier,
      task: state.task,
      q: state.query,
    });
    const data = await getJson(`/api/leaderboard?${params}`);
    if (thisRequest !== requestNumber) return;
    state.data = data;
    state.cohort = data.cohort || "";
    $("error").hidden = true;
    render();
  } catch (error) {
    $("error").textContent = `Could not load results. ${error.message}`;
    $("error").hidden = false;
  }
}
function render() {
  const data = state.data,
    rows = data.rows;
  $("cohort").innerHTML = data.cohorts.length
    ? data.cohorts
        .map(
          (c) =>
            `<option value="${escapeHtml(c.id)}">${escapeHtml(c.target.toUpperCase())} · ${escapeHtml(c.id.slice(0, 8))} · ${c.tasks.length}×${c.tiers.length}</option>`,
        )
        .join("")
    : '<option value="">No measured runs</option>';
  $("cohort").value = state.cohort;
  const cohort = data.cohorts.find((c) => c.id === state.cohort);
  $("cohort-note").textContent = cohort
    ? `${cohort.cpu}. ${cohort.repeats} trials, ${cohort.trial_ms} ms calibration target.${cohort.target_verified ? "" : " Development hardware; target unverified."}`
    : "Comparable hardware and settings stay together.";
  $("target-badge").textContent = cohort
    ? `${cohort.target.toUpperCase().replace("ZEN", "ZEN ")} · AVX-512${cohort.target_verified ? "" : " · DEV"}`
    : "ZEN 5 · AVX-512";
  $("stat-models").textContent = rows.length;
  $("stat-score").textContent = rows.length
    ? num(Math.max(...rows.map((r) => r.score)))
    : "—";
  $("stat-valid").textContent = rows.length
    ? `${rows.reduce((a, r) => a + r.valid, 0)} / ${rows.reduce((a, r) => a + r.total, 0)}`
    : "—";
  $("row-count").textContent = rows.length;
  $("metric-heading").textContent =
    state.metric === "score" ? "Mean score ↓" : "Elo rating ↓";
  const sorted = [...rows].sort(
    (a, b) =>
      b[state.metric] - a[state.metric] || a.model.localeCompare(b.model),
  );
  $("leaderboard").innerHTML = sorted.length
    ? sorted
        .map(
          (row, i) => `<tr>
    <td>${String(i + 1).padStart(2, "0")}</td><td class="model-cell"><div class="model-label"><svg width="7" height="7"><circle cx="3.5" cy="3.5" r="3.5" fill="${color(row.model)}"/></svg>${escapeHtml(row.model)}</div><span class="model-meta">${escapeHtml(row.metadata.backend || "submitted assembly")} · ${escapeHtml(row.run_id)}</span></td>
    <td class="score-cell">${num(row[state.metric])}</td><td>${row.geomean_speedup ? num(row.geomean_speedup, 2) + "×" : "—"}</td><td><span class="${row.valid === row.total ? "valid" : "invalid"}">${row.valid}</span><span class="muted"> / ${row.total}</span></td><td>${memory(row.memory_bytes)}</td><td><button class="detail-button" data-run="${escapeHtml(row.run_id)}" aria-label="View ${escapeHtml(row.model)} run details">↗</button></td></tr>`,
        )
        .join("")
    : '<tr><td class="empty-row" colspan="7">No implementations match these filters.</td></tr>';
  $("kernel-results").innerHTML = rows.length
    ? rows
        .flatMap((row) =>
          row.results.map(
            (cell) =>
              `<tr><td>${escapeHtml(row.model)}<span class="kernel-name">${escapeHtml(state.meta?.tasks.find((t) => t.id === cell.task)?.name || cell.task)}</span></td><td>${tierNames[cell.tier]}</td><td><span class="${cell.valid ? "valid" : "invalid"}">${cell.valid ? "Valid" : "Invalid"}</span>${cell.error ? `<span class="kernel-error">${escapeHtml(cell.error)}</span>` : ""}</td><td>${num(cell.score)}</td><td>${cell.timing ? `${num(cell.timing.median_ns, 2)} ± ${num(cell.timing.mad_ns, 2)} ns` : "—"}</td><td>${cell.valid ? num(cell.speedup, 2) + "×" : "—"}</td><td>${cell.memory_bytes != null ? memory(cell.memory_bytes) : "—"}</td></tr>`,
          ),
        )
        .join("")
    : '<tr><td class="empty-row" colspan="7">No kernel results match these filters.</td></tr>';
  document
    .querySelectorAll("[data-run]")
    .forEach(
      (button) => (button.onclick = () => showDetail(button.dataset.run)),
    );
  drawChart();
}
function svgElement(tag, attributes = {}, text = "") {
  const el = document.createElementNS("http://www.w3.org/2000/svg", tag);
  for (const [key, value] of Object.entries(attributes))
    el.setAttribute(key, value);
  if (text) el.textContent = text;
  return el;
}
const intervalCache = new Map();
function scoreInterval(cells, key) {
  if (intervalCache.has(key)) return intervalCache.get(key);
  const valid = cells.filter((c) => c.valid);
  if (
    !valid.length ||
    valid.some(
      (c) =>
        !c.timing?.samples_ns ||
        !c.reference_timing?.samples_ns ||
        c.timing.samples_ns.length < 3 ||
        c.timing.samples_ns.length !== c.reference_timing.samples_ns.length,
    )
  )
    return null;
  // Resample paired batch observations within each kernel. This describes
  // retained within-run variability, not independent sessions or a population CI.
  let seed = 2166136261;
  for (const char of key) seed = Math.imul(seed ^ char.charCodeAt(0), 16777619);
  const random = () => {
    seed |= 0;
    seed = (seed + 0x6d2b79f5) | 0;
    let t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
    t ^= t + Math.imul(t ^ (t >>> 7), 61 | t);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
  const median = (values) => {
    values.sort((a, b) => a - b);
    const m = values.length >> 1;
    return values.length % 2 ? values[m] : (values[m - 1] + values[m]) / 2;
  };
  const means = [];
  for (let draw = 0; draw < 400; draw++) {
    let sum = 0;
    for (const cell of valid) {
      const a = [],
        b = [],
        n = cell.timing.samples_ns.length;
      for (let i = 0; i < n; i++) {
        const index = Math.floor(random() * n);
        a.push(cell.timing.samples_ns[index]);
        b.push(cell.reference_timing.samples_ns[index]);
      }
      sum += 100 + 25 * Math.max(0, Math.log2(median(b) / median(a)));
    }
    means.push(sum / cells.length); // Invalid cells remain zero in the denominator.
  }
  means.sort((a, b) => a - b);
  const quantile = (p) => {
    const index = (means.length - 1) * p,
      low = Math.floor(index);
    return means[low] + (means[Math.ceil(index)] - means[low]) * (index - low);
  };
  const interval = {
    low: quantile(0.25),
    high: quantile(0.75),
    trials: Math.min(...valid.map((c) => c.timing.samples_ns.length)),
  };
  intervalCache.set(key, interval);
  return interval;
}
function drawChart() {
  const svg = $("chart");
  svg.replaceChildren();
  $("chart-tooltip").hidden = true;
  const rows = state.data.rows;
  $("chart-empty").hidden = rows.length > 0;
  $("chart-legend").replaceChildren();
  if (!rows.length) return;
  const series = rows
    .filter((r) => !state.hidden.has(r.model))
    .map((row) => ({
      row,
      points: tierOrder
        .map((tier) => {
          const cells = row.results.filter((c) => c.tier === tier);
          return cells.length
            ? {
                tier,
                score: cells.reduce((a, c) => a + c.score, 0) / cells.length,
                valid: cells.filter((c) => c.valid).length,
                total: cells.length,
                interval: scoreInterval(
                  cells,
                  row.run_id +
                    ":" +
                    tier +
                    ":" +
                    cells.map((c) => c.task).join(","),
                ),
              }
            : null;
        })
        .filter(Boolean),
    }));
  const scores = series.flatMap((s) =>
    s.points.flatMap((p) =>
      p.interval ? [p.score, p.interval.low, p.interval.high] : [p.score],
    ),
  );
  const trials = state.data.cohorts.find((c) => c.id === state.cohort)?.repeats;
  $("uncertainty-note").textContent =
    `Within-run bootstrap IQR · ${trials || "—"} trials/kernel`;
  const max = Math.max(125, Math.ceil(Math.max(...scores, 100) / 25) * 25 + 25);
  const min = Math.min(...scores, 100) < 80 ? 0 : 75;
  const x = (tier) => 115 + tierOrder.indexOf(tier) * 245;
  const y = (score) => 220 - ((score - min) / (max - min)) * 190;
  const ticks = 5;
  svg.append(
    svgElement(
      "text",
      { x: 65, y: 15, class: "chart-axis" },
      "Mean points / kernel (linear)",
    ),
  );
  for (let i = 0; i <= ticks; i++) {
    const value = min + ((max - min) * i) / ticks;
    svg.append(
      svgElement("line", {
        x1: 65,
        x2: 900,
        y1: y(value),
        y2: y(value),
        class: "chart-grid",
      }),
    );
    svg.append(
      svgElement(
        "text",
        { x: 48, y: y(value) + 4, "text-anchor": "end", class: "chart-axis" },
        num(value, 0),
      ),
    );
  }
  svg.append(
    svgElement("line", {
      x1: 65,
      x2: 900,
      y1: y(100),
      y2: y(100),
      class: "chart-baseline",
    }),
  );
  svg.append(
    svgElement(
      "text",
      { x: 898, y: y(100) - 8, "text-anchor": "end", class: "chart-axis" },
      "100 · valid output",
    ),
  );
  tierOrder.forEach((tier) =>
    svg.append(
      svgElement(
        "text",
        { x: x(tier), y: 246, "text-anchor": "middle", class: "chart-axis" },
        tierNames[tier],
      ),
    ),
  );
  svg.append(
    svgElement(
      "text",
      { x: 480, y: 277, "text-anchor": "middle", class: "chart-axis" },
      "Tables + scratch budget (binary bytes, log₂ scale)",
    ),
  );
  for (const { row, points } of series) {
    svg.append(
      svgElement("path", {
        d: points
          .map((p, i) => `${i ? "L" : "M"}${x(p.tier)},${y(p.score)}`)
          .join(" "),
        stroke: color(row.model),
        class: "chart-line",
      }),
    );
    for (const p of points) {
      if (p.interval) {
        const low = y(p.interval.low),
          high = y(p.interval.high),
          cx = x(p.tier);
        svg.append(
          svgElement("path", {
            d: `M${cx},${low}V${high}M${cx - 4},${low}H${cx + 4}M${cx - 4},${high}H${cx + 4}`,
            stroke: color(row.model),
            "stroke-width": 1.5,
            opacity: 0.65,
            fill: "none",
            class: "chart-uncertainty",
          }),
        );
      }
      const point = svgElement("circle", {
        cx: x(p.tier),
        cy: y(p.score),
        r: 4.5,
        fill: color(row.model),
        class: "chart-point",
        tabindex: 0,
        role: "button",
        "aria-label": `${row.model}, ${tierNames[p.tier]}, ${num(p.score)} points. View run details.`,
      });
      const show = () => {
        $("chart-tooltip").innerHTML =
          `<strong>${escapeHtml(row.model)}</strong><p>${tierNames[p.tier]} budget · ${p.valid}/${p.total} valid kernels<br>${num(p.score)} mean points${p.interval ? `<br>Bootstrap IQR: ${num(p.interval.low)}–${num(p.interval.high)} pts<br>${p.interval.trials} paired batches/kernel · within-run only` : ""}</p>`;
        $("chart-tooltip").hidden = false;
      };
      point.onmouseenter = point.onfocus = show;
      point.onmouseleave = point.onblur = () =>
        ($("chart-tooltip").hidden = true);
      point.onclick = () => showDetail(row.run_id);
      point.onkeydown = (e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          showDetail(row.run_id);
        }
      };
      svg.append(point);
    }
  }
  rows.forEach((row) => {
    const button = document.createElement("button");
    button.className = state.hidden.has(row.model) ? "off" : "";
    button.setAttribute("aria-pressed", String(!state.hidden.has(row.model)));
    const dot = svgElement("svg", { width: 7, height: 7 });
    dot.append(
      svgElement("circle", {
        cx: 3.5,
        cy: 3.5,
        r: 3.5,
        fill: color(row.model),
      }),
    );
    button.append(dot, document.createTextNode(row.model));
    button.onclick = () => {
      state.hidden.has(row.model)
        ? state.hidden.delete(row.model)
        : state.hidden.add(row.model);
      drawChart();
    };
    $("chart-legend").append(button);
  });
}
function setView(view) {
  state.view = view;
  ["overview", "kernels", "policy"].forEach(
    (name) => ($(name + "-view").hidden = name !== view),
  );
  document
    .querySelectorAll("[data-view]")
    .forEach((b) => b.classList.toggle("active", b.dataset.view === view));
}
async function showDetail(id) {
  try {
    const run = await getJson(`/api/runs/${encodeURIComponent(id)}`);
    $("detail-title").textContent = run.model;
    $("detail-content").innerHTML =
      `<div class="detail-facts"><span>CPU <b>${escapeHtml(run.hardware.cpu)}</b></span><span>Target <b>${escapeHtml(run.hardware.target)}${run.hardware.target_verified ? " · verified" : " · unverified"}</b></span><span>Run <b>${escapeHtml(run.id)}</b></span><span>Seed <b>${run.settings.seed}</b></span><span>Trials <b>${run.settings.repeats} paired batches</b></span><span>Affinity <b>logical CPU ${run.settings.cpu}</b></span><span>Created <b>${escapeHtml(new Date(run.created).toLocaleString())}</b></span><span>Suite <b>${run.tasks.length} tasks × ${run.tiers.length} tiers</b></span></div><p>${escapeHtml(run.settings.measurement)}.</p><p>Median and MAD come from retained trial batch means. See JSON for samples, validation, input coverage, provenance, and failures.</p><a href="/api/runs/${encodeURIComponent(run.id)}" target="_blank" rel="noreferrer">Open complete run JSON ↗</a><pre>${escapeHtml(JSON.stringify({ policy: run.policy, cohort: run.cohort, settings: run.settings, results: run.results }, null, 2))}</pre>`;
    if (!$("detail").open) $("detail").showModal();
  } catch (error) {
    $("error").textContent = error.message;
    $("error").hidden = false;
  }
}
document
  .querySelectorAll("[data-view]")
  .forEach((b) => (b.onclick = () => setView(b.dataset.view)));
document.querySelectorAll("[data-metric]").forEach(
  (b) =>
    (b.onclick = () => {
      state.metric = b.dataset.metric;
      document
        .querySelectorAll("[data-metric]")
        .forEach((x) => x.classList.toggle("active", x === b));
      if (state.data) render();
    }),
);
document.querySelectorAll("[data-tier]").forEach(
  (b) =>
    (b.onclick = () => {
      state.tier = b.dataset.tier;
      document.querySelectorAll("[data-tier]").forEach((x) => {
        x.classList.toggle("active", x === b);
        x.setAttribute("aria-pressed", String(x === b));
      });
      load();
    }),
);
$("cohort").onchange = () => {
  state.cohort = $("cohort").value;
  load();
};
$("task").onchange = () => {
  state.task = $("task").value;
  load();
};
let searchTimer;
$("search").oninput = () => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => {
    state.query = $("search").value;
    load();
  }, 150);
};
$("refresh").onclick = load;
$("show-policy").onclick = () => setView("policy");
$("reset").onclick = () => {
  state.tier = state.task = state.query = "";
  state.hidden.clear();
  $("search").value = "";
  $("task").value = "";
  document
    .querySelectorAll("[data-tier]")
    .forEach((b) => b.classList.toggle("active", b.dataset.tier === ""));
  load();
};
$("close-detail").onclick = () => $("detail").close();
$("detail").onclick = (event) => {
  if (event.target === $("detail")) $("detail").close();
};
function setTheme(light) {
  document.body.classList.toggle("light", light);
  $("theme").setAttribute(
    "aria-label",
    light ? "Switch to dark mode" : "Switch to light mode",
  );
  localStorage.setItem("movbench-theme", light ? "light" : "dark");
}
$("theme").onclick = () => setTheme(!document.body.classList.contains("light"));
setTheme(localStorage.getItem("movbench-theme") === "light");
$("export").onclick = () => {
  if (!state.data) return;
  const url = URL.createObjectURL(
    new Blob([JSON.stringify(state.data, null, 2)], {
      type: "application/json",
    }),
  );
  const a = document.createElement("a");
  a.href = url;
  a.download = `movbench-${state.cohort || "empty"}.json`;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
};
document.addEventListener("keydown", (e) => {
  if (
    e.key === "/" &&
    !/INPUT|SELECT|TEXTAREA/.test(document.activeElement.tagName)
  ) {
    e.preventDefault();
    $("search").focus();
  }
});
async function initialize() {
  try {
    $("start-command").textContent =
      "uv run movbench generate \\\n  --model your-model \\\n  --out submissions/yours";
    state.meta = await getJson("/api/meta");
    state.meta.tasks.forEach((task) => {
      const option = document.createElement("option");
      option.value = task.id;
      option.textContent = task.name;
      $("task").append(option);
    });
    $("policy-id").textContent = state.meta.policy.id;
    $("policy-groups").innerHTML = Object.entries(state.meta.policy.groups)
      .map(
        ([name, instructions]) =>
          `<section class="policy-group"><h3>${escapeHtml(name.replaceAll("_", " "))} <span class="muted">${instructions.length}</span></h3><div class="instructions">${instructions.map((i) => `<code>${escapeHtml(i.toUpperCase())}</code>`).join("")}</div></section>`,
      )
      .join("");
    await load();
  } catch (error) {
    $("error").textContent = error.message;
    $("error").hidden = false;
  }
}
initialize();
setInterval(load, 30000);
