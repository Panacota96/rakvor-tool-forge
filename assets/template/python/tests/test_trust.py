"""RFC 3161 timestamps, PDF signatures and custody forms, against a local test TSA (tests/fake_tsa.py)."""

import http.server
import io
import json
import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import fake_tsa  # noqa: E402
from rv_hashinv.core import RunConfig, run  # noqa: E402
from rv_hashinv.core.audit import AuditLog, heads, verify  # noqa: E402
from rv_hashinv.core.trust import (  # noqa: E402
    MAX_RESPONSE,
    TimestampError,
    _gen_unix,
    build_request,
    load_ca_bundle,
    parse_response,
    request_timestamp,
    timestamp_head,
    validate_chain,
    verify_timestamps,
    verify_timestamps_with,
    verify_token,
)
from rv_hashinv.core.verify import verify_log  # noqa: E402


@pytest.fixture(scope="module")
def pki(tmp_path_factory):
    d = tmp_path_factory.mktemp("pki")
    fake_tsa.make_certs(d)
    srv, url = fake_tsa.serve(d)
    yield d, url
    srv.shutdown()


def _run(tmp_path, sample, **kw):
    cfg = RunConfig(inputs=[sample], out_dir=tmp_path / "out", algorithms=["sha256", "blake3"], langs=["en"], actor="tester", **kw)
    return run(cfg, lambda e: None, threading.Event())


def test_timestamp_covers_chain_head_and_tamper_is_caught(tmp_path, sample, pki):
    _, url = pki
    s = _run(tmp_path, sample, tsa_url=url)
    assert s.timestamp["status"] == "ok"
    log = s.log_dir / "audit.jsonl"
    assert verify(log)[1] == []
    assert verify_timestamps(log) == []
    meta = json.loads(next(s.log_dir.glob("timestamp-*.json")).read_text())
    assert heads(log)[meta["covers_seq"]] == meta["head"]
    v = verify_log(log)
    assert v.problems == [] and v.truncation == (
        f"truncation: entries up to #{meta['covers_seq']} are anchored by the RFC 3161 timestamp (timestamp-{meta['covers_seq']:05d}.json); "
        "3 entries after it (timestamp_obtained, statistics_report_written, run_finished) cannot be proven complete"
    )
    # Removing entries back to (and including) the timestamped one is detected through covers_seq.
    full = log.read_text("utf-8")
    log.write_text("".join(full.splitlines(keepends=True)[:-4]), encoding="utf-8")
    assert any(p.startswith(f"truncated: {meta['file']} covers entry #{meta['covers_seq']}") for p in verify_log(log).problems)
    log.write_text(full, encoding="utf-8")
    tsr = s.log_dir / meta["file"]
    tsr.write_bytes(tsr.read_bytes()[:-1] + b"\x00")
    assert verify_timestamps(log)
    assert verify_log(log).problems


def test_offline_tsa_is_pending_then_added_later(tmp_path, sample, pki):
    _, url = pki
    s = _run(tmp_path, sample, tsa_url="http://127.0.0.1:9/tsr")
    assert s.timestamp["status"] == "pending"
    assert '"timestamp_pending"' in (s.log_dir / "audit.jsonl").read_text()
    audit = AuditLog.resume(s.log_dir, "later")
    ts = timestamp_head(s.log_dir, audit.seq, audit.head, url)
    audit.record("timestamp_obtained", ts.file, ts.to_json())
    audit.close()
    log = s.log_dir / "audit.jsonl"
    assert verify(log)[1] == [] and verify_timestamps(log) == []


def test_tsa_signature_is_checked_and_the_chain_only_with_tsa_ca(tmp_path, sample, pki):
    d, url = pki
    s = _run(tmp_path, sample, tsa_url=url)
    log = s.log_dir / "audit.jsonl"
    plain = verify_log(log)
    assert plain.problems == []
    assert any("TSA signature valid (signer: " in t and "Rakvor TEST TSA" in t and "chain not checked" in t for t in plain.timestamps)
    chained = verify_log(log, load_ca_bundle(d / "ca.pem"))
    assert chained.problems == []
    assert any("chain validated to " in t and "Rakvor TEST CA (revocation not checked)" in t for t in chained.timestamps)
    # A CA that did not issue the TSA certificate: the chain is not trusted.
    other = tmp_path / "other"
    fake_tsa.make_certs(other)
    wrong = verify_log(log, load_ca_bundle(other / "ca.pem"))
    assert any("TSA certificate chain not validated: no trusted issuer" in p for p in wrong.problems), wrong.problems


