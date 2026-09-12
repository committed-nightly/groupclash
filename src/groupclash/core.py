"""The checks.

Four things can be wrong with a concurrency group, and three of them look
identical in the file:

    empty-group       it expands to nothing for some event it receives
    group-ignores-ref it is the same string for every branch and pull request
    never-cancels     it is different for every run, so nothing is ever cancelled
    shared-group      two workflows are quietly in the same group

Every check is made once per *event*, because a group is not right or wrong on
its own -- it is right for the event it was written for and usually wrong for
the one that got added underneath it later.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .contexts import Site, resolver, single_ref_event
from .expr import (
    EMPTY,
    KNOWN_TEXT,
    OTHER,
    PER_COMMIT,
    PER_REF,
    PER_RUN,
    UNKNOWN,
    Value,
    collapse,
    evaluate,
    truthiness,
)
from .workflows import Concurrency

EMPTY_GROUP = "empty-group"
IGNORES_REF = "group-ignores-ref"
NEVER_CANCELS = "never-cancels"
SHARED_GROUP = "shared-group"

ORDER = [EMPTY_GROUP, IGNORES_REF, NEVER_CANCELS, SHARED_GROUP]


@dataclass
class Finding:
    kind: str
    path: str
    where: str
    group: str
    events: list[str]
    message: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "path": self.path,
            "where": self.where,
            "group": self.group,
            "events": self.events,
            "message": self.message,
        }


@dataclass
class Undecided:
    path: str
    where: str
    event: str
    reason: str


@dataclass
class Report:
    findings: list[Finding] = field(default_factory=list)
    undecided: list[Undecided] = field(default_factory=list)
    workflows: int = 0
    blocks: int = 0

    def sorted_findings(self) -> list[Finding]:
        return sorted(
            self.findings, key=lambda f: (ORDER.index(f.kind), f.path, f.where)
        )


@dataclass
class Form:
    """One group expression, evaluated under one event."""

    decl: Concurrency
    event: str
    segments: list[Value]

    @property
    def decided(self) -> bool:
        return not any(value.kind == UNKNOWN for value in self.segments)

    @property
    def unknown_sources(self) -> list[str]:
        return [v.source or "an expression" for v in self.segments if v.kind == UNKNOWN]

    @property
    def is_empty(self) -> bool:
        """Nothing at all survives -- GitHub is handed an empty group."""
        return all(
            value.kind == EMPTY or (value.kind in KNOWN_TEXT and value.text == "")
            for value in self.segments
        )

    @property
    def empty_sources(self) -> list[str]:
        return [v.source for v in self.segments if v.kind == EMPTY and v.source]

    def kinds(self) -> set[str]:
        return {value.kind for value in self.segments}

    @property
    def unique_parts(self) -> list[Value]:
        """Parts that make the group different for every run."""
        return [v for v in self.segments if v.kind in (PER_RUN, PER_COMMIT)]

    @property
    def deliberate_opt_out(self) -> bool:
        """Every unique part came from an `||` fallback.

        `${{ github.head_ref || github.run_id }}` on a push is the documented
        way of saying "do not apply concurrency to pushes". It is not the same
        mistake as writing `${{ github.run_id }}` and expecting cancellation,
        so it is not reported.
        """
        parts = self.unique_parts
        return bool(parts) and all(value.from_fallback for value in parts)

    def render(self) -> str:
        return "".join(value.render() for value in self.segments)

    @property
    def fully_known(self) -> bool:
        return all(value.kind in KNOWN_TEXT for value in self.segments)

    @property
    def other_parts(self) -> list[str]:
        """Sources that are non-empty but say nothing about which ref this is."""
        return sorted({v.source for v in self.segments if v.kind == OTHER and v.source})

    def key(self) -> tuple:
        # Empty pieces contribute no text, so they cannot make two groups
        # different from each other. Dropping them here keeps
        # `${{ github.head_ref }}ci` on a push comparable to a plain `ci`.
        return tuple(
            value.identity for value in self.segments if value.kind != EMPTY
        )


def site_for(decl: Concurrency, repository: str) -> Site:
    return Site(
        workflow_name=decl.workflow_name, repository=repository, job=decl.job
    )


def decide_cancel(decl: Concurrency, event: str, site: Site) -> bool | None:
    """Whether `cancel-in-progress` is on, as far as we can tell.

    None means it is an expression we could not decide. The checks treat that
    as "it might be", and say so, because a group that cancels the wrong runs
    on some branches is still a group that cancels the wrong runs.
    """
    raw = decl.cancel_in_progress
    if isinstance(raw, bool):
        return raw
    if raw is None:
        return False
    if not isinstance(raw, str):
        return None
    value = collapse(evaluate(raw, resolver(event, site)))
    if value.kind in KNOWN_TEXT:
        if value.text.lower() in ("false", ""):
            return False
        if value.text.lower() == "true":
            return True
    return truthiness(value)


def forms_for(decl: Concurrency, repository: str) -> list[Form]:
    site = site_for(decl, repository)
    return [
        Form(decl=decl, event=event, segments=evaluate(decl.group, resolver(event, site)))
        for event in decl.triggers
    ]


def _join(names: list[str]) -> str:
    if not names:
        return ""
    if len(names) == 1:
        return names[0]
    return ", ".join(names[:-1]) + " and " + names[-1]


def _cancel_clause(cancel: bool | None) -> str:
    """The sentence that says what the cancelling actually costs."""
    if cancel is None:
        return (
            "cancel-in-progress is an expression here, so wherever it is true a "
            "run on one ref cancels a run on any other"
        )
    return (
        "cancel-in-progress is true, so a run on one ref cancels a run on any other"
    )


def check_declaration(decl: Concurrency, repository: str, report: Report) -> list[Form]:
    """Everything decidable about one concurrency block.

    Returns the forms that are eligible for the cross-workflow comparison,
    which happens later because it needs every workflow at once.
    """
    site = site_for(decl, repository)
    comparable: list[Form] = []

    # Findings are collected per kind and reported once, naming every event
    # they apply to. The same group is usually wrong in the same way for
    # several events, and four near-identical lines is how output gets
    # skimmed instead of read.
    buckets: dict[str, list[tuple[Form, bool | None]]] = {}

    for form in forms_for(decl, repository):
        if not form.decided:
            report.undecided.append(
                Undecided(
                    path=decl.path,
                    where=decl.where,
                    event=form.event,
                    reason=_join(sorted(set(form.unknown_sources))),
                )
            )
            continue

        cancel = decide_cancel(decl, form.event, site)

        if form.is_empty:
            buckets.setdefault(EMPTY_GROUP, []).append((form, cancel))
            continue

        comparable.append(form)

        if form.unique_parts:
            if not form.deliberate_opt_out and cancel is not False:
                buckets.setdefault(NEVER_CANCELS, []).append((form, cancel))
            continue

        if PER_REF in form.kinds():
            continue

        if cancel is False:
            # A fixed group with cancellation off is how you serialise
            # deployments, and it is the one arrangement here that is usually
            # deliberate. Left alone.
            continue

        if single_ref_event(form.event, decl.triggers.get(form.event)):
            # Only one ref can ever run this, so a fixed group is exactly as
            # specific as github.ref would have been.
            continue

        buckets.setdefault(IGNORES_REF, []).append((form, cancel))

    for kind, entries in buckets.items():
        report.findings.append(_finding(kind, decl, entries))

    # Only PER_RUN rules a group out of colliding with another workflow.
    # PER_COMMIT does not: two workflows triggered by the same push see the
    # same github.sha, so a group built from it puts them together.
    return [form for form in comparable if PER_RUN not in form.kinds()]


def _finding(kind: str, decl: Concurrency, entries: list[tuple[Form, bool | None]]) -> Finding:
    events = [form.event for form, _ in entries]
    form, cancel = entries[0]
    on = "on " + _join(events)

    if kind == EMPTY_GROUP:
        sources = sorted({source for f, _ in entries for source in f.empty_sources})
        because = (
            f" -- {_join(sources)} is not set for that event"
            if len(sources) == 1
            else (f" -- {_join(sources)} are not set for that event" if sources else "")
        )
        message = (
            f"{on} this expands to an empty group{because}. GitHub does not "
            "document what an empty group means; what people report is that no "
            "concurrency is applied at all, so this block does nothing for "
            "those events"
        )
    elif kind == NEVER_CANCELS:
        sources = sorted({v.source for f, _ in entries for v in f.unique_parts if v.source})
        message = (
            f"{on} the group contains {_join(sources) or 'a value'}, which is "
            "different for every run, so no two runs are ever in the same "
            "group. cancel-in-progress is set and there is nothing it can ever "
            "cancel"
        )
    elif kind == IGNORES_REF:
        if form.fully_known:
            what = f"{on} this is {form.render()!r} for every branch and pull request"
        else:
            others = sorted({source for f, _ in entries for source in f.other_parts})
            what = f"{on} nothing in this group varies by branch or pull request"
            if others:
                verb = "does" if len(others) == 1 else "do"
                what += f" ({_join(others)} {verb} not)"
        message = f"{what}. {_cancel_clause(cancel)}"
        # Worth spelling out, because it is the one that surprises people who
        # got the rest of this right.
        if any(
            f.event == "pull_request_target" and "github.ref" in f.other_parts
            for f, _ in entries
        ):
            message += (
                ". On pull_request_target, github.ref is the branch being "
                "merged into, not the pull request's own ref"
            )
    else:  # pragma: no cover - shared-group is built in check()
        message = ""

    return Finding(
        kind=kind,
        path=decl.path,
        where=decl.where,
        group=decl.group,
        events=events,
        message=message,
    )


def _cross_workflow(forms: list[Form], report: Report) -> None:
    """Groups that two different workflows both land in.

    Concurrency groups are scoped to the *repository*, not to the workflow --
    which is the single least-known thing about the feature. Copying
    `group: ${{ github.ref }}` into three workflow files puts all three in one
    group per branch, and two of them will spend their lives cancelling the
    third.

    Comparison is by evaluated shape, not by source text: `${{ github.workflow }}`
    is known text and differs per file, so the common
    `${{ github.workflow }}-${{ github.ref }}` does not trip this.
    """
    buckets: dict[tuple, list[Form]] = {}
    for form in forms:
        buckets.setdefault(form.key(), []).append(form)

    for forms_here in buckets.values():
        for group in _comparable_groups(forms_here):
            paths = sorted({form.decl.path for form in group})
            if len(paths) < 2:
                continue
            first = min(group, key=lambda f: (f.decl.path, f.decl.where))
            others = sorted(
                {
                    f"{form.decl.path} ({form.decl.where})"
                    for form in group
                    if form.decl.path != first.decl.path
                }
            )
            events = sorted({form.event for form in group})
            report.findings.append(
                Finding(
                    kind=SHARED_GROUP,
                    path=first.decl.path,
                    where=first.decl.where,
                    group=first.decl.group,
                    events=events,
                    message=(
                        f"on {_join(events)} this is the same group as "
                        f"{_join(others)}. Concurrency groups are scoped to the "
                        "repository, not to the workflow, so these are one group "
                        "between them"
                    ),
                )
            )


def _comparable_groups(forms: list[Form]) -> list[list[Form]]:
    """Split same-shape forms into sets that really can collide.

    Two forms with the same shape only collide if they evaluate to the same
    string. When the shape is entirely known text that is certain whatever the
    events are. When it is not -- `${{ github.ref }}` -- it is only safe to
    claim for one event at a time, since `github.ref` on a push and on a
    pull_request are never the same string.
    """
    if forms and forms[0].fully_known:
        return [forms]
    by_event: dict[str, list[Form]] = {}
    for form in forms:
        by_event.setdefault(form.event, []).append(form)
    return list(by_event.values())


def check(files: dict[str, str], repository: str = "owner/repo") -> Report:
    """Check every workflow in `files`, given as {repo-relative path: text}."""
    from .workflows import load

    report = Report()
    comparable: list[Form] = []

    for path in sorted(files):
        workflow, blocks = load(files[path], path)
        report.workflows += 1
        report.blocks += len(blocks)
        for decl in blocks:
            if not decl.triggers:
                continue
            comparable.extend(check_declaration(decl, repository, report))

    _cross_workflow(comparable, report)
    return report
