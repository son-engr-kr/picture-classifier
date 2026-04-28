"use strict";

// ---------- layouts ----------
const LAYOUTS = {
  1: { cols: 1, rows: 1 },
  2: { cols: 2, rows: 1 },
  4: { cols: 2, rows: 2 },
  8: { cols: 4, rows: 2 },
};

// ---------- state ----------
const state = {
  photos: [],
  byScene: new Map(),
  sceneOrder: [],
  selectedScene: null,
  filter: "all",
  personFilter: new Set(),  // person ids required (any-match)
  filteredPhotos: [],
  pageSize: 4,
  cursorIdx: 0,             // index into filteredPhotos (focused tile)
  modal: { open: false, idx: 0, fit: true },
  people: [],
  peopleById: new Map(),
};

// ---------- helpers ----------
const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => document.querySelectorAll(sel);
const enc = (p) => p.split("/").map(encodeURIComponent).join("/");
function matchesDecisionFilter(p) {
  if (state.filter === "all") return true;
  if (state.filter === "undecided") return p.decision == null;
  return p.decision === state.filter;
}
function matchesPersonFilter(p) {
  if (state.personFilter.size === 0) return true;
  for (const f of (p.faces || [])) {
    if (f.person_id && state.personFilter.has(f.person_id)) return true;
  }
  return false;
}
function matchesFilter(p) { return matchesDecisionFilter(p) && matchesPersonFilter(p); }

function bestPriority(photo) {
  let best = Infinity;
  for (const f of (photo.faces || [])) {
    if (!f.person_id) continue;
    const person = state.peopleById.get(f.person_id);
    if (!person || person.excluded) continue;
    if (person.priority < best) best = person.priority;
  }
  return best;
}

function prunePersonFilter() {
  for (const id of [...state.personFilter]) {
    const person = state.peopleById.get(id);
    if (!person || person.excluded) state.personFilter.delete(id);
  }
}
const pageIdx = () => Math.floor(state.cursorIdx / state.pageSize);
const pageCount = () => Math.max(1, Math.ceil(state.filteredPhotos.length / state.pageSize));
const visiblePhotos = () => {
  const start = pageIdx() * state.pageSize;
  return state.filteredPhotos.slice(start, start + state.pageSize);
};

// ---------- API ----------
async function loadDb() {
  const res = await fetch("/api/db");
  if (!res.ok) throw new Error(`db load failed: ${res.status}`);
  const data = await res.json();
  state.photos = data.photos;
  state.byScene = new Map();
  state.sceneOrder = [];
  for (const p of state.photos) {
    if (!state.byScene.has(p.scene)) {
      state.byScene.set(p.scene, []);
      state.sceneOrder.push(p.scene);
    }
    state.byScene.get(p.scene).push(p);
  }
  state.people = data.people || [];
  state.peopleById = new Map(state.people.map((p) => [p.id, p]));
  state.sceneGrouping = data.scene_grouping || { mode: "folder", gap_minutes: 30 };
  prunePersonFilter();
}

async function postDecision(rel_path, decision) {
  const res = await fetch("/api/decide", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ rel_path, decision }),
  });
  if (!res.ok) throw new Error(`decide failed: ${res.status}`);
  return res.json();
}

// ---------- rendering: sidebar ----------
function renderPeopleChips() {
  const wrap = $("#people-chips");
  wrap.innerHTML = "";
  if (!state.people.length) {
    const empty = document.createElement("div");
    empty.id = "people-empty";
    empty.textContent = "No clusters yet — click ↻ cluster after scoring.";
    wrap.appendChild(empty);
    return;
  }
  const visible = state.people.filter((p) => !p.excluded);
  if (!visible.length) {
    const empty = document.createElement("div");
    empty.id = "people-empty";
    empty.textContent = "All clusters excluded — open ⚙ to restore.";
    wrap.appendChild(empty);
    return;
  }
  // sort by priority asc for display
  const sorted = visible.slice().sort((a, b) => a.priority - b.priority);
  for (const person of sorted) {
    const chip = document.createElement("button");
    chip.className = "person-chip" + (state.personFilter.has(person.id) ? " active" : "");
    chip.title = `Toggle filter: only photos containing ${person.label}`;
    chip.innerHTML = `
      <img src="/face/${enc(person.ref.rel_path)}?idx=${person.ref.face_idx}" alt="" />
      <span class="pri">#${person.priority}</span>
      <span class="lbl">${person.label}</span>
      <span class="cnt">${person.count}</span>`;
    chip.addEventListener("click", () => {
      if (state.personFilter.has(person.id)) state.personFilter.delete(person.id);
      else state.personFilter.add(person.id);
      renderPeopleChips();
      state.cursorIdx = 0;
      recomputeFilter();
      renderMain();
    });
    wrap.appendChild(chip);
  }
}

function renderSidebar() {
  const list = $("#scene-list");
  list.innerHTML = "";
  let totalPick = 0, totalReview = 0, totalReject = 0, totalUndecided = 0;
  for (const scene of state.sceneOrder) {
    const photos = state.byScene.get(scene);
    const counts = { pick: 0, review: 0, reject: 0, undecided: 0 };
    for (const p of photos) counts[p.decision || "undecided"]++;
    totalPick += counts.pick; totalReview += counts.review;
    totalReject += counts.reject; totalUndecided += counts.undecided;

    const li = document.createElement("li");
    li.className = "scene-item" + (state.selectedScene === scene ? " active" : "");
    const n = photos.length;
    li.innerHTML = `
      <span class="name">${scene}</span>
      <span class="stats">${n} shots · pick ${counts.pick} · rev ${counts.review} · rej ${counts.reject} · — ${counts.undecided}</span>
      <span class="bar">
        <span class="b-pick" style="width:${100*counts.pick/n}%"></span>
        <span class="b-review" style="width:${100*counts.review/n}%"></span>
        <span class="b-reject" style="width:${100*counts.reject/n}%"></span>
      </span>`;
    li.addEventListener("click", () => selectScene(scene));
    list.appendChild(li);
  }
  const total = state.photos.length;
  const decided = total - totalUndecided;
  $("#overall-progress").textContent =
    `${total} shots · decided ${decided}/${total} (${total ? Math.round(100*decided/total) : 0}%) · ` +
    `pick ${totalPick} rev ${totalReview} rej ${totalReject}`;
}