def _tamper_signature(der: bytes) -> bytes:
    """Flips one bit of the SignerInfo signature value, keeping the file parseable."""
    from asn1crypto import tsp

    resp = tsp.TimeStampResp.load(der)
    si = resp["time_stamp_token"]["content"]["signer_infos"][0]
    sig = bytearray(si["signature"].native)
    sig[len(sig) // 2] ^= 0x01
    i = der.index(si["signature"].native)
    return der[:i] + bytes(sig) + der[i + len(sig) :]


def test_forged_token_is_rejected_even_when_its_file_digest_is_updated(tmp_path, sample, pki):
    _, url = pki
    s = _run(tmp_path, sample, tsa_url=url)
    log = s.log_dir / "audit.jsonl"
    meta_path = next(s.log_dir.glob("timestamp-*.json"))
    meta = json.loads(meta_path.read_text())
    tsr = s.log_dir / meta["file"]
    forged = _tamper_signature(tsr.read_bytes())
    tsr.write_bytes(forged)
    # The recorded digest no longer matches: the altered file is caught first ...
    assert any("missing or altered" in p for p in verify_timestamps(log))
    # ... and an attacker who also rewrites the metadata and the log entry is caught by the TSA signature.
    import hashlib

    new = hashlib.sha256(forged).hexdigest()
    meta_path.write_text(json.dumps({**meta, "token_sha256": new}), encoding="utf-8")
    lines = log.read_text("utf-8").splitlines(keepends=True)
    log.write_text("".join(x.replace(meta["token_sha256"], new) for x in lines), encoding="utf-8")
    problems = verify_timestamps_with(log, None).problems
    assert any("TSA signature value does not verify" in p for p in problems), problems


def test_missing_metadata_and_unrecorded_tokens_are_problems(tmp_path, sample, pki):
    _, url = pki
    s = _run(tmp_path, sample, tsa_url=url)
    log = s.log_dir / "audit.jsonl"
    meta_path = next(s.log_dir.glob("timestamp-*.json"))
    meta_text = meta_path.read_text()
    meta_path.unlink()
    assert any("its metadata" in p and "missing or unreadable" in p for p in verify_timestamps(log))
    meta_path.write_text(meta_text)
    stray = s.log_dir / "timestamp-99999.tsr"
    stray.write_bytes(b"\x30\x00")
    assert any("neither the audit log nor a metadata file records" in p for p in verify_timestamps(log))
    stray.unlink()
    assert verify_timestamps(log) == []


def test_verify_log_cli_tsa_ca_exit_codes(tmp_path, sample, pki):
    from click.testing import CliRunner

    from rv_hashinv.cli import cli

    d, url = pki
    s = _run(tmp_path, sample, tsa_url=url)
    r = CliRunner()
    ok = r.invoke(cli, ["verify-log", str(s.log_dir), "--tsa-ca", str(d / "ca.pem")])
    assert ok.exit_code == 0, ok.output
    assert "chain validated to" in ok.output
    plain = r.invoke(cli, ["verify-log", str(s.log_dir)])
    assert plain.exit_code == 0 and "chain not checked" in plain.output
    unreadable = r.invoke(cli, ["verify-log", str(s.log_dir), "--tsa-ca", str(tmp_path / "nope.pem")])
    assert unreadable.exit_code == 1
    other = tmp_path / "other"
    fake_tsa.make_certs(other)
    untrusted = r.invoke(cli, ["verify-log", str(s.log_dir), "--tsa-ca", str(other / "ca.pem")])
    assert untrusted.exit_code == 3 and "BROKEN" in untrusted.output


def test_request_rejects_unreachable_tsa():
    with pytest.raises(TimestampError):
        request_timestamp(b"\0" * 32, "http://127.0.0.1:9/tsr", timeout=2)


def test_tsa_response_larger_than_1_mib_is_refused():
    class Huge(http.server.BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            self.rfile.read(int(self.headers.get("Content-Length", 0)))
            self.send_response(200)
            self.send_header("Content-Type", "application/timestamp-reply")
            self.end_headers()
            self.wfile.write(b"\x30" * (MAX_RESPONSE + 4096))

        def log_message(self, *args):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), Huge)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        with pytest.raises(TimestampError, match="larger than 1048576 bytes"):
            request_timestamp(b"\0" * 32, f"http://127.0.0.1:{srv.server_address[1]}/tsr", timeout=5)
    finally:
        srv.shutdown()
    assert MAX_RESPONSE == 1 << 20


