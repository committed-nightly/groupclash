"""Helpers for building workflow sets in memory.

The checks never touch git or the network -- their whole input is workflow
text -- so the tests hand them exactly that, and only the CLI tests write
files to disk.
"""

from __future__ import annotations

import textwrap

import pytest

from groupclash.contexts import Site, resolver
from groupclash.core import check
from groupclash.expr import collapse, evaluate


def workflow(
    name: str = "CI",
    on: str = "[push]",
    group: str = "${{ github.ref }}",
    cancel: str = "true",
    extra_jobs: str = "",
) -> str:
    """One workflow file with a workflow-level concurrency block."""
    return textwrap.dedent(
        f"""\
        name: {name}
        on: {on}
        concurrency:
          group: {group}
          cancel-in-progress: {cancel}
        jobs:
          build:
            runs-on: ubuntu-latest
            steps:
              - run: 'true'
        {extra_jobs}
        """
    )


@pytest.fixture
def run():
    """Check one or more workflows and give back the report."""

    def _run(*texts: str, **named: str):
        files = {f".github/workflows/w{i}.yml": t for i, t in enumerate(texts)}
        files.update({f".github/workflows/{k}.yml": v for k, v in named.items()})
        return check(files)

    return _run


@pytest.fixture
def kinds():
    """The finding kinds a report contains, in report order."""

    def _kinds(report):
        return [finding.kind for finding in report.sorted_findings()]

    return _kinds


@pytest.fixture
def value():
    """Evaluate one group template under one event, collapsed to a value."""

    def _value(template: str, event: str = "push", name: str = "CI", job=None):
        site = Site(workflow_name=name, job=job)
        return collapse(evaluate(template, resolver(event, site)))

    return _value


@pytest.fixture
def pieces():
    """Evaluate one group template and give back every piece."""

    def _pieces(template: str, event: str = "push", name: str = "CI", job=None):
        site = Site(workflow_name=name, job=job)
        return evaluate(template, resolver(event, site))

    return _pieces