// ---------- rendering: main ----------
function selectScene(scene) {
  state.selectedScene = scene;
  state.cursorIdx = 0;
  recomputeFilter();
  renderSidebar();
  renderMain();
}

function recomputeFilter() {
  if (!state.selectedScene) { state.filteredPhotos = []; return; }
  const photos = state.byScene.get(state.selectedScene) || [];
  let arr = photos.filter(matchesFilter);
  if (state.peopleById.size > 0) {
    arr = arr.slice().sort((a, b) => {
      const pa = bestPriority(a), pb = bestPriority(b);
      if (pa !== pb) return pa - pb;
      // stable secondary: by rel_path
      return a.rel_path < b.rel_path ? -1 : a.rel_path > b.rel_path ? 1 : 0;
    });
  }
  state.filteredPhotos = arr;
  if (state.cursorIdx >= state.filteredPhotos.length) {
    state.cursorIdx = Math.max(0, state.filteredPhotos.length - 1);
  }
}

function renderMain() {
  applyLayoutCSS();
  renderHeader();
  renderGrid();
  renderNextPreview();
}

function applyLayoutCSS() {
  const L = LAYOUTS[state.pageSize];
  const grid = $("#grid");
  grid.style.setProperty("--cols", L.cols);
  grid.style.setProperty("--rows", L.rows);
}

function renderHeader() {
  const total = state.filteredPhotos.length;
  const scenePhotos = state.selectedScene
    ? (state.byScene.get(state.selectedScene) || []) : [];
  const sceneTotal = scenePhotos.length;
  const sceneUndecided = scenePhotos.reduce((n, p) => n + (p.decision == null ? 1 : 0), 0);
  $("#scene-title").textContent = state.selectedScene || "— select a scene —";
  $("#scene-stats").textContent = state.selectedScene
    ? `${total} of ${sceneTotal} shown · filter: ${state.filter}`
    : "";
  $("#page-indicator").textContent = total
    ? `page ${pageIdx() + 1}/${pageCount()}`
    : "—";
  $("#prev-page").disabled = pageIdx() === 0 || total === 0;
  $("#next-page").disabled = pageIdx() >= pageCount() - 1 || total === 0;
  const rejectBtn = $("#reject-undecided-btn");
  rejectBtn.disabled = sceneUndecided === 0;
  rejectBtn.textContent = sceneUndecided > 0
    ? `✕ reject ${sceneUndecided} undecided`
    : "✕ reject undecided";
  const totalPicks = state.photos.reduce((n, p) => n + (p.decision === "pick" ? 1 : 0), 0);
  const exportBtn = $("#export-picks-btn");
  exportBtn.disabled = totalPicks === 0;
  exportBtn.textContent = totalPicks > 0
    ? `📁 export ${totalPicks} pick${totalPicks > 1 ? "s" : ""}`
    : "📁 export picks";
}

function renderGrid() {
  const grid = $("#grid");
  grid.innerHTML = "";
  const start = pageIdx() * state.pageSize;
  const visible = visiblePhotos();
  visible.forEach((p, i) => {
    const absIdx = start + i;
    const tile = document.createElement("div");
    const orientation = (p.width && p.height && p.height > p.width) ? "portrait" : "landscape";
    tile.className = "tile " + orientation
      + (p.decision ? " decision-" + p.decision : "")
      + (absIdx === state.cursorIdx ? " focused" : "");
    const auto = p.auto_suggestion || "";
    const badness = p.scores?.badness != null ? p.scores.badness.toFixed(2) : "—";
    const fname = p.rel_path.split("/").pop();
    const visibleFaces = (p.faces || [])
      .map((f, fi) => ({ f, fi, person: f.person_id ? state.peopleById.get(f.person_id) : null }))
      .filter(({ person }) => !(person && person.excluded))
      .sort((a, b) => {
        const pa = a.person ? a.person.priority : Infinity;
        const pb = b.person ? b.person.priority : Infinity;
        return pa - pb;
      });
    const faceCount = visibleFaces.length;
    const facesHtml = faceCount
      ? `<div class="tile-faces">${
          visibleFaces.map(({ fi }) =>
            `<div class="face-thumb"><img loading="lazy" src="/face/${enc(p.rel_path)}?idx=${fi}" alt="" /></div>`
          ).join("")
        }</div>`
      : "";
    tile.innerHTML = `
      <div class="tile-content">
        ${facesHtml}
        <div class="tile-img" data-action="open">
          <img loading="lazy" src="/thumb/${enc(p.rel_path)}" alt="" />
          ${auto ? `<span class="auto-badge ${auto}">auto: ${auto}</span>` : ""}
          <span class="badness">${badness}</span>
        </div>
      </div>
      <div class="tile-name">${fname}${faceCount ? ` · ${faceCount} face${faceCount>1?"s":""}` : ""}</div>
      <div class="tile-action-bar">
        <button class="btn-decision${p.decision === "reject" ? " active" : ""}" data-decision="reject">REJECT <span class="kbd">R</span></button>
        <button class="btn-decision${p.decision === "review" ? " active" : ""}" data-decision="review">REVIEW <span class="kbd">V</span></button>
        <button class="btn-decision${p.decision === "pick" ? " active" : ""}" data-decision="pick">PICK <span class="kbd">A</span></button>
      </div>`;
    tile.querySelector(".tile-img").addEventListener("click", () => openModal(absIdx));
    tile.querySelectorAll(".btn-decision").forEach((btn) => {
      btn.addEventListener("click", (e) => {
        e.stopPropagation();
        const wantDecision = btn.dataset.decision;
        const newDecision = p.decision === wantDecision ? null : wantDecision;
        decideAt(absIdx, newDecision);
      });
    });
    tile.addEventListener("mouseenter", () => focusAt(absIdx, false));
    grid.appendChild(tile);
  });
}

