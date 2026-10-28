# GitOps Roadmap

## Where the project stands

- **What's built:** a staging workbench, history with a lane graph, branches, stashes, a file inspector, blame, search across history, a conflict resolver, interactive rebase, undo from the reflog, and a dashboard for many repos.
- **Versions:** 0.3.0 to 0.3.5, with a `CHANGELOG.md` entry for each. Pushing a `v*` tag builds macOS, Windows and Linux and publishes a GitHub release; 0.3.4 is the only one published so far.
- **Quality checks:** CI runs ruff, pytest on macOS, Windows and Linux, and mypy on `gitops/core` (type-checked as each platform).
- **Gaps in the basics:**
  - The builds are unsigned, so macOS and Windows warn before opening them. Windows gets a zip, not an installer, and macOS builds are Apple silicon only.
  - The repository has no license file.
- **Hotspots:** `gitops/core/git_backend.py` (~1,100 lines), `gitops/ui/main_window.py` (~1,100) and `gitops/ui/dialogs/conflict_editor.py` (~960) are the files that will slow down future changes.
- **Scaling risks:**
  - History loads pages with `--skip`, so each later page costs more on large repos.
  - The app only refreshes when the window is re-activated, so changes made in a terminal or editor don't show up until then.

## Direction

GitOps is the git client that makes history and risky operations safe and easy to understand. Search across history, the conflict resolver and undo from the reflog are what set it apart; each release builds on them instead of matching other clients feature for feature.

## v0.3: Ship it

Done:

1. **Versioning:** a single source for the version in `gitops/__init__.py`, `CHANGELOG.md`, and tagged releases (0.3.0).
2. **Logging** to a rotating file in the platform's app data folder, with Help › Copy diagnostics (0.3.1).
3. **Update check** on launch, at most once a day, linking to the download page (0.3.2).
4. **App icon** for the window, the macOS bundle, the Windows executable and a Linux desktop file (0.3.3).
5. **Release builds** for macOS, Windows and Linux from `v*` tags, attached to a GitHub release (0.3.4).
6. **Checks:** a pre-commit hook for ruff (0.3.0), mypy on `gitops/core` in CI, and the macOS bundle version that Finder shows (0.3.5).

Still to do:

1. **Choose a license.** It also decides whether a paid edition stays possible later.
2. **Code signing:** macOS codesign and notarization, and Windows signing. These need an Apple Developer account and a code-signing certificate, stored as CI secrets.
3. **Windows installer** (Inno Setup or MSIX) with a Start menu entry and an uninstaller.
4. **Intel Mac build**, if there's demand: a second macOS runner in the Release workflow.

## v0.4: Always current

1. **File watching:** a debounced `QFileSystemWatcher` on `.git/HEAD`, `index` and `refs`, plus the work tree, honouring ignore rules.
2. **Auto-fetch** in the background for the open repo and the dashboard, with notifications when remote branches change.
3. **History paging:** continue from the last loaded commit instead of using `--skip`, and stream `git log` output instead of buffering it.
4. **Large repos:** handle huge numbers of untracked files and big diffs, and use `git status --porcelain=v2 -z` with `--untracked-files` tuned as needed.
5. **Benchmarks:** a `tests/perf` suite gated behind a marker, with time budgets for status (200 ms), a log page (300 ms), blame and search on a large repo such as linux or chromium.
6. **Settings page:** default branch, fetch interval, diff options (whitespace, tab width), fonts.

## v0.5: Trust and identity

1. **Commit signing:** GPG and SSH signing, with verified badges in History.
2. **Credentials UX:** a sign-in flow (OAuth device flow or tokens in the OS keychain) instead of only showing clear errors.
3. **Identity profiles:** a name, email and signing key per folder (for example work and personal), so commits never go out with the wrong email.

## v0.6: Collaborate

1. **Hosting integration:** GitHub and GitLab pull requests: list, check out, create from a branch.
2. **CI status** on commits and branches in History and Branches.
3. **Dashboard:** pull request and CI chips on each repo card.
4. Review comments, after the above is stable.

## v0.7: Better diffs and big repos

1. **Diff upgrades:** syntax highlighting, image diffs, word-level highlighting within changed lines.
2. **External tools:** configurable diff and merge tools in Settings.
3. **Git LFS** awareness, partial and shallow clone options, and sparse checkout.

## v1.0: Stable

1. **Accessibility and i18n:** keyboard-only flows, screen-reader labels, and wrapping strings in `tr()`.
2. A docs site with a page per feature.
3. A stability bar: no known data-loss bugs and every perf budget met.

## Refactor for maintainability (alongside v0.4 and v0.5)

Finish this before v0.6, which adds a lot of new UI.

1. Split `GitBackend` into focused modules (status/staging, refs/branches, history/log, remotes, rewrite, stash) behind the same `GitBackend` interface, so the UI code doesn't change.
2. Move page wiring and actions out of `MainWindow` into small controllers. Split `conflict_editor.py` into the model, the three-pane view and the navigation.
3. Make `gitops/core` free of any Qt import (checked in CI) so it can be reused, for example by a CLI or other front-ends.

## Candidate features

Not yet scheduled; each builds on what's already there. Pick them into a release once it has room.

1. **Safety net:** warn before a force push, before pushing to a protected branch, and before committing something that looks like a secret. Snapshot uncommitted work before a reset or discard, so Undo covers it too.
2. **Bisect:** mark good and bad commits from History and step through visually.
3. **Branch cleanup:** find branches that are merged or whose upstream is gone, and delete them in bulk.
4. **Commit composer:** a co-author picker, Conventional Commit templates, issue-key linking, and readable output when a pre-commit hook fails.
5. **Split and stack:** split a commit into several, and keep stacked branches in sync on rebase.
6. **Dashboard bulk actions:** pull all, switch branch across repos, and saved repo groups.
7. **AI help (opt-in, your own key):** draft a commit message from the staged diff, explain a commit, suggest a conflict resolution, write a PR description. Off by default, and clear about what leaves the machine.
8. **`gitops` CLI** on top of the Qt-free core.

## Open questions

- Individual developers or teams first? Teams would move v0.6 ahead of v0.5.
- Free and open source, or paid later? This sets how much signing, installer and auto-update work is worth it.
- Telemetry and AI: ruled out, or allowed as opt-in?

## Ongoing

- Every redesign commit comes with UI smoke tests; keep extending `tests/test_ui_smoke.py`.
- Track crash and robustness reports in `tests/test_robustness.py`.
- Every release ends with a tag and a CHANGELOG entry.
