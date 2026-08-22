# DEMO.md — stage runbook

Three goals, proven live, each showing a different part of the loop. Read
this once before you're on stage; the beats are written to be said, not read
off a screen.

## DoD-parseable goals (machine-read by tests/dod/_harness.py::parse_demo_goals — keep byte-for-byte identical to the copy-paste blocks below)

```dod-goals
Write 3 Meta feed ad headlines for a $149 12-week strength program for women over 40. Each headline must be 30 characters or fewer, no exceptions.
Policy: Refunds are available within 30 days of purchase for unused subscriptions. After 30 days, no refunds are issued except for billing errors, which are refunded in full once verified. Goal: A customer emailed asking for a refund on a subscription they bought 38 days ago, no billing error involved. Draft the reply, citing the specific policy clause for the decision.
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
- [ ] `omniagentos demo` has been test-run as the fallback path — if the
      provider hiccups live, you switch to this without missing a beat.
- [ ] Browser zoom is up (this is a stage, not a laptop) and the dashboard
      tab is already open at `http://127.0.0.1:8486`.

---

## Goal 1 — marketing-content (shows the loop repairing itself)

Copy-paste exactly:

```
Write 3 Meta feed ad headlines for a $149 12-week strength program for
women over 40. Each headline must be 30 characters or fewer, no exceptions.
```

**What the audience sees, beat by beat:**
1. You hit Run — the Planner lane lights up, the goal token appears.
2. The marketing-content skill panel highlights ("skill: Ad Copy Framework
   Writer") — this goal was matched to a real skill pack, not written from
   scratch.
3. Worker lane streams the three headlines in live.
4. Critic lane flips **red** — round 1 headlines are over the 30-character
   limit (the skill's own QUALITY CHECKS catch this, not a hidden rule).
   The red card shows the exact criterion, the offending headline, and the
   fix note.
5. Repair dispatches automatically — only the failing task reruns, not the
   whole plan.
6. Critic card flips **green**, before/after comparison visible.
7. Verifier lane passes; deliverable panel renders the final three headlines.

**What you say:** "Watch the critic card — it just failed round one on its
own character-limit check, and the system repaired it without me touching
anything. That's the loop the platform's whole pitch rests on: it doesn't
stop at a bad first draft."

---

## Goal 2 — customer-support (shows grounding on real policy text)

First paste this policy into the goal box as context, then the ask:

```
Policy: Refunds are available within 30 days of purchase for unused
subscriptions. After 30 days, no refunds are issued except for billing
errors, which are refunded in full once verified.

Goal: A customer emailed asking for a refund on a subscription they bought
38 days ago, no billing error involved. Draft the reply, citing the
specific policy clause for the decision.
```

**What the audience sees:** deliverable panel types out a reply that
explicitly cites "after 30 days, no refunds" — not a generic apology. The
quality-gate checklist shows the "from skill: Onboarding Welcome Sequence /
customer-support" attribution line so the audience can see which skill's
standards graded this output.

**What you say:** "It's not guessing at policy — it's grounded on the exact
text I gave it, and it has to cite the clause it used. That's the
difference between an assistant that sounds confident and one that's
actually checkable."

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

**What you say:** "Every one of those is an actual file, written by the
workspace tool, in a sandboxed directory scoped to this one run. Nothing
this agent does can touch anything outside it."

---

## White-label swap beat (do this between goals, or at the end)

```bash
OMNIAGENTOS_BRAND_NAME="Acme Inc" OMNIAGENTOS_BRAND_LOGO=/path/to/acme-logo.png ./start.sh
```

Refresh the dashboard — header logo and name change instantly, no rebuild,
no fork.

**What you say:** "Same engine, your brand — this is what a client sees if
you're running this white-labeled for them."

---

## Recovery beats (rehearse these — they will happen eventually)

- **Provider hiccup mid-demo** (timeout, 5xx, rate limit): don't stall —
  say "let's not burn stage time waiting on a network blip" and run
  `omniagentos demo` in a second terminal/tab. It replays a real recorded
  run at the same paced speed, so the audience sees the identical loop
  without waiting on a live model.
- **A run ends `run.failed`**: the error banner will show the specific
  `error_tag` (not a generic "something went wrong"). Point at it, then hit
  **Retry this goal** — the button is right there on the failed run, no
  retyping.
- **Nothing loads / server not responding**: switch to the pre-recorded
  fallback the same way as the provider hiccup — `omniagentos demo` needs
  no key and no network.