function renderNextPreview() {
  const preview = $("#next-preview");
  if (state.pageSize !== 1 || state.filteredPhotos.length === 0) {
    preview.classList.add("hidden");
    return;
  }
  const next = state.filteredPhotos[state.cursorIdx + 1];
  if (!next) {
    preview.classList.add("hidden");
    return;
  }
  preview.classList.remove("hidden");
  $("#next-preview-img").src = "/thumb/" + enc(next.rel_path);
  $("#next-preview-name").textContent = next.rel_path.split("/").pop();
}

// ---------- focus & paging ----------
function focusAt(absIdx, scroll) {
  if (absIdx < 0 || absIdx >= state.filteredPhotos.length) return;
  const oldPage = pageIdx();
  state.cursorIdx = absIdx;
  if (pageIdx() !== oldPage) {
    renderMain();
  } else {
    $$(".tile").forEach((el, i) => el.classList.toggle("focused", i === (absIdx - oldPage * state.pageSize)));
    if (state.pageSize === 1) renderNextPreview();
  }
}

function gotoPage(delta) {
  const newPage = pageIdx() + delta;
  if (newPage < 0 || newPage >= pageCount()) return;
  const newPageStart = newPage * state.pageSize;
  if (delta < 0) {
    // Previous: land on the last tile of the new page.
    const newPageEnd = Math.min(newPageStart + state.pageSize, state.filteredPhotos.length);
    state.cursorIdx = Math.max(newPageStart, newPageEnd - 1);
  } else {
    state.cursorIdx = newPageStart;
  }
  renderMain();
}

function tryAutoAdvance() {
  // If every visible photo has a decision, move to the first photo of the next page.
  const visible = visiblePhotos();
  if (!visible.length) return;
  const allDecided = visible.every((p) => p.decision != null);
  if (!allDecided) return;
  if (pageIdx() < pageCount() - 1) {
    state.cursorIdx = (pageIdx() + 1) * state.pageSize;
    renderMain();
  }
}

// ---------- decisions ----------
async function decideAt(absIdx, decision) {
  const photo = state.filteredPhotos[absIdx];
  if (!photo) return;
  const updated = await postDecision(photo.rel_path, decision);
  Object.assign(photo, updated);
  // Re-filter (a decided photo might leave the current filter view)
  const wasInFilter = matchesFilter(photo);
  recomputeFilter();
  renderSidebar();
  if (!wasInFilter && state.filteredPhotos.indexOf(photo) === -1) {
    // (no-op) photo no longer matches; cursor may have shifted
  }
  // Move cursor: stay at same position (which now points to the next photo
  // if the just-decided one left the filter), else advance by 1 within page.
  if (state.cursorIdx >= state.filteredPhotos.length) {
    state.cursorIdx = Math.max(0, state.filteredPhotos.length - 1);
  }
  renderMain();
  if (state.modal.open) {
    state.modal.idx = state.cursorIdx;
    renderModal();
  } else {
    tryAutoAdvance();
  }
}

// ---------- export picks ----------
async function openExportModal() {
  const res = await fetch("/api/export/picks/preview");
  if (!res.ok) {
    alert("Could not load export info: " + res.status);
    return;
  }
  const info = await res.json();
  $("#export-count").textContent = `${info.count} photo${info.count === 1 ? "" : "s"}`;
  $("#export-target").value = info.default_target;
  $("#export-mode").value = "folder";
  refreshExportModeHelp();
  $("#export-status").textContent = "";
  $("#export-confirm").disabled = info.count === 0;
  $("#export-modal").classList.remove("hidden");
}

function refreshExportModeHelp() {
  const mode = $("#export-mode").value;
  const help = $("#export-mode-help");
  if (mode === "folder") {
    help.textContent = "Original subfolder structure (Scene_001/, …) is preserved.";
  } else if (mode === "flat") {
    help.textContent = "All picks land directly in the target folder; duplicates get _1, _2 suffixes.";
  } else {
    help.textContent = "One subfolder per unique combo of non-excluded people (A/, A & B/, …). Photos with no recognized people go to Others/.";
  }
}

function closeExportModal() {
  $("#export-modal").classList.add("hidden");
}

async function confirmExport() {
  const target = $("#export-target").value.trim();
  if (!target) { alert("Target folder is required."); return; }
  const mode = $("#export-mode").value;
  $("#export-confirm").disabled = true;
  $("#export-status").textContent = "Copying…";
  const res = await fetch("/api/export/picks", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ target_dir: target, mode }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    $("#export-status").textContent = "Failed: " + (err.detail || res.status);
    $("#export-confirm").disabled = false;
    return;
  }
  const result = await res.json();
  let html = `✓ Copied <b>${result.copied}</b> photos to<br><code>${result.target_dir}</code>`;
  if (result.skipped) html += `<br>Skipped ${result.skipped} (missing source).`;
  if (result.per_combo && Object.keys(result.per_combo).length) {
    const sorted = Object.entries(result.per_combo).sort((a, b) => b[1] - a[1]);
    html += `<br><span class="combo-summary">${sorted.map(([k, v]) => `${escapeHtml(k)}: ${v}`).join(" · ")}</span>`;
  }
  $("#export-status").innerHTML = html;
  $("#export-confirm").disabled = false;
}

