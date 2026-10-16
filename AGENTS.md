# AGENTS.md — stable development & acceptance constraints

This file records the rules that must not drift. It is not a changelog.

## Product contract

- Execution must work **without Zed**. The CLI is the source of truth; the TUI and
  Zed Tasks are entry points.
- One business layer: `zedqa.core.service.ProjectService`. CLI and TUI must not
  each implement execution, scheduling or reporting.
- Never ask users to modify their test functions or replace their fixtures
  (`browser_manager`, `context`, `page`, …). zedqa hooks around pytest.
- Never reimplement pytest collection, xdist distribution, Playwright contexts,
  Allure rendering or a debugger. Reuse the real tools.

## Correctness invariants

- Discovery uses **real pytest collection** (`--collect-only` + plugin), never a
  regex scan of `def test_`.
- A run executes **exactly** the selected nodeids. Unknown nodeids abort with an
  error; they are never silently dropped and another test is never substituted.
- `resource` scheduling must guarantee, and tests must prove, that cases sharing
  a resource (transitively) never run concurrently — within a run and across
  independent runs on one machine.
- `class` scheduling must keep a class on one worker, in pytest's order.
- A test that fails then passes on rerun is **flaky**: keep the original failure
  text/evidence; do not report it as a clean pass.
- Test failure and report-generation failure are **separate** states.
  Report generation is attempted even when tests fail.
- Runs are isolated: `.qa-runs/<run-id>/`; parallel workers' attachments must not
  collide; two runs must not share results.
- Commands are argv lists, never shell strings (spaces, brackets, unicode
  parameterized nodeids, Windows paths).
- Cancellation and worker crashes must terminate the run, mark unfinished cases
  as cancelled/errored, and release every resource lock.

## Secrets

`zedqa.toml` contains aliases and resource ids only. Never store passwords,
tokens or cookies. Never print credentials in logs or reports.

## Testing rules

- Real pytest/Playwright runs are the acceptance bar; mocks are only for
  third-party boundaries that cannot run here.
- If an environment capability is missing (Zed GUI, Chromium, Allure,
  cross-platform execution), mark it **NOT VERIFIED**. Never fake a pass.
- Do not delete failing assertions or skip required scenarios to make tests green.
- After any code change, re-run the full suite. Results from before the change are
  not valid evidence.

## Scope

- Only modify this repository and the demo project under `examples/`.
- Run artifacts (`*.qa-runs/`, caches) stay out of git; keep `.gitignore` current.
- Do not push to a remote unless explicitly asked.
