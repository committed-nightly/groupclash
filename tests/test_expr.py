"""The expression evaluator.

If this is wrong the whole tool is wrong in the worst available way: it
reports confidently on an expression it misread. Most of these are about the
operators people actually write in a group, and about refusing to answer.
"""

from __future__ import annotations

import pytest

from groupclash.expr import (
    BOOL,
    EMPTY,
    FIXED,
    PER_REF,
    PER_RUN,
    UNKNOWN,
    Value,
    evaluate,
    tokenise,
    truthiness,
)


def test_plain_text_has_no_holes(value):
    assert value("deploy").kind == FIXED
    assert value("deploy").text == "deploy"


def test_literal_and_context_concatenate(pieces):
    parts = pieces("ci-${{ github.ref }}")
    assert [p.kind for p in parts] == [FIXED, PER_REF]
    assert parts[0].text == "ci-"


def test_workflow_name_is_known_text(value):
    # Not opaque: the cross-workflow check depends on two files with
    # different names not looking like the same group.
    assert value("${{ github.workflow }}", name="Release").text == "Release"


def test_head_ref_is_empty_off_a_pull_request(value):
    assert value("${{ github.head_ref }}", event="push").kind == EMPTY
    assert value("${{ github.head_ref }}", event="pull_request").kind == PER_REF


class TestFallback:
    """`||`, which is how every real group expression handles two events."""

    def test_empty_left_yields_right(self, value):
        assert value("${{ github.head_ref || github.ref }}", event="push").kind == PER_REF

    def test_non_empty_left_wins(self, value):
        got = value("${{ github.head_ref || github.ref }}", event="pull_request")
        assert got.source == "github.head_ref"

    def test_fallback_is_marked_as_such(self, value):
        # A run_id reached through a fallback is a deliberate opt-out; a bare
        # one is a mistake. The checks need to tell them apart.
        reached = value("${{ github.head_ref || github.run_id }}", event="push")
        assert reached.kind == PER_RUN and reached.from_fallback
        assert not value("${{ github.run_id }}", event="push").from_fallback

    def test_undecidable_left_poisons_the_result(self, value):
        assert value("${{ inputs.name || github.ref }}").kind == UNKNOWN


class TestComparison:
    def test_event_name_is_decidable(self, value):
        assert value("${{ github.event_name == 'push' }}").text == "true"
        assert value("${{ github.event_name == 'pull_request' }}").text == "false"

    def test_string_comparison_ignores_case(self, value):
        # GitHub's own rule, and the reason this is not a plain ==.
        assert value("${{ github.event_name == 'PUSH' }}").text == "true"

    def test_unknown_operand_is_undecidable(self, value):
        assert value("${{ matrix.os == 'ubuntu' }}").kind == UNKNOWN

    def test_the_ternary_idiom_resolves_per_event(self, value):
        template = "${{ github.event_name == 'pull_request' && github.head_ref || github.ref }}"
        assert value(template, event="pull_request").source == "github.head_ref"
        assert value(template, event="push").source == "github.ref"


class TestFormat:
    def test_arguments_keep_their_kinds(self, pieces):
        parts = pieces("${{ format('{0}-{1}', github.workflow, github.ref) }}")
        assert [p.kind for p in parts] == [FIXED, PER_REF]
        assert parts[0].text == "CI-"

    def test_missing_argument_is_not_guessed(self, value):
        assert value("${{ format('{0}-{1}', github.ref) }}").kind == UNKNOWN

    def test_non_literal_template_is_not_guessed(self, value):
        assert value("${{ format(matrix.fmt, github.ref) }}").kind == UNKNOWN


class TestRefusingToAnswer:
    """Everything here must come out UNKNOWN rather than a plausible guess."""

    @pytest.mark.parametrize(
        "template",
        [
            "${{ matrix.os }}",
            "${{ inputs.environment }}",
            "${{ needs.setup.outputs.key }}",
            "${{ env.NAME }}",
            "${{ vars.THING }}",
            "${{ hashFiles('a') }}",
            "${{ github.event.some.unknown.thing }}",
            "${{ github.event.inputs.name }}",
            "${{ fromJSON(github.event.x)[0] }}",
        ],
    )
    def test_undecidable(self, value, template):
        assert value(template).kind == UNKNOWN

    def test_unparseable_expression_is_unknown_not_a_crash(self, value):
        assert value("${{ this is @@ not an expression }}").kind == UNKNOWN

    def test_trailing_tokens_are_unknown(self, value):
        assert value("${{ github.ref github.sha }}").kind == UNKNOWN


def test_tokenise_rejects_a_stray_character():
    with pytest.raises(Exception):
        tokenise("github.ref @ 3")


class TestTruthiness:
    def test_empty_is_false(self):
        assert truthiness(Value(EMPTY)) is False

    def test_the_string_false_is_true(self):
        # A string is truthy whatever it says; only the boolean false is not.
        assert truthiness(Value(FIXED, text="false")) is True
        assert truthiness(Value(BOOL, text="false")) is False

    def test_zero_is_false_as_a_number_only(self):
        assert truthiness(Value(FIXED, text="0", numeric=True)) is False
        assert truthiness(Value(FIXED, text="0")) is True

    def test_unknown_is_undecided(self):
        assert truthiness(Value(UNKNOWN)) is None


def test_empty_pieces_keep_their_source(pieces):
    # Dropping these would cost the empty-group finding its explanation.
    parts = pieces("${{ github.head_ref }}", event="push")
    assert parts[0].kind == EMPTY
    assert parts[0].source == "github.head_ref"


def test_evaluate_handles_a_template_with_no_holes():
    assert evaluate("plain", lambda path: Value(UNKNOWN))[0].text == "plain"
