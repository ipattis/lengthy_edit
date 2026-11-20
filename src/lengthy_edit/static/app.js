// lengthy-edit UI — vanilla JS, no build step.
"use strict";

const $ = (sel, el = document) => el.querySelector(sel);
const $$ = (sel, el = document) => [...el.querySelectorAll(sel)];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmt = (s) => { s = Number(s) || 0; const m = Math.floor(s / 60); return `${m}:${(s - m * 60).toFixed(1).padStart(4, "0")}`; };

const state = { project: null, tab: "brief", tasks: [], polling: null, health: {} };

// --------------------------------------------------------------------------- api

async function api(method, path, body, isForm = false) {
  const opts = { method, headers: {} };
  if (body !== undefined) {
    if (isForm) opts.body = body;
    else { opts.headers["Content-Type"] = "application/json"; opts.body = JSON.stringify(body); }
  }
  const r = await fetch(path, opts);
  const text = await r.text();
  let data; try { data = text ? JSON.parse(text) : null; } catch { data = text; }
  if (!r.ok) throw new Error((data && data.detail) ? (typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail)) : `${r.status}`);
  return data;
}
const P = () => `/api/projects/${state.project.slug}`;
const fileUrl = (rel) => rel ? `/files/${state.project.slug}/${rel}` : "";

function toast(msg, err = false) {
  const t = $("#toast");
  t.textContent = msg; t.className = "show" + (err ? " err" : "");
  clearTimeout(t._h); t._h = setTimeout(() => (t.className = ""), err ? 6000 : 2500);
}
async function guard(fn) { try { return await fn(); } catch (e) { toast(e.message, true); } }
async function copy(text) { await navigator.clipboard.writeText(text); toast("Copied"); }

// --------------------------------------------------------------------------- projects

async function loadHealth() {
  const h = state.health = await api("GET", "/api/health");
  const row = (ok, label) => `<div class="${ok ? "ok" : "no"}">${ok ? "●" : "○"} ${label}</div>`;
  $("#health").innerHTML =
    row(h.anthropic_key, `Claude (${esc(h.model)})${h.anthropic_key ? "" : " — key from env/profile"}`) +
    row(h.higgsfield, "Higgsfield / Seedance") + row(h.ffmpeg, "ffmpeg") +
    `<div title="${esc(h.workspace)}">📁 ${esc(h.workspace.split("/").slice(-2).join("/"))}</div><div>v${esc(h.version)}</div>`;
}

async function loadProjects() {
  const list = await api("GET", "/api/projects");
  $("#projects").innerHTML = list.map((p) =>
    `<li data-slug="${esc(p.slug)}" class="${state.project?.slug === p.slug ? "active" : ""}">${esc(p.title || p.slug)}
       <small>${esc(p.slug)} · ${esc(p.status.assets)} shots ready</small></li>`).join("");
}

async function openProject(slug, tab) {
  state.project = await api("GET", `/api/projects/${slug}`);
  if (tab) state.tab = tab;
  location.hash = `${slug}/${state.tab}`;
  $("#topbar").hidden = false;
  await loadProjects();
  render();
  pollTasks();
}

async function refresh() {
  if (!state.project) return;
  state.project = await api("GET", P());
  render();
}

// --------------------------------------------------------------------------- tasks

async function pollTasks() {
  clearTimeout(state.polling);
  if (!state.project) return;
  const slug = state.project.slug;
  try {
    const prev = state.tasks;
    const tasks = await api("GET", `/api/projects/${slug}/tasks`);
    if (state.project?.slug !== slug) return;
    state.tasks = tasks;
    renderTasks();
    const finished = tasks.filter((t) => t.status !== "running" && prev.find((p) => p.id === t.id && p.status === "running"));
    finished.filter((t) => t.status === "error").forEach((t) => toast(`${t.kind}: ${t.error}`, true));
    const running = tasks.some((t) => t.status === "running");
    // Refresh while running too, so Seedance status updates show up.
    if (finished.length || (running && tasks.some((t) => t.kind.startsWith("motion")))) await refresh();
    state.polling = setTimeout(pollTasks, running ? 2000 : 8000);
  } catch { state.polling = setTimeout(pollTasks, 8000); }
}

