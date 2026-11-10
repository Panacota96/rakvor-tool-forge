"""Trust features: RFC 3161 timestamps of the audit chain and digital signatures on PDF reports.

Timestamp
  The head of the audit chain (the entry_hash of the last entry, itself a SHA-256) is sent to a
  time-stamping authority (TSA). The signed token proves the log — and therefore every evidence hash,
  finding and report digest recorded before it — existed at that time. Stored next to the log:
    timestamp-<seq>.tsr   the TSA's DER response (verify: openssl ts -verify -digest <head> -in … -CAfile …)
    timestamp-<seq>.json  tsa, gen_time, covers_seq, head, token_sha256
  If the TSA cannot be reached the run still completes: a `timestamp_pending` entry is logged and
  `rv-hashinv timestamp <log-dir>` adds the timestamp later (logged as `timestamp_obtained`).

PDF signature
  Each PDF is signed with the examiner's own certificate (.p12/.pfx). The password is never logged.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import io
import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

from asn1crypto import pem, tsp, x509

DEFAULT_TSA = "http://timestamp.digicert.com"
TIMEOUT_S = 15
# A time-stamp response is a few KiB; anything larger than 1 MiB is refused unread (same limit as the Rust profile).
MAX_RESPONSE = 1 << 20


class TimestampError(Exception):
    pass


@dataclass
class Timestamp:
    tsa: str
    gen_time: str
    covers_seq: int
    head: str
    token_sha256: str
    file: str

    def to_json(self) -> dict:
        return {
            "tsa": self.tsa,
            "gen_time": self.gen_time,
            "covers_seq": self.covers_seq,
            "head": self.head,
            "token_sha256": self.token_sha256,
            "file": self.file,
        }


def build_request(digest: bytes) -> tuple[bytes, int]:
    nonce = int.from_bytes(os.urandom(8), "big")
    req = tsp.TimeStampReq(
        {
            "version": "v1",
            "message_imprint": {"hash_algorithm": {"algorithm": "sha256"}, "hashed_message": digest},
            "nonce": nonce,
            "cert_req": True,
        }
    )
    return req.dump(), nonce


def parse_response(der: bytes, digest: bytes, nonce: int | None) -> tuple[str, bytes]:
    """Returns (gen_time ISO, token DER) or raises TimestampError."""
    resp = tsp.TimeStampResp.load(der)
    status = resp["status"]["status"].native
    if status not in ("granted", "granted_with_mods"):
        raise TimestampError(f"TSA refused the request: {status}")
    token = resp["time_stamp_token"]
    info = token["content"]["encap_content_info"]["content"].parsed
    if info["message_imprint"]["hashed_message"].native != digest:
        raise TimestampError("the token does not cover the requested digest")
    if nonce is not None and info["nonce"].native not in (None, nonce):
        raise TimestampError("nonce mismatch (possible replay)")
    gen = info["gen_time"].native
    return gen.strftime("%Y-%m-%dT%H:%M:%SZ"), token.dump()


def request_timestamp(digest: bytes, url: str, timeout: float = TIMEOUT_S) -> tuple[str, bytes, bytes]:
    """(gen_time, token DER, full response DER)."""
    if not url.lower().startswith(("http://", "https://")):
        raise TimestampError(f"unsupported TSA URL scheme: {url}")
    body, nonce = build_request(digest)
    req = urllib.request.Request(  # noqa: S310 - http(s) only, checked above
        url, data=body, headers={"Content-Type": "application/timestamp-query"}, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 - URL chosen by the examiner
            der = r.read(MAX_RESPONSE + 1)
    except (urllib.error.URLError, OSError, TimeoutError) as e:
        raise TimestampError(f"cannot reach {url}: {getattr(e, 'reason', e)}") from e
    if len(der) > MAX_RESPONSE:
        raise TimestampError(f"cannot read TSA response: larger than {MAX_RESPONSE} bytes (refused)")
    try:
        gen, token = parse_response(der, digest, nonce)
    except (ValueError, KeyError, TypeError) as e:  # asn1crypto parse errors
        raise TimestampError(f"not a time-stamp response: {e}") from e
    return gen, token, der


def timestamp_head(log_dir: Path, seq: int, head: str, url: str) -> Timestamp:
    gen, _token, der = request_timestamp(bytes.fromhex(head), url)
    name = f"timestamp-{seq:05d}.tsr"
    (log_dir / name).write_bytes(der)
    ts = Timestamp(url, gen, seq, head, hashlib.sha256(der).hexdigest(), name)
    (log_dir / f"timestamp-{seq:05d}.json").write_text(json.dumps(ts.to_json(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return ts


@dataclass
class TimestampVerification:
    """Outcome of verify_timestamps_with: problems (any → the log does not verify) and one line per token."""

    problems: list[str] = field(default_factory=list)
    # One line per token whose TSA signature (and, with anchors, chain) verified.
    tokens: list[str] = field(default_factory=list)
    # One line per token known (recorded in the log or found next to it).
    listed: list[str] = field(default_factory=list)
    # (covered entry, token file) for every token whose covered entry is in the log: the truncation anchors.
    covers: list[tuple[int, str]] = field(default_factory=list)


def _is_token_name(name: str) -> bool:
    """A token file name as the tools write it (timestamp-<seq>.tsr): a plain name in the log folder."""
    return name.startswith("timestamp-") and name.endswith(".tsr") and len(name) <= 64 and not any(ch in name for ch in "/\\:") and ".." not in name


def _read_json(path: Path) -> dict | None:
    try:
        v = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return v if isinstance(v, dict) else None


def _int(v: object) -> int | None:
    return v if isinstance(v, int) and not isinstance(v, bool) and v >= 0 else None


def verify_timestamps(audit_path: Path) -> list[str]:
    return verify_timestamps_with(audit_path, None).problems


def verify_timestamps_with(audit_path: Path, anchors: list | None) -> TimestampVerification:
    """Checks the RFC 3161 tokens of the audit log at audit_path, driven by the log itself so that deleting a
    file cannot skip a check (fail closed) — the same rules as the Rust profile's trust.rs:

    1. every timestamp_obtained entry names a token: its .tsr must be next to the log with the recorded SHA-256,
       its timestamp-<seq>.json metadata must be there and agree with the entry, the entry it covers must still
       be in the log with that hash, and the token's TSA signature (and, with anchors — a CA bundle, --tsa-ca —
       its certificate chain at genTime) must verify;
    2. a timestamp-*.json next to the log that no entry records is checked the same way from its own fields;
    3. a timestamp-*.tsr that neither an entry nor a metadata file names cannot be checked and is a problem.

    Revocation of the TSA certificate is never checked."""
    from .audit import heads as audit_heads

    audit_path = Path(audit_path)
    log_dir = audit_path.parent
    heads = audit_heads(audit_path)
    entries = []
    try:
        for line in audit_path.read_text(encoding="utf-8").splitlines():
            try:
                e = json.loads(line)
            except ValueError:
                continue
            if isinstance(e, dict):
                entries.append(e)
    except OSError:
        pass
    out = TimestampVerification()
    named: list[str] = []

    def claim(file: str, covers: int | None, tsa: str, gen_time: str) -> tuple[int, bytes] | None:
        if covers is None:
            out.problems.append(f"{file}: the entry it covers is not recorded")
            return None
        out.listed.append(f"timestamp {gen_time} by {tsa} covers entry #{covers}")
        head = heads.get(covers)
        try:
            head_b = bytes.fromhex(head) if head else None
        except ValueError:
            head_b = None
        if head_b is None:
            out.problems.append(f"truncated: {file} covers entry #{covers}, which is not in the log (entries removed from the end)")
            return None
        if covers > 0:
            out.covers.append((covers, file))
        return covers, head_b

    def check(file: str, seq: int, head: bytes, tsa: str, der: bytes) -> None:
        try:
            c = verify_token(der, head, anchors)
        except TimestampError as e:
            out.problems.append(f"{file}: {e} (entry #{seq})")
            return
        out.tokens.append(f"timestamp {c.gen_time} by {tsa} covers entry #{seq}: {c.describe()}")

    def read(name: str) -> bytes:
        try:
            return (log_dir / name).read_bytes()
        except OSError:
            return b""

    # 1. Tokens the audit log records.
    for e in (e for e in entries if e.get("action") == "timestamp_obtained"):
        d = e.get("details") if isinstance(e.get("details"), dict) else {}
        file = d.get("file") if isinstance(d.get("file"), str) else ""
        at = _int(e.get("seq")) or 0
        if not _is_token_name(file):
            out.problems.append(f"entry #{at} records timestamp token {file or '?'}, which is missing or altered (not a token file name)")
            continue
        if file in named:
            continue
        named.append(file)
        want = d.get("token_sha256") if isinstance(d.get("token_sha256"), str) else ""
        covers = _int(d.get("covers_seq"))
        tsa = d.get("tsa") if isinstance(d.get("tsa"), str) else "?"
        gen_time = d.get("gen_time") if isinstance(d.get("gen_time"), str) else "?"
        if covers is not None and covers >= at:
            out.problems.append(f"entry #{at} records timestamp token {file} covering a later entry")
            continue
        claimed = claim(file, covers, tsa, gen_time)
        der = read(file)
        if not der or hashlib.sha256(der).hexdigest() != want:
            out.problems.append(f"entry #{at} records timestamp token {file}, which is missing or altered")
            continue
        meta_name = file[:-4] + ".json"
        m = _read_json(log_dir / meta_name)
        if m is None:
            out.problems.append(f"{file}: its metadata {meta_name} is missing or unreadable (entry #{at} records the token)")
        elif m.get("file") != file or m.get("token_sha256") != want or _int(m.get("covers_seq")) != covers:
            out.problems.append(f"{file}: its metadata {meta_name} does not match entry #{at} of the audit log")
        if claimed is not None:
            check(file, claimed[0], claimed[1], tsa, der)

    # 2. and 3. Token files next to the log that no entry records.
    try:
        on_disk = sorted(p.name for p in log_dir.iterdir() if p.name.startswith("timestamp-"))
    except OSError:
        on_disk = []
    for m_name in (n for n in on_disk if n.endswith(".json")):
        stem = m_name[:-5]
        if any(n[:-4] == stem for n in named):
            continue
        meta = _read_json(log_dir / m_name)
        if meta is None:
            out.problems.append(f"{log_dir / m_name}: unreadable")
            continue
        file = meta.get("file") if isinstance(meta.get("file"), str) else ""
        if not _is_token_name(file):
            out.problems.append(f"{m_name}: names no token file ({file!r})")
            continue
        named.append(file)
        tsa = meta.get("tsa") if isinstance(meta.get("tsa"), str) else "?"
        gen_time = meta.get("gen_time") if isinstance(meta.get("gen_time"), str) else "?"
        claimed = claim(file, _int(meta.get("covers_seq")), tsa, gen_time)
        if claimed is None:
            continue
        der = read(file)
        if hashlib.sha256(der).hexdigest() != meta.get("token_sha256"):
            out.problems.append(f"{file}: token file missing or altered")
            continue
        check(file, claimed[0], claimed[1], tsa, der)
    for t in (n for n in on_disk if n.endswith(".tsr")):
        if t not in named:
            out.problems.append(f"{t}: a timestamp token that neither the audit log nor a metadata file records (cannot be checked)")
    return out


# ---------------------------------------------------------------- TSA token signature (RFC 3161 / RFC 5652)

OID_CT_TSTINFO = "1.2.840.113549.1.9.16.1.4"
OID_ATTR_CONTENT_TYPE = "1.2.840.113549.1.9.3"
OID_ATTR_MESSAGE_DIGEST = "1.2.840.113549.1.9.4"
OID_ATTR_SIGNING_CERT = "1.2.840.113549.1.9.16.2.12"
OID_ATTR_SIGNING_CERT_V2 = "1.2.840.113549.1.9.16.2.47"
OID_KP_TIME_STAMPING = "1.3.6.1.5.5.7.3.8"
OID_ANY_EKU = "2.5.29.37.0"
OID_EXT_EKU = "2.5.29.37"
OID_EXT_BASIC_CONSTRAINTS = "2.5.29.19"
OID_EXT_SKI = "2.5.29.14"
OID_EXT_KEY_USAGE = "2.5.29.15"
OID_EXT_SAN = "2.5.29.17"
OID_EXT_NAME_CONSTRAINTS = "2.5.29.30"
OID_EXT_CERT_POLICIES = "2.5.29.32"
OID_EXT_AKI = "2.5.29.35"
OID_EMAIL_ADDRESS = "1.2.840.113549.1.9.1"
OID_RSA = "1.2.840.113549.1.1.1"
_MD_BY_OID = {
    "1.3.14.3.2.26": "sha1",
    "2.16.840.1.101.3.4.2.1": "sha256",
    "2.16.840.1.101.3.4.2.2": "sha384",
    "2.16.840.1.101.3.4.2.3": "sha512",
}
_RSA_SIG_MD = {
    "1.2.840.113549.1.1.11": "sha256",
    "1.2.840.113549.1.1.12": "sha384",
    "1.2.840.113549.1.1.13": "sha512",
}
# Critical extensions the path validation processes (any other critical extension rejects the path).
HANDLED_CRITICAL = (
    OID_EXT_BASIC_CONSTRAINTS,
    OID_EXT_KEY_USAGE,
    OID_EXT_EKU,
    OID_EXT_NAME_CONSTRAINTS,
    OID_EXT_CERT_POLICIES,
    OID_EXT_SAN,
    OID_EXT_SKI,
    OID_EXT_AKI,
)
# At most this many certificates below the trust anchor (the TSA certificate and its intermediates).
MAX_PATH = 8
# At most this many candidate issuers tried (signature checks) while building one path.
MAX_CANDIDATES = 512


@dataclass
class TokenCheck:
    """What verify_token established about one token."""

    gen_time: str
    tsa_subject: str
    # Subject of the trust anchor the TSA certificate chains to; None = chain not validated.
    anchor: str | None

    def describe(self) -> str:
        if self.anchor is not None:
            return f"TSA signature valid (signer: {self.tsa_subject}); chain validated to {self.anchor} (revocation not checked)"
        return f"TSA signature valid (signer: {self.tsa_subject}); chain not checked (pass --tsa-ca <CA bundle> to validate it)"


def _subject(c: x509.Certificate) -> str:
    return c.subject.human_friendly


def _ext(c: x509.Certificate, oid: str):
    for e in c["tbs_certificate"]["extensions"] or []:
        if e["extn_id"].dotted == oid:
            return e
    return None


def _digest(md: str, data: bytes) -> bytes:
    return hashlib.new(md, data).digest()


def _rsa_verify(c: x509.Certificate, md: str, data: bytes, sig: bytes) -> None:
    """RSA PKCS#1 v1.5 verification of sig over data hashed with md (raises TimestampError)."""
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding, rsa

    try:
        key = serialization.load_der_public_key(c["tbs_certificate"]["subject_public_key_info"].dump())
    except (ValueError, TypeError) as e:
        raise TimestampError("the signer key is not RSA (not supported: signature not checked)") from e
    if not isinstance(key, rsa.RSAPublicKey):
        raise TimestampError("the signer key is not RSA (not supported: signature not checked)")
    if md == "sha1":
        raise TimestampError("SHA-1 signatures are not supported (signature not checked)")
    h = {"sha256": hashes.SHA256(), "sha384": hashes.SHA384(), "sha512": hashes.SHA512()}[md]
    try:
        key.verify(sig, data, padding.PKCS1v15(), h)
    except InvalidSignature as e:
        raise TimestampError("signature value does not verify") from e


