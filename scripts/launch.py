"""PyInstaller entry point (a plain script, so the package imports normally)."""

import sys

from gitops.app import main

sys.exit(main())
