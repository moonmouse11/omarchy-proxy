.pragma library

function lines(text) {
    var rows = []
    String(text || "").split("\n").forEach(function (line) {
        var trimmed = line.trim()
        if (!trimmed)
            return
        try {
            rows.push(JSON.parse(trimmed))
        } catch (error) {
        }
    })
    return rows
}

function last(text) {
    var rows = lines(text)
    return rows.length ? rows[rows.length - 1] : null
}

function schemeOf(uri) {
    var text = String(uri || "")
    var cut = text.indexOf(":")
    return cut < 0 ? "" : text.slice(0, cut).toLowerCase()
}