function renderTasks() {
  const recent = state.tasks.sort((a, b) => b.created - a.created).slice(0, 6);
  $("#tasks").innerHTML = recent.map((t) =>
    `<span class="task ${t.status}" title="${esc(t.error || "")}">${t.status === "running" ? "⏳" : t.status === "done" ? "✓" : "✗"}
     ${esc(t.kind)} — ${esc(t.status === "error" ? t.error : t.message)}
     ${t.status === "running" ? `<span class="bar"><i style="width:${t.progress * 100}%"></i></span>` : ""}</span>`).join("");
}
const busy = (kind) => state.tasks.some((t) => t.kind === kind && t.status === "running");

async function startTask(path, body) {
  return guard(async () => { await api("POST", path, body); toast("Started"); pollTasks(); });
}

// --------------------------------------------------------------------------- render shell

function render() {
  const p = state.project;
  $("#project-title").textContent = p.brief.title || p.slug;
  const done = { brief: p.status.brief, research: p.status.research, lyrics: p.status.lyrics && p.status.song,
    storyboard: p.status.storyboard, sound: p.status.sound > 0, render: p.status.render > 0 };
  $$("#tabs button").forEach((b) => {
    b.classList.toggle("active", b.dataset.tab === state.tab);
    if (!b.querySelector(".dot")) b.insertAdjacentHTML("beforeend", `<span class="dot"></span>`);
    b.querySelector(".dot").classList.toggle("done", !!done[b.dataset.tab]);
  });
  const focused = document.activeElement;
  // Don't wipe a field the user is typing in during background refreshes.
  const typing = focused && $("#view").contains(focused) &&
    (focused.tagName === "TEXTAREA" || (focused.tagName === "INPUT" && !["file", "checkbox"].includes(focused.type)));
  if (typing && !state.forceRender) return;
  state.forceRender = false;
  const v = $("#view");
  ({ brief: viewBrief, research: viewResearch, lyrics: viewLyrics, storyboard: viewStoryboard, sound: viewSound, render: viewRender })[state.tab](v, p);
}

// --------------------------------------------------------------------------- 1. brief

function viewBrief(v, p) {
  const b = p.brief;
  const f = (k, label, ta, hint = "") => `<label>${label}</label>${ta
    ? `<textarea name="${k}" rows="${ta}">${esc(b[k])}</textarea>` : `<input name="${k}" value="${esc(b[k])}" style="width:100%">`}${hint ? `<div class="hint">${hint}</div>` : ""}`;
  v.innerHTML = `<form id="brief" class="card" style="max-width:900px">
    ${f("title", "Title")}
    ${f("logline", "Logline", 2, "One sentence: what is this film about?")}
    ${f("concept", "Concept", 6, "The idea, the argument, the story. This drives everything downstream.")}
    <div class="grid2">
      <div>${f("tone", "Tone", 2, "e.g. wry, unsettling, tender, satirical")}</div>
      <div>${f("music_style", "Music style", 2, "e.g. dark synth-pop, 100 bpm, female vocal")}</div>
    </div>
    ${f("visual_style", "Visual style", 3, "e.g. 1970s corporate film stock, muted teal/orange, grain, symmetrical framing")}
    <div class="grid2">
      <div><label>Target duration (s)</label><input type="number" name="target_duration_s" value="${b.target_duration_s}"></div>
      <div><label>Aspect ratio</label><select name="aspect_ratio">${["16:9", "21:9", "4:3", "1:1", "9:16"].map((a) => `<option ${a === b.aspect_ratio ? "selected" : ""}>${a}</option>`).join("")}</select></div>
    </div>
    ${f("notes", "Notes for Claude", 3)}
    <div class="toolbar" style="margin-top:14px"><button class="primary">Save brief</button>
      <span class="hint">Next: Research — Claude builds a dossier the later stages draw on.</span></div>
  </form>`;
  $("#brief").onsubmit = (e) => {
    e.preventDefault();
    const fd = Object.fromEntries(new FormData(e.target));
    fd.target_duration_s = parseInt(fd.target_duration_s) || 210;
    guard(async () => { state.project = await api("PUT", `${P()}/brief`, fd); toast("Saved"); render(); loadProjects(); });
  };
}

