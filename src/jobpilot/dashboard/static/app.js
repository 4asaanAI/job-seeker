// JobPilot dashboard. Every piece of job text is inserted as text (never as HTML): job posts are written by strangers.
"use strict";

const COLUMNS = [["match", "Matches"], ["started", "Started"], ["applied", "Applied"], ["interview", "Interview"],
                 ["rejected", "Closed"]];
let state = { board: null, view: "board", openJob: null };

function h(tag, attrs, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (k === "class") el.className = v;
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat()) {
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
  if (s.answers_saved_by_fo_7d) el.append(h("span", { class: "warn" }, `Fo saved ${s.answers_saved_by_fo_7d} answer(s) this week: check Search profile`));
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
      item.deadline_soon ? h("span", { class: "badge warn" }, "closes in 48h") : null,
      item.work_mode ? h("span", { class: "badge" }, item.work_mode) : null,
      h("span", { class: "quick" }, quick)));
}

function renderBoard(b) {
  const el = document.getElementById("board");
  el.replaceChildren(...COLUMNS.map(([key, label]) => {
    const items = b.columns[key] || [];
    return h("section", { class: "column", "aria-label": label },
      h("h2", {}, h("span", {}, label), h("span", {}, items.length)),
      items.length ? items.map((i) => card(i, key)) : h("div", { class: "note" }, "Nothing here yet."));
  }));
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
  state.openJob = pid;
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

  // review: prefilled fields, answers, the request, approve with code
  if (d.review) {
    parts.push(h("h3", {}, "Review before applying"));
    if (d.review.error) parts.push(h("p", { class: "check-bad" }, d.review.error));
    else {
      if (d.form) parts.push(kv([["Name", d.form.full_name], ["Email", d.form.email], ["Phone", d.form.phone],
                                 ["Address", d.form.address], ["Current job", d.form.current_employer ? `${d.form.current_title}, ${d.form.current_employer}` : "none (no job without an end date)"]]));
      const answers = Object.entries(d.review.answers || {});
      parts.push(h("h3", {}, "Your saved answers used"),
        answers.length ? kv(answers) : h("p", { class: "note" }, "None. Add them under Search profile so forms prefill."));
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

function closeDrawer() { document.getElementById("drawer").hidden = true; state.openJob = null; }

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
    inputs[`mode_${m}`] = box; return h("label", { for: `m-${m}` }, box, ` ${m} `);
  });
  const staffing = h("input", { type: "checkbox", id: "f-staff" }); staffing.checked = pr.exclude_staffing_agencies !== false;
  const form = h("div", { class: "panel" }, h("h2", {}, "What to search for"),
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
  const q = h("input", { type: "text", list: "std-q", "aria-label": "Question" });
  const a = h("textarea", { "aria-label": "Your answer", style: "min-height:70px" });
  const answers = h("div", { class: "panel" }, h("h2", {}, "Your saved answers"),
    h("p", { class: "note" }, "Type each answer yourself. A formal, polite version is written for you to read; forms use it only after you approve it, word for word. Until then the answer goes in as you typed it."),
    (p.answers || []).map((x) => h("div", { class: "list-row" },
      h("div", { class: "grow" }, h("div", {}, x.question), h("div", { class: "note" }, `As you typed: ${x.answer}`),
        x.formal ? h("div", {}, h("div", { class: "note" }, `Formal version${x.formal.approved ? " (approved, used in forms)" : " (not approved yet)"}:`),
          h("div", {}, x.formal.text),
          (x.formal.warnings || []).length ? h("ul", { class: "check-bad" }, x.formal.warnings.map((w) => h("li", {}, w))) : null)
          : h("div", { class: "note" }, "No formal version yet.")),
      h("button", { class: "btn", onclick: async () => { toast("Writing the formal version…");
        report(await api("/api/answer/formal", { question: x.question, action: "draft" }), "Formal version written"); renderProfile(); } }, x.formal ? "Rewrite" : "Write formal version"),
      x.formal && !x.formal.approved ? h("button", { class: "btn primary", onclick: async () => {
        report(await api("/api/answer/formal", { question: x.question, action: "approve", text: x.formal.text }), "Approved"); renderProfile(); } }, "Approve") : null,
      h("button", { class: "btn danger", onclick: async () => { report(await api("/api/answer", { question: x.question, answer: "" }), "Removed"); renderProfile(); } }, "Remove"))),
    (p.not_answered_yet || []).length ? h("p", { class: "note" }, `Not answered yet: ${p.not_answered_yet.join(" · ")}`) : null,
    h("datalist", { id: "std-q" }, (p.not_answered_yet || []).map((x) => h("option", { value: x }))),
    h("div", { class: "field" }, h("label", {}, "Question"), q), h("div", { class: "field" }, h("label", {}, "Answer"), a),
    h("button", { class: "btn primary", onclick: async () => {
      toast("Saving and writing the formal version…");
      report(await api("/api/answer", { question: q.value, answer: a.value }), "Answer saved"); renderProfile(); } }, "Save answer"));
  el.replaceChildren(form, answers);
}

// ---- wiring -------------------------------------------------------------------------------------------------------------

function show(view) {
  state.view = view;
  for (const b of document.querySelectorAll(".tab")) b.classList.toggle("is-on", b.dataset.view === view);
  for (const v of ["board", "inbox", "profile"]) document.getElementById(`view-${v}`).hidden = v !== view;
  if (view === "profile") renderProfile();
}

async function refresh() {
  const b = await api("/api/board");
  if (!b.ok) return;
  state.board = b;
  renderStatus(b.status); renderBoard(b); renderInbox(b);
}

document.addEventListener("DOMContentLoaded", () => {
  for (const b of document.querySelectorAll(".tab")) b.addEventListener("click", () => show(b.dataset.view));
  document.getElementById("drawer-close").addEventListener("click", closeDrawer);
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeDrawer(); });
  refresh();
  setInterval(() => { if (!document.hidden) refresh(); }, 30000);
  if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js").catch(() => {});
});
