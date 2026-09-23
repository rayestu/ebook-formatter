"use strict";
const $ = (id) => document.getElementById(id);
const st = { id: null, page: 0, pages: 0, req: 0, timer: null, poll: null, uploadToken: 0 };

const KIND_LABEL = { header: "Header zone", footer: "Footer zone", pagenum: "Page number", running: "Running header/footer", footnote: "Footnote (moved to endnotes)", figure: "Figure (kept as an image)" };

function opts() {
  return {
    top: +$("top").value, bottom: +$("bottom").value,
    odd_even: $("oddEven").checked, top_even: +$("topEven").value, bottom_even: +$("bottomEven").value,
    dehyphenate: $("dehyphenate").checked, footnotes: $("footnotes").checked, chapters: $("chapters").checked,
  };
}
const qs = (o) => new URLSearchParams(o).toString();

// the zone actually in effect for a given (1-based) page number, mirroring Options.top_for/bottom_for
function zonesFor(pageNum1) {
  const o = opts();
  const even = o.odd_even && pageNum1 % 2 === 0;
  return { top: even ? o.top_even : o.top, bottom: even ? o.bottom_even : o.bottom };
}

function say(msg, isErr) { const s = $("status"); s.textContent = msg; s.classList.toggle("error", !!isErr); }

// ---------- upload ----------
const loadModal = $("loadModal");
$("loadClose").onclick = () => loadModal.close();
loadModal.addEventListener("cancel", (e) => e.preventDefault());   // Esc can't dismiss while reading

async function upload(file) {
  if (!file) return;
  if (!/\.pdf$/i.test(file.name) && file.type !== "application/pdf") { say("Please choose a PDF file.", true); return; }
  const my = ++st.uploadToken;

  $("dropTitle").textContent = file.name;
  $("loadTitle").textContent = "Reading PDF…";
  $("loadStatus").textContent = `Analyzing ${file.name} — this can take a while for long books.`;
  $("loadStatus").classList.remove("error");
  $("loadClose").hidden = true;
  if (!loadModal.open) loadModal.showModal();
  say(`Reading ${file.name} …`);

  const fd = new FormData(); fd.append("file", file);
  try {
    const r = await fetch("/api/upload", { method: "POST", body: fd });
    const j = await r.json();
    if (my !== st.uploadToken) return;          // superseded by a newer upload or a reset
    if (!r.ok) throw new Error(j.detail || r.statusText);
    Object.assign(st, { id: j.doc_id, pages: j.pages, page: 0 });
    $("docInfo").hidden = false;
    $("docInfo").textContent = `${j.pages} pages · TOC: ${{ bookmarks: "PDF bookmarks", visual: "contents page", none: "none found" }[j.toc_source]}` +
      (j.text_pages < j.pages ? ` · ${j.pages - j.text_pages} page(s) without text` : "") +
      (j.repaired ? " · ⚠ damaged file was repaired; some content may be missing" : "");
    $("newBook").hidden = false;
    $("opts").disabled = false; $("convert").disabled = false;
    $("download").hidden = true; $("log").textContent = ""; $("bar").style.width = "0";
    $("pageNum").max = j.pages; $("pageCount").textContent = `/ ${j.pages}`;
    say("Ready. Adjust settings, check the preview, then convert.");
    loadModal.close();
    refresh();
  } catch (e) {
    if (my !== st.uploadToken) return;
    say("Upload failed: " + e.message, true);
    $("loadTitle").textContent = "Could not read that PDF";
    $("loadStatus").textContent = e.message;
    $("loadStatus").classList.add("error");
    $("loadClose").hidden = false;
  }
}