// ---------- bulk actions ----------
async function rejectUndecidedInScene() {
  if (!state.selectedScene) return;
  const photos = state.byScene.get(state.selectedScene) || [];
  const targets = photos.filter((p) => p.decision == null);
  if (!targets.length) return;
  const ok = confirm(
    `Reject all ${targets.length} undecided photos in "${state.selectedScene}"?\n` +
    `This can be undone per-photo with U.`
  );
  if (!ok) return;
  const res = await fetch("/api/decide/bulk", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      rel_paths: targets.map((p) => p.rel_path),
      decision: "reject",
    }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    alert("Bulk reject failed: " + (err.detail || res.status));
    return;
  }
  const now = new Date().toISOString();
  for (const p of targets) {
    p.decision = "reject";
    p.decided_at = now;
  }
  recomputeFilter();
  renderSidebar();
  renderMain();
}

// ---------- modal ----------
function openModal(absIdx) {
  state.modal.open = true;
  state.modal.idx = absIdx;
  state.modal.fit = true;
  state.cursorIdx = absIdx;
  $("#modal").classList.remove("hidden");
  renderModal();
}

function closeModal() {
  state.modal.open = false;
  $("#modal").classList.add("hidden");
  renderMain();
}

function renderModal() {
  const photo = state.filteredPhotos[state.modal.idx];
  if (!photo) { closeModal(); return; }
  const img = $("#modal-image");
  img.src = "/img/" + enc(photo.rel_path);
  img.className = state.modal.fit ? "fit" : "actual";
  $("#modal-title").textContent = photo.rel_path;
  $("#modal-meta").textContent =
    `${state.modal.idx + 1}/${state.filteredPhotos.length} in ${photo.scene} · auto: ${photo.auto_suggestion || "—"}`;
  const dec = $("#modal-decision");
  dec.className = photo.decision || "none";
  dec.textContent = (photo.decision || "—").toUpperCase();
  const s = photo.scores || {};
  const eye = s.eye_open != null ? s.eye_open.toFixed(3) : "—";
  $("#modal-scores").textContent =
    `blur ${(s.blur ?? 0).toFixed(0)} (pct ${(s.blur_pct ?? 0).toFixed(2)}) · ` +
    `exp_z ${(s.exposure_zscore ?? 0).toFixed(2)} · eye ${eye} · badness ${(s.badness ?? 0).toFixed(2)}`;
}

function modalNav(delta) {
  if (!state.filteredPhotos.length) return;
  const i = Math.max(0, Math.min(state.filteredPhotos.length - 1, state.modal.idx + delta));
  if (i === state.modal.idx) return;
  state.modal.idx = i;
  state.cursorIdx = i;
  state.modal.fit = true;
  renderModal();
}

// ---------- keyboard ----------
function bindKeys() {
  document.addEventListener("keydown", async (e) => {
    if (e.metaKey || e.ctrlKey || e.altKey) return;
    const k = e.key;

    if (state.modal.open) {
      if (k === "Escape") { closeModal(); e.preventDefault(); return; }
      if (k === "ArrowRight" || k === " ") { modalNav(+1); e.preventDefault(); return; }
      if (k === "ArrowLeft") { modalNav(-1); e.preventDefault(); return; }
      if (k === "f" || k === "F" || k === "z" || k === "Z") {
        state.modal.fit = !state.modal.fit;
        $("#modal-image").className = state.modal.fit ? "fit" : "actual";
        e.preventDefault(); return;
      }
      let decision = null, hasDecision = false, advance = true;
      if (k === "1" || k === "r" || k === "R") { decision = "reject"; hasDecision = true; }
      else if (k === "2" || k === "v" || k === "V") { decision = "review"; hasDecision = true; }
      else if (k === "3" || k === "p" || k === "P" || k === "a" || k === "A") { decision = "pick"; hasDecision = true; }
      else if (k === "u" || k === "U") { decision = null; hasDecision = true; advance = false; }
      else return;
      e.preventDefault();
      await decideAt(state.modal.idx, decision);
      if (advance && hasDecision && decision !== null) modalNav(+1);
      return;
    }

    // grid mode
    if (!state.filteredPhotos.length) return;
    const layout = LAYOUTS[state.pageSize];
    const cols = layout.cols;
    const i = state.cursorIdx;

    const total = state.filteredPhotos.length;
    const pageStart = pageIdx() * state.pageSize;
    const pageEnd = Math.min(pageStart + state.pageSize, total);

    if (k === "ArrowRight") {
      if (i + 1 < total) focusAt(i + 1);
      e.preventDefault(); return;
    }
    if (k === "ArrowLeft") {
      if (i - 1 >= 0) focusAt(i - 1);
      e.preventDefault(); return;
    }
    if (k === "ArrowDown") {
      let next = i + cols;
      // If the column-preserving step crosses to the next page, snap to its first tile.
      if (next >= pageEnd && pageEnd < total) next = pageEnd;
      focusAt(Math.min(total - 1, next));
      e.preventDefault(); return;
    }
    if (k === "ArrowUp") {
      let next = i - cols;
      // If the column-preserving step crosses to the previous page, snap to its last tile.
      if (next < pageStart && pageStart > 0) next = pageStart - 1;
      focusAt(Math.max(0, next));
      e.preventDefault(); return;
    }
    if (k === "PageDown" || k === "]") { gotoPage(+1); e.preventDefault(); return; }
    if (k === "PageUp" || k === "[") { gotoPage(-1); e.preventDefault(); return; }
    if (k === "Enter") { openModal(i); e.preventDefault(); return; }

    let decision = null, hasDecision = false;
    if (k === "1" || k === "r" || k === "R") { decision = "reject"; hasDecision = true; }
    else if (k === "2" || k === "v" || k === "V") { decision = "review"; hasDecision = true; }
    else if (k === "3" || k === "p" || k === "P" || k === "a" || k === "A") { decision = "pick"; hasDecision = true; }
    else if (k === "u" || k === "U") { decision = null; hasDecision = true; }
    if (hasDecision) {
      e.preventDefault();
      await decideAt(i, decision);
    }
  });
}

