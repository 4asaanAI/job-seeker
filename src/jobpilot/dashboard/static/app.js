// JobPilot dashboard. Every piece of job text is inserted as text (never as HTML): job posts are written by strangers.
"use strict";

const COLUMNS = [["match", "Matches"], ["started", "Started"], ["applied", "Applied"], ["interview", "Interview"],
                 ["rejected", "Closed"]];
let state = { board: null, view: "board", openJob: null, filters: null, assist: null };

function h(tag, attrs, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (k === "class") el.className = v;
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat(Infinity)) {
    if (c === null || c === undefined || c === false) continue;
    el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return el;
}

async function api(path, body) {
  const opts = body === undefined ? {} : {
    method: "POST", headers: { "Content-Type": "application/json", "X-JobPilot": "1" }, body: JSON.stringify(body) };
  const res = await fetch(path, { credentials: "same-origin", ...opts });
  if (res.status === 401) { location.href = "/login"; throw new Error("log in"); }
  return res.json();
}

function toast(text) {
  const t = document.getElementById("toast");
  t.textContent = text; t.hidden = false;
  clearTimeout(toast.timer); toast.timer = setTimeout(() => { t.hidden = true; }, 4000);
}

function report(out, ok) {
  if (out && out.ok) toast(ok || "Done");
  else toast((out && out.error) ? `Not done: ${out.error}` : "Not done");
  return out && out.ok;
}

// ---- status line ----------------------------------------------------------------------------------------------------

function renderStatus(s) {
  const el = document.getElementById("status");
  el.replaceChildren();
  if (!s || !s.ok) return;
  el.append(h("span", {}, `Last 7 days: ${s.found} found, ${s.scored} scored, ${s.applied} applied`));
  if (s.next_scheduled_refresh) el.append(h("span", {}, `Next refresh: ${s.next_scheduled_refresh.replace("T", " ")}`));
  if (s.answers_awaiting_formal_approval) el.append(h("span", { class: "warn" }, `${s.answers_awaiting_formal_approval} answer(s) go into forms as typed until you approve a formal version`));
  if (s.answers_saved_by_fo_7d) el.append(h("span", { class: "warn" }, `Fo saved ${s.answers_saved_by_fo_7d} answer(s) this week: check My answers`));
  if (s.in_flight) el.append(h("span", { class: "warn" }, `In flight: job ${s.in_flight}`));
  for (const w of s.warnings || []) el.append(h("span", { class: "warn" }, w));
  if ((s.errors_last_24h || []).length) el.append(h("span", { class: "bad" }, `${s.errors_last_24h.length} error(s) in the last day`));
}

// ---- the pipeline board -----------------------------------------------------------------------------------------------

function card(item, column) {
  const quick = column === "match"
    ? [h("button", { title: "Skip this job", onclick: (e) => { e.stopPropagation(); act(item.posting_id, "skip"); } }, "Skip"),
       h("button", { title: "Shortlist", onclick: (e) => { e.stopPropagation(); act(item.posting_id, "shortlist"); } }, "Keep")]
    : column === "applied"
      ? [h("button", { onclick: (e) => { e.stopPropagation(); act(item.posting_id, "interview"); } }, "Interview"),
         h("button", { onclick: (e) => { e.stopPropagation(); rejectWithReason(item.posting_id); } }, "Rejected")]
      : [];
  return h("div", { class: "card", tabindex: "0", role: "button", onclick: () => openJob(item.posting_id),
                    onkeydown: (e) => { if (e.key === "Enter") openJob(item.posting_id); } },
    h("div", { class: "t" }, item.title),
    h("div", { class: "c" }, item.company, item.location ? ` · ${item.location}` : ""),
    h("div", { class: "row" },
      item.score !== undefined && item.score !== null ? h("span", { class: "score" }, Math.round(item.score)) : null,
      item.status && column !== "match" ? h("span", { class: "badge" }, item.status) : null,
      item.needs_you ? h("span", { class: "badge warn" }, "needs you") : null,
      item.deadline_soon ? h("span", { class: "badge warn" }, "closes in 48h") : null,
      item.work_mode ? h("span", { class: "badge" }, item.work_mode) : null,
      fmtPay(item) ? h("span", { class: "badge" }, fmtPay(item)) : null,
      h("span", { class: "quick" }, quick)));
}

// ---- filters, sorting and the list view -------------------------------------------------------------------------------

const FILTER_DEFAULTS = { q: "", sort: "score", minScore: 0, modes: [], location: "", source: "", minSalary: "",
                          currency: "INR", salaryOnly: false, soon: false, posted: "", layout: "board" };
const SORTS = [["score", "Best match"], ["newest", "Newest first"], ["closes", "Closing soonest"],
               ["salary", "Highest salary (approx.)"], ["company", "Company A–Z"], ["title", "Title A–Z"]];
const ui = {};

function loadFilters() {
  try { return { ...FILTER_DEFAULTS, ...JSON.parse(localStorage.getItem("jp_filters") || "{}") }; }
  catch (e) { return { ...FILTER_DEFAULTS }; }
}
function saveFilters() { try { localStorage.setItem("jp_filters", JSON.stringify(state.filters)); } catch (e) { /* private window */ } }

const dateOf = (i) => i.posted_on || i.first_seen || "";
const salaryOf = (i) => (i.salary_max || i.salary_min || null);
const TO_INR = { INR: 1, USD: 83, EUR: 90, GBP: 105 };          // rough rates, only for ordering jobs by pay
const payInInr = (i) => (salaryOf(i) === null ? null : salaryOf(i) * (TO_INR[i.salary_currency] || 1));
const SORT_FN = {
  score: (a, b) => (b.score ?? -1) - (a.score ?? -1),
  newest: (a, b) => dateOf(b).localeCompare(dateOf(a)),
  closes: (a, b) => (a.closes ? (b.closes ? a.closes.localeCompare(b.closes) : -1) : (b.closes ? 1 : 0)),
  salary: (a, b) => (payInInr(b) ?? -1) - (payInInr(a) ?? -1),
  company: (a, b) => a.company.localeCompare(b.company),
  title: (a, b) => a.title.localeCompare(b.title),
};

