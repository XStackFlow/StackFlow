import { LiteGraph } from 'litegraph.js';

/**
 * LiteGraph Patch for LangGraph Support
 * - Dynamic Input Slots: Adds a new slot when the last one is connected.
 * - Connection Validation: Prevents duplicate connections between the same nodes/slots.
 */

// Helper function to check if a slot is actually occupied
function isSlotOccupied(node, slotIndex) {
    const input = node.inputs[slotIndex];
    if (!input) return false;
    const hasLink = input.link != null && input.link !== -1;
    const hasLinks = input.links && input.links.length > 0;
    return hasLink || hasLinks;
}

// Helper function to check if a node should have dynamic inputs
function shouldHaveDynamicInputs(node) {
    const ignoredTypes = ["langgraph/start", "langgraph/state"];
    if (ignoredTypes.includes(node.type)) return false;
    return node.inputs && node.inputs.length > 0;
}

// Helper function to ensure the correct number of input slots
function refreshDynamicInputs(node) {
    if (!node || !shouldHaveDynamicInputs(node)) return;

    // 1. Add a new input slot if the last one is connected
    const lastSlotIndex = node.inputs.length - 1;
    if (isSlotOccupied(node, lastSlotIndex)) {
        const firstInput = node.inputs[0];
        const slotName = firstInput.name || "In";
        const slotType = firstInput.type;
        node.addInput(slotName, slotType);
        node.setDirtyCanvas(true, true);
    }

    // 2. Clean up: remove redundant empty slots at the end, but keep at least one spare
    let changed = false;
    while (node.inputs.length > 1) {
        const lastIsEmpty = !isSlotOccupied(node, node.inputs.length - 1);
        const prevIsEmpty = !isSlotOccupied(node, node.inputs.length - 2);

        if (lastIsEmpty && prevIsEmpty) {
            node.removeInput(node.inputs.length - 1);
            changed = true;
        } else {
            break;
        }
    }

    if (changed) {
        node.setDirtyCanvas(true, true);
    }
}

function deferredRefresh(node) {
    if (!node) return;
    setTimeout(() => refreshDynamicInputs(node), 10);
}

/**
 * Fix mouse coordinates when CSS zoom on body causes the canvas visual size
 * to differ from its pixel buffer size.
 *
 * LiteGraph computes:  canvasX = (clientX - rect.left) / ds.scale - ds.offset
 * where rect = getBoundingClientRect() returns VISUAL (zoomed) dimensions.
 * But ds operates in canvas buffer pixel space.  We need to multiply by the
 * ratio (canvas.width / rect.width) to bridge the two spaces.
 */
function patchAdjustMouseEvent(LGraphCanvas) {
    const orig = LGraphCanvas.prototype.adjustMouseEvent;
    LGraphCanvas.prototype.adjustMouseEvent = function (e) {
        orig.call(this, e);
        if (!this.canvas) return;
        const rect = this.canvas.getBoundingClientRect();
        if (rect.width <= 0 || this.canvas.width === rect.width) return;
        const sx = this.canvas.width / rect.width;
        const sy = this.canvas.height / rect.height;
        e.canvasX = (e.clientX - rect.left) * sx / this.ds.scale - this.ds.offset[0];
        e.canvasY = (e.clientY - rect.top)  * sy / this.ds.scale - this.ds.offset[1];
    };
}

