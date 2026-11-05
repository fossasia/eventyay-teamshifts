const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const editorScript = fs.readFileSync(
    path.join(__dirname, "../teamshifts/static/teamshifts/certificate_editor.js"),
    "utf8"
);

function makeTextObject(properties = {}) {
    return {
        type: "textarea",
        fontSize: 20,
        fontWeight: "normal",
        fontStyle: "normal",
        textAlign: "left",
        set(name, value) {
            if (typeof name === "object") {
                Object.assign(this, name);
            } else {
                this[name] = value;
            }
        },
        setCoords() {},
        ...properties
    };
}

function createEditorHarness(objects, values = {}) {
    const handlers = [];
    const document = {};
    const canvasArea = {
        contains(target) {
            return target && target.insideCanvas === true;
        }
    };

    class Collection {
        constructor(selector) {
            this.selector = selector;
        }

        on(events, delegateOrHandler, delegatedHandler) {
            handlers.push({
                selector: this.selector,
                events: events.split(/\s+/).map((event) => event.split(".")[0]),
                delegate: typeof delegateOrHandler === "string" ? delegateOrHandler : null,
                handler: delegatedHandler || delegateOrHandler
            });
            return this;
        }

        find(selector) {
            return new Collection(`${this.selector} ${selector}`);
        }

        val(value) {
            if (arguments.length) {
                values[this.selector] = value;
                return this;
            }
            return values[this.selector];
        }

        is(selector) {
            return selector === ".active" &&
                this.selector.classList &&
                this.selector.classList.contains("active");
        }

        prop() { return this; }
        toggleClass() { return this; }
        attr() { return this; }
        text() { return this; }
        data() { return undefined; }
        colorpicker() { return this; }
        show() { return this; }
        hide() { return this; }
        removeClass() { return this; }
        addClass() { return this; }
    }

    const $ = (selector) => new Collection(selector === document ? "document" : selector);
    const fabric = {
        getObjects: () => objects,
        getActiveObjects: () => objects,
        on() {},
        renderAll() {}
    };

    let savepointCount = 0;
    const editor = {
        fabric,
        $cva: { get: () => canvasArea },
        history: [],
        _history_pos: 0,
        _history_modification_in_progress: false,
        _create_savepoint() {
            savepointCount++;
        },
        _update_values_from_toolbox() {},
        _init_fabric() {},
        _on_keydown() {},
        _undo() {},
        _redo() {},
        load() {},
        dump(source) {
            return (source || fabric.getObjects()).map((object) => ({ type: object.type }));
        },
        _add_from_data(data) {
            return makeTextObject({ type: data.type });
        },
        _apply_autofit_fontsize(object, maxFontPt) {
            if (typeof maxFontPt === "number") {
                object.maxFontPt = maxFontPt;
            }
            object.fontSize = (object.maxFontPt || 10) * 2;
        },
        _pt2px(value) {
            return value * 2;
        },
        _px2pt(value) {
            return value / 2;
        }
    };

    vm.runInNewContext(editorScript, {
        editor,
        document,
        window: { requestAnimationFrame: (callback) => callback() },
        fabric: {},
        $,
        gettext: (value) => value
    });

    return {
        editor,
        handlers,
        values,
        get savepointCount() {
            return savepointCount;
        }
    };
}

function findHandler(harness, selector, event, delegate) {
    const registration = harness.handlers.find((entry) =>
        entry.selector === selector &&
        entry.events.includes(event) &&
        entry.delegate === (delegate || null)
    );
    assert.ok(registration, `missing ${event} handler for ${selector}`);
    return registration.handler;
}

test("pinning is serialized and restored through the editor", () => {
    const object = makeTextObject();
    const harness = createEditorHarness([object]);

    findHandler(harness, "#toolbox-pin", "click")({
        preventDefault() {},
        stopPropagation() {}
    });

    assert.equal(object.pinned, true);
    assert.equal(harness.editor.dump()[0].pinned, true);

    const restored = harness.editor._add_from_data({
        type: "textarea",
        pinned: true
    });
    assert.equal(restored.pinned, true);
    assert.equal(restored.lockMovementX, true);
    assert.equal(restored.editable, false);
});

