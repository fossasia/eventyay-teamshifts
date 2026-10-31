(function() {
    "use strict";

    if (typeof editor !== "undefined") {
        var tsUpdateUndoRedoButtons = function () {
            var history = editor.history || [];
            var historyFloor = typeof editor._ts_history_floor === "number"
                ? editor._ts_history_floor
                : 0;
            var currentHistoryIndex = history.length - 1 - editor._history_pos;
            var canUndo = currentHistoryIndex > historyFloor;
            var canRedo = editor._history_pos > 0;

            $("#toolbox-undo")
                .prop("disabled", !canUndo)
                .toggleClass("disabled", !canUndo)
                .attr("aria-disabled", String(!canUndo));
            $("#toolbox-redo")
                .prop("disabled", !canRedo)
                .toggleClass("disabled", !canRedo)
                .attr("aria-disabled", String(!canRedo));
        };

        var tsOriginalCreateSavepoint = editor._create_savepoint;
        editor._create_savepoint = function () {
            try {
                if (editor._ts_editor_loading) {
                    return;
                }

                var event = arguments[0];
                var isTextColorControlEvent = event &&
                    event.target &&
                    event.target.id === "toolbox-col" &&
                    editor.fabric &&
                    editor._ts_are_text_objects &&
                    editor._ts_are_text_objects(editor.fabric.getActiveObjects());

                if (isTextColorControlEvent && !editor._ts_saving_group_color) {
                    return;
                }

                if (editor._ts_color_picker_active && !editor._ts_saving_group_color) {
                    return;
                }

                if (
                    !editor._history_modification_in_progress &&
                    editor.fabric &&
                    editor.history &&
                    editor.history.length
                ) {
                    var historyIndex = editor.history.length - 1 - editor._history_pos;

                    if (
                        historyIndex >= 0 &&
                        JSON.stringify(editor.dump()) ===
                            JSON.stringify(editor.history[historyIndex])
                    ) {
                        return;
                    }
                }

                return tsOriginalCreateSavepoint.apply(this, arguments);
            } finally {
                tsUpdateUndoRedoButtons();
            }
        };

        var tsOriginalLoadForHistory = editor.load;
        editor.load = function () {
            var historyModificationInProgress =
                editor._history_modification_in_progress;
            var editorLoading = editor._ts_editor_loading;
            editor._history_modification_in_progress = true;
            editor._ts_editor_loading = true;

            try {
                return tsOriginalLoadForHistory.apply(this, arguments);
            } finally {
                editor._history_modification_in_progress =
                    historyModificationInProgress;
                editor._ts_editor_loading = editorLoading;
            }
        };

        var tsOriginalUndo = editor._undo;
        editor._undo = function () {
            var historyFloor = typeof editor._ts_history_floor === "number"
                ? editor._ts_history_floor
                : 0;
            var currentHistoryIndex = editor.history.length - 1 - editor._history_pos;

            if (currentHistoryIndex <= historyFloor) {
                tsUpdateUndoRedoButtons();
                return;
            }

            var result = tsOriginalUndo.apply(this, arguments);
            tsUpdateUndoRedoButtons();
            return result;
        };

        var tsOriginalRedo = editor._redo;
        editor._redo = function () {
            if (editor._history_pos <= 0) {
                tsUpdateUndoRedoButtons();
                return;
            }

            var result = tsOriginalRedo.apply(this, arguments);
            tsUpdateUndoRedoButtons();
            return result;
        };

        editor._ts_are_text_objects = function (objects) {
            return !!objects &&
                objects.length > 0 &&
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

        editor._all_selected_are_text = function (objects) {
            return !!objects &&
                objects.length > 1 &&
                editor._ts_are_text_objects(objects);
        };

        var tsOriginalUpdateValuesFromToolbox = editor._update_values_from_toolbox;
        editor._update_values_from_toolbox = function (event) {
            if (editor._ts_editor_loading) {
                return;
            }

            var target = event && event.target;
            if (
                target &&
                target.id === "toolbox-col" &&
                editor.fabric &&
                editor._ts_are_text_objects(editor.fabric.getActiveObjects())
            ) {
                tsApplyGroupColor();
                return;
            }

            var isTypographyControl = target && (
                target.id === "toolbox-fontsize" ||
                target.id === "toolbox-fontfamily" ||
                (
                    typeof target.closest === "function" &&
                    target.closest(
                        "#toolbox button[data-action=bold], " +
                        "#toolbox button[data-action=italic], " +
                        "#toolbox-align button[data-action]"
                    )
                )
            );

            if (
                isTypographyControl &&
                editor.fabric &&
                editor._all_selected_are_text(editor.fabric.getActiveObjects())
            ) {
                return;
            }

            return tsOriginalUpdateValuesFromToolbox.apply(this, arguments);
        };

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
                    editor._ts_updating_group_toolbox = true;
                    try {
                        $color.colorpicker("setValue", hex);
                    } finally {
                        editor._ts_updating_group_toolbox = false;
                    }
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

        var tsOriginalInitFabric = editor._init_fabric;
        editor._init_fabric = function () {
            tsOriginalInitFabric.apply(this, arguments);
            tsBindFabricHandlers();
        };

        if (editor.fabric) {
            tsBindFabricHandlers();
        }

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

            editor._history_modification_in_progress = true;
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
            editor._history_modification_in_progress = false;

            object.dirty = true;

            tsUpdatePinButton();
            editor.fabric.renderAll();
            editor._create_savepoint();
        });

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

        var tsOriginalInitFabricForPin = editor._init_fabric;
        editor._init_fabric = function () {
            tsOriginalInitFabricForPin.apply(this, arguments);
            tsBindPinnedSelection();
            tsBindKeyboardMovement();
        };

        if (editor.fabric) {
            tsBindPinnedSelection();
            tsBindKeyboardMovement();
        }

        var tsBindKeyboardMovement = function () {
            if (!editor.fabric || editor._ts_keyboard_bound) {
                return;
            }

            editor._ts_keyboard_bound = true;

            $(document).on("keydown.ts_keyboard", function (e) {
                if (e.keyCode < 37 || e.keyCode > 40) {
                    return;
                }

                var objects = editor.fabric.getActiveObjects();
                if (!objects || objects.length === 0) {
                    return;
                }

                var hasPinned = objects.some(function (obj) {
                    return obj.pinned === true;
                });

                if (hasPinned) {
                    e.preventDefault();
                    e.stopPropagation();
                }
            });
        };

        var tsApplyGroupFormatting = function () {
            if (!editor.fabric || editor._ts_updating_group_toolbox) {
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

            var historyModificationInProgress =
                editor._history_modification_in_progress;
            editor._history_modification_in_progress = true;
            try {
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
            } finally {
                editor._history_modification_in_progress =
                    historyModificationInProgress;
            }

            editor.fabric.renderAll();
            editor._create_savepoint();
        };

        var tsApplyGroupColor = function () {
            if (
                !editor.fabric ||
                editor._ts_updating_group_toolbox
            ) {
                return;
            }

            var objects = editor.fabric.getActiveObjects();

            if (!editor._ts_are_text_objects(objects)) {
                return;
            }

            var color = $("#toolbox-col").val();
            if (!color) {
                return;
            }

            var historyModificationInProgress =
                editor._history_modification_in_progress;
            editor._history_modification_in_progress = true;
            try {
                objects.forEach(function (object) {
                    if (object.pinned) {
                        return;
                    }

                    object.set("fill", color);
                    object.setCoords();
                });
            } finally {
                editor._history_modification_in_progress =
                    historyModificationInProgress;
            }

            editor.fabric.renderAll();
            if (editor._ts_color_picker_active) {
                editor._ts_color_picker_pending = true;
                return;
            }

            editor._ts_saving_group_color = true;
            try {
                editor._create_savepoint();
            } finally {
                editor._ts_saving_group_color = false;
            }
        };

        $("#toolbox")
            .on(
                "change.ts_group_format input.ts_group_format keyup.ts_group_format",
                "#toolbox-fontsize, #toolbox-fontfamily",
                tsApplyGroupFormatting
            )
            .on(
                "click.ts_group_format",
                "button.toggling",
                tsApplyGroupFormatting
            )
            .on(
                "showPicker.ts_group_format",
                "#toolbox-col",
                function () {
                    editor._ts_color_picker_active = true;
                    editor._ts_color_picker_pending = false;
                }
            )
            .on(
                "hidePicker.ts_group_format",
                "#toolbox-col",
                function () {
                    editor._ts_color_picker_active = false;
                    if (editor._ts_color_picker_pending) {
                        editor._ts_color_picker_pending = false;
                        tsApplyGroupColor();
                    }
                }
            );

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

        var tsOriginalInitFabricForHistory = editor._init_fabric;
        editor._init_fabric = function () {
            var result = tsOriginalInitFabricForHistory.apply(this, arguments);
            editor._ts_history_floor = 0;
            tsUpdateUndoRedoButtons();
            return result;
        };

        tsUpdateUndoRedoButtons();
    }
})();