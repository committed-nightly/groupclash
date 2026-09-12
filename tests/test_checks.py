"""The checks, and -- more importantly -- what they decline to report.

A checker that cries wolf gets deleted from a CI config within a fortnight,
so roughly half of these are arrangements that look wrong and are not.
"""

from __future__ import annotations

from groupclash.core import (
    EMPTY_GROUP,
    IGNORES_REF,
    NEVER_CANCELS,
    SHARED_GROUP,
)

from conftest import workflow


class TestEmptyGroup:
    def test_head_ref_alone_on_a_push(self, run, kinds):
        report = run(workflow(on="[push]", group="${{ github.head_ref }}"))
        assert kinds(report) == [EMPTY_GROUP]

    def test_the_message_names_the_context_that_was_empty(self, run):
        report = run(workflow(on="[push]", group="${{ github.head_ref }}"))
        assert "github.head_ref is not set" in report.findings[0].message

    def test_only_for_the_events_it_is_empty_on(self, run):
        report = run(workflow(on="[push, pull_request]", group="${{ github.head_ref }}"))
        assert report.findings[0].events == ["push"]

    def test_reported_even_with_cancelling_off(self, run, kinds):
        # An empty group is inert whatever cancel-in-progress says.
        report = run(workflow(on="[push]", group="${{ github.head_ref }}", cancel="false"))
        assert kinds(report) == [EMPTY_GROUP]


class TestIgnoresRef:
    def test_a_constant_group(self, run, kinds):
        report = run(workflow(on="[push, pull_request]", group="ci"))
        assert kinds(report) == [IGNORES_REF]

    def test_the_partly_empty_group_that_looks_fine(self, run, kinds):
        # The one this tool exists for: reads as per-branch, is not.
        report = run(
            workflow(on="[push]", group="${{ github.workflow }}-${{ github.head_ref }}")
        )
        assert kinds(report) == [IGNORES_REF]
        assert "'CI-'" in report.findings[0].message

    def test_base_ref_does_not_separate_pull_requests(self, run, kinds):
        report = run(workflow(on="[pull_request]", group="pr-${{ github.base_ref }}"))
        assert kinds(report) == [IGNORES_REF]

    def test_pull_request_target_ref_is_the_base_branch(self, run, kinds):
        report = run(
            workflow(
                on="[pull_request_target]",
                group="${{ github.workflow }}-${{ github.ref }}",
            )
        )
        assert kinds(report) == [IGNORES_REF]
        assert "branch being merged into" in report.findings[0].message

    def test_events_are_gathered_into_one_finding(self, run):
        report = run(workflow(on="[push, pull_request]", group="ci"))
        assert len(report.findings) == 1
        assert report.findings[0].events == ["push", "pull_request"]