// --------------------------------------------------------------------------- 2. research

function viewResearch(v, p) {
  v.innerHTML = `<div class="toolbar">
      <button class="primary" id="gen" ${busy("research") ? "disabled" : ""}>${p.research ? "Regenerate" : "Generate"} research with Claude</button>
      <label style="margin:0;display:flex;gap:6px;align-items:center"><input type="checkbox" id="ws" checked> use web search</label>
      <span class="spacer"></span><button id="save">Save edits</button></div>
    <textarea id="research" rows="34" class="md" placeholder="Research dossier (markdown). Generate with Claude, or paste your own.">${esc(p.research)}</textarea>`;
  $("#gen").onclick = () => (!p.research || confirm("Replace the current research?")) && startTask(`${P()}/generate/research`, { web_search: $("#ws").checked });
  $("#save").onclick = () => guard(async () => { state.project = await api("PUT", `${P()}/research`, { research: $("#research").value }); toast("Saved"); });
}

// --------------------------------------------------------------------------- 3. lyrics & song

function viewLyrics(v, p) {
  const L = p.lyrics;
  const sections = L.sections.map((s, i) => `<div class="section-row card" data-i="${i}">
      <input class="name" value="${esc(s.name)}">
      <textarea class="lines" rows="${Math.max(2, s.lines.length)}">${esc(s.lines.join("\n"))}</textarea>
      <div><div class="times"><input type="number" step="0.1" class="start" value="${s.start_s ?? ""}" placeholder="start s">
        <input type="number" step="0.1" class="end" value="${s.end_s ?? ""}" placeholder="end s"></div>
        <button class="small mark" type="button" title="Set start to current song position">⏱ start = playhead</button></div>
      <button class="small danger del" type="button">✕</button></div>`).join("");
  v.innerHTML = `<div class="toolbar">
      <button class="primary" id="gen" ${busy("lyrics") ? "disabled" : ""}>${L.sections.length ? "Regenerate" : "Generate"} lyrics with Claude</button>
      <span class="spacer"></span>
      <a href="${P()}/export/suno.txt" target="_blank">suno.txt</a>
      <button id="copy-suno">Copy lyrics for Suno</button><button id="save" class="primary">Save</button></div>
    <div class="grid2">
      <div class="card"><label>Suno title</label><input id="suno_title" value="${esc(L.suno_title)}" style="width:100%">
        <label>Suno "style of music"</label><div class="prompt-box"><textarea id="suno_style" rows="3">${esc(L.suno_style)}</textarea>
        <button class="small copy" id="copy-style">copy</button></div>
        <div class="hint">Paste the style + lyrics into Suno (custom mode), generate, then upload your favourite take here.</div></div>
      <div class="card"><label>Song (from Suno)</label>
        ${p.song.file ? `<audio id="song" controls src="${fileUrl(p.song.file)}" style="width:100%"></audio>
          <div class="hint">${esc(p.song.file)} · ${fmt(p.song.duration_s)} (${p.song.duration_s}s)</div>` : `<div class="hint">No song uploaded yet.</div>`}
        <div class="toolbar" style="margin-top:10px"><span class="filebtn"><button>Upload song…</button><input type="file" id="song-file" accept="audio/*"></span>
          <button id="autotime" ${L.sections.length ? "" : "disabled"}>Auto-time sections</button></div>
        <div class="hint">Section timings tell the storyboard where verses and choruses land. Auto-time gives an estimate; play the song and use “start = playhead” to correct.</div></div>
    </div>
    <h3>Sections</h3>${sections || `<p class="hint">No lyrics yet.</p>`}
    <button id="add-sec">+ Add section</button>`;

  const collect = () => ({
    suno_title: $("#suno_title").value, suno_style: $("#suno_style").value,
    sections: $$(".section-row").map((r) => ({
      name: $(".name", r).value, lines: $(".lines", r).value.split("\n").filter((l) => l.trim() !== ""),
      start_s: $(".start", r).value === "" ? null : parseFloat($(".start", r).value),
      end_s: $(".end", r).value === "" ? null : parseFloat($(".end", r).value),
    })),
  });
  const save = async (quiet) => { state.project = await api("PUT", `${P()}/lyrics`, collect()); if (!quiet) toast("Saved"); };
  $("#gen").onclick = () => (!L.sections.length || confirm("Replace the current lyrics?")) && startTask(`${P()}/generate/lyrics`);
  $("#save").onclick = () => guard(async () => { await save(); render(); });
  $("#copy-suno").onclick = () => copy(collect().sections.map((s) => `[${s.name}]\n${s.lines.join("\n")}`).join("\n\n"));
  $("#copy-style").onclick = () => copy($("#suno_style").value);
  $("#add-sec").onclick = () => guard(async () => { const c = collect(); c.sections.push({ name: "Section", lines: [] }); state.project = await api("PUT", `${P()}/lyrics`, c); render(); });
  $$(".section-row .del").forEach((b) => (b.onclick = () => guard(async () => {
    const c = collect(); c.sections.splice(+b.closest(".section-row").dataset.i, 1);
    state.project = await api("PUT", `${P()}/lyrics`, c); render();
  })));
  $$(".section-row .mark").forEach((b) => (b.onclick = () => {
    const a = $("#song"); if (!a) return toast("Upload the song first", true);
    const rows = $$(".section-row"), i = rows.indexOf(b.closest(".section-row"));
    $(".start", rows[i]).value = a.currentTime.toFixed(2);
    if (i > 0) $(".end", rows[i - 1]).value = a.currentTime.toFixed(2);
  }));
  $("#autotime").onclick = () => guard(async () => { await save(true); state.project = await api("POST", `${P()}/lyrics/auto-time`); render(); toast("Sections timed — adjust by ear"); });
  $("#song-file").onchange = (e) => upload("song", null, e.target.files[0]);
}

