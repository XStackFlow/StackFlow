// Formats a Jinja2 template string with indentation for block tags.
function _formatJinja(template) {
    const INDENT = "    ";
    const tokens = [];
    const tagRe = /\{%-?[\s\S]*?-?%\}/g;
    let last = 0, m;
    while ((m = tagRe.exec(template)) !== null) {
        if (m.index > last) tokens.push({ type: "text", val: template.slice(last, m.index) });
        tokens.push({ type: "tag", val: m[0] });
        last = m.index + m[0].length;
    }
    if (last < template.length) tokens.push({ type: "text", val: template.slice(last) });

    const OPEN  = /^\{%-?\s*(if|for|block|macro|call|filter|with)\b/;
    const CLOSE = /^\{%-?\s*(endif|endfor|endblock|endmacro|endcall|endfilter|endwith)\b/;
    const MID   = /^\{%-?\s*(else|elif)\b/;

    let indent = 0;
    const lines = [];
    for (const tok of tokens) {
        if (tok.type === "tag") {
            const tag = tok.val.trim();
            if (CLOSE.test(tag) || MID.test(tag)) indent = Math.max(0, indent - 1);
            lines.push(INDENT.repeat(indent) + tag);
            if (OPEN.test(tag) || MID.test(tag)) indent++;
        } else {
            // Text lines must NEVER be indented — Jinja2 renders any leading
            // whitespace verbatim (unlike block tags, which lstrip_blocks strips).
            //
            // Boundary cleanup:
            //   • Strip exactly one leading \n — trim_blocks eats that newline at
            //     render time, so preserving it would add a phantom blank line.
            //   • Strip all trailing whitespace — it's always the indentation of
            //     the next block tag plus a line-ending, both harmless to remove.
            //
            // A lone \n remaining in the middle of a token is an intentional
            // section-separator blank line and is preserved.
            const inner = tok.val.replace(/^\n/, '').replace(/\s+$/, '');
            if (!inner) continue;
            for (const seg of inner.split('\n')) {
                lines.push(seg.trim());
            }
        }
    }

    // Collapse runs of 2+ blank lines to exactly one, and strip leading/trailing blanks.
    const result = [];
    let prevBlank = false;
    for (const line of lines) {
        const isBlank = line === '';
        if (isBlank && prevBlank) continue;
        result.push(line);
        prevBlank = isBlank;
    }
    while (result.length && result[0] === '') result.shift();
    while (result.length && result[result.length - 1] === '') result.pop();

    return result.join('\n');
}

// Helper for in-place JSON editing
export function openJSONEditor(node, widget, pName, canvas, isDirty, onSave = null, mode = "json") {
    const isText     = mode === "text" || mode === "template";
    const isTemplate = mode === "template";
    if (!canvas || !canvas.ds) return;

    // Create a background overlay
    const overlay = document.createElement("div");
    overlay.id = "json-editor-overlay";
    overlay.style.position = "fixed";
    overlay.style.top = "0";
    overlay.style.left = "0";
    overlay.style.width = "100%";
    overlay.style.height = "100%";
    overlay.style.backgroundColor = "rgba(0, 0, 0, 0.7)";
    overlay.style.backdropFilter = "blur(4px)";
    overlay.style.zIndex = "2000";
    overlay.style.display = "flex";
    overlay.style.justifyContent = "center";
    overlay.style.alignItems = "center";

    // Create container
    const container = document.createElement("div");
    container.style.width = "90vw";
    container.style.maxWidth = "1200px";
    container.style.height = "85vh";
    container.style.maxHeight = "1000px";
    container.style.background = "#0f172a";
    container.style.border = "1px solid #3b82f6";
    container.style.borderRadius = "12px";
    container.style.display = "flex";
    container.style.flexDirection = "column";
    container.style.boxShadow = "0 25px 50px -12px rgba(0, 0, 0, 0.5)";
    container.style.overflow = "hidden";
    container.style.animation = "modalFadeIn 0.2s ease-out";

    // Add animation style if not exists
    if (!document.getElementById("modal-styles")) {
        const style = document.createElement("style");
        style.id = "modal-styles";
        style.innerHTML = `
            @keyframes modalFadeIn {
                from { opacity: 0; transform: scale(0.95); }
                to { opacity: 1; transform: scale(1); }
            }
        `;
        document.head.appendChild(style);
    }

    // Create header
    const header = document.createElement("div");
    header.style.padding = "12px 20px";
    header.style.background = "#1e293b";
    header.style.color = "#60a5fa";
    header.style.fontSize = "13px";
    header.style.fontFamily = "'Inter', sans-serif";
    header.style.fontWeight = "600";
    header.style.textTransform = "uppercase";
    header.style.letterSpacing = "0.05em";
    header.style.borderBottom = "1px solid #334155";
    header.style.display = "flex";
    header.style.justifyContent = "space-between";
    header.style.alignItems = "center";
    header.innerHTML = `
        <span>Editing: <span style="color: #cbd5e1; margin-left: 8px;">${pName}</span></span>
        <div style="display: flex; gap: 12px; font-size: 10px; opacity: 0.6;">
            <span>Ctrl + Enter to Save</span>
            <span>Esc to Cancel</span>
        </div>
    `;

    // Create body container for textarea and line numbers
    const body = document.createElement("div");
    body.style.flex = "1";
    body.style.display = "flex";
    body.style.position = "relative";
    body.style.overflow = "hidden";
    body.style.background = "#020617";

    // Create line numbers gutter
    const gutter = document.createElement("div");
    gutter.style.width = "40px";
    gutter.style.padding = "20px 0";
    gutter.style.background = "#0f172a";
    gutter.style.color = "#475569";
    gutter.style.fontFamily = "'Fira Code', monospace";
    gutter.style.fontSize = "14px";
    gutter.style.lineHeight = "1.6";
    gutter.style.textAlign = "right";
    gutter.style.paddingRight = "10px";
    gutter.style.userSelect = "none";
    gutter.style.borderRight = "1px solid #1e293b";
    gutter.style.overflow = "hidden";

    // Create textarea
    let initialVal = (node.properties && node.properties[pName] !== undefined) ? node.properties[pName] : (isText ? "" : "{}");
    if (!isText) {
        try {
            initialVal = JSON.stringify(JSON.parse(initialVal), null, 4);
        } catch (e) { }
    }

    // Create area (textarea for edit, div for view)
    let area;

    // Synchronize gutter and area
    const updateLineNumbers = () => {
        let lineCount;
        if (mode === "viewer") {
            // In viewer mode, we need to count physical lines because some divs might have multi-line content
            const temp = document.createElement("div");
            temp.style.width = area.clientWidth + "px";
            temp.style.font = area.style.font;
            temp.style.lineHeight = area.style.lineHeight;
            temp.style.whiteSpace = "pre";
            // Rough estimation for line numbers
            lineCount = area.innerText.split("\n").length;
        } else {
            lineCount = area.value.split("\n").length;
        }
        gutter.innerHTML = Array.from({ length: lineCount }, (_, i) => i + 1).join("<br>");
    };

    // Create area (textarea for edit, div for view)
    if (mode === "viewer") {
        // Hide gutter — line numbers don't apply to a collapsible tree
        gutter.style.display = "none";

        area = document.createElement("div");
        area.style.flex = "1";
        area.style.background = "transparent";
        area.style.padding = "12px 16px";
        area.style.fontFamily = "'Fira Code', 'JetBrains Mono', monospace";
        area.style.fontSize = "13px";
        area.style.lineHeight = "1.6";
        area.style.overflow = "auto";
        area.style.outline = "none";

        // Inject tree styles once
        if (!document.getElementById("jt-styles")) {
            const s = document.createElement("style");
            s.id = "jt-styles";
            s.textContent = `
                .jt-root { user-select: text; }
                .jt-row  {}
                .jt-hdr  { display:flex; align-items:center; padding:2px 4px; cursor:pointer; border-radius:3px; }
                .jt-hdr:hover { background: rgba(255,255,255,0.05); }
                .jt-arr  { width:16px; flex-shrink:0; color:#555; font-size:9px; transition:transform .15s; }
                .jt-arr::before { content:'▶'; }
                .jt-row.open > .jt-hdr .jt-arr { transform:rotate(90deg); color:#888; }
                .jt-key  { color:#9cdcfe; }
                .jt-idx  { color:#888; font-size:11px; }
                .jt-brace { color:#666; }
                .jt-hint { color:#3a3a3a; font-size:11px; margin:0 4px; }
                .jt-row.open > .jt-hdr .jt-hint,
                .jt-row.open > .jt-hdr .jt-bc { display:none; }
                .jt-children { display:none; margin-left:18px; border-left:1px solid #222; padding-left:4px; }
                .jt-row.open > .jt-children { display:block; }
                .jt-closing { display:none; color:#666; padding:2px 4px 2px 20px; }
                .jt-row.open > .jt-closing { display:block; }
                .jt-leaf { display:flex; align-items:baseline; padding:2px 4px 2px 20px; }
                .jt-sep  { color:#555; margin:0 3px; }
                .jt-str  { color:#ce9178; }
                .jt-num  { color:#b5cea8; }
                .jt-bool { color:#569cd6; }
                .jt-null { color:#666; }
            `;
            document.head.appendChild(s);
        }

        const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, m =>
            ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#039;'}[m]));

        function primitive(v) {
            if (v === null)             return `<span class="jt-null">null</span>`;
            if (typeof v === 'boolean') return `<span class="jt-bool">${v}</span>`;
            if (typeof v === 'number')  return `<span class="jt-num">${v}</span>`;
            return `<span class="jt-str">"${esc(v)}"</span>`;
        }

        function buildTree(value) {
            const isArr = Array.isArray(value);
            const entries = isArr ? value.map((v, i) => [i, v]) : Object.entries(value);
            if (entries.length === 0) return '';
            return entries.map(([k, v]) => {
                const keyHtml = isArr
                    ? `<span class="jt-idx">${k}</span>`
                    : `<span class="jt-key">${esc(k)}</span>`;
                const isComplex = v !== null && typeof v === 'object';
                if (isComplex) {
                    const childArr   = Array.isArray(v);
                    const childCount = childArr ? v.length : Object.keys(v).length;
                    const ob = childArr ? '[' : '{';
                    const cb = childArr ? ']' : '}';
                    const hint = childCount > 0 ? (childArr ? `${childCount} items` : `${childCount} keys`) : '';
                    return `<div class="jt-row">` +
                        `<div class="jt-hdr" onclick="this.closest('.jt-row').classList.toggle('open')">` +
                        `<span class="jt-arr"></span>${keyHtml}` +
                        `<span class="jt-sep">:</span>` +
                        `<span class="jt-brace">&thinsp;${ob}</span>` +
                        `<span class="jt-hint">${hint}</span>` +
                        `<span class="jt-brace jt-bc">${cb}</span>` +
                        `</div>` +
                        `<div class="jt-children">${buildTree(v)}</div>` +
                        `<div class="jt-closing">${cb}</div>` +
                        `</div>`;
                } else {
                    return `<div class="jt-leaf">${keyHtml}<span class="jt-sep">:</span>${primitive(v)}</div>`;
                }
            }).join('');
        }

        try {
            const parsed = JSON.parse(initialVal);
            const isRootArr = Array.isArray(parsed);
            const ob = isRootArr ? '[' : '{';
            const cb = isRootArr ? ']' : '}';
            const rootCount = isRootArr ? parsed.length : Object.keys(parsed).length;
            const rootHint = rootCount > 0 ? (isRootArr ? `${rootCount} items` : `${rootCount} keys`) : '';
            area.innerHTML = `<div class="jt-root">` +
                `<div class="jt-row open">` +
                `<div class="jt-hdr" onclick="this.closest('.jt-row').classList.toggle('open')">` +
                `<span class="jt-arr"></span>` +
                `<span class="jt-brace">&thinsp;${ob}</span>` +
                `<span class="jt-hint">${rootHint}</span>` +
                `<span class="jt-brace jt-bc">${cb}</span>` +
                `</div>` +
                `<div class="jt-children">${buildTree(parsed)}</div>` +
                `<div class="jt-closing">${cb}</div>` +
                `</div>` +
                `</div>`;
        } catch (e) {
            area.style.whiteSpace = "pre";
            area.style.color = "#ccc";
            area.textContent = initialVal;
        }

    } else {
        area = document.createElement("textarea");
        area.value = initialVal;
        area.style.flex = "1";
        area.style.background = "transparent";
        area.style.color = "#10b981";
        area.style.border = "none";
        area.style.padding = "20px";
        area.style.fontFamily = "'Fira Code', monospace";
        area.style.fontSize = "14px";
        area.style.lineHeight = "1.6";
        area.style.outline = "none";
        area.style.resize = "none";
        area.style.whiteSpace = "pre";
        area.style.overflow = "auto";
        area.spellcheck = false;
        area.oninput = updateLineNumbers;
    }

    area.onscroll = () => {
        gutter.scrollTop = area.scrollTop;
    };

    updateLineNumbers();

    body.appendChild(gutter);
    body.appendChild(area);

    let finished = false;
    let finish = (save) => {
        if (finished) return;

        if (save && mode !== "viewer") {
            let val = area.value;
            if (!isText) {
                try {
                    // Validate and save as compact JSON
                    val = JSON.stringify(JSON.parse(val));
                } catch (e) {
                    alert("Invalid JSON: " + e.message);
                    return; // Allow the user to try again
                }
            }
            if (onSave) {
                onSave(val);
            } else {
                node.properties[pName] = val;
                widget.value = val;
                if (node.onPropertyChanged) node.onPropertyChanged(pName, val);
                isDirty.value = true;
            }
        }

        finished = true;
        document.body.removeChild(overlay);
        if (canvas && canvas.draw) canvas.draw(true, true);
    };

    const handleKey = (e) => {
        if (e.key === "Escape") {
            finish(false);
            e.stopPropagation();
            e.preventDefault();
        }
        if (e.key === "Enter" && (e.ctrlKey || e.metaKey) && mode !== "viewer") {
            finish(true);
            e.stopPropagation();
            e.preventDefault();
        }
    };

    if (mode === "viewer") {
        window.addEventListener("keydown", handleKey);
        const originalFinish = finish;
        finish = (save) => {
            window.removeEventListener("keydown", handleKey);
            originalFinish(save);
        };
    } else {
        area.onkeydown = handleKey;
    }

    // Create footer
    const footer = document.createElement("div");
    footer.style.padding = "10px 20px";
    footer.style.background = "#1e293b";
    footer.style.borderTop = "1px solid #334155";
    footer.style.display = "flex";
    footer.style.justifyContent = "flex-end";
    footer.style.gap = "10px";

    const exitBtn = document.createElement("button");
    exitBtn.innerText = mode === "viewer" ? "Close" : "Exit";
    exitBtn.style.padding = "6px 16px";
    exitBtn.style.background = "transparent";
    exitBtn.style.color = "#94a3b8";
    exitBtn.style.border = "1px solid #334155";
    exitBtn.style.borderRadius = "4px";
    exitBtn.style.cursor = "pointer";
    exitBtn.style.fontSize = "12px";
    exitBtn.style.transition = "all 0.2s";
    exitBtn.onmouseover = () => exitBtn.style.background = "rgba(255,255,255,0.05)";
    exitBtn.onmouseout = () => exitBtn.style.background = "transparent";
    exitBtn.onclick = () => finish(false);

    if (mode !== "viewer") {
        if (!isText) {
            const formatBtn = document.createElement("button");
            formatBtn.innerText = "Format";
            formatBtn.style.padding = "6px 16px";
            formatBtn.style.background = "rgba(16, 185, 129, 0.1)";
            formatBtn.style.color = "#10b981";
            formatBtn.style.border = "1px solid rgba(16, 185, 129, 0.3)";
            formatBtn.style.borderRadius = "4px";
            formatBtn.style.cursor = "pointer";
            formatBtn.style.fontSize = "12px";
            formatBtn.style.marginRight = "auto";
            formatBtn.style.transition = "all 0.2s";
            formatBtn.onmouseover = () => formatBtn.style.background = "rgba(16, 185, 129, 0.2)";
            formatBtn.onmouseout = () => formatBtn.style.background = "rgba(16, 185, 129, 0.1)";
            formatBtn.onclick = () => {
                try {
                    area.value = JSON.stringify(JSON.parse(area.value), null, 4);
                    updateLineNumbers();
                } catch (e) {
                    alert("Invalid JSON: " + e.message);
                }
            };
            footer.appendChild(formatBtn);
        }

        if (isTemplate) {
            const prettyBtn = document.createElement("button");
            prettyBtn.innerText = "Pretty";
            prettyBtn.title = "Indent {% if %}, {% for %} and other block tags";
            prettyBtn.style.padding = "6px 16px";
            prettyBtn.style.background = "rgba(16, 185, 129, 0.1)";
            prettyBtn.style.color = "#10b981";
            prettyBtn.style.border = "1px solid rgba(16, 185, 129, 0.3)";
            prettyBtn.style.borderRadius = "4px";
            prettyBtn.style.cursor = "pointer";
            prettyBtn.style.fontSize = "12px";
            prettyBtn.style.marginRight = "auto";
            prettyBtn.style.transition = "all 0.2s";
            prettyBtn.onmouseover = () => prettyBtn.style.background = "rgba(16, 185, 129, 0.2)";
            prettyBtn.onmouseout = () => prettyBtn.style.background = "rgba(16, 185, 129, 0.1)";
            prettyBtn.onclick = () => {
                area.value = _formatJinja(area.value);
                updateLineNumbers();
            };
            footer.appendChild(prettyBtn);
        }

        const saveBtn = document.createElement("button");
        saveBtn.innerText = "Save";
        saveBtn.style.padding = "6px 16px";
        saveBtn.style.background = "#3b82f6";
        saveBtn.style.color = "white";
        saveBtn.style.border = "none";
        saveBtn.style.borderRadius = "4px";
        saveBtn.style.cursor = "pointer";
        saveBtn.style.fontSize = "12px";
        saveBtn.style.fontWeight = "600";
        saveBtn.style.transition = "all 0.2s";
        saveBtn.onmouseover = () => saveBtn.style.background = "#2563eb";
        saveBtn.onmouseout = () => saveBtn.style.background = "#3b82f6";
        saveBtn.onclick = () => finish(true);
        footer.appendChild(saveBtn);
    }

    footer.appendChild(exitBtn);

    // Append to DOM after all logic is set up
    container.appendChild(header);
    container.appendChild(body);
    container.appendChild(footer);
    overlay.appendChild(container);
    document.body.appendChild(overlay);

    // Focus for immediate keyboard interaction
    if (mode === "viewer") {
        area.tabIndex = -1; // Make div focusable
    }
    area.focus();
}