function fmtPay(i) {
  const v = salaryOf(i);
  if (v === null) return "";
  const money = (x) => i.salary_currency === "INR" ? `${+(x / 100000).toFixed(1)} LPA` : `${i.salary_currency} ${Math.round(x / 1000)}k`;
  return i.salary_min && i.salary_max && i.salary_min !== i.salary_max ? `${money(i.salary_min)} – ${money(i.salary_max)}` : money(v);
}

function passes(i, f) {
  if (f.q && !`${i.title} ${i.company} ${i.location || ""}`.toLowerCase().includes(f.q.toLowerCase())) return false;
  if ((i.score ?? 0) < Number(f.minScore || 0)) return false;
  if (f.modes.length && !f.modes.includes(i.work_mode || "unknown")) return false;
  if (f.location && !(i.location || "").toLowerCase().includes(f.location.toLowerCase())) return false;
  if (f.source && !(i.sources || []).includes(f.source)) return false;
  const pay = salaryOf(i);
  if (f.salaryOnly && pay === null) return false;
  if (f.minSalary !== "" && pay !== null && i.salary_currency === f.currency && pay < Number(f.minSalary)) return false;
  if (f.soon && !i.deadline_soon) return false;
  if (f.posted && dateOf(i) && dateOf(i) < new Date(Date.now() - Number(f.posted) * 864e5).toISOString().slice(0, 10)) return false;
  return true;
}

function activeCount(f) {
  return ["q", "location", "source", "minSalary", "posted"].filter((k) => f[k] !== FILTER_DEFAULTS[k]).length
    + (Number(f.minScore) > 0 ? 1 : 0) + (f.modes.length ? 1 : 0) + (f.salaryOnly ? 1 : 0) + (f.soon ? 1 : 0);
}

function buildFilters() {
  const f = state.filters, root = document.getElementById("filters-body");
  const rerender = () => { saveFilters(); if (state.board) renderBoard(state.board); };
  const field = (label, el, wide) => h("div", { class: wide ? "field wide" : "field" }, h("label", {}, label), el);
  ui.q = h("input", { type: "search", placeholder: "Title, company or place", "aria-label": "Search",
                      oninput: (e) => { f.q = e.target.value; rerender(); } });
  ui.sort = h("select", { "aria-label": "Sort by", onchange: (e) => { f.sort = e.target.value; rerender(); } },
              SORTS.map(([k, l]) => h("option", { value: k }, l)));
  ui.minScore = h("input", { type: "number", min: "0", max: "100", "aria-label": "Lowest score",
                             oninput: (e) => { f.minScore = e.target.value === "" ? 0 : Number(e.target.value); rerender(); } });
  ui.location = h("input", { type: "text", list: "loc-options", placeholder: "e.g. Gurugram", "aria-label": "Place",
                             oninput: (e) => { f.location = e.target.value; rerender(); } });
  ui.locOptions = h("datalist", { id: "loc-options" });
  ui.source = h("select", { "aria-label": "Job board", onchange: (e) => { f.source = e.target.value; rerender(); } });
  ui.minSalary = h("input", { type: "number", min: "0", step: "100000", placeholder: "yearly", "aria-label": "Lowest salary",
                              oninput: (e) => { f.minSalary = e.target.value; rerender(); } });
  ui.currency = h("select", { "aria-label": "Currency", onchange: (e) => { f.currency = e.target.value; rerender(); } },
                  ["INR", "USD", "EUR", "GBP"].map((c) => h("option", { value: c }, c)));
  ui.posted = h("select", { "aria-label": "Posted within", onchange: (e) => { f.posted = e.target.value; rerender(); } },
                [["", "Any time"], ["2", "Last 2 days"], ["7", "Last week"], ["14", "Last 2 weeks"]].map(([v, l]) => h("option", { value: v }, l)));
  const check = (key, label) => {
    const box = h("input", { type: "checkbox", onchange: (e) => { f[key] = e.target.checked; rerender(); } });
    ui[key] = box; return h("label", { class: "check" }, box, ` ${label}`);
  };
  ui.modes = [["remote", "Remote"], ["hybrid", "Hybrid"], ["onsite", "On-site"], ["unknown", "Not stated"]].map(([m, l]) => {
    const box = h("input", { type: "checkbox", onchange: () => {
      f.modes = ui.modes.filter((x) => x.box.checked).map((x) => x.mode); rerender(); } });
    const wrap = h("label", { class: "check" }, box, ` ${l}`); wrap.box = box; wrap.mode = m; return wrap;
  });
  ui.layoutBoard = h("button", { class: "btn", onclick: () => { f.layout = "board"; rerender(); } }, "Board");
  ui.layoutList = h("button", { class: "btn", onclick: () => { f.layout = "list"; rerender(); } }, "List");
  const reset = h("button", { class: "btn", onclick: () => {
    Object.assign(f, { ...FILTER_DEFAULTS, layout: f.layout }); syncFilters(); rerender(); } }, "Reset filters");
  root.replaceChildren(
    field("Search", ui.q, true), field("Sort by", ui.sort), field("Lowest score", ui.minScore),
    field("Place contains", h("div", {}, ui.location, ui.locOptions)), field("Job board", ui.source),
    field("Work mode (none ticked = all)", h("div", { class: "checks" }, ui.modes)),
    field("Lowest salary", h("div", { class: "pair" }, ui.minSalary, ui.currency)),
    field("Posted", ui.posted),
    h("div", { class: "field checks" }, check("salaryOnly", "Only jobs that state a salary"), check("soon", "Closing within 48 hours")),
    h("div", { class: "field row" }, ui.layoutBoard, ui.layoutList, reset));
  syncFilters();
  document.getElementById("filters").open = window.innerWidth > 760;
}

