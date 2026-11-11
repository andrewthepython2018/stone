"use strict";
// Веб-версия «Кусковатость породы по фото»: интерфейс. Расчёт — на сервере (web/server.py).

const $ = (id) => document.getElementById(id);
const SIEVES = [50, 100, 150, 200, 300, 500, 1000];
const CLASS_NAMES = (() => {
  const out = []; let lo = 0;
  for (const s of SIEVES) { out.push(lo ? `${lo}–${s} мм` : `до ${s} мм`); lo = s; }
  out.push(`более ${lo} мм`); return out;
})();

const photos = [];          // {file, name, id, w, h, base, over, res, bucket, polygon, scale, scaleSrc, state}
let cur = null;
let busy = false;
let lastManualScale = null;
let queue = Promise.resolve();

// ---------------------------------------------------------------- статус
function status(text, kind = "") {
  const s = $("status"); s.textContent = text; s.className = kind;
}
function setBusy(b, text) {
  busy = b; $("busy").classList.toggle("show", b);
  if (text) $("busy").textContent = text;
  updateButtons();
}
function updateButtons() {
  const has = cur !== null && !busy;
  for (const id of ["autoBtn", "polyBtn", "lineBtn", "runBtn", "delBtn"]) $(id).disabled = !has;
  $("saveBtn").disabled = busy || !photos.some((p) => p.res);
  $("polyBtn").textContent = view.mode === "poly" && view.pts.length >= 3 ? "Замкнуть контур" : "Обвести контур";
  $("polyBtn").classList.toggle("on", view.mode === "poly");
  $("lineBtn").classList.toggle("on", view.mode === "line");
}

