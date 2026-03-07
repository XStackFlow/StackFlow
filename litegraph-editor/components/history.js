
let graph, canvas, isDirtyRef, addLogRef;

const undoStack = [];
const redoStack = [];
const MAX_HISTORY = 50;
let isHistoryAction = false;
let pendingSave = null;
let lastActionName = "Initial Load";
let lastKnownState = "";

// Debounce timers from initHistoryHooks (module-level so cancelPendingSaves can clear them)
let afterChangeTimer = null;
let propertyChangeTimer = null;
let moveTimer = null;
let resizeTimer = null;
let groupMoveTimer = null;

/**
 * Remove execution-specific visual states from a JSON-serialized graph state.
 */
function sanitizeGraphData(jsonState) {
    const data = JSON.parse(jsonState);
    if (data.nodes) {
        data.nodes.forEach(node => {
            if (node.boxcolor) delete node.boxcolor;
            if (node._last_exec_time) delete node._last_exec_time;
            if (node.color) delete node.color;
        });
    }
    return JSON.stringify(data);
}

/**
 * Capture the current graph state as the "Reference State".
 * This allows us to ignore LiteGraph noise (dynamic ports, etc) during undo/redo.
 */
function captureState() {
    if (!graph) return;
    lastKnownState = JSON.stringify(graph.serialize());
}

/**
 * Save execution-visual state (glows, timers) keyed by node ID.
 * These are stripped by sanitizeGraphData so they'd be lost on configure().
 */
function saveNodeVisuals() {
    const visuals = new Map();
    if (graph && graph._nodes) {
        graph._nodes.forEach(node => {
            if (node.boxcolor || node._last_exec_time) {
                visuals.set(node.id, {
                    boxcolor: node.boxcolor || null,
                    _last_exec_time: node._last_exec_time || null
                });
            }
        });
    }
    return visuals;
}

/**
 * Restore execution-visual state after graph.configure().
 */
function restoreNodeVisuals(visuals) {
    if (!graph || !graph._nodes || !visuals.size) return;
    graph._nodes.forEach(node => {
        const saved = visuals.get(node.id);
        if (saved) {
            if (saved.boxcolor) node.boxcolor = saved.boxcolor;
            if (saved._last_exec_time) node._last_exec_time = saved._last_exec_time;
        }
    });
}

export function initHistory(g, c, dirtyObj, logFn) {
    graph = g;
    canvas = c;
    isDirtyRef = dirtyObj;
    addLogRef = logFn;

    // Initial save
    const state = sanitizeGraphData(JSON.stringify(graph.serialize()));
    lastKnownState = state;
    undoStack.push({ state: state, action: "Initial Load" });
    updateHistoryUI();
}

export function setHistoryBlock(val) {
    isHistoryAction = val;
}

/**
 * Cancel all pending debounced history saves.
 * Call this before loading a new graph to prevent stale timers
 * from marking the freshly-loaded graph as dirty.
 */
export function cancelPendingSaves() {
    clearTimeout(pendingSave);
    clearTimeout(afterChangeTimer);
    clearTimeout(propertyChangeTimer);
    clearTimeout(moveTimer);
    clearTimeout(resizeTimer);
    clearTimeout(groupMoveTimer);
    pendingSave = null;
    afterChangeTimer = null;
    propertyChangeTimer = null;
    moveTimer = null;
    resizeTimer = null;
    groupMoveTimer = null;
}

export function saveHistory(actionName = "Action", immediate = false) {
    if (isHistoryAction || !graph) return;

    // Track last action name for unsaved checkpoints
    if (actionName !== "Action") lastActionName = actionName;

    const doSave = () => {
        const currentState = sanitizeGraphData(JSON.stringify(graph.serialize()));

        // If nothing changed since the last known saved/restored state, skip
        if (currentState === lastKnownState) {
            console.log(`[History] saveHistory ignored: No changes since last mark`);
            pendingSave = null;
            return;
        }

        if (undoStack.length > 0) isDirtyRef.value = true;

        undoStack.push({ state: currentState, action: actionName, timestamp: Date.now() });
        if (undoStack.length > MAX_HISTORY) undoStack.shift();

        redoStack.length = 0; // New action breaks redo chain
        lastKnownState = currentState;
        pendingSave = null;

        console.log(`[History] Saved: "${actionName}" (Stack: ${undoStack.length})`);
        updateHistoryUI();
    };

    if (immediate) {
        clearTimeout(pendingSave);
        doSave();
    } else {
        clearTimeout(pendingSave);
        pendingSave = setTimeout(doSave, 200);
    }
}

/**
 * Returns true if there is something to undo.
 * Note: does NOT check isHistoryAction — that's an execution guard,
 * not a "should we intercept the keystroke" guard.
 */
export function canUndo() {
    if (!graph) return false;
    return undoStack.length > 1;
}

/**
 * Returns true if there is something to redo.
 */
export function canRedo() {
    if (!graph) return false;
    return redoStack.length > 0;
}

