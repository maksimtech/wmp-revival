"""From capture to test fixture: replaces GUIDs with stable placeholders.

The same GUID always becomes the same placeholder within a capture, so the relations
between fields stay visible. The ``base64`` fields of text bodies are recomputed from the
anonymized text; a binary-only body is left as is and reported.
"""

from __future__ import annotations

import base64
import re
from typing import Any

GUID_RE = re.compile(r"[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}")


class _Anonymizer:
    def __init__(self) -> None:
        self.mapping: dict[str, str] = {}
        self.warnings: list[str] = []

    def _placeholder(self, match: re.Match[str]) -> str:
        key = match.group().lower()
        if key not in self.mapping:
            self.mapping[key] = f"00000000-0000-0000-0000-{len(self.mapping) + 1:012d}"
        value = self.mapping[key]
        return value.upper() if match.group().isupper() else value

    def text(self, value: str) -> str:
        return GUID_RE.sub(self._placeholder, value)

    def walk(self, node: Any, where: str = "") -> Any:
        if isinstance(node, dict):
            if "base64" in node and "length" in node:
                return self.body(node, where)
            return {k: self.walk(v, f"{where}.{k}") for k, v in node.items()}
        if isinstance(node, list):
            return [self.walk(v, where) for v in node]
        if isinstance(node, str):
            return self.text(node)
        return node

    def body(self, node: dict[str, Any], where: str) -> dict[str, Any]:
        out = dict(node)
        if "form" in node:
            out["form"] = self.walk(node["form"], f"{where}.form")
        if "text" in node and "decoded_length" not in node:
            out["text"] = self.text(node["text"])
            data = out["text"].encode(node.get("text_encoding", "utf-8"))
            out["base64"] = base64.b64encode(data).decode("ascii")
            out["length"] = len(data)
        elif "text" in node:
            # Compressed body: the text is anonymized, the original bytes are dropped.
            out["text"] = self.text(node["text"])
            out["base64"] = None
            self.warnings.append(f"{where}: compressed body, original base64 dropped")
        elif node["length"]:
            self.warnings.append(f"{where}: binary body not anonymized")
        return out


def anonymize(record: dict[str, Any]) -> tuple[dict[str, Any], int, list[str]]:
    """Returns the anonymized capture, the number of distinct GUIDs and the warnings."""
    anon = _Anonymizer()
    result = anon.walk(record)
    if isinstance(result, dict) and "client" in result:
        result["client"] = "127.0.0.1"
    return result, len(anon.mapping), anon.warnings


OMITTED = "omitted: content from the Microsoft server, not redistributed in the repository"


def drop_upstream_body(record: dict[str, Any]) -> dict[str, Any]:
    """Drops the body of the real server's response (Microsoft HTML, CSS, JS).

    Status, headers and length remain: they're enough for the tests, and the repository
    doesn't host Microsoft material (rule 2 in CLAUDE.md, extended to pages and scripts).
    """
    out = dict(record)
    upstream = out.get("upstream")
    if isinstance(upstream, dict) and isinstance(upstream.get("body"), dict):
        body = upstream["body"]
        kept = {k: body[k] for k in ("length", "decoded_length", "text_encoding") if k in body}
        out["upstream"] = {**upstream, "body": {**kept, "omitted": OMITTED}}
    return out
