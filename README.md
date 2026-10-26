# GitOps

A desktop git repository manager built with Python and PySide6 (Qt 6).

## Run

```bash
uv venv .venv && uv pip install --python .venv/bin/python -e ".[dev]"
.venv/bin/python -m gitops /path/to/repo
```

## Test

```bash
.venv/bin/python -m pytest
```

## What works today

- **History**: branch sidebar, commit list and a diff for each commit
- **File history** (Ctrl+Shift+H): every commit that created, modified, renamed or deleted a file. It follows renames, shows +/- counts and the diff for each version, and can restore an old version
- **Search history** (Ctrl+Shift+F): finds text in *any past version* of the repo and labels each commit **Added**, **Changed** or **Removed**. Supports regex, ignore case, all branches, path and author filters, live streaming results and cancel. A summary line shows whether the text exists now, when it was first added and when it was last removed

## Layout

```
gitops/
├── core/          # Qt-free git logic (unit-tested)
│   ├── git_backend.py     # git CLI wrapper, streaming, status/branches/log
│   ├── file_history.py    # log --follow parser
│   └── content_search.py  # log -G engine + Added/Changed/Removed classifier
├── services/
│   └── task_runner.py     # QThreadPool tasks, batching, cancellation
└── ui/
    ├── main_window.py
    └── widgets/           # diff view, file history, history search
tests/                     # pytest + pytest-qt (offscreen)
```

## How history search classifies a commit

`git log -G<pattern> -p --unified=0` selects the commits whose diff adds or
removes a line containing the text. For each file in each commit, matches on
`+` and `-` lines are counted:

| `+` lines | `-` lines | Result  |
|-----------|-----------|---------|
| yes       | no        | Added   |
| no        | yes       | Removed |
| yes       | yes       | Changed |