// ---------- UI bindings ----------
function bindUi() {
  $$(".filter").forEach((b) => {
    b.addEventListener("click", () => {
      $$(".filter").forEach((x) => x.classList.toggle("active", x === b));
      state.filter = b.dataset.filter;
      state.cursorIdx = 0;
      recomputeFilter();
      renderMain();
    });
  });
  $$("#cols-toggle .cols").forEach((b) => {
    b.addEventListener("click", () => {
      $$("#cols-toggle .cols").forEach((x) => x.classList.toggle("active", x === b));
      state.pageSize = parseInt(b.dataset.cols, 10);
      renderMain();
    });
  });
  $("#prev-page").addEventListener("click", () => gotoPage(-1));
  $("#next-page").addEventListener("click", () => gotoPage(+1));
  $("#modal-close").addEventListener("click", closeModal);
  $("#rescore-btn").addEventListener("click", startRescore);
  $("#reject-undecided-btn").addEventListener("click", rejectUndecidedInScene);
  $("#export-picks-btn").addEventListener("click", openExportModal);
  $("#export-modal-close").addEventListener("click", closeExportModal);
  $("#export-cancel").addEventListener("click", closeExportModal);
  $("#export-confirm").addEventListener("click", confirmExport);
  $("#export-mode").addEventListener("change", refreshExportModeHelp);
  $("#people-cluster-btn").addEventListener("click", startCluster);
  $("#people-manage-btn").addEventListener("click", openPeopleModal);
  $("#people-modal-close").addEventListener("click", closePeopleModal);
  $("#people-save").addEventListener("click", savePeople);
  $("#switch-project-btn").addEventListener("click", async () => {
    if (!confirm("Close this project and open a different one?")) return;
    try {
      await fetch("/api/close", { method: "POST" });
    } catch {}
    location.reload();
  });
  $("#scene-mode").addEventListener("change", () => {
    syncSceneGroupingControls();
    applySceneGrouping();
  });
  $("#scene-gap").addEventListener("change", () => applySceneGrouping());
  $("#landing-open").addEventListener("click", submitOpen);
  $("#landing-photo-dir").addEventListener("keydown", (e) => {
    if (e.key === "Enter") submitOpen();
  });
  $("#landing-browse").addEventListener("click", browseFolder);
}

// ---------- People modal ----------
function openPeopleModal() {
  if (!state.people.length) {
    alert("No people clusters yet. Click ↻ cluster first.");
    return;
  }
  const list = $("#people-list");
  list.innerHTML = "";

  const activeSection = document.createElement("div");
  activeSection.className = "people-section";
  activeSection.innerHTML = `<h3 class="people-section-head">Active <span class="count-pill" id="active-count"></span><span class="hint">drag to reorder · top = highest priority</span></h3>`;
  const activeWrap = document.createElement("div");
  activeWrap.className = "people-active";
  activeSection.appendChild(activeWrap);
  list.appendChild(activeSection);

  const excludedSection = document.createElement("div");
  excludedSection.className = "people-section";
  excludedSection.innerHTML = `<h3 class="people-section-head">Excluded <span class="count-pill" id="excluded-count"></span><span class="hint">not used for sorting or filtering</span></h3>`;
  const excludedWrap = document.createElement("div");
  excludedWrap.className = "people-excluded";
  excludedSection.appendChild(excludedWrap);
  list.appendChild(excludedSection);

  const active = state.people.filter((p) => !p.excluded).sort((a, b) => a.priority - b.priority);
  const excluded = state.people.filter((p) => p.excluded).sort((a, b) => a.priority - b.priority);
  for (const p of active) activeWrap.appendChild(buildPersonCard(p, false));
  for (const p of excluded) excludedWrap.appendChild(buildPersonCard(p, true));

  bindPeopleDrag(activeWrap);
  refreshPeopleSections();
  $("#people-modal").classList.remove("hidden");
}

function buildPersonCard(person, isExcluded) {
  const card = document.createElement("div");
  card.className = "person-card" + (isExcluded ? " excluded" : "");
  card.dataset.id = person.id;
  card.dataset.excluded = isExcluded ? "1" : "0";
  card.draggable = !isExcluded;
  card.innerHTML = `
    <span class="drag-handle" title="Drag to reorder">⋮⋮</span>
    <span class="priority-badge"></span>
    <img src="/face/${enc(person.ref.rel_path)}?idx=${person.ref.face_idx}" alt="" />
    <div class="fields">
      <input type="text" data-field="label" value="${escapeAttr(person.label)}" placeholder="Label" />
      <span class="count">${person.count} faces</span>
    </div>
    <button class="exclude-toggle" type="button">${isExcluded ? "Restore" : "Exclude"}</button>`;
  card.querySelector(".exclude-toggle").addEventListener("click", () => toggleExclude(card));
  return card;
}

function toggleExclude(card) {
  const activeWrap = $(".people-active");
  const excludedWrap = $(".people-excluded");
  const becomingExcluded = card.dataset.excluded === "0";
  card.dataset.excluded = becomingExcluded ? "1" : "0";
  card.classList.toggle("excluded", becomingExcluded);
  card.draggable = !becomingExcluded;
  card.querySelector(".exclude-toggle").textContent = becomingExcluded ? "Restore" : "Exclude";
  if (becomingExcluded) excludedWrap.appendChild(card);
  else activeWrap.appendChild(card);
  refreshPeopleSections();
}

