"""What each context value is worth, for each event.

This table is the tool. Everything else is parsing and printing.

The question it answers, for one context path and one event, is not "what is
this" but "does this tell two runs apart, and which two". `github.ref` is a
different answer on `push` (the branch), on `pull_request` (the PR's merge
ref) and on `pull_request_target` (the *base* branch, identical for every
pull request into main -- which is the finding people are most surprised by).
"""

from __future__ import annotations

from dataclasses import dataclass

from .expr import (
    EMPTY,
    FIXED,
    OTHER,
    PER_COMMIT,
    PER_REF,
    PER_RUN,
    UNKNOWN,
    Value,
)

#: The two events that carry a head ref. GitHub is explicit that
#: `github.head_ref` is set for these and empty everywhere else -- including
#: on `pull_request_review`, which does have a pull request attached.
HEAD_REF_EVENTS = ("pull_request", "pull_request_target")

#: Events that carry a pull request number in `github.event.number`.
PULL_REQUEST_EVENTS = (
    "pull_request",
    "pull_request_target",
    "pull_request_review",
    "pull_request_review_comment",
)

#: Events GitHub only ever runs against the default branch. For these,
#: `github.ref` is the same string every time, so a group built from it is no
#: more specific than a constant -- and, just as importantly, a constant group
#: is no *worse* than `github.ref`. Both facts are used below.
DEFAULT_BRANCH_EVENTS = (
    "schedule",
    "workflow_run",
    "issues",
    "issue_comment",
    "discussion",
    "discussion_comment",
    "release",
    "repository_dispatch",
    "deployment",
    "deployment_status",
    "watch",
    "fork",
    "public",
    "label",
    "milestone",
    "project",
    "project_card",
    "project_column",
    "status",
    "registry_package",
    "page_build",
    "gollum",
    "member",
)


@dataclass(frozen=True)
class Site:
    """Where a concurrency block is, which fixes some context values."""

    workflow_name: str
    repository: str = "owner/repo"
    job: str | None = None


def _ref_kind(event: str) -> str:
    if event in ("pull_request", "merge_group"):
        # refs/pull/<n>/merge -- one per pull request.
        return PER_REF
    if event == "pull_request_target":
        # The base branch. Every pull request into main sees refs/heads/main.
        return OTHER
    if event in DEFAULT_BRANCH_EVENTS:
        return OTHER
    # push, workflow_dispatch, create, delete, and anything we do not know.
    return PER_REF


def _sha_kind(event: str) -> str:
    if event in ("pull_request_target",) or event in DEFAULT_BRANCH_EVENTS:
        # The head of some branch, which moves on its own schedule rather
        # than once per run of this workflow.
        return OTHER
    return PER_COMMIT


#: Context paths whose value never varies between runs of one workflow.
CONSTANT: dict[str, str] = {
    "github.repository": "repository",
    "github.repository_owner": "owner",
    "github.repository_id": "repository-id",
    "github.workflow": "workflow",
    "github.event_name": "event",
    "github.job": "job",
    "github.workspace": "constant",
    "github.server_url": "constant",
    "github.api_url": "constant",
    "github.graphql_url": "constant",
    "github.workflow_ref": "workflow",
    "github.workflow_sha": "constant",
}

#: Different for every run, so no two runs ever share a group containing one.
PER_RUN_PATHS = ("github.run_id", "github.run_number", "github.run_attempt")

#: Non-empty, but no help in telling one branch or pull request from another.
NOT_DISCRIMINATING = (
    "github.actor",
    "github.actor_id",
    "github.triggering_actor",
    "github.ref_type",
    "github.ref_protected",
)

#: Contexts that exist but whose contents we have no way to predict.
OPAQUE_ROOTS = (
    "inputs",
    "matrix",
    "needs",
    "env",
    "vars",
    "secrets",
    "steps",
    "runner",
    "job",
    "strategy",
)


def resolver(event: str, site: Site):
    """A `resolve` callback for `expr.evaluate`, bound to one event."""

    def resolve(path: str) -> Value:
        return classify(path, event, site)

    return resolve