def _sig_md(alg: str, digest_md: str | None) -> str:
    """Hash used by a signature algorithm: rsaEncryption takes the SignerInfo digest algorithm."""
    if alg == OID_RSA:
        if digest_md is None:
            raise TimestampError("unsupported digest algorithm")
        return digest_md
    if alg in _RSA_SIG_MD:
        return _RSA_SIG_MD[alg]
    raise TimestampError(f"signature algorithm {alg} not supported (signature not checked)")


def verify_token(der: bytes, digest: bytes, anchors: list | None) -> TokenCheck:
    """Full check of a stored TSA response: status, message imprint = digest, then the CMS SignedData of the
    token — eContentType id-ct-TSTInfo, one SignerInfo whose certificate is in the token, signed attributes with
    content-type, message-digest over the TSTInfo, ESS signing-certificate (v1 or v2) binding that certificate,
    the signature value (RSA PKCS#1 v1.5) and the timeStamping extended key usage. With anchors, the
    certificate chain is also validated at genTime; revocation is never checked."""
    try:
        gen_time, _ = parse_response(der, digest, None)
        sd = tsp.TimeStampResp.load(der)["time_stamp_token"]["content"]
        eci = sd["encap_content_info"]
        if eci["content_type"].dotted != OID_CT_TSTINFO:
            raise TimestampError("token content is not a TSTInfo")
        if not eci["content"].contents:
            raise TimestampError("token without TSTInfo")
        tst = eci["content"].contents
        sis = list(sd["signer_infos"])
        if not sis:
            raise TimestampError("token has no SignerInfo")
        if len(sis) > 1:
            raise TimestampError("token has more than one SignerInfo")
        si = sis[0]
        certs = [c.chosen for c in (sd["certificates"] or []) if c.name == "certificate"]
        sid = si["sid"]

        def is_signer(c: x509.Certificate) -> bool:
            if sid.name == "issuer_and_serial_number":
                ias = sid.chosen
                return c["tbs_certificate"]["issuer"].dump() == ias["issuer"].dump() and c.serial_number == ias["serial_number"].native
            ski = _ext(c, OID_EXT_SKI)
            return ski is not None and ski["extn_value"].parsed.native == sid.chosen.native

        signer = next((c for c in certs if is_signer(c)), None)
        if signer is None:
            raise TimestampError("the TSA certificate is not in the token (signature not checked)")
        tsa_subject = _subject(signer)
        digest_md = _MD_BY_OID.get(si["digest_algorithm"]["algorithm"].dotted)
        attrs = si["signed_attrs"]
        if not attrs.contents:
            raise TimestampError("token SignerInfo has no signed attributes")

        def attr(oid: str):
            for a in attrs:
                if a["type"].dotted == oid:
                    vals = list(a["values"])
                    return vals[0] if vals else None
            return None

        ct = attr(OID_ATTR_CONTENT_TYPE)
        if ct is None:
            raise TimestampError("signed attributes lack content-type")
        if ct.dotted != OID_CT_TSTINFO:
            raise TimestampError("signed content-type is not id-ct-TSTInfo")
        if digest_md is None:
            raise TimestampError(f"digest algorithm {si['digest_algorithm']['algorithm'].dotted} not supported")
        want = attr(OID_ATTR_MESSAGE_DIGEST)
        if want is None:
            raise TimestampError("signed attributes lack message-digest")
        if want.native != _digest(digest_md, tst):
            raise TimestampError("message-digest does not match the TSTInfo (token altered)")
        # ESS signing-certificate (v2 preferred, else v1) binds the TSA certificate.
        v2, v1 = attr(OID_ATTR_SIGNING_CERT_V2), attr(OID_ATTR_SIGNING_CERT)
        ess = v2 if v2 is not None else v1
        if ess is None:
            raise TimestampError("signed attributes lack the ESS signing-certificate (RFC 5816/2634)")
        ids = list(ess["certs"])
        if not ids:
            raise TimestampError("ESS signing-certificate lists no certificate")
        if v2 is not None:
            hash_md = _MD_BY_OID.get(ids[0]["hash_algorithm"]["algorithm"].dotted)
            if hash_md is None:
                raise TimestampError("ESSCertIDv2 hash algorithm not supported")
        else:
            hash_md = "sha1"
        if ids[0]["cert_hash"].native != _digest(hash_md, signer.dump()):
            raise TimestampError("ESS signing-certificate does not match the TSA certificate in the token")
        # Signature over the DER of the signed attributes (SET OF, not the [0] IMPLICIT tag of the SignerInfo).
        signed = b"\x31" + attrs.dump()[1:]
        smd = _sig_md(si["signature_algorithm"]["algorithm"].dotted, digest_md)
        try:
            _rsa_verify(signer, smd, signed, si["signature"].native)
        except TimestampError as e:
            raise TimestampError(f"TSA {e}") from e
        # RFC 3161 §2.3: the TSA certificate's extendedKeyUsage is critical and contains only id-kp-timeStamping.
        check_tsa_eku(signer)
        anchor = None if anchors is None else validate_chain(signer, certs, anchors, gen_time)
    except (ValueError, KeyError, TypeError, AttributeError) as e:  # asn1crypto parse errors
        raise TimestampError(f"bad token: {e}") from e
    return TokenCheck(gen_time, tsa_subject, anchor)


