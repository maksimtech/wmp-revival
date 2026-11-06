"""Risposte al posto del servizio Microsoft (``wmprevival serve``).

- ``GET /cdinfo/GetMDRCD.aspx?...&CD=<toc>``: ricerca automatica del CD → MDR-CD o "nessun dato";
- ``POST /cdinfo/GetMDRCD.aspx?...&requestID=<GUID>`` (MDQ-CD nel corpo): ricerca automatica dei
  file della libreria → MDR-CD con le tracce abbinate o "nessun dato";
- ``GET /FAI/default.aspx?...&cd=<toc>``: pagina dei risultati per un CD;
- ``GET /FAI/default.aspx?...&requestid=<GUID>``: pagina ponte per i file;
- ``POST /FAI/default.aspx`` (campo ``mdq``): pagina dei risultati per i file;
- ``/FAI/style.css``, ``/FAI/suggestions.js``: i nostri; ``/FAI/log``: diagnostica dalla pagina.

Mai un 5xx (WMP rallenta i tentativi): gli errori diventano "nessun dato" o un messaggio.
Pagine solo da template Jinja2 con escaping automatico (regola 8 di CLAUDE.md).
"""

from __future__ import annotations

import logging
import urllib.parse
from importlib import resources
from typing import Any

from aiohttp import web
from jinja2 import Environment, PackageLoader, select_autoescape

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
)


def raw_query(raw_path: str) -> dict[str, str]:
    """Parametri con chiavi minuscole. Niente unquote_plus: nel TOC '+' è un separatore."""
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
    return _env.get_template(template).render(**context)


def results_page(tracks: list[dict[str, Any]], choices: list[Choice], message: str = "") -> web.Response:
    html = render(
        "results.html",
        tracks=tracks,
        choices=choices,
        mdrcds=[c.mdr for c in choices],
        matches=[list(c.matches) for c in choices],
        message=message or "Nessuna corrispondenza trovata su MusicBrainz.",
    )
    return web.Response(text=html, content_type="text/html", charset="utf-8", headers=NO_CACHE)


def xml_response(xml: str) -> web.Response:
    return web.Response(text=xml, content_type="text/xml", charset="us-ascii", headers=NO_CACHE)


class MetadataResponder:
    def __init__(self, service: MetadataService, auto: bool = True) -> None:
        self.service = service
        # auto=False: la ricerca automatica dei CD risponde sempre "nessun dato" e restano
        # solo le scelte manuali ("Trova informazioni album"): nessuna scrittura senza conferma.
        self.auto = auto

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
            text = resources.files("wmprevival").joinpath("web", name).read_text(encoding="utf-8")
            content_type, _, charset = STATIC[name].partition("; charset=")
            return web.Response(text=text, content_type=content_type, charset=charset)
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
        except Exception:  # noqa: BLE001 - MusicBrainz irraggiungibile o risposta inattesa: niente 5xx
            log.exception("ricerca automatica del CD fallita")
            return xml_response(mdr.build_not_found(code="Lookup failed"))
        return xml_response(xml if xml is not None else mdr.build_not_found())

    async def file_lookup(self, body: bytes, request_id: str) -> web.Response:
        """Ricerca automatica dei file della libreria: MDQ-CD in XML nel corpo della POST."""
        reply_id = request_id if mdq_mod.GUID_RE.match(request_id) else mdr.ZERO_GUID
        if not self.auto:
            return xml_response(mdr.build_not_found(reply_id, code="Automatic lookup disabled"))
        try:
            query = mdq_mod.parse(body.decode("utf-8", errors="replace"))
        except mdq_mod.MdqError:
            return xml_response(mdr.build_not_found(reply_id, code="Invalid MDQ"))
        try:
            xml = await self.service.file_automatic(query)
        except Exception:  # noqa: BLE001 - niente 5xx
            log.exception("ricerca automatica dei file fallita")
            return xml_response(mdr.build_not_found(query.request_id, code="Lookup failed"))
        return xml_response(xml if xml is not None else mdr.build_not_found(query.request_id))

    async def cd_results(self, toc_value: str) -> web.Response:
        try:
            toc = Toc.from_wmp(toc_value)
        except TocError:
            return results_page([], [], "Il disco non è stato riconosciuto (TOC non valido).")
        tracks = [
            {"number": i + 1, "title": f"Traccia {i + 1}", "duration": _duration(ms)}
            for i, ms in enumerate(toc.track_lengths_ms)
        ]
        try:
            choices = await self.service.cd_choices(toc)
        except Exception:  # noqa: BLE001
            log.exception("ricerca del CD fallita")
            return results_page(tracks, [], "MusicBrainz non risponde: riprovare più tardi.")
        return results_page(tracks, choices)

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
            return results_page([], [], f"Richiesta di WMP non valida ({exc}).")
        tracks = [
            {"number": t.number or "", "title": t.title or t.filename, "duration": _duration(t.duration_ms)}
            for t in query.tracks
        ]
        try:
            choices = await self.service.file_choices(query)
        except Exception:  # noqa: BLE001
            log.exception("ricerca dei file fallita")
            return results_page(tracks, [], "MusicBrainz non risponde: riprovare più tardi.")
        return results_page(tracks, choices)
