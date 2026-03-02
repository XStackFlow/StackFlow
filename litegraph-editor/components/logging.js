/**
 * Logging system — manages the log panel UI, log creation, and server-side log posting.
 */

const API_BASE = "http://localhost:8000";

// DOM references
const logContainer = document.getElementById("log-container");
const logContent = document.getElementById("log-content");
const toggleLogsBtn = document.getElementById("toggle-logs");
const logHeader = document.querySelector(".log-header");

/**
 * Adds a log entry to the log panel UI.
 */
export function addLog(message, type = "info") {
    if (!logContent) return;

    // Check if message already contains a backend timestamp like [11:18:16 AM]
    const tsMatch = message.match(/^\[(\d{1,2}:\d{2}:\d{2} [AP]M)\] (.*)/s);

    let timestamp, content;
    if (tsMatch) {
        timestamp = tsMatch[1];
        content = tsMatch[2];
    } else {
        timestamp = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
        content = message;
    }

    console.log(`[Log] ${type.toUpperCase()}: ${content}`);

    const entry = document.createElement("div");
    entry.className = `log-entry ${type}`;

    // Make multi-line logs (like JSON) collapsible
    const isMultiline = content.split('\n').length > 1;
    if (isMultiline) {
        entry.classList.add("collapsible", "collapsed");
        entry.addEventListener("click", () => {
            // Don't toggle if user is selecting text
            const selection = window.getSelection();
            if (selection.toString().length > 0) return;

            entry.classList.toggle("collapsed");
        });
    }

    // Create timestamp element
    const tsSpan = document.createElement("span");
    tsSpan.className = "log-timestamp";
    tsSpan.textContent = `[${timestamp}] `;

    // Create message content element
    const msgSpan = document.createElement("span");
    msgSpan.className = "log-message";
    msgSpan.textContent = content;

    // Create copy button
    const copyBtn = document.createElement("button");
    copyBtn.className = "log-copy-btn";
    copyBtn.textContent = "Copy";
    copyBtn.onclick = (e) => {
        e.stopPropagation(); // Don't toggle collapse
        navigator.clipboard.writeText(content).then(() => {
            copyBtn.textContent = "Copied!";
            copyBtn.classList.add("copied");
            setTimeout(() => {
                copyBtn.textContent = "Copy";
                copyBtn.classList.remove("copied");
            }, 2000);
        });
    };

    entry.appendChild(tsSpan);
    entry.appendChild(msgSpan);
    entry.appendChild(copyBtn);

    logContent.appendChild(entry);
    logContent.scrollTop = logContent.scrollHeight;
}

/**
 * Posts a log message to the backend for persistence.
 */
export async function pushLog(threadId, message, level = "info") {
    if (!threadId) return;
    try {
        await fetch(`${API_BASE}/post_log`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                thread_id: threadId,
                message: message,
                level: level
            })
        });
    } catch (e) {
        console.warn("Failed to push log to server:", e);
    }
}

/**
 * Returns the log content DOM element (for clearing, etc.)
 */
export function getLogContent() {
    return logContent;
}

/**
 * Returns the log container DOM element.
 */
export function getLogContainer() {
    return logContainer;
}

// --- Log Header Toggle ---
if (logHeader) {
    logHeader.addEventListener("click", () => {
        const isCollapsed = logContainer.classList.contains("collapsed");
        logContainer.classList.remove(isCollapsed ? "collapsed" : "expanded");
        logContainer.classList.add(isCollapsed ? "expanded" : "collapsed");
        toggleLogsBtn.textContent = isCollapsed ? "Collapse" : "Expand";
    });
}

