"""Command line entry point.

Exit codes, because the main use for this is a CI gate:

    0  every concurrency group does what it looks like it does
    1  at least one of them does not
    2  the check could not run at all

2 is deliberately not 1 and very deliberately not 0. No workflow directory, a
file that is not YAML, a path that does not exist: those all mean nobody
looked, and "nobody looked" reported as a green tick is the failure this tool
exists to catch in other people's CI.

A repository with workflows but no `concurrency:` blocks anywhere is a 0, not
a 2. Having none is a perfectly good answer, and it is the state most
repositories are in.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import textwrap

from .core import SHARED_GROUP, Report, check
from .workflows import WORKFLOW_DIR, WORKFLOW_SUFFIXES, WorkflowError, is_workflow_path

EXIT_OK = 0
EXIT_FOUND = 1
EXIT_ERROR = 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="groupclash",
        description=(
            "Find the GitHub Actions concurrency groups that cancel the wrong "
            "runs, or never cancel anything."
        ),
    )
    parser.add_argument(
        "path",
        nargs="?",
        default=".",
        help="the repository to check (default: the current directory)",
    )
    parser.add_argument(
        "--repository",
        default=None,
        metavar="OWNER/NAME",
        help=(
            "what ${{ github.repository }} expands to. Only matters if a group "
            "uses it; the default is a placeholder that compares equal to "
            "itself, which is all the checks need"
        ),
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="write the findings as JSON instead of text",
    )
    return parser


def collect(root: str) -> dict[str, str]:
    """Every workflow file under `root`, as {repo-relative path: text}."""
    directory = os.path.join(root, WORKFLOW_DIR)
    if not os.path.isdir(directory):
        raise FileNotFoundError(directory)

    files: dict[str, str] = {}
    for name in sorted(os.listdir(directory)):
        full = os.path.join(directory, name)
        if not os.path.isfile(full) or not name.endswith(WORKFLOW_SUFFIXES):
            continue
        relative = f"{WORKFLOW_DIR}/{name}"
        if not is_workflow_path(relative):  # pragma: no cover - belt and braces
            continue
        with open(full, encoding="utf-8") as handle:
            files[relative] = handle.read()
    return files


def wrap(text: str, indent: str) -> str:
    return textwrap.fill(
        text, width=79, initial_indent=indent, subsequent_indent=indent
    )


def render(report: Report) -> str:
    lines: list[str] = []
    last_path = None

    for finding in report.sorted_findings():
        if finding.path != last_path:
            lines.append("")
            lines.append(f"  {finding.path}")
            last_path = finding.path
        lines.append(wrap(f"{finding.where}: {finding.group!r}", "      "))
        lines.append(wrap(f"{finding.kind}: {finding.message}", "      "))

    if report.findings:
        lines.append("")

    counted = (
        f"{report.workflows} workflow{'s' if report.workflows != 1 else ''} checked, "
        f"{report.blocks} concurrency block{'s' if report.blocks != 1 else ''}, "
        f"{len(report.findings)} finding{'s' if len(report.findings) != 1 else ''}"
    )
    lines.append(counted)

    # Every check that did not run gets named, on green runs too. A group
    # expression this cannot decide is the most likely place for it to be
    # quietly useless, so it should never be quiet about it.
    for skipped in report.undecided:
        lines.append("")
        lines.append(
            wrap(
                f"not checked: {skipped.path} {skipped.where} on {skipped.event} "
                f"-- cannot decide {skipped.reason}",
                "",
            )
        )

    return "\n".join(lines).strip("\n") + "\n"


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    try:
        files = collect(args.path)
    except FileNotFoundError:
        print(
            f"groupclash: {args.path} has no {WORKFLOW_DIR} directory, so there "
            "is nothing to check. Point it at the root of a repository.",
            file=sys.stderr,
        )
        return EXIT_ERROR
    except OSError as exc:
        print(f"groupclash: cannot read {args.path}: {exc}", file=sys.stderr)
        return EXIT_ERROR

    if not files:
        print(
            f"groupclash: {args.path}/{WORKFLOW_DIR} contains no workflow files.",
            file=sys.stderr,
        )
        return EXIT_ERROR

    try:
        report = check(files, repository=args.repository or "owner/repo")
    except WorkflowError as exc:
        print(f"groupclash: {exc}", file=sys.stderr)
        return EXIT_ERROR

    if args.as_json:
        print(
            json.dumps(
                {
                    "workflows": report.workflows,
                    "blocks": report.blocks,
                    "findings": [f.as_dict() for f in report.sorted_findings()],
                    "undecided": [
                        {
                            "path": u.path,
                            "where": u.where,
                            "event": u.event,
                            "reason": u.reason,
                        }
                        for u in report.undecided
                    ],
                },
                indent=2,
            )
        )
    else:
        sys.stdout.write(render(report))

    return EXIT_FOUND if report.findings else EXIT_OK


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