def check_tsa_eku(signer: x509.Certificate) -> None:
    """RFC 3161 §2.3: the TSA certificate MUST contain only one extended key usage, id-kp-timeStamping, and the
    extension MUST be critical (same rule as OpenSSL's timestampsign purpose)."""
    e = _ext(signer, OID_EXT_EKU)
    if e is None:
        raise TimestampError("the TSA certificate lacks the timeStamping extended key usage")
    try:
        purposes = [p.dotted for p in e["extn_value"].parsed]
    except (ValueError, TypeError) as ex:
        raise TimestampError("the TSA certificate's extended key usage is malformed") from ex
    if OID_KP_TIME_STAMPING not in purposes:
        raise TimestampError("the TSA certificate lacks the timeStamping extended key usage")
    if len(purposes) != 1:
        raise TimestampError("the TSA certificate's extended key usage must contain only id-kp-timeStamping (RFC 3161 §2.3)")
    if not e["critical"].native:
        raise TimestampError("the TSA certificate's extended key usage must be critical (RFC 3161 §2.3)")


# ---------------------------------------------------------------- TSA certificate path (RFC 5280 subset)
#
# The same rules as the Rust profile's trust.rs, graded by the same fixtures (tests/fixtures/tsa-chains):
# * every signature (RSA PKCS#1 v1.5) and every certificate's validity at genTime;
# * basicConstraints cA=TRUE on every intermediate; an anchor is a CA when it has cA=TRUE, or (no
#   basicConstraints) when it is an X.509 v1 certificate or its keyUsage has keyCertSign (as OpenSSL);
# * keyCertSign when an issuer (intermediate or anchor) has a keyUsage extension;
# * pathLenConstraint of every issuer, anchor included; self-issued key-rollover certificates do not count;
# * nameConstraints of every issuer: directoryName subtrees are evaluated against the subject (and
#   directoryName subjectAltNames) of every certificate below it, except self-issued intermediates; names
#   compared per RFC 5280 §7.1 (string values case-folded, whitespace collapsed). Other name forms are not
#   evaluated: a subtree of such a form while a certificate below carries a name of that form rejects the path
#   (fail closed); subtrees with minimum/maximum are rejected too;
# * critical extensions: only HANDLED_CRITICAL are accepted, on every certificate including the anchor;
#   a critical extendedKeyUsage on a CA must allow timeStamping (or anyEKU);
# * path building tries every candidate issuer in the token and every candidate anchor in the bundle,
#   depth-first, at most MAX_PATH certificates below the anchor, never the same certificate twice on one path,
#   at most MAX_CANDIDATES signature checks.
# Not checked: revocation (CRL/OCSP) — never; certificate policies; the extended key usage of CAs when not
# critical; algorithms other than RSA.

