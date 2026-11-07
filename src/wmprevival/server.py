"""Responses in place of the Microsoft service (``wmprevival serve``).

- ``GET /cdinfo/GetMDRCD.aspx?...&CD=<toc>``: automatic CD lookup → MDR-CD or "no data";
- ``POST /cdinfo/GetMDRCD.aspx?...&requestID=<GUID>`` (MDQ-CD in the body): automatic lookup of
  library files → MDR-CD with the matched tracks or "no data";
- ``GET /FAI/default.aspx?...&cd=<toc>``: results page for a CD;
- ``GET /FAI/default.aspx?...&requestid=<GUID>``: bridge page for files;
- ``POST /FAI/default.aspx`` (``mdq`` field): results page for files;
- ``/FAI/style.css``, ``/FAI/suggestions.js``: ours; ``/FAI/log``: diagnostics from the page.

Never a 5xx (WMP backs off its retries): errors become "no data" or a message.
Pages only from Jinja2 templates with automatic escaping (rule 8 in CLAUDE.md).
"""

from __future__ import annotations

import logging
import urllib.parse
from importlib import resources
from typing import Any

from aiohttp import web
from jinja2 import Environment, PackageLoader, StrictUndefined, select_autoescape

from wmprevival import mdq as mdq_mod
from wmprevival import mdr
from wmprevival.service import Choice, MetadataService
from wmprevival.toc import Toc, TocError

log = logging.getLogger(__name__)

NO_CACHE = {"Cache-Control": "no-cache, no-store"}
STATIC = {"style.css": "text/css; charset=utf-8", "suggestions.js": "application/x-javascript; charset=utf-8"}

_env = Environment(
    loader=PackageLoader("wmprevival", "web"),
    autoescape=select_autoescape(["html"]),
    trim_blocks=True,
    lstrip_blocks=True,
    auto_reload=False,
    undefined=StrictUndefined,
)
# Pages and scripts are loaded once, at startup, together with the code: a running server
# must never serve a new page with old code (seen on 2026-11-06: the updated page expected
# writeType, the process started earlier did not pass it).
_TEMPLATES = {name: _env.get_template(name) for name in ("results.html", "bridge.html")}
_STATIC_TEXT = {
    name: resources.files("wmprevival").joinpath("web", name).read_text(encoding="utf-8") for name in STATIC
}


def raw_query(raw_path: str) -> dict[str, str]:
    """Parameters with lowercase keys. No unquote_plus: in the TOC '+' is a separator."""
    params: dict[str, str] = {}
    for part in raw_path.partition("?")[2].split("&"):
        key, _, value = part.partition("=")
        if key:
            params.setdefault(urllib.parse.unquote(key).lower(), urllib.parse.unquote(value))
    return params


def _duration(ms: int | None) -> str:
    if not ms:
        return ""
    seconds = round(ms / 1000)
    return f"{seconds // 60}:{seconds % 60:02d}"


def render(template: str, **context: Any) -> str:
    return _TEMPLATES[template].render(**context)


WRITE_CD_BY_TOC = 0  # WMP_WRITENAMES_TYPE_CD_BY_TOC: the target is the CD with that TOC
WRITE_BY_MDQ = 2  # WMP_WRITENAMES_TYPE_CD_BY_MDQCD: the target is the current MDQ request


def results_page(
    tracks: list[dict[str, Any]],
    choices: list[Choice],
    message: str = "",
    *,
    write_type: int = WRITE_BY_MDQ,
    write_id: str = "",
    rename_files: bool = False,
) -> web.Response:
    html = render(
        "results.html",
        tracks=tracks,
        choices=choices,
        mdrcds=[c.mdr for c in choices],
        matches=[list(c.matches) for c in choices],
        message=message or "No match found on MusicBrainz.",
        write_type=write_type,
        write_id=write_id,
        rename_files=rename_files,
    )
    return web.Response(text=html, content_type="text/html", charset="utf-8", headers=NO_CACHE)


def xml_response(xml: str) -> web.Response:
    return web.Response(text=xml, content_type="text/xml", charset="us-ascii", headers=NO_CACHE)


