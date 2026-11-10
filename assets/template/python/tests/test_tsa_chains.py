"""TSA certificate path validation, graded by the fixtures shared with the Rust profile.

tests/fixtures/tsa-chains/make_chains.py generates the cases and records OpenSSL's verdict for each in
expected.json; this test checks that the verdict (and, for a rejection, the reason) matches the expected one,
and that it agrees with OpenSSL except where expected.json documents a deliberate difference.
"""

import json
from pathlib import Path

from rv_hashinv.core.trust import TimestampError, check_tsa_eku, load_ca_bundle, validate_chain

DIR = Path(__file__).parent / "fixtures" / "tsa-chains"


def test_chain_fixtures_match_expected_and_openssl():
    expected = json.loads((DIR / "expected.json").read_text(encoding="utf-8"))
    gen_time, cases = expected["gen_time"], expected["cases"]
    assert len(cases) >= 30, len(cases)
    wrong = []
    for name, case in cases.items():
        d = DIR / "cases" / name
        leaf = load_ca_bundle(d / "leaf.pem")[0]
        chain = load_ca_bundle(d / "chain.pem") if (d / "chain.pem").exists() else []
        anchors = load_ca_bundle(d / "anchors.pem")
        try:
            check_tsa_eku(leaf)
            got = validate_chain(leaf, chain, anchors, gen_time)
            ok = case["expect"] == "ok"
        except TimestampError as e:
            got = str(e)
            ok = case["expect"] != "ok" and case["expect"] in got
        if not ok:
            wrong.append(f"{name}: want {case['expect']!r}, got {got!r}")
        if (case["expect"] == "ok") != (case["openssl"] == "ok") and "deliberate_difference" not in case:
            wrong.append(f"{name}: disagrees with OpenSSL ({case['openssl']})")
    assert not wrong, "\n".join(wrong)
