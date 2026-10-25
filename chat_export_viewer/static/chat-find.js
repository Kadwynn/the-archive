/* Search the displayed message bodies, leaving metadata and raw JSON out. */
(() => {
  "use strict";

  const blockTags = new Set([
    "BLOCKQUOTE", "DIV", "H1", "H2", "H3", "H4", "H5", "H6",
    "HR", "LI", "OL", "P", "PRE", "TABLE", "TD", "TH", "TR", "UL",
  ]);

  function textIndex(root) {
    let text = "";
    const segments = [];
    const separator = () => {
      if (text && !/\s$/u.test(text)) text += "\n";
    };
    function visit(node) {
      if (node.nodeType === Node.TEXT_NODE) {
        const start = text.length;
        text += node.textContent;
        if (text.length > start) segments.push({ node, start, end: text.length });
      } else if (node.nodeType === Node.ELEMENT_NODE) {
        if (node.tagName === "BR") {
          separator();
          return;
        }
        const isBlock = blockTags.has(node.tagName);
        if (isBlock) separator();
        node.childNodes.forEach(visit);
        if (isBlock) separator();
      }
    }
    visit(root);
    return { text, segments };
  }

  function literalPattern(query) {
    const parts = query.trim().split(/\s+/u);
    return parts.map(part => part.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("\\s+");
  }

  window.ArchiveChatFind = function ({ root, input, previous, next, clear, status }) {
    let matches = [];
    let active = -1;

    function syncControls() {
      previous.disabled = next.disabled = matches.length === 0;
      clear.disabled = input.value.length === 0;
      status.textContent = !input.value.trim() ? "" : matches.length
        ? `${active + 1} of ${matches.length}` : "No matches";
    }

    function navigate(index, scroll = true) {
      if (!matches.length) {
        syncControls();
        return;
      }
      if (active >= 0) matches[active].forEach(mark => mark.classList.remove("is-current"));
      active = (index + matches.length) % matches.length;
      matches[active].forEach(mark => mark.classList.add("is-current"));
      syncControls();
      if (scroll) {
        matches[active][0].scrollIntoView({ block: "center", inline: "nearest", behavior: "instant" });
      }
    }

    function removeHighlights() {
      root.querySelectorAll("mark.chat-find-match").forEach(mark => {
        mark.replaceWith(document.createTextNode(mark.textContent));
      });
      root.querySelectorAll(".message-content").forEach(content => content.normalize());
      matches = [];
      active = -1;
    }

    function refresh({ scroll = false, preserveActive = false } = {}) {
      const previousActive = active;
      removeHighlights();
      if (!input.value.trim()) {
        syncControls();
        return;
      }
      const pattern = literalPattern(input.value);
      root.querySelectorAll(".message-content").forEach(content => {
        const { text, segments } = textIndex(content);
        const piecesByNode = new Map();
        let segmentIndex = 0;
        for (const hit of text.matchAll(new RegExp(pattern, "giu"))) {
          const start = hit.index;
          const end = start + hit[0].length;
          const marks = [];
          while (segmentIndex < segments.length && segments[segmentIndex].end <= start) segmentIndex++;
          for (let i = segmentIndex; i < segments.length && segments[i].start < end; i++) {
            const segment = segments[i];
            const from = Math.max(start, segment.start) - segment.start;
            const through = Math.min(end, segment.end) - segment.start;
            if (through <= from) continue;
            const pieces = piecesByNode.get(segment.node) || [];
            pieces.push({ from, through, marks });
            piecesByNode.set(segment.node, pieces);
          }
          matches.push(marks);
        }
        for (const [node, pieces] of piecesByNode) {
          const fragment = document.createDocumentFragment();
          let cursor = 0;
          for (const piece of pieces) {
            fragment.appendChild(document.createTextNode(node.textContent.slice(cursor, piece.from)));
            const mark = document.createElement("mark");
            mark.className = "chat-find-match";
            mark.textContent = node.textContent.slice(piece.from, piece.through);
            fragment.appendChild(mark);
            piece.marks.push(mark);
            cursor = piece.through;
          }
          fragment.appendChild(document.createTextNode(node.textContent.slice(cursor)));
          node.replaceWith(fragment);
        }
      });
      matches = matches.filter(marks => marks.length);
      navigate(preserveActive && previousActive >= 0 ? Math.min(previousActive, matches.length - 1) : 0, scroll);
    }

    input.addEventListener("input", () => refresh({ scroll: true }));
    input.addEventListener("keydown", event => {
      if (event.isComposing) return;
      if (event.key === "Enter") {
        event.preventDefault();
        navigate(active + (event.shiftKey ? -1 : 1));
      } else if (event.key === "Escape") {
        event.preventDefault();
        event.stopPropagation();
        input.value = "";
        refresh();
      }
    });
    previous.addEventListener("click", () => navigate(active - 1));
    next.addEventListener("click", () => navigate(active + 1));
    clear.addEventListener("click", () => {
      input.value = "";
      refresh();
      input.focus();
    });
    syncControls();
    return { refresh };
  };
})();