class TestIgnoresRefDeclines:
    """Constant groups that are not bugs."""

    def test_cancelling_off_is_how_deploys_are_serialised(self, run, kinds):
        report = run(workflow(on="[push, pull_request]", group="prod", cancel="false"))
        assert kinds(report) == []

    def test_shorthand_form_implies_cancelling_off(self, run, kinds):
        text = "name: CI\non: [push]\nconcurrency: prod\njobs:\n  a:\n    runs-on: ubuntu-latest\n    steps: [{run: 'true'}]\n"
        assert kinds(run(text)) == []

    def test_a_single_branch_has_nothing_to_be_constant_across(self, run, kinds):
        text = (
            "name: Deploy\non:\n  push:\n    branches: [main]\n"
            "concurrency:\n  group: prod\n  cancel-in-progress: true\n"
            "jobs:\n  a:\n    runs-on: ubuntu-latest\n    steps: [{run: 'true'}]\n"
        )
        assert kinds(run(text)) == []

    def test_a_wildcard_branch_filter_is_many_branches(self, run, kinds):
        text = (
            "name: Deploy\non:\n  push:\n    branches: [release/**]\n"
            "concurrency:\n  group: prod\n  cancel-in-progress: true\n"
            "jobs:\n  a:\n    runs-on: ubuntu-latest\n    steps: [{run: 'true'}]\n"
        )
        assert kinds(run(text)) == [IGNORES_REF]

    def test_a_schedule_only_ever_runs_on_one_ref(self, run, kinds):
        text = (
            "name: Nightly\non:\n  schedule:\n    - cron: '0 0 * * *'\n"
            "concurrency:\n  group: nightly\n  cancel-in-progress: true\n"
            "jobs:\n  a:\n    runs-on: ubuntu-latest\n    steps: [{run: 'true'}]\n"
        )
        assert kinds(run(text)) == []

    def test_a_manual_dispatch_alone_is_a_deliberate_one_at_a_time(self, run, kinds):
        # `group: ${{ github.workflow }}` on a dispatch-only publish workflow
        # is the idiom for "one of these at a time". A dispatch can run on any
        # ref, but two of them overlapping takes two deliberate clicks.
        # Checked against real repositories, this was the largest single
        # source of false positives.
        assert kinds(run(workflow(on="[workflow_dispatch]", group="ci"))) == []

    def test_but_a_dispatch_alongside_pull_request_is_still_caught(self, run):
        report = run(workflow(on="[workflow_dispatch, pull_request]", group="ci"))
        assert [f.kind for f in report.findings] == [IGNORES_REF]
        assert report.findings[0].events == ["pull_request"]

    def test_an_upstream_run_id_is_a_key_not_a_unique_value(self, run, kinds):
        # Re-running the upstream workflow delivers the same workflow_run.id
        # again, so there is something for cancel-in-progress to cancel.
        text = (
            "name: After\non:\n  workflow_run:\n    workflows: [Build]\n    types: [completed]\n"
            "concurrency:\n  group: ${{ github.workflow }}-${{ github.event.workflow_run.id }}\n"
            "  cancel-in-progress: true\n"
            "jobs:\n  a:\n    runs-on: ubuntu-latest\n    steps: [{run: 'true'}]\n"
        )
        assert kinds(run(text)) == []

    def test_a_per_ref_group_is_the_point_of_the_feature(self, run, kinds):
        assert kinds(run(workflow(on="[push]", group="${{ github.ref }}"))) == []

    def test_the_documented_two_event_idiom(self, run, kinds):
        report = run(
            workflow(
                on="[push, pull_request]",
                group="${{ github.workflow }}-${{ github.head_ref || github.ref }}",
            )
        )
        assert kinds(report) == []


class TestNeverCancels:
    def test_run_id_in_the_group(self, run, kinds):
        report = run(workflow(on="[push]", group="${{ github.workflow }}-${{ github.run_id }}"))
        assert kinds(report) == [NEVER_CANCELS]

    def test_sha_moves_every_push(self, run, kinds):
        report = run(workflow(on="[push]", group="build-${{ github.sha }}"))
        assert kinds(report) == [NEVER_CANCELS]

    def test_a_fallback_to_run_id_is_a_deliberate_opt_out(self, run, kinds):
        # GitHub's own recommended way of disabling concurrency for pushes.
        report = run(
            workflow(on="[push, pull_request]", group="${{ github.head_ref || github.run_id }}")
        )
        assert kinds(report) == []

    def test_not_reported_when_nothing_would_be_cancelled_anyway(self, run, kinds):
        report = run(workflow(on="[push]", group="${{ github.run_id }}", cancel="false"))
        assert kinds(report) == []


class TestSharedGroup:
    def test_two_workflows_with_the_same_expression(self, run, kinds):
        report = run(
            ci=workflow(name="CI", group="${{ github.ref }}"),
            release=workflow(name="Release", group="${{ github.ref }}"),
        )
        assert kinds(report) == [SHARED_GROUP]

    def test_the_message_names_the_other_file(self, run):
        report = run(
            ci=workflow(name="CI", group="${{ github.ref }}"),
            release=workflow(name="Release", group="${{ github.ref }}"),
        )
        assert ".github/workflows/release.yml" in report.findings[0].message

    def test_workflow_name_keeps_two_files_apart(self, run, kinds):
        # The reason the comparison is on evaluated shape and not on text.
        report = run(
            ci=workflow(name="CI", group="${{ github.workflow }}-${{ github.ref }}"),
            release=workflow(name="Release", group="${{ github.workflow }}-${{ github.ref }}"),
        )
        assert kinds(report) == []

    def test_identical_constants_in_two_files(self, run, kinds):
        report = run(
            a=workflow(name="A", on="[push]", group="deploy", cancel="false"),
            b=workflow(name="B", on="[pull_request]", group="deploy", cancel="false"),
        )
        assert kinds(report) == [SHARED_GROUP]

    def test_a_run_id_group_cannot_collide_with_anything(self, run, kinds):
        report = run(
            a=workflow(name="A", group="${{ github.run_id }}", cancel="false"),
            b=workflow(name="B", group="${{ github.run_id }}", cancel="false"),
        )
        assert kinds(report) == []

    def test_the_same_sha_is_shared_between_workflows(self, run, kinds):
        # Unlike run_id: one push, two workflows, one github.sha.
        report = run(
            a=workflow(name="A", group="${{ github.sha }}", cancel="false"),
            b=workflow(name="B", group="${{ github.sha }}", cancel="false"),
        )
        assert kinds(report) == [SHARED_GROUP]

    def test_different_events_do_not_share_a_symbolic_group(self, run, kinds):
        # github.ref on a push and on a pull_request are never the same
        # string, so claiming a collision would be wrong.
        report = run(
            a=workflow(name="A", on="[push]", group="${{ github.ref }}", cancel="false"),
            b=workflow(name="B", on="[pull_request]", group="${{ github.ref }}", cancel="false"),
        )
        assert kinds(report) == []

    def test_one_workflow_alone_is_not_a_collision(self, run, kinds):
        assert kinds(run(workflow(group="${{ github.ref }}"))) == []


