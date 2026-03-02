/**
 * Session management — handles session IDs, session UI, session switching,
 * and the session sync lifecycle.
 */

const API_BASE = "http://localhost:8000";

// --- Session State ---
let sessions = JSON.parse(localStorage.getItem("stackflow_sessions") || "[]");
let sessionId = localStorage.getItem("stackflow_active_session");

function generateSessionId() {
    return Math.random().toString(36).substring(2, 10);
}

// Initialize sessions if empty
if (sessions.length === 0) {
    sessionId = generateSessionId();
    sessions = [sessionId];
    localStorage.setItem("stackflow_sessions", JSON.stringify(sessions));
    localStorage.setItem("stackflow_active_session", sessionId);
} else if (!sessionId || !sessions.includes(sessionId)) {
    sessionId = sessions[0];
    localStorage.setItem("stackflow_active_session", sessionId);
}

/**
 * Returns the current active session ID.
 */
export function getSessionId() {
    return sessionId;
}

/**
 * Sets the current session ID and updates localStorage.
 */
export function setSessionId(newId) {
    sessionId = newId;
    localStorage.setItem("stackflow_active_session", sessionId);
}

/**
 * Returns the sessions array.
 */
export function getSessions() {
    return sessions;
}

/**
 * Sets the sessions array and updates localStorage.
 */
export function setSessions(newSessions) {
    sessions = newSessions;
    localStorage.setItem("stackflow_sessions", JSON.stringify(sessions));
}

/**
 * Syncs session logs with the server (trims stale sessions).
 */
export async function syncSessionLogs() {
    try {
        await fetch(`${API_BASE}/session/sync_logs`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ active_session_ids: sessions })
        });
    } catch (e) {
        console.warn("Failed to sync session logs with server", e);
    }
}

/**
 * Updates the session input and datalist UI elements.
 */
export function updateSessionUI() {
    const input = document.getElementById("session-input");
    const dlist = document.getElementById("session-list");
    if (!input || !dlist) return;

    if (!sessionId) {
        sessionId = localStorage.getItem("stackflow_active_session") || (sessions.length > 0 ? sessions[0] : null);
    }

    if (sessionId) {
        input.value = sessionId;
    }

    dlist.innerHTML = "";
    sessions.forEach(s => {
        const opt = document.createElement("option");
        opt.value = s;
        dlist.appendChild(opt);
    });
}

/**
 * Initializes session input event listeners.
 * @param {Function} reloadLogsFn - callback to reload logs on session change
 */
export function initSessionInput(reloadLogsFn) {
    const sessionInput = document.getElementById("session-input");
    if (!sessionInput) return;

    // Show all options on click by briefly clearing the filter
    sessionInput.addEventListener("mousedown", () => {
        if (sessionInput.value) {
            sessionInput.setAttribute('data-prev-val', sessionInput.value);
            sessionInput.value = "";
        }
    });

    sessionInput.addEventListener("focus", () => {
        // Clear on focus to allow the datalist to show all past sessions immediately
        if (sessionInput.value) {
            sessionInput.setAttribute('data-prev-val', sessionInput.value);
            sessionInput.value = "";
        }
    });

    function applySessionId() {
        const value = sessionInput.value.trim();
        const prev = sessionInput.getAttribute('data-prev-val');

        if (!value) {
            // Restore previous session if blurred without typing/selecting
            if (prev) {
                sessionId = prev;
                sessionInput.value = prev;
                return; // Nothing changed
            }
            sessionId = generateSessionId();
        } else {
            sessionId = value;
        }

        localStorage.setItem("stackflow_active_session", sessionId);

        if (prev !== sessionId) {
            // Remove if exists to move to front
            const idx = sessions.indexOf(sessionId);
            if (idx !== -1) {
                sessions.splice(idx, 1);
            }

            sessions.unshift(sessionId);
            if (sessions.length > 10) {
                sessions.pop();
                syncSessionLogs();
            }

            localStorage.setItem("stackflow_sessions", JSON.stringify(sessions));
            updateSessionUI();
            reloadLogsFn();
        } else {
            updateSessionUI();
        }
    }

    sessionInput.addEventListener("input", (e) => {
        const val = e.target.value.trim();
        if (val) {
            // If the value matches one of our known sessions, apply it immediately
            if (sessions.includes(val)) {
                applySessionId();
            }
        }
    });

    sessionInput.addEventListener("change", () => {
        applySessionId();
    });

    sessionInput.addEventListener("blur", () => {
        applySessionId();
    });

    // Initialize UI immediately
    updateSessionUI();

    // New Session button
    const newSessionBtn = document.getElementById("new-session");
    if (newSessionBtn) {
        newSessionBtn.addEventListener("click", () => {
            const newId = generateSessionId();
            sessions.unshift(newId);
            if (sessions.length > 10) {
                sessions.pop();
                syncSessionLogs();
            }
            sessionId = newId;
            localStorage.setItem("stackflow_sessions", JSON.stringify(sessions));
            localStorage.setItem("stackflow_active_session", sessionId);
            updateSessionUI();
            reloadLogsFn();
        });
    }
}