test("pinned selection only suppresses arrow keys from the editor canvas", () => {
    const object = makeTextObject({ pinned: true });
    const harness = createEditorHarness([object]);
    let prevented = false;

    harness.editor._on_keydown({
        keyCode: 37,
        target: { tagName: "INPUT" },
        preventDefault() { prevented = true; },
        stopPropagation() {}
    });
    assert.equal(prevented, false);

    harness.editor._on_keydown({
        keyCode: 37,
        target: { insideCanvas: true },
        preventDefault() { prevented = true; },
        stopPropagation() {}
    });
    assert.equal(prevented, true);
});

test("group font-size input previews without snapshots and change preserves other styles", () => {
    const pinned = makeTextObject({
        pinned: true,
        fontFamily: "Pinned Font",
        fontWeight: "bold",
        fontStyle: "italic",
        textAlign: "right"
    });
    const editable = makeTextObject({
        fontFamily: "Existing Font",
        fontWeight: "normal",
        fontStyle: "italic",
        textAlign: "center"
    });
    const harness = createEditorHarness(
        [pinned, editable],
        {
            "#toolbox-fontsize": "15",
            "#toolbox-fontfamily": "New Font"
        }
    );

    findHandler(harness, "#toolbox", "input", "#toolbox-fontsize")({
        currentTarget: {
            id: "toolbox-fontsize",
            getAttribute() { return null; }
        }
    });
    assert.equal(editable.maxFontPt, 15);
    assert.equal(harness.savepointCount, 0);

    findHandler(harness, "#toolbox", "change", "#toolbox-fontsize, #toolbox-fontfamily")({
        currentTarget: {
            id: "toolbox-fontsize",
            getAttribute() { return null; }
        }
    });
    assert.equal(harness.savepointCount, 1);
    assert.equal(pinned.maxFontPt, undefined);
    assert.equal(pinned.fontWeight, "bold");
    assert.equal(pinned.fontFamily, "Pinned Font");
    assert.equal(editable.fontFamily, "Existing Font");
    assert.equal(editable.fontWeight, "normal");
    assert.equal(editable.fontStyle, "italic");
    assert.equal(editable.textAlign, "center");

    findHandler(harness, "#toolbox", "change", "#toolbox-fontsize, #toolbox-fontfamily")({
        currentTarget: {
            id: "toolbox-fontfamily",
            getAttribute() { return null; }
        }
    });
    assert.equal(harness.savepointCount, 2);
    assert.equal(pinned.fontFamily, "Pinned Font");
    assert.equal(editable.fontFamily, "New Font");
    assert.equal(editable.fontWeight, "normal");
    assert.equal(editable.fontStyle, "italic");
    assert.equal(editable.textAlign, "center");
});

test("group font-size typing creates one savepoint after the focused edit", () => {
    const editable = makeTextObject({
        maxFontPt: 13,
        fontSize: 26
    });
    const harness = createEditorHarness(
        [makeTextObject({ pinned: true }), editable],
        { "#toolbox-fontsize": "13" }
    );
    const fontSizeInput = {
        id: "toolbox-fontsize",
        getAttribute() { return null; }
    };

    findHandler(harness, "#toolbox", "focusin", "#toolbox-fontsize")({
        currentTarget: fontSizeInput
    });

    harness.values["#toolbox-fontsize"] = "1";
    findHandler(harness, "#toolbox", "input", "#toolbox-fontsize")({
        currentTarget: fontSizeInput
    });
    harness.editor._create_savepoint({
        type: "change",
        target: fontSizeInput
    });

    harness.values["#toolbox-fontsize"] = "19";
    findHandler(harness, "#toolbox", "input", "#toolbox-fontsize")({
        currentTarget: fontSizeInput
    });
    findHandler(
        harness,
        "#toolbox",
        "change",
        "#toolbox-fontsize, #toolbox-fontfamily"
    )({ currentTarget: fontSizeInput });
    harness.editor._create_savepoint({
        type: "change",
        target: fontSizeInput
    });

    assert.equal(editable.maxFontPt, 19);
    assert.equal(harness.savepointCount, 0);

    findHandler(harness, "#toolbox", "focusout", "#toolbox-fontsize")({
        currentTarget: fontSizeInput
    });

    assert.equal(harness.savepointCount, 1);
    assert.equal(editable.maxFontPt, 19);
});
