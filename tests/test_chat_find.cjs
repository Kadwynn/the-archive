/* Run with a local Playwright installation: node --test tests/test_chat_find.cjs */
const assert = require('node:assert/strict');
const { after, before, beforeEach, afterEach, test } = require('node:test');
const path = require('node:path');
const { chromium } = require('playwright');

let browser;
let page;

before(async () => {
  const channel = process.env.TEST_BROWSER_CHANNEL || 'chrome';
  browser = await chromium.launch({ headless: true, ...(channel === 'chromium' ? {} : { channel }) });
});

after(async () => { await browser?.close(); });

beforeEach(async () => {
  page = await browser.newPage();
  await page.setContent(`
    <input id="query"><button id="previous">Previous</button><button id="next">Next</button>
    <button id="clear">Clear</button><output id="status"></output>
    <section id="messages">
      <article>
        <header>Needle metadata</header>
        <div class="message-content"><p>Needle <strong>red</strong> thread. A needle follows.</p><p>A <em>silver</em> moon.</p><p>Line one<br>line two.</p><pre><code>literal [x].* + C++</code></pre><p>Emoji 😀Needle and İSTANBUL.</p></div>
        <details open><summary>Raw message JSON</summary><pre>{"needle":"needle","hiddenOnly":"hiddenOnly"}</pre></details>
      </article>
      <article><div class="message-content">One more NEEDLE</div><div class="message-meta">Needle metadata</div></article>
    </section>
  `);
  await page.addScriptTag({ path: path.resolve(__dirname, '../chat_export_viewer/static/chat-find.js') });
  await page.evaluate(() => {
    window.finder = window.ArchiveChatFind({
      root: document.querySelector('#messages'), input: document.querySelector('#query'),
      previous: document.querySelector('#previous'), next: document.querySelector('#next'),
      clear: document.querySelector('#clear'), status: document.querySelector('#status'),
    });
  });
});

afterEach(async () => { await page?.close(); });

test('counts only message bodies, excluding expanded raw JSON and metadata', async () => {
  await page.locator('#query').fill('needle');
  assert.equal(await page.locator('#status').textContent(), '1 of 4');
  assert.equal(await page.locator('.chat-find-match').count(), 4);
  assert.equal(await page.locator('details .chat-find-match, header .chat-find-match, .message-meta .chat-find-match').count(), 0);
  await page.locator('#query').fill('hiddenOnly');
  assert.equal(await page.locator('#status').textContent(), 'No matches');
});

test('phrases spanning inline emphasis count once and retain formatting', async () => {
  const beforeText = await page.locator('.message-content').first().textContent();
  await page.locator('#query').fill('silver moon');
  assert.equal(await page.locator('#status').textContent(), '1 of 1');
  assert.equal(await page.locator('.chat-find-match').count(), 2);
  assert.equal(await page.locator('em .chat-find-match').textContent(), 'silver');
  assert.equal(await page.locator('.message-content').first().textContent(), beforeText);
  await page.locator('#query').fill('red thread');
  assert.equal(await page.locator('#status').textContent(), '1 of 1');
  assert.equal(await page.locator('strong .chat-find-match').textContent(), 'red');
});

test('matches phrases across line breaks and flexible query whitespace', async () => {
  await page.locator('#query').fill('line   one line two');
  assert.equal(await page.locator('#status').textContent(), '1 of 1');
  assert.equal(await page.locator('.chat-find-match').count(), 2);
  assert.equal(await page.locator('br').count(), 1);
});

test('regex punctuation is searched literally, including code blocks', async () => {
  await page.locator('#query').fill('[x].*');
  assert.equal(await page.locator('#status').textContent(), '1 of 1');
  assert.equal(await page.locator('code mark').textContent(), '[x].*');
  await page.locator('#query').fill('C++');
  assert.equal(await page.locator('#status').textContent(), '1 of 1');
  assert.equal(await page.locator('code mark').textContent(), 'C++');
});

test('next/previous and Enter/Shift+Enter wrap through logical matches', async () => {
  await page.locator('#query').fill('needle');
  await page.locator('#previous').click();
  assert.equal(await page.locator('#status').textContent(), '4 of 4');
  await page.locator('#next').click();
  assert.equal(await page.locator('#status').textContent(), '1 of 4');
  await page.locator('#query').press('Enter');
  assert.equal(await page.locator('#status').textContent(), '2 of 4');
  await page.locator('#query').press('Shift+Enter');
  assert.equal(await page.locator('#status').textContent(), '1 of 4');
  assert.equal(await page.locator('mark.is-current').count(), 1);
});

test('clearing and Escape restore content without accumulating marks', async () => {
  const before = await page.locator('.message-content').first().innerHTML();
  await page.locator('#query').fill('needle');
  await page.locator('#query').fill('red thread');
  await page.locator('#clear').click();
  assert.equal(await page.locator('.message-content').first().innerHTML(), before);
  assert.equal(await page.locator('.chat-find-match').count(), 0);
  assert.equal(await page.locator('#previous').isDisabled(), true);
  assert.equal(await page.locator('#next').isDisabled(), true);
  assert.equal(await page.locator('#clear').isDisabled(), true);
  assert.equal(await page.locator('#query').evaluate(el => el === document.activeElement), true);
  await page.locator('#query').fill('needle');
  await page.locator('#query').press('Escape');
  assert.equal(await page.locator('#query').inputValue(), '');
  assert.equal(await page.locator('.chat-find-match').count(), 0);
});

test('missing and whitespace-only queries disable navigation', async () => {
  await page.locator('#query').fill('not present');
  assert.equal(await page.locator('#status').textContent(), 'No matches');
  assert.equal(await page.locator('#next').isDisabled(), true);
  await page.locator('#query').fill('   ');
  assert.equal(await page.locator('#status').textContent(), '');
  assert.equal(await page.locator('.chat-find-match').count(), 0);
});

test('Unicode match offsets preserve surrounding emoji and Turkish dotted I', async () => {
  await page.locator('#query').fill('😀needle');
  assert.equal(await page.locator('#status').textContent(), '1 of 1');
  assert.equal(await page.locator('.chat-find-match').textContent(), '😀Needle');
  await page.locator('#query').fill('İSTANBUL');
  assert.equal(await page.locator('#status').textContent(), '1 of 1');
  assert.equal(await page.locator('.chat-find-match').textContent(), 'İSTANBUL');
});

test('refresh reindexes replaced message content and preserves active match when possible', async () => {
  await page.locator('#query').fill('needle');
  await page.locator('#next').click();
  await page.evaluate(() => {
    document.querySelector('#messages').innerHTML = '<div class="message-content"><strong>needle</strong> needle needle</div>';
    finder.refresh({ preserveActive: true });
  });
  assert.equal(await page.locator('#status').textContent(), '2 of 3');
  assert.equal(await page.locator('.chat-find-match').count(), 3);
  assert.equal(await page.locator('strong').textContent(), 'needle');
});
