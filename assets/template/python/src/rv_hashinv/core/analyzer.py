"""Domain logic — THE PART EACH NEW TOOL REPLACES.

This reference analyzer inventories evidence: it detects the real file type from its signature
(magic bytes) and flags extension mismatches, programs under other names and empty files.
Duplicates are found later, across items, by run.py. A new tool keeps the `analyze` signature
and the Finding shape so audit, reports, CLI and GUI work unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import PurePath

SEVERITY_ORDER = {"info": 0, "low": 1, "medium": 2, "high": 3}


@dataclass
class Finding:
    code: str
    severity: str  # info | low | medium | high
    params: dict[str, str] = field(default_factory=dict)

    def to_json(self) -> dict:
        return {"code": self.code, "severity": self.severity, "params": dict(sorted(self.params.items()))}


@dataclass
class Analysis:
    detected_type: str | None
    extension: str | None
    findings: list[Finding]


# (name, offset, magic, expected extensions, executable)
SIGNATURES = [
    ("PDF document", 0, b"%PDF-", ("pdf",), False),
    ("PNG image", 0, b"\x89PNG\r\n\x1a\n", ("png",), False),
    ("JPEG image", 0, b"\xff\xd8\xff", ("jpg", "jpeg", "jpe", "jfif"), False),
    ("GIF image", 0, b"GIF8", ("gif",), False),
    ("ZIP / Office Open XML", 0, b"PK\x03\x04", ("zip", "docx", "xlsx", "pptx", "odt", "ods", "jar", "apk", "epub"), False),
    ("OLE2 (legacy Office, MSG)", 0, b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1", ("doc", "xls", "ppt", "msg"), False),
    ("Windows executable (PE)", 0, b"MZ", ("exe", "dll", "sys", "scr", "com", "efi"), True),
    ("Linux executable (ELF)", 0, b"\x7fELF", ("so", "bin", "elf", "o"), True),
    ("SQLite database", 0, b"SQLite format 3\x00", ("db", "sqlite", "sqlite3"), False),
    ("7-Zip archive", 0, b"7z\xbc\xaf\x27\x1c", ("7z",), False),
    ("RAR archive", 0, b"Rar!\x1a\x07", ("rar",), False),
    ("GZIP archive", 0, b"\x1f\x8b", ("gz", "tgz"), False),
    ("Windows event log (EVTX)", 0, b"ElfFile\x00", ("evtx",), False),
    ("Windows registry hive", 0, b"regf", ("dat", "hve"), False),
    ("MP4 / QuickTime video", 4, b"ftyp", ("mp4", "m4v", "mov", "m4a", "3gp", "heic"), False),
]
TEXT_EXTENSIONS = ("txt", "csv", "log", "xml", "json", "html")


def _le16(h: bytes, at: int) -> int | None:
    return int.from_bytes(h[at : at + 2], "little") if len(h) >= at + 2 else None


def confirm_mz(h: bytes, size: int) -> str | None:
    """`MZ` (1 in 65,536 for random data): a PE when `PE\\0\\0` is at the offset stored at 0x3C, or when that offset
    points past the bytes read but inside the file (the loader accepts up to 256 MiB: a moved header cannot hide an
    executable); else a DOS executable when the DOS header is self-consistent; else not an executable."""
    if len(h) >= 0x40:
        lfanew = int.from_bytes(h[0x3C:0x40], "little")
        if lfanew >= 0x40 and h[lfanew : lfanew + 4] == b"PE\x00\x00":
            return "Windows executable (PE)"
        if 0x40 <= lfanew < 0x1000_0000 and lfanew + 4 <= size and lfanew + 4 > len(h):
            return "Windows executable (PE, header after the first 4 KiB, not checked)"
    last, pages, hdr = _le16(h, 2), _le16(h, 4), _le16(h, 8)
    if last is None or pages is None or hdr is None or pages < 1:
        return None
    image = (pages - 1) * 512 + (512 if last == 0 else last)
    return "DOS executable (MZ)" if last < 512 and hdr >= 2 and hdr * 16 <= image <= size else None


def confirm_gzip(h: bytes, size: int) -> str | None:
    """`1F 8B`: gzip only with compression method 8 (deflate) and the reserved flag bits clear (RFC 1952)."""
    return "GZIP archive" if len(h) >= 4 and h[2] == 8 and h[3] & 0xE0 == 0 else None


# Second checks for the short magics that random data also matches.
CONFIRM = {"Windows executable (PE)": confirm_mz, "GZIP archive": confirm_gzip}


def detect(head: bytes, size: int = 0):
    """(signature, detected name) of the first matching signature, or None."""
    for sig in SIGNATURES:
        name, off, magic, _, _ = sig
        if head[off : off + len(magic)] == magic:
            check = CONFIRM.get(name)
            found = check(head, size) if check else name
            if found:
                return sig, found
    return None


def analyze(file_name: str, size: int, head: bytes) -> Analysis:
    suffix = PurePath(file_name).suffix
    ext = suffix[1:].lower() if suffix else None
    hit = detect(head, size)
    sig, found = hit if hit else (None, None)
    findings: list[Finding] = []
    if size == 0:
        findings.append(Finding("EMPTY_FILE", "info"))
    if sig and ext:
        _, _, _, exts, executable = sig
        name = found
        if ext not in exts:
            if executable:
                findings.append(Finding("EXECUTABLE_CONTENT", "high", {"detected": name, "extension": ext}))
            elif any(ext in s[3] for s in SIGNATURES) or ext in TEXT_EXTENSIONS:
                findings.append(Finding("EXTENSION_MISMATCH", "medium", {"detected": name, "extension": ext}))
    return Analysis(found, ext, findings)
