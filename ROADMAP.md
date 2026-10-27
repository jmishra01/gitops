# GitOps Roadmap

## Where the project stands

- **What's built:** a staging workbench, history with a lane graph, branches, stashes, a file inspector, blame, search across history, a conflict resolver, interactive rebase, undo from the reflog, and a dashboard for many repos.
- **Gaps in the basics:**
  - `pyproject.toml` still says `0.1.0`, although commit messages call this work v0.3.
  - There's no CI, no lint config (no `[tool.ruff]` section), and no logging.
- **Hotspots:** `gitops/core/git_backend.py` (~1,050 lines, 96 methods), `gitops/ui/main_window.py` (~990) and `gitops/ui/dialogs/conflict_editor.py` (~910) are the files that will slow down future changes.
- **Scaling risks:**
  - History loads pages with `--skip`, so each later page costs more on large repos.
  - The app only refreshes when the window is re-activated, so changes made in a terminal or editor don't show up until then.

## Phase 1: Harden and release

1. Commit the Windows `CREATE_NO_WINDOW` fix, then run the test suite on Windows and Linux too.
2. Add GitHub Actions: pytest offscreen on macOS, Windows and Ubuntu with Python 3.11 and 3.12, plus `ruff check` and `ruff format --check`.
3. Add a ruff config and a pre-commit hook, and consider `mypy` on `gitops/core` only.
4. Set up versioning: bump to `0.3.0`, keep a single source for the version, add `CHANGELOG.md` and tag releases.
5. Add packaging polish: an app icon (the `.spec` has `icon=None`), macOS codesign and notarization, a Windows installer, and builds uploaded as CI release artifacts.
6. Add logging to a rotating file under the platform's app data folder, and a "Copy diagnostics" item under Help.

## Phase 2: Performance and refresh

1. **File watching:** a debounced `QFileSystemWatcher` on `.git/HEAD`, `index` and `refs`, plus the work tree, honouring ignore rules.
2. **History paging:** continue from the last loaded commit instead of using `--skip`, and stream `git log` output instead of buffering it. Benchmark on a large repo such as linux or chromium.
3. **Large repos:** handle huge numbers of untracked files and big diffs, and use `git status --porcelain=v2 -z` with `--untracked-files` tuned as needed.
4. **Benchmarks:** a `tests/perf` suite gated behind a marker, with time budgets for status, log page, blame and search.

## Phase 3: Refactor for maintainability (alongside Phase 2)

1. Split `GitBackend` into focused modules (status/staging, refs/branches, history/log, remotes, rewrite, stash) behind the same `GitBackend` interface, so the UI code doesn't change.
2. Move page wiring and actions out of `MainWindow` into small controllers. Split `conflict_editor.py` into the model, the three-pane view and the navigation.
3. Make `gitops/core` free of any Qt import (checked in CI) so it can be reused, for example by a CLI or other front-ends.

## Phase 4: Features (v0.4 to v1.0)

Ordered by value to users:

1. **Commit signing:** GPG and SSH signing, with verified badges in History.
2. **Credentials UX:** a sign-in flow (OAuth device flow or tokens in the OS keychain) instead of only showing clear errors.
3. **Hosting integration:** GitHub and GitLab pull requests (list, check out, create, CI status on commits), then review comments.
4. **Settings page:** default branch, fetch interval, diff options (whitespace, word diff, tab width), external diff and merge tools, fonts.
5. **Auto-fetch** in the background for the open repo and the dashboard, with notifications.
6. **Git LFS** awareness, partial and shallow clone options, and sparse checkout.
7. **Diff upgrades:** syntax highlighting, image diffs, word-level highlighting within changed lines.
8. **Accessibility and i18n:** keyboard-only flows, screen-reader labels, and wrapping strings in `tr()`.

## Ongoing

- Every redesign commit comes with UI smoke tests; keep extending `tests/test_ui_smoke.py`.
- Track crash and robustness reports in `tests/test_robustness.py`.
- Every phase ends with a tagged release and a CHANGELOG entry.
