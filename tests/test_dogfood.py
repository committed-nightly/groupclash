"""The claims the dogfood job makes, as tests that run on every machine.

The dogfood job in `.github/workflows/ci.yml` runs groupclash on this
repository and on the corpora in `examples/`. Three of its assertions did not
check what they read as checking, and the fixes are in ci.yml; these tests are
so that the claims hold on a laptop too, and that the two corpora keep
producing the output the job greps for.

* `if grep -q '^not checked:' report.txt; then ... exit 1; fi` was reported by
  `deadassert` as `unwitnessed` (logbook#64). It fails only when its pattern
  appears, and nothing in those workflows established that the pattern can
  appear at all, so it would have gone on passing if the undecided path
  stopped working. `examples/undecidable-group` is the witness.

* Neither that step nor the bare `groupclash` above it could tell a clean
  repository from one that no longer holds the subject. groupclash exits 0 on
  a workflow set with no `concurrency:` block in it, so deleting the block at
  the top of ci.yml left both steps green with the README's worked example
  gone.

* *the group everyone copies is caught* built its corpus in a heredoc, and
  GitHub substitutes `${{ }}` into a `run:` script before the shell sees it.
  The file it wrote held `group: test-<branch>` on a pull request and
  `group: test-` on a push -- a literal, never the template in the step's
  name. Both are findings, so the step passed either way. `examples/README.md`
  has the log.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from groupclash.cli import EXIT_FOUND, EXIT_OK, main

ROOT = Path(__file__).resolve().parents[1]
CI = ROOT / ".github" / "workflows" / "ci.yml"
COPIED = ROOT / "examples" / "copied-group"
UNDECIDABLE = ROOT / "examples" / "undecidable-group"


@pytest.fixture
def report(capsys):
    """Run the CLI somewhere and hand back the exit code and stdout."""

    def _report(path: Path, *args: str) -> tuple[int, str]:
        code = main([str(path), *args])
        return code, capsys.readouterr().out

    return _report


def test_this_repository_is_a_corpus_for_its_own_subject(report) -> None:
    """The dogfood steps assert over one real concurrency block, not none.

    Pinned whole, because `0 concurrency blocks, 0 findings` is also a clean
    run and is what the job would have reported over a repository that had
    lost the thing it is about.
    """
    code, out = report(ROOT)
    assert code == EXIT_OK
    assert out == "1 workflow checked, 1 concurrency block, 0 findings\n"


def test_the_copied_group_is_wrong_on_push_and_only_on_push(capsys) -> None:
    """The distinction the old heredoc could not make.

    A literal group is wrong on every event it declares, and the expanded
    heredoc was a literal, so `exit 1` said nothing about which event. The
    template is wrong on `push` alone -- `github.head_ref` is
    per-pull-request on `pull_request` and empty on a push -- and that
    asymmetry is the entire claim of the step named *the group everyone
    copies is caught*.
    """
    assert main([str(COPIED), "--json"]) == EXIT_FOUND
    payload = json.loads(capsys.readouterr().out)
    assert [f["kind"] for f in payload["findings"]] == ["group-ignores-ref"]
    assert payload["findings"][0]["events"] == ["push"]


def test_the_undecidable_corpus_prints_the_line_the_guard_greps_for(report) -> None:
    """Witness for the `^not checked:` guard, which cannot be witnessed here.

    The guard requires the string's absence in this repository, so the proof
    that it can appear has to come from a second corpus. Anchored, because
    `^not checked:` at the start of a line is what the guard asks for.
    """
    code, out = report(UNDECIDABLE)
    assert code == EXIT_OK
    lines = out.splitlines()
    assert any(
        line.startswith("not checked: .github/workflows/undecidable.yml concurrency.group")
        for line in lines
    ), out
    assert "cannot decide matrix.os" in out


def test_no_run_block_in_our_own_ci_holds_a_github_expression() -> None:
    """The regression guard for the heredoc above.

    There is no way to keep a `${{ }}` out of GitHub's hands inside a `run:`
    script -- quoting the heredoc does not do it, because the substitution
    happens before bash starts. So a corpus that needs one is a file, and a
    `${{` in a run block here is a bug until proven otherwise.
    """
    workflow = yaml.safe_load(CI.read_text(encoding="utf-8"))
    offenders = [
        (job, step.get("name", "(unnamed)"))
        for job, body in workflow["jobs"].items()
        for step in body["steps"]
        if "${{" in step.get("run", "")
    ]
    assert not offenders, (
        f"GitHub will substitute these before the shell sees them: {offenders}. "
        "If the step needs a workflow holding an expression, put it in "
        "examples/ -- nothing outside .github/workflows/ is a workflow."
    )
