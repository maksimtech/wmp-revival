"""Da cattura a fixture di test: sostituisce i GUID con segnaposto stabili.

Lo stesso GUID diventa sempre lo stesso segnaposto all'interno di una cattura, così le
relazioni tra campi restano visibili. I campi ``base64`` dei corpi testuali vengono
ricalcolati dal testo anonimizzato; un corpo solo binario resta com'è e viene segnalato.
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
            # Corpo compresso: il testo si anonimizza, i byte originali si tolgono.
            out["text"] = self.text(node["text"])
            out["base64"] = None
            self.warnings.append(f"{where}: corpo compresso, tolto il base64 originale")
        elif node["length"]:
            self.warnings.append(f"{where}: corpo binario non anonimizzato")
        return out


def anonymize(record: dict[str, Any]) -> tuple[dict[str, Any], int, list[str]]:
    """Restituisce la cattura anonimizzata, il numero di GUID distinti e gli avvisi."""
    anon = _Anonymizer()
    result = anon.walk(record)
    if isinstance(result, dict) and "client" in result:
        result["client"] = "127.0.0.1"
    return result, len(anon.mapping), anon.warnings