_NAME_KIND = {
    "other_name": "otherName",
    "rfc822_name": "rfc822Name",
    "dns_name": "dNSName",
    "x400_address": "x400Address",
    "directory_name": "directoryName",
    "edi_party_name": "ediPartyName",
    "uniform_resource_identifier": "uniformResourceIdentifier",
    "ip_address": "iPAddress",
    "registered_id": "registeredID",
}


def _gen_unix(gen_time: str) -> float:
    try:
        return _dt.datetime.fromisoformat(gen_time.replace("Z", "+00:00")).timestamp()
    except ValueError as e:
        raise TimestampError(f"bad genTime {gen_time}: {e}") from e


def _check_validity(c: x509.Certificate, at: float) -> None:
    v = c["tbs_certificate"]["validity"]
    if not (v["not_before"].native.timestamp() <= at <= v["not_after"].native.timestamp()):
        raise TimestampError(f"certificate {_subject(c)} was not valid at the token's genTime")


def _signed_by(cert: x509.Certificate, issuer: x509.Certificate) -> bool:
    try:
        md = _sig_md(cert["signature_algorithm"]["algorithm"].dotted, None)
        _rsa_verify(issuer, md, cert["tbs_certificate"].dump(), cert["signature_value"].native)
    except TimestampError:
        return False
    return True


