/* String Art Studio client. Plain JS, hash routes, no build step.
   Views: library (#/), studio (#/p/<id>), results (#/r/<runId>), build mode (#/build/<runId>). */
(() => {
  "use strict";

  // ---------------------------------------------------------------------------------------------
  // Helpers
  // ---------------------------------------------------------------------------------------------
  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));
  const el = (tag, attrs = {}, children = []) => {
    const node = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) {
      if (v === null || v === undefined || v === false) continue;
      if (k === "class") node.className = v;
      else if (k === "html") node.innerHTML = v;
      else if (k.startsWith("on") && typeof v === "function") node.addEventListener(k.slice(2), v);
      else if (k === "dataset") Object.assign(node.dataset, v);
      else if (v === true) node.setAttribute(k, "");
      else node.setAttribute(k, v);
    }
    for (const c of [].concat(children)) {
      if (c === null || c === undefined || c === false) continue;
      node.append(c instanceof Node ? c : document.createTextNode(String(c)));
    }
    return node;
  };
  const debounce = (fn, ms) => { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; };
  const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));
  const MM_PER_IN = 25.4;
  const fmt = {
    int: (n) => (n === null || n === undefined || Number.isNaN(n)) ? "–" : Math.round(n).toLocaleString(),
    fixed: (n, d = 2) => (n === null || n === undefined || Number.isNaN(n)) ? "–" : Number(n).toFixed(d),
    pct: (n) => (n === null || n === undefined) ? "–" : (n * 100).toFixed(1) + "%",
    len: (mm) => {
      if (mm === null || mm === undefined) return "–";
      return state.units === "in" ? (mm / 914.4).toFixed(0) + " yd" : (mm / 1000).toFixed(0) + " m";
    },
    size: (mm, d = 0) => state.units === "in" ? (mm / MM_PER_IN).toFixed(d === 0 ? 1 : d) + " in" : mm.toFixed(d) + " mm",
    secs: (s) => (s === null || s === undefined) ? "–" : (s < 60 ? s.toFixed(0) + " s" : (s / 60).toFixed(1) + " min"),
    when: (ts) => {
      if (!ts) return "–";
      const d = new Date(ts * 1000), diff = (Date.now() - d.getTime()) / 1000;
      if (diff < 60) return "just now";
      if (diff < 3600) return Math.round(diff / 60) + " min ago";
      if (diff < 86400) return Math.round(diff / 3600) + " h ago";
      return d.toLocaleDateString(undefined, { month: "short", day: "numeric" }) + (d.getFullYear() !== new Date().getFullYear() ? " " + d.getFullYear() : "");
    },
  };
  const toast = (msg, ms = 3200) => {
    const t = $("#toast");
    t.textContent = msg;
    t.classList.remove("hidden");
    clearTimeout(toast.timer);
    toast.timer = setTimeout(() => t.classList.add("hidden"), ms);
  };

  // ---------------------------------------------------------------------------------------------
  // API (relative URLs resolve against <base href>, so a path prefix needs no code)
  // ---------------------------------------------------------------------------------------------
  async function call(method, url, body, opts = {}) {
    const init = { method, headers: {} };
    if (body instanceof FormData || body instanceof Blob) init.body = body;
    else if (body !== undefined) { init.headers["Content-Type"] = "application/json"; init.body = JSON.stringify(body); }
    const res = await fetch(url, init);
    if (!res.ok) {
      let detail = res.statusText;
      try { detail = (await res.json()).detail || detail; } catch (_) { /* not json */ }
      throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
    }
    if (res.status === 204) return null;
    if (opts.blob) return res.blob();
    return res.json();
  }
  const api = {
    get: (u) => call("GET", u),
    post: (u, b) => call("POST", u, b),
    patch: (u, b) => call("PATCH", u, b),
    put: (u, b) => call("PUT", u, b),
    del: (u) => call("DELETE", u),
    blob: (u, b) => call("POST", u, b, { blob: true }),
  };

  // ---------------------------------------------------------------------------------------------
  // State and router
  // ---------------------------------------------------------------------------------------------
  const state = {
    units: localStorage.getItem("sas-units") || "mm",
    defaults: null,
    health: null,
    project: null,
    presets: [],
    cleanup: [],
  };
  const sheet = $("#sheet");
  const topTitle = $("#topTitle");
  const topActions = $("#topActions");

  function setNav(active, project, runId) {
    const links = { library: "#/", studio: project ? `#/p/${project.id}` : "#", compare: runId ? `#/r/${runId}` : "#", build: runId ? `#/build/${runId}` : "#" };
    $$("#nav a").forEach((a) => {
      const key = a.dataset.nav;
      a.href = links[key];
      a.classList.toggle("hidden", (key === "studio" && !project) || ((key === "compare" || key === "build") && !runId));
      if (key === active) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current");
    });
    $("#sidebar").classList.remove("open");
    $("#scrim").classList.add("hidden");
    $("#menuButton").setAttribute("aria-expanded", "false");
  }

  function setTop(title, actions = []) {
    topTitle.textContent = "";
    if (title instanceof Node) topTitle.append(title); else topTitle.textContent = title;
    topActions.textContent = "";
    actions.forEach((a) => topActions.append(a));
  }

  function teardown() {
    state.cleanup.forEach((fn) => { try { fn(); } catch (_) { /* ignore */ } });
    state.cleanup = [];
    document.body.classList.remove("build-mode");
  }

  async function route() {
    teardown();
    const hash = location.hash || "#/";
    const parts = hash.slice(2).split("/").filter(Boolean);
    try {
      if (parts.length === 0) await viewLibrary();
      else if (parts[0] === "p" && parts[1]) await viewStudio(parts[1]);
      else if (parts[0] === "r" && parts[1]) await viewCompare(parts[1]);
      else if (parts[0] === "build" && parts[1]) await viewBuild(parts[1]);
      else location.hash = "#/";
    } catch (err) {
      console.error(err);
      sheet.textContent = "";
      sheet.append(el("div", { class: "empty" }, [el("strong", {}, "Could not load this page"), String(err.message || err)]));
    }
    sheet.scrollTop = 0;
  }
  window.addEventListener("hashchange", route);

  // ---------------------------------------------------------------------------------------------
  // Library
  // ---------------------------------------------------------------------------------------------
  async function viewLibrary() {
    setNav("library", null, null);
    const projects = await api.get("api/projects");
    const newBtn = el("button", { class: "btn primary", onclick: () => newProjectFromPicker() }, "New project");
    const importBtn = el("button", { class: "btn", onclick: () => importBundle() }, "Import bundle");
    setTop("Library", [importBtn, newBtn]);
    sheet.textContent = "";
    const withRuns = projects.filter((p) => p.run_count > 0).length;
    const finding = projects.length
      ? `${projects.length} project${projects.length === 1 ? "" : "s"}, ${withRuns} with a generated plan.`
      : "A project holds one photo and every plan generated from it.";
    sheet.append(el("div", { class: "title-row" }, [el("div", {}, [el("h1", { class: "page-title" }, "Projects"), el("div", { class: "finding" }, finding)])]));
    if (!projects.length) {
      sheet.append(el("div", { class: "empty" }, [el("strong", {}, "No projects yet"), "Start with a photo: a portrait cropped tight to the face works best. ", el("button", { class: "btn primary", style: "margin-top:16px", onclick: () => newProjectFromPicker() }, "Choose a photo")]));
      return;
    }
    const table = el("table", { class: "data" }, [
      el("thead", {}, el("tr", {}, [el("th", {}, ""), el("th", {}, "Project"), el("th", { class: "num" }, "Plans"), el("th", { class: "hide-phone" }, "Latest plan"), el("th", { class: "hide-phone" }, "Updated")])),
      el("tbody", {}, projects.map((p) => el("tr", { class: "clickable", onclick: () => { location.hash = `#/p/${p.id}`; } }, [
        el("td", { style: "width:68px" }, p.has_image ? el("img", { class: "thumb", src: `api/projects/${p.id}/image?size=160&t=${Math.floor(p.updated_at)}`, alt: "" }) : el("div", { class: "thumb placeholder" }, "no photo")),
        el("td", {}, [el("div", { style: "font-weight:500" }, p.name), p.notes ? el("div", { class: "muted", style: "font-size:12px" }, p.notes.slice(0, 80)) : null]),
        el("td", { class: "num" }, fmt.int(p.run_count)),
        el("td", { class: "hide-phone" }, p.latest_run_id ? el("a", { href: `#/r/${p.latest_run_id}`, class: "muted", onclick: (e) => e.stopPropagation() }, "Open results") : el("span", { class: "muted" }, "–")),
        el("td", { class: "hide-phone muted" }, fmt.when(p.updated_at)),
      ]))),
    ]);
    sheet.append(table);
  }

  function pickFile(input) {
    return new Promise((resolve) => {
      input.value = "";
      input.onchange = () => resolve(input.files[0] || null);
      input.click();
    });
  }

  async function newProjectFromPicker() {
    const file = await pickFile($("#filePicker"));
    if (!file) return;
    const name = file.name.replace(/\.[^.]+$/, "").replace(/[_-]+/g, " ").trim() || "Untitled";
    try {
      const p = await api.post("api/projects", { name });
      await uploadImage(p.id, file);
      location.hash = `#/p/${p.id}`;
    } catch (err) { toast("Upload failed: " + err.message); }
  }

  async function uploadImage(pid, file) {
    const fd = new FormData();
    fd.append("file", file, file.name);
    toast("Uploading photo");
    return api.post(`api/projects/${pid}/image`, fd);
  }

  async function importBundle() {
    const file = await pickFile($("#bundlePicker"));
    if (!file) return;
    const fd = new FormData();
    fd.append("file", file, file.name);
    try {
      const p = await api.post("api/projects/import", fd);
      toast("Imported " + p.name);
      location.hash = `#/p/${p.id}`;
    } catch (err) { toast("Import failed: " + err.message); }
  }

  // ---------------------------------------------------------------------------------------------
  // Studio: editor + board + engine on one page, live preview on the left
  // ---------------------------------------------------------------------------------------------
  const PREP_FIELDS = [
    { group: "tone", key: "brightness", label: "Brightness", min: -1, max: 1, step: 0.01 },
    { group: "tone", key: "contrast", label: "Contrast", min: -1, max: 1, step: 0.01 },
    { group: "tone", key: "gamma", label: "Gamma", min: 0.3, max: 3, step: 0.01, help: "Below 1 darkens mid-tones. Portraits usually want 0.7 to 0.85 so skin gets thread." },
    { group: "levels", key: "black_point", label: "Black point", min: 0, max: 0.95, step: 0.01, help: "Everything darker than this becomes true black." },
    { group: "levels", key: "white_point", label: "White point", min: 0.05, max: 1, step: 0.01, help: "Everything lighter than this becomes true white, so the thread leaves it alone." },
    { group: "detail", key: "clahe_clip", label: "Local contrast", min: 0, max: 4, step: 0.1, help: "0 is off. Pulls out facial detail without blowing out the whole image." },
    { group: "detail", key: "edge_strength", label: "Edge emphasis", min: 0, max: 2, step: 0.05, help: "Strengthens outlines such as the jaw and the eyes." },
    { group: "detail", key: "edge_radius", label: "Edge radius", min: 0.5, max: 10, step: 0.5 },
    { group: "mix", key: "mix_r", label: "Red weight", min: 0, max: 1, step: 0.01 },
    { group: "mix", key: "mix_g", label: "Green weight", min: 0, max: 1, step: 0.01 },
    { group: "mix", key: "mix_b", label: "Blue weight", min: 0, max: 1, step: 0.01 },
  ];
  const PREP_PRESETS = {
    "Portrait": { gamma: 0.8, black_point: 0.04, white_point: 0.9, clahe_clip: 1.2, edge_strength: 0.3, contrast: 0.1 },
    "High key": { gamma: 0.7, black_point: 0.02, white_point: 0.8, clahe_clip: 0.8, edge_strength: 0.5 },
    "Soft": { gamma: 1.0, black_point: 0.0, white_point: 1.0, clahe_clip: 0, edge_strength: 0, contrast: 0 },
    "Pet": { gamma: 0.85, black_point: 0.05, white_point: 0.92, clahe_clip: 2.0, edge_strength: 0.6, edge_radius: 1.5 },
  };

  async function viewStudio(pid) {
    const [project, defaults, presets, health] = await Promise.all([
      api.get(`api/projects/${pid}`), state.defaults || api.get("api/defaults"), api.get("api/presets"), state.health || api.get("api/health").catch(() => null),
    ]);
    state.defaults = defaults;
    state.health = health;
    state.presets = presets;
    state.project = project;
    const prep = Object.assign({}, defaults.prep, project.prep);
    const board = Object.assign({}, defaults.board, project.board);
    const engine = Object.assign({}, defaults.engine, project.engine);
    const runs = project.runs || [];
    const latestDone = runs.find((r) => r.status === "done");
    setNav("studio", project, latestDone ? latestDone.id : null);

    // Top bar: editable name, Generate.
    const nameInput = el("input", { type: "text", value: project.name, "aria-label": "Project name" });
    nameInput.addEventListener("change", async () => {
      const name = nameInput.value.trim() || "Untitled";
      await api.patch(`api/projects/${pid}`, { name });
      project.name = name;
    });
    const generateBtn = el("button", { class: "btn primary", onclick: () => generate() }, "Generate");
    setTop(nameInput, [generateBtn]);

    sheet.textContent = "";
    const layout = el("div", { class: "studio" });
    const previewCol = el("div", { class: "preview-col" });
    const settingsCol = el("div", { class: "settings-col" });
    layout.append(previewCol, settingsCol);
    sheet.append(layout);

    // ---- Persistence --------------------------------------------------------------------------
    const save = debounce(async () => {
      try { await api.patch(`api/projects/${pid}`, { prep, board, engine }); } catch (err) { toast("Could not save settings: " + err.message); }
    }, 600);

    // ---- Preview stage ------------------------------------------------------------------------
    const stage = el("div", { class: "preview-stage", title: "Drag to move the photo, scroll to zoom" });
    const img = el("img", { alt: "Target preview: the image as the engine will see it" });
    const maskView = el("canvas", { class: "mask", width: 512, height: 512 });
    const overlay = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    overlay.setAttribute("viewBox", "0 0 1000 1000");
    const busy = el("span", { class: "pill busy hidden" }, "Updating");
    const hint = el("span", { class: "hint" }, project.has_image ? "Drag to move, scroll to zoom" : "No photo yet");
    stage.append(img, maskView, overlay, busy, hint);
    previewCol.append(stage);

    let viewBlur = false;
    const blurStyle = () => {
      if (!viewBlur) { img.style.filter = ""; return; }
      const px = (board.viewing_distance_m * board.blur_mm_per_m) / (board.diameter_mm / stage.clientWidth);
      img.style.filter = `blur(${px.toFixed(2)}px)`;
    };

    function drawOverlay() {
      const r = (board.diameter_mm / 2 - board.pin_inset_mm) / board.diameter_mm * 1000;
      const n = board.pin_count;
      let pins = "";
      for (let i = 0; i < n; i++) {
        const a = (i / n) * Math.PI * 2;
        pins += `<circle cx="${(500 + r * Math.sin(a)).toFixed(1)}" cy="${(500 - r * Math.cos(a)).toFixed(1)}" r="2.2" />`;
      }
      let sectors = "";
      const letters = "ABCDEFGHIJKLMNOPQRSTUVWXYZ";
      for (let s = 0; s < board.sector_count; s++) {
        const a = (s / board.sector_count) * Math.PI * 2;
        sectors += `<text x="${(500 + (r + 28) * Math.sin(a)).toFixed(1)}" y="${(500 - (r + 28) * Math.cos(a) + 6).toFixed(1)}" text-anchor="middle" font-size="20" font-family="inherit" fill="#5d5f63">${letters[s]}</text>`;
      }
      overlay.innerHTML = `
        <path d="M0 0H1000V1000H0Z M500 ${500 - 500} a500 500 0 1 0 0.001 0 Z" fill="#f1f1ef" fill-opacity="0.72" fill-rule="evenodd"/>
        <circle cx="500" cy="500" r="499" fill="none" stroke="#5d5f63" stroke-width="1.2"/>
        <g fill="#15161a" fill-opacity="0.8">${pins}</g>${sectors}`;
    }

    let previewSeq = 0;
    let lastUrl = null;
    const requestPreview = debounce(async () => {
      if (!project.has_image) return;
      const seq = ++previewSeq;
      busy.classList.remove("hidden");
      try {
        const blob = await api.blob(`api/projects/${pid}/preview`, { prep, board, size: 640 });
        if (seq !== previewSeq) return;
        const url = URL.createObjectURL(blob);
        img.src = url;
        if (lastUrl) URL.revokeObjectURL(lastUrl);
        lastUrl = url;
      } catch (err) {
        if (seq === previewSeq) toast("Preview failed: " + err.message);
      } finally {
        if (seq === previewSeq) busy.classList.add("hidden");
      }
    }, 120);
    state.cleanup.push(() => { if (lastUrl) URL.revokeObjectURL(lastUrl); });

    const changed = () => { save(); requestPreview(); };

    // Pan and zoom on the stage. Panning ignores rotation; that is fine for a nudge.
    let drag = null;
    stage.addEventListener("pointerdown", (e) => {
      if (maskMode) return paintStart(e);
      if (!project.has_image) return;
      drag = { x: e.clientX, y: e.clientY, cx: prep.crop_cx, cy: prep.crop_cy };
      stage.setPointerCapture(e.pointerId);
    });
    stage.addEventListener("pointermove", (e) => {
      if (maskMode) return paintMove(e);
      if (!drag) return;
      const short = Math.min(project.image_width, project.image_height) || 1;
      const side = prep.crop_size * short;
      const fx = (e.clientX - drag.x) / stage.clientWidth, fy = (e.clientY - drag.y) / stage.clientHeight;
      prep.crop_cx = clamp(drag.cx - fx * side / project.image_width, 0, 1);
      prep.crop_cy = clamp(drag.cy - fy * side / project.image_height, 0, 1);
      syncField("crop_cx"); syncField("crop_cy");
      changed();
    });
    const endDrag = (e) => { if (maskMode) return paintEnd(e); drag = null; };
    stage.addEventListener("pointerup", endDrag);
    stage.addEventListener("pointercancel", endDrag);
    stage.addEventListener("wheel", (e) => {
      if (!project.has_image || maskMode) return;
      e.preventDefault();
      prep.crop_size = clamp(prep.crop_size * (e.deltaY > 0 ? 1.05 : 0.95), 0.05, 3);
      syncField("crop_size");
      changed();
    }, { passive: false });

    // ---- Importance mask (painted on the framed preview, saved as a 512 px PNG) ----------------
    let maskMode = false;
    const maskCanvas = document.createElement("canvas");
    maskCanvas.width = 512; maskCanvas.height = 512;
    const mctx = maskCanvas.getContext("2d", { willReadFrequently: true });
    mctx.fillStyle = "#808080"; mctx.fillRect(0, 0, 512, 512);
    let brush = { size: 40, mode: "more" };
    let painting = false, maskDirty = false;
    if (project.has_mask) {
      const m = new Image();
      m.onload = () => { mctx.drawImage(m, 0, 0, 512, 512); renderMaskView(); };
      m.src = `api/projects/${pid}/mask?t=${Date.now()}`;
    }
    function renderMaskView() {
      const src = mctx.getImageData(0, 0, 512, 512).data;
      const vctx = maskView.getContext("2d");
      const out = vctx.createImageData(512, 512);
      const d = out.data;
      for (let i = 0; i < src.length; i += 4) {
        const v = src[i];
        if (v > 132) { d[i] = 168; d[i + 1] = 50; d[i + 2] = 30; d[i + 3] = Math.round(((v - 128) / 127) * 255); }
        else if (v < 124) { d[i] = 47; d[i + 1] = 112; d[i + 2] = 187; d[i + 3] = Math.round(((128 - v) / 128) * 255); }
        else { d[i + 3] = 0; }
      }
      vctx.putImageData(out, 0, 0);
    }
    const stagePoint = (e) => {
      const r = stage.getBoundingClientRect();
      return { x: (e.clientX - r.left) / r.width * 512, y: (e.clientY - r.top) / r.height * 512 };
    };
    function paintAt(p) {
      mctx.globalCompositeOperation = "source-over";
      mctx.fillStyle = brush.mode === "more" ? "#ffffff" : brush.mode === "less" ? "#000000" : "#808080";
      mctx.beginPath(); mctx.arc(p.x, p.y, brush.size / 2, 0, Math.PI * 2); mctx.fill();
    }
    function paintStart(e) { painting = true; stage.setPointerCapture(e.pointerId); paintAt(stagePoint(e)); renderMaskView(); }
    function paintMove(e) { if (!painting) return; paintAt(stagePoint(e)); renderMaskView(); }
    async function paintEnd() {
      if (!painting) return;
      painting = false; maskDirty = true;
      saveMask();
    }
    const saveMask = debounce(async () => {
      if (!maskDirty) return;
      maskDirty = false;
      const blob = await new Promise((res) => maskCanvas.toBlob(res, "image/png"));
      try { await api.put(`api/projects/${pid}/mask`, blob); project.has_mask = true; } catch (err) { toast("Mask not saved: " + err.message); }
    }, 800);
    async function clearMask() {
      mctx.fillStyle = "#808080"; mctx.fillRect(0, 0, 512, 512); renderMaskView();
      try { await api.put(`api/projects/${pid}/mask`, new Blob([])); project.has_mask = false; } catch (err) { toast(err.message); }
    }

    // ---- Preview tools ------------------------------------------------------------------------
    const maskBtn = el("button", { class: "btn", "aria-pressed": "false" }, "Paint importance");
    const blurBtn = el("button", { class: "btn", "aria-pressed": "false" }, "Viewing distance");
    const replaceBtn = el("button", { class: "btn quiet", onclick: async () => {
      const file = await pickFile($("#filePicker"));
      if (!file) return;
      try {
        const p = await uploadImage(pid, file);
        Object.assign(project, p);
        hint.textContent = "Drag to move, scroll to zoom";
        requestPreview();
        toast("Photo replaced");
      } catch (err) { toast("Upload failed: " + err.message); }
    } }, project.has_image ? "Replace photo" : "Choose photo");
    const maskTools = el("div", { class: "mask-tools hidden" }, [
      (() => {
        const seg = el("div", { class: "seg", role: "group", "aria-label": "Brush" });
        [["more", "More important"], ["less", "Less important"], ["erase", "Erase"]].forEach(([k, label]) => {
          const b = el("button", { type: "button", "aria-pressed": String(brush.mode === k), onclick: () => { brush.mode = k; $$("button", seg).forEach((x) => x.setAttribute("aria-pressed", String(x === b))); } }, label);
          seg.append(b);
        });
        return seg;
      })(),
      el("label", { class: "muted", style: "display:flex;align-items:center;gap:8px" }, ["Brush", el("input", { type: "range", min: 8, max: 160, value: brush.size, oninput: (e) => { brush.size = Number(e.target.value); } })]),
      el("button", { class: "btn small", onclick: clearMask }, "Clear mask"),
      el("span", { class: "muted", style: "font-size:12px" }, "Red areas count up to three times as much, blue areas almost nothing. Paint the eyes and mouth red."),
    ]);
    maskBtn.addEventListener("click", () => {
      maskMode = !maskMode;
      maskBtn.setAttribute("aria-pressed", String(maskMode));
      maskTools.classList.toggle("hidden", !maskMode);
      stage.style.cursor = maskMode ? "crosshair" : "grab";
      hint.textContent = maskMode ? "Paint on the preview" : "Drag to move, scroll to zoom";
    });
    blurBtn.addEventListener("click", () => { viewBlur = !viewBlur; blurBtn.setAttribute("aria-pressed", String(viewBlur)); blurStyle(); });
    previewCol.append(el("div", { class: "preview-tools" }, [maskBtn, blurBtn, el("span", { class: "spacer" }), replaceBtn]), maskTools);

    // ---- Runs (versions) under the preview ----------------------------------------------------
    const runsBox = el("div", { class: "runs-list" });
    previewCol.append(el("h2", { class: "section", style: "margin-top:24px" }, "Plans from this photo"), runsBox);
    function renderRuns() {
      runsBox.textContent = "";
      if (!runs.length) {
        runsBox.append(el("div", { class: "muted" }, "Nothing generated yet. Tune the preview, then press Generate."));
        return;
      }
      runs.forEach((r) => {
        const m = r.metrics || {};
        const status = r.status === "done" ? el("span", { class: "pill ok" }, "Done") : r.status === "running" || r.status === "queued" ? el("span", { class: "pill live" }, r.status === "queued" ? "Queued" : "Running") : el("span", { class: "pill bad" }, r.status);
        const thumb = el("img", { src: r.status === "done" ? `api/runs/${r.id}/render.png` : `api/runs/${r.id}/live.jpg?t=${Date.now()}`, alt: "", onerror: (e) => { e.target.style.visibility = "hidden"; } });
        const row = el("div", { class: "run-row" }, [
          thumb,
          el("div", {}, [
            el("div", { style: "display:flex;gap:8px;align-items:center;flex-wrap:wrap" }, [el("b", {}, r.label || `Engine ${r.engine.toUpperCase()}`), status, el("span", { class: "muted", style: "font-size:12px" }, fmt.when(r.created_at))]),
            el("div", { class: "meta" }, r.status === "done" ? [el("b", {}, fmt.int(m.line_count)), " lines, error ", el("b", {}, fmt.fixed(m.error, 3)), ", ", el("b", {}, fmt.len(m.thread_length_mm)), " thread"] : (r.error_message || `Engine ${r.engine.toUpperCase()}`)),
          ]),
          el("a", { class: "btn small", href: `#/r/${r.id}` }, "Open"),
        ]);
        runsBox.append(row);
      });
    }
    renderRuns();

    // ---- Settings column ----------------------------------------------------------------------
    const fieldRefs = {};
    function syncField(key) {
      const ref = fieldRefs[key];
      if (!ref) return;
      ref.input.value = ref.toInput ? ref.toInput(ref.obj[key]) : ref.obj[key];
      if (ref.val) ref.val.textContent = ref.format ? ref.format(ref.obj[key]) : String(ref.obj[key]);
    }
    function rangeField(obj, spec, onChange) {
      const val = el("span", { class: "val" });
      const input = el("input", { type: "range", min: spec.min, max: spec.max, step: spec.step, value: obj[spec.key] });
      input.addEventListener("input", () => { obj[spec.key] = Number(input.value); val.textContent = spec.format ? spec.format(obj[spec.key]) : fmt.fixed(obj[spec.key], spec.step < 0.1 ? 2 : 1); onChange(); });
      input.addEventListener("dblclick", () => { if (spec.reset !== undefined) { obj[spec.key] = spec.reset; syncField(spec.key); onChange(); } });
      fieldRefs[spec.key] = { input, val, obj, format: spec.format || ((v) => fmt.fixed(v, spec.step < 0.1 ? 2 : 1)) };
      syncField(spec.key);
      return el("div", { class: "field" }, [el("label", {}, [spec.label, val]), input, spec.help ? el("div", { class: "help" }, spec.help) : null]);
    }
    function numberField(obj, key, label, opts, onChange) {
      const isLen = !!opts.length;
      const toInput = (v) => isLen && state.units === "in" ? +(v / MM_PER_IN).toFixed(3) : v;
      const fromInput = (v) => isLen && state.units === "in" ? v * MM_PER_IN : v;
      const input = el("input", { type: "number", step: opts.step || 1, min: toInput(opts.min), max: toInput(opts.max), value: toInput(obj[key]) });
      input.addEventListener("change", () => {
        let v = fromInput(Number(input.value));
        if (Number.isNaN(v)) v = obj[key];
        obj[key] = clamp(v, opts.min, opts.max);
        syncField(key);
        onChange();
      });
      fieldRefs[key] = { input, obj, toInput };
      return el("div", { class: "field" }, [el("label", {}, [label, el("span", { class: "val" }, isLen ? (state.units === "in" ? "inches" : "mm") : (opts.unit || ""))]), input, opts.help ? el("div", { class: "help" }, opts.help) : null]);
    }
    function section(title, lead, body, summary) {
      const leadEl = el("div", { class: "lead" }, [el("h2", { class: "section" }, title), el("p", {}, lead), summary ? el("div", { class: "summary" }, summary) : null]);
      return el("section", { class: "settings-section" }, [leadEl, el("div", { class: "panel" }, body)]);
    }

    // Photo: crop, zoom, rotate and tone presets.
    const cropBody = el("div", {}, [
      el("div", { class: "chips", style: "margin-bottom:14px" }, Object.keys(PREP_PRESETS).map((name) => el("button", { class: "chip", type: "button", onclick: () => { Object.assign(prep, PREP_PRESETS[name]); Object.keys(PREP_PRESETS[name]).forEach(syncField); changed(); } }, name))),
      rangeField(prep, { key: "crop_size", label: "Zoom (frame size)", min: 0.05, max: 1.5, step: 0.01, reset: 1, format: (v) => fmt.fixed(v, 2) }, changed),
      el("div", { class: "row2" }, [
        rangeField(prep, { key: "crop_cx", label: "Centre X", min: 0, max: 1, step: 0.001, reset: 0.5, format: (v) => fmt.fixed(v, 3) }, changed),
        rangeField(prep, { key: "crop_cy", label: "Centre Y", min: 0, max: 1, step: 0.001, reset: 0.5, format: (v) => fmt.fixed(v, 3) }, changed),
      ]),
      rangeField(prep, { key: "rotation", label: "Rotation", min: -180, max: 180, step: 0.5, reset: 0, format: (v) => fmt.fixed(v, 1) + "°" }, changed),
    ]);
    settingsCol.append(section("Photo and frame", "Fit the subject inside the circle. The frame mask on the preview shows exactly what the board will hold; the dimmed corners are cut off.", cropBody,
      project.has_image ? `Source ${project.image_width} × ${project.image_height} px.` : "No photo uploaded yet."));

    // Tone.
    const toneBody = el("div", {}, [
      ...PREP_FIELDS.filter((f) => f.group === "tone" || f.group === "levels").map((f) => rangeField(prep, Object.assign({ reset: defaults.prep[f.key] }, f), changed)),
    ]);
    settingsCol.append(section("Tone", "The optimiser can only match what it is given. Push the background to true white and the darkest shadows to true black, then shape the mid-tones.", toneBody));

    // Detail and channel mix.
    const detailBody = el("div", {}, [
      ...PREP_FIELDS.filter((f) => f.group === "detail").map((f) => rangeField(prep, Object.assign({ reset: defaults.prep[f.key] }, f), changed)),
      el("div", { class: "row3" }, PREP_FIELDS.filter((f) => f.group === "mix").map((f) => rangeField(prep, Object.assign({ reset: defaults.prep[f.key] }, f), changed))),
      el("div", { class: "help", style: "margin-top:-6px;color:var(--muted);font-size:12px" }, "Channel weights decide how colours map to grey: more red lightens skin, more blue darkens it."),
    ]);
    settingsCol.append(section("Detail and colour mix", "Local contrast and edge emphasis bring out eyes, jawlines and hair without a global contrast boost.", detailBody));

    // Background removal and mask strength.
    const bgAvailable = !!(health && health.background_removal);
    const bgToggle = el("button", { class: "toggle", role: "switch", "aria-checked": String(!!prep.background_removal), disabled: !bgAvailable, onclick: () => { prep.background_removal = !prep.background_removal; bgToggle.setAttribute("aria-checked", String(prep.background_removal)); changed(); } });
    const subjectBody = el("div", {}, [
      el("div", { class: "switch" }, [el("div", {}, [el("div", { class: "label" }, "Remove background"), el("div", { class: "sub" }, bgAvailable ? "Isolates the subject with a local model (rembg). The first preview after switching it on takes a few seconds." : "Not installed on this server. Install the rembg extra to enable it.")]), bgToggle]),
      rangeField(prep, { key: "mask_strength", label: "Importance mask strength", min: 0, max: 3, step: 0.1, reset: 1, help: "How much the painted mask counts. 0 ignores it." }, changed),
    ]);
    settingsCol.append(section("Subject", "Optional one-click subject isolation, and how strongly the painted importance mask weighs in.", subjectBody));

    // Board.
    const unitsSeg = el("div", { class: "seg", role: "group", "aria-label": "Units" });
    [["mm", "mm"], ["in", "inches"]].forEach(([k, label]) => {
      unitsSeg.append(el("button", { type: "button", "aria-pressed": String(state.units === k), onclick: () => { state.units = k; localStorage.setItem("sas-units", k); route(); } }, label));
    });
    const boardChanged = () => { board.min_skip = clamp(board.min_skip, 1, Math.floor(board.pin_count / 2) - 1); drawOverlay(); blurStyle(); save(); requestPreview(); boardSummary.textContent = boardSummaryText(); };
    const invertToggle = el("button", { class: "toggle", role: "switch", "aria-checked": String(!!board.invert), onclick: () => { board.invert = !board.invert; invertToggle.setAttribute("aria-checked", String(board.invert)); if (board.invert && board.thread_color === "#161616") { board.thread_color = "#f3f0ea"; board.board_color = "#15161a"; } else if (!board.invert && board.thread_color === "#f3f0ea") { board.thread_color = "#161616"; board.board_color = "#f6f3ee"; } threadColor.value = board.thread_color; boardColor.value = board.board_color; boardChanged(); } });
    const threadColor = el("input", { type: "color", value: board.thread_color, onchange: (e) => { board.thread_color = e.target.value; boardChanged(); } });
    const boardColor = el("input", { type: "color", value: board.board_color, onchange: (e) => { board.board_color = e.target.value; boardChanged(); } });
    const boardSummaryText = () => `${fmt.size(board.diameter_mm)} board, ${board.pin_count} pins ${fmt.size(board.pin_inset_mm, 1)} in from the edge, pins ${(board.diameter_mm / board.pin_count * Math.PI / 1).toFixed(1)} mm apart along the rim. Thread ${board.thread_width_mm} mm.`;
    const boardSummary = el("div", {}, boardSummaryText());
    const boardBody = el("div", {}, [
      el("div", { style: "display:flex;justify-content:flex-end;margin-bottom:8px" }, unitsSeg),
      el("div", { class: "row2" }, [
        numberField(board, "diameter_mm", "Diameter", { min: 200, max: 1000, length: true, step: state.units === "in" ? 0.25 : 5 }, boardChanged),
        numberField(board, "pin_count", "Pin count", { min: 100, max: 400, step: 1 }, boardChanged),
        numberField(board, "pin_inset_mm", "Pin inset from edge", { min: 5, max: 30, length: true, step: state.units === "in" ? 0.05 : 1 }, boardChanged),
        numberField(board, "pin_diameter_mm", "Pin diameter", { min: 0.5, max: 5, length: true, step: state.units === "in" ? 0.005 : 0.1, help: "Match the brad gauge; used for the fab files." }, boardChanged),
        numberField(board, "thread_width_mm", "Thread width", { min: 0.05, max: 2, length: true, step: state.units === "in" ? 0.001 : 0.01, help: "Measure ten wraps around a ruler and divide by ten." }, boardChanged),
        numberField(board, "thread_opacity", "Thread opacity", { min: 0.2, max: 3, step: 0.05, help: "Calibration knob after the pilot build. 1 means the thread covers exactly its width." }, boardChanged),
      ]),
      el("div", { class: "row2" }, [
        el("div", { class: "field" }, [el("label", {}, "Thread colour"), threadColor]),
        el("div", { class: "field" }, [el("label", {}, "Board colour"), boardColor]),
      ]),
      el("div", { class: "switch" }, [el("div", {}, [el("div", { class: "label" }, "Invert"), el("div", { class: "sub" }, "Light thread on a dark board. The thread then builds the light areas.")]), invertToggle]),
      el("div", { class: "row2" }, [
        numberField(board, "min_skip", "Minimum pin skip", { min: 1, max: 199, step: 1, help: "Chords between pins closer than this are never used; they hug the rim and add little." }, boardChanged),
        numberField(board, "max_lines", "Maximum lines", { min: 10, max: 20000, step: 100, help: "A cap. The engines stop earlier when no line still helps." }, boardChanged),
        numberField(board, "viewing_distance_m", "Viewing distance", { min: 0.3, max: 10, step: 0.1, unit: "metres", help: "Sets the blur both engines compare through." }, boardChanged),
        numberField(board, "sector_count", "Sectors", { min: 1, max: 26, step: 1, help: "Pins are grouped A, B, C... so they are quicker to find while building." }, boardChanged),
      ]),
    ]);
    const boardSection = section("Board, pins and thread", "Every physical parameter, so the simulation matches the real board. Save a configuration you like as a preset.", boardBody, boardSummary);
    settingsCol.append(boardSection);

    // Engine and presets.
    const engineSeg = el("div", { class: "seg", role: "group", "aria-label": "Engine" });
    const engineChoice = { value: engine.engine === "both" ? "both" : engine.engine || "a" };
    [["a", "Engine A"], ["b", "Engine B"], ["both", "Run both"]].forEach(([k, label]) => {
      engineSeg.append(el("button", { type: "button", "aria-pressed": String(engineChoice.value === k), onclick: () => { engineChoice.value = k; engine.engine = k; $$("button", engineSeg).forEach((b) => b.setAttribute("aria-pressed", String(b.textContent === label))); save(); } }, label));
    });
    const presetSelect = el("select", { "aria-label": "Preset" }, [el("option", { value: "" }, "Load a preset"), ...presets.map((p) => el("option", { value: p.id }, p.name))]);
    presetSelect.addEventListener("change", () => {
      const p = presets.find((x) => x.id === presetSelect.value);
      if (!p) return;
      Object.assign(board, p.board);
      Object.assign(engine, p.engine || {});
      toast("Preset loaded: " + p.name);
      save();
      route();
    });
    const savePresetBtn = el("button", { class: "btn", onclick: async () => {
      const name = prompt("Preset name", `${board.diameter_mm} mm, ${board.pin_count} pins`);
      if (!name) return;
      const p = await api.post("api/presets", { name, board, engine });
      presets.push(p);
      presetSelect.append(el("option", { value: p.id }, p.name));
      presetSelect.value = p.id;
      toast("Preset saved");
    } }, "Save as preset");
    const deletePresetBtn = el("button", { class: "btn quiet danger", onclick: async () => {
      const p = presets.find((x) => x.id === presetSelect.value);
      if (!p || !confirm(`Delete preset "${p.name}"?`)) return;
      await api.del(`api/presets/${p.id}`);
      presets.splice(presets.indexOf(p), 1);
      presetSelect.querySelector(`option[value="${p.id}"]`).remove();
      presetSelect.value = "";
    } }, "Delete preset");
    const engineBody = el("div", {}, [
      el("div", { class: "field" }, [el("label", {}, "Engine"), engineSeg, el("div", { class: "help" }, "A is the proven greedy walk with refinement. B builds the best set of chords first, then repairs it into one continuous thread. Run both to compare them side by side.")]),
      rangeField(engine, { key: "light_protection", label: "Light area protection", min: 0.05, max: 1, step: 0.05, reset: 0.5, help: "1 treats a thread crossing a white area as bad as a missing thread in a dark area. Lower values allow more wash and darker faces.", format: (v) => fmt.fixed(v, 2) }, save),
      el("div", { class: "row2" }, [
        numberField(engine, "refine_seconds", "Refinement budget (A)", { min: 0, max: 600, step: 5, unit: "seconds" }, save),
        numberField(engine, "time_budget_s", "Time limit", { min: 5, max: 3600, step: 10, unit: "seconds" }, save),
        numberField(board, "work_res", "Working resolution", { min: 300, max: 2400, step: 100, unit: "px", help: "800 px is about three thread widths per pixel on a 610 mm board and runs in under a minute. Higher is slower." }, boardChanged),
        numberField(engine, "batch_add", "Chords per round (B)", { min: 1, max: 200, step: 1 }, save),
      ]),
      el("div", { class: "btn-row", style: "margin-top:6px;align-items:center" }, [el("div", { style: "flex:1;min-width:160px" }, presetSelect), savePresetBtn, deletePresetBtn]),
    ]);
    settingsCol.append(section("Engine", "Choose the generator and how long it may spend. Presets store the board and engine settings together.", engineBody));

    // Project notes and danger zone.
    const notes = el("textarea", { rows: 3, placeholder: "Notes: thread brand, who it is for, calibration results" }, project.notes || "");
    notes.addEventListener("change", () => api.patch(`api/projects/${pid}`, { notes: notes.value }).catch((e) => toast(e.message)));
    const projectBody = el("div", {}, [
      el("div", { class: "field" }, [el("label", {}, "Notes"), notes]),
      el("div", { class: "btn-row" }, [
        el("a", { class: "btn", href: `api/projects/${pid}/bundle.zip` }, "Export project bundle"),
        el("button", { class: "btn quiet danger", onclick: async () => {
          if (!confirm(`Delete project "${project.name}" and all of its plans? This cannot be undone.`)) return;
          await api.del(`api/projects/${pid}`);
          location.hash = "#/";
        } }, "Delete project"),
      ]),
    ]);
    settingsCol.append(section("Project", "Notes travel with the bundle. Deleting removes the photo and every plan.", projectBody));

    drawOverlay();
    if (project.has_image) requestPreview();
    else stage.append(el("div", { class: "dropzone", style: "position:absolute;inset:24px;display:flex;align-items:center;justify-content:center", onclick: () => replaceBtn.click() }, "Choose a photo to begin"));

    // Drop a file anywhere on the studio to replace the photo.
    const onDrop = async (e) => {
      e.preventDefault();
      const file = e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files[0];
      if (!file) return;
      try { Object.assign(project, await uploadImage(pid, file)); requestPreview(); toast("Photo replaced"); } catch (err) { toast(err.message); }
    };
    const onDragOver = (e) => e.preventDefault();
    sheet.addEventListener("drop", onDrop);
    sheet.addEventListener("dragover", onDragOver);
    state.cleanup.push(() => { sheet.removeEventListener("drop", onDrop); sheet.removeEventListener("dragover", onDragOver); });

    async function generate() {
      if (!project.has_image) { toast("Choose a photo first"); return; }
      generateBtn.disabled = true;
      try {
        if (maskDirty) { saveMask.flush && saveMask.flush(); }
        await api.patch(`api/projects/${pid}`, { prep, board, engine });
        const res = await api.post(`api/projects/${pid}/runs`, { engine: engineChoice.value, prep, board, params: engine });
        location.hash = `#/r/${res.runs[0].id}`;
      } catch (err) {
        toast("Could not start: " + err.message);
      } finally {
        generateBtn.disabled = false;
      }
    }
  }

  // ---------------------------------------------------------------------------------------------
  // Results: target vs render, engine A vs engine B, exports
  // ---------------------------------------------------------------------------------------------
  async function viewCompare(runId) {
    const run = await api.get(`api/runs/${runId}`);
    const project = await api.get(`api/projects/${run.project_id}`);
    state.project = project;
    const allRuns = project.runs || [];
    let shown = run.group_id ? allRuns.filter((r) => r.group_id === run.group_id) : [run];
    shown.sort((a, b) => a.engine.localeCompare(b.engine));
    const extra = (location.hash.split("?")[1] || "").split("&").map((s) => s.split("=")).find((kv) => kv[0] === "with");
    if (extra && extra[1]) {
      const other = allRuns.find((r) => r.id === extra[1]);
      if (other && !shown.some((r) => r.id === other.id)) shown.push(other);
    }
    setNav("compare", project, runId);
    const buildBtn = el("a", { class: "btn primary", href: `#/build/${runId}` }, "Build this one");
    const backBtn = el("a", { class: "btn quiet", href: `#/p/${project.id}` }, "Settings");
    setTop(project.name, [backBtn, buildBtn]);
    sheet.textContent = "";

    const board = run.board || {};
    const title = el("h1", { class: "page-title" }, shown.length > 1 ? "Engine A against engine B" : `Engine ${run.engine.toUpperCase()} result`);
    const finding = el("div", { class: "finding" }, "");
    sheet.append(el("div", { class: "title-row" }, [el("div", {}, [title, finding])]));

    // Cards: target + one per run. The metrics box exists first because a finished run fills it
    // while its card is being built.
    const metricsBox = el("div");
    let viewBlur = false;
    const grid = el("div", { class: "compare-grid" });
    // The target is written a moment after the run starts; retry until it exists.
    let targetTries = 0;
    const targetImg = el("img", { alt: "Target as the engine saw it", onerror: () => {
      if (targetTries++ < 20) setTimeout(() => { targetImg.src = `api/runs/${runId}/target.jpg?t=${Date.now()}`; }, 1500);
      else targetImg.alt = "Target not available";
    } });
    targetImg.src = `api/runs/${runId}/target.jpg`;
    grid.append(el("div", { class: "compare-card" }, [el("div", { class: "head" }, [el("b", {}, "Target"), el("span", { class: "muted", style: "font-size:12px" }, `${board.work_res || ""} px working image`)]), targetImg]));
    const cards = {};
    shown.forEach((r) => {
      const img = el("img", { alt: `Engine ${r.engine.toUpperCase()} render` });
      const status = el("span", { class: "pill" }, r.status);
      const bar = el("div", { class: "progress-bar" }, el("i"));
      const phase = el("div", { class: "muted", style: "font-size:12px;margin-top:6px;min-height:16px" }, "");
      const cancel = el("button", { class: "btn small hidden", onclick: () => api.post(`api/runs/${r.id}/cancel`).then(() => toast("Cancelling")) }, "Cancel");
      const label = el("b", {}, r.label || `Engine ${r.engine.toUpperCase()}`);
      const card = el("div", { class: "compare-card" }, [el("div", { class: "head" }, [label, el("span", { style: "display:flex;gap:8px;align-items:center" }, [status, cancel])]), img, bar, phase]);
      grid.append(card);
      cards[r.id] = { run: r, img, status, bar, phase, cancel, label };
      applyRunState(r.id, { status: r.status, phase: r.status, lines: (r.metrics || {}).line_count, metrics: r.metrics });
      if (r.status === "queued" || r.status === "running") subscribe(r.id);
    });
    sheet.append(grid);

    const blurBtn = el("button", { class: "btn", "aria-pressed": "false", onclick: () => {
      viewBlur = !viewBlur;
      blurBtn.setAttribute("aria-pressed", String(viewBlur));
      const px = ((board.viewing_distance_m || 1.5) * (board.blur_mm_per_m || 1.5)) / ((board.diameter_mm || 610) / targetImg.clientWidth);
      $$(".compare-card img", grid).forEach((i) => { i.style.filter = viewBlur ? `blur(${px.toFixed(2)}px)` : ""; });
    } }, "Viewing distance");
    const withSelect = el("select", { "aria-label": "Compare with another plan", style: "width:auto" }, [el("option", { value: "" }, "Compare with another plan"), ...allRuns.filter((r) => r.status === "done" && !shown.some((s) => s.id === r.id)).map((r) => el("option", { value: r.id }, `${r.label || "Engine " + r.engine.toUpperCase()} · ${fmt.when(r.created_at)} · ${fmt.int((r.metrics || {}).line_count)} lines`))]);
    withSelect.addEventListener("change", () => { if (withSelect.value) location.hash = `#/r/${runId}?with=${withSelect.value}`; });
    const renameBtn = el("button", { class: "btn quiet", onclick: async () => {
      const label = prompt("Name this plan", run.label || "");
      if (label === null) return;
      await api.patch(`api/runs/${runId}`, { label });
      run.label = label;
      cards[runId].label.textContent = label || `Engine ${run.engine.toUpperCase()}`;
    } }, "Rename");
    const deleteBtn = el("button", { class: "btn quiet danger", onclick: async () => {
      if (!confirm("Delete this plan?")) return;
      await api.del(`api/runs/${runId}`);
      location.hash = `#/p/${project.id}`;
    } }, "Delete plan");
    sheet.append(el("div", { class: "btn-row", style: "margin:16px 0 8px;align-items:center" }, [blurBtn, withSelect, el("span", { style: "flex:1" }), renameBtn, deleteBtn]));

    // Metrics table.
    sheet.append(el("h2", { class: "section", style: "margin-top:24px" }, "Numbers"), metricsBox);
    function renderMetrics() {
      const rows = [
        ["Error (weighted RMS, lower is better)", (m) => fmt.fixed(m.error, 4), (a, b) => a < b],
        ["Lines", (m) => fmt.int(m.line_count), null],
        ["Thread", (m) => fmt.len(m.thread_length_mm), null],
        ["Thread to buy (20% margin)", (m) => fmt.len(m.thread_with_margin_mm), null],
        ["Spools of 1,000 yd", (m) => fmt.int(m.spools_1000yd), null],
        ["Generation time", (m) => fmt.secs(m.elapsed_s), (a, b) => a < b],
        ["Stopped because", (m) => m.stop_reason || "–", null],
        ["Repair lines added (B)", (m) => m.engine === "b" ? fmt.int(m.repair_lines) : "–", null],
        ["Error added by repair (B)", (m) => m.engine === "b" ? (m.repair_error >= 0 ? "+" : "") + fmt.fixed(m.repair_error, 4) : "–", null],
        ["Refinement changes (A)", (m) => m.engine === "a" ? fmt.int(m.refined_changes) : "–", null],
        ["Coverage per pass", (m) => fmt.fixed(m.coverage_per_pass, 3), null],
      ];
      const done = shown.filter((r) => r.status === "done" && r.metrics);
      metricsBox.textContent = "";
      if (!done.length) { metricsBox.append(el("div", { class: "muted" }, "Numbers appear when a plan finishes.")); return; }
      const keyFor = { "Error (weighted RMS, lower is better)": "error", "Generation time": "elapsed_s" };
      const table = el("table", { class: "metrics" }, [
        el("thead", {}, el("tr", {}, [el("th", {}, ""), ...done.map((r) => el("th", { class: "num" }, r.label || `Engine ${r.engine.toUpperCase()}`))])),
        el("tbody", {}, rows.map(([name, f, better]) => {
          let bestId = null;
          if (better && done.length > 1) {
            const key = keyFor[name];
            bestId = done.reduce((best, r) => (best === null || better(r.metrics[key], best.metrics[key]) ? r : best), null).id;
          }
          return el("tr", {}, [el("td", {}, name), ...done.map((r) => el("td", { class: "num" + (r.id === bestId ? " best" : "") }, f(r.metrics)))]);
        })),
      ]);
      metricsBox.append(table);
      const m = (shown.find((r) => r.id === runId) || done[0]).metrics;
      finding.textContent = `${fmt.int(m.line_count)} lines and ${fmt.len(m.thread_length_mm)} of thread, error ${fmt.fixed(m.error, 3)} from ${fmt.fixed(m.error_initial, 3)}; buy ${fmt.len(m.thread_with_margin_mm)} (${fmt.int(m.spools_1000yd)} spool${m.spools_1000yd === 1 ? "" : "s"} of 1,000 yd).`;
    }
    renderMetrics();

    // Exports.
    const paperName = state.units === "in" ? "Letter" : "A4";
    const paperKey = state.units === "in" ? "letter" : "a4";
    const holeInput = el("input", { type: "number", step: 0.1, min: 0.5, max: 10, value: board.pin_diameter_mm || 1.2, style: "width:90px;height:32px", "aria-label": "Hole diameter in mm" });
    const fabLink = (fmtName, variant) => el("a", { class: "btn small", href: `api/runs/${runId}/fab.${fmtName}?variant=${variant}&hole=${holeInput.value}`, onclick: (e) => { e.target.href = `api/runs/${runId}/fab.${fmtName}?variant=${variant}&hole=${holeInput.value}`; } }, `${fmtName.toUpperCase()} ${variant}`);
    const exportsBox = el("div", { class: "exports" }, [
      el("div", { class: "export-card panel" }, [el("h3", {}, "Pin template, true size"), el("p", {}, "Tiled across sheets with alignment marks and overlap, or one sheet for large-format printing. Numbered pins, sector letters, centre mark and a 100 mm check bar."), el("div", { class: "btn-row" }, [
        el("a", { class: "btn small", href: `api/runs/${runId}/template.pdf?paper=letter` }, "Letter tiles"),
        el("a", { class: "btn small", href: `api/runs/${runId}/template.pdf?paper=a4` }, "A4 tiles"),
        el("a", { class: "btn small", href: `api/runs/${runId}/template.pdf?paper=single` }, "Single sheet"),
      ])]),
      el("div", { class: "export-card panel" }, [el("h3", {}, "Fab file for laser or CNC"), el("p", {}, "Board outline plus pin holes at a chosen diameter, or a notched ring. Millimetre units."), el("div", { class: "btn-row", style: "align-items:center" }, [el("label", { class: "muted", style: "display:flex;gap:6px;align-items:center;font-size:12px" }, ["Hole mm", holeInput]), fabLink("svg", "holes"), fabLink("dxf", "holes"), fabLink("svg", "ring"), fabLink("dxf", "ring")])]),
      el("div", { class: "export-card panel" }, [el("h3", {}, "Sequence"), el("p", {}, "Pin order for any player or a manual build. The JSON carries the board and the pin table too."), el("div", { class: "btn-row" }, [
        el("a", { class: "btn small", href: `api/runs/${runId}/sequence.txt` }, "TXT"),
        el("a", { class: "btn small", href: `api/runs/${runId}/sequence.csv` }, "CSV"),
        el("a", { class: "btn small", href: `api/runs/${runId}/sequence.json` }, "JSON"),
      ])]),
      el("div", { class: "export-card panel" }, [el("h3", {}, "Pictures"), el("p", {}, "The simulated piece and the side-by-side comparison."), el("div", { class: "btn-row" }, [
        el("a", { class: "btn small", href: `api/runs/${runId}/render.png`, download: "string-art-render.png" }, "Render PNG"),
        el("a", { class: "btn small", href: `api/runs/${runId}/compare.jpg`, download: "string-art-compare.jpg" }, "Comparison"),
        el("a", { class: "btn small", href: `api/runs/${runId}/compare.jpg?blur=1`, download: "string-art-compare-blurred.jpg" }, "Comparison, blurred"),
      ])]),
      el("div", { class: "export-card panel" }, [el("h3", {}, "Project bundle"), el("p", {}, "ZIP with the photo, the mask, every plan and its settings. Import it on another instance."), el("div", { class: "btn-row" }, [el("a", { class: "btn small", href: `api/projects/${project.id}/bundle.zip` }, "Download bundle")])]),
    ]);
    sheet.append(el("h2", { class: "section", style: "margin-top:28px" }, "Exports"), exportsBox);
    void paperName; void paperKey;

    function applyRunState(id, ev) {
      const c = cards[id];
      if (!c) return;
      const status = ev.status || c.run.status;
      c.status.textContent = status === "done" ? "Done" : status === "running" ? "Running" : status === "queued" ? "Queued" : status;
      c.status.className = "pill " + (status === "done" ? "ok" : status === "running" || status === "queued" ? "live" : "bad");
      c.cancel.classList.toggle("hidden", !(status === "running" || status === "queued"));
      const max = (c.run.board || {}).max_lines || 4000;
      const lines = ev.lines || 0;
      c.bar.firstChild.style.width = status === "done" ? "100%" : `${clamp((lines / max) * 100, 0, 100)}%`;
      c.bar.classList.toggle("hidden", status === "done" || status === "failed" || status === "cancelled");
      if (status === "done") {
        c.img.src = `api/runs/${id}/render.png?t=${ev.preview_version || ""}`;
        c.phase.textContent = "";
        if (ev.metrics) { c.run.metrics = ev.metrics; c.run.status = "done"; renderMetrics(); }
      } else if (status === "failed") {
        c.phase.textContent = "Failed: " + (ev.error || c.run.error_message || "unknown error");
      } else if (status === "cancelled") {
        c.phase.textContent = "Cancelled";
        if (ev.preview_version || lines) c.img.src = `api/runs/${id}/live.jpg?t=${Date.now()}`;
      } else {
        c.phase.textContent = `${ev.phase || status}: ${fmt.int(lines)} lines, ${fmt.secs(ev.elapsed_s || 0)}`;
        if (ev.preview_version) c.img.src = `api/runs/${id}/live.jpg?v=${ev.preview_version}`;
      }
    }

    function subscribe(id) {
      const es = new EventSource(`api/runs/${id}/events`);
      es.onmessage = (m) => {
        const ev = JSON.parse(m.data);
        applyRunState(id, ev);
        if (ev.status === "done" || ev.status === "failed" || ev.status === "cancelled") es.close();
      };
      es.onerror = () => { /* the browser retries; a closed server stream ends with the final event */ };
      state.cleanup.push(() => es.close());
    }
  }

  // ---------------------------------------------------------------------------------------------
  // Build mode: phone first, thread in both hands
  // ---------------------------------------------------------------------------------------------
  async function viewBuild(runId) {
    const data = await api.get(`api/runs/${runId}/steps`);
    const seq = data.sequence;
    const n = data.pin_count;
    const labels = data.labels;
    const total = Math.max(seq.length - 1, 0);
    const storeKey = `sas-build-${runId}`;
    let step = Number((data.progress && data.progress.step) || 0);
    const local = Number(localStorage.getItem(storeKey) || 0);
    const localAt = Number(localStorage.getItem(storeKey + "-at") || 0);
    if (local > step && localAt > ((data.progress && data.progress.updated_at) || 0) * 1000) step = local;
    step = clamp(step, 0, total);

    setNav("build", state.project && state.project.id === data.project_id ? state.project : { id: data.project_id }, runId);
    document.body.classList.add("build-mode");
    sheet.textContent = "";

    const prefs = JSON.parse(localStorage.getItem("sas-build-prefs") || "{}");
    let voice = !!prefs.voice;
    let pace = Number(prefs.pace || 0);
    let rate = Number(prefs.rate || 1);
    let timer = null;

    const stepEl = el("span", { class: "step num" });
    const exitBtn = el("a", { class: "btn quiet", href: `#/r/${runId}` }, "Exit");
    const top = el("div", { class: "build-top" }, [exitBtn, stepEl, el("span", { class: "muted", style: "font-size:12px" }, fmt.len(data.thread.length_mm) + " total")]);
    const progress = el("div", { class: "build-progress" }, el("i"));
    const main = el("div", { class: "build-main", role: "button", tabindex: 0, "aria-label": "Advance to the next pin" });
    const backBtn = el("button", { class: "btn" }, "Back");
    const nextBtn = el("button", { class: "btn primary" }, "Next");
    const voiceBtn = el("button", { class: "btn small", "aria-pressed": String(voice) }, "Voice");
    const paceSel = el("select", { "aria-label": "Auto-advance" }, [[0, "Manual"], [3, "Every 3 s"], [5, "Every 5 s"], [8, "Every 8 s"], [12, "Every 12 s"]].map(([v, t]) => el("option", { value: v, selected: v === pace }, t)));
    const rateSel = el("select", { "aria-label": "Voice speed" }, [[0.8, "Slow voice"], [1, "Normal voice"], [1.25, "Fast voice"]].map(([v, t]) => el("option", { value: v, selected: v === rate }, t)));
    const pauseBtn = el("button", { class: "btn small hidden" }, "Pause");
    const bar = el("div", { class: "build-bar" }, [backBtn.cloneNode(false), voiceBtn, rateSel, paceSel, pauseBtn]);
    bar.firstChild.remove();
    const controls = el("div", { class: "build-controls" }, [backBtn, nextBtn]);
    const wrap = el("div", { class: "build" }, [el("div", {}, [top, progress]), main, el("div", {}, [bar, controls])]);
    sheet.append(wrap);

    function savePrefs() { localStorage.setItem("sas-build-prefs", JSON.stringify({ voice, pace, rate })); }
    const persist = debounce(() => {
      localStorage.setItem(storeKey, String(step));
      localStorage.setItem(storeKey + "-at", String(Date.now()));
      api.put(`api/runs/${runId}/progress`, { step, device: navigator.platform || "" }).catch(() => { /* offline: local copy stands */ });
    }, 1200);

    function direction(from, to) {
      const cw = ((to - from) % n + n) % n;
      if (cw === 0) return "same pin";
      return cw <= n / 2 ? `${cw} pins clockwise` : `${n - cw} pins counter-clockwise`;
    }
    function speak(text) {
      if (!voice || !("speechSynthesis" in window)) return;
      window.speechSynthesis.cancel();
      const u = new SpeechSynthesisUtterance(text);
      u.rate = rate;
      window.speechSynthesis.speak(u);
    }
    function sayLabel(pin) { return labels[pin].replace(/^([A-Z])(\d+)$/, "$1 $2"); }
    function render(announce) {
      stepEl.textContent = `Step ${fmt.int(step)} of ${fmt.int(total)}`;
      progress.firstChild.style.width = `${(step / Math.max(total, 1)) * 100}%`;
      main.textContent = "";
      if (step >= total) {
        main.append(el("div", { class: "build-done" }, [el("div", { class: "pin", style: "font-size:64px" }, "Done"), el("p", { class: "muted" }, `All ${fmt.int(total)} lines strung. Tie off at pin ${seq[total]} (${labels[seq[total]]}).`)]));
        nextBtn.disabled = true;
        stopAuto();
        if (announce) speak("Finished. Tie off the thread.");
        return;
      }
      nextBtn.disabled = false;
      const from = seq[step], to = seq[step + 1], after = step + 2 <= total ? seq[step + 2] : null;
      main.append(
        el("div", { class: "label" }, step === 0 ? `Start: tie the thread at pin ${from} (${labels[from]}), then wrap` : `From pin ${from}, wrap`),
        el("div", { class: "pin num" }, String(to)),
        el("div", { class: "sector" }, labels[to]),
        el("div", { class: "dir" }, direction(from, to)),
        after !== null ? el("div", { class: "next" }, ["then ", el("b", { class: "num" }, String(after)), ` (${labels[after]})`]) : el("div", { class: "next" }, "last line"),
        el("div", { class: "thread" }, `${fmt.int(total - step)} lines left`),
      );
      backBtn.disabled = step === 0;
      if (announce) speak(`Pin ${to}. ${sayLabel(to)}.`);
    }
    function advance(delta, announce = true) {
      const next = clamp(step + delta, 0, total);
      if (next === step) return;
      step = next;
      render(announce);
      persist();
    }
    function startAuto() {
      stopAuto();
      if (!pace) return;
      timer = setInterval(() => advance(1), pace * 1000);
      pauseBtn.classList.remove("hidden");
      pauseBtn.textContent = "Pause";
    }
    function stopAuto() { if (timer) clearInterval(timer); timer = null; }

    main.addEventListener("click", () => advance(1));
    nextBtn.addEventListener("click", () => advance(1));
    backBtn.addEventListener("click", () => advance(-1));
    voiceBtn.addEventListener("click", () => { voice = !voice; voiceBtn.setAttribute("aria-pressed", String(voice)); savePrefs(); if (voice) speak("Voice on"); });
    rateSel.addEventListener("change", () => { rate = Number(rateSel.value); savePrefs(); });
    paceSel.addEventListener("change", () => { pace = Number(paceSel.value); savePrefs(); if (pace) startAuto(); else { stopAuto(); pauseBtn.classList.add("hidden"); } });
    pauseBtn.addEventListener("click", () => { if (timer) { stopAuto(); pauseBtn.textContent = "Resume"; } else { startAuto(); } });
    const onKey = (e) => {
      if (["INPUT", "SELECT", "TEXTAREA"].includes(document.activeElement.tagName)) return;
      if ([" ", "Enter", "ArrowRight", "ArrowDown", "PageDown", "MediaTrackNext"].includes(e.key)) { e.preventDefault(); advance(1); }
      else if (["ArrowLeft", "ArrowUp", "PageUp", "Backspace", "MediaTrackPrevious"].includes(e.key)) { e.preventDefault(); advance(-1); }
    };
    document.addEventListener("keydown", onKey);
    state.cleanup.push(() => { document.removeEventListener("keydown", onKey); stopAuto(); if ("speechSynthesis" in window) window.speechSynthesis.cancel(); });

    // Keep the screen on while building.
    let lock = null;
    const requestLock = async () => {
      try { if ("wakeLock" in navigator && document.visibilityState === "visible") lock = await navigator.wakeLock.request("screen"); } catch (_) { /* unsupported or denied */ }
    };
    const onVis = () => { if (document.visibilityState === "visible") requestLock(); };
    document.addEventListener("visibilitychange", onVis);
    state.cleanup.push(() => { document.removeEventListener("visibilitychange", onVis); if (lock) lock.release().catch(() => {}); });
    requestLock();

    render(false);
    if (pace) startAuto();
  }

  // ---------------------------------------------------------------------------------------------
  // Boot
  // ---------------------------------------------------------------------------------------------
  $("#menuButton").addEventListener("click", () => {
    const open = !$("#sidebar").classList.contains("open");
    $("#sidebar").classList.toggle("open", open);
    $("#scrim").classList.toggle("hidden", !open);
    $("#menuButton").setAttribute("aria-expanded", String(open));
  });
  $("#scrim").addEventListener("click", () => { $("#sidebar").classList.remove("open"); $("#scrim").classList.add("hidden"); });
  api.get("api/health").then((h) => { state.health = h; $("#health").textContent = `Server up, ${h.workers} worker${h.workers === 1 ? "" : "s"}`; }).catch(() => { $("#health").textContent = "Server unreachable"; });
  if ("serviceWorker" in navigator) {
    navigator.serviceWorker.register("sw.js").catch(() => { /* http on a LAN address has no SW; fine */ });
  }
  route();
})();
