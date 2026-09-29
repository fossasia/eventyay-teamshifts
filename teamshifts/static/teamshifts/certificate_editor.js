/**
 * Certificate Editor Extensions
 * 
 * This script extends the pretixcontrol/js/ui/editor.js global editor object
 * with teamshifts-specific certificate functionality:
 * - Group text formatting for multiple selected text objects
 * - Pin/unpin controls for text objects
 * - Pin state persistence
 */

(function() {
    "use strict";

    if (typeof editor !== "undefined") {
        // Add method to check if all selected objects are text
        editor._all_selected_are_text = function (objects) {
            return !!objects &&
                objects.length > 1 &&
                objects.every(function (obj) {
                    return (
                        obj.type === "textarea" ||
                        obj.type === "text" ||
                        obj.type === "textbox" ||
                        obj.type === "i-text" ||
                        (typeof obj.type === "string" && obj.type.toLowerCase().indexOf("text") !== -1)
                    );
                });
        };

        var tsOriginalUpdateValuesFromToolbox = editor._update_values_from_toolbox;
        editor._update_values_from_toolbox = function (event) {
            if (
                event &&
                event.target &&
                event.target.id === "toolbox-col" &&
                editor.fabric &&
                editor._all_selected_are_text(editor.fabric.getActiveObjects())
            ) {
                return;
            }

            return tsOriginalUpdateValuesFromToolbox.apply(this, arguments);
        };

        // Apply group text toolbox UI
        var tsApplyTextGroupToolbox = function () {
            if (!editor.fabric) {
                return;
            }

            var objects = editor.fabric.getActiveObjects();

            if (!editor._all_selected_are_text(objects)) {
                return;
            }

            $("#toolbox").attr("data-type", "group-text");
            $("#toolbox-heading").text(
                typeof gettext !== "undefined"
                    ? gettext("Group of text objects")
                    : "Group of text objects"
            );

            var first = objects[0];

            if (first.fill) {
                var color = new fabric.Color(first.fill)._source;
                var hex = "#" + ((1 << 24) +
                    (color[0] << 16) +
                    (color[1] << 8) +
                    color[2]).toString(16).slice(1);

                $("#toolbox-col").val(hex);

                var $color = $("#toolbox-col");
                if ($color.data("colorpicker")) {
                    $color.colorpicker("setValue", hex);
                }
            }

            $("#toolbox-fontsize").val(
                editor._px2pt(first.fontSize).toFixed(1)
            );
            $("#toolbox-fontfamily").val(first.fontFamily);
            $("#toolbox").find("button[data-action=bold]")
                .toggleClass("active", first.fontWeight === "bold");
            $("#toolbox").find("button[data-action=italic]")
                .toggleClass("active", first.fontStyle === "italic");
            $("#toolbox").find("button[data-action=left]")
                .toggleClass("active", first.textAlign === "left");
            $("#toolbox").find("button[data-action=center]")
                .toggleClass("active", first.textAlign === "center");
            $("#toolbox").find("button[data-action=right]")
                .toggleClass("active", first.textAlign === "right");
        };

        // Update pin button visibility and state
        var tsUpdatePinButton = function () {
            var $pin = $("#toolbox-pin");

            if (!editor.fabric) {
                $pin.hide();
                return;
            }

            var objects = editor.fabric.getActiveObjects();

            if (
                objects.length !== 1 ||
                (
                    objects[0].type !== "textarea" &&
                    objects[0].type !== "text" &&
                    objects[0].type !== "textbox" &&
                    objects[0].type !== "i-text" &&
                    !(typeof objects[0].type === "string" &&
                        objects[0].type.toLowerCase().indexOf("text") !== -1)
                )
            ) {
                $pin.hide().removeClass("is-pinned btn-info").addClass("btn-default");
                return;
            }

            var pinned = objects[0].pinned === true;

            $pin.show()
                .toggleClass("is-pinned", pinned)
                .toggleClass("btn-info", pinned)
                .toggleClass("btn-default", !pinned);
        };

        // Bind fabric event handlers
        var tsBindFabricHandlers = function () {
            if (!editor.fabric || editor._ts_handlers_bound) {
                return;
            }

            editor._ts_handlers_bound = true;

            editor.fabric.on("selection:created", function () {
                tsUpdatePinButton();
                tsApplyTextGroupToolbox();
            });

            editor.fabric.on("selection:updated", function () {
                tsUpdatePinButton();
                tsApplyTextGroupToolbox();
            });

            editor.fabric.on("selection:cleared", function () {
                tsUpdatePinButton();
            });

            editor.fabric.on("mouse:up", function () {
                tsUpdatePinButton();

                if (editor._ts_group_ui_pending) {
                    return;
                }

                editor._ts_group_ui_pending = true;

                window.requestAnimationFrame(function () {
                    editor._ts_group_ui_pending = false;

                    if (editor.fabric) {
                        tsApplyTextGroupToolbox();
                    }
                });
            });

            tsUpdatePinButton();
        };

        // Override fabric initialization to bind handlers
        var tsOriginalInitFabric = editor._init_fabric;
        editor._init_fabric = function () {
            tsOriginalInitFabric.apply(this, arguments);
            tsBindFabricHandlers();
        };

        if (editor.fabric) {
            tsBindFabricHandlers();
        }

        // Pin button click handler
        $("#toolbox-pin").on("click.ts_pin", function (event) {
            event.preventDefault();
            event.stopPropagation();

            var objects = editor.fabric
                ? editor.fabric.getActiveObjects()
                : [];

            if (
                objects.length !== 1 ||
                (
                    objects[0].type !== "textarea" &&
                    objects[0].type !== "text" &&
                    objects[0].type !== "textbox" &&
                    objects[0].type !== "i-text" &&
                    !(typeof objects[0].type === "string" &&
                        objects[0].type.toLowerCase().indexOf("text") !== -1)
                )
            ) {
                return;
            }

            var object = objects[0];
            var pinned = object.pinned !== true;

            object.set({
                pinned: pinned,
                editable: !pinned,
                lockMovementX: pinned,
                lockMovementY: pinned,
                lockRotation: pinned,
                lockScalingX: pinned,
                lockScalingY: pinned,
                hasControls: !pinned
            });

            object.dirty = true;

            tsUpdatePinButton();
            editor.fabric.renderAll();
            editor._create_savepoint();
        });

        // Bind pinned selection exclusion
        var tsBindPinnedSelection = function () {
            if (!editor.fabric || editor._ts_pin_selection_bound) {
                return;
            }

            editor._ts_pin_selection_bound = true;

            editor.fabric.on("mouse:down:before", function (opt) {
                if (opt.target) {
                    editor._ts_pinned_objects = null;
                    return;
                }

                editor._ts_pinned_objects = editor.fabric.getObjects().filter(function (obj) {
                    return obj.pinned === true;
                });

                editor._ts_pinned_objects.forEach(function (obj) {
                    obj.selectable = false;
                });
            });

            editor.fabric.on("mouse:up", function () {
                if (!editor._ts_pinned_objects) {
                    return;
                }

                editor._ts_pinned_objects.forEach(function (obj) {
                    obj.selectable = true;
                });

                editor._ts_pinned_objects = null;
            });
        };

        // Override fabric initialization again for pin selection
        var tsOriginalInitFabricForPin = editor._init_fabric;
        editor._init_fabric = function () {
            tsOriginalInitFabricForPin.apply(this, arguments);
            tsBindPinnedSelection();
        };

        if (editor.fabric) {
            tsBindPinnedSelection();
        }

        // Apply group formatting to all selected text objects
        var tsApplyGroupFormatting = function () {
            if (!editor.fabric) {
                return;
            }

            var objects = editor.fabric.getActiveObjects();

            if (!editor._all_selected_are_text(objects)) {
                return;
            }

            var color = $("#toolbox-col").val();
            var fontFamily = $("#toolbox-fontfamily").val();
            var fontSize = parseFloat($("#toolbox-fontsize").val());
            var bold = $("#toolbox").find("button[data-action=bold]").is(".active");
            var italic = $("#toolbox").find("button[data-action=italic]").is(".active");
            var align = $("#toolbox-align").find(".active").attr("data-action");

            objects.forEach(function (object) {
                if (object.pinned) {
                    return;
                }

                if (color) {
                    object.set("fill", color);
                }

                if (fontFamily) {
                    object.set("fontFamily", fontFamily);
                }

                if (!isNaN(fontSize)) {
                    object.set("fontSize", editor._pt2px(fontSize));
                }

                object.set("fontWeight", bold ? "bold" : "normal");
                object.set("fontStyle", italic ? "italic" : "normal");

                if (align) {
                    object.set("textAlign", align);
                }

                object.setCoords();
            });

            editor.fabric.renderAll();
        };

        var tsApplyGroupColor = function () {
            if (!editor.fabric) {
                return;
            }

            var objects = editor.fabric.getActiveObjects();

            if (!editor._all_selected_are_text(objects)) {
                return;
            }

            var color = $("#toolbox-col").val();
            if (!color) {
                return;
            }

            objects.forEach(function (object) {
                if (object.pinned) {
                    return;
                }

                object.set("fill", color);
                object.setCoords();
            });

            editor.fabric.renderAll();
        };

        // Bind group formatting events
        $("#toolbox")
            .on(
                "change.ts_group_format input.ts_group_format keyup.ts_group_format",
                "#toolbox-fontsize, #toolbox-fontfamily",
                tsApplyGroupFormatting
            )
            .on(
                "change.ts_group_format input.ts_group_format keyup.ts_group_format changeColor.ts_group_format",
                "#toolbox-col",
                tsApplyGroupColor
            )
            .on(
                "click.ts_group_format",
                "button.toggling",
                tsApplyGroupFormatting
            );

        // Override dump method to include pinned state
        var tsOriginalDump = editor.dump;
        editor.dump = function (objects) {
            var result = tsOriginalDump.apply(this, arguments);
            var source = objects || editor.fabric.getObjects();

            source.forEach(function (object, index) {
                if (
                    result[index] &&
                    (object.type === "textarea" || object.type === "text")
                ) {
                    result[index].pinned = object.pinned === true;
                }
            });

            return result;
        };

        // Override _add_from_data method to restore pinned state
        var tsOriginalAddFromData = editor._add_from_data;
        editor._add_from_data = function (data) {
            var object = tsOriginalAddFromData.apply(this, arguments);

            if (
                object &&
                (object.type === "textarea" || object.type === "text") &&
                data.pinned
            ) {
                object.pinned = true;
                object.set({
                    editable: false,
                    lockMovementX: true,
                    lockMovementY: true,
                    lockRotation: true,
                    lockScalingX: true,
                    lockScalingY: true,
                    hasControls: false
                });
            }

            return object;
        };
    }
})();