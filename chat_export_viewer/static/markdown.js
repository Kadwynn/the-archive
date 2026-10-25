/* Offline Markdown presentation. The archive's source text is never changed. */
(() => {
  "use strict";

  const escapeHtml = (value) => String(value ?? "").replace(/[&<>"']/g, (character) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[character]));

  function safeLink(value) {
    try {
      // Absolute web/email links only. Relative paths could accidentally target the local app.
      const url = new URL(String(value ?? "").trim());
      return ["https:", "http:", "mailto:"].includes(url.protocol) ? url.href : null;
    } catch {
      return null;
    }
  }

  let parser;
  function getParser() {
    if (!parser && window.marked?.Marked) {
      parser = new window.marked.Marked({
        gfm: true,
        breaks: true,
        renderer: {
          // Treat archived HTML as text, including tags that would load external resources.
          html({ text }) {
            return escapeHtml(text);
          },
          link({ href, title, tokens }) {
            const label = this.parser.parseInline(tokens);
            const destination = safeLink(href);
            return destination
              ? `<a href="${escapeHtml(destination)}"${title ? ` title="${escapeHtml(title)}"` : ""}>${label}</a>`
              : label;
          },
          // Reading an archive must not silently fetch remote images or tracking pixels.
          image({ href, text }) {
            const label = `[Image: ${escapeHtml(text || "open image")}]`;
            const destination = safeLink(href);
            return destination ? `<a href="${escapeHtml(destination)}">${label}</a>` : label;
          },
          checkbox({ checked }) {
            return checked ? "☑ " : "☐ ";
          },
        },
      });
    }
    return parser;
  }

  function renderInto(element, text) {
    const source = String(text ?? "");
    element.classList.remove("is-formatted");
    try {
      const markdown = getParser();
      if (!markdown || !window.DOMPurify?.isSupported) {
        element.textContent = source;
        return false;
      }

      const fragment = window.DOMPurify.sanitize(markdown.parse(source), {
        ALLOWED_TAGS: [
          "p", "br", "strong", "em", "del", "s", "code", "pre", "blockquote",
          "ul", "ol", "li", "h1", "h2", "h3", "h4", "h5", "h6", "hr",
          "table", "thead", "tbody", "tr", "th", "td", "a",
        ],
        ALLOWED_ATTR: ["href", "title", "class", "align", "start"],
        ALLOWED_URI_REGEXP: /^(?:https?:|mailto:)/i,
        ALLOW_DATA_ATTR: false,
        ALLOW_ARIA_ATTR: false,
        SANITIZE_DOM: true,
        SANITIZE_NAMED_PROPS: true,
        RETURN_DOM_FRAGMENT: true,
      });

      fragment.querySelectorAll("a").forEach((link) => {
        const destination = safeLink(link.getAttribute("href"));
        if (!destination) {
          link.replaceWith(...link.childNodes);
          return;
        }
        link.setAttribute("href", destination);
        link.setAttribute("target", "_blank");
        link.setAttribute("rel", "noopener noreferrer");
      });
      fragment.querySelectorAll("table").forEach((table) => {
        const wrapper = document.createElement("div");
        wrapper.className = "md-table";
        wrapper.tabIndex = 0;
        wrapper.setAttribute("role", "region");
        wrapper.setAttribute("aria-label", "Scrollable table");
        table.replaceWith(wrapper);
        wrapper.appendChild(table);
      });

      element.replaceChildren(fragment);
      element.classList.add("is-formatted");
      return true;
    } catch (error) {
      // A damaged message or unavailable library should never prevent reading the chat.
      console.warn("Markdown display unavailable; showing original text.", error);
      element.textContent = source;
      return false;
    }
  }

  window.ArchiveMarkdown = Object.freeze({ renderInto });
})();
