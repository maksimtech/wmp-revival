// WMP Revival: results page logic. ES3 (MSHTML in IE7 mode).
// Uses the globals `mdrcds` (one MDR-CD per album), `matches` (indices of the requested
// tracks covered by each album), `writeType`, `writeId` and `renameFiles`
// (WriteNamesEx arguments), defined by the page.
// The MDR already arrives as ASCII with &#N; references, so it is passed to WMP as is.

var chosen = -1;

function byId(id) {
    return document.getElementById(id);
}

function hasClass(el, name) {
    return (" " + el.className + " ").indexOf(" " + name + " ") !== -1;
}

function toggleClass(el, name, on) {
    if (!el) {
        return;
    }
    if (on && !hasClass(el, name)) {
        el.className = el.className ? el.className + " " + name : name;
    } else if (!on && hasClass(el, name)) {
        el.className = (" " + el.className + " ").replace(" " + name + " ", " ").replace(/^\s+|\s+$/g, "");
    }
}

// Reports an event to the server (it ends up in the captures): useful when WMP rejects the MDR.
function report(kind, detail) {
    var img = new Image();
    img.src = "log?kind=" + encodeURIComponent(kind) + "&detail=" + encodeURIComponent(detail) +
        "&t=" + (new Date()).getTime();
}

function showError(text) {
    var box = byId("error");
    box.innerText = text;
    box.style.display = "block";
}

function select(index) {
    var rows = document.getElementsByTagName("tr");
    var i;
    if (chosen >= 0) {
        toggleClass(byId("album-" + chosen), "selected", false);
    }
    chosen = index;
    toggleClass(byId("album-" + index), "selected", true);
    for (i = 0; i < rows.length; i++) {
        if (rows[i].id && rows[i].id.indexOf("track-") === 0) {
            toggleClass(rows[i], "matched", false);
        }
    }
    for (i = 0; i < matches[index].length; i++) {
        toggleClass(byId("track-" + matches[index][i]), "matched", true);
    }
    byId("finish").disabled = false;
}

function finish() {
    if (chosen < 0) {
        return;
    }
    try {
        // Explicit target: for a CD its TOC (type 0, CD_BY_TOC); for files the MDQ request
        // that produced the page (type 2, CD_BY_MDQCD, empty id). With type 2 for the CD too,
        // WMP applied a CD's MDR to a file from an earlier FAI.
        window.external.WriteNamesEx(writeType, writeId, mdrcds[chosen], renameFiles);
    } catch (e) {
        var detail = (e.number !== undefined ? "0x" + (e.number >>> 0).toString(16) + " " : "") + (e.message || "");
        report("WriteNamesEx-error", detail);
        showError("WMP rejected the data: " + detail);
        return;
    }
    report("WriteNamesEx-ok", String(chosen));
    window.external.ReturnToMainTask();
}

function cancel() {
    window.external.ReturnToMainTask();
}
