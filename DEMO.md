# DEMO.md — stage runbook

Three goals, proven live, each showing a different part of the loop. Read
this once before you're on stage; the beats are written to be said, not read
off a screen.

## DoD-parseable goals (machine-read by tests/dod/_harness.py::parse_demo_goals — keep byte-for-byte identical to the copy-paste blocks below)

```dod-goals
Write 3 Meta feed ad copy variants for a $149 12-week strength program for women over 40 using the PAS framework. Each variant: primary text under 125 characters, one headline under 40 characters, and a labelled P/A/S breakdown. ||| dod: Every variant must contain the exact phrase 'Stronger at 40+'
Policy: Refunds are available within 30 days of purchase for unused subscriptions. After 30 days, no refunds are issued except for billing errors, which are refunded in full once verified. Goal: A customer emailed asking for a refund on a subscription they bought 38 days ago, no billing error involved. Draft the reply, citing the specific policy clause for the decision. ||| agent: riley-meal-prep-support
Draft a 5-email onboarding sequence for OmniAgentOS Starter and save each email as a separate file named email-1.md, email-2.md, email-3.md, email-4.md, and email-5.md in the workspace.
```

---

## Framing line (say this before you type anything)

> "This is the open orchestration engine — the loop, the skills format, the
> local dashboard, running on my own machine with my own key. The platform
> adds 10,000+ agents, SeeDance and voice generation, and white-label
> hosting on top of this same engine — but everything you're about to see is
> the real thing, not a mockup."

## Pre-flight checklist (do this before you're live)

- [ ] A provider key is set (`XAI_API_KEY` preferred; `OPENROUTER_API_KEY` or
      `OPENAI_API_KEY` as fallback) in the shell you'll launch from.
- [ ] `./start.sh` (or `start.ps1`) has been run once already today so the
      venv/install step doesn't eat stage time — `omniagentos serve --open`
      should come up in under 5 seconds.
- [ ] One throwaway test run has already been done end-to-end today, so you
      know the provider is actually answering right now.
- [ ] The dashboard's **Replay demo** control (next to Run) has been
      test-clicked once on the already-running stage server — that is the
      fallback if the provider hiccups live, not a second `omniagentos demo`
      process (that one works fine on its own port now, but it's a second
      dashboard on the projector and costs you the beat — see the Recovery
      beats below).
- [ ] Browser zoom is up (this is a stage, not a laptop) and the dashboard
      tab is already open at `http://127.0.0.1:8486`.
- [ ] Remember: workspace file links open in a NEW TAB (`target=_blank`) —
      plan to open just one file for goal 3, then switch back; don't click
      all five on a projector.

---

## Beat 0 — Agents (30s: create a named agent, hand it goal 2)

1. Click **Agents** in the nav (scrolls to the Agents section). Point at
   the roster cards (Cole/Sales Closer, Ava/Support Rep, Max/Content
   Writer, Nora/Researcher, Sage/Ops Assistant, plus the built-in General
   Worker) — say: "these ship built in; you can also make your own." Each
   card has Edit / Duplicate / delete; skip past Duplicate for this beat —
   it opens a prefilled copy of the form, not an instant card, and still
   needs an explicit Save.
2. Click **Create**. Fill in: name `Riley`, title `Meal-Prep Support`,
   persona a sentence or two about handling meal-plan subscription
   support, skill: check **Refund Request Handler**. Save. The slug is
   built from both fields together — name `Riley` + title `Meal-Prep
   Support` produces `riley-meal-prep-support`, which is what goal 2's
   assignment below expects.
3. The new roster card appears immediately — no reload.
4. Go to the goal box for Goal 2 (below). Pick **Riley** from the
   **Assign to** dropdown, or type `@riley-meal-prep-support` at the start
   of the goal text — both work, and a picker choice always wins if both
   are present. Either way, watch the line under the goal box: it says
   "will run as Riley · Meal-Prep Support" the moment the assignment
   resolves, before you press Run — that's the on-screen proof of who's
   about to do the work. (An @mention that doesn't resolve to a real agent
   is refused outright before the run starts — it's a hard stop, not a
   silent miss — so a mistyped slug can't quietly ship as an unassigned
   run with the stray text stuck in the reply.)

