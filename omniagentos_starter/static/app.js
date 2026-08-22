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
    "verifier.verdict", "run.done", "run.failed", "lesson.saved", "llm.call",
    "worker.reset", "skill.declined", "agent.assigned", "skill.assigned_by_router",
    "team.delegated", "agent.skills_unrestricted"
  ];

  /* The global tool allow-list, mirrored for the form's checkboxes. The server
     validates every save against its own list — this is an affordance, not the
     authority. */
  var TOOL_NAMES = ["read_file", "write_file", "list_files"];

  var el = function (id) { return document.getElementById(id); };
  var state = {
    runId: null, source: null, goal: "", busySince: null, busyTimer: null,
    tasks: {}, cards: {}, dod: [], skills: {}, deliverable: "", files: [],
    calls: 0, tokens: 0, cost: 0, rounds: 0, health: null,
    inflight: false, streamErrors: 0, token: "", replay: false,
    agents: [], editing: null, agentMode: "create", agentsGeneration: 0,
    delegations: {}, managerName: "", agentFilter: ""
  };

  var MAX_STREAM_ERRORS = 5;

  /* ----------------------------------------------------------------- auth
     When the server was started with OMNIAGENTOS_TOKEN (required for any
     non-loopback bind), every /api/* call needs it — including the ones no
     JavaScript makes: the EventSource connection and the workspace file links.
     An EventSource cannot send a header at all, so the token arrives in the page
     URL once, is exchanged for a same-origin cookie, and is then scrubbed out of
     the address bar so it does not live in history or in a Referer. */
  function bootstrapToken() {
    var params = new URLSearchParams(window.location.search);
    var supplied = params.get("token") || "";
    if (!supplied) { return Promise.resolve(false); }
    state.token = supplied;
    params.delete("token");
    var rest = params.toString();
    window.history.replaceState({}, "", window.location.pathname + (rest ? "?" + rest : ""));
    return fetch("/api/session", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ token: supplied })
    }).then(function (r) { return r.ok; }).catch(function () { return false; });
  }

  function authHeaders(base) {
    var headers = base || {};
    if (state.token) { headers["Authorization"] = "Bearer " + state.token; }
    return headers;
  }

  function apiFetch(path, options) {
    var opts = options || {};
    opts.headers = authHeaders(opts.headers || {});
    opts.credentials = "same-origin";
    return fetch(path, opts);
  }

  /* The two URLs the browser fetches without any JS in the loop. */
  function tokenQuery() {
    return state.token ? "?token=" + encodeURIComponent(state.token) : "";
  }

  function streamUrl(runId) {
    return "/api/runs/" + encodeURIComponent(runId) + "/events" + tokenQuery();
  }

  function esc(text) {
    return String(text === undefined || text === null ? "" : text)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function hhmmss(date) {
    var p = function (n) { return (n < 10 ? "0" : "") + n; };
    return p(date.getHours()) + ":" + p(date.getMinutes()) + ":" + p(date.getSeconds());
  }

  /* --------------------------------------------------------------- brand
     `logo_url` comes from an environment variable the operator set, and it is
     assigned straight into an <img src>. A filesystem path 404s on the projector
     AND puts a home directory in the DOM; a javascript:/data: value is an
     unfiltered URL sink. Only a same-origin path or an https URL is a dashboard
     URL, and anything else keeps the bundled default. */
  function safeLogoUrl(raw) {
    var value = String(raw || "").trim();
    if (!value) { return null; }
    if (value.charAt(0) === "/" && value.charAt(1) !== "/") { return value; }
    if (/^https:\/\//i.test(value)) { return value; }
    return null;
  }

  /* ------------------------------------------------------------- health */
  function applyHealth(h) {
    state.health = h;
    var logo = el("brand-logo");
    if (h.brand) {
      var url = safeLogoUrl(h.brand.logo_url);
      if (url) {
        logo.src = url; // brand.logo_url, only after safeLogoUrl() allowlisted it
      } else if (h.brand.logo_url) {
        showError("BAD_REQUEST",
          "OMNIAGENTOS_BRAND_LOGO is not a dashboard URL — copy the file into assets/ " +
          "and set /assets/<name>; keeping the bundled logo");
      }
      logo.alt = h.brand.name || "OmniAgentOS Starter";
    }
    var chip = el("chip-provider");
    if (h.configured) {
      chip.textContent = "✓ provider ready — " + (h.provider || "?") + " / " + (h.model || "?");
      chip.className = "chip ok";
    } else {
      chip.textContent = "✕ provider unavailable — " + (h.error_tag || "PROVIDER_NOT_CONFIGURED");
      chip.className = "chip bad";
    }
    el("chip-skills").textContent = "◧ " + (typeof h.skills === "number" ? h.skills : "?") + " skills loaded";
    el("r-provider").textContent = h.provider || "–";
    el("r-model").textContent = h.model || "–";
    var firstRun = el("first-run");
    firstRun.hidden = !!h.configured;
    if (!h.configured) {
      el("first-run-tag").textContent = h.error_tag || "PROVIDER_NOT_CONFIGURED";
      // Run stays enabled on purpose: the run must be allowed to fail with the
      // provider's own error_tag rather than being pre-empted by the UI.
      el("run-button").disabled = false;
    }
    return h;
  }

  function loadHealth() {
    return apiFetch("/api/health").then(function (r) {
      if (!r.ok) {
        return r.json().catch(function () { return {}; }).then(function (body) {
          throw { apiError: body, status: r.status };
        });
      }
      return r.json().then(applyHealth);
    }).catch(function (err) {
      // "Cannot reach the API" is NOT "no provider key". Reusing the first-run
      // copy here sent operators looking for a key they had already set.
      var chip = el("chip-provider");
      var tag = (err && err.apiError && err.apiError.error_tag) || "INTERNAL_ERROR";
      var message = (err && err.apiError && err.apiError.message) ||
        "the dashboard cannot reach the API — is the server still running?";
      chip.textContent = "✕ " + tag;
      chip.className = "chip bad";
      showError(tag, message);
      // Keep probing: a laptop that lost Wi-Fi for four seconds should not need
      // a page reload in front of an audience.
      window.setTimeout(loadHealth, 4000);
    });
  }

  function loadSkills() {
    return apiFetch("/api/skills").then(function (r) {
      if (!r.ok) { throw new Error("HTTP " + r.status); }
      return r.json();
    }).then(function (data) {
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
    }).catch(function () {
      // An empty catch left "Loading skill library…" on screen for the whole
      // webinar — in-progress forever is indistinguishable from slow.
      el("skills-body").innerHTML =
        '<p class="muted">✕ INTERNAL_ERROR — the skill library could not be read.</p>';
    });
  }

  /* ------------------------------------------------------------ avatars
     A deterministic SVG identity per agent, derived from its slug: the same
     slug always draws the same face, and two agents never collide by accident
     the way two initials do. No dependency, no network, no image file — it is
     arithmetic on a string, which is why it works offline on a stage. */
  function slugHash(slug) {
    var h = 2166136261;
    var text = String(slug || "");
    for (var i = 0; i < text.length; i += 1) {
      h ^= text.charCodeAt(i);
      h = (h * 16777619) >>> 0;
    }
    return h >>> 0;
  }

  var AVATAR_HUES = [198, 262, 152, 24, 340, 44, 288, 178];

  function avatarSvg(slug, size) {
    var h = slugHash(slug);
    var px = size || 44;
    var hue = AVATAR_HUES[h % AVATAR_HUES.length];
    var hue2 = AVATAR_HUES[(h >>> 3) % AVATAR_HUES.length];
    var rot = h % 360;
    var shape = (h >>> 5) % 3;
    var cx = 12 + ((h >>> 7) % 12);
    var cy = 12 + ((h >>> 11) % 12);
    var r = 9 + ((h >>> 13) % 7);
    var body = shape === 0
      ? '<circle cx="' + cx + '" cy="' + cy + '" r="' + r + '" fill="hsl(' + hue2 + ' 70% 62%)" opacity=".9"/>'
      : shape === 1
        ? '<rect x="' + (cx - r) + '" y="' + (cy - r) + '" width="' + (r * 2) + '" height="' + (r * 2) +
          '" rx="' + Math.round(r / 2) + '" fill="hsl(' + hue2 + ' 70% 62%)" opacity=".9"/>'
        : '<polygon points="' + cx + ',' + (cy - r) + ' ' + (cx + r) + ',' + (cy + r) + ' ' +
          (cx - r) + ',' + (cy + r) + '" fill="hsl(' + hue2 + ' 70% 62%)" opacity=".9"/>';
    return '<svg class="agent-avatar" data-testid="agent-avatar" width="' + px + '" height="' + px +
      '" viewBox="0 0 36 36" role="img" aria-label="' + esc(slug) + '">' +
      '<rect width="36" height="36" rx="10" fill="hsl(' + hue + ' 42% 24%)"/>' +
      '<g transform="rotate(' + rot + " 18 18)" + '">' + body + "</g>" +
      '<circle cx="' + (30 - (h % 6)) + '" cy="' + (8 + ((h >>> 17) % 6)) +
      '" r="3" fill="hsl(' + hue + ' 80% 76%)" opacity=".85"/>' +
      "</svg>";
  }

  /* ------------------------------------------------------------- agents */
  /* Roster fetches are not ordered. Saving an agent fires one while an earlier
     one may still be in flight, and the browser gives no promise about which
     lands first — so a slow response carrying the PRE-save list could arrive
     last and repaint the roster without the agent that was just created. The
     generation counter makes the answer "only the newest request may render";
     an older reply is discarded rather than believed. */
  function loadAgents() {
    var generation = (state.agentsGeneration += 1);
    var current = function () { return generation === state.agentsGeneration; };
    return apiFetch("/api/agents").then(function (r) {
      if (!r.ok) { throw new Error("HTTP " + r.status); }
      return r.json();
    }).then(function (data) {
      if (!current()) { return state.agents; }
      state.agents = data.agents || [];
      renderAgents();
      fillAgentPicker();
      renderAgentFilter();
      updateAgentHint();
      return state.agents;
    }).catch(function () {
      // A stale failure must not overwrite a fresh success either.
      if (!current()) { return; }
      el("agents-list").innerHTML =
        '<p class="muted">✕ INTERNAL_ERROR — the agent roster could not be read.</p>';
    });
  }

  function renderAgents() {
    var host = el("agents-list");
    if (!state.agents.length) {
      host.innerHTML = '<p class="muted">No agents yet. An agent is a named worker with a persona, ' +
        "its own skills and its own memory — press <b>New agent</b> and the roster fills here.</p>";
      return;
    }
    host.innerHTML = state.agents.map(function (a) {
      // A manager's capability lives with its team, so "the router may choose
      // from the whole library" is the wrong sentence on a manager's card.
      var skills = (a.skills || []).length
        ? esc((a.skills || []).join(", "))
        : (a.team_members || []).length
          ? "no skills of its own · the work goes to its team"
          : "no skills declared · the router may choose from the whole library";
      var lessons = typeof a.lessons === "number" ? a.lessons : 0;
      // A manager is shown by WHO it manages, by name. A list of slugs is a
      // database table, not a team.
      var team = (a.team_members || []).length
        ? '<div class="agent-meta agent-team" data-testid="agent-team">◉ manages ' +
          (a.team_members || []).map(function (m) { return esc(m.name || m.id); }).join(", ") +
          "</div>"
        : "";
      // A disabled agent is SHOWN and says why. Hiding it would look exactly
      // like nobody ever created it.
      // A disabled card names its FILE as well as its reason: two cards can
      // legitimately share a slug when a duplicate is on disk, and without the
      // filename the operator cannot tell which one to go and delete.
      var broken = a.enabled === false
        ? '<div class="agent-meta">✕ disabled — ' + esc((a.errors || []).join("; ")) +
          (a.file ? " (" + esc(a.file) + ")" : "") + "</div>"
        : "";
      // A built-in shows a DISABLED delete control that says why. Silently
      // omitting the button left the operator with no explanation on screen —
      // the API's 403 ("ships with the package and cannot be deleted") was only
      // visible to somebody reading the network tab.
      var actions = '<button type="button" class="link-button" data-agent-edit="' + esc(a.id) + '">edit</button>' +
        '<button type="button" class="link-button" data-agent-duplicate="' + esc(a.id) + '">duplicate</button>' +
        (a.builtin
          ? '<button type="button" class="link-button" data-testid="agent-delete-disabled" ' +
            'disabled title="the built-in agent ships with the package">built-in · cannot be deleted</button>'
          : '<button type="button" class="link-button" data-agent-delete="' + esc(a.id) + '">delete</button>');
      // Name bold, title as a muted subtitle beneath it — never the two run
      // together in one string.
      return '<article class="agent-card' + (a.enabled === false ? " disabled" : "") +
        '" data-testid="agent-card" data-agent="' + esc(a.id) + '">' +
        '<div class="agent-head">' + avatarSvg(a.id, 44) +
        '<div class="agent-ident"><b>' + esc(a.name) + "</b>" +
        '<span class="agent-role">' + esc(a.title || "") + "</span></div>" +
        '<span class="agent-orb" data-testid="lane-orb" aria-hidden="true"></span></div>' +
        '<div class="agent-meta">◧ ' + skills + "</div>" +
        team +
        '<div class="agent-meta">◈ ' + lessons + " lesson" + (lessons === 1 ? "" : "s") + " learned</div>" +
        broken +
        '<div class="agent-actions">' + actions + "</div>" +
        "</article>";
    }).join("");
  }

  function fillAgentPicker() {
    var picker = el("agent-picker");
    if (!picker) { return; }
    var chosen = picker.value;
    picker.innerHTML = '<option value="">Let the router decide</option>' +
      state.agents.filter(function (a) { return a.enabled !== false; }).map(function (a) {
        return '<option value="' + esc(a.id) + '">' + esc(a.name) +
          (a.title ? " — " + esc(a.title) : "") + "</option>";
      }).join("");
    if (chosen) { picker.value = chosen; }
  }

  function highlightAgentCard(slug) {
    if (!slug) { return; }
    var card = document.querySelector('[data-agent="' + String(slug).replace(/"/g, "") + '"]');
    if (!card) { return; }
    card.classList.add("just-saved");
    if (card.scrollIntoView) { card.scrollIntoView({ block: "center" }); }
    window.setTimeout(function () { card.classList.remove("just-saved"); }, 4000);
  }

  /* Who will actually run this, said BEFORE Run is pressed.
     Two channels can assign a run — the picker and an @slug in the goal — and
     until the operator could see which one won, the picker could say "let the
     router decide" while a leftover @slug quietly assigned somebody. */
  function mentionedSlug() {
    var match = /^\s*@([A-Za-z0-9_-]{1,64})\b/.exec(el("goal").value || "");
    return match ? match[1].toLowerCase() : "";
  }

  function agentLabel(agent) {
    return agent.name + (agent.title ? " · " + agent.title : "");
  }

  function updateAgentHint() {
    var hint = el("agent-resolved");
    if (!hint) { return; }
    var picker = el("agent-picker");
    var picked = picker ? picker.value : "";
    var mention = mentionedSlug();
    hint.className = "agent-resolved muted";
    if (picked) {
      var chosen = agentById(picked);
      hint.textContent = "will run as " + (chosen ? agentLabel(chosen) : picked) +
        (mention && mention !== picked ? " — the picker overrides @" + mention + " in the goal" : "");
      return;
    }
    if (!mention) {
      hint.textContent = "";
      return;
    }
    var resolved = agentById(mention);
    if (resolved) {
      hint.textContent = "will run as " + agentLabel(resolved) + " — from @" + mention + " in the goal";
      return;
    }
    // Named and unresolvable: say so here rather than letting the server refuse
    // it after the operator has committed to a run.
    hint.className = "agent-resolved bad";
    hint.textContent = "no agent @" + mention + " — this run will be refused; pick one above or fix the name";
  }

  function agentById(slug) {
    return state.agents.filter(function (a) { return a.id === slug; })[0] || null;
  }

  /* ------------------------------------------------------- the agent form */
  function renderChoices(hostId, names, checked, testidPrefix) {
    el(hostId).innerHTML = names.map(function (name) {
      var id = hostId + "-" + name;
      var on = checked.indexOf(name) !== -1 ? " checked" : "";
      var testid = testidPrefix ? ' data-testid="' + testidPrefix + esc(name) + '"' : "";
      return '<label for="' + esc(id) + '"><input type="checkbox" id="' + esc(id) + '"' + testid +
        ' value="' + esc(name) + '"' + on + " /> " + esc(name) + "</label>";
    }).join("");
  }

  function openAgentForm(agent, mode) {
    state.editing = agent ? agent.id : null;
    state.agentMode = mode || (agent ? "edit" : "create");
    el("agent-form-title").textContent =
      state.agentMode === "edit" ? "Edit agent" : state.agentMode === "duplicate" ? "Duplicate agent" : "New agent";
    el("agent-name").value = agent ? (state.agentMode === "duplicate" ? agent.name + " copy" : agent.name) : "";
    el("agent-title").value = agent ? agent.title || "" : "";
    el("agent-persona").value = agent ? agent.persona || "" : "";
    el("agent-body").value = agent ? agent.body || "" : "";
    var skillNames = Object.keys(state.skills);
    renderChoices("agent-skills", skillNames, agent ? agent.skills || [] : [], "agent-skill-");
    renderChoices("agent-tools", TOOL_NAMES, agent ? agent.tools || TOOL_NAMES : TOOL_NAMES, "agent-tool-");
    // An agent may never be on its own team, so it is not offered as an option —
    // the rule is enforced server-side, but a checkbox you can tick and that is
    // then refused is a worse way to learn it.
    var editing = state.agentMode === "edit" && agent ? agent.id : null;
    var candidates = state.agents
      .filter(function (a) { return a.id !== editing && a.enabled !== false; })
      .map(function (a) { return a.id; });
    if (candidates.length) {
      renderChoices("agent-team", candidates, agent ? agent.team || [] : [], "agent-team-");
    } else {
      el("agent-team").innerHTML =
        '<p class="muted">Create another agent first — a manager needs somebody to manage.</p>';
    }
    el("agent-form-error").textContent = "";
    el("agent-form").hidden = false;
    el("agent-name").focus();
  }

  function closeAgentForm() {
    el("agent-form").hidden = true;
    state.editing = null;
  }

  function checkedValues(hostId) {
    var boxes = el(hostId).querySelectorAll("input[type=checkbox]");
    return Array.prototype.filter.call(boxes, function (b) { return b.checked; })
      .map(function (b) { return b.value; });
  }

  function saveAgent(event) {
    if (event) { event.preventDefault(); }
    var payload = {
      name: el("agent-name").value.trim(),
      title: el("agent-title").value.trim(),
      persona: el("agent-persona").value.trim(),
      body: el("agent-body").value.trim(),
      skills: checkedValues("agent-skills"),
      tools: checkedValues("agent-tools"),
      team: checkedValues("agent-team")
    };
    if (!payload.name) { el("agent-form-error").textContent = "an agent needs a name"; return; }
    var editing = state.editing;
    var mode = state.agentMode;
    var request = mode === "edit"
      ? apiFetch("/api/agents/" + encodeURIComponent(editing), {
        method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload)
      })
      : apiFetch("/api/agents", {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload)
      });
    el("agent-save").disabled = true;
    request.then(function (r) {
      return r.json().then(function (body) { return { ok: r.ok, body: body }; });
    }).then(function (res) {
      el("agent-save").disabled = false;
      if (!res.ok) {
        // The form says why, in place — an error banner across the page for a
        // duplicate name is a jump-scare, not an explanation.
        el("agent-form-error").textContent =
          (res.body.error_tag || "BAD_REQUEST") + " — " + (res.body.message || "the agent was refused");
        return;
      }
      closeAgentForm();
      loadAgents().then(function () {
        var picker = el("agent-picker");
        // Only when the operator had not already chosen someone. Quietly
        // repointing a deliberate selection at whatever was just saved is how a
        // run gets handed to the wrong agent.
        if (picker && res.body.id && !picker.value) { picker.value = res.body.id; }
        // Duplicate is a two-step flow (prefill, then Save), so the result has
        // to be findable: scroll the new card into view and mark it, or the
        // operator is left scanning a roster for what just happened.
        highlightAgentCard(res.body.id);
      });
    }).catch(function (err) {
      el("agent-save").disabled = false;
      el("agent-form-error").textContent = String(err);
    });
  }

  function duplicateAgent(slug) {
    var agent = agentById(slug);
    if (agent) { openAgentForm(agent, "duplicate"); }
  }

  function deleteAgent(slug) {
    apiFetch("/api/agents/" + encodeURIComponent(slug), { method: "DELETE" })
      .then(function (r) {
        return r.json().then(function (body) { return { ok: r.ok, body: body }; });
      })
      .then(function (res) {
        if (!res.ok) {
          showError(res.body.error_tag || "BAD_REQUEST", res.body.message || "the agent was not deleted");
          return;
        }
        loadAgents();
      })
      .catch(function (err) { showError("INTERNAL_ERROR", String(err)); });
  }

  /* Clicking an agent filters what you are looking at to that agent — its runs
     and the lessons it learned. With a roster of one this is decoration; with a
     manager and a team it is how delegation becomes browsable. */
  function filterByAgent(slug) {
    state.agentFilter = state.agentFilter === slug ? "" : slug;
    Array.prototype.forEach.call(document.querySelectorAll("[data-agent]"), function (card) {
      card.classList.toggle("filtered", state.agentFilter === card.getAttribute("data-agent"));
    });
    renderAgentFilter();
  }

  function renderAgentFilter() {
    var host = el("agent-runs-filter");
    if (!host) { return; }
    if (!state.agentFilter) {
      host.hidden = true;
      host.textContent = "";
      return;
    }
    var agent = agentById(state.agentFilter);
    var name = agent ? agent.name : state.agentFilter;
    var lessons = agent && typeof agent.lessons === "number" ? agent.lessons : 0;
    host.hidden = false;
    host.innerHTML = "Showing <b>" + esc(name) + "</b> — " + lessons + " lesson" +
      (lessons === 1 ? "" : "s") + " learned. " +
      '<button type="button" class="link-button" id="agent-filter-clear">Show everyone</button>';
    var clear = el("agent-filter-clear");
    if (clear) { clear.addEventListener("click", function () { filterByAgent(state.agentFilter); }); }
  }

  function showWorkerAgent(payload) {
    var chip = el("worker-agent");
    if (!chip) { return; }
    if (!payload) {
      chip.hidden = true;
      chip.textContent = "";
      el("r-agent").textContent = "–";
      return;
    }
    var label = payload.name || payload.agent_id;
    chip.textContent = "◉ " + label + (payload.title ? " · " + payload.title : "");
    chip.hidden = false;
    el("r-agent").textContent = label;
  }

  /* --------------------------------------------------------------- busy */
  function setBusy(on) {
    var busy = el("busy");
    el("run-button").disabled = on;
    if (on) {
      state.busySince = new Date();
      busy.hidden = false;
      el("busy-text").textContent = "Working… since " + hhmmss(state.busySince);
      if (state.busyTimer) { clearInterval(state.busyTimer); }
      state.busyTimer = setInterval(function () {
        el("busy-text").textContent = "Working… since " + hhmmss(state.busySince);
      }, 1000);
    } else {
      busy.hidden = true;
      state.busySince = null;
      state.inflight = false;
        el("demo-button").disabled = false;
      if (state.busyTimer) { clearInterval(state.busyTimer); state.busyTimer = null; }
    }
  }

  var LANES = ["planner", "worker", "critic", "verifier", "deliverable"];

  function laneStatus(lane, text, cls) {
    var node = el("lane-" + lane);
    if (!node) {
      // A silent return here froze the Workers header on "○ Idle" for a whole
      // live run while every other lane lit up. A lane we cannot find is a bug
      // in this file, and it says so.
      showError("INTERNAL_ERROR", "dashboard lane '" + lane + "' is missing from the page");
      return;
    }
    node.querySelector("[data-status]").textContent = text;
    node.classList.remove("active", "pass", "fail");
    if (cls) { node.classList.add(cls); }
  }

  function highlightSkills(ids) {
    var chosen = {};
    (ids || []).forEach(function (id) { chosen[id] = true; });
    var nodes = document.querySelectorAll("[data-skill]");
    Array.prototype.forEach.call(nodes, function (node) {
      var on = !!chosen[node.getAttribute("data-skill")];
      node.classList.toggle("selected", on);
      var mark = node.querySelector(".skill-mark");
      if (on && !mark) {
        mark = document.createElement("span");
        mark.className = "skill-mark";
        // Never colour alone: an icon and a word, per the stylesheet's contract.
        mark.textContent = "▶ selected ";
        node.insertBefore(mark, node.firstChild);
      } else if (!on && mark) {
        mark.remove();
      }
    });
  }

  function num(value, fallback) {
    // `||` treats 0 and "" as missing, so a run that really used 0 rounds or
    // cost $0 rendered as "never arrived".
    return typeof value === "number" && isFinite(value) ? value : fallback;
  }

  function travel(n) {
    var t = el("token-" + n);
    if (!t) { return; }
    t.classList.remove("travelling");
    void t.offsetWidth;
    t.classList.add("travelling");
  }

  function showError(tag, message) {
    // The tag element carries the tag ALONE — machine-checkable, and the thing
    // a user can search for. Anything human-readable goes in the sibling.
    el("error-tag").textContent = tag || "INTERNAL_ERROR";
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
        : c.source === "operator" ? "from you"
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
      var delegated = state.delegations[id];
      var who = delegated
        ? '<div class="task-member" data-testid="task-member">◉ ' +
          esc(delegated.member_name || delegated.member) + "</div>"
        : "";
      return '<div class="task"><div class="task-title">' + mark + " " + esc(t.title || id) + "</div>" +
        who +
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
      // Every segment is encoded: an unencoded name with a space, '#' or '?' in
      // it broke the "click the file" beat, and '..' would be resolved by the
      // browser against the origin before the workspace guard ever saw it.
      var href = "/api/runs/" + encodeURIComponent(state.runId) + "/files/" + String(f.path).split("/").map(encodeURIComponent).join("/") + tokenQuery();
      var size = typeof f.bytes === "number" && isFinite(f.bytes) ? f.bytes + " bytes" : "size unknown";
      return '<li>▤ <a href="' + esc(href) + '" target="_blank" rel="noopener">' + esc(f.path) + "</a> " +
        '<span class="muted">(' + esc(size) + ")</span></li>";
    }).join("");
  }

  function renderReceipt(extra) {
    el("r-calls").textContent = state.calls;
    el("r-tokens").textContent = state.tokens;
    // Number() first: a string here used to throw inside handle(), which then
    // skipped setBusy(false) and left the spinner up for ever.
    el("r-cost").textContent = "$" + num(Number(state.cost), 0).toFixed(4);
    el("r-rounds").textContent = typeof state.rounds === "number" ? state.rounds : "–";
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
        laneStatus("planner", "● Planning", "active");
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
        // Mark the chosen packs in #skills-body (data-skill + classList), so the
        // Agent Skills column is not a static directory listing during a run.
        highlightSkills(p.skill_ids || (p.skills || []).map(function (s) { return s.id; }));
        var names = (p.skills || []).map(function (s) { return s.name + " (" + s.category + ")"; });
        var host = el("planner-body");
        host.innerHTML = '<p>◧ skills: ' + esc(names.join(", ") || (p.skill_ids || []).join(", ")) + "</p>" +
          (host.dataset.plan || "");
        break;
      }

      case "team.delegated": {
        // Delegation is the whole point of a manager, so it is visible on the
        // lane doing the work rather than only in the event log.
        state.delegations[p.task_id] = p;
        var chip = el("worker-agent");
        if (chip) {
          chip.textContent = "◉ " + (p.member_name || p.member) + " · delegated by " +
            (state.managerName || p.manager);
          chip.hidden = false;
        }
        renderTasks();
        break;
      }

      case "agent.assigned":
        state.managerName = p.name || p.agent_id;
        showWorkerAgent(p);
        el("run-id").textContent = (el("run-id").textContent || "") + " · @" + (p.agent_id || "");
        break;

      case "skill.selection_fallback":
        el("chip-skills").textContent = "◧ Fallback pack: " + (p.skill_id || "general-assistant");
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
        laneStatus("planner", "✓ Planned", "pass");
        travel(1);
        break;
      }

      case "worker.started":
        state.tasks[p.task_id] = state.tasks[p.task_id] || {};
        state.tasks[p.task_id].title = p.title;
        state.tasks[p.task_id].skill_id = p.skill_id;
        state.tasks[p.task_id].status = "running";
        laneStatus("worker", "● Working", "active");
        laneStatus("deliverable", "● Typing", "active");
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
        laneStatus("worker", "✓ Tasks complete", "pass");
        travel(2);
        break;
      }

      case "tool.write":
        state.files.push({ path: p.path, bytes: p.bytes });
        renderFiles();
        break;

      case "tool.error":
        // The banner shows the REASON only. The path the agent asked for is, for
        // an absolute-path attempt, a real filesystem path — redacted for keys,
        // not for /Users — and this banner is on a projector.
        showError(p.error_tag || "INTERNAL_ERROR", p.reason || "a tool call was refused");
        break;

      case "critic.verdict": {
        state.rounds = num(p.round, state.rounds);
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
        laneStatus("critic", p.pass ? "✓ All criteria pass" : "✕ " + (p.failures || []).length + " failing", p.pass ? "pass" : "fail");
        if (p.pass) { travel(3); }
        break;
      }

      case "repair.dispatched":
        state.rounds = num(p.round, state.rounds);
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
        laneStatus("verifier", p.verified ? "✓ Verified" : "✕ Rejected", p.verified ? "pass" : "fail");
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
        if ("deliverable" in p) { state.deliverable = p.deliverable; }
        el("deliverable").textContent = state.deliverable;
        state.rounds = num(p.rounds, state.rounds);
        state.cost = num(p.est_cost_usd, state.cost);
        state.tokens = num(p.tokens, state.tokens);
        state.calls = num(p.llm_calls, state.calls);
        if (Array.isArray(p.files)) { state.files = p.files; }
        renderFiles();
        renderReceipt(p);
        // A terminal event is not a signature. Only `verified === true` — the
        // same strict predicate the engine uses — turns this lane green.
        if (p.verified === true) {
          laneStatus("deliverable", "✓ Delivered", "pass");
        } else {
          laneStatus("deliverable", "✕ delivered but NOT verified", "fail");
          showError("INTERNAL_ERROR", "the run finished without a verifier sign-off");
        }
        setBusy(false);
        el("retry-button").hidden = true;
        break;

      case "run.failed":
        showError(p.error_tag || "INTERNAL_ERROR", p.message || "the run did not finish");
        laneStatus("deliverable", "✕ Failed", "fail");
        state.rounds = num(p.rounds, state.rounds);
        renderReceipt(p);
        setBusy(false);
        el("retry-button").hidden = false;
        break;

      case "worker.reset":
        // The stream dropped mid-deliverable and is being written again. Clear
        // what is on screen so attempt one is not welded to attempt two.
        state.deliverable = "";
        el("deliverable").textContent = "";
        laneStatus("worker", "↻ stream dropped — rewriting", "fail");
        break;

      case "verdict.incomplete":
        laneStatus(
          "critic",
          p.retry ? "◌ incomplete verdict — asking again" : "✕ incomplete verdict — treated as fail",
          p.retry ? "active" : "fail"
        );
        break;

      case "plan.pruned":
        el("planner-body").innerHTML =
          '<p class="muted">✂ plan capped: ' + esc(JSON.stringify(p.caps || {})) + "</p>" +
          el("planner-body").innerHTML;
        break;

      case "skill.declined":
        break;

      default:
        // A type we do not render is not an error, but a type nobody has ever
        // seen is worth saying out loud once, in the console, not on stage.
        if (window.console && console.debug) { console.debug("unhandled event", type, p); }
        break;
    }
  }

  /* ---------------------------------------------------------- streaming */
  function closeStream() {
    if (state.source) {
      state.source.close();
      state.source = null;
    }
  }

  function attach(runId) {
    if (typeof runId !== "string" || !runId) {
      // `/api/runs/undefined/events` is a 404 the EventSource retries for ever,
      // with the spinner up and Run disabled: the operator cannot even retry.
      showError("BAD_REQUEST", "the server did not return a run_id");
      setBusy(false);
      return;
    }
    closeStream();
    state.runId = runId;
    state.streamErrors = 0;
    var source = new EventSource(streamUrl(runId));
    state.source = source;
    EVENT_TYPES.forEach(function (type) {
      source.addEventListener(type, function (e) {
        var data;
        try {
          data = JSON.parse(e.data);
        } catch (err) {
          // A parse failure used to return silently — and because the browser
          // had already consumed the `id:` line, the reconnect's Last-Event-ID
          // skipped past the terminal event and the run never appeared to end.
          showError("INTERNAL_ERROR", "the event stream sent something this dashboard could not read");
          setBusy(false);
          closeStream();
          return;
        }
        var terminal = (type === "run.done" || type === "run.failed");
        try {
          handle(type, data);
        } finally {
          // A throw inside handle() must not be what keeps the stream open and
          // the spinner spinning. A replay keeps talking past run.done (the
          // lesson it saved is the last thing on the tape), so it closes on the
          // server ending the stream instead.
          if (terminal && !state.replay) { closeStream(); }
        }
      });
    });
    source.onerror = function () {
      state.streamErrors += 1;
      if (source.readyState === EventSource.CLOSED) {
        setBusy(false);
        return;
      }
      // readyState CONNECTING means the browser is retrying — which it will do
      // for ever against a 404 or a dead server, spinner up, Run disabled.
      if (state.streamErrors >= MAX_STREAM_ERRORS) {
        closeStream();
        setBusy(false);
        el("retry-button").hidden = false;
        showError("PROVIDER_UNAVAILABLE", "lost the event stream after several attempts");
      }
    };
  }

  function resetRun(goal) {
    // Close FIRST. Events from the previous run were still arriving into the
    // state we had just wiped, mixing two runs' deliverables together.
    if (state.source) { state.source.close(); state.source = null; }
    state.tasks = {}; state.cards = {}; state.dod = []; state.deliverable = "";
    state.files = []; state.calls = 0; state.tokens = 0; state.cost = 0; state.rounds = 0;
    state.goal = goal;
    state.streamErrors = 0;
    state.delegations = {}; state.managerName = "";
    el("deliverable").textContent = "";
    el("critic-cards").innerHTML = "";
    el("error-banner").hidden = true;
    el("retry-button").hidden = true;
    el("planner-body").dataset.plan = "";
    el("planner-body").innerHTML = '<p class="muted">Planning…</p>';
    el("workers-body").innerHTML = '<p class="muted">No tasks yet.</p>';
    el("dod-list").innerHTML = '<li class="muted">The Definition of Done appears once the Planner has run.</li>';
    highlightSkills([]);
    showWorkerAgent(null);
    renderFiles();
    LANES.forEach(function (l) { laneStatus(l, "\u25cb idle", null); });
  }

  function extraDod() {
    var raw = el("extra-dod") ? el("extra-dod").value : "";
    return raw.split("\n").map(function (line) { return line.trim(); })
      .filter(function (line) { return line.length > 0; });
  }

  /* An in-flight lock that is NOT the busy spinner. The spinner may only appear
     after a 2xx (the operator-vantage contract), but the button has to lock on
     the click itself — two clicks used to start two live runs against the real
     key, and their events interleaved in one dashboard. */
  function lockControls(on) {
    el("run-button").disabled = on;
    el("demo-button").disabled = on;
  }

  function endRequest() {
    state.inflight = false;
    el("demo-button").disabled = false;
    if (!state.busySince) { el("run-button").disabled = false; }
  }

  function startRun() {
    var goal = el("goal").value.trim();
    if (!goal) { el("goal").focus(); return; }
    if (state.inflight) { return; }
    state.inflight = true;
    lockControls(true);
    state.replay = false;
    resetRun(goal);
    var body = { goal: goal };
    var criteria = extraDod();
    if (criteria.length) { body.extra_dod = criteria; }
    var picker = el("agent-picker");
    // An explicit pick wins over an @slug prefix; the server honours the same
    // precedence, so the two cannot disagree.
    if (picker && picker.value) { body.agent_id = picker.value; }
    apiFetch("/api/runs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body)
    }).then(function (r) {
      return r.json().then(function (body) { return { ok: r.ok, status: r.status, body: body }; });
    }).then(function (res) {
      if (!res.ok) {
        showError(res.body.error_tag || "BAD_REQUEST", res.body.message || "the run was refused");
        endRequest();
        return;
      }
      setBusy(true);
      attach(res.body.run_id);
    }).catch(function (err) {
      showError("INTERNAL_ERROR", String(err));
      endRequest();
    });
  }

  function startDemo() {
    if (state.inflight) { return; }
    state.inflight = true;
    lockControls(true);
    state.replay = true;
    resetRun(el("goal").value.trim());
    apiFetch("/api/demo", { method: "POST" }).then(function (r) {
      return r.json().then(function (body) { return { ok: r.ok, body: body }; });
    }).then(function (res) {
      if (!res.ok) {
        showError(res.body.error_tag || "PROVIDER_NOT_CONFIGURED", res.body.message || "no recorded run available");
        endRequest();
        return;
      }
      if (res.body.goal) { el("goal").value = res.body.goal; }
      setBusy(true);
      attach(res.body.run_id);
    }).catch(function (err) {
      showError("INTERNAL_ERROR", String(err));
      endRequest();
    });
  }

  /* ------------------------------------------------------------- wiring */
  el("run-button").addEventListener("click", startRun);
  el("retry-button").addEventListener("click", startRun);
  el("demo-button").addEventListener("click", startDemo);
  el("error-dismiss").addEventListener("click", function () { el("error-banner").hidden = true; });
  el("agent-create").addEventListener("click", function () { openAgentForm(null, "create"); });
  el("agent-cancel").addEventListener("click", closeAgentForm);
  el("agent-form").addEventListener("submit", saveAgent);
  el("agents-list").addEventListener("click", function (e) {
    var edit = e.target.getAttribute("data-agent-edit");
    var dup = e.target.getAttribute("data-agent-duplicate");
    var del = e.target.getAttribute("data-agent-delete");
    if (edit) { openAgentForm(agentById(edit), "edit"); }
    if (dup) { duplicateAgent(dup); }
    if (del) { deleteAgent(del); }
    if (edit || dup || del) { return; }
    var card = e.target.closest ? e.target.closest("[data-agent]") : null;
    if (card) { filterByAgent(card.getAttribute("data-agent")); }
  });
  el("goal").addEventListener("keydown", function (e) {
    if ((e.metaKey || e.ctrlKey) && e.key === "Enter") { startRun(); }
  });
  el("goal").addEventListener("input", updateAgentHint);
  el("agent-picker").addEventListener("change", updateAgentHint);

  bootstrapToken().then(function () {
    // Skills first: the agent form's multi-select is built from the library.
    return loadSkills().then(loadAgents).then(loadHealth);
  }).then(function () {
    var params = new URLSearchParams(window.location.search);
    if (params.get("demo") === "1") { startDemo(); }
    var replay = params.get("replay");
    if (replay) { state.replay = true; resetRun(""); attach(replay); }
  });
})();
