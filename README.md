# groupclash

Checks that your GitHub Actions `concurrency:` groups cancel the runs you
meant and not the ones you didn't. It is for anyone who has watched a green
pull request go orange and say `Canceled` with nothing to explain it.

A concurrency group is a string. Nothing validates it, nothing reports on it,
and the difference between the one you want and one that quietly cancels
every branch in the repository is usually a single context that expands to
nothing on half your events. Both spellings look right in the file.

## Install

```
pip install git+https://github.com/committed-nightly/groupclash
```

Python 3.10 or newer. One dependency, PyYAML.

## Use

```
cd your-repo
groupclash
```

It reads `.github/workflows` off disk, including a file you have written but
not yet `git add`ed, so you can check a group before you commit it. It never
touches git, the network or the Actions API — the whole input is the workflow
text.

`--json` gives you the same findings in a form you can pipe somewhere.

## A real example

Five workflows. Four of them are wrong, and none of them looks it:

```bash
mkdir demo && cd demo && mkdir -p .github/workflows

cat > .github/workflows/ci.yml <<'YAML'
name: CI
on: [push, pull_request]
concurrency:
  group: ${{ github.workflow }}-${{ github.head_ref }}
  cancel-in-progress: true
jobs:
  test: {runs-on: ubuntu-latest, steps: [{run: make test}]}
YAML

cat > .github/workflows/e2e.yml <<'YAML'
name: End to end
on: [pull_request]
concurrency:
  group: ${{ github.ref }}
  cancel-in-progress: true
jobs:
  e2e: {runs-on: ubuntu-latest, steps: [{run: make e2e}]}
YAML

cat > .github/workflows/lint.yml <<'YAML'
name: Lint
on: [pull_request]
concurrency:
  group: ${{ github.ref }}
  cancel-in-progress: true
jobs:
  lint: {runs-on: ubuntu-latest, steps: [{run: make lint}]}
YAML

cat > .github/workflows/deploy.yml <<'YAML'
name: Deploy
on:
  push:
    branches: [main, 'release/**']
concurrency:
  group: ${{ github.workflow }}-${{ github.sha }}
  cancel-in-progress: true
jobs:
  deploy: {runs-on: ubuntu-latest, steps: [{run: make deploy}]}
YAML

cat > .github/workflows/preview.yml <<'YAML'
name: Preview
on: [push]
concurrency:
  group: ${{ github.head_ref }}
  cancel-in-progress: true
jobs:
  preview: {runs-on: ubuntu-latest, steps: [{run: make preview}]}
YAML

groupclash
```

```
  .github/workflows/preview.yml
      concurrency.group: '${{ github.head_ref }}'
      empty-group: on push this expands to an empty group -- github.head_ref is
      not set for that event. GitHub does not document what an empty group
      means; what people report is that no concurrency is applied at all, so
      this block does nothing for those events

  .github/workflows/ci.yml
      concurrency.group: '${{ github.workflow }}-${{ github.head_ref }}'
      group-ignores-ref: on push this is 'CI-' for every branch and pull
      request. cancel-in-progress is true, so a run on one ref cancels a run on
      any other

  .github/workflows/deploy.yml
      concurrency.group: '${{ github.workflow }}-${{ github.sha }}'
      never-cancels: on push the group contains github.sha, which is different
      for every run, so no two runs are ever in the same group.
      cancel-in-progress is set and there is nothing it can ever cancel

  .github/workflows/e2e.yml
      concurrency.group: '${{ github.ref }}'
      shared-group: on pull_request this is the same group as
      .github/workflows/lint.yml (concurrency.group). Concurrency groups are
      scoped to the repository, not to the workflow, so these are one group
      between them

5 workflows checked, 5 concurrency blocks, 4 findings
```

`ci.yml` is the one worth staring at. It is the single most copied
concurrency block there is, it is correct for pull requests, and on a push
`github.head_ref` is empty — so the group is `CI-` for every branch in the
repository and a push to any branch kills a push to any other. The workflow
is not broken and never fails. It just cancels.

