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
        fill: "#000000",
        maxFontPt: 12,
        fontSize: 24,
        fontFamily: "Open Sans",
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

function createHarness(objects, values = {}) {
    const handlers = [];
    const document = {};

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

        attr() { return this; }
        toggleClass() { return this; }
        text() { return this; }
        data() { return undefined; }
        colorpicker() { return this; }
        show() { return this; }
        hide() { return this; }
        removeClass() { return this; }
        addClass() { return this; }
    }

    const $ = (selector) => new Collection(selector === document ? "document" : selector);
    const editor = {
        fabric: null,
        _init_fabric() {},
        _update_values_from_toolbox() {},
        _create_savepoint() {},
        _pt2px(value) { return value * 2; },
        _px2pt(value) { return value / 2; },
        _apply_autofit_fontsize(object, maxFontPt) {
            if (typeof maxFontPt === "number") {
                object.maxFontPt = maxFontPt;
            }
            object.fontSize = object.maxFontPt * 2;
        },
        dump(source) {
            return (source || objects).map((object) => ({ type: object.type }));
        },
        _add_from_data(data) {
            return makeTextObject({ type: data.type });
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

    editor.fabric = {
        getObjects: () => objects,
        getActiveObjects: () => objects,
        on() {},
        renderAll() {}
    };
    editor._init_fabric();

    return { editor, handlers };
}

function getGroupHandler(harness, event, delegate) {
    const registration = harness.handlers.find((entry) =>
        entry.selector === "#toolbox" &&
        entry.events.includes(event) &&
        entry.delegate === delegate
    );
    assert.ok(registration, `Missing ${event} handler for ${delegate}`);
    return registration.handler;
}

function eventFor(control) {
    return {
        currentTarget: {
            id: control,
            getAttribute() { return null; }
        }
    };
}

test("group font-size edits persist the point size and preserve other styles", () => {
    const pinned = makeTextObject({
        pinned: true,
        maxFontPt: 14,
        fontWeight: "bold",
        fontStyle: "italic",
        textAlign: "right"
    });
    const editable = makeTextObject({
        maxFontPt: 11,
        fontWeight: "normal",
        fontStyle: "italic",
        textAlign: "center"
    });
    const harness = createHarness(
        [pinned, editable],
        { "#toolbox-fontsize": "17" }
    );

    getGroupHandler(
        harness,
        "change",
        "#toolbox-fontsize, #toolbox-fontfamily"
    )(eventFor("toolbox-fontsize"));

    assert.equal(pinned.maxFontPt, 14);
    assert.equal(pinned.fontWeight, "bold");
    assert.equal(pinned.fontStyle, "italic");
    assert.equal(pinned.textAlign, "right");
    assert.equal(editable.maxFontPt, 17);
    assert.equal(editable.fontSize, 34);
    assert.equal(editable.fontWeight, "normal");
    assert.equal(editable.fontStyle, "italic");
    assert.equal(editable.textAlign, "center");
});

test("group font-family edits preserve each object's other styles", () => {
    const first = makeTextObject({
        fontFamily: "Open Sans",
        fontWeight: "bold",
        fontStyle: "normal",
        textAlign: "left"
    });
    const second = makeTextObject({
        fontFamily: "Droid Sans Fallback",
        fontWeight: "normal",
        fontStyle: "italic",
        textAlign: "right"
    });
    const harness = createHarness(
        [first, second],
        { "#toolbox-fontfamily": "Open Sans" }
    );

    getGroupHandler(
        harness,
        "change",
        "#toolbox-fontsize, #toolbox-fontfamily"
    )(eventFor("toolbox-fontfamily"));

    assert.equal(first.fontFamily, "Open Sans");
    assert.equal(second.fontFamily, "Open Sans");
    assert.equal(first.fontWeight, "bold");
    assert.equal(first.fontStyle, "normal");
    assert.equal(first.textAlign, "left");
    assert.equal(second.fontWeight, "normal");
    assert.equal(second.fontStyle, "italic");
    assert.equal(second.textAlign, "right");
});
