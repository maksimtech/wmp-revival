"""Registra una risposta dell'API di MusicBrainz come fixture di test.

Uso (dalla radice del repo, con config.toml che contiene il contatto):

    uv run python tools/record_mb_fixture.py discid <DISC_ID> tests/fixtures/musicbrainz/<nome>.json
    uv run python tools/record_mb_fixture.py toc <TOC_WMP> tests/fixtures/musicbrainz/<nome>.json

I dati principali di MusicBrainz sono CC0: le risposte possono stare nel repository.
Usa il client vero (limite di richieste, User-Agent), senza cache.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import sys
from pathlib import Path

from wmprevival.config import Config
from wmprevival.musicbrainz import DISCID_INC, MusicBrainzClient
from wmprevival.paths import Paths
from wmprevival.toc import Toc


async def record(kind: str, value: str) -> dict[str, object]:
    config = Config.load(Paths.from_env().config_file).musicbrainz
    async with MusicBrainzClient(config) as client:
        if kind == "discid":
            path, params = f"discid/{value}", {"inc": DISCID_INC}
        elif kind == "toc":
            path, params = "discid/-", {"toc": Toc.from_wmp(value).mb_toc, "cdstubs": "no", "inc": DISCID_INC}
        else:
            raise SystemExit(f"tipo sconosciuto: {kind}")
        body = await client.get(path, params)
        return {
            "recorded_at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
            "url": str(client.url_for(path, params)),
            "status": 404 if body is None else 200,
            "body": body,
        }


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(__doc__)
        return 2
    kind, value, dest = argv
    data = asyncio.run(record(kind, value))
    Path(dest).parent.mkdir(parents=True, exist_ok=True)
    Path(dest).write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    print(f"{dest}: HTTP {data['status']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
