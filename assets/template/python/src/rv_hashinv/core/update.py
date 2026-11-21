"""Release check: is a newer release of a Rakvor tool published? Optional, notify only (the Python profile's copy of
the Rust tools' src/core/src/update.rs; same behaviour, same JSON).

GET https://github.com/Panacota96/<tool>/releases/latest  (no redirect followed, 5 s, User-Agent rv-<tool>/<ver>)
  -> 302 Location: .../<tool>/releases/tag/v<X.Y.Z> -> latest X.Y.Z -> compared with the running version
     newer: "available" (the GUI shows a banner, `update-check` exits 3); same or older: "up_to_date";
     anything else (offline, timeout, no release): "failed", never blocking.

- When: the GUI checks once at start-up, in the background (started by gui.app.main() only); the CLI only through
  `update-check`. A run, a verification or any other command never contacts the network for it.
- What is sent: one HTTPS GET of the public release page with `User-Agent: rv-<tool>/<version>` and nothing else:
  nothing about the case, the evidence, the examiner or the machine. Not part of a run, not audited.
- What it does: it names the newer version and its release page; it never downloads or installs anything.
- Off switch: RV_NO_UPDATE_CHECK=1 (any value but empty or 0), or the file update-check-off in %APPDATA%\\Rakvor
  (RV Hub's setting), turns it off for every Rakvor tool.
"""

from __future__ import annotations

import os
import re
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path

from .. import TOOL_NAME, __version__

OWNER = "Panacota96"
ENV_OFF = "RV_NO_UPDATE_CHECK"
OFF_FILE = "update-check-off"
TIMEOUT = 5.0
SCHEMA = "rakvor.update-check/1"
_TRIPLE = re.compile(r"^v?(\d{1,9})\.(\d{1,9})\.(\d{1,9})$")


def config_dir() -> Path | None:
    """%APPDATA%\\Rakvor on Windows, $XDG_CONFIG_HOME/rakvor (else ~/.config/rakvor) elsewhere."""
    if sys.platform == "win32":
        a = os.environ.get("APPDATA")
        return Path(a) / "Rakvor" if a else None
    x = os.environ.get("XDG_CONFIG_HOME") or (str(Path(os.environ["HOME"]) / ".config") if os.environ.get("HOME") else "")
    return Path(x) / "rakvor" if x else None


def off_file() -> Path | None:
    d = config_dir()
    return d / OFF_FILE if d else None


def env_says_off(value: str | None) -> bool:
    return value is not None and value.strip() not in ("", "0")


def disabled() -> bool:
    f = off_file()
    return env_says_off(os.environ.get(ENV_OFF)) or bool(f and f.is_file())


def latest_page(tool: str) -> str:
    return f"https://github.com/{OWNER}/{tool}/releases/latest"


def release_page(tool: str, version: str) -> str:
    return f"https://github.com/{OWNER}/{tool}/releases/tag/v{version}"


def version_triple(v: str) -> tuple[int, int, int] | None:
    m = _TRIPLE.match(v.strip())
    return (int(m[1]), int(m[2]), int(m[3])) if m else None


def version_from_location(location: str, tool: str) -> str | None:
    path = location[len("https://github.com"):] if location.startswith("https://github.com") else location
    prefix = f"/{OWNER}/{tool}/releases/tag/"
    if not path.startswith(prefix):
        return None
    t = version_triple(path[len(prefix):])
    return f"{t[0]}.{t[1]}.{t[2]}" if t else None


def compare(current: str, latest: str, tool: str) -> dict:
    c, l = version_triple(current), version_triple(latest)
    if c is None or l is None:
        return {"status": "failed", "current": current, "reason": f"cannot compare versions {current!r} and {latest!r}"}
    if l > c:
        return {"status": "available", "current": current, "latest": latest, "url": release_page(tool, latest)}
    return {"status": "up_to_date", "current": current, "latest": latest}


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):  # noqa: D102 - the redirect is read, never followed
        return None


def latest_version(tool: str, agent: str) -> str:
    """The latest released version of `tool` (raises ValueError with the reason)."""
    if not re.fullmatch(r"rv-[a-z0-9]+(-[a-z0-9]+)*", tool):
        raise ValueError(f"not a Rakvor tool name: {tool!r}")
    opener = urllib.request.build_opener(_NoRedirect)
    req = urllib.request.Request(latest_page(tool), headers={"User-Agent": agent}, method="GET")
    try:
        with opener.open(req, timeout=TIMEOUT) as r:  # noqa: S310 - fixed https URL
            raise ValueError(f"unexpected answer from github.com: HTTP {r.status}")
    except urllib.error.HTTPError as e:
        if not 300 <= e.code < 400:
            raise ValueError(f"unexpected answer from github.com: HTTP {e.code}") from None
        location = e.headers.get("Location") or ""
    except (urllib.error.URLError, OSError) as e:
        raise ValueError(f"cannot reach github.com: {e}") from None
    v = version_from_location(location, tool)
    if v is None:
        raise ValueError(f"no published release ({location or 'no location'})")
    return v


def check_tool(tool: str, current: str, agent: str) -> dict:
    if disabled():
        return {"status": "disabled", "current": current}
    try:
        return compare(current, latest_version(tool, agent), tool)
    except ValueError as e:
        return {"status": "failed", "current": current, "reason": str(e)}


def user_agent() -> str:
    return f"{TOOL_NAME}/{__version__}"


def check() -> dict:
    return check_tool(TOOL_NAME, __version__, user_agent())


def line(c: dict, tool: str = TOOL_NAME) -> str:
    s = c["status"]
    if s == "available":
        return f"{tool} {c['latest']} is available (this is {c['current']}): {c['url']}"
    if s == "up_to_date":
        return f"{tool} {c['current']} is the latest release"
    if s == "disabled":
        return f"{tool} {c['current']}: release check turned off ({ENV_OFF} or {OFF_FILE})"
    return f"{tool} {c['current']}: could not check for a newer release ({c.get('reason', '')})"


def report(tool: str, c: dict) -> dict:
    return {**c, "schema": SCHEMA, "tool": tool, "release_page": latest_page(tool)}


def banner(c: dict, title: str, lang: str) -> str | None:
    if c.get("status") != "available":
        return None
    latest, current = c["latest"], c["current"]
    if lang == "es":
        return f"Hay una versión nueva de {title}: {latest} (usted usa {current}). Descárguela y verifique su firma antes de usarla."
    if lang == "fr":
        return (f"Une nouvelle version de {title} est publiée : {latest} (vous utilisez {current}). "
                "Téléchargez-la et vérifiez sa signature avant de l'utiliser.")
    return f"A new release of {title} is published: {latest} (you are using {current}). Download it and verify its signature before use."


class Background:
    """A check on a thread; `result` is None while it runs."""

    def __init__(self) -> None:
        self.result: dict | None = None
        if disabled():
            self.result = {"status": "disabled", "current": __version__}
            return
        threading.Thread(target=self._run, name="rv-update-check", daemon=True).start()

    def _run(self) -> None:
        self.result = check()