export function undo() {
    if (!graph || undoStack.length <= 1) return;

    // Cancel any pending debounced saves so they don't fire during/after the undo
    clearTimeout(pendingSave);
    pendingSave = null;

    try {
        isHistoryAction = true;

        const currentState = sanitizeGraphData(JSON.stringify(graph.serialize()));

        // 1. Handle unsaved work — checkpoint what's on screen before undoing
        if (currentState !== lastKnownState) {
            console.log(`[History] undo: Capturing unsaved change as "${lastActionName}"`);
            undoStack.push({ state: currentState, action: lastActionName, timestamp: Date.now() });
            if (undoStack.length > MAX_HISTORY) undoStack.shift();
        }

        if (undoStack.length <= 1) {
            console.log(`[History] undo: At base state, nothing to undo`);
            return;
        }

        // 2. Move current state to Redo
        const currentObj = undoStack.pop();
        redoStack.push(currentObj);

        // 3. Restore previous state, preserving execution glows
        const previousObj = undoStack[undoStack.length - 1];
        const visuals = saveNodeVisuals();
        graph.configure(JSON.parse(previousObj.state));
        restoreNodeVisuals(visuals);

        // Sync lastKnownState immediately so the next undo doesn't re-checkpoint
        lastKnownState = previousObj.state;

        isDirtyRef.value = true;
        canvas.draw(true, true);
        if (addLogRef) addLogRef(`System: Undo "${currentObj.action}"`, "info");
        updateHistoryUI();

        console.log(`[History] Undo: Popped "${currentObj.action}"`);

    } catch (err) {
        console.error("Undo error:", err);
    } finally {
        // Keep isHistoryAction true briefly to absorb LiteGraph configure() noise,
        // but this no longer blocks undo/redo themselves.
        setTimeout(() => {
            isHistoryAction = false;
        }, 200);
    }
}

export function redo() {
    if (!graph || redoStack.length === 0) return;

    clearTimeout(pendingSave);
    pendingSave = null;

    try {
        isHistoryAction = true;
        const nextObj = redoStack.pop();
        undoStack.push(nextObj);

        const visuals = saveNodeVisuals();
        graph.configure(JSON.parse(nextObj.state));
        restoreNodeVisuals(visuals);
        lastKnownState = nextObj.state;

        isDirtyRef.value = true;
        canvas.draw(true, true);
        if (addLogRef) addLogRef(`System: Redo "${nextObj.action}"`, "info");
        updateHistoryUI();

        console.log(`[History] Redo: Restored "${nextObj.action}"`);
    } catch (err) {
        console.error("Redo error:", err);
    } finally {
        setTimeout(() => {
            isHistoryAction = false;
        }, 200);
    }
}

export function clearHistory(actionName = "Initial Reset") {
    undoStack.length = 0;
    redoStack.length = 0;
    const tempLock = isHistoryAction;
    isHistoryAction = false;
    saveHistory(actionName, true);
    isHistoryAction = tempLock;
    updateHistoryUI();
}

export function updateHistoryUI() {
    const undoList = document.getElementById("undo-stack-list");
    const redoList = document.getElementById("redo-stack-list");
    if (!undoList || !redoList) return;

    undoList.innerHTML = "";
    redoList.innerHTML = "";

    const visibleUndo = undoStack.slice(-5);
    visibleUndo.forEach((obj, i) => {
        const item = document.createElement("div");
        item.className = "history-item";
        if (i === visibleUndo.length - 1) item.classList.add("active");
        try {
            const data = JSON.parse(obj.state);
            const nodeCount = data.nodes ? data.nodes.length : 0;
            const timeStr = obj.timestamp ? new Date(obj.timestamp).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' }) : '';
            item.textContent = `${obj.action} (${nodeCount})${timeStr ? ' · ' + timeStr : ''}`;
            item.title = `Nodes: ${nodeCount}`;
        } catch (e) { item.textContent = obj.action; }
        undoList.appendChild(item);
    });

    const visibleRedo = redoStack.slice(-5);
    visibleRedo.forEach((obj) => {
        const item = document.createElement("div");
        item.className = "history-item";
        try {
            const data = JSON.parse(obj.state);
            const nodeCount = data.nodes ? data.nodes.length : 0;
            const timeStr = obj.timestamp ? new Date(obj.timestamp).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' }) : '';
            item.textContent = `${obj.action} (${nodeCount})${timeStr ? ' · ' + timeStr : ''}`;
        } catch (e) { item.textContent = obj.action; }
        redoList.appendChild(item);
    });
}

/**
 * Hook into LiteGraph events to auto-save history on structural changes.
 * Call this after initHistory() and after the canvas is set up.
 */