function syncFilters() {
  const f = state.filters;
  ui.q.value = f.q; ui.sort.value = f.sort; ui.minScore.value = f.minScore || ""; ui.location.value = f.location;
  ui.source.value = f.source; ui.minSalary.value = f.minSalary; ui.currency.value = f.currency; ui.posted.value = f.posted;
  ui.salaryOnly.checked = f.salaryOnly; ui.soon.checked = f.soon;
  for (const w of ui.modes) w.box.checked = f.modes.includes(w.mode);
  ui.layoutBoard.classList.toggle("primary", f.layout === "board"); ui.layoutList.classList.toggle("primary", f.layout === "list");
}

function updateFilterOptions(all) {
  const places = [...new Set(all.map((i) => (i.location || "").trim()).filter(Boolean))].sort();
  ui.locOptions.replaceChildren(...places.slice(0, 200).map((p) => h("option", { value: p })));
  const sources = [...new Set(all.flatMap((i) => i.sources || []))].sort();
  ui.source.replaceChildren(h("option", { value: "" }, "All boards"), ...sources.map((s) => h("option", { value: s }, s)));
  ui.source.value = state.filters.source;
}

function renderTable(items) {
  const f = state.filters;
  const head = (label, sortKey) => h("th", sortKey ? { class: "sortable" + (f.sort === sortKey ? " on" : ""), tabindex: "0",
      onclick: () => { f.sort = sortKey; syncFilters(); saveFilters(); renderBoard(state.board); },
      onkeydown: (e) => { if (e.key === "Enter") { f.sort = sortKey; syncFilters(); saveFilters(); renderBoard(state.board); } } }
    : {}, label + (f.sort === sortKey ? " ▾" : ""));
  const rows = items.map((i) => h("tr", { tabindex: "0", onclick: () => openJob(i.posting_id),
      onkeydown: (e) => { if (e.key === "Enter") openJob(i.posting_id); } },
    h("td", { class: "score" }, Math.round(i.score ?? 0)), h("td", {}, i.title), h("td", {}, i.company),
    h("td", {}, i.location || ""), h("td", {}, i.work_mode || ""), h("td", {}, fmtPay(i)),
    h("td", {}, dateOf(i)), h("td", { class: i.deadline_soon ? "warn" : "" }, i.closes || ""),
    h("td", {}, h("span", { class: "quick" },
      h("button", { onclick: (e) => { e.stopPropagation(); act(i.posting_id, "skip"); } }, "Skip"),
      h("button", { onclick: (e) => { e.stopPropagation(); act(i.posting_id, "shortlist"); } }, "Keep")))));
  return h("table", { class: "table" }, h("thead", {}, h("tr", {}, head("Score", "score"), head("Title", "title"),
    head("Company", "company"), head("Place"), head("Mode"), head("Salary", "salary"), head("Posted", "newest"),
    head("Closes", "closes"), h("th", {}))), h("tbody", {}, rows));
}

function renderBoard(b) {
  const f = state.filters;
  const all = b.columns.match || [];
  const shown = all.filter((i) => passes(i, f)).sort(SORT_FN[f.sort] || SORT_FN.score);
  updateFilterOptions(all);
  const n = activeCount(f);
  document.getElementById("filter-count").textContent = `Showing ${shown.length} of ${all.length} matches`;
  document.getElementById("filter-active").textContent = n ? ` (${n} on)` : "";
  const boardEl = document.getElementById("board"), listEl = document.getElementById("list");
  boardEl.hidden = f.layout !== "board"; listEl.hidden = f.layout !== "list";
  syncFilters();
  const q = f.q.toLowerCase();
  const others = (key) => (b.columns[key] || []).filter((i) => !q || `${i.title} ${i.company}`.toLowerCase().includes(q));
  boardEl.replaceChildren(...COLUMNS.map(([key, label]) => {
    const items = key === "match" ? shown : others(key);
    const empty = key === "match" && all.length ? "No match fits these filters." : "Nothing here yet.";
    return h("section", { class: "column", "aria-label": label },
      h("h2", {}, h("span", {}, label), h("span", {}, key === "match" && shown.length !== all.length ? `${shown.length}/${all.length}` : items.length)),
      items.length ? items.map((i) => card(i, key)) : h("div", { class: "note" }, empty));
  }));
  listEl.replaceChildren(shown.length ? renderTable(shown) : h("div", { class: "note" }, "No match fits these filters."));
  const todo = (b.proposals || []).length + (b.retry_queue || []).length + (b.followups || []).length;
  const pill = document.getElementById("todo-count");
  pill.hidden = !todo; pill.textContent = todo;
}

// ---- the to-do view: proposals, failed submits, follow-ups -----------------------------------------------------------

