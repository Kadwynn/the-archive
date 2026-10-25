// Uses the existing Playwright runtime and an installed Chrome; no packages or browsers are installed.
const assert = require("node:assert/strict");
const path = require("node:path");
const { test, before, after, beforeEach, afterEach } = require("node:test");
const { chromium } = require("playwright");

const staticRoot = path.join(__dirname, "..", "chat_export_viewer", "static");
let browser;
let page;
let requests;

before(async () => {
  browser = await chromium.launch({ channel: process.env.CEV_BROWSER_CHANNEL || "chrome", headless: true });
});
after(async () => { await browser?.close(); });
beforeEach(async () => {
  page = await browser.newPage();
  requests = [];
  page.on("request", (request) => requests.push(request.url()));
  await page.setContent('<div id="content" class="message-content"></div>');
  for (const script of ["vendor/marked.umd.js", "vendor/purify.min.js", "markdown.js"]) {
    await page.addScriptTag({ path: path.join(staticRoot, script) });
  }
});
afterEach(async () => { await page?.close(); });

test("renders common Markdown and accessible, scrollable tables", async () => {
  const result = await page.evaluate(() => {
    const content = document.getElementById("content");
    const source = '# A heading\n\n**Bold** and *italic* with `code`.\n\n- First\n- Second\n\n> A quote\n\n```js\nconst answer = 42;\n```\n\n| Name | Value |\n| --- | ---: |\n| Answer | 42 |';
    return {
      formatted: ArchiveMarkdown.renderInto(content, source),
      headings: content.querySelectorAll("h1").length,
      strong: content.querySelector("strong").textContent,
      emphasis: content.querySelector("em").textContent,
      items: content.querySelectorAll("li").length,
      quote: content.querySelector("blockquote").textContent.trim(),
      code: content.querySelector("pre code").textContent,
      tableRegion: content.querySelector(".md-table").getAttribute("aria-label"),
      cells: content.querySelectorAll("td").length,
      formattedClass: content.classList.contains("is-formatted"),
    };
  });
  assert.deepEqual(result, { formatted: true, headings: 1, strong: "Bold", emphasis: "italic", items: 2, quote: "A quote", code: "const answer = 42;\n", tableRegion: "Scrollable table", cells: 2, formattedClass: true });
});

test("keeps conversational single line breaks and Unicode text", async () => {
  const result = await page.evaluate(() => {
    const content = document.getElementById("content");
    ArchiveMarkdown.renderInto(content, 'Syojatar 🖤\nSecond line\n\nA separate paragraph.');
    return { breaks: content.querySelectorAll("br").length, paragraphs: content.querySelectorAll("p").length, text: content.textContent };
  });
  assert.equal(result.breaks, 1);
  assert.equal(result.paragraphs, 2);
  assert.match(result.text, /Syojatar 🖤/);
});

test("allows explicit web/email links and rejects executable/local destinations", async () => {
  const links = await page.evaluate(() => {
    const content = document.getElementById("content");
    ArchiveMarkdown.renderInto(content, '[web](https://example.com/path?q=1&x=2) [email](mailto:reader@example.com) [bad](javascript:alert%281%29) [data](data:text/html,evil) [file](file:///C:/private) [relative](/api/import-path) [encoded](javascript&#58;alert%281%29)');
    return Array.from(content.querySelectorAll("a")).map((link) => ({ href: link.getAttribute("href"), rel: link.rel, target: link.target }));
  });
  assert.deepEqual(links, [
    { href: "https://example.com/path?q=1&x=2", rel: "noopener noreferrer", target: "_blank" },
    { href: "mailto:reader@example.com", rel: "noopener noreferrer", target: "_blank" },
  ]);
  assert.deepEqual(requests, []);
});

test("turns image Markdown into links without loading remote media", async () => {
  const result = await page.evaluate(() => {
    const content = document.getElementById("content");
    ArchiveMarkdown.renderInto(content, '![A lighthouse](https://example.com/remote.png) ![Unsafe](data:image/svg+xml,evil)');
    return { images: content.querySelectorAll("img").length, links: content.querySelectorAll("a").length, text: content.textContent };
  });
  assert.equal(result.images, 0);
  assert.equal(result.links, 1);
  assert.match(result.text, /\[Image: A lighthouse\]/);
  assert.match(result.text, /\[Image: Unsafe\]/);
  assert.deepEqual(requests, []);
});

test("renders archived HTML as text and cannot execute events, scripts, or resource loads", async () => {
  const result = await page.evaluate(() => {
    const content = document.getElementById("content");
    ArchiveMarkdown.renderInto(content, '<img src="https://example.com/pixel" onerror="window.archiveAttack = true">\n\n<script>window.archiveAttack = true</script>\n\n<iframe src="https://example.com"></iframe>');
    return { activeElements: content.querySelectorAll("img,script,iframe,svg,style,form").length, ran: window.archiveAttack === true, text: content.textContent };
  });
  assert.equal(result.activeElements, 0);
  assert.equal(result.ran, false);
  assert.match(result.text, /<img src=/);
  assert.deepEqual(requests, []);
});

test("task lists stay visible without interactive form controls", async () => {
  const result = await page.evaluate(() => {
    const content = document.getElementById("content");
    ArchiveMarkdown.renderInto(content, '- [x] Finished\n- [ ] Pending');
    return { controls: content.querySelectorAll("input").length, text: content.textContent };
  });
  assert.equal(result.controls, 0);
  assert.match(result.text, /☑ Finished/);
  assert.match(result.text, /☐ Pending/);
});

test("sanitizes generated HTML before inserting it into the reader", async () => {
  const result = await page.evaluate(() => {
    const content = document.getElementById("content");
    // Exercise the sanitizer independently of the parser's escaping protections.
    window.marked = { Marked: class {
      parse() {
        return '<p id="messages" style="position:fixed" onclick="window.archiveAttack=true">Kept text</p><img src="https://example.com/pixel"><script>window.archiveAttack=true</script><svg onload="window.archiveAttack=true"></svg><a href="javascript:alert(1)">Unsafe link</a>';
      }
    } };
    ArchiveMarkdown.renderInto(content, "source");
    return {
      activeElements: content.querySelectorAll("script,img,svg,iframe").length,
      unsafeAttributes: content.querySelectorAll("[onclick],[onload],[style],[id]").length,
      unsafeLinks: content.querySelectorAll("a").length,
      text: content.textContent,
      ran: window.archiveAttack === true,
    };
  });
  assert.deepEqual(result, { activeElements: 0, unsafeAttributes: 0, unsafeLinks: 0, text: "Kept textUnsafe link", ran: false });
  assert.deepEqual(requests, []);
});

test("falls back to exact text if the sanitizer is unavailable", async () => {
  const result = await page.evaluate(() => {
    const content = document.getElementById("content");
    window.DOMPurify = undefined;
    const source = '**Original**\n<script>not executed</script>';
    return { formatted: ArchiveMarkdown.renderInto(content, source), text: content.textContent, elements: content.children.length, formattedClass: content.classList.contains("is-formatted") };
  });
  assert.deepEqual(result, { formatted: false, text: '**Original**\n<script>not executed</script>', elements: 0, formattedClass: false });
});