def classify(path: str, event: str, site: Site) -> Value:
    """What one context path is worth under one event."""
    root = path.split(".")[0]

    if root in OPAQUE_ROOTS:
        return Value(UNKNOWN, source=path)

    if path in CONSTANT:
        return _constant(path, event, site)
    if path in PER_RUN_PATHS:
        return Value(PER_RUN, source=path)
    if path in NOT_DISCRIMINATING:
        return Value(OTHER, source=path)

    if path in ("github.ref", "github.ref_name"):
        return Value(_ref_kind(event), source=path)
    if path == "github.sha":
        return Value(_sha_kind(event), source=path)
    if path == "github.head_ref":
        if event in HEAD_REF_EVENTS:
            return Value(PER_REF, source=path)
        return Value(EMPTY, source=path)
    if path == "github.base_ref":
        if event in HEAD_REF_EVENTS:
            # The branch being merged *into*: the same for most pull requests
            # in a repository, so it groups them together rather than apart.
            return Value(OTHER, source=path)
        return Value(EMPTY, source=path)

    if root == "github":
        return _event_payload(path, event)

    return Value(UNKNOWN, source=path)


def _constant(path: str, event: str, site: Site) -> Value:
    """A constant, with its actual text where we know it.

    Knowing the text matters for the cross-workflow check and nowhere else:
    two workflows that both say `${{ github.workflow }}` are in *different*
    groups, and a comparison that treated the expression as opaque would
    report them as colliding.
    """
    if path == "github.workflow":
        return Value(FIXED, text=site.workflow_name, source=path)
    if path == "github.event_name":
        return Value(FIXED, text=event, source=path)
    if path == "github.repository":
        return Value(FIXED, text=site.repository, source=path)
    if path == "github.repository_owner":
        return Value(FIXED, text=site.repository.split("/")[0], source=path)
    if path == "github.job":
        if site.job is None:
            # There is no job context around a workflow-level concurrency
            # block. GitHub does not error, it just has nothing to put there.
            return Value(UNKNOWN, source=path)
        return Value(FIXED, text=site.job, source=path)
    return Value(OTHER, source=path)


def _event_payload(path: str, event: str) -> Value:
    """`github.event.*`, which is only as predictable as the event is."""
    if path in ("github.event.number", "github.event.pull_request.number"):
        if event in PULL_REQUEST_EVENTS:
            return Value(PER_REF, source=path)
        return Value(EMPTY, source=path)
    if path == "github.event.pull_request.head.ref":
        if event in PULL_REQUEST_EVENTS:
            return Value(PER_REF, source=path)
        return Value(EMPTY, source=path)
    if path == "github.event.issue.number":
        if event in ("issues", "issue_comment"):
            return Value(PER_REF, source=path)
        return Value(EMPTY, source=path)
    if path == "github.event.workflow_run.head_branch":
        if event == "workflow_run":
            return Value(PER_REF, source=path)
        return Value(EMPTY, source=path)
    if path == "github.event.workflow_run.id":
        if event == "workflow_run":
            return Value(PER_RUN, source=path)
        return Value(EMPTY, source=path)
    if path == "github.event.inputs" or path.startswith("github.event.inputs."):
        return Value(UNKNOWN, source=path)
    return Value(UNKNOWN, source=path)


def single_ref_event(event: str, config) -> bool:
    """Whether every run of this event happens on the same ref.

    When it does, a constant group is not a bug: there is only one branch for
    it to be constant across. `on: push: branches: [main]` with
    `group: deploy` behaves exactly like `group: ${{ github.ref }}`, and
    reporting it would be noise -- which is how a checker stops being run.

    Note this is a different question from the one about which branch GitHub
    *reads the workflow from*. A `workflow_dispatch` is listed from the
    default branch but runs against whichever ref you pick, so it is not
    single-ref here even though the file has to be on the default branch.
    """
    if event in DEFAULT_BRANCH_EVENTS:
        return True
    if event != "push":
        return False
    if not isinstance(config, dict):
        return False
    if config.get("tags") or config.get("tags-ignore"):
        return False
    branches = config.get("branches")
    if not isinstance(branches, list):
        branches = [branches] if isinstance(branches, str) else []
    if len(branches) != 1 or not isinstance(branches[0], str):
        return False
    return not any(character in branches[0] for character in "*?[]!+")