**What you say:** "I didn't just get an agent that happens to be good at
this — I built one, gave it a name, and told it which skill to carry. It
remembers what it learns, separately from every other agent."

---

## Goal 1 — marketing-content (shows the loop repairing itself)

Copy-paste exactly:

```
Write 3 Meta feed ad copy variants for a $149 12-week strength program for
women over 40 using the PAS framework. Each variant: primary text under 125
characters, one headline under 40 characters, and a labelled P/A/S breakdown.
```

Acceptance criteria → paste into the Acceptance criteria field:

```
Every variant must contain the exact phrase 'Stronger at 40+'
```

The goal box only asks for what the Ad Copy Framework Writer pack actually
writes (variants, not bare headlines) — the 'Stronger at 40+' phrase rule
lives in the separate Acceptance criteria field, which the Worker never sees,
only the Critic does. That's deliberate: it's what makes the repair loop show
up reliably on stage, on round 1, every time — not the pack's own framework
or character-limit checks, which the goal is now written to satisfy on its own.

**What the audience sees, beat by beat:**
1. You hit Run — the Planner lane lights up, the goal token appears.
2. The marketing-content skill panel highlights ("skill: Ad Copy Framework
   Writer") — this goal is matched to, and assigned, that real skill pack.
3. Worker lane streams three ad copy variants in live — Worker-blind, it
   has no idea the phrase-matching rule exists.
4. Critic lane flips **red** — driven by the hidden Acceptance criterion
   (the phrase rule) the Worker never saw. The quality-gate checklist also
   grades the pack's own checks — primary-text/headline character limits
   and a labelled P/A/S breakdown — attributed "from skill: Ad Copy
   Framework Writer". The red card shows the exact failing criterion, the
   offending variant, and the fix note.
5. Repair dispatches automatically — only the failing task reruns, not the
   whole plan.
6. Critic card flips **green**, before/after comparison visible.
7. Verifier lane passes; deliverable panel renders the final three variants.

**What you say:** "Watch the critic card — it just failed the variants
against a rule the writer never even saw, and the system repaired it
without me touching anything. That's the production line catching what a
single prompt would have shipped."

---

## Goal 2 — customer-support (shows grounding on real policy text, run by the agent from Beat 0)

Before you paste the goal in: from the **Assign to** picker, pick **Riley**
(the agent you created in Beat 0) — or type `@riley-meal-prep-support` at
the start of the goal box and watch the "will run as Riley" line confirm
it. Then paste this policy as context, then the ask:

```
Policy: Refunds are available within 30 days of purchase for unused
subscriptions. After 30 days, no refunds are issued except for billing
errors, which are refunded in full once verified.

Goal: A customer emailed asking for a refund on a subscription they bought
38 days ago, no billing error involved. Draft the reply, citing the
specific policy clause for the decision.
```

**What the audience sees:** the Worker lane carries Riley's chip, not a
generic "Worker" label — this run is Riley's, not the router's pick.
Deliverable panel types out a reply that explicitly cites the 30-day policy
clause — not a generic apology. The quality-gate checklist shows the "from
skill: Refund Request Handler / customer-support" attribution line, and its
checks are the real reason the citation is mandatory: that pack's QUALITY
CHECKS require a decision of Approved/Denied/Escalate, a cited policy
clause on any denial, and a correct days-since-purchase calculation — so
the audience is watching the actual gate, not a narrated one.

**What you say:** "This is Riley's run, not the router's guess — I assigned
it. It's not guessing at policy either — it's grounded on the exact text I
gave it, and it has to cite the clause it used. That's the difference
between an assistant that sounds confident and one that's actually
checkable."

---

## Goal 3 — operations (shows the live workspace filling with files)

```
Draft a 5-email onboarding sequence for OmniAgentOS Starter and save each
email as a separate file named email-1.md, email-2.md, email-3.md,
email-4.md, and email-5.md in the workspace.
```

**What the audience sees:** the workspace file tree panel — empty at the
start of this goal — fills in live as each worker task finishes, one file
appearing per email. By the end there are 5 files sitting in a real
directory on disk, not a single markdown blob.

Each filename in the tree is a link that opens in a new tab
(`target=_blank`) — click just one (e.g. `email-1.md`) to show it's real,
then switch back to the dashboard tab. Don't open all five in a row on a
projector; five new tabs stacking up mid-demo is its own distraction.

**What you say:** "Every one of those is an actual file, written by the
workspace tool, in a sandboxed directory scoped to this one run. Nothing
this agent does can touch anything outside it."

---

## Beat 4 — Teams, optional (60s: one goal, two specialists, one director)

Not in the ```dod-goals fence — the oracle doesn't parse a manager
assignment yet (checked `tests/dod/_harness.py` before writing this; only
`||| dod:` and `||| agent:` exist there today). Cut this beat first if
you're tight on time; the three numbered goals above are the ones that are
mechanically proven.

Pick **Studio Director** (Remy) from the **Assign to** picker — not a
worker this time, a manager — then paste:

```
Score whether a $149 12-week strength program for women over 40 is a
promising niche, and separately write 3 Meta feed ad copy variants
promoting it using the PAS framework.
```

**What the audience sees:** the Planner lane frames the plan in Remy's
voice, then the Workers lane shows **two** chips lighting up together —
Nora (Researcher) and Max (Content Writer) — each carrying a
"delegated by studio-director" tag, each running under its own skill and
persona, not Remy's. The deliverable panel assembles both halves — the
niche score and the ad variants — into one answer.

**What you say:** "This isn't one generalist trying to do two different
jobs. Remy split the brief, handed each half to the specialist who
actually does that kind of work, and is reviewing both before it counts
as done — a director, not a chatbot pretending to be a whole team."

---

## White-label swap beat (do this between goals, or at the end)

Brand is resolved once at server startup, not on refresh, and
`OMNIAGENTOS_BRAND_LOGO` must be something the browser can fetch (a URL, or
a file already copied into `assets/`) — a bare filesystem path 404s. Prep
the logo file into `assets/` *before* you're on stage, then to do this beat
live: stop the running server (Ctrl+C in its terminal), restart with the
brand env set, and reload the page — do not start a second server on the
same port while the first is still up.

```bash
# one-time prep, before you're live:
cp /path/to/acme-logo.png assets/acme-logo.png

# on stage: Ctrl+C the running server first, then:
OMNIAGENTOS_BRAND_NAME="Acme Inc" OMNIAGENTOS_BRAND_LOGO="/assets/acme-logo.png" ./start.sh
```

Reload the dashboard — header logo and name have changed, no rebuild, no
fork. (It's a restart, not a live refresh — say so if asked.)

**What you say:** "Same engine, your brand — this is what a client sees if
you're running this white-labeled for them."

---

## Recovery beats (rehearse these — they will happen eventually)

- **Provider hiccup mid-demo** (timeout, 5xx, rate limit): don't stall —
  say "let's not burn stage time waiting on a network blip" and click
  **Replay demo** (next to Run) on the dashboard that's already up. It
  replays a real recorded run at the same paced speed on the SAME server,
  so the audience sees the identical loop without waiting on a live model
  and without touching a terminal.
  **Do not** run `omniagentos demo` as a separate command here. It works —
  it starts its own server on port 8487 (falling back to an ephemeral port
  if 8487 is busy) rather than colliding with the stage server on 8486 —
  but it puts a second dashboard on the projector and costs you the beat
  for nothing. The button is on the screen already; use it.
- **A run ends `run.failed`**: the error banner will show the specific
  `error_tag` (not a generic "something went wrong"). Point at it, then hit
  **Retry this goal** — the button is right there on the failed run, no
  retyping.
- **Nothing loads / server not responding** (the whole process died, not
  just the provider): this is the one case where a fresh `omniagentos demo`
  process is the right call — the crashed server has released its port, so
  there's no collision. Run it in a terminal; it needs no key and no
  network and replays the same recorded run standalone.