def _self_issued(c: x509.Certificate) -> bool:
    return c["tbs_certificate"]["subject"].dump() == c["tbs_certificate"]["issuer"].dump()


def _decoded(c: x509.Certificate, oid: str, what: str):
    """(parsed value, critical) of an extension, None when absent; malformed → TimestampError."""
    e = _ext(c, oid)
    if e is None:
        return None
    try:
        v = e["extn_value"].parsed
        v.native  # noqa: B018 - forces a full decode
    except (ValueError, TypeError) as ex:
        raise TimestampError(f"certificate {_subject(c)} has a malformed {what}") from ex
    return v, bool(e["critical"].native)


def _check_extensions(c: x509.Certificate) -> None:
    """No duplicates, no unhandled critical extension, and the handled ones decodable."""
    seen: list[str] = []
    for e in c["tbs_certificate"]["extensions"] or []:
        oid = e["extn_id"].dotted
        if oid in seen:
            raise TimestampError(f"certificate {_subject(c)} has the extension {oid} twice")
        seen.append(oid)
        if e["critical"].native and oid not in HANDLED_CRITICAL:
            raise TimestampError(f"certificate {_subject(c)} has an unsupported critical extension {oid} (not evaluated: rejected)")
    for oid, what in (
        (OID_EXT_BASIC_CONSTRAINTS, "basicConstraints"),
        (OID_EXT_KEY_USAGE, "keyUsage"),
        (OID_EXT_EKU, "extendedKeyUsage"),
        (OID_EXT_NAME_CONSTRAINTS, "nameConstraints"),
        (OID_EXT_SAN, "subjectAltName"),
    ):
        _decoded(c, oid, what)


