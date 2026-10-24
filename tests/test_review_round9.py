"""Ninth review: zero-subject follow-ups share the pronoun follow-up lexical focus."""

import pytest

from rag_workbench.application.retrieval_expansion import coverage_focus
from rag_workbench.application.teaching import contextual_query, is_followup
from rag_workbench.domain.models import ChatMessage


@pytest.mark.parametrize(
    "question,expected",
    [
        ("为什么这样？", "王伦 这样"),
        ("到期后呢？", "王伦 到期后"),
    ],
)
def test_zero_pronoun_followups_use_previous_subject_for_coverage(question, expected):
    history = [ChatMessage(role="user", content="王伦是谁？")]
    assert is_followup(question)
    context = contextual_query(question, history)
    assert coverage_focus(question, context) == expected


def test_zero_pronoun_followup_without_named_subject_keeps_question():
    question = "为什么这样？"
    assert coverage_focus(question, f"随便聊聊\n{question}") == question
