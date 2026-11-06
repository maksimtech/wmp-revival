"""Stato dell'installazione, scritto passo per passo da ``setup``.

``teardown`` legge da qui cosa annullare: un ``setup`` interrotto a metà lascia
annotati solo i passi effettivamente fatti.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Literal

Store = Literal["machine", "user"]
STORES: tuple[Store, ...] = ("machine", "user")


@dataclass
class InstallState:
    ca_thumbprint: str | None = None
    ca_store: Store | None = None
    hosts_backups: list[str] = field(default_factory=list)
    hosts_modified: bool = False
    installed_at: str | None = None

    @classmethod
    def load(cls, path: Path) -> InstallState:
        if not path.exists():
            return cls()
        data = json.loads(path.read_text(encoding="utf-8"))
        known = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        return cls(**known)

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(asdict(self), indent=2) + "\n", encoding="utf-8")
        tmp.replace(path)