function renderInbox(b) {
  const el = document.getElementById("view-inbox");
  const proposals = (b.proposals || []).map((p) => h("div", { class: "list-row" },
    h("div", { class: "grow" },
      h("div", {}, p.kind === "status" ? `${p.title}, ${p.company_name}: ${p.payload.from} → ${p.payload.to}`
                                       : `${p.title}, ${p.company_name}: calendar hold ${p.payload.starts_at.replace("T", " ")}`),
      h("div", { class: "note" }, `From an email: ${p.evidence}`)),
    h("button", { class: "btn primary", onclick: () => decide(p.id, true) }, "Yes"),
    h("button", { class: "btn", onclick: () => decide(p.id, false) }, "No")));
  const retries = (b.retry_queue || []).map((r) => h("div", { class: "list-row" },
    h("div", { class: "grow" }, h("div", {}, `${r.title}, ${r.company_name}`),
      h("div", { class: "note" }, `${r.state === "failed" ? "Failed twice; apply by hand" : "Submit failed"}: ${r.failure}`)),
    r.state === "parked" ? h("button", { class: "btn", onclick: () => openJob(r.posting_id) }, "Review retry") : null));
  const follow = (b.followups || []).map((f) => h("div", { class: "list-row" },
    h("div", { class: "grow" }, `${f.title}, ${f.company} (applied ${String(f.applied_at || "").slice(0, 10)})`),
    h("button", { class: "btn", onclick: () => draftFollowup(f.posting_id) }, "Draft follow-up")));
  el.replaceChildren(
    h("div", { class: "panel" }, h("h2", {}, "Confirm from email"),
      proposals.length ? proposals : h("div", { class: "note" }, "No suggestions waiting. Claude can read your email for "
        + "application replies and suggest status changes here.")),
    h("div", { class: "panel" }, h("h2", {}, "Failed submissions"),
      retries.length ? retries : h("div", { class: "note" }, "None.")),
    h("div", { class: "panel" }, h("h2", {}, "Follow-ups due"),
      follow.length ? follow : h("div", { class: "note" }, "None due.")));
}

async function decide(id, accept) {
  const out = await api(`/api/proposal/${id}`, { accept });
  if (report(out, accept ? "Recorded" : "Dismissed") && out.message) toast("Calendar hold ready: ask Claude or Fo to add it.");
  refresh();
}

async function draftFollowup(pid) {
  const out = await api(`/api/job/${pid}/document/followup`, {});
  if (report(out, "Follow-up drafted")) { await openJob(pid); showText("Follow-up draft (not sent)", out.draft); }
}

// ---- actions ----------------------------------------------------------------------------------------------------------

async function act(pid, action, extra) {
  report(await api(`/api/job/${pid}/action/${action}`, extra || {}), action === "skip" ? "Skipped" : "Saved");
  refresh();
  if (state.openJob === pid) openJob(pid);
}

function rejectWithReason(pid) {
  const reason = prompt("Rejected. The reason they gave, if any (it improves future matches):", "");
  if (reason === null) return;
  act(pid, "rejected", { reason });
}

// ---- the review drawer -------------------------------------------------------------------------------------------------

function kv(pairs) {
  return h("dl", { class: "kv" }, pairs.filter(([, v]) => v !== null && v !== undefined && v !== "")
    .map(([k, v]) => [h("dt", {}, k), h("dd", {}, v)]));
}

function showText(title, text) {
  const body = document.getElementById("drawer-body");
  body.prepend(h("div", { class: "panel" }, h("h2", {}, title), h("pre", { class: "text" }, text)));
}