def _der(tag: int, content: bytes) -> bytes:
    n = len(content)
    if n < 0x80:
        head = bytes([tag, n])
    elif n < 0x100:
        head = bytes([tag, 0x81, n])
    else:
        head = bytes([tag, 0x82, n >> 8, n & 0xFF])
    return head + content


def _crafted_response(digest: bytes, gen_time: bytes, nonce: bytes | None) -> bytes:
    """A synthetic TimeStampResp (status granted, SignedData with no signer) whose TSTInfo has the raw genTime
    and nonce given: enough for parse_response, never a token whose signature verifies."""
    sha256 = _der(0x06, bytes([0x60, 0x86, 0x48, 0x01, 0x65, 0x03, 0x04, 0x02, 0x01]))
    alg = _der(0x30, sha256 + b"\x05\x00")
    tst = _der(0x02, b"\x01") + _der(0x06, b"\x2a\x03\x04") + _der(0x30, alg + _der(0x04, digest))
    tst += _der(0x02, b"\x2a") + _der(0x18, gen_time)
    if nonce is not None:
        tst += _der(0x02, nonce)
    oid_tst = _der(0x06, bytes([0x2A, 0x86, 0x48, 0x86, 0xF7, 0x0D, 0x01, 0x09, 0x10, 0x01, 0x04]))
    encap = _der(0x30, oid_tst + _der(0xA0, _der(0x04, _der(0x30, tst))))
    sd = _der(0x30, _der(0x02, b"\x03") + b"\x31\x00" + encap + b"\x31\x00")
    oid_sd = _der(0x06, bytes([0x2A, 0x86, 0x48, 0x86, 0xF7, 0x0D, 0x01, 0x07, 0x02]))
    ci = _der(0x30, oid_sd + _der(0xA0, sd))
    return _der(0x30, _der(0x30, _der(0x02, b"\x00")) + ci)


def test_malformed_gen_time_is_an_error():
    """SEC-HI-01 (same rule as the Rust profile): genTime is checked on its bytes, YYYYMMDDhhmmss[.f…]Z only."""
    d = b"\x07" * 32
    assert parse_response(_crafted_response(d, b"20261124120000Z", None), d, None)[0] == "2026-11-24T12:00:00Z"
    assert parse_response(_crafted_response(d, b"20261020182422.123Z", None), d, None)[0] == "2026-10-20T18:24:22Z"
    for bad in [
        "1234567890123éZ".encode(),
        "20261124120000éZ".encode(),
        b"20261124120000" + "é".encode() * 150,
        b"",
        b"2026112412000Z",
        b"2026112412Z",
        b"20261124120000",
        b"20261124120000.Z",
        b"20261124120000+0100",
        b"20261124120000Zx",
        b"20261324120000Z",
        b"20261124240000Z",
        b"20261124120061Z",
        # A leap second only exists at 23:59 UTC (CR-R018-02).
        b"20261124120060Z",
        b"20161231235860Z",
        b"20161231225960Z",
        # Dates that do not exist (Codex on rakvor-tool-forge#24): 30 February, 29 February of a common year
        # (2025, and 1900: a century that is not a multiple of 400), 31 April, year 0000.
        b"20260230120000Z",
        b"20250229120000Z",
        b"19000229120000Z",
        b"20260431120000Z",
        b"00001124120000Z",
        b"00000101000000Z",
    ]:
        with pytest.raises(TimestampError, match="bad genTime"):
            parse_response(_crafted_response(d, bad, None), d, None)
        with pytest.raises(TimestampError):
            verify_token(_crafted_response(d, bad, None), d, None)
    # Leap days that exist (2024; 2000 is a multiple of 400), month ends, and the first possible year.
    for good, iso in [
        (b"20240229120000Z", "2024-02-29T12:00:00Z"),
        (b"20000229000000Z", "2000-02-29T00:00:00Z"),
        (b"20260228235959Z", "2026-02-28T23:59:59Z"),
        (b"20260430120000Z", "2026-04-30T12:00:00Z"),
        (b"20261231120000Z", "2026-12-31T12:00:00Z"),
        (b"00010101000000Z", "0001-01-01T00:00:00Z"),
        (b"20161231235960Z", "2016-12-31T23:59:60Z"),
    ]:
        assert parse_response(_crafted_response(d, good, None), d, None)[0] == iso, good