async function upload(target, id, file) {
  if (!file) return;
  const fd = new FormData(); fd.append("file", file);
  toast(`Uploading ${file.name}…`);
  await guard(async () => {
    state.project = await api("POST", `${P()}/upload?target=${target}${id ? `&id=${encodeURIComponent(id)}` : ""}`, fd, true);
    toast("Uploaded"); render();
  });
}

// --------------------------------------------------------------------------- 4. storyboard

const KINDS = ["still", "loop", "motion", "title"];

function timelineHtml(p) {
  const total = Math.max(p.song.duration_s || 0, p.timeline_duration) || 1;
  const segs = p.shots.map((s) => {
    const has = s.video || s.image || s.kind === "title";
    const bg = s.image ? `background-image:url('${fileUrl(s.image)}')` : "";
    return `<div class="seg ${has ? "" : "missing"}" data-id="${s.id}" title="${esc(`${s.id} ${fmt(s.start_s)} ${s.kind}: ${s.description}`)}"
      style="left:${(s.start_s / total) * 100}%;width:${(s.duration_s / total) * 100}%;--k:var(--${s.kind});${bg}"></div>`;
  }).join("");
  const secs = p.lyrics.sections.filter((s) => s.start_s != null).map((s) =>
    `<div class="sec" style="left:${(s.start_s / total) * 100}%">${esc(s.name)}</div>`).join("");
  return `<div id="timeline">${segs}${secs}</div>
    <div class="legend">${KINDS.map((k) => `<span><i style="background:var(--${k})"></i>${k}</span>`).join("")}
      <span>· faded = asset missing</span><span>· timeline ${fmt(p.timeline_duration)} / song ${fmt(p.song.duration_s)}</span></div>`;
}