async function openJob(pid) {
  state.openJob = pid; state.assist = null;
  const d = await api(`/api/job/${pid}`);
  const drawer = document.getElementById("drawer");
  drawer.hidden = false;
  document.getElementById("drawer-title").textContent = d.ok ? `${d.job.title} · ${d.job.company}` : "Job";
  const body = document.getElementById("drawer-body");
  if (!d.ok) { body.replaceChildren(h("p", {}, d.error || "Not available")); return; }
  const j = d.job, f = j.facts || {}, sc = j.score || {};
  const parts = [];
  parts.push(kv([["Score", sc.fit_score !== undefined ? `${Math.round(sc.fit_score)} (model ${Math.round(sc.model_score ?? sc.fit_score)})` : "not scored"],
                 ["Why", sc.reasoning], ["Adjusted", sc.adjust_notes], ["Location", j.location],
                 ["Work mode", f.work_mode], ["Salary", f.salary_min ? `${f.salary_currency} ${Math.round(f.salary_min).toLocaleString()}${f.salary_max ? " – " + Math.round(f.salary_max).toLocaleString() : ""}` : ""],
                 ["Deadline", f.valid_through], ["Posted", f.posted_on], ["Boards", (j.sources || []).join(", ")],
                 ["Applying", `${j.route}: ${j.route_note}`],
                 ["Application", d.application ? `${d.application.status}${d.application.applied_at ? " since " + d.application.applied_at.slice(0, 10) : ""}` : "none"]]));
  if (j.url && /^https?:\/\//i.test(j.url)) parts.push(h("p", {}, h("a", { href: j.url, target: "_blank", rel: "noopener noreferrer" }, "Open the posting")));

  // hand-off: Fo could not submit this one, so the final link and the values are here for the user
  if (d.handoff) {
    const hf = d.handoff;
    parts.push(h("div", { class: "panel" }, h("h2", {}, "Fo could not submit this one: finish it yourself"),
      h("p", { class: "note" }, "Fo filled in what it could and stopped. Open the final page, enter the values below, and press Submit yourself. Then tell JobPilot."),
      hf.final_link && /^https:\/\//i.test(hf.final_link)
        ? h("p", {}, h("a", { class: "btn primary", href: hf.final_link, target: "_blank", rel: "noopener noreferrer" }, "Open the final application page"))
        : h("p", { class: "check-bad" }, "No safe link was recorded; open the posting instead."),
      hf.kind ? h("p", { class: "note" }, `Stopped at: ${hf.kind}${hf.step ? " (" + hf.step + ")" : ""}`) : null,
      kv(hf.values.map((v) => [v.field, v.value])),
      (hf.filled || []).length ? h("p", { class: "note" }, `Fo had already filled: ${hf.filled.join(", ")}`) : null,
      h("p", { class: "note" }, "Resume: use “Make resume PDF” below, or paste the resume text shown in the review."),
      h("div", { class: "approve" },
        h("button", { class: "btn primary", onclick: () => act(pid, "applied", {}) }, "I submitted it"),
        h("button", { class: "btn danger", onclick: () => act(pid, "cancel", {}) }, "Cancel"))));
  }

  // review: prefilled fields, answers, the request, approve with code
  if (d.review) {
    parts.push(h("h3", {}, "Review before applying"));
    if (d.review.error) parts.push(h("p", { class: "check-bad" }, d.review.error));
    else {
      if (d.form) parts.push(kv([["Name", d.form.full_name], ["Email", d.form.email], ["Phone", d.form.phone],
                                 ["Address", d.form.address], ["Current job", d.form.current_employer ? `${d.form.current_title}, ${d.form.current_employer}` : "none (no job without an end date)"]]));
      const answers = Object.entries(d.review.answers || {});
      parts.push(h("h3", {}, "Your saved answers used"),
        answers.length ? kv(answers) : h("p", { class: "note" }, "None. Add them under My answers so forms prefill."));
      if ((d.review.job_terms_missing_from_resume || []).length)
        parts.push(h("p", { class: "note" }, `Terms in the job your resume does not mention (not added): ${d.review.job_terms_missing_from_resume.join(", ")}`));
      parts.push(h("h3", {}, "Resume text that will be used"), h("pre", { class: "text" }, d.review.resume_text || ""));
      if (d.review.letter_text) parts.push(h("h3", {}, "Cover letter (only where the form requires one)"), h("pre", { class: "text" }, d.review.letter_text));
      parts.push(h("h3", {}, "The request Fo will get"), h("pre", { class: "text" }, d.review.message || ""));
      const code = h("input", { type: "text", inputmode: "numeric", autocomplete: "one-time-code", maxlength: "6",
                                placeholder: "000000", "aria-label": "Authenticator code" });
      const go = h("button", { class: "btn primary", onclick: async () => {
        go.disabled = true;
        const out = await api(`/api/job/${pid}/approve`, { code: code.value.trim() });
        report(out, "Approved and sent to Fo"); go.disabled = false; refresh(); openJob(pid);
      } }, "Approve and apply");
      parts.push(h("div", { class: "approve" }, code, go),
        h("p", { class: "note" }, "Enter the 6-digit code from your authenticator app. Nothing is submitted without it; Fo stops for anything not in your resume or saved answers."));
    }
  }
  if (d.send) parts.push(h("p", { class: d.send.state === "failed" ? "check-bad" : "note" },
    `Fo: ${d.send.state}${d.send.reply ? " – " + d.send.reply : ""}${d.send.error ? " – " + d.send.error : ""}`));

  // outcome buttons for an application
  if (d.application && !["rejected", "withdrawn", "offer"].includes(d.application.status)) {
    const next = { started: ["applied", "blocked", "failed", "cancel"], applied: ["interview", "rejected", "withdrawn"],
                   interview: ["offer", "rejected", "withdrawn"] }[d.application.status] || [];
    parts.push(h("h3", {}, "Record what happened"), h("div", { class: "approve" }, next.map((a) =>
      h("button", { class: ["rejected", "cancel", "failed"].includes(a) ? "btn danger" : "btn", onclick: () =>
        a === "rejected" ? rejectWithReason(pid) : act(pid, a, a === "blocked" ? { kind: "manual" } : {}) },
        { applied: "It was submitted", blocked: "Stuck: I must finish it", failed: "The submit failed", cancel: "Cancel", interview: "Interview",
          rejected: "Rejected", withdrawn: "Withdrew", offer: "Offer" }[a]))));
  }
  const retry = (state.board && state.board.retry_queue || []).find((r) => r.posting_id === pid && r.state === "parked");
  if (retry) {
    const code = h("input", { type: "text", inputmode: "numeric", maxlength: "6", placeholder: "000000", "aria-label": "Authenticator code" });
    parts.push(h("h3", {}, "Retry once"), h("p", { class: "note" }, `It failed: ${retry.failure}. The exact same request can be sent once more.`),
      h("div", { class: "approve" }, code, h("button", { class: "btn primary", onclick: async () => {
        report(await api(`/api/job/${pid}/retry`, { code: code.value.trim() }), "Retry sent"); refresh(); openJob(pid); } }, "Retry once")));
  }

  // documents
  parts.push(h("h3", {}, "Documents"), h("div", { class: "approve" },
    h("button", { class: "btn", onclick: async () => { const o = await api(`/api/job/${pid}/document/pdf`, {});
      if (report(o, "PDF saved")) showText("PDF saved", o.path); } }, "Make resume PDF"),
    h("button", { class: "btn", onclick: async () => { toast("Writing a draft…"); const o = await api(`/api/job/${pid}/document/letter_draft`, {});
      if (report(o, "Draft ready")) openJob(pid); } }, "Draft cover letter"),
    d.application && d.application.status === "interview"
      ? h("button", { class: "btn", onclick: async () => { const o = await api(`/api/job/${pid}/document/prep`, {});
          if (report(o, "Prep brief saved")) showText("Interview prep", o.brief); } }, "Interview prep") : null));
  if (d.letter) {
    const area = h("textarea", { "aria-label": "Cover letter" });
    area.value = d.letter.approved_text || d.letter.draft;
    parts.push(h("p", { class: "note" }, d.letter.approved_text ? "Approved letter (edit and approve again to change it):"
                                                                 : "AI draft. Edit it; it is used only after you approve it."),
      (d.letter.warnings || []).length ? h("ul", { class: "check-bad" }, d.letter.warnings.map((w) => h("li", {}, w))) : null,
      area, h("div", { class: "approve" }, h("button", { class: "btn primary", onclick: async () => {
        report(await api(`/api/job/${pid}/document/letter_approve`, { text: area.value }), "Letter approved"); openJob(pid); } },
        "Approve this letter")));
  }
  if ((d.resumes || []).length) parts.push(h("h3", {}, "Resume versions"),
    h("ul", {}, d.resumes.map((r) => h("li", {}, `${r.format} · ${String(r.generated_at).slice(0, 16).replace("T", " ")} · ${r.path.split("/").pop()}`))));

  parts.push(h("h3", {}, "Job description (from the job board)"),
    h("pre", { class: "text" }, j.description || "No description stored; open the posting."));
  if ((d.audit || []).length) parts.push(h("h3", {}, "Submit history"),
    h("ul", {}, d.audit.map((a) => h("li", {}, `${a.created_at.slice(0, 16).replace("T", " ")} · ${a.action} · ${a.outcome}${a.detail ? " · " + a.detail : ""}`))));
  body.replaceChildren(...parts);
  body.scrollTop = 0;
}

function closeDrawer() { document.getElementById("drawer").hidden = true; state.openJob = null; state.assist = null; }

// ---- the search profile ----------------------------------------------------------------------------------------------

const LIST_FIELDS = [["search_terms", "Roles to search"], ["also_search", "More cities to search"],
  ["seniority", "Seniority (for scoring)"], ["locations", "Only jobs located in (blank = anywhere)"],
  ["blacklist_companies", "Never show these companies"], ["whitelist_companies", "Always show these companies"]];

async function renderProfile() {
  const p = await api("/api/profile");
  const el = document.getElementById("view-profile");
  if (!p.ok) { el.replaceChildren(h("p", {}, p.error || "Cannot read the profile")); return; }
  const pr = p.profile, inputs = {};
  const text = (key, label, value, type) => {
    const input = h("input", { type: type || "text", id: `f-${key}` });
    input.value = value ?? ""; inputs[key] = input;
    return h("div", { class: "field" }, h("label", { for: `f-${key}` }, label), input);
  };
  const modes = ["remote", "hybrid", "onsite"].map((m) => {
    const box = h("input", { type: "checkbox", id: `m-${m}` }); box.checked = (pr.work_modes || []).includes(m);
    inputs[`mode_${m}`] = box; return h("label", { for: `m-${m}`, class: "check" }, box, ` ${m} `);
  });
  const staffing = h("input", { type: "checkbox", id: "f-staff" }); staffing.checked = pr.exclude_staffing_agencies !== false;
  const form = h("div", { class: "panel" }, h("h2", {}, "What to search for"),
    h("p", { class: "note" }, "These decide what JobPilot searches for and which jobs are hidden. To sort and filter the jobs you already have, use Filters & sorting on the Pipeline tab. Your answers for application forms are under My answers."),
    text("headline", "Headline", pr.headline), text("location", "Home city", pr.location),
    LIST_FIELDS.map(([k, l]) => text(k, `${l} (comma separated)`, (pr[k] || []).join(", "))),
    h("div", { class: "field" }, h("label", {}, "Work modes (none ticked = all)"), modes),
    text("salary_floor", "Lowest yearly salary (blank = no floor)", pr.salary_floor, "number"),
    text("salary_currency", "Salary currency (INR, USD, EUR, GBP)", pr.salary_currency),
    text("min_score", "Digest score threshold (50–100)", pr.min_score ?? 70, "number"),
    text("followup_days", "Days of silence before a follow-up", pr.followup_days ?? 7, "number"),
    h("div", { class: "field" }, h("label", { for: "f-staff" }, staffing, " Leave out staffing-agency posts")),
    h("button", { class: "btn primary", onclick: async () => {
      const body = { headline: inputs.headline.value, location: inputs.location.value,
                     work_modes: ["remote", "hybrid", "onsite"].filter((m) => inputs[`mode_${m}`].checked),
                     exclude_staffing_agencies: staffing.checked, salary_currency: inputs.salary_currency.value.trim() };
      for (const [k] of LIST_FIELDS) body[k] = inputs[k].value.split(",").map((x) => x.trim()).filter(Boolean);
      for (const k of ["salary_floor", "min_score", "followup_days"]) if (inputs[k].value !== "") body[k] = Number(inputs[k].value);
      if (body.salary_floor === undefined) body.salary_floor = 0;
      report(await api("/api/profile", body), "Profile saved; matches re-weighed"); refresh();
    } }, "Save profile"));
  el.replaceChildren(form);
}

// ---- my answers: ready-made questions, formal versions, and the Fo writing helper ------------------------------------

const fieldRefs = {};

function answerField(q, saved) {
  let el;
  if (q.kind === "choice") {
    const options = [...(q.options || [])];
    if (saved && saved.answer && !options.includes(saved.answer)) options.push(saved.answer);
    el = h("select", { "aria-label": q.question }, h("option", { value: "" }, "— choose —"), options.map((o) => h("option", { value: o }, o)));
  } else if (q.kind === "long") {
    el = h("textarea", { "aria-label": q.question, rows: "5" });
  } else {
    el = h("input", { type: "text", "aria-label": q.question });
  }
  el.value = saved ? saved.answer : "";
  fieldRefs[q.question] = el;
  return el;
}

function formalBlock(q, saved) {
  if (!saved) return null;
  const f = saved.formal;
  const redo = h("button", { class: "btn", onclick: async (e) => { e.target.disabled = true; toast("Writing the formal version…");
    report(await api("/api/answer/formal", { question: q.question, action: "draft" }), "Formal version written"); rerenderAnswers(); } },
    f ? "Rewrite formal version" : "Write formal version");
  if (!f) return h("div", { class: "formal" }, h("div", { class: "note" }, "No formal version yet: this goes into forms exactly as you typed it."), redo);
  return h("div", { class: "formal" },
    h("div", { class: "note" }, f.approved ? "Formal version (approved, used in forms word for word):" : "Formal version (NOT approved yet; forms still use what you typed):"),
    h("div", { class: "text" }, f.text),
    (f.warnings || []).length ? h("ul", { class: "check-bad" }, f.warnings.map((w) => h("li", {}, w))) : null,
    h("div", { class: "row" },
      !f.approved ? h("button", { class: "btn primary", onclick: async () => {
        report(await api("/api/answer/formal", { question: q.question, action: "approve", text: f.text }), "Approved"); rerenderAnswers(); } },
        "Approve this formal version") : null, redo));
}

let derivedOn = false;

function answerCard(q) {
  const saved = q.saved;
  const field = answerField(q, saved);
  const state_ = q.derived && derivedOn ? h("span", { class: "badge ok" }, "Worked out per job")
    : !saved ? h("span", { class: "badge" }, "Not answered")
    : saved.formal && saved.formal.approved ? h("span", { class: "badge ok" }, "Approved")
    : h("span", { class: "badge warn" }, "Saved, formal not approved");
  return h("div", { class: "qcard" },
    h("div", { class: "qhead" }, h("div", { class: "qtext" }, q.question), state_),
    q.hint ? h("div", { class: "note" }, q.hint) : null, field,
    h("div", { class: "row" },
      h("button", { class: "btn primary", onclick: async (e) => {
        if (!field.value.trim()) { toast("Type or choose an answer first"); return; }
        e.target.disabled = true; toast("Saving, then writing the formal version…");
        const out = await api("/api/answer", { question: q.question, answer: field.value });
        report(out, "Saved. Read the formal version below, then approve it."); rerenderAnswers(); } }, "Save"),
      h("button", { class: "btn", onclick: () => openAssistant(q) }, "Ask Fo to help me write it"),
      saved ? h("button", { class: "btn danger", onclick: async () => {
        report(await api("/api/answer", { question: q.question, answer: "" }), "Removed"); rerenderAnswers(); } }, "Remove") : null),
    formalBlock(q, saved));
}

async function rerenderAnswers() { const y = window.scrollY; await renderAnswers(); window.scrollTo(0, y); }

async function renderAnswers() {
  const p = await api("/api/profile");
  const el = document.getElementById("view-answers");
  if (!p.ok) { el.replaceChildren(h("p", {}, p.error || "Cannot read your answers")); return; }
  for (const k of Object.keys(fieldRefs)) delete fieldRefs[k];
  derivedOn = ((p.eligibility || {}).work_authorized_countries || []).length > 0;
  const catalog = p.catalog || [], custom = (p.custom_answers || []).map((a) => ({
    question: a.question, kind: "long", options: [], hint: "", saved: a }));
  const all = [...catalog, ...custom];
  const answered = all.filter((q) => q.saved).length;
  const approved = all.filter((q) => q.saved && q.saved.formal && q.saved.formal.approved).length;
  const groups = [];
  for (const q of catalog) {
    let g = groups.find((x) => x.name === q.group);
    if (!g) { g = { name: q.group, items: [] }; groups.push(g); }
    g.items.push(q);
  }
  const newQ = h("input", { type: "text", "aria-label": "Your own question", placeholder: "A question a form asked that is not above" });
  const addCard = h("div", { class: "panel" }, h("h2", {}, "Add a question of your own"), newQ,
    h("div", { class: "row" }, h("button", { class: "btn", onclick: () => {
      const text = newQ.value.trim();
      if (!text) { toast("Type the question first"); return; }
      const q = { question: text, kind: "long", options: [], hint: "", saved: null };
      const card = answerCard(q); addCard.after(card); card.scrollIntoView({ behavior: "smooth", block: "center" }); newQ.value = ""; } },
      "Add and answer it")));
  el.replaceChildren(...[
    h("div", { class: "panel" }, h("h2", {}, "My answers"),
      h("p", {}, `${answered} of ${all.length} answered, ${approved} formal versions approved.`),
      h("p", { class: "note" }, "Answer once; every application is prefilled from these. When you save an answer, a formal, polite version is written for you to read. Forms use it only after you approve it, word for word; until then your answer goes in exactly as you typed it. Stuck? Press “Ask Fo to help me write it”: Fo uses only your resume and what you tell it.")),
    eligibilityPanel(p),
    ...groups.map((g) => h("div", { class: "panel" }, h("h2", {}, g.name), g.items.map((q) => answerCard(q)))),
    custom.length ? h("div", { class: "panel" }, h("h2", {}, "Your own questions"), custom.map((q) => answerCard(q))) : null,
    addCard].filter(Boolean));
}

function eligibilityPanel(p) {
  const el = p.eligibility || {};
  const cap = (c) => c.replace(/\b\w/g, (x) => x.toUpperCase());
  const list = (v) => v.split(",").map((x) => x.trim()).filter(Boolean);
  const auth = h("input", { type: "text", "aria-label": "Countries you can work in", placeholder: "e.g. India" });
  auth.value = (el.work_authorized_countries || []).map(cap).join(", ");
  const reloc = h("input", { type: "text", "aria-label": "Countries you would relocate to", placeholder: "e.g. United Kingdom, Canada" });
  reloc.value = (el.relocation_countries || []).map(cap).join(", ");
  const trial = h("input", { type: "text", "aria-label": "A job location to try", placeholder: "e.g. Austin, TX, US   or   London, UK   or   Remote" });
  const result = h("div", { class: "formal" }); result.hidden = true;
  const tryIt = async () => {
    const r = await api(`/api/eligibility?location=${encodeURIComponent(trial.value)}`);
    if (!r.ok) { report(r); return; }
    result.hidden = false;
    result.replaceChildren(
      h("div", { class: "note" }, `For a job at “${r.location}”: ${r.basis}.`),
      ...Object.entries(r.answers).map(([q, a]) => h("div", {}, h("b", {}, a), ` — ${q}`)),
      r.ask_me.length ? h("div", { class: "check-bad" }, "Not clear from the location, so Fo will stop and ask you before answering: " + r.ask_me.join(" | ")) : null);
  };
  return h("div", { class: "panel" }, h("h2", {}, "Where can you work?"),
    h("p", { class: "note" }, "Some answers depend on the country of each job. Tell JobPilot where you can work without sponsorship and where you would relocate, and it answers “authorised to work?”, “need sponsorship?” and “willing to relocate?” for each job from the job's location. When a job's country is not clear (a plain “Remote”), Fo stops and asks you instead of guessing."),
    h("div", { class: "field" }, h("label", {}, "Countries where you can work without sponsorship (comma separated)"), auth),
    h("div", { class: "field" }, h("label", {}, "Countries you would relocate to (comma separated, optional)"), reloc),
    h("div", { class: "row" }, h("button", { class: "btn primary", onclick: async () => {
      report(await api("/api/profile", { work_authorized_countries: list(auth.value), relocation_countries: list(reloc.value) }), "Saved");
      rerenderAnswers(); } }, "Save countries")),
    h("div", { class: "field" }, h("label", {}, "Try it on a job location"), trial),
    h("div", { class: "row" }, h("button", { class: "btn", onclick: tryIt }, "What would JobPilot answer?")), result);
}

function sleep(ms) { return new Promise((r) => setTimeout(r, ms)); }

function renderTranscript() {
  const a = state.assist, box = document.getElementById("assist-log");
  if (!a || !box) return;
  box.replaceChildren(...[...a.history.map((m, idx) => h("div", { class: `msg ${m.role}` },
    h("div", { class: "who" }, m.role === "me" ? "You" : "Fo"), h("div", { class: "text" }, m.text),
    m.role === "fo" && idx === a.history.length - 1 && !a.busy ? h("div", { class: "row" },
      h("button", { class: "btn primary", onclick: () => {
        const f = fieldRefs[a.question];
        if (f) { f.value = m.text; f.scrollIntoView({ behavior: "smooth", block: "center" }); }
        toast("Put into the answer box. Edit it if you like, then press Save."); closeDrawer(); } }, "Use this as my answer")) : null)),
    a.busy ? h("div", { class: "note" }, "Fo is writing… this can take a minute.") : null].filter(Boolean));
  box.scrollTop = box.scrollHeight;
}

async function assistSend(notes) {
  const a = state.assist;
  if (!a || a.busy) return;
  const field = fieldRefs[a.question];
  const prior = a.history.slice(-6);
  if (notes) a.history.push({ role: "me", text: notes });
  a.busy = true; renderTranscript();
  const start = await api("/api/assist", { question: a.question, notes, current: field ? field.value : "", history: prior });
  if (!start.ok) { a.busy = false; a.history.push({ role: "fo", text: `Could not ask Fo: ${start.error || "unknown error"}` }); renderTranscript(); return; }
  let result = { state: "working" };
  for (let i = 0; i < 150 && result.state === "working" && state.assist === a; i++) {
    await sleep(2000);
    result = await api(`/api/assist/${start.id}`);
  }
  if (state.assist !== a) return;                                    // the helper was closed meanwhile
  a.busy = false;
  a.history.push({ role: "fo", text: result.state === "done" ? result.reply : (result.error || "Fo did not answer in time; try again.") });
  renderTranscript();
}

function openAssistant(q) {
  state.openJob = null;
  state.assist = { question: q.question, history: [], busy: false };
  document.getElementById("drawer").hidden = false;
  document.getElementById("drawer-title").textContent = "Fo writing helper";
  const notes = h("textarea", { "aria-label": "What you want to say", rows: "4",
    placeholder: "Tell Fo the facts and what you want to say, for example: 30 days notice, can join from 1 December. Or press “Draft it for me”." });
  const send = h("button", { class: "btn primary", onclick: () => { const t = notes.value.trim(); notes.value = ""; assistSend(t); } }, "Send");
  const draft = h("button", { class: "btn", onclick: () => assistSend("") }, "Draft it for me");
  document.getElementById("drawer-body").replaceChildren(
    h("div", { class: "panel" }, h("div", { class: "note" }, "Question"), h("div", { class: "qtext" }, q.question)),
    h("p", { class: "note" }, "Fo uses only your resume and what you tell it here, and asks you when it needs a fact. It takes no other action. Nothing is saved until you press Save on the question."),
    h("div", { id: "assist-log", class: "assist-log" }), notes, h("div", { class: "row" }, send, draft));
  renderTranscript();
}

// ---- wiring -------------------------------------------------------------------------------------------------------------

function show(view) {
  state.view = view;
  for (const b of document.querySelectorAll(".tab")) b.classList.toggle("is-on", b.dataset.view === view);
  for (const v of ["board", "inbox", "answers", "profile"]) document.getElementById(`view-${v}`).hidden = v !== view;
  if (view === "profile") renderProfile();
  if (view === "answers") renderAnswers();
}

async function refresh() {
  const b = await api("/api/board");
  if (!b.ok) return;
  state.board = b;
  renderStatus(b.status); renderBoard(b); renderInbox(b);
}

document.addEventListener("DOMContentLoaded", () => {
  state.filters = loadFilters(); buildFilters();
  for (const b of document.querySelectorAll(".tab")) b.addEventListener("click", () => show(b.dataset.view));
  document.getElementById("drawer-close").addEventListener("click", closeDrawer);
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeDrawer(); });
  refresh();
  setInterval(() => { if (!document.hidden) refresh(); }, 8000);       // near real time: Fo's work shows within seconds
  if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js").catch(() => {});
});
