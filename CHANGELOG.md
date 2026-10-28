# Changelog

All notable changes to GitOps. Versions follow [semantic versioning](https://semver.org); the version lives in `gitops/__init__.py` only.

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
