"""Render gitops/resources/gitops.svg into the icon files each platform needs.

Usage: uv run python scripts/make_icons.py

Writes, next to the SVG:
  gitops.png   512px, the window icon at runtime and the Linux desktop icon
  gitops.ico   16-256px, for the Windows executable
  gitops.icns  16-1024px, for the macOS app bundle

Both container formats are written by hand (each entry is a PNG), so this
runs the same on any platform and needs nothing beyond PySide6.
"""

from __future__ import annotations

import os
import struct
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, Qt  # noqa: E402
from PySide6.QtGui import QGuiApplication, QImage, QPainter  # noqa: E402
from PySide6.QtSvg import QSvgRenderer  # noqa: E402

RESOURCES = Path(__file__).resolve().parent.parent / "gitops" / "resources"
ICO_SIZES = [16, 24, 32, 48, 64, 128, 256]
# icns entry type -> pixel size. The @2x types reuse a larger render.
ICNS_TYPES = [
    (b"icp4", 16),
    (b"icp5", 32),
    (b"icp6", 64),
    (b"ic07", 128),
    (b"ic08", 256),
    (b"ic09", 512),
    (b"ic10", 1024),
    (b"ic11", 32),
    (b"ic12", 64),
    (b"ic13", 256),
    (b"ic14", 512),
]


def render_png(renderer: QSvgRenderer, size: int) -> bytes:
    image = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    renderer.render(painter)
    painter.end()
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, "PNG")
    return bytes(data)


def build_ico(pngs: dict[int, bytes]) -> bytes:
    header = struct.pack("<HHH", 0, 1, len(pngs))
    offset = 6 + 16 * len(pngs)
    entries, blobs = b"", b""
    for size, png in pngs.items():
        dim = 0 if size >= 256 else size  # 0 means 256 in the ICO directory
        entries += struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(png), offset)
        blobs += png
        offset += len(png)
    return header + entries + blobs


def build_icns(pngs: dict[int, bytes]) -> bytes:
    body = b"".join(kind + struct.pack(">I", 8 + len(pngs[size])) + pngs[size] for kind, size in ICNS_TYPES)
    return b"icns" + struct.pack(">I", 8 + len(body)) + body


def main() -> int:
    _app = QGuiApplication(sys.argv)
    renderer = QSvgRenderer(str(RESOURCES / "gitops.svg"))
    if not renderer.isValid():
        print("gitops.svg could not be read", file=sys.stderr)
        return 1
    sizes = sorted({*ICO_SIZES, *(size for _, size in ICNS_TYPES)})
    pngs = {size: render_png(renderer, size) for size in sizes}
    (RESOURCES / "gitops.png").write_bytes(pngs[512])
    (RESOURCES / "gitops.ico").write_bytes(build_ico({s: pngs[s] for s in ICO_SIZES}))
    (RESOURCES / "gitops.icns").write_bytes(build_icns(pngs))
    for name in ("gitops.png", "gitops.ico", "gitops.icns"):
        print(f"wrote {RESOURCES / name} ({(RESOURCES / name).stat().st_size:,} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