export function applyLiteGraphPatch() {
    console.log("[Patch] Applying LiteGraph LangGraph patches...");

    // 0. CSS ZOOM COORDINATE FIX
    patchAdjustMouseEvent(LiteGraph.LGraphCanvas);

    // 1. DYNAMIC INPUTS MANAGEMENT

    const originalOnConnectionsChange = LiteGraph.LGraphNode.prototype.onConnectionsChange;
    LiteGraph.LGraphNode.prototype.onConnectionsChange = function (type, slot, is_connected, link_info, input_info) {
        if (originalOnConnectionsChange) {
            originalOnConnectionsChange.apply(this, arguments);
        }
        if (type === 1) { // Input connection changed
            deferredRefresh(this);
        }
    };

    const originalOnConfigure = LiteGraph.LGraphNode.prototype.onConfigure;
    LiteGraph.LGraphNode.prototype.onConfigure = function (info) {
        if (originalOnConfigure) {
            originalOnConfigure.apply(this, arguments);
        }
        deferredRefresh(this);
    };

    const originalConnect = LiteGraph.LGraphNode.prototype.connect;
    LiteGraph.LGraphNode.prototype.connect = function (slot, target_node, target_slot) {
        const res = originalConnect.apply(this, arguments);
        if (target_node) deferredRefresh(target_node);
        return res;
    };

    const originalDisconnectInput = LiteGraph.LGraphNode.prototype.disconnectInput;
    LiteGraph.LGraphNode.prototype.disconnectInput = function (slot) {
        const res = originalDisconnectInput.apply(this, arguments);
        deferredRefresh(this);
        return res;
    };

    // 2. NODES: require Shift to drag (hint only shown on actual drag attempt)
    let _shiftHintTimeout = null;
    let _dragBlocked = false;

    function showShiftHint() {
        if (_shiftHintTimeout) return;
        let hint = document.getElementById("shift-drag-hint");
        if (!hint) {
            hint = document.createElement("div");
            hint.id = "shift-drag-hint";
            hint.textContent = "⇧ Shift + drag to move nodes";
            Object.assign(hint.style, {
                position: "fixed",
                top: "50%",
                left: "50%",
                transform: "translate(-50%, -50%)",
                background: "rgba(234,179,8,0.15)",
                color: "#fbbf24",
                border: "1px solid rgba(234,179,8,0.5)",
                padding: "10px 20px",
                borderRadius: "8px",
                fontSize: "14px",
                fontWeight: "600",
                pointerEvents: "none",
                zIndex: "9999",
                opacity: "1",
                transition: "opacity 0.4s ease",
            });
            document.body.appendChild(hint);
        }
        hint.style.opacity = "1";
        _shiftHintTimeout = setTimeout(() => {
            hint.style.opacity = "0";
            _shiftHintTimeout = null;
        }, 1800);
    }

    // Shift+click vs shift+drag: defer drag until mouse moves past threshold.
    // shift+click (no movement)  = toggle multi-select only
    // shift+drag on already-selected node = move all selected nodes together
    // shift+drag on unselected node = deselect all, drag only that node
    let _deferredDragNode = null;   // node saved from mousedown, waiting for threshold
    let _deferredDragOrigin = null; // {x, y} of initial mousedown
    let _deferredWasSelected = false; // was the node already selected before this click?
    const DRAG_THRESHOLD = 5;       // px before a click becomes a drag

    const origProcessMouseDown = LiteGraph.LGraphCanvas.prototype.processMouseDown;
    LiteGraph.LGraphCanvas.prototype.processMouseDown = function (e) {
        // Snapshot selected nodes BEFORE the original handler modifies them
        const prevSelected = this.selected_nodes ? { ...this.selected_nodes } : {};

        const res = origProcessMouseDown.call(this, e);
        _dragBlocked = false;
        _deferredDragNode = null;
        _deferredDragOrigin = null;
        _deferredWasSelected = false;

        if (!e.shiftKey) {
            // Without shift: block all drags and show hint
            if (this.selected_group) {
                this.selected_group = null;
                this.selected_group_resizing = false;
            }
            if (this.node_dragged) {
                this.node_dragged = null;
                _dragBlocked = true;
            }
        } else if (this.node_dragged) {
            // With shift: defer the drag — don't move until threshold is reached.
            // This lets shift+click be a pure selection toggle.
            _deferredDragNode = this.node_dragged;
            _deferredDragOrigin = { x: e.clientX, y: e.clientY };
            // Was this node already in the selection BEFORE this mousedown?
            _deferredWasSelected = !!(prevSelected[this.node_dragged.id]);
            this.node_dragged = null;
        }
        return res;
    };

    const origProcessMouseMove = LiteGraph.LGraphCanvas.prototype.processMouseMove;
    LiteGraph.LGraphCanvas.prototype.processMouseMove = function (e) {
        // Show hint when user tries to drag without shift
        if (_dragBlocked && (e.buttons & 1)) {
            _dragBlocked = false;
            showShiftHint();
        }

        // Check if deferred shift+drag has passed the threshold
        if (_deferredDragNode && _deferredDragOrigin && (e.buttons & 1)) {
            const dx = e.clientX - _deferredDragOrigin.x;
            const dy = e.clientY - _deferredDragOrigin.y;
            if (dx * dx + dy * dy > DRAG_THRESHOLD * DRAG_THRESHOLD) {
                if (!_deferredWasSelected) {
                    // Node was NOT previously selected: reset group selection,
                    // drag only this single node.
                    this.deselectAllNodes();
                    this.selected_nodes = {};
                    this.selected_nodes[_deferredDragNode.id] = _deferredDragNode;
                    _deferredDragNode.is_selected = true;
                }
                // Threshold reached — activate the drag
                this.node_dragged = _deferredDragNode;
                _deferredDragNode = null;
                _deferredDragOrigin = null;
            }
        }

        return origProcessMouseMove.call(this, e);
    };

    // 3. DUPLICATE CONNECTION PREVENTION

    const originalOnConnectInput = LiteGraph.LGraphNode.prototype.onConnectInput;
    LiteGraph.LGraphNode.prototype.onConnectInput = function (target_slot, type, output_info, src_node, src_slot) {
        if (originalOnConnectInput) {
            const res = originalOnConnectInput.apply(this, arguments);
            if (res === false) return false;
        }

        // Check if this specific output (src_node + src_slot) is already connected to ANY of our input slots
        if (this.inputs) {
            for (let i = 0; i < this.inputs.length; ++i) {
                const input = this.inputs[i];
                if (!input || input.link == null) continue;

                // Find the link in the graph
                const link = this.graph ? this.graph.links[input.link] : null;
                if (link && link.origin_id === src_node.id && link.origin_slot === src_slot) {
                    console.warn(`[Validation] Duplicate connection blocked: Node ${src_node.id} (Slot ${src_slot}) is already connected to Node ${this.id}`);
                    return false; // Reject the connection
                }

                // If allow_multi_link_to_inputs is true, check multiple links per slot
                if (input.links && input.links.length > 0) {
                    for (const lId of input.links) {
                        const l = this.graph.links[lId];
                        if (l && l.origin_id === src_node.id && l.origin_slot === src_slot) {
                            console.warn(`[Validation] Duplicate multi-connection blocked`);
                            return false;
                        }
                    }
                }
            }
        }

        return true; // Allow connection
    };

    // 4. VISUAL PATCHES (Glow & IDs)
    const originalOnDrawForeground = LiteGraph.LGraphNode.prototype.onDrawForeground;
    LiteGraph.LGraphNode.prototype.onDrawForeground = function (ctx, canvas) {
        if (originalOnDrawForeground) {
            originalOnDrawForeground.apply(this, arguments);
        }

        // --- A. GLOW EFFECTS ---

        // 1. Current Active Status (Steady Pulsating Glow)
        if (this.boxcolor) {
            ctx.save();
            ctx.strokeStyle = this.boxcolor;
            // Use thicker line for grey/orange alerts to make them obvious
            const isAlert = this.boxcolor === "#a1a1aa" || this.boxcolor === "#f59e0b" || this.boxcolor === "#ffffff";
            ctx.lineWidth = isAlert ? 6 : 4;
            ctx.shadowBlur = (isAlert ? 20 : 15) + Math.sin(Date.now() / 200) * 5;
            ctx.shadowColor = this.boxcolor;
            ctx.strokeRect(0, 0, this.size[0], this.size[1]);
            ctx.restore();
        }

        // 2. Recent Completion/Start (Fading Green Glow)
        // Only show green glow if NOT currently active (not blue)
        else if (this._last_exec_time) {
            const now = Date.now();
            const elapsed = now - this._last_exec_time;
            const duration = 3000; // 3 seconds fade

            if (elapsed < duration) {
                const opacity = 1.0 - (elapsed / duration);
                ctx.save();
                // Vibrant green (Green-500 equivalent)
                const color = `rgba(34, 197, 94, ${opacity})`;
                ctx.strokeStyle = color;
                ctx.lineWidth = 8; // Slightly thicker for the completion pulse
                ctx.shadowBlur = 20 * opacity;
                ctx.shadowColor = color;
                ctx.strokeRect(0, 0, this.size[0], this.size[1]);
                ctx.restore();

                // Ensure the canvas keeps redrawing while fading
                this.setDirtyCanvas(true, true);
            }
        }

        // Only draw ID if not collapsed
        if (this.flags.collapsed) return;

        const idText = String(this.id);
        ctx.font = "11px Arial, sans-serif";
        const metrics = ctx.measureText(idText);
        const paddingH = 6;
        const rectWidth = metrics.width + paddingH * 2;
        const rectHeight = 16;
        const titleHeight = LiteGraph.NODE_TITLE_HEIGHT || 20;
        const x = this.size[0] - rectWidth - 6;
        const y = -titleHeight + (titleHeight - rectHeight) / 2;

        ctx.save();

        // --- B. Subtle Dark Grey ID Badge ---
        ctx.fillStyle = "rgba(0, 0, 0, 0.45)";
        ctx.strokeStyle = "rgba(255, 255, 255, 0.1)";

        if (ctx.roundRect) {
            ctx.beginPath();
            ctx.roundRect(x, y, rectWidth, rectHeight, 4);
            ctx.fill();
            ctx.stroke();
        } else {
            ctx.fillRect(x, y, rectWidth, rectHeight);
            ctx.strokeRect(x, y, rectWidth, rectHeight);
        }

        // --- C. Low-Contrast ID Text ---
        ctx.textAlign = "center";
        ctx.textBaseline = "middle";
        ctx.fillStyle = "rgba(255, 255, 255, 0.4)";
        ctx.fillText(idText, x + rectWidth / 2, y + rectHeight / 2 + 1);

        ctx.restore();
    };
}