function resetToUpload() {
  const old = st.id;
  st.uploadToken = (st.uploadToken || 0) + 1;    // ignore any in-flight upload/preview response
  st.req++;
  clearTimeout(st.timer);
  Object.assign(st, { id: null, page: 0, pages: 0 });
  if (old) fetch(`/api/${old}`, { method: "DELETE" }).catch(() => {});   // best-effort; frees server memory sooner

  $("dropTitle").textContent = "Drop a PDF here"; $("dropSub").textContent = "or click to browse";
  fileIn.value = "";
  $("docInfo").hidden = true; $("docInfo").textContent = "";
  $("newBook").hidden = true;
  $("opts").disabled = true; $("convert").disabled = true;
  $("top").value = 6; $("bottom").value = 6; $("topOut").textContent = "6%"; $("bottomOut").textContent = "6%";
  $("topEven").value = 6; $("bottomEven").value = 6; $("topEvenOut").textContent = "6%"; $("bottomEvenOut").textContent = "6%";
  $("oddEven").checked = false; $("evenZones").hidden = true;
  $("topLabel").firstChild.textContent = "Top margin "; $("bottomLabel").firstChild.textContent = "Bottom margin ";
  $("dehyphenate").checked = true; $("footnotes").checked = true; $("chapters").checked = true;
  $("prev").disabled = true; $("next").disabled = true;
  $("pageNum").value = 1; $("pageNum").disabled = true; $("pageCount").textContent = "/ –";
  const o = $("orig"); o.classList.add("empty"); o.textContent = "No document loaded";
  o.querySelectorAll(".hl, .band").forEach((n) => n.remove());
  const c = $("clean"); c.classList.add("empty"); c.textContent = "No document loaded";
  $("warn").textContent = "";
  $("download").hidden = true; $("log").textContent = ""; $("bar").style.width = "0";
  say("Choose a PDF to begin.");
  fileIn.click();
}
$("newBook").onclick = resetToUpload;

const drop = $("drop"), fileIn = $("file");
drop.addEventListener("click", () => fileIn.click());
drop.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); fileIn.click(); } });
fileIn.addEventListener("change", () => upload(fileIn.files[0]));
["dragenter", "dragover"].forEach((t) => drop.addEventListener(t, (e) => { e.preventDefault(); drop.classList.add("over"); }));
["dragleave", "drop"].forEach((t) => drop.addEventListener(t, (e) => { e.preventDefault(); drop.classList.remove("over"); }));
drop.addEventListener("drop", (e) => upload(e.dataTransfer.files[0]));
window.addEventListener("dragover", (e) => e.preventDefault());
window.addEventListener("drop", (e) => e.preventDefault());

// ---------- preview ----------
function schedule() { clearTimeout(st.timer); st.timer = setTimeout(refresh, 200); paintBands(); }

function paintBands() {
  const o = $("orig"); if (!o.querySelector("img")) return;
  o.querySelectorAll(".band").forEach((b) => b.remove());
  const z = zonesFor(st.page + 1);
  for (const [cls, v] of [["t", z.top], ["b", z.bottom]]) {
    if (v <= 0) continue;
    const d = document.createElement("div"); d.className = "band " + cls; d.style.height = v + "%"; o.appendChild(d);
  }
}

async function refresh() {
  if (!st.id) return;
  const n = st.page, my = ++st.req, o = opts();
  $("pageNum").value = n + 1;
  $("prev").disabled = n <= 0; $("next").disabled = n >= st.pages - 1; $("pageNum").disabled = false;
  try {
    const r = await fetch(`/api/${st.id}/page/${n}/preview?${qs(o)}`);
    const j = await r.json();
    if (my !== st.req) return;                 // a newer request superseded this one
    if (!r.ok) throw new Error(j.detail || r.statusText);
    renderOriginal(j, n);
    const c = $("clean"); c.classList.remove("empty");
    c.innerHTML = j.html || '<p class="muted">Nothing left on this page after cleanup.</p>';
    const notes = [];
    if (j.merged.from_prev) notes.push("continues from previous page");
    if (j.merged.to_next) notes.push("continues on next page");
    if (j.footnotes) notes.push(`${j.footnotes} footnote${j.footnotes > 1 ? "s" : ""} moved to chapter endnotes`);
    if (j.warning) notes.push(j.warning);
    $("warn").textContent = notes.join(" · ");
  } catch (e) { if (my === st.req) say("Preview failed: " + e.message, true); }
}

function renderOriginal(j, n) {
  const o = $("orig");
  let img = o.querySelector("img");
  o.classList.remove("empty");
  if (!img) { o.textContent = ""; img = new Image(); img.alt = "Original PDF page"; o.appendChild(img); }
  const src = `/api/${st.id}/page/${n}/image`;
  if (!img.src.endsWith(src)) img.src = src;
  o.querySelectorAll(".hl").forEach((h) => h.remove());
  for (const r of j.regions) {
    const [x0, y0, x1, y1] = r.bbox, d = document.createElement("div");
    d.className = "hl " + r.kind; d.title = `${KIND_LABEL[r.kind] || r.kind}${r.text ? ": " + r.text : ""}`;
    Object.assign(d.style, { left: x0 * 100 + "%", top: y0 * 100 + "%", width: (x1 - x0) * 100 + "%", height: (y1 - y0) * 100 + "%" });
    o.appendChild(d);
  }
  paintBands();
}