function shotHtml(s) {
  const media = s.video ? `<video src="${fileUrl(s.video)}" muted loop playsinline onmouseover="this.play()" onmouseout="this.pause()"></video>`
    : s.image ? `<img src="${fileUrl(s.image)}">` : s.kind === "title" ? `<b style="padding:10px;text-align:center">${esc(s.on_screen_text || "title card")}</b>` : "no asset";
  const job = s.job_status ? `<span class="status-pill ${s.job_error ? "err" : ""}" title="${esc(s.job_error || "")}">seedance: ${esc(s.job_error ? "error" : s.job_status)}</span>` : "";
  const ta = (k, label, rows, full = false, copyBtn = false) => `<div class="${full ? "full" : ""}"><label>${label}</label>
    <div class="prompt-box"><textarea data-k="${k}" rows="${rows}">${esc(s[k])}</textarea>${copyBtn ? `<button class="small copy" data-copy="${k}">copy</button>` : ""}</div></div>`;
  return `<div class="card shot" id="shot-${s.id}" data-id="${s.id}">
    <div class="media">
      <div class="thumb">${media}</div>
      <div class="row-actions">
        <span class="filebtn"><button class="small">Image…</button><input type="file" data-up="shot_image" accept="image/*"></span>
        <span class="filebtn"><button class="small">Video/loop…</button><input type="file" data-up="shot_video" accept="video/*,.gif"></span>
        ${s.image ? `<button class="small danger" data-clear="image">✕ img</button>` : ""}
        ${s.video ? `<button class="small danger" data-clear="video">✕ vid</button>` : ""}
      </div>
      ${s.kind === "motion" || s.kind === "loop" ? `<button class="small" data-act="motion" ${state.health.higgsfield && !busy(`motion:${s.id}`) ? "" : "disabled"}
        title="${state.health.higgsfield ? "Animate the still with Seedance (image-to-video); text-to-video if no still" : "Set HF_API_KEY_ID / HF_API_KEY_SECRET"}">🎬 Seedance ${s.image ? "from still" : "(text)"}</button>` : ""}
      ${job}
    </div>
    <div>
      <div class="head">
        <span class="id">${s.id}</span><span class="time">${fmt(s.start_s)} → ${fmt(s.start_s + s.duration_s)}</span>
        <input type="number" step="0.1" min="0.5" data-k="duration_s" value="${s.duration_s}" title="duration (s)">s
        <select data-k="kind">${KINDS.map((k) => `<option ${k === s.kind ? "selected" : ""}>${k}</option>`).join("")}</select>
        <span class="kind ${s.kind}">${s.kind}</span>
        <input data-k="section" value="${esc(s.section)}" placeholder="section" style="width:110px">
        ${s.kind === "still" ? `<label style="margin:0;display:flex;gap:4px;align-items:center"><input type="checkbox" data-k="ken_burns" ${s.ken_burns ? "checked" : ""}> push-in</label>` : ""}
        <span class="spacer" style="flex:1"></span>
        <button class="small" data-act="refine">✎ Refine with Claude</button>
        <button class="small" data-act="add">+ after</button>
        <button class="small danger" data-act="delete">✕</button>
      </div>
      <div class="fields">
        ${ta("description", "Description", 2)}${ta("lyric", "Lyric", 2)}
        ${s.kind === "title" ? ta("on_screen_text", "On-screen text", 2, true) : ta("midjourney_prompt", "Midjourney prompt", 3, true, true)}
        ${s.kind === "motion" || s.kind === "loop" ? ta("motion_prompt", "Motion prompt (Seedance / loop)", 2, true, true) : ""}
        ${ta("camera", "Camera", 1, true)}
      </div>
    </div></div>`;
}

