import os
import struct

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QIcon  # noqa: E402

from gitops.app import ICON  # noqa: E402

RESOURCES = ICON.parent
PNG = b"\x89PNG\r\n\x1a\n"


def test_window_icon_loads(qapp):
    assert not QIcon(str(ICON)).isNull()
    assert QIcon(str(ICON)).availableSizes()[0].width() == 512


def test_ico_holds_a_png_per_size():
    data = (RESOURCES / "gitops.ico").read_bytes()
    reserved, kind, count = struct.unpack_from("<HHH", data)
    assert (reserved, kind) == (0, 1)
    sizes = []
    for i in range(count):
        width, _, _, _, _, _, length, offset = struct.unpack_from("<BBBBHHII", data, 6 + 16 * i)
        assert data[offset : offset + 8] == PNG and offset + length <= len(data)
        sizes.append(width or 256)
    assert sizes == [16, 24, 32, 48, 64, 128, 256]


def test_icns_entries_cover_every_size():
    data = (RESOURCES / "gitops.icns").read_bytes()
    assert data[:4] == b"icns" and struct.unpack_from(">I", data, 4)[0] == len(data)
    kinds, pos = [], 8
    while pos < len(data):
        kind, length = data[pos : pos + 4], struct.unpack_from(">I", data, pos + 4)[0]
        assert data[pos + 8 : pos + 16] == PNG
        kinds.append(kind)
        pos += length
    assert pos == len(data)
    assert {b"icp4", b"ic07", b"ic10", b"ic14"} <= set(kinds)