function refreshPeopleSections() {
  const activeWrap = $(".people-active");
  const excludedWrap = $(".people-excluded");
  activeWrap.querySelectorAll(".person-card").forEach((card, i) => {
    card.querySelector(".priority-badge").textContent = `#${i + 1}`;
  });
  excludedWrap.querySelectorAll(".person-card").forEach((card) => {
    card.querySelector(".priority-badge").textContent = "—";
  });
  $("#active-count").textContent = activeWrap.children.length;
  $("#excluded-count").textContent = excludedWrap.children.length;
  // Empty-state placeholders so drop targets remain usable.
  setEmptyPlaceholder(activeWrap, "All clusters are excluded.");
  setEmptyPlaceholder(excludedWrap, "Nothing excluded.");
}

function setEmptyPlaceholder(wrap, text) {
  const hasCards = wrap.querySelector(".person-card");
  let ph = wrap.querySelector(".empty-placeholder");
  if (hasCards) { if (ph) ph.remove(); return; }
  if (!ph) {
    ph = document.createElement("div");
    ph.className = "empty-placeholder";
    wrap.appendChild(ph);
  }
  ph.textContent = text;
}

function bindPeopleDrag(container) {
  let dragSrc = null;
  container.addEventListener("dragstart", (e) => {
    const card = e.target.closest(".person-card");
    if (!card || card.dataset.excluded === "1") return;
    dragSrc = card;
    card.classList.add("dragging");
    e.dataTransfer.effectAllowed = "move";
    // Required by Firefox to actually start the drag.
    e.dataTransfer.setData("text/plain", card.dataset.id);
  });
  container.addEventListener("dragend", () => {
    if (dragSrc) dragSrc.classList.remove("dragging");
    dragSrc = null;
    refreshPeopleSections();
  });
  container.addEventListener("dragover", (e) => {
    if (!dragSrc) return;
    e.preventDefault();
    e.dataTransfer.dropEffect = "move";
    const target = e.target.closest(".person-card");
    if (!target || target === dragSrc) {
      // Allow dropping into empty container.
      if (e.target === container && !container.querySelector(".person-card")) {
        container.appendChild(dragSrc);
      }
      return;
    }
    const rect = target.getBoundingClientRect();
    const after = (e.clientY - rect.top) > rect.height / 2;
    if (after) target.after(dragSrc);
    else target.before(dragSrc);
    refreshPeopleSections();
  });
  container.addEventListener("drop", (e) => { e.preventDefault(); });
}

function closePeopleModal() {
  $("#people-modal").classList.add("hidden");
}

function escapeAttr(s) {
  return String(s).replace(/&/g, "&amp;").replace(/"/g, "&quot;").replace(/</g, "&lt;");
}

async function savePeople() {
  const payload = { people: [] };
  const activeCards = $$(".people-active .person-card");
  const excludedCards = $$(".people-excluded .person-card");
  activeCards.forEach((card, i) => {
    const id = card.dataset.id;
    const label = card.querySelector('input[data-field="label"]').value.trim() || "(unnamed)";
    payload.people.push({ id, label, priority: i + 1, excluded: false });
  });
  excludedCards.forEach((card, i) => {
    const id = card.dataset.id;
    const label = card.querySelector('input[data-field="label"]').value.trim() || "(unnamed)";
    payload.people.push({ id, label, priority: 1000 + i, excluded: true });
  });
  const res = await fetch("/api/people", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    alert("Save failed: " + (err.detail || res.status));
    return;
  }
  const result = await res.json();
  state.people = result.people;
  state.peopleById = new Map(state.people.map((p) => [p.id, p]));
  prunePersonFilter();
  closePeopleModal();
  renderPeopleChips();
  recomputeFilter();
  renderMain();
}

// ---------- Cluster trigger ----------
async function startCluster() {
  if (!confirm("Run face clustering? This takes ~1-2 minutes for 1000+ faces.")) return;
  const res = await fetch("/api/cluster", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({}),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    alert("Cluster failed: " + (err.detail || res.status));
    return;
  }
  $("#score-title").textContent = "Clustering faces…";
  $("#score-progress").classList.remove("hidden");
  $("#score-bar-fill").style.width = "0%";
  $("#score-progress-text").textContent = "starting…";
  $("#score-current").textContent = "";
  pollClusterStatus();
}

function pollClusterStatus() {
  if (scorePollTimer) clearInterval(scorePollTimer);
  let finished = false;
  const tick = async () => {
    if (finished) return;
    const res = await fetch("/api/cluster/status", { cache: "no-store" });
    if (!res.ok) { console.warn("cluster status fetch failed", res.status); return; }
    const s = await res.json();
    console.log("cluster status:", s);
    const pct = s.total ? Math.min(100, 100 * s.idx / s.total) : 0;
    $("#score-bar-fill").style.width = pct.toFixed(1) + "%";
    $("#score-progress-text").textContent = (s.phase || "running") +
      (s.total ? ` · ${s.idx}/${s.total}` : "");
    $("#score-current").textContent = "";
    if (!s.running) {
      finished = true;
      clearInterval(scorePollTimer);
      scorePollTimer = null;
      if (s.error) {
        $("#score-progress").classList.add("hidden");
        $("#score-title").textContent = "Scoring photos…";
        alert("Clustering failed: " + s.error);
        return;
      }
      const prevScene = state.selectedScene;
      await loadDb();
      $("#score-title").textContent = `✓ Done · ${state.people.length} clusters`;
      $("#score-bar-fill").style.width = "100%";
      $("#score-progress-text").textContent =
        `Total faces grouped: ${state.people.reduce((s, p) => s + p.count, 0)}`;
      $("#score-current").textContent = "Click anywhere to dismiss · ⚙ in sidebar to edit labels";
      const dismiss = () => {
        $("#score-progress").classList.add("hidden");
        $("#score-progress").removeEventListener("click", dismiss);
        $("#score-title").textContent = "Scoring photos…";
        renderSidebar();
        renderPeopleChips();
        if (prevScene && state.byScene.has(prevScene)) selectScene(prevScene);
        else if (state.sceneOrder.length) selectScene(state.sceneOrder[0]);
        else renderMain();
      };
      $("#score-progress").addEventListener("click", dismiss);
      setTimeout(dismiss, 4000);
    }
  };
  tick();
  scorePollTimer = setInterval(tick, 200);
}

// ---------- Scoring trigger ----------
let scorePollTimer = null;

async function startRescore() {
  const total = state.photos.length;
  const ok = confirm(
    `Re-score all ${total} photos? Existing decisions are preserved.\n` +
    `(Decisions disabled while scoring runs.)`,
  );
  if (!ok) return;
  const res = await fetch("/api/score", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ with_faces: false }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    alert("Rescore failed: " + (err.detail || res.status));
    return;
  }
  $("#score-progress").classList.remove("hidden");
  $("#score-bar-fill").style.width = "0%";
  $("#score-progress-text").textContent = "starting…";
  $("#score-current").textContent = "";
  pollScoreStatus();
}