// ---------------------------------------------------------------- сервер
async function api(path, opts = {}) {
  const r = await fetch(path, opts);
  if (!r.ok) {
    let msg = `${r.status}`;
    try { const j = await r.json(); msg = j.detail || msg; } catch (_) { /* не JSON */ }
    const e = new Error(typeof msg === "string" ? msg : JSON.stringify(msg)); e.status = r.status; throw e;
  }
  return r;
}
function loadImg(src) {
  return new Promise((ok, fail) => { const i = new Image(); i.onload = () => ok(i); i.onerror = fail; i.src = src; });
}
async function upload(ph) {
  const fd = new FormData(); fd.append("file", ph.file, ph.name);
  const j = await (await api("/api/photo", { method: "POST", body: fd })).json();
  ph.id = j.id; ph.w = j.width; ph.h = j.height;
  ph.base = await loadImg(j.image);
}
async function runOn(ph, body) {
  const req = () => api(`/api/photo/${ph.id}/run`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  let r;
  try { r = await req(); }
  catch (e) {
    if (e.status !== 410) throw e;
    await upload(ph); r = await req();          // фото удалено с сервера — загружаем заново
  }
  return r.json();
}
function params() {
  return { bucket_width_mm: +$("widthMm").value || 3000, detail: $("detail").value, use_fines: $("fines").checked };
}

// ---------------------------------------------------------------- фото
function addFiles(files) {
  const imgs = [...files].filter((f) => /^image\//.test(f.type) || /\.(jpe?g|png|bmp|tiff?|webp)$/i.test(f.name));
  if (!imgs.length) { status("Это не фото: нужны jpg, png, bmp, tif или webp", "warn"); return; }
  for (const f of imgs) {
    if (photos.some((p) => p.name === f.name && p.file.size === f.size)) continue;   // без дублей
    const ph = { file: f, name: f.name, polygon: [], scale: null, scaleSrc: "", state: "в очереди" };
    photos.push(ph);
    if (!cur) select(ph);
    queue = queue.then(() => firstRun(ph));
  }
  renderList();
}
async function firstRun(ph) {
  if (!photos.includes(ph)) return;
  ph.state = "загружаю…"; renderList();
  setBusy(true, `Загружаю ${ph.name}…`);
  try {
    await upload(ph);
    if (ph === cur) { view.fit(); draw(); }
    await doAuto(ph);
  } catch (e) {
    ph.state = "ошибка"; status(`${ph.name}: ${e.message}`, "err");
  } finally { setBusy(false); renderList(); }
}
async function doAuto(ph) {
  ph.state = "ищу ковш…"; renderList();
  setBusy(true, "Ищу ковш и считаю куски…");
  if (ph === cur) status("Ищу ковш и распознаю куски… (несколько секунд)");
  const j = await runOn(ph, { mode: "auto", ...params() });
  ph.bucket = j.bucket;
  if (j.bucket.mm_per_px && (ph.scaleSrc === "" || ph.scaleSrc === "ковш")) { ph.scale = j.bucket.mm_per_px; ph.scaleSrc = "ковш"; }
  if (j.ok) { ph.polygon = []; await setResult(ph, j); return; }
  ph.state = j.bucket.message || "ковш не найден";
  if (ph === cur) {
    showScale(ph); afterNoBucket(ph);
  }
}
function afterNoBucket(ph) {
  const why = ph.bucket?.message || "Ковш не найден";
  let tail;
  if (ph.scaleSrc === "ковш") tail = `Масштаб по ковшу ≈ ${ph.scale.toFixed(2)} мм/пикс (точен для области на глубине ковша).`;
  else if (ph.scale) tail = `Масштаб ${ph.scale.toFixed(2)} мм/пикс.`;
  else tail = "Масштаб на этом фото не известен: задайте «Эталон» (линия по предмету известной длины) или введите мм/пикс, иначе размеры будут условными.";
  status(`${why}. Обведите область для расчёта. ${tail}`, "warn");
  startMode("poly");
}
async function doPolygon(ph) {
  if (ph.scaleSrc === "" && lastManualScale) { ph.scale = lastManualScale; ph.scaleSrc = "вручную"; }
  ph.state = "считаю…"; renderList();
  setBusy(true, "Распознаю куски…"); status("Распознаю куски… (несколько секунд)");
  try {
    const j = await runOn(ph, { mode: "polygon", polygon: ph.polygon, scale: ph.scale, scale_src: ph.scaleSrc, ...params() });
    ph.bucket = j.bucket;
    if (j.scale) { ph.scale = j.scale; ph.scaleSrc = j.scale_src; }
    await setResult(ph, j);
  } catch (e) { ph.state = "ошибка"; status(e.message, "err"); }
  finally { setBusy(false); renderList(); }
}
async function setResult(ph, j) {
  ph.res = j; ph.over = await loadImg(j.overlay);
  ph.state = "";
  renderList();
  if (ph === cur) { $("showOv").checked = true; showScale(ph); draw(); describe(); }
  renderResults();
}
function describe() {
  const ph = cur;
  if (!ph || !ph.res) return;
  const v = variant(ph);
  status(v.text, ph.res.source === "контур" && ph.scaleSrc === "условный" ? "warn" : "");
}
const variant = (ph) => ($("fines").checked ? ph.res.with_fines : ph.res.without_fines);

function select(ph) {
  cur = ph; stopMode();
  view.fit(); draw(); renderList(); renderResults(); showScale(ph);
  if (!ph) { status("Откройте фото ковша с породой (можно несколько) или перетащите их в окно."); return; }
  if (ph.res) describe();
  else if (ph.bucket && !ph.bucket.usable) afterNoBucket(ph);
  else status(ph.state ? `${ph.name}: ${ph.state}` : "Нажмите «Найти ковш» или «Обвести контур»");
}
function showScale(ph) {
  const v = ph && ph.scale ? ph.scale : lastManualScale;
  $("scale").value = v ? (+v).toFixed(2) : "";
}
function removeCurrent() {
  if (!cur || busy) return;
  const i = photos.indexOf(cur);
  if (cur.id) fetch(`/api/photo/${cur.id}`, { method: "DELETE" }).catch(() => {});
  photos.splice(i, 1);
  select(photos[Math.min(i, photos.length - 1)] || null);
  renderList(); renderResults();
}

// ---------------------------------------------------------------- список
function fmtX(st, xt, k) { return xt && xt[k] ? xt[k] : (st[k] != null ? Math.round(st[k]) : "—"); }
function renderList() {
  const ul = $("list"); ul.innerHTML = "";
  if (!photos.length) { ul.innerHTML = '<li class="empty">Фото пока нет</li>'; }
  photos.forEach((ph, i) => {
    const li = document.createElement("li");
    if (ph === cur) li.className = "cur";
    let sub = ph.state;
    if (ph.res && !ph.state) {
      const v = variant(ph);
      sub = v.stats.x50_mm != null ? `X50 ${fmtX(v.stats, v.x_text, "x50_mm")} мм (${ph.res.source})` : `мало кусков (${ph.res.source})`;
    }
    li.innerHTML = `<div class="nm"></div><div class="sub"></div>`;
    li.querySelector(".nm").textContent = `${i + 1}. ${ph.name}`;
    li.querySelector(".sub").textContent = sub || "";
    li.onclick = () => { if (ph !== cur) select(ph); };
    ul.appendChild(li);
  });
  $("drop").classList.toggle("hidden", photos.length > 0);
  updateButtons();
}

// ---------------------------------------------------------------- просмотр и обвод
const canvas = $("view"), ctx = canvas.getContext("2d");
const view = {
  s: 1, ox: 0, oy: 0, mode: "view", pts: [], line: null, drag: null,
  fit() {
    const r = canvas.getBoundingClientRect();
    const ph = cur;
    if (!ph || !ph.w) return;
    this.s = Math.min(r.width / ph.w, r.height / ph.h);
    this.ox = (r.width - ph.w * this.s) / 2; this.oy = (r.height - ph.h * this.s) / 2;
  },
  toImg(e) { const r = canvas.getBoundingClientRect(); return [(e.clientX - r.left - this.ox) / this.s, (e.clientY - r.top - this.oy) / this.s]; },
};
function resizeCanvas() {
  const r = canvas.getBoundingClientRect(), d = window.devicePixelRatio || 1;
  canvas.width = Math.round(r.width * d); canvas.height = Math.round(r.height * d);
  draw();
}
function draw() {
  const d = window.devicePixelRatio || 1;
  ctx.setTransform(1, 0, 0, 1, 0, 0);
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  const ph = cur;
  if (!ph || !ph.base) return;
  ctx.setTransform(d * view.s, 0, 0, d * view.s, d * view.ox, d * view.oy);
  const showOv = ph.over && $("showOv").checked && view.mode === "view";
  ctx.drawImage(showOv ? ph.over : ph.base, 0, 0);
  const lw = 2.5 / view.s;
  const poly = view.mode === "poly" ? view.pts : (!showOv ? ph.polygon : []);
  if (poly.length) {
    ctx.beginPath(); ctx.moveTo(poly[0][0], poly[0][1]);
    for (const p of poly.slice(1)) ctx.lineTo(p[0], p[1]);
    if (view.mode !== "poly") ctx.closePath();
    ctx.strokeStyle = "#ff00ff"; ctx.lineWidth = lw; ctx.stroke();
    if (view.mode === "poly") {
      ctx.fillStyle = "#ffff00";
      for (const p of poly) { ctx.beginPath(); ctx.arc(p[0], p[1], 4 / view.s, 0, 7); ctx.fill(); }
    }
  }
  if (view.line) {
    const [a, b] = view.line;
    ctx.beginPath(); ctx.moveTo(a[0], a[1]); ctx.lineTo(b[0], b[1]);
    ctx.strokeStyle = "#00ff66"; ctx.lineWidth = lw; ctx.stroke();
  }
}
function startMode(m) {
  if (!cur || !cur.base) return;
  view.mode = m; view.pts = []; view.line = null;
  $("hint").textContent = m === "poly"
    ? "Обведите область: щелчки — вершины; двойной щелчок, правая кнопка, Enter или кнопка «Замкнуть контур» — готово. Можно вести с нажатой кнопкой. Backspace — шаг назад, Esc — отмена."
    : "Проведите линию вдоль предмета известной длины (нажать — вести — отпустить). Esc — отмена.";
  draw(); updateButtons();
}
function stopMode() { view.mode = "view"; view.pts = []; view.line = null; view.drag = null; $("hint").textContent = ""; draw(); updateButtons(); }
function closePoly() {
  const pts = dedupe(view.pts);
  if (pts.length < 3) { status("Нужно хотя бы 3 точки контура", "warn"); return; }
  const ph = cur; ph.polygon = pts; stopMode(); $("showOv").checked = false; draw();
  queue = queue.then(() => doPolygon(ph));
}
function dedupe(pts) {
  const out = [];
  for (const p of pts) { const q = out[out.length - 1]; if (!q || Math.hypot(p[0] - q[0], p[1] - q[1]) * view.s > 3) out.push(p); }
  return out;
}

canvas.addEventListener("pointerdown", (e) => {
  if (!cur || !cur.base) return;
  canvas.setPointerCapture(e.pointerId);
  const p = view.toImg(e);
  if (view.mode === "poly") {
    if (e.button === 2) { closePoly(); return; }
    if (e.button !== 0) return;
    view.pts.push(p); view.drag = { free: 0 };
  } else if (view.mode === "line") {
    view.line = [p, p]; view.drag = { line: true };
  } else {
    view.drag = { x: e.clientX, y: e.clientY, ox: view.ox, oy: view.oy };
  }
  draw(); updateButtons();
});
canvas.addEventListener("pointermove", (e) => {
  const dr = view.drag; if (!dr) return;
  const p = view.toImg(e);
  if (view.mode === "poly") {
    const q = view.pts[view.pts.length - 1];
    if (Math.hypot(p[0] - q[0], p[1] - q[1]) * view.s > 8) { view.pts.push(p); dr.free++; }
  } else if (view.mode === "line") view.line[1] = p;
  else { view.ox = dr.ox + e.clientX - dr.x; view.oy = dr.oy + e.clientY - dr.y; }
  draw();
});
canvas.addEventListener("pointerup", () => {
  const dr = view.drag; view.drag = null; if (!dr) return;
  if (view.mode === "poly" && dr.free > 5) closePoly();            // обвели, ведя с нажатой кнопкой
  else if (view.mode === "line") {
    const [a, b] = view.line, L = Math.hypot(b[0] - a[0], b[1] - a[1]);
    if (L * view.s < 10) { view.line = null; draw(); return; }
    askLength(L);
  }
  updateButtons();
});
canvas.addEventListener("dblclick", (e) => {
  if (view.mode === "poly") { e.preventDefault(); closePoly(); }
  else if (view.mode === "view") { view.fit(); draw(); }
});
canvas.addEventListener("contextmenu", (e) => e.preventDefault());
canvas.addEventListener("wheel", (e) => {
  if (!cur || !cur.base) return;
  e.preventDefault();
  const r = canvas.getBoundingClientRect(), mx = e.clientX - r.left, my = e.clientY - r.top;
  const k = Math.exp(-e.deltaY * 0.0015), s2 = Math.min(20, Math.max(0.05, view.s * k));
  view.ox = mx - (mx - view.ox) * s2 / view.s; view.oy = my - (my - view.oy) * s2 / view.s; view.s = s2;
  draw();
}, { passive: false });
document.addEventListener("keydown", (e) => {
  if (e.target.tagName === "INPUT" || $("lenDlg").open) return;
  if (view.mode === "poly") {
    if (e.key === "Enter") closePoly();
    else if (e.key === "Backspace") { view.pts.pop(); draw(); updateButtons(); }
    else if (e.key === "Escape") { stopMode(); describe(); }
  } else if (view.mode === "line" && e.key === "Escape") stopMode();
});
function askLength(L) {
  const dlg = $("lenDlg");
  $("lenText").textContent = `Длина линии ${Math.round(L)} пикс. Реальная длина, мм:`;
  dlg.returnValue = "";
  dlg.onclose = () => {
    const mm = +$("lenMm").value;
    stopMode();
    if (dlg.returnValue !== "ok" || !(mm > 0) || !cur) return;
    const ph = cur; ph.scale = mm / L; ph.scaleSrc = "эталон"; lastManualScale = ph.scale; showScale(ph);
    status(`Масштаб по эталону: ${ph.scale.toFixed(2)} мм/пикс`);
    if (ph.polygon.length) queue = queue.then(() => doPolygon(ph));
    else startMode("poly");
  };
  dlg.showModal(); $("lenMm").select();
}

// ---------------------------------------------------------------- график и таблица
const NS = "http://www.w3.org/2000/svg";
function el(svg, tag, attrs, text) {
  const n = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v);
  if (text != null) n.textContent = text;
  svg.appendChild(n); return n;
}
const cssVar = (n) => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
function curveOf(st) {
  if (st.curve) return st.curve;
  const pts = Object.entries(st.passing_pct).map(([k, v]) => [+k, v]).filter(([k]) => k < st.d_max_mm);
  pts.push([st.x10_mm, 10], [st.x50_mm, 50], [st.x80_mm, 80], [st.x90_mm, 90], [st.d_max_mm, 100]);
  pts.sort((a, b) => a[0] - b[0]);
  let m = 0; return pts.map(([x, y]) => [x, (m = Math.max(m, y))]);
}
function renderResults() {
  const done = photos.filter((p) => p.res && variant(p).stats.x50_mm != null);
  const c = cur && done.includes(cur) ? cur : null;
  // --- состав
  const s1 = $("chart1"); s1.innerHTML = "";
  const L = 40, R = 370, T = 10, B = 195;
  for (const y of [0, 20, 40, 60, 80, 100]) {
    const py = B - (B - T) * y / 100;
    el(s1, "line", { x1: L, x2: R, y1: py, y2: py, class: "grid" });
    el(s1, "text", { x: L - 5, y: py + 4, "text-anchor": "end", class: "muted" }, y);
  }
  el(s1, "text", { x: (L + R) / 2, y: 225, "text-anchor": "middle", class: "muted" }, "размер куска, мм (лог. шкала) · проход, % по массе");
  if (!done.length) {
    el(s1, "text", { x: (L + R) / 2, y: (T + B) / 2, "text-anchor": "middle", class: "muted" }, "нет результатов");
  } else {
    const xs = done.flatMap((p) => curveOf(variant(p).stats).map((q) => q[0])).filter((v) => v > 0);
    const x0 = Math.log10(Math.max(10, Math.min(...xs) * 0.8)), x1 = Math.log10(Math.max(...xs) * 1.2);
    const px = (v) => L + (R - L) * (Math.log10(Math.max(v, 1e-6)) - x0) / (x1 - x0);
    const py = (y) => B - (B - T) * y / 100;
    for (const t of [10, 20, 50, 100, 200, 500, 1000, 2000]) {
      const lt = Math.log10(t); if (lt < x0 || lt > x1) continue;
      el(s1, "line", { x1: px(t), x2: px(t), y1: T, y2: B, class: "grid" });
      el(s1, "text", { x: px(t), y: B + 14, "text-anchor": "middle", class: "muted" }, t);
    }
    const path = (st) => curveOf(st).filter((q) => Math.log10(q[0]) >= x0 - 1e-9).map((q, i) => `${i ? "L" : "M"}${px(q[0]).toFixed(1)},${py(q[1]).toFixed(1)}`).join("");
    for (const p of done) if (p !== c) el(s1, "path", { d: path(variant(p).stats), fill: "none", stroke: cssVar("--c-other"), "stroke-width": 1.2 });
    if (c) {
      const v = variant(c), st = v.stats;
      el(s1, "path", { d: path(st), fill: "none", stroke: cssVar("--c-cur"), "stroke-width": 2.5 });
      for (const [k, col, lab] of [["x50_mm", "--c-cur", "X50"], ["x80_mm", "--c-x80", "X80"]]) {
        const x = Math.min(R, Math.max(L, px(st[k])));
        el(s1, "line", { x1: x, x2: x, y1: T, y2: B, stroke: cssVar(col), "stroke-dasharray": "4 3" });
        el(s1, "text", { x: x + 3, y: k === "x50_mm" ? T + 12 : T + 26, fill: cssVar(col), style: `fill:${cssVar(col)}` }, `${lab} ${fmtX(st, v.x_text, k)}`);
      }
    }
  }
  // --- X50/X80 по фото
  const s2 = $("chart2"); s2.innerHTML = "";
  const L2 = 40, R2 = 370, T2 = 22, B2 = 160;
  if (done.length) {
    const x50 = done.map((p) => variant(p).stats.x50_mm), x80 = done.map((p) => variant(p).stats.x80_mm);
    const ymax = Math.max(...x80) * 1.3 || 1;
    const step = niceStep(ymax / 4);
    const py = (y) => B2 - (B2 - T2) * y / ymax;
    for (let y = 0; y <= ymax; y += step) {
      el(s2, "line", { x1: L2, x2: R2, y1: py(y), y2: py(y), class: "grid" });
      el(s2, "text", { x: L2 - 5, y: py(y) + 4, "text-anchor": "end", class: "muted" }, Math.round(y));
    }
    const w = (R2 - L2) / done.length, bx = (i) => L2 + w * (i + 0.5);
    done.forEach((p, i) => {
      el(s2, "rect", { x: bx(i) - w * 0.3, width: w * 0.6, y: py(x50[i]), height: B2 - py(x50[i]), rx: 2,
        fill: cssVar(p === c ? "--c-cur" : "--c-bar") });
      el(s2, "text", { x: bx(i), y: B2 + 14, "text-anchor": "middle", class: "muted" }, photos.indexOf(p) + 1);
    });
    el(s2, "path", { d: x80.map((v, i) => `${i ? "L" : "M"}${bx(i)},${py(v)}`).join(""), fill: "none", stroke: cssVar("--c-x80"), "stroke-width": 2 });
    x80.forEach((v, i) => el(s2, "circle", { cx: bx(i), cy: py(v), r: 3.5, fill: cssVar("--c-x80") }));
    const m50 = avg(x50), m80 = avg(x80);
    el(s2, "line", { x1: L2, x2: R2, y1: py(m50), y2: py(m50), stroke: cssVar("--c-cur"), "stroke-dasharray": "4 3" });
    el(s2, "line", { x1: L2, x2: R2, y1: py(m80), y2: py(m80), stroke: cssVar("--c-x80"), "stroke-dasharray": "4 3" });
    el(s2, "rect", { x: L2 + 4, y: 4, width: 10, height: 10, fill: cssVar("--c-cur") });
    el(s2, "text", { x: L2 + 18, y: 13 }, "X50 — медианный размер");
    el(s2, "circle", { cx: L2 + 178, cy: 9, r: 4, fill: cssVar("--c-x80") });
    el(s2, "text", { x: L2 + 186, y: 13 }, "X80   (пунктир — средние)");
    el(s2, "text", { x: (L2 + R2) / 2, y: 186, "text-anchor": "middle", class: "muted" }, "фото № · размер куска, мм");
  } else {
    el(s2, "text", { x: 190, y: 95, "text-anchor": "middle", class: "muted" }, "нет результатов");
  }
  // --- итог и таблица
  const fon = $("fines").checked;
  let head = "";
  if (c) {
    const v = variant(c), s = v.stats;
    head = `Это фото: X50 = ${fmtX(s, v.x_text, "x50_mm")} мм, X80 = ${fmtX(s, v.x_text, "x80_mm")} мм, d_ср = ${Math.round(s.d_mean_mm)} мм, макс. ${Math.round(s.d_max_mm)} мм\n`;
  }
  if (done.length) {
    const ms = { x50_mm: avg(done.map((p) => variant(p).stats.x50_mm)), x80_mm: avg(done.map((p) => variant(p).stats.x80_mm)) };
    const lim = avg(done.map((p) => variant(p).stats.fines_below_mm || 0));
    const f = (k) => (fon && lim && ms[k] < lim ? `< ${Math.round(lim)}` : Math.round(ms[k]));
    $("total").textContent = head + `Среднее по фото (${done.length}): X50 = ${f("x50_mm")} мм, X80 = ${f("x80_mm")} мм`;
  } else $("total").textContent = "Кусковатость: —";
  const withP = photos.filter((p) => p.res && variant(p).stats.passing_pct);
  const curCls = c ? variant(c).classes : null;
  let avgCls = null;
  if (withP.length) {
    const mean = SIEVES.map((s) => avg(withP.map((p) => variant(p).stats.passing_pct[String(s)])));
    let prev = 0; avgCls = mean.map((m) => { const r = Math.max(0, m - prev); prev = m; return r; });
    avgCls.push(Math.max(0, 100 - prev));
  }
  const tb = $("table").tBodies[0]; tb.innerHTML = "";
  CLASS_NAMES.forEach((n, i) => {
    const tr = tb.insertRow();
    tr.insertCell().textContent = n;
    tr.insertCell().textContent = curCls ? curCls[i][1].toFixed(1) : "—";
    tr.insertCell().textContent = avgCls ? avgCls[i].toFixed(1) : "—";
  });
  const tr = tb.insertRow(); tr.className = "fines";
  tr.insertCell().textContent = fon ? "в т.ч. мелочь" : "мелочь (не учтена)";
  tr.insertCell().textContent = c ? variant(c).stats.fines_pct.toFixed(1) : "—";
  tr.insertCell().textContent = done.length ? avg(done.map((p) => variant(p).stats.fines_pct || 0)).toFixed(1) : "—";
}
const avg = (a) => a.reduce((x, y) => x + y, 0) / a.length;
function niceStep(v) { const e = 10 ** Math.floor(Math.log10(v)); for (const m of [1, 2, 2.5, 5, 10]) if (m * e >= v) return m * e; return 10 * e; }

// ---------------------------------------------------------------- отчёт
async function saveReport() {
  const ids = photos.map((p) => (p.res ? p.id : ""));
  setBusy(true, "Готовлю отчёт…");
  try {
    const r = await api("/api/report", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ids, use_fines: $("fines").checked }) });
    const blob = await r.blob();
    const name = (r.headers.get("Content-Disposition") || "").match(/filename="(.+)"/)?.[1] || "kuskovatost_foto.zip";
    const a = document.createElement("a"); a.href = URL.createObjectURL(blob); a.download = name;
    document.body.appendChild(a); a.click(); a.remove(); setTimeout(() => URL.revokeObjectURL(a.href), 5000);
    status(`Отчёт скачан: ${name} (results.csv открывается в Excel, overlays — фото с кусками)`);
  } catch (e) { status(`Отчёт: ${e.message}`, "err"); }
  finally { setBusy(false); }
}

