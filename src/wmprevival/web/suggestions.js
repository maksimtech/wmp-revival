// WMP Revival: logica della pagina dei risultati. ES3 (MSHTML in modalità IE7).
// Usa le variabili globali `mdrcds` (un MDR-CD per album) e `matches` (indici delle
// tracce richieste coperte da ciascun album), definite dalla pagina.
// L'MDR arriva già in ASCII con riferimenti &#N;, quindi va passato a WMP così com'è.

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

// Segnala un evento al server (finisce nelle catture): utile quando WMP rifiuta l'MDR.
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
        window.external.WriteNamesEx(2, "", mdrcds[chosen], true);
    } catch (e) {
        var detail = (e.number !== undefined ? "0x" + (e.number >>> 0).toString(16) + " " : "") + (e.message || "");
        report("WriteNamesEx-error", detail);
        showError("WMP ha rifiutato i dati: " + detail);
        return;
    }
    report("WriteNamesEx-ok", String(chosen));
    window.external.ReturnToMainTask();
}

function cancel() {
    window.external.ReturnToMainTask();
}