function pollScoreStatus() {
  if (scorePollTimer) clearInterval(scorePollTimer);
  let finished = false;
  const tick = async () => {
    if (finished) return;
    const res = await fetch("/api/score/status", { cache: "no-store" });
    if (!res.ok) { console.warn("score status fetch failed", res.status); return; }
    const s = await res.json();
    const pct = s.total ? Math.min(100, 100 * s.idx / s.total) : 0;
    $("#score-bar-fill").style.width = pct.toFixed(1) + "%";
    $("#score-progress-text").textContent = s.total
      ? `${s.idx}/${s.total} (${pct.toFixed(1)}%)`
      : "starting…";
    $("#score-current").textContent = s.current || (s.running ? "" : "finalizing…");
    if (!s.running) {
      finished = true;
      clearInterval(scorePollTimer);
      scorePollTimer = null;
      if (s.error) {
        $("#score-progress").classList.add("hidden");
        alert("Scoring failed: " + s.error);
        return;
      }
      const prevScene = state.selectedScene;
      await loadDb();
      const totalFaces = state.photos.reduce((s, p) => s + (p.faces?.length || 0), 0);
      $("#score-title").textContent = `✓ Scored ${state.photos.length} photos`;
      $("#score-bar-fill").style.width = "100%";
      $("#score-progress-text").textContent = `${totalFaces} faces detected`;
      $("#score-current").textContent = "Click anywhere to dismiss · ↻ cluster next";
      const dismiss = () => {
        $("#score-progress").classList.add("hidden");
        $("#score-progress").removeEventListener("click", dismiss);
        $("#score-title").textContent = "Scoring photos…";
        renderSidebar();
        renderPeopleChips();
        if (prevScene && state.byScene.has(prevScene)) selectScene(prevScene);
        else if (state.sceneOrder.length) selectScene(state.sceneOrder[0]);
        else renderMain();
      };
      $("#score-progress").addEventListener("click", dismiss);
      setTimeout(dismiss, 4000);
    }
  };
  tick();
  scorePollTimer = setInterval(tick, 250);
}

// ---------- landing / project switching ----------
async function fetchState() {
  const res = await fetch("/api/state", { cache: "no-store" });
  if (!res.ok) throw new Error("state fetch failed: " + res.status);
  return res.json();
}

function showLanding() {
  document.body.classList.add("landing-mode");
  $("#landing").classList.remove("hidden");
  renderRecents();
  $("#landing-photo-dir").focus();
}

function hideLanding() {
  document.body.classList.remove("landing-mode");
  $("#landing").classList.add("hidden");
}

async function renderRecents() {
  const wrap = $("#landing-recents");
  wrap.innerHTML = "";
  let recents = [];
  try {
    const res = await fetch("/api/recents", { cache: "no-store" });
    if (res.ok) recents = (await res.json()).recents || [];
  } catch {}
  if (!recents.length) {
    wrap.innerHTML = `<div class="recents-empty">No recent projects yet.</div>`;
    return;
  }
  for (const r of recents) {
    const item = document.createElement("div");
    item.className = "recent-item";
    const opened = r.opened_at ? new Date(r.opened_at).toLocaleString() : "—";
    item.innerHTML = `
      <button class="recent-open" type="button">
        <div class="recent-name">${escapeHtml(basename(r.photo_dir))}</div>
        <div class="recent-path">${escapeHtml(r.photo_dir)}${r.jpeg_subdir ? ` <span class="dim">/ ${escapeHtml(r.jpeg_subdir)}</span>` : ""}</div>
        <div class="recent-meta">${opened}</div>
      </button>
      <button class="recent-forget" type="button" title="Remove from recents">×</button>`;
    item.querySelector(".recent-open").addEventListener("click", () => {
      $("#landing-photo-dir").value = r.photo_dir;
      $("#landing-jpeg-subdir").value = r.jpeg_subdir || "";
      $("#landing-db-path").value = r.db_path || "";
      submitOpen();
    });
    item.querySelector(".recent-forget").addEventListener("click", async () => {
      await fetch("/api/recents/forget", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ db_path: r.db_path }),
      });
      renderRecents();
    });
    wrap.appendChild(item);
  }
}

