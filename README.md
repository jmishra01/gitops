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

The window has a **top bar** (repository and branch switchers, command search, one Sync button that knows whether to push, pull or publish, Undo, the git console and theme) and a **navigation rail** with one page per job.

### Changes
- Files with **checkboxes**: tick to stage, untick to unstage. A count badge on the rail shows how many files changed.
- Each **hunk** is a card with Stage / Unstage / Discard buttons. Select **lines** inside a hunk and a floating bar stages or discards just those.
- **Commit composer**: subject and body, amend, Ctrl+Enter. It warns when the subject passes 72 characters.
- Right-click an untracked file to ignore it, its extension, or its folder.
- When a merge, rebase, cherry-pick or revert stops on a conflict, a banner offers Continue, Skip and Abort.

### History
- Commit list with a lane graph, author avatars and branch/tag badges that stay readable on the selected row.
- Current branch or all branches; loads page by page; **filter** by message, `author:name` or SHA.
- **Details panel**: full message, author, actions (check out, branch, tag, cherry-pick, revert, reset, rebase from here), the files the commit changed with +/− bars, and each file's diff.

### Branches
- **Local branches** with their tip commit and a chip for where they stand: up to date, ↑ to push, ↓ behind, not published, or upstream gone.
- **Remote branches** grouped by remote, **tags**, **worktrees** and **submodules** on the same page, with a filter.
- The details panel lists what you can do with the selected item: switch, check out a remote branch as tracking, merge into the current branch, rebase onto it, branch from it, rename, delete; show, push or delete a tag; open or remove a worktree; open or update submodules. Double-click runs the main action.

### Stashes
- Every stash with its message, the branch it came from and its age, and a preview of its changes.
- **Stash changes…** (message, include untracked, keep staged), **Apply**, **Pop**, **Drop**. A pop that conflicts keeps the stash.

### Files and Blame
- **File inspector** (Ctrl+Shift+H): a **timeline** of every commit that created, changed, renamed or deleted the file (following renames), a versions list, and a side-by-side **A/B compare** of any two versions or a version and your working copy. Open a version read-only, save a copy, or restore it.
- **Blame**: who last changed each line, tinted by age. Click a line to open its commit; blame the version before it.

### Search history (Ctrl+Shift+F)
- Finds text in **any past version** and marks each commit **Added**, **Changed** or **Removed**.
- Filters: regex, ignore case, all branches, paths, author, since/until.
- A summary (present now? first added, last removed), the diff of each match, and **"Where was it at this commit?"**

### Conflict resolver
- The **result** is in the centre with ours and theirs on either side. Each conflict gets one decision: accept ours, theirs, both, or edit, with per-conflict undo.
- Shows progress across conflicted files; handles binary and delete/modify conflicts. Merges use diff3 markers, so the base is always available.

### Rewriting history
- **Interactive rebase**: drag to reorder; pick, reword, squash, fixup, drop. No editor ever opens.
- **Undo** (Ctrl+Alt+Z): every place the branch has pointed, from git's reflog. Restoring uses `reset --keep`, which never overwrites uncommitted work, so commits, amends, merges, rebases and resets can all be undone.

### Many repositories
- **Repositories dashboard** (Ctrl+Shift+D): a card per repo with status chips (changes, conflicts, ahead/behind, stashes, operation in progress) and the one **next action** that makes sense for it. Filter by *Needs attention*, *Behind* and more; scan a folder for repos; fetch them all.

### Everything else
- **Remotes**: fetch, pull and push run in the background with progress and **Cancel**. Push sets the upstream the first time. git never waits on a terminal prompt, so credential problems show as clear errors.
- **Command palette** (Ctrl+K): fuzzy-search every command, with its shortcut.
- **Git console** (Ctrl+\`): the git commands the app runs, so you can learn or reproduce them.
- **.gitignore editor** that tests a path against your *unsaved* rules.
- **Themes**: System, Light or Dark (View › Theme). Every page, diff, blame and dialog follows it.
- Repositories open, clone (with live progress) and init from the repo menu. The last repo reopens on launch; window and pane sizes are remembered.
- Files that aren't UTF-8 keep their exact bytes when you stage lines or resolve conflicts.

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