// ---------------------------------------------------------------- кнопки
$("openBtn").onclick = () => $("file").click();
$("file").onchange = (e) => { addFiles(e.target.files); e.target.value = ""; };
$("autoBtn").onclick = () => { const ph = cur; if (!ph) return; stopMode();
  queue = queue.then(async () => { try { await doAuto(ph); } catch (e) { status(e.message, "err"); } finally { setBusy(false); renderList(); } }); };
$("polyBtn").onclick = () => { if (view.mode === "poly" && view.pts.length >= 3) closePoly(); else if (view.mode === "poly") { stopMode(); describe(); } else { $("showOv").checked = false; startMode("poly"); } };
$("lineBtn").onclick = () => (view.mode === "line" ? stopMode() : startMode("line"));
$("runBtn").onclick = () => { const ph = cur; if (!ph) return;
  if (ph.polygon.length) queue = queue.then(() => doPolygon(ph)); else $("autoBtn").onclick(); };
$("delBtn").onclick = removeCurrent;
$("saveBtn").onclick = saveReport;
$("showOv").onchange = draw;
$("fines").onchange = () => { renderList(); renderResults(); describe(); };
$("scale").onchange = () => {
  const v = +$("scale").value; if (!(v > 0)) return;
  lastManualScale = v;
  if (cur && (!cur.scale || Math.abs(cur.scale - v) > 0.004)) { cur.scale = v; cur.scaleSrc = "вручную"; }
};
$("widthMm").onchange = () => { if (cur && cur.scaleSrc === "ковш") cur.scaleSrc = ""; };
$("exBtn").onclick = async () => {
  try {
    const names = await (await api("/api/examples")).json();
    const files = await Promise.all(names.map(async (n) => new File([await (await api(`/api/examples/${encodeURIComponent(n)}`)).blob()], n, { type: "image/jpeg" })));
    addFiles(files);
  } catch (e) { status(`Примеры: ${e.message}`, "err"); }
};
const wrap = $("viewwrap");
for (const ev of ["dragenter", "dragover"]) wrap.addEventListener(ev, (e) => { e.preventDefault(); wrap.classList.add("dragover"); });
for (const ev of ["dragleave", "drop"]) wrap.addEventListener(ev, () => wrap.classList.remove("dragover"));
document.addEventListener("dragover", (e) => e.preventDefault());
document.addEventListener("drop", (e) => { e.preventDefault(); if (e.dataTransfer?.files?.length) addFiles(e.dataTransfer.files); });
new ResizeObserver(() => { const keep = view.s; resizeCanvas(); if (keep === 1 || view.mode === "view") { view.fit(); draw(); } }).observe(canvas);
matchMedia("(prefers-color-scheme: dark)").addEventListener("change", renderResults);
renderList(); renderResults();
