from __future__ import annotations

import base64

from wmprevival import capture, recorder

GUID = "{DEADBEEF-1234-4ABC-8DEF-0123456789AB}"
OTHER = "a7557~1-not-a-guid"


def _record() -> dict:
    body_text = f"<MDQ-CD><userGUID>{GUID}</userGUID><x>{GUID.lower()}</x></MDQ-CD>"
    form_body = f"mdq=%3Cid%3E{GUID[1:-1]}%3C%2Fid%3E"
    return {
        "client": "::1",
        "request": {
            "raw_path": f"/FAI/default.aspx?u={GUID}",
            "headers": [["Cookie", f"g={GUID}"], ["User-Agent", OTHER]],
            "body": recorder.encode_body(body_text.encode()),
        },
        "upstream": {
            "body": recorder.encode_body(form_body.encode(), content_type="application/x-www-form-urlencoded"),
        },
        "binary": recorder.encode_body(b"\x00\x01\x02\xff"),
    }


def test_same_guid_same_placeholder_everywhere() -> None:
    out, count, warnings = capture.anonymize(_record())
    placeholder = "00000000-0000-0000-0000-000000000001"
    assert count == 1
    assert out["request"]["raw_path"] == f"/FAI/default.aspx?u={{{placeholder.upper()}}}"
    assert out["request"]["headers"] == [["Cookie", f"g={{{placeholder.upper()}}}"], ["User-Agent", OTHER]]
    text = out["request"]["body"]["text"]
    assert GUID.lower()[1:-1] not in text.lower()
    assert base64.b64decode(out["request"]["body"]["base64"]).decode() == text
    assert out["upstream"]["body"]["form"] == [["mdq", f"<id>{placeholder.upper()}</id>"]]
    assert out["client"] == "127.0.0.1"
    assert warnings == [".binary: corpo binario non anonimizzato"]


def test_original_record_is_not_modified() -> None:
    rec = _record()
    capture.anonymize(rec)
    assert GUID in rec["request"]["raw_path"]