def test_leap_second_gen_time_is_usable_for_the_chain():
    """CR-R018-02: a 23:59:60 genTime is a valid instant (one second after 23:59:59) when the chain is checked
    at genTime, not a "bad genTime" (datetime cannot hold second 60)."""
    assert _gen_unix("2016-12-31T23:59:60Z") == _gen_unix("2017-01-01T00:00:00Z") == 1483228800
    assert _gen_unix("2016-12-31T23:59:59Z") == 1483228799
    with pytest.raises(TimestampError, match="bad genTime"):
        _gen_unix("2026-11-24T12:00:60Z")
    # The shared chain fixtures are checked at 2026-06-01T00:00:00Z; 2026-05-31T23:59:60Z is the same instant.
    case = Path(__file__).parent / "fixtures" / "tsa-chains" / "cases" / "ok_root_issues_tsa"
    leaf = load_ca_bundle(case / "leaf.pem")[0]
    chain = load_ca_bundle(case / "chain.pem") if (case / "chain.pem").exists() else []
    anchors = load_ca_bundle(case / "anchors.pem")
    want = validate_chain(leaf, chain, anchors, "2026-06-01T00:00:00Z")
    assert validate_chain(leaf, chain, anchors, "2026-05-31T23:59:60Z") == want


def test_nonce_is_required_in_the_response_when_sent():
    """SEC-HI-02: RFC 3161 §2.4.2, a request's nonce must come back unchanged."""
    d = b"\x09" * 32
    g = b"20261124120000Z"
    with pytest.raises(TimestampError, match=r"nonce missing \(possible replay\)"):
        parse_response(_crafted_response(d, g, None), d, 0x123456)
    assert parse_response(_crafted_response(d, g, None), d, None)[0] == "2026-11-24T12:00:00Z"
    assert parse_response(_crafted_response(d, g, b"\x12\x34\x56"), d, 0x123456)[0] == "2026-11-24T12:00:00Z"
    with pytest.raises(TimestampError, match=r"nonce mismatch \(possible replay\)"):
        parse_response(_crafted_response(d, g, b"\x12\x34\x57"), d, 0x123456)
    _, a = build_request(d)
    _, b = build_request(d)
    assert a != b and 0 < a < 1 << 127 and 0 < b < 1 << 127


def test_every_pdf_is_signed_with_the_examiner_certificate(tmp_path, sample, pki):
    d, _ = pki
    from pyhanko.pdf_utils.reader import PdfFileReader
    from pyhanko.sign.validation import validate_pdf_signature

    s = _run(tmp_path, sample, sign_cert=d / "examiner.p12", sign_password="test", jurisdiction="fr")
    pdfs = list(s.report_dir.rglob("*.pdf"))
    assert pdfs
    for p in pdfs:
        sig = PdfFileReader(io.BytesIO(p.read_bytes())).embedded_signatures
        assert sig, p.name
        st = validate_pdf_signature(sig[0])
        assert st.intact and st.valid, p.name
        assert "Ana Ruiz" in st.signing_cert.subject.human_friendly
    log = (s.log_dir / "audit.jsonl").read_text()
    assert '"signing_certificate_loaded"' in log and "password" not in log
    # The reports print the signer's certificate fingerprint, as logged at signing_certificate_loaded.
    fp = next(json.loads(x) for x in log.splitlines() if '"signing_certificate_loaded"' in x)["details"]["certificate_sha256"]
    assert len(fp) == 64
    item = next((s.report_dir / "items").iterdir())
    assert fp in (item / "technical.en.html").read_text("utf-8")
    assert json.loads((s.report_dir / "statistics.json").read_text("utf-8"))["run"]["pdf_signer_certificate_sha256"] == fp


def test_wrong_certificate_password_fails_and_is_audited(tmp_path, sample, pki):
    d, _ = pki
    with pytest.raises(ValueError):
        _run(tmp_path, sample, sign_cert=d / "examiner.p12", sign_password="wrong")
    log = next((tmp_path / "out" / "logs").iterdir()) / "audit.jsonl"
    assert '"run_failed"' in log.read_text()


@pytest.mark.parametrize("jurisdiction,lang,title", [("co", "es", "Registro de cadena de custodia"), ("fr", "fr", "Fiche de traçabilité")])
def test_custody_form_per_item_in_country_language_and_english(tmp_path, sample, jurisdiction, lang, title):
    s = _run(tmp_path, sample, jurisdiction=jurisdiction)
    for item in (s.report_dir / "items").iterdir():
        for f in ("custody.json", f"custody.{lang}.html", f"custody.{lang}.pdf", "custody.en.html", "custody.en.pdf"):
            assert (item / f).is_file(), f
        assert title in (item / f"custody.{lang}.html").read_text("utf-8")
        cj = json.loads((item / "custody.json").read_text())
        assert cj["schema"] == "rakvor.item-custody/1" and cj["jurisdiction"] == jurisdiction and len(cj["hashes"]) == 2