class TestJobLevel:
    def test_a_job_concurrency_block_is_checked(self, run, kinds):
        text = (
            "name: CI\non: [push, pull_request]\njobs:\n"
            "  deploy:\n    runs-on: ubuntu-latest\n"
            "    concurrency:\n      group: ci\n      cancel-in-progress: true\n"
            "    steps: [{run: 'true'}]\n"
        )
        report = run(text)
        assert kinds(report) == [IGNORES_REF]
        assert report.findings[0].where == "jobs.deploy.concurrency.group"

    def test_github_job_is_known_inside_a_job(self, run, kinds):
        # Two jobs in one file are not compared, but github.job must still
        # resolve rather than making the whole group undecidable.
        text = (
            "name: CI\non: [push]\njobs:\n"
            "  deploy:\n    runs-on: ubuntu-latest\n"
            "    concurrency:\n      group: ${{ github.job }}\n      cancel-in-progress: true\n"
            "    steps: [{run: 'true'}]\n"
        )
        report = run(text)
        assert kinds(report) == [IGNORES_REF]
        assert report.undecided == []

    def test_github_job_at_workflow_level_is_undecidable(self, run):
        report = run(workflow(group="${{ github.job }}"))
        assert [u.reason for u in report.undecided] == ["github.job"]


class TestUndecided:
    def test_an_unreadable_group_is_reported_as_unchecked(self, run):
        report = run(workflow(group="${{ matrix.os }}"))
        assert report.findings == []
        assert len(report.undecided) == 1
        assert report.undecided[0].reason == "matrix.os"

    def test_a_compound_expression_names_itself(self, run):
        # The `||` that gave up does not know which operand was to blame, so
        # without this the report reads "cannot decide an expression".
        report = run(workflow(group="${{ contains(github.x, 'y') && 'a' || 'b' }}"))
        assert report.undecided[0].reason == "contains(github.x, 'y') && 'a' || 'b'"

    def test_undecided_is_per_event(self, run):
        report = run(workflow(on="[push, pull_request]", group="${{ inputs.x }}"))
        assert [u.event for u in report.undecided] == ["push", "pull_request"]


class TestParsing:
    def test_the_on_key_is_a_boolean_in_yaml(self, run, kinds):
        # `on:` parses as True. If this regresses, every workflow looks like
        # it has no triggers and the tool silently reports nothing.
        report = run(workflow(on="[push, pull_request]", group="ci"))
        assert kinds(report) == [IGNORES_REF]

    def test_quoted_on_key_works_too(self, run, kinds):
        text = (
            "name: CI\n'on': [push, pull_request]\n"
            "concurrency:\n  group: ci\n  cancel-in-progress: true\n"
            "jobs:\n  a:\n    runs-on: ubuntu-latest\n    steps: [{run: 'true'}]\n"
        )
        assert kinds(run(text)) == [IGNORES_REF]

    def test_a_workflow_with_no_concurrency_is_no_finding(self, run, kinds):
        text = "name: CI\non: [push]\njobs:\n  a:\n    runs-on: ubuntu-latest\n    steps: [{run: 'true'}]\n"
        report = run(text)
        assert kinds(report) == [] and report.blocks == 0

    def test_a_workflow_with_no_triggers_is_skipped(self, run, kinds):
        text = "name: CI\nconcurrency:\n  group: ci\n  cancel-in-progress: true\njobs: {}\n"
        assert kinds(run(text)) == []

    def test_counts(self, run):
        report = run(workflow(name="A"), workflow(name="B"))
        assert (report.workflows, report.blocks) == (2, 2)
