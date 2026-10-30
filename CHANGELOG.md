# Changelog

All notable changes to GitOps. Versions follow [semantic versioning](https://semver.org); the version lives in `gitops/__init__.py` only.

## 0.3.11 - 2026-10-30

### Fixed
- Tests turn off git's automatic background maintenance. After a commit, git can start a detached maintenance process that may still be touching `.git` while a test checks it; the likely cause of an intermittent index check failure on Windows.

## 0.3.10 - 2026-10-30

### Changed
- **Release notes cover everything since the previous release.** They list every CHANGELOG section after the highest earlier `v*` tag, newest first, so versions that were never tagged (like 0.3.6 to 0.3.8 before 0.3.9) are included. `scripts/release_info.py --since <tag>` picks the starting point by hand.

## 0.3.9 - 2026-10-29

### Fixed
- The test that Cancel kills git's whole process group is skipped on Windows, which has no process groups; there Cancel stops git itself. With this, CI passes on macOS, Linux and Windows.

## 0.3.8 - 2026-10-29

### Fixed
- **Files with Windows line endings (CRLF)** no longer show a stray carriage return at the end of each line in diffs, hunks, blame and history search results. The bytes git stores are unchanged, so staging lines still keeps every CRLF exactly.
- Rebase commit messages, the rebase plan, `MERGE_MSG` and the .gitignore tester now read and write UTF-8 with LF line endings on every OS. On Windows they used cp1252, so a message with "é" reached git garbled.
- Tests write files byte for byte, so they pass on Windows, where text mode wrote CRLF and cp1252. New tests cover CRLF files on every OS.

## 0.3.7 - 2026-10-29

### Changed
- **GitOps now needs Python 3.12 or newer** to run from source. With PySide6 6.12 on Python 3.11, every Qt signal emitted from Python drops one reference to `True`, and once enough have been emitted Python aborts with "deallocating True or False". Python 3.12 made `True` immortal, so the same bug is harmless there. The downloadable builds already used 3.12.
- CI tests on Python 3.12 and 3.13, and publishes the end of pytest's output as a public annotation when a test job fails, since job logs need a GitHub sign-in.

## 0.3.6 - 2026-10-28

### Fixed
- CI tests on Linux and Windows hung until GitHub's six-hour limit, from the day CI was added. The runners have no git identity, so a test's commit failed, and the app's error dialog waited for a click nobody could make. Tests now set a git identity, and any dialog a test didn't plan for fails that test straight away with its message.
- A dashboard layout test assumed the window could get narrow enough for one column, which Linux fonts don't allow.

### Changed
- CI test jobs stop after 20 minutes, and pytest prints every thread's stack when a test runs longer than two minutes.

## 0.3.5 - 2026-10-28

### Added
- The macOS app's Finder info shows the GitOps version instead of 0.0.0. The release workflow checks the version and the bundle's signature.
- CI type-checks `gitops/core` with mypy, as Linux, macOS and Windows.

### Changed
- Stopping a git process on Windows no longer relies on `os.killpg` failing there; the platform is checked first. Behaviour is unchanged.
- ROADMAP.md marks the finished v0.3 items and lists what's left: a license, code signing, a Windows installer and an Intel Mac build.

## 0.3.4 - 2026-10-28

### Added
- **Release builds:** pushing a `v*` tag builds GitOps on macOS (Apple silicon), Windows and Linux, checks each build starts, and publishes a GitHub release with the zips and tarball attached and this changelog's section as its notes. The builds are unsigned for now.
- `scripts/release_info.py` stops a release whose tag doesn't match the version in the code or that has no CHANGELOG entry.
- `gitops --version` prints the version and exits.

### Fixed
- The macOS build reports `dist/GitOps.app` as its output instead of the staging folder.

## 0.3.3 - 2026-10-28

### Added
- **App icon:** a commit graph with a branch running beside main, on GitOps blue. It shows in the window, the Dock and taskbar, the macOS app bundle and the Windows executable.
- A Linux desktop file (`packaging/linux/gitops.desktop`), copied into Linux builds with the icon.
- `scripts/make_icons.py` rebuilds the PNG, `.ico` and `.icns` from `gitops/resources/gitops.svg` on any platform.

## 0.3.2 - 2026-10-28

### Added
- **Update check:** on launch, at most once a day, GitOps asks GitHub for the latest release and shows "GitOps X is available" in the status bar, linking to the download page. Only the release metadata is requested; nothing about you or your repositories is sent.
- **Help › Check for updates…** checks right away, and **Help › Check for updates automatically** turns the launch check off.

## 0.3.1 - 2026-10-28

### Added
- A rotating log file (1 MB, three backups) in the platform's app data folder. It records failed git commands (the subcommand and git's error, never commit messages or file contents), unexpected errors with their traceback, crashes and Qt warnings.
- **Help › Copy diagnostics** copies the GitOps, Python, Qt, OS and git versions plus the end of the log, ready for a bug report. **Help › Open log folder** shows the log files.

## 0.3.0 - 2026-10-28

The first numbered release. It covers everything built so far.

### Added
- **Changes:** stage files with checkboxes, stage or discard single hunks and selected lines, and a commit composer with amend.
- **History:** a lane graph, avatars, branch and tag badges, filters, and a details panel with commit actions.
- **Branches and Stashes pages:** local and remote branches with where they stand, tags, worktrees, submodules, and every stash with a preview.
- **File inspector and Blame:** a timeline that follows renames, side-by-side compare of any two versions, and a file finder.
- **Search history:** find text in any past version, marked Added, Changed or Removed.
- **Conflict resolver** with the result in the centre, and **interactive rebase** with no editor.
- **Undo** from the reflog, using `reset --keep`.
- **Repositories dashboard** with status chips and the next action for each repo.
- Command palette, git console, .gitignore editor, themes, clone with progress.
- Help › About GitOps shows the version.
- CI runs ruff and pytest on macOS, Windows and Linux; a pre-commit config runs ruff locally.

### Fixed
- Opening a subfolder of a repository, submodule paths with spaces, symlink previews, remotes with `/` in their name.
- Hangs, locale-dependent errors and huge previews; the console window git flashed on Windows.
- Files that aren't UTF-8 keep their exact bytes when staging lines or resolving conflicts.
