/* OmniAgentOS Starter dashboard.
   Vanilla JS, no framework, no build step, no CDN. It renders the production
   line: the goal enters at the Planner and travels lane by lane until the
   Verifier signs it off. */

(function () {
  "use strict";

  var EVENT_TYPES = [
    "run.started", "memory.recalled", "skill.selected", "skill.selection_fallback",
    "plan.pruned", "planner.plan", "worker.started", "worker.delta", "worker.finished",
    "tool.write", "tool.error", "critic.verdict", "verdict.incomplete", "repair.dispatched",
    "verifier.verdict", "run.done", "run.failed", "lesson.saved", "llm.call"
  ];

  var el = function (id) { return document.getElementById(id); };
  var state = {
    runId: null, source: null, goal: "", busySince: null, busyTimer: null,
    tasks: {}, cards: {}, dod: [], skills: {}, deliverable: "", files: [],
    calls: 0, tokens: 0, cost: 0, rounds: 0, health: null
  };

  function esc(text) {
    return String(text === undefined || text === null ? "" : text)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function hhmmss(date) {
    var p = function (n) { return (n < 10 ? "0" : "") + n; };
    return p(date.getHours()) + ":" + p(date.getMinutes()) + ":" + p(date.getSeconds());
  }

  /* ------------------------------------------------------------- health */
  function loadHealth() {
    return fetch("/api/health").then(function (r) { return r.json(); }).then(function (h) {
      state.health = h;
      var logo = el("brand-logo");
      if (h.brand) {
        if (h.brand.logo_url) { logo.src = h.brand.logo_url; }
        logo.alt = h.brand.name || "OmniRogue";
      }
      var chip = el("chip-provider");
      if (h.configured) {
        chip.textContent = "✓ provider ready — " + (h.provider || "?") + " / " + (h.model || "?");
        chip.className = "chip ok";
      } else {
        chip.textContent = "✕ provider unavailable — " + (h.error_tag || "PROVIDER_NOT_CONFIGURED");
        chip.className = "chip bad";
      }
      el("chip-skills").textContent = "🧩 " + (h.skills || 0) + " skills loaded";
      el("r-provider").textContent = h.provider || "–";
      el("r-model").textContent = h.model || "–";
      var firstRun = el("first-run");
      firstRun.hidden = !!h.configured;
      if (!h.configured) {
        el("first-run-tag").textContent = h.error_tag || "PROVIDER_NOT_CONFIGURED";
        el("run-button").disabled = false;
      }
      return h;
    }).catch(function () {
      el("chip-provider").textContent = "✕ dashboard cannot reach the API";
      el("chip-provider").className = "chip bad";
    });
  }

  function loadSkills() {
    return fetch("/api/skills").then(function (r) { return r.json(); }).then(function (data) {
      var packs = data.skills || [];
      packs.forEach(function (s) { state.skills[s.id] = s; });
      var body = el("skills-body");
      if (!packs.length) {
        body.innerHTML = '<p class="muted">No skill packs installed — the built-in general-assistant pack will be used.</p>';
        return;
      }
      body.innerHTML = '<p class="muted">' + packs.length + " packs across " +
        ((data.categories || []).length) + " categories. Drop more into <code>skills/</code>; the loader is a directory scan.</p>" +
        packs.map(function (s) {
          return '<div class="skill" data-skill="' + esc(s.id) + '"><b>' + esc(s.name) +
            '</b> <span class="skill-cat">· ' + esc(s.category) + "</span></div>";
        }).join("");
    }).catch(function () { });
  }

  /* --------------------------------------------------------------- busy */
  function setBusy(on) {
    var busy = el("busy");
    el("run-button").disabled = on;
    if (on) {
      state.busySince = new Date();
      busy.hidden = false;
      el("busy-text").textContent = "working… since " + hhmmss(state.busySince);
      if (state.busyTimer) { clearInterval(state.busyTimer); }
      state.busyTimer = setInterval(function () {
        el("busy-text").textContent = "working… since " + hhmmss(state.busySince);
      }, 1000);
    } else {
      busy.hidden = true;
      if (state.busyTimer) { clearInterval(state.busyTimer); state.busyTimer = null; }
    }
  }

  function laneStatus(lane, text, cls) {
    var node = el("lane-" + lane);
    if (!node) { return; }
    node.querySelector("[data-status]").textContent = text;
    node.classList.remove("active", "pass", "fail");
    if (cls) { node.classList.add(cls); }
  }

  function travel(n) {
    var t = el("token-" + n);
    if (!t) { return; }
    t.classList.remove("travelling");
    void t.offsetWidth;
    t.classList.add("travelling");
  }

  function showError(tag, message) {
    el("error-tag").textContent = tag || "ERROR";
    el("error-message").textContent = message || "";
    el("error-banner").hidden = false;
  }

  /* ------------------------------------------------------------ rendering */
  function renderDod() {
    var list = el("dod-list");
    if (!state.dod.length) { return; }
    list.innerHTML = state.dod.map(function (c) {
      var mark = c.state === "pass" ? '<span class="dod-state pass">✓ pass</span>'
        : c.state === "fail" ? '<span class="dod-state fail">✕ fail</span>'
          : '<span class="dod-state">○ pending</span>';
      var skill = state.skills[c.source];
      var source = c.source === "planner" ? "from planner"
        : c.source === "operator" ? "from operator"
          : "from skill: " + esc(skill ? skill.name : c.source);
      return "<li>" + mark + esc(c.criterion) +
        '<span class="dod-source">' + source + " · " + esc(c.id) + "</span></li>";
    }).join("");
  }

  function renderTasks() {
    var ids = Object.keys(state.tasks);
    var body = el("workers-body");
    if (!ids.length) { return; }
    body.innerHTML = ids.map(function (id) {
      var t = state.tasks[id];
      var mark = t.status === "done" ? "✓" : t.status === "running" ? "●" : "○";
      var files = (t.files && t.files.length) ? " · " + t.files.length + " file(s)" : "";
      return '<div class="task"><div class="task-title">' + mark + " " + esc(t.title || id) + "</div>" +
        '<div class="task-skill">' + esc(t.skill_id || "") + " · " + esc(t.status) + files + "</div></div>";
    }).join("");
  }

  function renderCards() {
    var keys = Object.keys(state.cards);
    var host = el("critic-cards");
    if (!keys.length) { host.innerHTML = ""; return; }
    host.innerHTML = keys.map(function (k) {
      var c = state.cards[k];
      var repaired = c.state === "repaired";
      var ba = "";
      if (repaired && (c.before || c.after)) {
        ba = '<div class="beforeafter">' +
          "<div><b>before</b><pre>" + esc((c.before || "").slice(0, 600)) + "</pre></div>" +
          "<div><b>after</b><pre>" + esc((c.after || "").slice(0, 600)) + "</pre></div></div>";
      }
      return '<article class="critic-card' + (repaired ? " repaired" : "") + '">' +
        "<h3>" + (repaired ? "✓ repaired" : "✕ critic failed") + " — " + esc(c.criterion || c.id) + "</h3>" +
        '<div class="row"><span class="k">why</span>' + esc(c.reason) + "</div>" +
        '<div class="row"><span class="k">fix</span>' + esc(c.fix) + "</div>" +
        '<div class="row"><span class="k">task</span>' + esc(c.task_id || "?") +
        ' <span class="k">round</span>' + esc(c.round) + "</div>" + ba + "</article>";
    }).join("");
  }

  function renderFiles() {
    var tree = el("file-tree");
    if (!state.files.length) {
      tree.innerHTML = '<li class="muted">No files written in this run.</li>';
      return;
    }
    tree.innerHTML = state.files.map(function (f) {
      var href = "/api/runs/" + encodeURIComponent(state.runId) + "/files/" + f.path;
      return '<li>📄 <a href="' + esc(href) + '" target="_blank" rel="noopener">' + esc(f.path) + "</a> " +
        '<span class="muted">(' + (f.bytes || 0) + " bytes)</span></li>";
    }).join("");
  }

  function renderReceipt(extra) {
    el("r-calls").textContent = state.calls;
    el("r-tokens").textContent = state.tokens;
    el("r-cost").textContent = "$" + state.cost.toFixed(4);
    el("r-rounds").textContent = state.rounds || "–";
    if (extra && extra.elapsed_ms !== undefined) {
      el("r-elapsed").textContent = (extra.elapsed_ms / 1000).toFixed(1) + "s";
    }
  }

  /* -------------------------------------------------------------- events */
  function handle(type, ev) {
    var p = ev.payload || {};
    switch (type) {
      case "run.started":
        state.rounds = 0;
        laneStatus("planner", "● planning", "active");
        el("run-id").textContent = "run " + (p.run_id || state.runId) + (p.replay ? " (replay)" : "");
        break;

      case "memory.recalled": {
        var body = el("memory-body");
        if (!p.matched) {
          body.innerHTML = '<p class="muted">○ no lesson matched this goal yet — the first run teaches the next one.</p>';
        } else {
          body.innerHTML = (p.lessons || []).map(function (l) {
            return '<div class="lesson">' + esc(l.text) +
              '<div class="lesson-meta">learned in run #' + esc(l.run_id) + ", " + esc(l.age_s) + "s ago</div></div>";
          }).join("");
        }
        break;
      }

      case "skill.selected": {
        (p.skills || []).forEach(function (s) { state.skills[s.id] = s; });
        var names = (p.skills || []).map(function (s) { return s.name + " (" + s.category + ")"; });
        var host = el("planner-body");
        host.innerHTML = '<p>🧩 skills: ' + esc(names.join(", ") || (p.skill_ids || []).join(", ")) + "</p>" +
          (host.dataset.plan || "");
        break;
      }

      case "skill.selection_fallback":
        el("chip-skills").textContent = "🧩 fallback pack: " + (p.skill_id || "general-assistant");
        break;

      case "planner.plan": {
        state.dod = (p.dod || []).map(function (c) { return { id: c.id, criterion: c.criterion, source: c.source, state: "pending" }; });
        renderDod();
        var plan = "<p><b>" + (p.tasks || []).length + " tasks · " + (p.dod || []).length + " criteria</b></p>" +
          (p.tasks || []).map(function (t) {
            return '<div class="task"><div class="task-title">' + esc(t.title) + "</div>" +
              '<div class="task-skill">' + esc(t.id) + " · " + esc(t.skill_id) + "</div></div>";
          }).join("");
        var pb = el("planner-body");
        pb.dataset.plan = plan;
        pb.innerHTML = pb.innerHTML + plan;
        laneStatus("planner", "✓ planned", "pass");
        travel(1);
        break;
      }

      case "worker.started":
        state.tasks[p.task_id] = state.tasks[p.task_id] || {};
        state.tasks[p.task_id].title = p.title;
        state.tasks[p.task_id].skill_id = p.skill_id;
        state.tasks[p.task_id].status = "running";
        laneStatus("worker", "● working", "active");
        laneStatus("deliverable", "● typing", "active");
        renderTasks();
        break;

      case "worker.delta":
        state.deliverable += p.text || "";
        el("deliverable").textContent = state.deliverable.slice(-6000);
        break;

      case "worker.finished": {
        var t = state.tasks[p.task_id] || (state.tasks[p.task_id] = {});
        t.status = "done";
        t.artifact = p.artifact || "";
        t.files = p.files_written || [];
        Object.keys(state.cards).forEach(function (k) {
          if (state.cards[k].task_id === p.task_id && state.cards[k].state === "repairing") {
            state.cards[k].after = t.artifact;
          }
        });
        renderTasks();
        laneStatus("worker", "✓ tasks complete", "pass");
        travel(2);
        break;
      }

      case "tool.write":
        state.files.push({ path: p.path, bytes: p.bytes });
        renderFiles();
        break;

      case "tool.error":
        showError(p.error_tag || "WORKSPACE_ESCAPE", (p.reason || "") + " — " + (p.requested || ""));
        break;

      case "critic.verdict": {
        state.rounds = p.round || state.rounds;
        (p.verdicts || []).forEach(function (v) {
          var c = state.dod.filter(function (d) { return d.id === v.criterion_id; })[0];
          if (c) { c.state = v.pass ? "pass" : "fail"; }
          if (!v.pass) {
            var existing = state.cards[v.criterion_id] || {};
            state.cards[v.criterion_id] = {
              id: v.criterion_id, criterion: (c && c.criterion) || v.criterion_id,
              reason: v.reason, fix: v.fix, task_id: v.task_id, round: p.round,
              state: "fail", before: existing.before, after: existing.after
            };
          } else if (state.cards[v.criterion_id]) {
            state.cards[v.criterion_id].state = "repaired";
            state.cards[v.criterion_id].reason = v.reason;
          }
        });
        renderDod();
        renderCards();
        laneStatus("critic", p.pass ? "✓ all criteria pass" : "✕ " + (p.failures || []).length + " failing", p.pass ? "pass" : "fail");
        if (p.pass) { travel(3); }
        break;
      }

      case "repair.dispatched":
        state.rounds = p.round || state.rounds;
        (p.task_ids || []).forEach(function (id) {
          var task = state.tasks[id];
          Object.keys(state.cards).forEach(function (k) {
            if (state.cards[k].task_id === id && state.cards[k].state === "fail") {
              state.cards[k].state = "repairing";
              state.cards[k].before = task ? task.artifact : "";
            }
          });
          if (task) { task.status = "repairing"; }
        });
        state.deliverable = "";
        el("deliverable").textContent = "";
        renderTasks();
        renderCards();
        laneStatus("critic", "↻ repair round " + (p.round || "?"), "fail");
        break;

      case "verifier.verdict":
        laneStatus("verifier", p.verified ? "✓ verified" : "✕ rejected", p.verified ? "pass" : "fail");
        if (p.verified) { travel(4); }
        break;

      case "llm.call":
        state.calls += 1;
        state.tokens += (p.prompt_tokens || 0) + (p.completion_tokens || 0);
        renderReceipt();
        break;

      case "lesson.saved": {
        var mem = el("memory-body");
        mem.innerHTML = '<div class="lesson">' + esc(p.text) +
          '<div class="lesson-meta">✓ lesson saved from this run (#' + esc(p.run_id) + ")</div></div>" + mem.innerHTML;
        break;
      }

      case "run.done":
        state.deliverable = p.deliverable || state.deliverable;
        el("deliverable").textContent = state.deliverable;
        state.rounds = p.rounds || state.rounds;
        state.cost = p.est_cost_usd || state.cost;
        state.tokens = p.tokens || state.tokens;
        state.calls = p.llm_calls || state.calls;
        state.files = p.files || state.files;
        renderFiles();
        renderReceipt(p);
        laneStatus("deliverable", "✓ delivered", "pass");
        setBusy(false);
        el("retry-button").hidden = true;
        break;

      case "run.failed":
        showError(p.error_tag || "INTERNAL_ERROR", p.message || "the run did not finish");
        laneStatus("deliverable", "✕ failed", "fail");
        state.rounds = p.rounds || state.rounds;
        renderReceipt(p);
        setBusy(false);
        el("retry-button").hidden = false;
        break;

      default:
        break;
    }
  }

  /* ---------------------------------------------------------- streaming */
  function attach(runId) {
    if (state.source) { state.source.close(); }
    state.runId = runId;
    var source = new EventSource("/api/runs/" + encodeURIComponent(runId) + "/events");
    state.source = source;
    EVENT_TYPES.forEach(function (type) {
      source.addEventListener(type, function (e) {
        var data;
        try { data = JSON.parse(e.data); } catch (err) { return; }
        handle(type, data);
        if (type === "run.done" || type === "run.failed") { source.close(); }
      });
    });
    source.onerror = function () {
      if (source.readyState === EventSource.CLOSED) { setBusy(false); }
    };
  }

  function resetRun(goal) {
    state.tasks = {}; state.cards = {}; state.dod = []; state.deliverable = "";
    state.files = []; state.calls = 0; state.tokens = 0; state.cost = 0; state.rounds = 0;
    state.goal = goal;
    el("deliverable").textContent = "";
    el("critic-cards").innerHTML = "";
    el("error-banner").hidden = true;
    el("retry-button").hidden = true;
    el("planner-body").dataset.plan = "";
    el("planner-body").innerHTML = '<p class="muted">Planning…</p>';
    el("workers-body").innerHTML = '<p class="muted">No tasks yet.</p>';
    el("dod-list").innerHTML = '<li class="muted">The Definition of Done appears once the Planner has run.</li>';
    renderFiles();
    ["planner", "worker", "critic", "verifier", "deliverable"].forEach(function (l) { laneStatus(l, "○ idle", null); });
  }

  function startRun() {
    var goal = el("goal").value.trim();
    if (!goal) { el("goal").focus(); return; }
    resetRun(goal);
    fetch("/api/runs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ goal: goal })
    }).then(function (r) {
      return r.json().then(function (body) { return { ok: r.ok, status: r.status, body: body }; });
    }).then(function (res) {
      if (!res.ok) {
        showError(res.body.error_tag || "BAD_REQUEST", res.body.message || "the run was refused");
        return;
      }
      setBusy(true);
      attach(res.body.run_id);
    }).catch(function (err) {
      showError("INTERNAL_ERROR", String(err));
    });
  }

  function startDemo() {
    resetRun(el("goal").value.trim());
    fetch("/api/demo", { method: "POST" }).then(function (r) {
      return r.json().then(function (body) { return { ok: r.ok, body: body }; });
    }).then(function (res) {
      if (!res.ok) {
        showError(res.body.error_tag || "PROVIDER_NOT_CONFIGURED", res.body.message || "no recorded run available");
        return;
      }
      if (res.body.goal) { el("goal").value = res.body.goal; }
      setBusy(true);
      attach(res.body.run_id);
    }).catch(function (err) { showError("INTERNAL_ERROR", String(err)); });
  }

  /* ------------------------------------------------------------- wiring */
  el("run-button").addEventListener("click", startRun);
  el("retry-button").addEventListener("click", startRun);
  el("demo-button").addEventListener("click", startDemo);
  el("error-dismiss").addEventListener("click", function () { el("error-banner").hidden = true; });
  el("goal").addEventListener("keydown", function (e) {
    if ((e.metaKey || e.ctrlKey) && e.key === "Enter") { startRun(); }
  });

  loadHealth().then(function () {
    var params = new URLSearchParams(window.location.search);
    if (params.get("demo") === "1") { startDemo(); }
    var replay = params.get("replay");
    if (replay) { resetRun(""); attach(replay); }
  });
  loadSkills();
})();
