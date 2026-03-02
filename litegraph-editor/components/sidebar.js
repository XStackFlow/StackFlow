/**
 * Sidebar & Resize utilities — manages the state panel, right sidebar toggles,
 * and drag-to-resize functionality for bottom panels.
 */

import { openJSONEditor } from './json_editor.js';
import { addLog } from './logging.js';

// --- STATE PANEL TOGGLE ---
const stateContainer = document.getElementById("current-state-container");
const stateHeader = document.querySelector(".current-state-header");
const toggleStateBtn = document.getElementById("toggle-state");

if (stateHeader) {
    stateHeader.addEventListener("click", (e) => {
        // Only toggle if we didn't click on a button
        if (e.target.tagName === "BUTTON") return;
        const isCollapsed = stateContainer.classList.contains("collapsed");
        stateContainer.classList.toggle("collapsed");
        toggleStateBtn.textContent = isCollapsed ? "Collapse" : "Expand";
    });
}

/**
 * Sets up the "View Full State" button to open a read-only JSON editor modal.
 * @param {Function} getLastStateData - returns the current lastStateData object
 * @param {LGraphCanvas} canvas - the LiteGraph canvas instance
 */
export function initViewStateButton(getLastStateData, canvas) {
    const viewStateFullBtn = document.getElementById("view-state-full");
    if (viewStateFullBtn) {
        viewStateFullBtn.addEventListener("click", (e) => {
            e.stopPropagation();
            const lastStateData = getLastStateData();
            const displayState = lastStateData.last_state || lastStateData.result;
            if (!displayState || Object.keys(displayState).length === 0) {
                addLog("System: No active state available to view", "info");
                return;
            }

            // Open the JSON editor modal to show the full state
            const pName = "Active State";
            const jsonStr = JSON.stringify(displayState, null, 2);
            const mockNode = { properties: { [pName]: jsonStr } };
            const mockWidget = { value: jsonStr };

            openJSONEditor(mockNode, mockWidget, pName, canvas, { value: false }, () => {
                // Read-only-ish view: we don't do anything on save currently
            }, "viewer");
        });
    }
}

// --- RIGHT SIDEBAR TOGGLE ---
const rightSidebar = document.getElementById("right-sidebar");
const rightSidebarToggler = document.getElementById("right-sidebar-toggler");
const toggleRightSidebarBtn = document.getElementById("toggle-right-sidebar");

if (rightSidebarToggler) {
    rightSidebarToggler.addEventListener("click", () => {
        const isCollapsed = rightSidebar.classList.contains("collapsed");
        rightSidebar.classList.toggle("collapsed");
        toggleRightSidebarBtn.textContent = isCollapsed ? "Collapse" : "Expand";
    });
}

// --- RESIZING SYSTEM ---

function makeResizable(container, handle) {
    let startY, startHeight;

    handle.addEventListener("mousedown", (e) => {
        if (container.classList.contains("collapsed")) return;
        startY = e.clientY;
        startHeight = parseInt(document.defaultView.getComputedStyle(container).height, 10);
        container.classList.add("resizing");
        document.documentElement.addEventListener("mousemove", doDrag, false);
        document.documentElement.addEventListener("mouseup", stopDrag, false);
        e.preventDefault();
    });

    function doDrag(e) {
        // e.clientY is in visual (zoomed) pixels; getComputedStyle heights are in CSS pixels.
        // Divide the delta by the body zoom factor to keep them in the same coordinate space.
        const zoom = parseFloat(getComputedStyle(document.body).zoom) || 1;
        const h = startHeight - (e.clientY - startY) / zoom;
        const minHeight = 100;
        const maxHeight = window.innerHeight / zoom - 50;

        if (h <= minHeight) {
            container.style.height = minHeight + "px";
            stopDrag();
        } else if (h >= maxHeight) {
            container.style.height = maxHeight + "px";
            stopDrag();
        } else {
            container.style.height = h + "px";
        }
    }

    function stopDrag() {
        container.classList.remove("resizing");
        document.documentElement.removeEventListener("mousemove", doDrag, false);
        document.documentElement.removeEventListener("mouseup", stopDrag, false);
    }
}

// Apply resize handles
const logContainer = document.getElementById("log-container");
const stateResizeHandle = stateContainer ? stateContainer.querySelector(".resize-handle") : null;
const logResizeHandle = logContainer ? logContainer.querySelector(".resize-handle") : null;

if (stateContainer && stateResizeHandle) makeResizable(stateContainer, stateResizeHandle);
if (logContainer && logResizeHandle) makeResizable(logContainer, logResizeHandle);

/**
 * Returns the state container element.
 */
export function getStateContainer() {
    return stateContainer;
}
