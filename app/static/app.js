/* MEDKids Rap Studio: a thin client over the authenticated REST API.
   All text from the server is inserted with textContent (never innerHTML). */
(() => {
  "use strict";

  const ROLES = { author: "Author", medical_reviewer: "Medical reviewer", educator: "Educator" };
  const STATUS_TEXT = {
    draft: "Draft", validation_failed: "Failed checks", pending_review: "Waiting for review",
    revision_requested: "Changes requested", approved: "Approved", rejected: "Rejected",
    candidate: "Proposed", uncertain: "Uncertain", passed: "Passed", requires_review: "Needs review",
    failed: "Failed", supported: "Supported", contradicted: "Contradicted", unsupported: "Unsupported",
    ambiguous: "Ambiguous", outside_scope: "Outside topic",
  };
  const SCORE_NAMES = {
    medical_accuracy: "Medical accuracy", educational_coverage: "Learning goals covered",
    age_appropriateness: "Right for the age", rhyme_rhythm: "Rhyme and rhythm", memorability: "Catchiness",
  };
  const GATE_NAMES = {
    medical_accuracy_not_failed: "No medical errors", child_safety_not_failed: "No child-safety problems",
    required_structure_present: "Verses and chorus present", cites_approved_facts: "Uses approved facts",
  };

  const state = {
    keys: {}, role: "author", me: null, topics: [], topicId: null, topic: null,
    raps: [], rapId: null, rap: null, history: null, selected: new Set(), step: "topic", samples: null,
  };

  // ---------- helpers ----------
  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

  function el(tag, attrs = {}, ...children) {
    const node = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (v === null || v === undefined || v === false) continue;
      if (k === "class") node.className = v;
      else if (k === "text") node.textContent = v;
      else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
      else node.setAttribute(k, v === true ? "" : v);
    }
    for (const c of children.flat()) {
      if (c === null || c === undefined || c === false) continue;
      node.append(c instanceof Node ? c : document.createTextNode(String(c)));
    }
    return node;
  }
  const badge = (status, extra = "") => el("span", { class: `badge ${status} ${extra}`, text: STATUS_TEXT[status] || status });
  const fmtTime = (iso) => { try { return new Date(iso).toLocaleString(); } catch { return iso; } };

  function store(key, value) { try { localStorage.setItem(key, JSON.stringify(value)); } catch { /* storage unavailable */ } }
  function load(key, fallback) { try { const v = localStorage.getItem(key); return v ? JSON.parse(v) : fallback; } catch { return fallback; } }

  let toastTimer;
  function toast(msg, isError = false) {
    const t = $("#toast");
    t.textContent = msg;
    t.classList.toggle("err", isError);
    t.classList.add("show");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => t.classList.remove("show"), isError ? 7000 : 3500);
  }

  async function busy(button, fn) {
    if (button) { button.disabled = true; button.classList.add("busy"); }
    try { return await fn(); }
    catch (err) { toast(err.message, true); return undefined; }
    finally { if (button) { button.disabled = false; button.classList.remove("busy"); } }
  }

  class ApiError extends Error {
    constructor(message, status, details) { super(message); this.status = status; this.details = details || {}; }
  }

  async function api(method, path, body) {
    const key = state.keys[state.role];
    const headers = { Accept: "application/json" };
    if (key) headers["X-API-Key"] = key;
    if (body !== undefined) headers["Content-Type"] = "application/json";
    const res = await fetch(path, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      const e = data.error || {};
      let msg = e.message || `Request failed (${res.status})`;
      if (res.status === 401) msg = `Not signed in as ${ROLES[state.role]}: add a valid key under "API keys".`;
      if (e.details && Array.isArray(e.details.errors) && e.details.errors.length) {
        msg += ": " + e.details.errors.map((x) => `${x.loc.slice(1).join(".") || "body"} ${x.msg}`).join("; ");
      }
      throw new ApiError(msg, res.status, e.details);
    }
    return data;
  }

  // ---------- session ----------
  async function refreshMe() {
    const who = $("#whoami");
    if (!state.keys[state.role]) {
      state.me = null;
      who.textContent = "No key for this role";
      who.className = "whoami err";
      applyRoleVisibility();
      return;
    }
    try {
      state.me = await api("GET", "/api/me");
      who.textContent = `Signed in as ${state.me.name}`;
      who.className = "whoami ok";
    } catch (err) {
      state.me = null;
      who.textContent = err.status === 401 ? "Key not recognised" : "Server not reachable";
      who.className = "whoami err";
    }
    applyRoleVisibility();
  }

  function can(role) {
    const r = state.me ? state.me.role : null;
    if (role === "author") return r === "author" || r === "admin";
    return r === role;
  }

  function applyRoleVisibility() {
    $$("[data-needs]").forEach((node) => node.classList.toggle("hidden", !can(node.dataset.needs)));
  }

  function openKeys() {
    for (const role of Object.keys(ROLES)) $(`#key-${role}`).value = state.keys[role] || "";
    $("#keys-dialog").showModal();
  }

  // ---------- navigation ----------
  function showStep(step) {
    state.step = step;
    $$(".step").forEach((b) => {
      const on = b.dataset.step === step;
      b.classList.toggle("active", on);
      if (on) b.setAttribute("aria-current", "step"); else b.removeAttribute("aria-current");
    });
    for (const s of ["topic", "facts", "raps", "review"]) $(`#step-${s}`).classList.toggle("hidden", s !== step);
    store("medkids.step", step);
  }

  // ---------- topics ----------
  async function loadTopics(selectId) {
    if (!state.me) { renderTopicSelect(); renderTopic(); return; }
    try { state.topics = await api("GET", "/api/topics"); }
    catch (err) { toast(err.message, true); state.topics = []; }
    const wanted = selectId ?? state.topicId ?? load("medkids.topic", null);
    state.topicId = state.topics.some((t) => t.id === wanted) ? wanted : (state.topics[0] ? state.topics[0].id : null);
    renderTopicSelect();
    await loadTopic();
  }

  function renderTopicSelect() {
    const sel = $("#topic-select");
    sel.replaceChildren();
    if (!state.topics.length) sel.append(el("option", { value: "", text: state.me ? "No topics yet: create one" : "Add an API key to begin" }));
    for (const t of state.topics) {
      sel.append(el("option", { value: t.id, text: `${t.title} (ages ${t.target_age_min}–${t.target_age_max})${t.is_demo_data ? " demo" : ""}` }));
    }
    if (state.topicId) sel.value = String(state.topicId);
  }

  async function loadTopic() {
    store("medkids.topic", state.topicId);
    if (!state.topicId) { state.topic = null; renderTopic(); renderFacts(); state.raps = []; renderRaps(); return; }
    try { state.topic = await api("GET", `/api/topics/${state.topicId}`); }
    catch (err) { toast(err.message, true); state.topic = null; }
    renderTopic();
    renderFacts();
    await loadRaps();
  }

  function renderTopic() {
    const view = $("#topic-view");
    view.replaceChildren();
    const meta = $("#topic-meta");
    const t = state.topic;
    if (!t) {
      meta.textContent = "";
      view.append(el("div", { class: "empty" }, state.me
        ? "No topic selected. Create one to get started."
        : "Add your API keys (top right) to connect to the pipeline."));
      return;
    }
    const approved = t.facts.filter((f) => f.status === "approved").length;
    meta.textContent = `${t.category}, ${approved} approved fact${approved === 1 ? "" : "s"}`;
    const left = el("div", {},
      t.is_demo_data ? el("p", { class: "demo-note", text: "Demonstration data. These starter facts still need a real medical review." }) : null,
      el("h3", { text: t.title }),
      el("p", { class: "lede", text: `${t.category}. For ages ${t.target_age_min} to ${t.target_age_max}. Created by ${t.created_by}.` }),
      el("h3", { text: "Source text", style: "font-size:1.05rem;margin:1rem 0 .5rem" }),
      el("div", { class: "source-text", text: (t.sources[0] || {}).content || "(source text stored on the server)" }),
      ...t.content_flags.map((f) => el("p", { class: "flag-note", text: `Possible instructions hidden in the source: "${f.excerpt}". Treated as plain text; facts from it are marked uncertain.` })),
    );
    const right = el("div", {},
      el("h3", { text: "Learning goals", style: "font-size:1.05rem;margin-bottom:.5rem" }),
      el("ol", { class: "plain-list" }, t.objectives.map((o) => el("li", { text: o.text }))),
      el("h3", { text: "Misconceptions blocked", style: "font-size:1.05rem;margin:1.2rem 0 .5rem" }),
      t.forbidden_claims.length
        ? el("ul", { class: "plain-list" }, t.forbidden_claims.map((c) => el("li", { text: c.claim })))
        : el("p", { class: "hint", text: "None listed. Common misconceptions are still checked." }),
      el("h3", { text: "Science words allowed", style: "font-size:1.05rem;margin:1.2rem 0 .5rem" }),
      el("p", { text: t.required_vocabulary.join(", ") || "None" }),
      el("div", { class: "form-actions", style: "margin-top:1.4rem" },
        el("button", { class: "btn primary", type: "button", onclick: () => showStep("facts"), text: "Go to facts" })),
    );
    view.append(el("div", { class: "topic-summary" }, left, right));
  }

  async function openTopicForm() {
    $("#topic-form").classList.remove("hidden");
    $("#topic-view").classList.add("hidden");
    $("#topic-form [name=topic]").focus();
    if (!state.samples) {
      try { state.samples = await api("GET", "/api/samples"); } catch { state.samples = []; }
    }
    const box = $("#sample-buttons");
    box.replaceChildren(...state.samples.map((s) => el("button", { class: "chip", type: "button", text: s.topic, onclick: () => fillTopicForm(s) })));
  }

  function closeTopicForm() {
    $("#topic-form").classList.add("hidden");
    $("#topic-view").classList.remove("hidden");
  }

  function fillTopicForm(s) {
    const f = $("#topic-form");
    f.topic.value = s.topic;
    f.category.value = s.category;
    f.target_age_min.value = s.target_age_min;
    f.target_age_max.value = s.target_age_max;
    f.educational_content.value = s.educational_content;
    f.learning_objectives.value = (s.learning_objectives || []).join("\n");
    f.forbidden_claims.value = (s.forbidden_claims || []).map((c) => c.claim).join("\n");
    f.required_vocabulary.value = (s.required_vocabulary || []).join(", ");
    f.source_is_approved.checked = !!s.source_is_approved;
    f.is_demo_data.checked = !!s.is_demo_data;
    f.dataset.sample = s.topic;
  }

  async function submitTopic(ev) {
    ev.preventDefault();
    const f = ev.target;
    const lines = (v) => v.split("\n").map((x) => x.trim()).filter(Boolean);
    const sample = (state.samples || []).find((s) => s.topic === f.dataset.sample && s.topic === f.topic.value.trim());
    const forbidden = lines(f.forbidden_claims.value).map((claim) => {
      const fromSample = sample && (sample.forbidden_claims || []).find((c) => c.claim === claim);
      return fromSample ? fromSample : { claim };
    });
    const body = {
      topic: f.topic.value.trim(),
      category: f.category.value,
      target_age_min: Number(f.target_age_min.value),
      target_age_max: Number(f.target_age_max.value),
      educational_content: f.educational_content.value,
      learning_objectives: lines(f.learning_objectives.value),
      forbidden_claims: forbidden,
      required_vocabulary: f.required_vocabulary.value.split(",").map((x) => x.trim()).filter(Boolean),
      source_is_approved: f.source_is_approved.checked,
      is_demo_data: f.is_demo_data.checked,
    };
    if (sample) Object.assign(body, { source_title: sample.source_title, source_version: sample.source_version, primary_objective: sample.primary_objective });
    await busy(f.querySelector("[type=submit]"), async () => {
      const t = await api("POST", "/api/topics", body);
      toast(`Topic "${t.title}" created.`);
      f.reset(); delete f.dataset.sample;
      closeTopicForm();
      await loadTopics(t.id);
    });
  }

  // ---------- facts ----------
  function renderFacts() {
    const list = $("#facts-list");
    list.replaceChildren();
    const t = state.topic;
    if (!t) { list.append(el("div", { class: "empty", text: "Choose a topic first." })); return; }
    if (!t.facts.length) {
      list.append(el("div", { class: "empty" }, can("author")
        ? "No facts yet. Use \"Propose facts from source\" to draft candidates."
        : "No facts yet. An author needs to propose them first."));
    }
    const reviewer = can("medical_reviewer");
    const order = { candidate: 0, uncertain: 1, approved: 2, rejected: 3 };
    for (const f of [...t.facts].sort((a, b) => order[a.status] - order[b.status])) {
      const selectable = reviewer && (f.status === "candidate" || f.status === "approved");
      const box = selectable
        ? el("input", { type: "checkbox", value: f.fact_key, "aria-label": `Select ${f.fact_key}` })
        : el("span", { style: "width:18px" });
      list.append(el("div", { class: `fact ${f.status}` },
        box,
        el("div", {},
          el("div", { class: "fact-text", text: f.text }),
          f.evidence_quote ? el("div", { class: "fact-quote", text: `Source: "${f.evidence_quote}"` }) : null,
          el("div", { class: "fact-meta", text:
            [f.fact_key, f.status === "uncertain" && f.uncertainty_reason ? `Why uncertain: ${f.uncertainty_reason}` : null,
             f.reviewed_by ? `Reviewed by ${f.reviewed_by}` : `Proposed by ${f.proposed_by}`].filter(Boolean).join(". ") })),
        badge(f.status)));
    }
    $("#fact-review-bar").classList.toggle("hidden", !reviewer || !t.facts.some((f) => f.status === "candidate" || f.status === "approved"));
  }

  async function extractFacts(btn) {
    if (!state.topic) return;
    await busy(btn, async () => {
      const r = await api("POST", `/api/topics/${state.topic.id}/facts/extract`);
      toast(`${r.candidates.length} fact${r.candidates.length === 1 ? "" : "s"} proposed, ${r.uncertain.length} marked uncertain. A medical reviewer can now approve them.`);
      await loadTopic();
    });
  }

  async function reviewFacts(btn, approve) {
    const ids = $$("#facts-list input[type=checkbox]:checked").map((c) => c.value);
    if (!ids.length) { toast("Select at least one fact first.", true); return; }
    const body = approve ? { approve_fact_ids: ids } : { reject_fact_ids: ids };
    const note = $("#fact-note").value.trim();
    if (note) body.note = note;
    await busy(btn, async () => {
      const r = await api("POST", `/api/topics/${state.topic.id}/facts/approve`, body);
      toast(approve ? `Approved ${r.approved.length}. The topic now has ${r.approved_fact_count} approved facts.` : `Rejected ${r.rejected.length}.`);
      $("#fact-note").value = "";
      await loadTopic();
    });
  }

  // ---------- raps ----------
  async function loadRaps() {
    if (!state.topic) { state.raps = []; renderRaps(); return; }
    try {
      const summaries = await api("GET", `/api/raps?topic_id=${state.topic.id}&limit=20`);
      state.raps = await Promise.all(summaries.map((s) => api("GET", `/api/raps/${s.id}`)));
    } catch (err) { toast(err.message, true); state.raps = []; }
    renderRaps();
    if (state.rapId && state.raps.some((r) => r.id === state.rapId)) await openRap(state.rapId, false);
    else { state.rapId = null; renderRapDetail(); }
  }

  function renderRaps() {
    const list = $("#rap-list");
    list.replaceChildren();
    const approvedFacts = state.topic ? state.topic.facts.filter((f) => f.status === "approved").length : 0;
    if (!state.raps.length) {
      list.append(el("div", { class: "empty", text: !state.topic ? "Choose a topic first."
        : approvedFacts ? "No raps yet for this topic." : "Approve some facts in step 2 before writing a rap." }));
      return;
    }
    for (const r of state.raps) {
      const score = r.evaluation ? Math.round(r.evaluation.total_score) : null;
      list.append(el("button", { class: "rap-row", type: "button", onclick: () => openRap(r.id) },
        el("span", {}, el("strong", { text: r.title }),
          el("span", { class: "sub", text: `Rap ${r.id}, version ${r.current_version}, ages ${r.age_band}, ${r.style.replaceAll("_", " ")}${r.needs_human_intervention ? ". Needs a person to fix" : ""}` })),
        el("span", { class: "score-pill", text: score === null ? "Not scored" : `${score}/100` }),
        badge(r.status, r.status === "approved" ? "approved-rap" : "")));
    }
  }

  async function generate(ev) {
    ev.preventDefault();
    if (!state.topic) { toast("Choose a topic first.", true); return; }
    const f = ev.target;
    const body = {
      topic_id: state.topic.id, style: f.style.value, num_verses: Number(f.num_verses.value),
      num_candidates: Number(f.num_candidates.value), include_intro: f.include_intro.checked, include_outro: f.include_outro.checked,
    };
    if (f.age_band.value) body.age_band = f.age_band.value;
    if (f.auto_revise) body.auto_revise = f.auto_revise.checked;
    if (f.demo_inject_error && f.demo_inject_error.checked) body.demo_inject_error = true; // demo page only
    await busy(f.querySelector("[type=submit]"), async () => {
      const r = await api("POST", "/api/raps/generate", body);
      const first = r.raps[0];
      const fixes = first.auto_revision_attempts;
      toast(`Wrote ${r.raps.length} rap${r.raps.length > 1 ? "s" : ""}. ` + (fixes
        ? `Checks flagged problems and ${fixes} automatic fix${fixes > 1 ? "es" : ""} ran (now version ${first.current_version}). `
        : "") + `Status: ${STATUS_TEXT[first.status]}.`);
      state.rapId = first.id;
      await loadRaps();
      showStep("review");
    });
  }

  // ---------- rap detail ----------
  async function openRap(id, jump = true) {
    try {
      const [rap, history] = await Promise.all([api("GET", `/api/raps/${id}`), api("GET", `/api/raps/${id}/history`)]);
      state.rap = rap; state.history = history;
      if (state.rapId !== id) state.selected.clear();
      state.rapId = id;
    } catch (err) { toast(err.message, true); return; }
    renderRapDetail();
    if (jump) showStep("review");
  }

  function lineMarks(rap) {
    const marks = {};
    const rank = { ok: 1, review: 2, bad: 3 };
    const put = (lineId, level, note) => {
      if (!lineId) return;
      const m = marks[lineId] || (marks[lineId] = { level: null, notes: [], facts: [] });
      if (!m.level || rank[level] > rank[m.level]) m.level = level;
      if (note) m.notes.push(note);
    };
    const med = rap.medical_validation;
    if (med) {
      for (const c of med.claims) {
        if (c.status === "supported") { put(c.line_id, "ok"); if (c.source_fact_id) marks[c.line_id].facts.push(c.source_fact_id); }
        else put(c.line_id, c.severity === "minor" ? "review" : "bad", `${STATUS_TEXT[c.status]}: ${c.rationale || c.claim}`);
      }
    }
    const saf = rap.safety_validation;
    if (saf) for (const f of saf.findings) put(f.line_id, f.severity === "minor" ? "review" : "bad", f.explanation);
    return marks;
  }

  function renderRapDetail() {
    const rap = state.rap && state.rap.id === state.rapId ? state.rap : null;
    $("#rap-empty").classList.toggle("hidden", !!rap);
    $("#rap-detail").classList.toggle("hidden", !rap);
    renderRapActions(rap);
    if (!rap) return;

    $("#rap-title").textContent = rap.title;
    $("#rap-sub").replaceChildren(
      badge(rap.status, rap.status === "approved" ? "approved-rap" : ""),
      ` Version ${rap.current_version}, ages ${rap.age_band}, ${rap.style.replaceAll("_", " ")}${rap.is_demo_data ? ", demo data" : ""}`);

    const factText = Object.fromEntries((state.topic ? state.topic.facts : []).map((f) => [f.fact_key, f.text]));
    const marks = lineMarks(rap);
    const current = state.history ? state.history.versions.find((v) => v.version_number === rap.current_version) : null;
    const changed = new Set(current ? current.changed_line_ids : []);
    const arrangement = rap.lyrics.arrangement || [];
    const box = $("#lyrics");
    box.replaceChildren();
    for (const section of rap.lyrics.sections) {
      const times = arrangement.filter((s) => s === section.section).length;
      const name = section.section === "chorus" ? "Chorus" : section.section.replace("verse_", "Verse ").replace("intro", "Intro").replace("outro", "Outro");
      box.append(el("div", { class: `section-label ${section.section}` }, name,
        times > 1 ? el("span", { class: "rep", text: `sung ${times} times` }) : null));
      for (const line of section.lines) {
        const m = marks[line.line_id] || { level: null, notes: [], facts: [] };
        const facts = [...new Set([...m.facts, ...line.fact_ids])];
        let cite = null;
        if (m.notes.length) cite = el("span", { class: "cite", text: m.notes.join(" ") });
        else if (facts.length) cite = el("span", { class: "cite" }, ...facts.flatMap((id, i) => [i ? "; " : "", el("b", { text: id }), factText[id] ? ` ${factText[id]}` : ""]));
        const btn = el("button", {
          type: "button",
          class: `lyric ${m.level || ""} ${section.section === "chorus" ? "chorus" : ""} ${changed.has(line.line_id) ? "changed" : ""} ${state.selected.has(line.line_id) ? "selected" : ""}`,
          "aria-pressed": state.selected.has(line.line_id) ? "true" : "false",
          "data-line": line.line_id,
          onclick: () => toggleLine(line.line_id),
        }, el("span", { class: "lid", text: line.line_id }), el("span", { class: "bar" }), el("span", { class: "txt" }, line.text, cite));
        box.append(btn);
      }
    }
    renderChecks(rap);
  }

  function toggleLine(id) {
    if (state.selected.has(id)) state.selected.delete(id); else state.selected.add(id);
    $$(`#lyrics [data-line="${CSS.escape(id)}"]`).forEach((b) => {
      b.classList.toggle("selected", state.selected.has(id));
      b.setAttribute("aria-pressed", state.selected.has(id) ? "true" : "false");
    });
    renderReviewBox(state.rap);
  }

  function renderChecks(rap) {
    // Gates
    const ev = rap.evaluation;
    const gate = $("#gate-box");
    gate.replaceChildren(el("p", { class: "box-title", text: "Must-pass checks" }));
    if (ev) {
      gate.append(el("ul", { class: "gate-list" }, Object.entries(ev.hard_gates).map(([k, v]) =>
        el("li", { class: v ? "yes" : "no", text: GATE_NAMES[k] || k }))));
    } else gate.append(el("p", { class: "hint", text: "Not checked yet. Run the checks to see results." }));

    // Scores
    const sc = $("#score-box");
    sc.replaceChildren();
    if (ev) {
      sc.append(el("div", { class: "total" }, el("b", { text: Math.round(ev.total_score) }),
        el("span", { text: `out of 100. ${ev.total_score >= ev.threshold ? "Above" : "Below"} the ${ev.threshold} target` })));
      for (const [k, s] of Object.entries(ev.scores)) {
        sc.append(el("div", { class: "meter", title: s.explanation },
          el("span", { text: SCORE_NAMES[k] || k }),
          el("span", { class: "meter-track" }, el("span", { class: `meter-fill ${s.score < 60 ? "low" : ""}`, style: `width:${Math.max(0, Math.min(100, s.score))}%; display:block` })),
          el("output", { text: Math.round(s.score) })));
      }
      if (ev.recommendations.length) sc.append(el("ul", { class: "plain-list note-small" }, ev.recommendations.map((r) => el("li", { text: r }))));
      sc.append(el("p", { class: "note-small", text: "Scores are guides for reviewers. A good score never approves a rap." }));
    } else sc.classList.add("hidden");
    sc.classList.toggle("hidden", !ev);

    // Medical
    const med = rap.medical_validation;
    const mBox = $("#medical-box > div");
    mBox.replaceChildren();
    if (med) {
      mBox.append(el("div", { class: "row", style: "margin-bottom:.6rem;display:flex;gap:.5rem;align-items:center" }, badge(med.status),
        el("span", { class: "hint", text: `${med.summary.supported || 0} supported, ${(med.summary.contradicted || 0) + (med.summary.unsupported || 0)} blocked, ${(med.summary.ambiguous || 0) + (med.summary.outside_scope || 0)} to check` })));
      mBox.append(el("ul", { class: "find-list" }, med.claims.map((c) => el("li", {},
        el("div", { class: "row" }, badge(c.status), c.line_id ? el("button", { class: "linkish", type: "button", text: c.line_id, onclick: () => focusLine(c.line_id) }) : null),
        el("span", { text: c.claim }),
        c.status !== "supported" && c.rationale ? el("span", { class: "why", text: c.rationale }) : null,
        c.source_fact_id ? el("span", { class: "why", text: `Backed by ${c.source_fact_id}` }) : null))));
      mBox.append(el("p", { class: "note-small", text: med.disclaimer }));
    } else mBox.append(el("p", { class: "hint", text: "Not checked yet." }));

    // Safety
    const saf = rap.safety_validation;
    const sBox = $("#safety-box > div");
    sBox.replaceChildren();
    if (saf) {
      sBox.append(el("div", { style: "margin-bottom:.6rem" }, badge(saf.status)));
      if (!saf.findings.length) sBox.append(el("p", { class: "hint", text: "No problems found." }));
      sBox.append(el("ul", { class: "find-list" }, saf.findings.map((f) => el("li", {},
        el("div", { class: "row" }, badge(f.severity), el("span", { text: f.category.replaceAll("_", " ") }),
          f.line_id ? el("button", { class: "linkish", type: "button", text: f.line_id, onclick: () => focusLine(f.line_id) }) : null),
        el("span", { class: "why", text: f.explanation }),
        f.suggestion ? el("span", { class: "why", text: `Try: ${f.suggestion}` }) : null))));
      $("#safety-box").open = saf.status !== "passed";
    } else sBox.append(el("p", { class: "hint", text: "Not checked yet." }));

    renderReviewBox(rap);
    renderHistory();
  }

  function focusLine(id) {
    const b = $(`#lyrics [data-line="${CSS.escape(id)}"]`);
    if (b) { b.scrollIntoView({ block: "center", behavior: "smooth" }); b.focus(); }
  }

  function renderReviewBox(rap) {
    const box = $("#review-box");
    box.replaceChildren(el("p", { class: "box-title", text: "Human review" }));
    if (!rap) return;
    const needed = rap.approvals_required;
    const done = new Set(rap.approvals_for_current_version);
    box.append(el("div", { class: "approvals" }, needed.map((r) =>
      el("span", { class: `approval ${done.has(r) ? "done" : ""}`, text: `${ROLES[r] || r}${done.has(r) ? " approved" : ""}` }))));

    const terminal = rap.status === "approved" || rap.status === "rejected";
    const reviewer = can("medical_reviewer") || can("educator");
    if (rap.status === "approved") {
      box.append(el("p", { text: "Approved by both reviewers. It can be exported." }));
      return;
    }
    if (rap.status === "rejected") { box.append(el("p", { class: "hint", text: "This rap was rejected." })); return; }
    if (!reviewer) {
      box.append(el("p", { class: "hint", text: rap.status === "pending_review"
        ? "Switch to Medical reviewer or Educator (top right) to review."
        : "Reviewers can approve once the rap passes its checks and is waiting for review." }));
      return;
    }
    if (terminal) return;

    const sev = el("select", { "aria-label": "How serious are the selected lines?" },
      el("option", { value: "major", text: "Must fix" }), el("option", { value: "minor", text: "Nice to fix" }),
      el("option", { value: "critical", text: "Critical (blocks approval)" }));
    const feedback = el("textarea", { rows: "3", maxlength: "2000", placeholder: "What should change, or why it is good to go." });
    box.append(el("label", { class: "field" }, el("span", { text: "Your feedback" }), feedback));
    box.append(el("div", { class: "issue-chips" }, state.selected.size
      ? [...state.selected].map((id) => el("span", { class: "issue-chip", text: id }))
      : el("span", { class: "hint", text: "No lines selected. Click lines on the sheet to point at them." })));
    if (state.selected.size) box.append(el("label", { class: "field inline" }, el("span", { text: "Selected lines are" }), sev));

    const issues = () => [...state.selected].map((line_id) => ({ line_id, severity: sev.value, reason: feedback.value.trim() || "Flagged by reviewer" }));
    const send = (decision) => async (e) => {
      const body = { decision };
      if (feedback.value.trim()) body.feedback = feedback.value.trim();
      if (decision !== "approve" && state.selected.size) body.issues = issues();
      if (decision !== "approve" && !body.feedback && !body.issues) { toast("Add feedback or select lines first.", true); feedback.focus(); return; }
      await busy(e.currentTarget, async () => {
        try {
          const r = await api("POST", `/api/raps/${rap.id}/review`, body);
          const status = r.rap.status;
          toast(decision === "approve" ? (status === "approved" ? "Approved. Both reviews are in." : "Approval recorded. Waiting for the other reviewer.")
            : decision === "request_revision" ? "Changes requested. The author can now fix the flagged lines."
            : decision === "reject" ? "Rap rejected." : "Comment added.");
          state.selected.clear();
          await loadRaps();
          await openRap(rap.id, false);
        } catch (err) {
          if (err.details && Array.isArray(err.details.blockers)) toast("Can't approve yet: " + err.details.blockers.join("; "), true);
          else throw err;
        }
      });
    };
    const pending = rap.status === "pending_review";
    box.append(el("div", { class: "review-actions" },
      el("button", { class: "btn primary", type: "button", disabled: !pending, onclick: send("approve"), text: "Approve" }),
      el("button", { class: "btn", type: "button", disabled: !pending, onclick: send("request_revision"), text: "Request changes" }),
      el("button", { class: "btn", type: "button", onclick: send("comment"), text: "Add comment" }),
      el("button", { class: "btn danger", type: "button", onclick: send("reject"), text: "Reject" })));
    if (!pending) box.append(el("p", { class: "note-small", text: "Approve and request changes are available once the rap is waiting for review." }));
  }

  function renderRapActions(rap) {
    const box = $("#rap-actions");
    box.replaceChildren();
    if (!rap) return;
    if (can("author")) {
      if (["draft", "validation_failed", "pending_review"].includes(rap.status)) {
        box.append(el("button", { class: "btn", type: "button", text: "Run checks again", onclick: (e) => busy(e.currentTarget, async () => {
          await api("POST", `/api/raps/${rap.id}/validate`);
          const r = await api("POST", `/api/raps/${rap.id}/evaluate`);
          toast(`Checks done. Status: ${STATUS_TEXT[r.rap.status]}.`);
          await loadRaps(); await openRap(rap.id, false);
        }) }));
      }
      if (rap.status === "validation_failed" || rap.status === "revision_requested") {
        box.append(el("button", { class: "btn primary", type: "button", text: "Fix flagged lines", onclick: (e) => busy(e.currentTarget, async () => {
          const r = await api("POST", `/api/raps/${rap.id}/revise`, { auto_validate: true });
          toast(r.changed ? `Revised to version ${r.rap.current_version}. Status: ${STATUS_TEXT[r.rap.status]}.` : "No lines could be improved automatically.");
          await loadRaps(); await openRap(rap.id, false);
        }) }));
      }
    }
    if (rap.status === "approved") {
      box.append(el("button", { class: "btn primary", type: "button", text: "Download approved lyrics", onclick: (e) => busy(e.currentTarget, async () => {
        const data = await api("GET", `/api/raps/${rap.id}/export`);
        if (window.MEDKIDS_DEMO && window.MEDKIDS_DEMO.showExport) { window.MEDKIDS_DEMO.showExport(data); return; }
        const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
        const a = el("a", { href: URL.createObjectURL(blob), download: `${data.topic_slug}-rap${data.rap_id}-v${data.version}.json` });
        document.body.append(a); a.click(); a.remove();
      }) }));
    }
  }

  function renderHistory() {
    const box = $("#history-box > div");
    box.replaceChildren();
    const h = state.history;
    if (!h) return;
    const items = [
      ...h.events.map((e) => ({ t: e.created_at, text: `${e.from_status ? `${STATUS_TEXT[e.from_status] || e.from_status} to ` : ""}${STATUS_TEXT[e.to_status] || e.to_status} (v${e.version_number}) by ${e.actor}${e.reason ? `: ${e.reason}` : ""}` })),
      ...h.reviews.map((r) => ({ t: r.created_at, text: `${r.reviewer_name} (${ROLES[r.reviewer_role] || r.reviewer_role}) chose ${r.decision.replaceAll("_", " ")} on v${r.rap_version_number}${r.feedback ? `: "${r.feedback}"` : ""}` })),
    ].sort((a, b) => String(a.t).localeCompare(String(b.t)));
    box.append(el("ol", { class: "timeline" }, items.map((i) => el("li", {}, el("time", { text: fmtTime(i.t) }), i.text))));
    const v = h.versions[h.versions.length - 1];
    if (v) box.append(el("p", { class: "note-small", text: `Written by ${v.provider} (${v.model}) using prompt ${v.prompt_name} v${v.prompt_version}.` }));
  }

  // ---------- wiring ----------
  async function init() {
    state.keys = load("medkids.keys", window.MEDKIDS_DEMO ? { ...window.MEDKIDS_DEMO.keys } : {});
    state.role = load("medkids.role", "author");
    $("#role-select").value = state.role;

    $("#role-select").addEventListener("change", async (e) => {
      state.role = e.target.value; store("medkids.role", state.role);
      await refreshMe();
      renderFacts(); renderRapDetail();
      if (!state.topics.length) await loadTopics();
    });
    $("#keys-btn").addEventListener("click", openKeys);
    $("#keys-dialog").addEventListener("close", async () => {
      if ($("#keys-dialog").returnValue !== "save") return;
      for (const role of Object.keys(ROLES)) {
        const v = $(`#key-${role}`).value.trim();
        if (v) state.keys[role] = v; else delete state.keys[role];
      }
      store("medkids.keys", state.keys);
      await refreshMe();
      await loadTopics();
      toast("Keys saved in this browser.");
    });
    $$(".step").forEach((b) => b.addEventListener("click", () => showStep(b.dataset.step)));
    $("#topic-select").addEventListener("change", async (e) => {
      state.topicId = e.target.value ? Number(e.target.value) : null; state.rapId = null; await loadTopic();
    });
    $("#new-topic-btn").addEventListener("click", openTopicForm);
    $("#cancel-topic").addEventListener("click", closeTopicForm);
    $("#topic-form").addEventListener("submit", submitTopic);
    $("#extract-btn").addEventListener("click", (e) => extractFacts(e.currentTarget));
    $("#approve-facts").addEventListener("click", (e) => reviewFacts(e.currentTarget, true));
    $("#reject-facts").addEventListener("click", (e) => reviewFacts(e.currentTarget, false));
    $("#gen-form").addEventListener("submit", generate);

    showStep(load("medkids.step", "topic"));
    await refreshMe();
    await loadTopics();
    if (!Object.keys(state.keys).length && !window.MEDKIDS_DEMO) openKeys();
  }

  document.addEventListener("DOMContentLoaded", init);
})();
