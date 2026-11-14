// RichRender — shared markdown/LaTeX renderer for assistant responses.
//
// Pipeline: mask math ($...$ / $$...$$) outside code fences → marked (GFM)
// → DOMPurify → unmask math via katex.render on the sanitized DOM.
// Math is rendered AFTER sanitization from the raw TeX source, so KaTeX
// output never needs DOMPurify allowances (katex with trust:false is the
// sanitizer for math). Raw HTML from the LLM is escaped and shown literally.
//
// Used by chat.js (print RESPONSE) and call-mode.js (zephyro transcript
// entries). Every consumer must go through render(); nothing here throws —
// worst case the raw text is shown.

window.RichRender = (() => {
    'use strict';

    const MASK_OPEN = String.fromCharCode(0xE000);
    const MASK_CLOSE = String.fromCharCode(0xE001);

    const PURIFY_CFG = {
        ALLOWED_TAGS: ['p', 'br', 'hr', 'strong', 'b', 'em', 'i', 'del', 'code', 'pre',
            'blockquote', 'ul', 'ol', 'li', 'h1', 'h2', 'h3', 'h4',
            'table', 'thead', 'tbody', 'tr', 'th', 'td',
            'a', 'span', 'div', 'sup', 'sub', 'img'],
        ALLOWED_ATTR: ['href', 'title', 'class', 'align', 'start', 'colspan', 'rowspan',
            'src', 'alt'],
        // href sigue limitado a http(s); el src de <img> se valida en el hook
        // de abajo (solo data:image/png del backend de charts).
        ALLOWED_URI_REGEXP: /^(?:https?:|data:image\/png;base64,)/i,
        ALLOW_DATA_ATTR: false,
        FORBID_TAGS: ['style', 'svg', 'math', 'iframe', 'form', 'input', 'button'],
    };

    const IMG_DATA_URI = /^data:image\/png;base64,[A-Za-z0-9+/=]+$/;

    let hooksInstalled = false;
    function installHooks() {
        if (hooksInstalled || !window.DOMPurify) return;
        window.DOMPurify.addHook('uponSanitizeAttribute', (node, data) => {
            // <img> only renders backend chart PNGs; any other source dies.
            if (node.tagName === 'IMG' && data.attrName === 'src'
                && !IMG_DATA_URI.test(data.attrValue)) {
                data.keepAttr = false;
            }
            // data: URIs never valid on links, only on <img src>.
            if (node.tagName === 'A' && data.attrName === 'href'
                && /^data:/i.test(data.attrValue.trim())) {
                data.keepAttr = false;
            }
        });
        window.DOMPurify.addHook('afterSanitizeAttributes', (node) => {
            if (node.tagName === 'A') {
                node.setAttribute('target', '_blank');
                node.setAttribute('rel', 'noopener noreferrer');
            }
        });
        hooksInstalled = true;
    }

    function escapeHtml(text) {
        return String(text ?? '')
            .replaceAll('&', '&amp;')
            .replaceAll('<', '&lt;')
            .replaceAll('>', '&gt;');
    }

    // Inline $...$ is ambiguous with prices ("cuesta $50 y $30"). Only treat
    // the span as math when it contains TeX-ish characters, is very short
    // (single symbols like $x$), or is function/complexity notation like
    // $O(n)$, $f(x)$, $T(n log n)$. Display $$...$$ is always math.
    function looksLikeMath(content) {
        const c = content.trim();
        return /[\\^_{}=]/.test(c)
            || c.length <= 3
            || /^[A-Za-z]{1,3}\([^()]{0,16}\)$/.test(c);
    }

    function maskMath(text, slots) {
        const maskIn = (segment) => segment
            .replace(/\$\$([\s\S]+?)\$\$/g, (_m, tex) => {
                slots.push({ tex, display: true });
                return MASK_OPEN + (slots.length - 1) + MASK_CLOSE;
            })
            .replace(/(?<![\\\w$])\$([^$\n]+?)\$(?![\w$])/g, (m, tex) => {
                if (!looksLikeMath(tex)) return m;
                slots.push({ tex, display: false });
                return MASK_OPEN + (slots.length - 1) + MASK_CLOSE;
            });

        // Never mask inside code fences: split on ``` and only touch the
        // segments outside them (even indices).
        const parts = text.split(/(```[\s\S]*?(?:```|$))/);
        return parts.map((part, i) => (i % 2 === 0 ? maskIn(part) : part)).join('');
    }

    function unmaskMath(root, slots) {
        const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
        const targets = [];
        let node;
        while ((node = walker.nextNode())) {
            if (node.nodeValue.includes(MASK_OPEN)) targets.push(node);
        }
        const maskRe = new RegExp(MASK_OPEN + '(\\d+)' + MASK_CLOSE);
        for (const textNode of targets) {
            let current = textNode;
            let match;
            while (current && (match = current.nodeValue.match(maskRe))) {
                const slot = slots[Number(match[1])];
                const after = current.splitText(match.index);
                after.nodeValue = after.nodeValue.slice(match[0].length);
                const span = document.createElement('span');
                current.parentNode.insertBefore(span, after);
                renderTex(span, slot);
                current = after;
            }
        }
    }

    function renderTex(span, slot) {
        if (!slot) return;
        try {
            window.katex.render(slot.tex, span, {
                throwOnError: false,
                errorColor: '#ff5370',
                trust: false,
                strict: 'ignore',
                maxExpand: 1000,
                displayMode: slot.display,
            });
        } catch (_e) {
            span.textContent = '$' + slot.tex + '$';
        }
    }

    let markedConfigured = false;
    function configureMarked() {
        if (markedConfigured) return;
        // Raw HTML from the LLM is displayed literally, never interpreted
        // (belt; DOMPurify is the suspenders).
        window.marked.use({
            gfm: true,
            breaks: true,
            renderer: {
                html(token) {
                    return escapeHtml(token?.raw ?? token?.text ?? '');
                },
                code(token) {
                    if ((token?.lang || '').trim() === 'mermaid') {
                        return '<pre class="mermaid-pending">'
                            + escapeHtml(token.text) + '</pre>\n';
                    }
                    return false; // default renderer
                },
            },
        });
        markedConfigured = true;
    }

    let mermaidReady = false;
    let mermaidSeq = 0;
    function initMermaid() {
        if (mermaidReady || !window.mermaid) return mermaidReady;
        try {
            window.mermaid.initialize({
                startOnLoad: false,
                securityLevel: 'strict',
                theme: 'dark',
                // SVG text labels instead of <foreignObject> HTML, so the
                // SVG-profile DOMPurify pass below keeps every label.
                // Mermaid 11 ignores flowchart.htmlLabels unless the top-level
                // flag is ALSO false (config resolution quirk): with only the
                // nested flag, node labels come out as <foreignObject>, which
                // DOMPurify strips → invisible boxes.
                htmlLabels: false,
                flowchart: { htmlLabels: false },
                themeVariables: {
                    primaryColor: '#111917',
                    primaryTextColor: '#CFFFEA',
                    primaryBorderColor: '#1B2A25',
                    lineColor: '#00FF88',
                    fontFamily: 'inherit',
                },
            });
            mermaidReady = true;
        } catch (e) {
            console.warn('[RichRender] mermaid init failed:', e);
        }
        return mermaidReady;
    }

    // Shared full-diagram modal (singleton, lazily created on first use).
    // One modal for the whole app: only one diagram can be inspected at a
    // time, which is also the desired UX (no stacked overlays).
    let modalEls = null;
    let modalKeyHandler = null;

    function ensureDiagramModal() {
        if (modalEls) return modalEls;
        const backdrop = document.createElement('div');
        backdrop.className = 'mermaid-modal-backdrop';
        backdrop.innerHTML =
            '<div class="mermaid-modal">'
            + '<div class="mermaid-modal-head">'
            + '<span>DIAGRAMA</span>'
            + '<button type="button" class="mermaid-modal-close" aria-label="Cerrar">&times;</button>'
            + '</div>'
            + '<div class="mermaid-modal-body"></div>'
            + '</div>';
        document.body.appendChild(backdrop);
        const body = backdrop.querySelector('.mermaid-modal-body');
        backdrop.querySelector('.mermaid-modal-close')
            .addEventListener('click', closeDiagramModal);
        // Only the backdrop click (not clicks inside the diagram) closes it.
        backdrop.addEventListener('click', (e) => {
            if (e.target === backdrop) closeDiagramModal();
        });
        modalEls = { backdrop, body };
        return modalEls;
    }

    function openDiagramModal(svgHtml) {
        const { backdrop, body } = ensureDiagramModal();
        body.innerHTML = svgHtml;
        backdrop.classList.add('active');
        // Capture phase + stopPropagation: closing the modal must not also
        // trigger call-mode's own Escape handler (barge-in/hangup), which is
        // bound on window in the bubble phase.
        modalKeyHandler = (e) => {
            if (e.key === 'Escape') {
                e.stopPropagation();
                e.preventDefault();
                closeDiagramModal();
            }
        };
        window.addEventListener('keydown', modalKeyHandler, { capture: true });
    }

    function closeDiagramModal() {
        if (!modalEls) return;
        modalEls.backdrop.classList.remove('active');
        modalEls.body.innerHTML = '';
        if (modalKeyHandler) {
            window.removeEventListener('keydown', modalKeyHandler, { capture: true });
            modalKeyHandler = null;
        }
    }

    // Async pass: ```mermaid blocks were emitted as <pre class="mermaid-pending">
    // with the escaped source. Render each to SVG; on failure the source stays
    // visible as a code block with an error badge. Never throws.
    //
    // Large diagrams (algorithm traces, multi-branch flows) are capped to a
    // preview height with an "expand" button that opens the full diagram in
    // a modal — a full-size graph inline was crowding the chat.
    function renderMermaidBlocks(root) {
        if (!initMermaid()) return;
        root.querySelectorAll('pre.mermaid-pending').forEach(async (pre) => {
            const source = pre.textContent;
            pre.classList.remove('mermaid-pending');
            const id = 'rich-mmd-' + (++mermaidSeq);
            try {
                const { svg } = await window.mermaid.render(id, source);
                const clean = window.DOMPurify.sanitize(svg, {
                    USE_PROFILES: { svg: true, svgFilters: true },
                });
                const wrap = document.createElement('div');
                wrap.className = 'mermaid-preview';
                const box = document.createElement('div');
                box.className = 'mermaid-svg';
                box.innerHTML = clean;
                wrap.appendChild(box);
                pre.replaceWith(wrap);

                // scrollHeight needs a layout pass to be accurate right after
                // attaching to the DOM.
                requestAnimationFrame(() => {
                    if (box.scrollHeight > box.clientHeight + 2) {
                        wrap.classList.add('is-capped');
                        const btn = document.createElement('button');
                        btn.type = 'button';
                        btn.className = 'mermaid-expand-btn';
                        btn.textContent = '⤢ Ver diagrama completo';
                        btn.addEventListener('click', () => openDiagramModal(clean));
                        wrap.appendChild(btn);
                    }
                });
            } catch (_e) {
                // mermaid can leave a temp error element behind on failure
                document.getElementById('d' + id)?.remove();
                const badge = document.createElement('span');
                badge.className = 'code-badge';
                badge.textContent = 'DIAGRAMA INVALIDO';
                pre.before(badge);
            }
        });
    }

    function available() {
        return Boolean(window.marked && window.DOMPurify && window.katex
            && window.DOMPurify.isSupported !== false);
    }

    function render(text) {
        const body = document.createElement('div');
        body.className = 'rich-body';
        const raw = String(text ?? '');
        try {
            configureMarked();
            installHooks();
            const slots = [];
            const masked = maskMath(raw, slots);
            const html = window.marked.parse(masked);
            body.innerHTML = window.DOMPurify.sanitize(html, PURIFY_CFG);
            if (slots.length) unmaskMath(body, slots);
            renderMermaidBlocks(body);
        } catch (e) {
            console.warn('[RichRender] fallback to plain text:', e);
            body.textContent = raw;
        }
        return body;
    }

    return { render, available };
})();