function basename(p) {
  if (!p) return "";
  const s = String(p).replace(/\/+$/, "");
  const i = s.lastIndexOf("/");
  return i >= 0 ? s.slice(i + 1) : s;
}
function escapeHtml(s) {
  return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

async function browseFolder() {
  const btn = $("#landing-browse");
  const status = $("#landing-status");
  btn.disabled = true;
  btn.textContent = "Choose…";
  status.textContent = "Opening Finder dialog… (check the Dock if it didn't pop forward)";
  const initial = $("#landing-photo-dir").value.trim() || null;
  try {
    const res = await fetch("/api/browse-folder", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ initial }),
    });
    if (!res.ok) {
      status.textContent = `Browse endpoint failed (${res.status}). Restart the server: pcls serve`;
      return;
    }
    const result = await res.json();
    if (result.path) {
      $("#landing-photo-dir").value = result.path;
      status.textContent = "";
    } else if (result.cancelled) {
      status.textContent = "";
    } else if (result.error) {
      status.textContent = "Browse failed: " + result.error;
    } else {
      status.textContent = "";
    }
  } catch (err) {
    status.textContent = "Browse network error: " + err.message;
  } finally {
    btn.disabled = false;
    btn.textContent = "Browse…";
  }
}

async function submitOpen() {
  const photoDir = $("#landing-photo-dir").value.trim();
  if (!photoDir) {
    $("#landing-status").textContent = "Please enter a folder path.";
    return;
  }
  const jpegSubdir = $("#landing-jpeg-subdir").value.trim();
  const dbPath = $("#landing-db-path").value.trim();
  $("#landing-status").textContent = "Starting…";
  $("#landing-open").disabled = true;
  const res = await fetch("/api/open", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      photo_dir: photoDir,
      jpeg_subdir: jpegSubdir,
      db_path: dbPath || null,
    }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    $("#landing-status").textContent = "Failed: " + (err.detail || res.status);
    $("#landing-open").disabled = false;
    return;
  }
  // Show progress overlay; landing stays hidden until project becomes ready.
  hideLanding();
  $("#score-title").textContent = "Opening project…";
  $("#score-progress").classList.remove("hidden");
  $("#score-bar-fill").style.width = "0%";
  $("#score-progress-text").textContent = "starting…";
  $("#score-current").textContent = "";
  pollOpenStatus();
}

function pollOpenStatus() {
  if (scorePollTimer) clearInterval(scorePollTimer);
  let finished = false;
  const tick = async () => {
    if (finished) return;
    let s;
    try {
      const stateRes = await fetch("/api/state", { cache: "no-store" });
      if (!stateRes.ok) return;
      s = (await stateRes.json()).opening || {};
    } catch { return; }
    const pct = s.total ? Math.min(100, 100 * s.idx / s.total) : 0;
    $("#score-title").textContent = (s.phase === "scoring") ? "Scoring photos…"
      : (s.phase === "clustering") ? "Clustering faces…"
      : (s.phase === "scanning") ? "Scanning…"
      : (s.phase === "loading") ? "Loading project…"
      : "Opening project…";
    $("#score-bar-fill").style.width = pct.toFixed(1) + "%";
    $("#score-progress-text").textContent = s.message || (s.total ? `${s.idx}/${s.total}` : "starting…");
    $("#score-current").textContent = s.current || "";
    if (!s.running) {
      finished = true;
      clearInterval(scorePollTimer);
      scorePollTimer = null;
      if (s.error) {
        $("#score-progress").classList.add("hidden");
        alert("Open failed: " + s.error);
        showLanding();
        $("#landing-open").disabled = false;
        $("#landing-status").textContent = s.error;
        return;
      }
      $("#score-progress").classList.add("hidden");
      $("#score-title").textContent = "Scoring photos…";
      await bootMain();
    }
  };
  tick();
  scorePollTimer = setInterval(tick, 250);
}

async function bootMain() {
  await loadDb();
  renderSidebar();
  renderPeopleChips();
  syncSceneGroupingControls();
  if (state.sceneOrder.length) selectScene(state.sceneOrder[0]);
  else renderMain();
}

function syncSceneGroupingControls() {
  const sg = state.sceneGrouping || { mode: "folder", gap_minutes: 30 };
  $("#scene-mode").value = sg.mode;
  $("#scene-gap").value = sg.gap_minutes;
  $("#scene-gap").style.display = sg.mode === "time_gap" ? "" : "none";
  $("#scene-grouping .unit").style.display = sg.mode === "time_gap" ? "" : "none";
}

async function applySceneGrouping() {
  const mode = $("#scene-mode").value;
  const gap = parseInt($("#scene-gap").value, 10) || 30;
  $("#score-title").textContent = "Regrouping scenes…";
  $("#score-progress").classList.remove("hidden");
  $("#score-bar-fill").style.width = "60%";
  $("#score-progress-text").textContent = mode === "folder" ? "by folder" : `${gap} min gap`;
  $("#score-current").textContent = "";
  const res = await fetch("/api/scene-grouping", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ mode, gap_minutes: gap }),
  });
  $("#score-progress").classList.add("hidden");
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    alert("Scene grouping failed: " + (err.detail || res.status));
    return;
  }
  await bootMain();
}

// ---------- boot ----------
(async () => {
  bindUi();
  bindKeys();
  let s;
  try { s = await fetchState(); } catch { showLanding(); return; }
  if (s.opening && s.opening.running) {
    hideLanding();
    $("#score-title").textContent = "Opening project…";
    $("#score-progress").classList.remove("hidden");
    pollOpenStatus();
    return;
  }
  if (!s.ready) { showLanding(); return; }
  await bootMain();
  // If a long task is already running on the server, attach to it.
  const sr = await fetch("/api/score/status").catch(() => null);
  if (sr && sr.ok) {
    const sc = await sr.json();
    if (sc.running) {
      $("#score-title").textContent = "Scoring photos…";
      $("#score-progress").classList.remove("hidden");
      pollScoreStatus();
      return;
    }
  }
  const cr = await fetch("/api/cluster/status").catch(() => null);
  if (cr && cr.ok) {
    const c = await cr.json();
    if (c.running) {
      $("#score-title").textContent = "Clustering faces…";
      $("#score-progress").classList.remove("hidden");
      pollClusterStatus();
    }
  }
})();
