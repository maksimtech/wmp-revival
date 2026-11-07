"""Records a MusicBrainz API response as a test fixture.

Usage (from the repo root, with a config.toml that contains the contact):

    uv run python tools/record_mb_fixture.py discid <DISC_ID> tests/fixtures/musicbrainz/<name>.json
    uv run python tools/record_mb_fixture.py toc <WMP_TOC> tests/fixtures/musicbrainz/<name>.json

MusicBrainz core data is CC0: the responses can live in the repository.
Uses the real client (rate limit, User-Agent), without cache.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import sys
from pathlib import Path

from wmprevival.config import Config
from wmprevival.musicbrainz import DISCID_INC, RELEASE_INC, MusicBrainzClient
from wmprevival.paths import Paths
from wmprevival.toc import Toc


async def record(kind: str, value: str) -> dict[str, object]:
    config = Config.load(Paths.from_env().config_file).musicbrainz
    async with MusicBrainzClient(config) as client:
        if kind == "discid":
            path, params = f"discid/{value}", {"inc": DISCID_INC}
        elif kind == "toc":
            path, params = "discid/-", {"toc": Toc.from_wmp(value).mb_toc, "cdstubs": "no", "inc": DISCID_INC}
        elif kind == "release":
            path, params = f"release/{value}", {"inc": RELEASE_INC}
        elif kind == "recording-search":
            path, params = "recording", {"query": value, "limit": "10"}
        else:
            raise SystemExit(f"unknown kind: {kind}")
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