`e2e.yml` and `lint.yml` are each fine on their own. Concurrency groups are
scoped to the *repository*, not to the workflow, which is the least-known
thing about the feature: two files with the same group string are one group,
and these two spend their lives cancelling each other.

## What it checks

| kind | what it means |
| --- | --- |
| `empty-group` | for some event this workflow receives, the whole group expands to nothing |
| `group-ignores-ref` | nothing in the group varies per branch or pull request, so unrelated runs cancel each other |
| `never-cancels` | the group contains something unique per run, so `cancel-in-progress` has nothing it can ever cancel |
| `shared-group` | two workflow files land in the same group, and neither one says so |

Exit codes, because this is meant to sit in CI: **0** every group does what it
looks like it does, **1** at least one does not, **2** the check could not run
— no `.github/workflows`, no workflow files in it, a file that is not YAML. 2
is deliberately not 0. A checker that passes when it did not run is worse than
no checker.

A repository with workflows but no `concurrency:` anywhere is a **0**. Having
none is a real answer, and it is the state most repositories are in.

## How it decides

Each group is evaluated once **per event the workflow declares**, because a
group is not right or wrong on its own. It is right for the event it was
written for and usually wrong for the one that got added underneath it a year
later. `${{ github.head_ref }}` is per-pull-request on `pull_request` and
empty on `push`; `${{ github.ref }}` is per-pull-request on `pull_request` and
the *base branch* on `pull_request_target`, identical for every pull request
into `main`.

Nothing is decided by pattern-matching the text. The expression is parsed and
evaluated symbolically — `||`, `&&`, comparisons, `format()` — with each
context replaced by what it varies with rather than by a value. That is why
`${{ github.event_name == 'pull_request' && github.head_ref || github.ref }}`
comes out clean on both its events, and why two workflows that both say
`${{ github.workflow }}-${{ github.ref }}` are correctly *not* reported as
sharing a group.

Anything it cannot decide is reported as not checked, on green runs too:

```
not checked: .github/workflows/ci.yml concurrency.group on pull_request --
cannot decide !contains(github.event.pull_request.labels.*.name,
'ci-test-flaky') && github.head_ref || github.run_id
```

## In CI

```yaml
- run: pip install git+https://github.com/committed-nightly/groupclash
- run: groupclash
```

No `fetch-depth` and no token: it reads files, and that is all it reads.

## What it is not

It is not a workflow linter. If you are not running one, run
[actionlint](https://github.com/rhysd/actionlint) first — it type-checks
expressions, shellchecks your `run:` blocks and catches far more than this
does. It checks that your `concurrency.group` is a string. This checks what
the string turns into.

## Known limits

**It was tuned against real repositories, and it stayed quiet.** Across 112
concurrency blocks in ten public repositories it reports one finding. The
first pass reported six, and four of those were wrong — a dispatch-only
publish workflow using `group: ${{ github.workflow }}` means "one at a time"
and is not a group that forgot to vary, and `github.event.workflow_run.id` is
a key rather than a unique value, because re-running the upstream workflow
delivers the same id again. Both rules were changed. If you get a finding you
believe is wrong, that is worth an issue; the last four were.

**A constant group with `cancel-in-progress: false` is never reported.** It
is how you serialise deployments, and it is the one arrangement here that is
usually deliberate.

**`workflow_dispatch` on its own is treated as a single ref.** It can run
against any ref, so two dispatches on two branches genuinely do share a
constant group — but somebody has to start both by hand. A workflow with
`pull_request` alongside it is still checked on that event.

**Job-level blocks are checked, but not compared with each other.** Two jobs
in one file sharing a group, or a job whose group matches its own workflow's,
are real arrangements with real consequences and this does not report on
either. Cross-file comparison only.

**An empty group is not documented behaviour.** GitHub says nothing about
what happens when `group` evaluates to `""`. What people report is that no
concurrency is applied at all. Either way it is not what the expression was
written to do, which is the finding.

**Reusable workflows are checked where they sit.** A `workflow_call` workflow
is evaluated under `workflow_call`, not under whatever events its callers
have, so a group that varies correctly for the caller may be reported — or
missed — here. Its `inputs.*` are undecidable, so in practice most of these
land in `not checked`.

## Licence

MIT.
