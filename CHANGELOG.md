# Changelog

All notable changes to GitOps. Versions follow [semantic versioning](https://semver.org); the version lives in `gitops/__init__.py` only.

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
