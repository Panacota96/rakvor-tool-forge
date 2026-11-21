"""The release check (core.update): parsing and comparison offline; the real check only with RV_TEST_NETWORK=1."""

import os

import pytest

from rv_hashinv.core import update


def test_versions_and_locations():
    assert update.version_triple("0.3.0") == (0, 3, 0)
    assert update.version_triple("v1.12.7") == (1, 12, 7)
    for bad in ["", "1.2", "1.2.3.4", "1.2.3-rc.1", "1.2.x", "v", "1..3"]:
        assert update.version_triple(bad) is None, bad
    t = "rv-timeline"
    assert update.version_from_location("https://github.com/Panacota96/rv-timeline/releases/tag/v0.3.0", t) == "0.3.0"
    assert update.version_from_location("/Panacota96/rv-timeline/releases/tag/v0.10.2", t) == "0.10.2"
    for bad in [
        "https://github.com/Panacota96/rv-timeline/releases",
        "https://github.com/Panacota96/rv-hub/releases/tag/v0.6.3",
        "https://github.com/Other/rv-timeline/releases/tag/v9.9.9",
        "https://example.org/Panacota96/rv-timeline/releases/tag/v9.9.9",
        "https://github.com/Panacota96/rv-timeline/releases/tag/v1.0.0-rc.1",
    ]:
        assert update.version_from_location(bad, t) is None, bad


def test_compare_texts_and_report():
    a = update.compare("0.2.3", "0.3.0", "rv-timeline")
    assert a == {"status": "available", "current": "0.2.3", "latest": "0.3.0",
                 "url": "https://github.com/Panacota96/rv-timeline/releases/tag/v0.3.0"}
    assert "0.3.0 is available" in update.line(a, "rv-timeline")
    assert update.compare("0.10.0", "0.9.9", "rv-timeline")["status"] == "up_to_date"
    assert update.compare("dev", "0.3.0", "rv-timeline")["status"] == "failed"
    assert "signature" in update.banner(a, "RV Timeline", "fr")
    assert "firma" in update.banner(a, "RV Timeline", "es")
    assert update.banner(update.compare("0.3.0", "0.3.0", "x"), "X", "en") is None
    r = update.report("rv-timeline", a)
    assert (r["schema"], r["status"], r["release_page"]) == (
        update.SCHEMA, "available", "https://github.com/Panacota96/rv-timeline/releases/latest")


def test_off_switch(monkeypatch):
    for v, off in [(None, False), ("", False), ("0", False), ("1", True), ("yes", True)]:
        assert update.env_says_off(v) is off
    monkeypatch.setenv(update.ENV_OFF, "1")
    assert update.check()["status"] == "disabled"
    with pytest.raises(ValueError):
        update.latest_version("../etc", "x")


@pytest.mark.skipif(os.environ.get("RV_TEST_NETWORK") != "1", reason="contacts github.com")
def test_live():
    v = update.latest_version("rv-hub", "rv-test/0.0.0")
    assert update.version_triple(v)
