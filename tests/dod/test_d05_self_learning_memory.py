"""How this could pass while broken: memory.recalled echoing last-inserted lesson_id without the planner ever seeing the text (F8); now an isolated temp SQLite must save T from run1, recall that exact id on run2, and T must appear verbatim in run2's planner prompt transcript."""

from __future__ import annotations

import tempfile
from pathlib import Path

from _harness import (
    collect_sse,
    event_payload,
    events_of,
    live_xai_base_url_ok,
    load_prompts,
    recalled_lesson_ids,
    require_live,
    spawn_serve,
    start_run,
    write_json,
)

RUN1_GOAL = (
    "Write a four-bullet briefing on why agent orchestration beats a chatbox. "
    "Always cite sources in bullets."
)
RUN2_GOAL = (
    "Write a four-bullet briefing on why a critic-and-verifier loop beats a single assistant."
)


def test_d05_lesson_from_run1_injected_into_run2_planner():
    require_live()
    assert live_xai_base_url_ok()

    data_dir = Path(tempfile.mkdtemp(prefix="omniagentos-d5-"))
    srv = spawn_serve(data_dir=data_dir)
    try:
        rid1 = start_run(srv.base_url, RUN1_GOAL)
        ev1 = collect_sse(srv.base_url, rid1, timeout_s=180.0)
        saved = events_of(ev1, "lesson.saved")
        assert saved, "run1 produced no lesson.saved (Reflector runs only on done+verified)"
        payload = event_payload(saved[-1])
        assert str(payload.get("run_id")) == str(rid1), (
            f"lesson.saved.run_id={payload.get('run_id')!r} != run1 {rid1}"
        )
        text_t = payload.get("text")
        assert isinstance(text_t, str) and text_t.strip(), "lesson.saved.text empty"
        assert len(text_t) <= 300, f"lesson text length {len(text_t)} > 300"

        lesson_id = payload.get("id") or payload.get("lesson_id")
        if lesson_id is None:
            # Recover from GET /api/lessons
            import httpx

            lessons = httpx.get(srv.base_url + "/api/lessons", timeout=15.0).json()
            items = lessons.get("items") or lessons.get("lessons") or lessons
            if isinstance(items, dict):
                items = items.get("items") or []
            match = [x for x in items if str(x.get("run_id")) == str(rid1)]
            assert match, "GET /api/lessons has no lesson for run1"
            lesson_id = match[0]["id"]
            if not text_t:
                text_t = match[0]["text"]
        lesson_id = str(lesson_id)

        rid2 = start_run(srv.base_url, RUN2_GOAL)
        ev2 = collect_sse(srv.base_url, rid2, timeout_s=180.0)
        recalled = events_of(ev2, "memory.recalled")
        assert recalled, "run2 missing memory.recalled"
        rec_payload = event_payload(recalled[0])
        assert "matched" in rec_payload, "memory.recalled.matched must be explicit (even if 0)"
        ids = recalled_lesson_ids(recalled[0])
        assert ids, (
            "memory.recalled lesson ids empty — empty recall MUST fail D5, not pass vacuously (F8)"
        )
        assert ids == [lesson_id] or lesson_id in ids, (
            f"memory.recalled.lesson_ids={ids!r} does not contain run1 lesson {lesson_id}"
        )
        # Prefer exact == [run1 id] when only one is recalled.
        if rec_payload.get("matched") == 1:
            assert ids == [lesson_id], f"matched==1 but ids={ids} != [{lesson_id}]"

        transcript = load_prompts(data_dir, rid2)
        # Planner prompt must contain T verbatim inside <recalled_lesson> (causal injection).
        assert text_t in transcript, (
            "run1 lesson text T does not appear verbatim in run2 prompts.jsonl "
            "(id match alone is F8)"
        )
        assert "<recalled_lesson>" in transcript, (
            "PLANNER prompt must wrap lessons in <recalled_lesson> tags"
        )
        write_json(
            "d5-memory.json",
            {
                "run1": rid1,
                "run2": rid2,
                "lesson_id": lesson_id,
                "text_len": len(text_t),
                "recalled_ids": ids,
            },
        )
    finally:
        srv.stop()