function go(n) {
  if (!st.id) return;
  st.page = Math.max(0, Math.min(st.pages - 1, n)); refresh();
}
$("prev").onclick = () => go(st.page - 1);
$("next").onclick = () => go(st.page + 1);
$("pageNum").addEventListener("change", (e) => go((parseInt(e.target.value, 10) || 1) - 1));
document.addEventListener("keydown", (e) => {
  if (/INPUT|TEXTAREA/.test(document.activeElement.tagName) && document.activeElement.type !== "range" && document.activeElement.type !== "checkbox") return;
  if (e.key === "ArrowLeft") go(st.page - 1); else if (e.key === "ArrowRight") go(st.page + 1);
});
$("clean").addEventListener("click", (e) => {      // keep footnote links inside the preview pane
  const a = e.target.closest('a[href^="#"]'); if (!a) return;
  e.preventDefault();
  const t = $("clean").querySelector(CSS.escape ? "#" + CSS.escape(a.getAttribute("href").slice(1)) : a.getAttribute("href"));
  if (t) t.scrollIntoView({ block: "center" });
});

for (const id of ["top", "bottom", "topEven", "bottomEven"])
  $(id).addEventListener("input", () => { $(id + "Out").textContent = $(id).value + "%"; schedule(); });
for (const id of ["dehyphenate", "footnotes", "chapters"]) $(id).addEventListener("change", schedule);

$("oddEven").addEventListener("change", () => {
  const on = $("oddEven").checked;
  $("evenZones").hidden = !on;
  $("topLabel").firstChild.textContent = on ? "Top margin (odd pages) " : "Top margin ";
  $("bottomLabel").firstChild.textContent = on ? "Bottom margin (odd pages) " : "Bottom margin ";
  if (on) {   // starting the split from whatever the single zone was set to, not back to 6%/6%
    $("topEven").value = $("top").value; $("topEvenOut").textContent = $("top").value + "%";
    $("bottomEven").value = $("bottom").value; $("bottomEvenOut").textContent = $("bottom").value + "%";
  }
  schedule();
});

// ---------- convert (modal) ----------
const modal = $("modal");
modal.addEventListener("cancel", (e) => { if (st.busy) e.preventDefault(); });   // Esc can't dismiss mid-conversion
$("mClose").onclick = () => modal.close();
$("reveal").onclick = () => fetch(`/api/${st.id}/reveal`, { method: "POST" });

$("convert").onclick = async () => {
  if (!st.id) return;
  st.busy = true;
  $("mTitle").textContent = "Converting…";
  $("bar").style.width = "0"; $("log").textContent = ""; $("mStatus").textContent = "Starting…"; $("mStatus").classList.remove("error");
  for (const id of ["reveal", "download", "mClose"]) $(id).hidden = true;
  if (!modal.open) modal.showModal();
  const r = await fetch(`/api/${st.id}/convert`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(opts()) });
  if (!r.ok) { finish(false, "Could not start the conversion."); return; }
  clearInterval(st.poll);
  st.poll = setInterval(pollStatus, 400);
};

function finish(ok, msg, saved) {
  st.busy = false; clearInterval(st.poll);
  $("mTitle").textContent = ok ? "EPUB ready" : "Conversion failed";
  $("mStatus").textContent = msg; $("mStatus").classList.toggle("error", !ok);
  $("mClose").hidden = false;
  if (ok) {
    $("reveal").hidden = !saved;
    const d = $("download"); d.href = `/api/${st.id}/download`; d.hidden = false;
  }
  say(ok ? "Done. Your EPUB is ready." : "Conversion failed. See the log.", !ok);
}

async function pollStatus() {
  try {
    const j = await (await fetch(`/api/${st.id}/status`)).json();
    $("bar").style.width = Math.round(j.progress * 100) + "%";
    const log = $("log"), atEnd = log.scrollTop + log.clientHeight >= log.scrollHeight - 8;
    log.textContent = j.log.join("\n"); if (atEnd) log.scrollTop = log.scrollHeight;
    $("mStatus").textContent = j.state === "running" ? `Converting… ${Math.round(j.progress * 100)}%` : $("mStatus").textContent;
    if (j.state === "done") {
      // the server saves to Downloads and opens the folder; wait for that log line (or give up after a few polls)
      st.waited = (st.waited || 0) + 1;
      if (j.saved || st.waited > 8 || j.log.some((l) => l.startsWith("Could not save"))) {
        st.waited = 0;
        finish(true, j.saved ? `Saved to ${j.saved}` : "Conversion finished; use “Download a copy”.", j.saved);
      }
    } else if (j.state === "error") { finish(false, "Something went wrong; see the log."); }
  } catch (e) { /* transient; keep polling */ }
}
