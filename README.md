# GitOps

A desktop git repository manager built with Python and PySide6 (Qt 6).

## Run

```bash
uv sync
uv run gitops /path/to/repo
```

## Test

```bash
uv run pytest
```

Tests run offscreen (`QT_QPA_PLATFORM=offscreen`) against throwaway repos and ignore your `~/.gitconfig`.

## Build a standalone app

```bash
scripts/build_app.sh
```

This produces `dist/GitOps.app` on macOS (a folder on Linux and Windows) using PyInstaller. Qt modules the app doesn't use are left out.

## Features

### Everyday work
- **Repositories**: open, clone (with live progress), create, open recent. The last repo reopens on launch, and window and pane sizes are remembered.
- **Changes**:
  - Stage or unstage whole files, a **hunk**, or **selected lines**. Discard changes (asks first).
  - Commit or amend (Ctrl+Enter). A hint shows when the subject passes 72 characters or line 2 isn't blank.
  - Right-click an untracked file to ignore it, its extension, or its folder.
- **History**:
  - Commit graph with lanes and merge nodes, plus branch and tag badges.
  - "All branches" toggle. History loads page by page as you scroll.
  - **Filter** by message, `author:name`, or SHA.
- **Branches** (sidebar): check out, create, rename, delete, check out a remote branch as tracking, merge into the current branch, rebase onto.
- **Remotes**:
  - Fetch, pull and push in the background, with progress and **Cancel**.
  - Push sets the upstream the first time. The status bar shows ↑ahead ↓behind.
  - git never waits on a terminal prompt, so credential problems show as clear errors.

### History tools
- **File history** (Ctrl+Shift+H):
  - Every commit that created, modified, renamed or deleted a file, following renames.
  - A timeline slider; compare **any two versions**, or a version **with your working copy**.
  - Open a version read-only, save a copy, restore it, or blame it.
- **Search history** (Ctrl+Shift+F):
  - Finds text in **any past version** and marks each commit **Added**, **Changed** or **Removed**.
  - Filters: regex, ignore case, branches, paths, author, since/until.
  - A summary line, plus **"Where was it at this commit?"**
- **Blame**: who last changed each line, tinted by age. Click a line to see its commit; blame the version before that commit.

### Rewriting history (v0.2)
- **Merge** with a **3-pane conflict editor**:
  - Ours, base and theirs shown side by side, with an editable result.
  - Choose ours, theirs, both or base per conflict, or take a whole file (handles binary and delete/modify conflicts).
  - Merges use diff3 markers, so the base is always available.
- **Stash**: save (message, untracked, keep staged), preview, apply, pop, drop. A pop with conflicts keeps the stash.
- **Commit menu** (right-click in History): copy SHA, branch here, tag here, **cherry-pick**, **revert** (merges too), **reset** soft, mixed or hard.
- **Interactive rebase**:
  - Drag to reorder; pick, reword, squash, fixup, drop.
  - No editor ever opens. Rewords and squash messages are applied with `exec git commit --amend`.
- **Operation banner**: when a merge, rebase, cherry-pick or revert stops on a conflict, the Changes tab shows Continue, Skip and Abort.
- **Tags**: create (lightweight or annotated), delete, push.

### Polish
- **Undo** (Ctrl+Alt+Z): a list of where the branch has pointed, from git's reflog. Restore any point with `reset --keep`, which never overwrites uncommitted work. This undoes commits, amends, merges, rebases and resets.
- **Themes**: System, Light or Dark (View › Theme). Every diff, blame and conflict color adapts.
- **Command palette** (Ctrl+K): fuzzy-search every menu command.
- **Git console** (Ctrl+\`): shows the git commands the app runs, so you can learn or reproduce them.

### Many repositories (v0.3)
- **Dashboard** (Ctrl+Shift+D): track many repos and see branch, changes, sync, stashes, last commit and state at a glance. Scan a folder for repos, or fetch them all.
- **Submodules** and **worktrees** in the sidebar: open them, update submodules, add or remove worktrees.
- **.gitignore editor** that tests a path against your *unsaved* rules (in a scratch repo, so your real file is never touched).

## Layout

```
gitops/
├── core/                  # Qt-free git logic (unit-tested)
│   ├── git_backend.py     # git CLI wrapper: status, staging, branches, remotes, merge, stash,
│   │                      #   cherry-pick/revert/reset, tags, blame, reflog, worktrees, dashboard
│   ├── file_history.py    # log --follow parser, version contents
│   ├── content_search.py  # log -G engine + Added/Changed/Removed classifier
│   ├── patch.py           # hunk/line staging: partial patches for git apply --cached
│   ├── conflicts.py       # conflict-marker parser and resolver (merge and diff3 styles)
│   ├── rebase.py          # editor-free interactive rebase
│   ├── compare.py         # diff any two file versions
│   └── graph_layout.py    # commit-graph lane assignment
├── services/
│   └── task_runner.py     # QThreadPool tasks, batching, cancellation, owner-safe signals
└── ui/
    ├── main_window.py     # menus, toolbar, sidebar, network runner, commit/branch/tag actions
    ├── dashboard.py       # multi-repo dashboard window
    ├── theme.py           # light/dark palettes and semantic colors
    ├── dialogs/           # clone, stash, conflict editor, rebase, undo, .gitignore, command palette
    └── widgets/           # changes, history+graph, file history, blame, search, diff, git console
scripts/build_app.sh       # PyInstaller build
tests/                     # pytest + pytest-qt
```

## How history search classifies a commit

`git log -G<pattern> -p --unified=0` selects the commits whose diff adds or removes a line containing the text. For each file in each commit, matches on `+` and `-` lines are counted:

| `+` lines | `-` lines | Result  |
|-----------|-----------|---------|
| yes       | no        | Added   |
| no        | yes       | Removed |
| yes       | yes       | Changed |

## Not built yet

- GitHub/GitLab pull requests and CI status
- SSH key, credential and GPG signing settings (GitOps uses your existing git and SSH setup)
- Notifications when remote branches change
