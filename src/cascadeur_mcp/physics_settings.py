"""Read and write AutoPhysics/physics settings through Cascadeur's settings UI.

Cascadeur's Python API cannot read or write these settings (secondary motion,
physics alpha, ragdoll, corrector weights...). They live in C++ tool settings
that only the Physics Settings panel writes, through ``view::ViewSettingsUnit``
objects bound to each control (``unitData``). This module finds those units in
the live QML tree and calls ``ViewSettingsUnit.setValue`` - the same path the
controls use.

The traversal runs as JavaScript inside Cascadeur's own QML engine and returns
plain JSON. Walking the tree with PySide item wrappers failed: when Cascadeur
rebuilds a panel (closing, reopening or undocking it), PySide keeps stale
wrappers that block access to the new items. QML JavaScript references are
always live. Only window objects and the engine are touched from Python.

Rules learned on Cascadeur 2026.2 (macOS):
- Each open scene tab has its own copy of the panel; only the active scene's
  copy is visible, so only visible controls are used. Values are stored in the
  scene file.
- Never call ``QCoreApplication.processEvents`` here: it re-enters Cascadeur's
  own Python event handlers inside the bridge and crashed the app.
- This depends on the UI structure, not a supported API; failures raise instead
  of guessing.
"""

from __future__ import annotations

import json
import math

PANEL_GROUP = "AutoPhysics"

_JS = r"""
(function (win, mode, group, name, index, value) {
    function cls(o) { var s = String(o); var i = s.indexOf("("); return i < 0 ? s : s.substring(0, i); }
    function walk(item, fn) {
        if (!item) return;
        if (fn(item) === false) return;
        var ch = item.children;
        if (!ch) return;
        for (var i = 0; i < ch.length; i++) walk(ch[i], fn);
    }
    function firstText(item) {
        var t = null;
        walk(item, function (x) {
            if (t !== null) return false;
            if (cls(x).indexOf("Text_") === 0 && x.text) { t = String(x.text); return false; }
        });
        return t;
    }
    function header(item) {
        var h = null;
        walk(item, function (x) {
            if (h !== null) return false;
            if (cls(x).indexOf("ViewSettingsGroupHeader") === 0) { h = firstText(x); return false; }
        });
        return h;
    }
    function units(container) {
        var found = [];
        walk(container, function (x) {
            var u = x.unitData;
            if (u && typeof u === "object" && typeof u.setValue === "function") {
                found.push({ control: x, unit: u });
                return false;
            }
        });
        return found;
    }
    function describe(c) {
        var v = c.unit.value;
        var name = cls(c.control).split("_QML")[0];
        var row = { control: name, value: v };
        if (typeof v === "boolean") row.kind = "bool";
        else {
            row.kind = name.indexOf("Int") >= 0 ? "int" : "float";
            if (c.control.minValue !== undefined) {
                row.min = Number(c.control.minValue);
                row.max = Number(c.control.maxValue);
            }
        }
        return row;
    }
    var out = { panels: 0, settings: [], matches: [] };
    walk(win.contentItem, function (p) {
        if (cls(p).indexOf("ViewSettings_QMLTYPE") !== 0) return;
        if (!p.visible) return false;
        var groups = [];
        walk(p, function (h) { if (cls(h).indexOf("ViewSettingsGroupHeader") === 0) groups.push(firstText(h)); });
        if (groups.indexOf("AUTOPHYSICS") < 0) return false;
        out.panels += 1;
        walk(p, function (d) {
            if (cls(d).indexOf("ViewSettingsGroupDelegate") !== 0) return;
            if (!d.visible) return false;
            var g = header(d);
            walk(d, function (c) {
                if (cls(c).indexOf("ViewSettingsGroupContainer") !== 0) return;
                if (!c.visible || !c.name) return false;
                var us = units(c);
                if (mode === "read") {
                    out.settings.push({ group: g, name: String(c.name), controls: us.map(describe) });
                } else if (g === group && String(c.name) === name) {
                    out.matches.push(us);
                }
                return false;
            });
            return false;
        });
        return false;
    });
    if (mode === "read") return JSON.stringify(out);
    if (out.matches.length !== 1 || index >= out.matches[0].length)
        return JSON.stringify({ panels: out.panels, error: "found " + out.matches.length });
    var u = out.matches[0][index].unit;
    var before = u.value;
    u.setValue(value);
    return JSON.stringify({ panels: out.panels, before: before, after: u.value });
})
""".replace("AUTOPHYSICS", PANEL_GROUP)