function viewStoryboard(v, p) {
  const motionMissing = p.shots.filter((s) => s.kind === "motion" && !s.video).length;
  v.innerHTML = `<div class="toolbar">
      <button class="primary" id="gen" ${busy("storyboard") ? "disabled" : ""}>${p.shots.length ? "Regenerate" : "Generate"} storyboard with Claude</button>
      <button id="fit" ${p.shots.length ? "" : "disabled"} title="Re-tile shots and stretch the last to the song end">Fit to song</button>
      <button id="all-motion" ${state.health.higgsfield && motionMissing ? "" : "disabled"}>🎬 Seedance all missing motion (${motionMissing})</button>
      <span class="spacer"></span>
      <a href="${P()}/export/prompts.csv">prompts.csv</a>
      <button id="copy-mj" ${p.shots.length ? "" : "disabled"}>Copy all Midjourney prompts</button>
      <button id="add-end">+ Shot</button></div>
    ${p.song.file ? "" : `<p class="hint">Tip: upload the Suno song and time its sections first so cuts land on the music.</p>`}
    ${p.shots.length ? timelineHtml(p) : ""}
    <div id="shots">${p.shots.map(shotHtml).join("") || `<p class="hint">No shots yet.</p>`}</div>`;

  $("#gen").onclick = () => (!p.shots.length || confirm("Replace the whole shot list? Uploaded files stay on disk but are unlinked.")) && startTask(`${P()}/generate/storyboard`);
  $("#fit").onclick = () => guard(async () => { state.project = await api("POST", `${P()}/shots/fit`); render(); });
  $("#all-motion").onclick = () => startTask(`${P()}/motion/all`);
  $("#copy-mj").onclick = () => copy(p.shots.filter((s) => s.midjourney_prompt).map((s) => s.midjourney_prompt).join("\n\n"));
  $("#add-end").onclick = () => guard(async () => { state.project = await api("POST", `${P()}/shots`, {}); render(); });
  $$("#timeline .seg").forEach((el) => (el.onclick = () => $(`#shot-${el.dataset.id}`).scrollIntoView({ behavior: "smooth", block: "center" })));

  $$(".shot").forEach((card) => {
    const id = card.dataset.id;
    const patch = (body) => guard(async () => { state.project = await api("PATCH", `${P()}/shots/${id}`, body); });
    $$("[data-k]", card).forEach((el) => (el.onchange = async () => {
      const k = el.dataset.k;
      const val = el.type === "checkbox" ? el.checked : el.type === "number" ? parseFloat(el.value) : el.value;
      await patch({ [k]: val });
      if (["duration_s", "kind", "ken_burns"].includes(k)) { state.forceRender = true; render(); }
    }));
    $$("[data-copy]", card).forEach((b) => (b.onclick = () => copy($(`[data-k=${b.dataset.copy}]`, card).value)));
    $$("[data-up]", card).forEach((inp) => (inp.onchange = (e) => upload(inp.dataset.up, id, e.target.files[0])));
    $$("[data-clear]", card).forEach((b) => (b.onclick = () => guard(async () => { state.project = await api("DELETE", `${P()}/shots/${id}/asset/${b.dataset.clear}`); render(); })));
    $$("[data-act]", card).forEach((b) => (b.onclick = () => {
      const act = b.dataset.act;
      if (act === "delete") return confirm(`Delete ${id}?`) && guard(async () => { state.project = await api("DELETE", `${P()}/shots/${id}`); render(); });
      if (act === "add") return guard(async () => { state.project = await api("POST", `${P()}/shots`, { after: id }); render(); });
      if (act === "motion") return startTask(`${P()}/shots/${id}/motion`);
      if (act === "refine") {
        const note = prompt(`Director's note for ${id} (e.g. "make it a wide establishing shot at dusk"):`);
        if (note) startTask(`${P()}/shots/${id}/refine`, { note });
      }
    }));
  });
}

// --------------------------------------------------------------------------- 5. sound

function viewSound(v, p) {
  v.innerHTML = `<div class="toolbar">
      <button class="primary" id="gen" ${busy("sound") ? "disabled" : ""}>${p.sound_cues.length ? "Regenerate" : "Generate"} sound layer with Claude</button>
      <span class="spacer"></span><button id="add">+ Cue</button><button id="save" class="primary">Save</button></div>
    <p class="hint">Cues are mixed under the song at render time. Claude proposes them; you source each sound (library, foley, or a generator) and upload it. Cues without files are skipped.</p>
    <table><thead><tr><th style="width:90px">at (s)</th><th style="width:90px">length (s)</th><th>description</th><th style="width:80px">gain dB</th><th style="width:280px">file</th><th></th></tr></thead>
    <tbody>${p.sound_cues.map((c) => `<tr data-id="${c.id}">
      <td><input type="number" step="0.1" class="at" value="${c.at_s}"></td>
      <td><input type="number" step="0.1" class="dur" value="${c.duration_s}"></td>
      <td><input type="text" class="desc" value="${esc(c.description)}"></td>
      <td><input type="number" step="1" class="gain" value="${c.gain_db}"></td>
      <td>${c.file ? `<audio controls src="${fileUrl(c.file)}" style="height:28px;width:200px"></audio>` : ""}
        <span class="filebtn"><button class="small">${c.file ? "Replace" : "Upload"}…</button><input type="file" accept="audio/*" class="up"></span></td>
      <td><button class="small danger del">✕</button></td></tr>`).join("")}</tbody></table>`;

  const collect = () => $$("tbody tr").map((r) => ({
    id: r.dataset.id, at_s: parseFloat($(".at", r).value) || 0, duration_s: parseFloat($(".dur", r).value) || 1,
    description: $(".desc", r).value, gain_db: parseFloat($(".gain", r).value) || 0,
    file: p.sound_cues.find((c) => c.id === r.dataset.id)?.file ?? null,
  }));
  const save = async (cues) => { state.project = await api("PUT", `${P()}/sound-cues`, cues); render(); };
  $("#gen").onclick = () => (!p.sound_cues.length || confirm("Replace all cues?")) && startTask(`${P()}/generate/sound`);
  $("#save").onclick = () => guard(async () => { await save(collect()); toast("Saved"); });
  $("#add").onclick = () => guard(async () => {
    const cues = collect(); const n = 1 + Math.max(0, ...cues.map((c) => parseInt(c.id.slice(1)) || 0));
    cues.push({ id: `c${String(n).padStart(3, "0")}`, at_s: 0, duration_s: 2, description: "", gain_db: -6, file: null });
    await save(cues);
  });
  $$("tbody .del").forEach((b) => (b.onclick = () => guard(() => save(collect().filter((c) => c.id !== b.closest("tr").dataset.id)))));
  $$("tbody .up").forEach((inp) => (inp.onchange = async (e) => { await guard(() => save(collect())); upload("cue", inp.closest("tr").dataset.id, e.target.files[0]); }));
}

