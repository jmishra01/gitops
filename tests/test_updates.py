import io
import json
import urllib.error

import pytest

from gitops.services import updates


@pytest.mark.parametrize(
    ("text", "expected"),
    [("v0.3.2", (0, 3, 2)), ("1.0", (1, 0)), (" 2 ", (2,)), ("v0.4.0-rc1", None), ("latest", None)],
)
def test_parse_version(text, expected):
    assert updates.parse_version(text) == expected


def test_is_newer():
    assert updates.is_newer("0.4.0", "0.3.9")
    assert updates.is_newer("v0.3.10", "0.3.9")
    assert not updates.is_newer("0.3", "0.3.0")
    assert not updates.is_newer("0.3.1", "0.3.1")
    assert not updates.is_newer("0.2.9", "0.3.0")
    assert not updates.is_newer("0.5.0-beta", "0.3.0")


def _respond(monkeypatch, body=None, status=None):
    seen = {}

    def fake_urlopen(request, timeout):
        seen["request"] = request
        if status:
            raise urllib.error.HTTPError(request.full_url, status, "error", {}, None)
        return io.BytesIO(json.dumps(body).encode())

    monkeypatch.setattr(updates.urllib.request, "urlopen", fake_urlopen)
    return seen


def test_latest_release_reads_tag_and_page(monkeypatch):
    seen = _respond(monkeypatch, {"tag_name": "v9.0.0", "html_url": "https://example.test/r/9"})
    assert updates.latest_release() == updates.Release("9.0.0", "https://example.test/r/9")
    assert seen["request"].get_header("User-agent").startswith("GitOps/")
    assert updates.newer_release("0.3.2") == updates.Release("9.0.0", "https://example.test/r/9")
    assert updates.newer_release("9.0.0") is None


def test_no_releases_yet_is_not_an_error(monkeypatch):
    _respond(monkeypatch, status=404)
    assert updates.newer_release() is None


def test_other_http_errors_raise(monkeypatch):
    _respond(monkeypatch, status=500)
    with pytest.raises(urllib.error.HTTPError):
        updates.latest_release()
