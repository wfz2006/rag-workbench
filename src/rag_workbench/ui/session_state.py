"""Transient browser state, collected on the Streamlit thread on every page."""

from dataclasses import dataclass

import streamlit as st

from rag_workbench.domain.models import AnswerResult, utc_now


@dataclass(frozen=True)
class UnsavedTurn:
    key: int
    question: str
    result: AnswerResult
    created_at: str
    replaces_assistant_id: int | None = None


@dataclass(frozen=True)
class RunningTurn:
    job: object


def _next_key() -> int:
    key = st.session_state.get("unsaved_turn_sequence", 0)
    st.session_state["unsaved_turn_sequence"] = key + 1
    return key


def collect_finished_jobs() -> bool:
    """Idempotently reap finished jobs, including those completed on another page."""
    jobs = st.session_state.setdefault("answer_jobs", {})
    unsaved = st.session_state.setdefault("unsaved_turns", {})
    changed = False
    for session, turns in list(unsaved.items()):
        # Preserve transient answers across an in-process reload from 0.1.1/0.1.2.
        if isinstance(turns, tuple):
            turns = [turns]
        if any(not isinstance(turn, UnsavedTurn) for turn in turns):
            unsaved[session] = [
                turn
                if isinstance(turn, UnsavedTurn)
                else UnsavedTurn(
                    turn.key,
                    turn.question,
                    turn.result,
                    turn.created_at,
                    getattr(turn, "replaces_assistant_id", None),
                )
                if hasattr(turn, "key")
                else UnsavedTurn(_next_key(), turn[0], turn[1], utc_now())
                for turn in turns
            ]
            changed = True
    for session, job in list(jobs.items()):
        snapshot = job.snapshot()
        if not snapshot.done:
            continue
        if snapshot.result and snapshot.result.error_code == "persistence_error":
            unsaved.setdefault(session, []).append(
                UnsavedTurn(
                    key=_next_key(),
                    question=snapshot.question,
                    result=snapshot.result,
                    created_at=getattr(snapshot, "created_at", "") or utc_now(),
                    replaces_assistant_id=getattr(job, "replaces_assistant_id", None),
                )
            )
        del jobs[session]
        changed = True
    return changed


def monitor_answer_jobs() -> None:
    # Disable interval execution when no jobs exist, avoiding idle polling.
    @st.fragment(
        run_every=0.5 if st.session_state.get("answer_jobs") else None, key="answer_job_monitor"
    )
    def monitor() -> None:
        if collect_finished_jobs():
            st.rerun()

    monitor()


def conversation_timeline(messages: list[dict], unsaved: list[UnsavedTurn], job=None) -> list:
    entries = [(m["created_at"], 0, m["id"], m) for m in messages]
    overlays = [(turn.replaces_assistant_id, turn.created_at, turn.key, turn) for turn in unsaved]
    if job:
        overlays.append(
            (getattr(job, "replaces_assistant_id", None), job.created_at, 0, RunningTurn(job))
        )
    for target, created, key, overlay in overlays:
        position = next((i for i, m in enumerate(messages) if m["id"] == target), None)
        if target is not None and position is not None and position > 0:
            user = messages[position - 1]
            entries = [
                entry for entry in entries if entry[2] not in {user["id"], target} or entry[1] != 0
            ]
            entries = [
                entry
                for entry in entries
                if not (entry[0] == user["created_at"] and entry[2] == user["id"])
            ]
            entries.append((user["created_at"], 0, user["id"], overlay))
        else:
            entries.append((created, 1, key, overlay))
    return [entry[3] for entry in sorted(entries, key=lambda entry: entry[:3])]


def discard_unsaved_turn(session_id: str, key: int) -> None:
    unsaved = st.session_state.get("unsaved_turns", {})
    remaining = [turn for turn in unsaved.get(session_id, []) if turn.key != key]
    if remaining:
        unsaved[session_id] = remaining
    else:
        unsaved.pop(session_id, None)
