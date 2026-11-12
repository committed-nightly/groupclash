# Corpora

Two workflow sets the dogfood job in `.github/workflows/ci.yml` and the tests
in `tests/test_dogfood.py` run `groupclash` against. Neither is here to be
read as advice — `copied-group` is the thing the README argues against.

```sh
groupclash examples/copied-group        # exit 1, group-ignores-ref on push
groupclash examples/undecidable-group   # exit 0, and names what it skipped
```

## Why they are files

Because GitHub substitutes `${{ }}` into a `run:` script before the shell
sees it, and a corpus built in a heredoc therefore is not the corpus you
wrote. The dogfood job used to do this:

```yaml
run: |
  cat > .github/workflows/scratch.yml <<'YAML'
  concurrency:
    group: ${{ github.workflow }}-${{ github.head_ref }}
  YAML
  code=0; groupclash || code=$?
  test "$code" -eq 1 || { echo "expected exit 1, got $code"; exit 1; }
```

The step is named *the group everyone copies is caught*. From the log of the
run on 2026-09-12, the file it actually wrote on a `pull_request`:

```
  group: test-shift/2026-09-12-groupclash
```

and on the `push` to `main` the same day:

```
  group: test-
```

A branch name and a dangling hyphen. Both are findings, so the step passed
both times, and in two months it never once handed the tool the template in
its own name. The quoted heredoc is no defence: the substitution happens when
the runner generates the script, before bash starts.

Nothing outside `.github/workflows/` is a workflow to GitHub, so the
expressions in these two files survive to be read.
