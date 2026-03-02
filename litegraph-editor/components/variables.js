/**
 * Variables panel — manages global variables in the right sidebar.
 *
 * Global variables are stored in variables.json at the project root and can be
 * referenced in node properties via {{KEY}} template syntax.
 */

const API_BASE = "http://localhost:8000";

const list = document.getElementById("variables-list");
const addBtn = document.getElementById("add-variable-btn");
const saveBtn = document.getElementById("save-variables-btn");

let dirty = false;

// ── Helpers ──────────────────────────────────────────────────────────────

function markDirty() {
    dirty = true;
    if (saveBtn) saveBtn.style.display = "";
}

function markClean() {
    dirty = false;
    if (saveBtn) saveBtn.style.display = "none";
}

// ── Drag-and-drop reordering ──────────────────────────────────────────────

let _dragSrc = null;

function initDragHandlers(row, handle) {
    handle.addEventListener("mousedown", () => { row.draggable = true; });
    handle.addEventListener("mouseup",   () => { row.draggable = false; });

    row.addEventListener("dragstart", (e) => {
        _dragSrc = row;
        e.dataTransfer.effectAllowed = "move";
        // Use a blank image so the browser's ghost doesn't interfere
        const blank = document.createElement("canvas");
        e.dataTransfer.setDragImage(blank, 0, 0);
        requestAnimationFrame(() => row.classList.add("var-dragging"));
    });

    row.addEventListener("dragend", () => {
        row.draggable = false;
        row.classList.remove("var-dragging");
        list.querySelectorAll(".variable-row").forEach(r => r.classList.remove("var-drag-over"));
        _dragSrc = null;
    });

    row.addEventListener("dragover", (e) => {
        if (!_dragSrc || _dragSrc === row) return;
        e.preventDefault();
        e.dataTransfer.dropEffect = "move";
        list.querySelectorAll(".variable-row").forEach(r => r.classList.remove("var-drag-over"));
        row.classList.add("var-drag-over");
    });

    row.addEventListener("dragleave", () => {
        row.classList.remove("var-drag-over");
    });

    row.addEventListener("drop", (e) => {
        e.preventDefault();
        if (!_dragSrc || _dragSrc === row) return;
        row.classList.remove("var-drag-over");

        // Insert before or after based on cursor position relative to row midpoint
        const rect = row.getBoundingClientRect();
        const after = e.clientY > rect.top + rect.height / 2;
        if (after) {
            row.after(_dragSrc);
        } else {
            row.before(_dragSrc);
        }
        markDirty();
    });
}

// ── Row creation ─────────────────────────────────────────────────────────

function addVariableRow(key = "", value = "") {
    const row = document.createElement("div");
    row.className = "variable-row";

    const handle = document.createElement("span");
    handle.className = "var-drag-handle";
    handle.textContent = "⠿";
    handle.title = "Drag to reorder";

    const keyInput = document.createElement("input");
    keyInput.className = "var-key";
    keyInput.type = "text";
    keyInput.placeholder = "KEY";
    keyInput.value = key;
    keyInput.spellcheck = false;

    const eq = document.createElement("span");
    eq.textContent = "=";
    eq.style.color = "#666";
    eq.style.flexShrink = "0";

    const valInput = document.createElement("input");
    valInput.className = "var-value";
    valInput.type = "text";
    valInput.placeholder = "value";
    valInput.value = value;
    valInput.spellcheck = false;

    const delBtn = document.createElement("button");
    delBtn.className = "var-delete";
    delBtn.textContent = "✕";

    keyInput.addEventListener("input", markDirty);
    valInput.addEventListener("input", markDirty);
    delBtn.addEventListener("click", () => {
        row.remove();
        markDirty();
        showEmptyIfNeeded();
    });

    row.appendChild(handle);
    row.appendChild(keyInput);
    row.appendChild(eq);
    row.appendChild(valInput);
    row.appendChild(delBtn);
    list.appendChild(row);

    initDragHandlers(row, handle);

    return keyInput;
}

function showEmptyIfNeeded() {
    if (!list) return;
    if (list.querySelectorAll(".variable-row").length === 0) {
        list.innerHTML = `<div class="no-sessions">No variables defined</div>`;
    }
}

// ── API ──────────────────────────────────────────────────────────────────

async function fetchVariables() {
    try {
        const res = await fetch(`${API_BASE}/variables`);
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();
        return data.variables || {};
    } catch (e) {
        console.warn("Failed to fetch variables:", e);
        return {};
    }
}

async function saveVariables() {
    // Collect rows
    const variables = {};
    const rows = list.querySelectorAll(".variable-row");
    for (const row of rows) {
        const k = row.querySelector(".var-key").value.trim();
        const v = row.querySelector(".var-value").value;
        if (k) variables[k] = v;
    }

    try {
        const res = await fetch(`${API_BASE}/variables`, {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ variables }),
        });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);

        markClean();

        // Brief "Saved!" feedback
        if (saveBtn) {
            saveBtn.textContent = "Saved!";
            saveBtn.style.display = "";
            setTimeout(() => {
                saveBtn.textContent = "Save";
                if (!dirty) saveBtn.style.display = "none";
            }, 1200);
        }
    } catch (e) {
        console.error("Failed to save variables:", e);
        if (saveBtn) {
            saveBtn.textContent = "Error";
            setTimeout(() => { saveBtn.textContent = "Save"; }, 1500);
        }
    }
}

// ── Render ────────────────────────────────────────────────────────────────

function renderVariables(variables) {
    if (!list) return;
    list.innerHTML = "";

    const entries = Object.entries(variables);
    if (entries.length === 0) {
        list.innerHTML = `<div class="no-sessions">No variables defined</div>`;
    } else {
        for (const [k, v] of entries) {
            addVariableRow(k, v);
        }
    }
    markClean();
}

// ── Init ─────────────────────────────────────────────────────────────────

export async function initVariablesPanel() {
    if (!list) return;

    // Wire buttons
    if (addBtn) {
        addBtn.addEventListener("click", () => {
            // Remove the "no variables" placeholder if present
            const placeholder = list.querySelector(".no-sessions");
            if (placeholder) placeholder.remove();

            const keyInput = addVariableRow("", "");
            markDirty();
            keyInput.focus();
        });
    }

    if (saveBtn) {
        saveBtn.addEventListener("click", saveVariables);
    }

    // Initial load
    const variables = await fetchVariables();
    renderVariables(variables);
}