def _attr_value_key(v) -> tuple:
    """RFC 5280 §7.1 comparison of one attribute value: string types case-folded, whitespace trimmed and
    collapsed; other types byte for byte."""
    tag = v.chosen.tag if hasattr(v, "chosen") else getattr(v, "tag", None)
    inner = v.chosen if hasattr(v, "chosen") else v
    if getattr(inner, "class_", 0) == 0 and tag in (12, 19, 22, 20, 30):
        try:
            return ("s", " ".join(str(inner.native).split()).lower())
        except (ValueError, TypeError):
            pass
    return ("b", v.dump())


def _rdn_key(rdn) -> list:
    return sorted((a["type"].dotted, _attr_value_key(a["value"])) for a in rdn)


def _dn_within(name: x509.Name, base: x509.Name) -> bool:
    """name is within the directoryName subtree base: base's RDNs are a prefix of name's."""
    n, b = list(name.chosen), list(base.chosen)
    return len(b) <= len(n) and all(_rdn_key(x) == _rdn_key(y) for x, y in zip(b, n, strict=False))


def _check_names(c: x509.Certificate, issuer: x509.Certificate, nc) -> None:
    """Checks the names of c against the nameConstraints nc of issuer."""
    d = _decoded(c, OID_EXT_SAN, "subjectAltName")
    sans = list(d[0]) if d else []
    subject = c["tbs_certificate"]["subject"]
    dns = [subject] if len(subject.chosen) else []
    dns += [s.chosen for s in sans if s.name == "directory_name"]
    has_email_attr = any(a["type"].dotted == OID_EMAIL_ADDRESS for rdn in subject.chosen for a in rdn)
    permitted = list(nc["permitted_subtrees"] or [])
    excluded = list(nc["excluded_subtrees"] or [])

    def violation(how: str) -> TimestampError:
        return TimestampError(f"certificate {_subject(c)} violates the name constraints of {_subject(issuer)} ({how})")

    for st in permitted + excluded:
        if (st["minimum"].native or 0) != 0 or st["maximum"].native is not None:
            raise TimestampError(f"the name constraints of {_subject(issuer)} use minimum/maximum, which cannot be evaluated (rejected)")
        kind = _NAME_KIND.get(st["base"].name, st["base"].name)
        if kind == "directoryName":
            continue
        carries = any(_NAME_KIND.get(s.name, s.name) == kind for s in sans) or (kind == "rfc822Name" and has_email_attr)
        if carries:
            raise TimestampError(
                f"the {kind} name constraints of {_subject(issuer)} cannot be evaluated for {_subject(c)}, which has {kind} names (rejected)"
            )
    permitted_dn = [st["base"].chosen for st in permitted if st["base"].name == "directory_name"]
    excluded_dn = [st["base"].chosen for st in excluded if st["base"].name == "directory_name"]
    for name in dns:
        if permitted_dn and not any(_dn_within(name, b) for b in permitted_dn):
            raise violation(f"{name.human_friendly} is outside the permitted subtrees")
        for b in excluded_dn:
            if _dn_within(name, b):
                raise violation(f"{name.human_friendly} is inside the excluded subtree {b.human_friendly}")


