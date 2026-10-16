# Demo pytest + Playwright project

A self-contained project used by the zedqa acceptance tests. It uses only local
`data:` pages, so no external site is contacted.

## Contents

- `tests/test_login.py` — `TestLogin` (account A, region JP): success, slow,
  parametrized, and one **intentional failure**.
- `tests/test_cart.py` — `TestCart` (account A, region JP): shares account A with
  `TestLogin`, so the two classes must never run in parallel.
- `tests/test_search.py` — `TestSearch` (account B, region US): parametrized, plus
  a **flaky** test that fails once and passes on rerun.
- `tests/test_checkout.py` — `TestCheckout` (account C, region **US**): real
  Chromium tests against `data:` pages, one **intentional failure** so a
  screenshot is produced. Shares `region:US` with `TestSearch`, which demonstrates
  a transitive resource conflict across different accounts.

`zedqa.toml` maps classes to resources. `zedqa init` generated `.zed/tasks.json`
and `.zed/debug.json`.

## Run it

```bash
cd examples/demo_pytest_project
zedqa discover
zedqa plan --all --strategy resource --workers 4
zedqa ui
```

Expected schedule (resource, 4 workers): two groups —
`{TestLogin, TestCart}` on one worker and `{TestSearch, TestCheckout}` on another,
so effective parallelism is 2 and no account/region is ever used concurrently.