export function initHistoryHooks(LiteGraph) {
    if (!graph || !canvas) return;

    // --- Node add/remove ---
    const originalOnNodeAdded = graph.onNodeAdded;
    graph.onNodeAdded = function (node) {
        if (originalOnNodeAdded) originalOnNodeAdded.apply(this, arguments);
        saveHistory("Added Node");
    };

    const originalOnNodeRemoved = graph.onNodeRemoved;
    graph.onNodeRemoved = function (node) {
        if (originalOnNodeRemoved) originalOnNodeRemoved.apply(this, arguments);
        saveHistory("Removed Node");
    };

    // --- Group add/remove (bypasses onNodeAdded/Removed) ---
    const originalGraphAdd = graph.add.bind(graph);
    graph.add = function (node, skip_compute_order) {
        const result = originalGraphAdd(node, skip_compute_order);
        if (node && node.constructor === LiteGraph.LGraphGroup) {
            saveHistory("Added Group");
        }
        return result;
    };

    const originalGraphRemove = graph.remove.bind(graph);
    graph.remove = function (node) {
        const isGroup = node && node.constructor === LiteGraph.LGraphGroup;
        const result = originalGraphRemove(node);
        if (isGroup) {
            saveHistory("Removed Group");
        }
        return result;
    };

    // --- Group move/resize ---
    const originalGroupMove = LiteGraph.LGraphGroup.prototype.move;
    LiteGraph.LGraphGroup.prototype.move = function (deltax, deltay, ignore_nodes) {
        originalGroupMove.apply(this, arguments);
        const activeCanvas = LiteGraph.LGraphCanvas.active_canvas;
        const isResize = activeCanvas && activeCanvas.selected_group_resizing;
        const label = isResize ? "Resized Group" : "Moved Group";
        clearTimeout(groupMoveTimer);
        groupMoveTimer = setTimeout(() => saveHistory(label), 500);
    };

    // --- Connections ---
    const originalOnConnectionChange = graph.onConnectionChange;
    graph.onConnectionChange = function () {
        if (originalOnConnectionChange) originalOnConnectionChange.apply(this, arguments);
        saveHistory("Changed Connection");
    };

    // --- Node move (throttled) ---
    const originalOnNodeMoved = canvas.onNodeMoved;
    canvas.onNodeMoved = function (node) {
        if (originalOnNodeMoved) originalOnNodeMoved.apply(this, arguments);
        clearTimeout(moveTimer);
        moveTimer = setTimeout(() => saveHistory("Moved Node"), 500);
    };

    const originalOnDragFinished = canvas.onDragFinished;
    canvas.onDragFinished = function () {
        if (originalOnDragFinished) originalOnDragFinished.apply(this, arguments);
        if (this.selected_nodes && Object.keys(this.selected_nodes).length > 0) {
            saveHistory("Moved Node", true);
        }
    };

    // --- Property/widget value changes ---
    const originalSetProperty = LiteGraph.LGraphNode.prototype.setProperty;
    LiteGraph.LGraphNode.prototype.setProperty = function (name, value) {
        const prev = this.properties ? this.properties[name] : undefined;
        originalSetProperty.apply(this, arguments);
        if (this.properties && this.properties[name] !== prev) {
            clearTimeout(propertyChangeTimer);
            propertyChangeTimer = setTimeout(() => saveHistory("Changed Value"), 300);
        }
    };

    // --- Node resize ---
    const originalSetSize = LiteGraph.LGraphNode.prototype.setSize;
    LiteGraph.LGraphNode.prototype.setSize = function (size) {
        originalSetSize.apply(this, arguments);
        const label = this.constructor === LiteGraph.LGraphGroup ? "Resized Group" : "Resized Node";
        clearTimeout(resizeTimer);
        resizeTimer = setTimeout(() => saveHistory(label), 500);
    };

    // --- Catch-all: afterChange ---
    const originalAfterChange = graph.afterChange;
    graph.afterChange = function () {
        if (originalAfterChange) originalAfterChange.apply(this, arguments);
        clearTimeout(afterChangeTimer);
        afterChangeTimer = setTimeout(() => saveHistory("Changed Graph"), 300);
    };

    // --- Title/font-size property editor ---
    const origShowPropEditor = LiteGraph.LGraphCanvas.onShowPropertyEditor;
    LiteGraph.LGraphCanvas.onShowPropertyEditor = function (item, options, e, menu, node) {
        const property = item.property || "title";
        const origValue = node[property];
        origShowPropEditor.apply(this, arguments);
        const checkInterval = setInterval(() => {
            if (node[property] !== origValue) {
                clearInterval(checkInterval);
                const label = property === "title" ? "Changed Title" : "Changed Property";
                saveHistory(label, true);
            }
        }, 100);
        setTimeout(() => clearInterval(checkInterval), 30000);
    };

    // --- Remove unwanted context menu items ---
    const hiddenMenuItems = new Set(["Mode", "Properties", "Pin", "Shapes"]);
    graph.onGetNodeMenuOptions = function (options) {
        for (let i = options.length - 1; i >= 0; i--) {
            if (options[i] && hiddenMenuItems.has(options[i].content)) {
                options.splice(i, 1);
            }
        }
        for (let i = options.length - 1; i >= 0; i--) {
            if (options[i] === null && (i === 0 || i === options.length - 1 || options[i - 1] === null)) {
                options.splice(i, 1);
            }
        }
    };
}