def _check_path(path: list[x509.Certificate]) -> None:
    """Path constraints of a complete candidate path: path[0] is the TSA certificate, the last one the anchor."""
    n = len(path)
    for j in range(1, n):
        c = path[j]
        is_anchor = j == n - 1
        bc = _decoded(c, OID_EXT_BASIC_CONSTRAINTS, "basicConstraints")
        ku = _decoded(c, OID_EXT_KEY_USAGE, "keyUsage")
        cert_sign = ku is not None and "key_cert_sign" in ku[0].native
        if bc is not None:
            if not bc[0]["ca"].native:
                raise TimestampError(f"certificate {_subject(c)} is not a CA (basicConstraints cA is false) but issued {_subject(path[j - 1])}")
        else:
            tbs = c["tbs_certificate"]
            v1 = tbs["version"].native == "v1" and not tbs["extensions"]
            if not (is_anchor and (v1 or cert_sign)):
                raise TimestampError(f"certificate {_subject(c)} is not a CA (no basicConstraints) but issued {_subject(path[j - 1])}")
        if ku is not None and not cert_sign:
            raise TimestampError(f"certificate {_subject(c)} may not sign certificates (its keyUsage lacks keyCertSign)")
        if bc is not None and bc[0]["path_len_constraint"].native is not None:
            limit = bc[0]["path_len_constraint"].native
            below = sum(1 for x in path[1:j] if not _self_issued(x))
            if below > limit:
                raise TimestampError(
                    f"certificate {_subject(c)} allows {limit} intermediate CA certificate(s) below it (pathLenConstraint), the path has {below}"
                )
        eku = _decoded(c, OID_EXT_EKU, "extendedKeyUsage")
        if eku is not None and eku[1] and not any(p.dotted in (OID_KP_TIME_STAMPING, OID_ANY_EKU) for p in eku[0]):
            raise TimestampError(f"certificate {_subject(c)} has a critical extended key usage without timeStamping")
        nc = _decoded(c, OID_EXT_NAME_CONSTRAINTS, "nameConstraints")
        if nc is not None:
            for k, below_c in enumerate(path[:j]):
                if k > 0 and _self_issued(below_c):
                    continue
                _check_names(below_c, c, nc[0])


