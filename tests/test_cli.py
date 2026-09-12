"""The command line, mostly its exit codes.

This is meant to sit in someone's CI, so the difference between "checked and
clean" and "did not check" has to survive all the way out to the shell.
"""

from __future__ import annotations

import json
import os

import pytest

from groupclash.cli import EXIT_ERROR, EXIT_FOUND, EXIT_OK, main

from conftest import workflow


@pytest.fixture
def repo(tmp_path):
    """A directory with a .github/workflows in it."""

    class Repo:
        def __init__(self, path):
            self.path = str(path)
            self.workflows = os.path.join(self.path, ".github", "workflows")
            os.makedirs(self.workflows)

        def add(self, name: str, text: str) -> None:
            with open(os.path.join(self.workflows, name), "w", encoding="utf-8") as fh:
                fh.write(text)

        def run(self, *args) -> int:
            return main([self.path, *args])

    return Repo(tmp_path)


def test_clean_repository_exits_zero(repo, capsys):
    repo.add("ci.yml", workflow(group="${{ github.ref }}"))
    assert repo.run() == EXIT_OK
    assert "0 findings" in capsys.readouterr().out


def test_a_finding_exits_one(repo, capsys):
    repo.add("ci.yml", workflow(group="ci", on="[push, pull_request]"))
    assert repo.run() == EXIT_FOUND
    assert "group-ignores-ref" in capsys.readouterr().out


def test_no_workflow_directory_is_an_error_not_a_pass(tmp_path, capsys):
    # The whole point of exit 2: nobody looked, so nobody may report green.
    assert main([str(tmp_path)]) == EXIT_ERROR
    assert "no .github/workflows" in capsys.readouterr().err


def test_an_empty_workflow_directory_is_an_error(repo, capsys):
    assert repo.run() == EXIT_ERROR
    assert "no workflow files" in capsys.readouterr().err


def test_a_repository_with_no_concurrency_at_all_is_a_pass(repo, capsys):
    # Unlike the two above: having no concurrency blocks is a real answer.
    repo.add(
        "ci.yml",
        "name: CI\non: [push]\njobs:\n  a:\n    runs-on: ubuntu-latest\n    steps: [{run: 'true'}]\n",
    )
    assert repo.run() == EXIT_OK
    assert "0 concurrency blocks" in capsys.readouterr().out


def test_unparseable_yaml_is_an_error(repo, capsys):
    repo.add("ci.yml", "name: CI\n  on: [push]\n bad indentation\n")
    assert repo.run() == EXIT_ERROR
    assert "groupclash:" in capsys.readouterr().err


def test_files_outside_the_workflow_directory_are_ignored(repo, capsys):
    repo.add("ci.yml", workflow(group="${{ github.ref }}"))
    nested = os.path.join(repo.workflows, "shared")
    os.makedirs(nested)
    with open(os.path.join(nested, "x.yml"), "w", encoding="utf-8") as fh:
        fh.write(workflow(name="Nested", group="ci"))
    assert repo.run() == EXIT_OK
    assert "1 workflow checked" in capsys.readouterr().out


def test_non_workflow_files_are_ignored(repo, capsys):
    repo.add("ci.yml", workflow(group="${{ github.ref }}"))
    repo.add("README.md", "not a workflow")
    assert repo.run() == EXIT_OK
    assert "1 workflow checked" in capsys.readouterr().out


def test_undecided_checks_are_named_on_a_clean_run(repo, capsys):
    repo.add("ci.yml", workflow(group="${{ matrix.os }}"))
    assert repo.run() == EXIT_OK
    out = capsys.readouterr().out
    assert "not checked:" in out and "matrix.os" in out


def test_json_output(repo, capsys):
    repo.add("ci.yml", workflow(group="ci", on="[push, pull_request]"))
    assert repo.run("--json") == EXIT_FOUND
    payload = json.loads(capsys.readouterr().out)
    assert payload["workflows"] == 1
    assert payload["findings"][0]["kind"] == "group-ignores-ref"
    assert payload["findings"][0]["events"] == ["push", "pull_request"]


def test_repository_flag_reaches_the_expression(repo, capsys):
    repo.add("ci.yml", workflow(group="${{ github.repository }}", on="[push]"))
    assert repo.run("--repository", "octo/cat", "--json") == EXIT_FOUND
    payload = json.loads(capsys.readouterr().out)
    assert "'octo/cat'" in payload["findings"][0]["message"]