// --------------------------------------------------------------------------- 6. render

function viewRender(v, p) {
  const r = p.render;
  const missing = p.shots.filter((s) => !(s.video || s.image || s.kind === "title")).length;
  const n = (k, label, step = 1) => `<div><label>${label}</label><input type="number" step="${step}" name="${k}" value="${r[k]}"></div>`;
  v.innerHTML = `<form id="rs" class="card" style="max-width:900px">
      <div style="display:flex;gap:16px;flex-wrap:wrap">${n("width", "Width")}${n("height", "Height")}${n("fps", "FPS")}${n("crf", "CRF (quality)")}${n("song_gain_db", "Song gain dB", 0.5)}</div>
      <div class="toolbar" style="margin-top:12px"><button>Save settings</button>
        <button type="button" class="primary" id="go" ${busy("render") || !p.shots.length ? "disabled" : ""}>Render ${missing ? "animatic" : "film"}</button>
        <span class="hint">${p.shots.length} shots · ${missing ? `${missing} missing assets render as labelled placeholder cards` : "all assets present"} · segments are cached between renders</span></div>
    </form>
    ${p.renders.map((x) => `<div class="card"><div class="toolbar"><b>${esc(x.file.split("/").pop())}</b>
        ${x.animatic ? `<span class="status-pill">animatic</span>` : ""}<span class="hint">${esc(x.created_at)}</span>
        <span class="spacer"></span><a href="${fileUrl(x.file)}" download>download</a></div>
        <video class="render" controls preload="metadata" src="${fileUrl(x.file)}"></video></div>`).join("")}`;
  $("#rs").onsubmit = (e) => {
    e.preventDefault();
    const fd = Object.fromEntries([...new FormData(e.target)].map(([k, val]) => [k, parseFloat(val)]));
    guard(async () => { state.project = await api("PUT", `${P()}/render-settings`, fd); toast("Saved"); });
  };
  $("#go").onclick = () => startTask(`${P()}/render`);
}

// --------------------------------------------------------------------------- wiring

$("#new-project").onsubmit = (e) => {
  e.preventDefault();
  const title = e.target.title.value.trim(); if (!title) return;
  guard(async () => { const p = await api("POST", "/api/projects", { title }); e.target.reset(); await openProject(p.slug, "brief"); });
};
$("#projects").onclick = (e) => { const li = e.target.closest("li"); if (li) openProject(li.dataset.slug); };
$$("#tabs button").forEach((b) => (b.onclick = () => { state.tab = b.dataset.tab; location.hash = `${state.project.slug}/${state.tab}`; if (document.activeElement) document.activeElement.blur(); render(); }));

(async () => {
  await guard(loadHealth);
  await guard(loadProjects);
  const [slug, tab] = location.hash.slice(1).split("/");
  if (slug) guard(() => openProject(slug, tab || "brief"));
})();