class MetadataResponder:
    def __init__(self, service: MetadataService, auto: bool = True, rename_files: bool = False) -> None:
        self.service = service
        # auto=False: the automatic CD lookup always answers "no data", leaving only the manual
        # choices ("Find album info"): nothing is written without confirmation.
        self.auto = auto
        # fRenameRegroupFiles of WriteNamesEx: with True WMP renames and moves the files to
        # Artist\Album\NN Title (seen on 2026-11-06 across the whole Music folder). Off.
        self.rename_files = rename_files

    async def __call__(self, request: web.Request, body: bytes) -> web.Response:
        path = request.path.lower()
        params = raw_query(request.raw_path)
        if path == "/cdinfo/getmdrcd.aspx" and request.method == "GET":
            return await self.cd_lookup(params.get("cd", ""))
        if path == "/cdinfo/getmdrcd.aspx" and request.method == "POST":
            return await self.file_lookup(body, params.get("requestid", ""))
        if path == "/fai/default.aspx":
            if request.method == "POST":
                return await self.file_results(body)
            if "cd" in params:
                return await self.cd_results(params["cd"])
            if "requestid" in params:
                return self.bridge(params["requestid"])
        if path.startswith("/fai/") and path[5:] in STATIC and request.method == "GET":
            name = path[5:]
            content_type, _, charset = STATIC[name].partition("; charset=")
            return web.Response(text=_STATIC_TEXT[name], content_type=content_type, charset=charset)
        if path == "/fai/log":
            return web.Response(status=204)
        return web.Response(status=404)

    async def cd_lookup(self, toc_value: str) -> web.Response:
        if not self.auto:
            return xml_response(mdr.build_not_found(code="Automatic lookup disabled"))
        try:
            xml = await self.service.cd_automatic(Toc.from_wmp(toc_value))
        except TocError:
            return xml_response(mdr.build_not_found(code="Invalid TOC"))
        except Exception:  # noqa: BLE001 - MusicBrainz unreachable or unexpected response: no 5xx
            log.exception("automatic CD lookup failed")
            return xml_response(mdr.build_not_found(code="Lookup failed"))
        return xml_response(xml if xml is not None else mdr.build_not_found())

    async def file_lookup(self, body: bytes, request_id: str) -> web.Response:
        """Automatic lookup of library files: MDQ-CD as XML in the POST body."""
        reply_id = request_id if mdq_mod.GUID_RE.match(request_id) else mdr.ZERO_GUID
        if not self.auto:
            return xml_response(mdr.build_not_found(reply_id, code="Automatic lookup disabled"))
        try:
            query = mdq_mod.parse(body.decode("utf-8", errors="replace"))
        except mdq_mod.MdqError:
            return xml_response(mdr.build_not_found(reply_id, code="Invalid MDQ"))
        try:
            xml = await self.service.file_automatic(query)
        except Exception:  # noqa: BLE001 - no 5xx
            log.exception("automatic file lookup failed")
            return xml_response(mdr.build_not_found(query.request_id, code="Lookup failed"))
        return xml_response(xml if xml is not None else mdr.build_not_found(query.request_id))

    async def cd_results(self, toc_value: str) -> web.Response:
        try:
            toc = Toc.from_wmp(toc_value)
        except TocError:
            return results_page([], [], "The disc was not recognized (invalid TOC).")
        tracks = [
            {"number": i + 1, "title": f"Track {i + 1}", "duration": _duration(ms)}
            for i, ms in enumerate(toc.track_lengths_ms)
        ]
        try:
            choices = await self.service.cd_choices(toc)
        except Exception:  # noqa: BLE001
            log.exception("CD lookup failed")
            return results_page(tracks, [], "MusicBrainz is not responding: try again later.")
        return results_page(
            tracks, choices, write_type=WRITE_CD_BY_TOC, write_id=toc.to_wmp(), rename_files=self.rename_files
        )

    def bridge(self, request_id: str) -> web.Response:
        if not mdq_mod.GUID_RE.match(request_id):
            return web.Response(status=404)
        html = render("bridge.html", request_id=request_id)
        return web.Response(text=html, content_type="text/html", charset="utf-8", headers=NO_CACHE)

    async def file_results(self, body: bytes) -> web.Response:
        form = urllib.parse.parse_qs(body.decode("utf-8", errors="replace"), keep_blank_values=True)
        try:
            query = mdq_mod.parse((form.get("mdq") or [""])[0])
        except mdq_mod.MdqError as exc:
            return results_page([], [], f"Invalid request from WMP ({exc}).")
        tracks = [
            {"number": t.number or "", "title": t.title or t.filename, "duration": _duration(t.duration_ms)}
            for t in query.tracks
        ]
        try:
            choices = await self.service.file_choices(query)
        except Exception:  # noqa: BLE001
            log.exception("file lookup failed")
            return results_page(tracks, [], "MusicBrainz is not responding: try again later.")
        return results_page(tracks, choices, write_type=WRITE_BY_MDQ, write_id="", rename_files=self.rename_files)
