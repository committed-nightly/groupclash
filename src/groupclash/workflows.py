"""Reading workflow files, and finding the concurrency blocks in them.

Two things here are worth knowing before you touch this file.

The first is that in YAML 1.1, which is what PyYAML implements, the bare word
``on`` is a **boolean**. The key every workflow file in the world starts with
does not come back as ``"on"``; it comes back as ``True``. A tool that looks
it up by name sees no triggers anywhere and reports nothing, cheerfully.

The second is that ``cancel-in-progress`` can itself be an expression, and a
group is only a problem when it is true. It is kept raw here and decided
later, per event, because that is the only place there is enough information
to decide it.
"""

from __future__ import annotations

import posixpath
from dataclasses import dataclass, field
from typing import Any

import yaml

WORKFLOW_DIR = ".github/workflows"
WORKFLOW_SUFFIXES = (".yml", ".yaml")


class WorkflowError(ValueError):
    """A file that could not be read as a workflow."""


@dataclass
class Concurrency:
    """One `concurrency:` block, workflow level or job level."""

    path: str
    #: The workflow's name as GitHub knows it, which is what
    #: `${{ github.workflow }}` expands to.
    workflow_name: str
    #: None for a workflow-level block, otherwise the job id.
    job: str | None
    group: str
    cancel_in_progress: Any
    #: {event: config} for the workflow this block is in.
    triggers: dict[str, Any] = field(default_factory=dict)

    @property
    def where(self) -> str:
        """How this block is named in the report."""
        if self.job is None:
            return "concurrency.group"
        return f"jobs.{self.job}.concurrency.group"


@dataclass
class Workflow:
    path: str
    declared_name: str | None
    triggers: dict[str, Any]
    jobs: dict[str, Any]
    raw: dict[str, Any] = field(repr=False, default_factory=dict)

    @property
    def display_name(self) -> str:
        """What GitHub calls this workflow, and what `github.workflow` is.

        With no `name:`, GitHub falls back to the workflow's path.
        """
        return self.declared_name if self.declared_name else self.path


def is_workflow_path(path: str) -> bool:
    """Whether a repo-relative path is a file GitHub reads as a workflow.

    GitHub only looks in .github/workflows itself, never in a subdirectory of
    it -- a file one level down is silently not a workflow at all.
    """
    if not path.endswith(WORKFLOW_SUFFIXES):
        return False
    return posixpath.dirname(path) == WORKFLOW_DIR


def trigger_block(document: dict[str, Any]) -> Any:
    """The value of the `on:` key, whatever YAML decided that key was."""
    if "on" in document:
        return document["on"]
    return document.get(True)


def normalise_triggers(raw: Any) -> dict[str, Any]:
    """The `on:` value as {event: config}, for all three spellings."""
    if raw is None:
        return {}
    if isinstance(raw, str):
        return {raw: {}}
    if isinstance(raw, list):
        return {event: {} for event in raw if isinstance(event, str)}
    if isinstance(raw, dict):
        return {
            event: ({} if config is None else config)
            for event, config in raw.items()
            if isinstance(event, str)
        }
    return {}


def parse(text: str, path: str) -> Workflow:
    """Parse one workflow file. Raises WorkflowError if it is not one."""
    try:
        document = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise WorkflowError(f"{path}: not valid YAML: {exc}") from exc

    if document is None:
        raise WorkflowError(f"{path}: file is empty")
    if not isinstance(document, dict):
        raise WorkflowError(f"{path}: top level is not a mapping")

    declared = document.get("name")
    jobs = document.get("jobs")

    return Workflow(
        path=path,
        declared_name=declared if isinstance(declared, str) and declared else None,
        triggers=normalise_triggers(trigger_block(document)),
        jobs=jobs if isinstance(jobs, dict) else {},
        raw=document,
    )


def _block(raw: Any) -> tuple[str, Any] | None:
    """A `concurrency:` value as (group, cancel-in-progress).

    The shorthand `concurrency: ci` is the same as
    `concurrency: {group: ci}`, and means cancel-in-progress is off.
    """
    if isinstance(raw, str):
        return (raw, False)
    if isinstance(raw, dict):
        group = raw.get("group")
        if not isinstance(group, (str, int, float, bool)):
            return None
        return (str(group), raw.get("cancel-in-progress", False))
    return None


def concurrency_blocks(workflow: Workflow, document: dict[str, Any]) -> list[Concurrency]:
    """Every concurrency block in one workflow, workflow level first."""
    found: list[Concurrency] = []

    block = _block(document.get("concurrency"))
    if block is not None:
        found.append(
            Concurrency(
                path=workflow.path,
                workflow_name=workflow.display_name,
                job=None,
                group=block[0],
                cancel_in_progress=block[1],
                triggers=workflow.triggers,
            )
        )

    for job_id, job in workflow.jobs.items():
        if not isinstance(job, dict):
            continue
        block = _block(job.get("concurrency"))
        if block is None:
            continue
        found.append(
            Concurrency(
                path=workflow.path,
                workflow_name=workflow.display_name,
                job=str(job_id),
                group=block[0],
                cancel_in_progress=block[1],
                triggers=workflow.triggers,
            )
        )

    return found


def load(text: str, path: str) -> tuple[Workflow, list[Concurrency]]:
    """Parse a workflow and pull its concurrency blocks out in one go.

    Everything goes through `parse`, which is the only place that turns a
    YAML error into a WorkflowError. Loading the document a second time here
    would let a scanner error escape uncaught and take the process down with
    a traceback instead of the documented exit 2.
    """
    workflow = parse(text, path)
    return workflow, concurrency_blocks(workflow, workflow.raw)