def _run(mode, group="", name="", index=0, value=None):
    """Run the JS walker on every visible Qt Quick window; return the panel's result."""
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtQml import QQmlEngine, qmlEngine

    windows = [
        w
        for w in QGuiApplication.topLevelWindows()
        if w.isVisible() and w.inherits("QQuickWindow")
    ]
    engine = next((e for e in map(qmlEngine, windows) if e is not None), None)
    if engine is None:
        raise RuntimeError("Cascadeur's QML engine was not found.")
    function = engine.evaluate(_JS)
    if function.isError():
        raise RuntimeError("Physics settings script failed: " + function.toString())
    results = []
    for window in windows:
        # Keep the JS engine from ever taking ownership of (and deleting) a window.
        QQmlEngine.setObjectOwnership(window, QQmlEngine.CppOwnership)
        returned = function.call(
            [
                engine.newQObject(window),
                mode,
                group,
                name,
                index,
                False if value is None else value,
            ]
        )
        if returned.isError():
            raise RuntimeError("Physics settings script failed: " + returned.toString())
        data = json.loads(returned.toString())
        if data.get("panels"):
            results.append(data)
    if not results:
        raise RuntimeError(
            "The Physics Settings panel is not visible. Open it (Window > Physics "
            "Settings) or bring its dock tab to the front, expand the groups you "
            "need, and call again."
        )
    if len(results) > 1 or results[0]["panels"] > 1:
        raise RuntimeError("More than one Physics Settings panel is visible.")
    return results[0]


def _normalize(row):
    for control in row["controls"]:
        if control["kind"] == "float" and isinstance(control["value"], int):
            control["value"] = float(control["value"])
    return row


def read(app, query=""):
    query = query.lower()
    return [
        _normalize(row)
        for row in _run("read")["settings"]
        if not query or query in f"{row['group']} {row['name']}".lower()
    ]


def _coerce(value, row):
    if row["kind"] == "bool":
        if not isinstance(value, bool):
            raise ValueError("Expected true or false.")
        return value
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("Expected a number.")
    if not math.isfinite(value):
        raise ValueError("Numbers must be finite.")
    if "min" in row and not row["min"] <= value <= row["max"]:
        raise ValueError("Value must be from %g to %g." % (row["min"], row["max"]))
    if row["kind"] == "int":
        if value != int(value):
            raise ValueError("Expected an integer.")
        return int(value)
    return float(value)


def _as_kind(kind, value):
    return float(value) if kind == "float" and not isinstance(value, bool) else value


def _same(kind, after, value):
    if kind == "bool":
        return after is value
    return math.isclose(float(after), float(value), rel_tol=1e-5, abs_tol=1e-6)


def write(app, changes):
    table = {}
    for row in read(app):
        table.setdefault((row["group"], row["name"]), []).append(row)
    prepared = []
    for change in changes:
        key = (change["group"], change["name"])
        rows = table.get(key, [])
        if len(rows) != 1:
            raise ValueError(
                "Expected one setting %r / %r; found %d. Use physics_settings to list "
                "names (collapsed groups are not listed)." % (key[0], key[1], len(rows))
            )
        index = change.get("index", 0)
        if not 0 <= index < len(rows[0]["controls"]):
            raise ValueError("Setting %r has no control index %d." % (key[1], index))
        control = rows[0]["controls"][index]
        prepared.append((key, index, control, _coerce(change["value"], control)))
    results = []
    for (group, name), index, control, value in prepared:
        data = _run("set", group, name, index, value)
        if "error" in data:
            raise RuntimeError(
                "%r / %r could not be set: %s" % (group, name, data["error"])
            )
        if not _same(control["kind"], data["after"], value):
            raise RuntimeError(
                "%r / %r reads back %r after setting %r."
                % (group, name, data["after"], value)
            )
        results.append(
            dict(
                group=group,
                name=name,
                index=index,
                before=_as_kind(control["kind"], data["before"]),
                after=_as_kind(control["kind"], data["after"]),
            )
        )
    return results