class _PathSearch:
    """Depth-first search for a valid path (see the section comment above)."""

    def __init__(self, token: list, anchors: list, at: float, leaf: x509.Certificate):
        self.token, self.anchors, self.at = token, anchors, at
        self.anchor_ders = [a.dump() for a in anchors]
        self.tried = 0
        self.first_problem: str | None = None
        self.deepest = _subject(leaf)

    def note(self, problem: str) -> None:
        if self.first_problem is None:
            self.first_problem = problem

    def budget(self) -> bool:
        self.tried += 1
        if self.tried > MAX_CANDIDATES:
            self.note(f"more than {MAX_CANDIDATES} candidate issuers (search stopped)")
            return False
        return True

    def extend(self, path: list, ders: list) -> str | None:
        cur = path[-1]
        try:
            _check_validity(cur, self.at)
            _check_extensions(cur)
        except TimestampError as e:
            self.note(str(e))
            return None
        self.deepest = _subject(cur)
        issuer = cur["tbs_certificate"]["issuer"].dump()
        if ders[-1] in self.anchor_ders:
            # The certificate itself is in the bundle: the path ends here.
            try:
                _check_path(path)
                return _subject(cur)
            except TimestampError as e:
                self.note(str(e))
                return None
        for a in self.anchors:
            if a["tbs_certificate"]["subject"].dump() != issuer or a.dump() in ders:
                continue
            if not self.budget():
                return None
            if not _signed_by(cur, a):
                continue
            try:
                _check_validity(a, self.at)
                _check_extensions(a)
                _check_path(path + [a])
                return _subject(a)
            except TimestampError as e:
                self.note(str(e))
        has_issuer_in_token = any(c["tbs_certificate"]["subject"].dump() == issuer for c in self.token)
        if len(path) >= MAX_PATH:
            if has_issuer_in_token:
                self.note(f"TSA certificate chain too long (more than {MAX_PATH} certificates below the trust anchor)")
            return None
        for c in self.token:
            if c["tbs_certificate"]["subject"].dump() != issuer or c.dump() in ders:
                continue
            if not self.budget():
                return None
            if not _signed_by(cur, c):
                continue
            found = self.extend(path + [c], ders + [c.dump()])
            if found is not None:
                return found
        return None


def validate_chain(leaf: x509.Certificate, token_certs: list, anchors: list, gen_time: str) -> str:
    """Builds and checks a path from the TSA certificate to one of anchors at genTime. Returns the anchor's
    subject; raises TimestampError."""
    search = _PathSearch(token_certs, anchors, _gen_unix(gen_time), leaf)
    found = search.extend([leaf], [leaf.dump()])
    if found is not None:
        return found
    if search.first_problem is not None:
        raise TimestampError(f"TSA certificate chain not validated: {search.first_problem}")
    raise TimestampError(f"TSA certificate chain not validated: no trusted issuer for {search.deepest} in the CA bundle")


def load_ca_bundle(path: Path) -> list[x509.Certificate]:
    """Reads a CA bundle for --tsa-ca: PEM (one or more certificates) or a single DER certificate.
    Raises ValueError (the CLI exits 1)."""
    try:
        data = Path(path).read_bytes()
    except OSError as e:
        raise ValueError(f"cannot read {path}: {e}") from e
    try:
        if data.startswith(b"-----") or b"-----BEGIN " in data:
            certs = [x509.Certificate.load(d) for t, _, d in pem.unarmor(data, multiple=True) if t == "CERTIFICATE"]
            for c in certs:
                c.native  # noqa: B018 - forces a full decode
        else:
            certs = [x509.Certificate.load(data)]
            certs[0].native  # noqa: B018
    except (ValueError, TypeError) as e:
        raise ValueError(f"{path}: bad certificate: {e}") from e
    if not certs:
        raise ValueError(f"{path}: no certificate")
    return certs


# ---------------------------------------------------------------- PDF signatures


@dataclass
class PdfSigner:
    signer: object
    subject: str
    fingerprint_sha256: str

    @classmethod
    def load(cls, pfx: Path, password: str | None) -> PdfSigner:
        from pyhanko.sign import signers

        try:
            s = signers.SimpleSigner.load_pkcs12(pfx_file=str(pfx), passphrase=password.encode() if password else None)
        except Exception as e:  # noqa: BLE001 - pyhanko raises several types
            raise ValueError(f"cannot load signing certificate {pfx}: {e}") from e
        if s is None:
            raise ValueError(f"cannot load signing certificate {pfx}: wrong password or unsupported file")
        cert = s.signing_cert
        return cls(s, cert.subject.human_friendly, hashlib.sha256(cert.dump()).hexdigest())

    def sign(self, pdf: bytes, reason: str, location: str = "") -> bytes:
        from pyhanko.pdf_utils.incremental_writer import IncrementalPdfFileWriter
        from pyhanko.sign import signers

        w = IncrementalPdfFileWriter(io.BytesIO(pdf))
        meta = signers.PdfSignatureMetadata(field_name="RakvorExaminerSignature", reason=reason, location=location or None)
        out = signers.sign_pdf(w, meta, signer=self.signer)
        return out.getvalue()
