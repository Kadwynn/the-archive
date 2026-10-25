const MAX_TAGS = 30;
const MAX_TAG_LENGTH = 80;

const state = {
  query: "",
  mode: "normal",
  limit: 40,
  offset: 0,
  total: 0,
  selectedConversationId: null,
  pathMode: "current",
  loadingList: false,
  pendingListReset: false,
  pendingListResolvers: [],
  listCriteriaVersion: 0,
  listReady: false,
  listErrorVisible: false,
  activityFrom: "",
  activityThrough: "",
  detailRequestVersion: 0,
  selectedTags: [],
  tagEditorOpen: false,
  tagEditorConversationId: null,
  tagSaving: false,
  tagSaveRequestVersion: 0,
  readingFormat: "formatted",
};

const messageSources = new WeakMap();
window.ArchiveHasUnsavedChanges = () => state.tagSaving || tagEditorHasUnsavedChanges();

const elements = {
  stats: document.getElementById("stats"),
  searchForm: document.getElementById("search-form"),
  searchInput: document.getElementById("search-input"),
  searchMode: document.getElementById("search-mode"),
  activityFrom: document.getElementById("activity-from"),
  activityThrough: document.getElementById("activity-through"),
  clearActivityDates: document.getElementById("clear-activity-dates"),
  listMeta: document.getElementById("list-meta"),
  conversationList: document.getElementById("conversation-list"),
  loadMore: document.getElementById("load-more"),
  detailEmpty: document.getElementById("detail-empty"),
  detailView: document.getElementById("detail-view"),
  detailTitle: document.getElementById("detail-title"),
  detailMeta: document.getElementById("detail-meta"),
  detailTags: document.getElementById("detail-tags"),
  editTags: document.getElementById("edit-tags"),
  tagEditor: document.getElementById("tag-editor"),
  tagInput: document.getElementById("tag-input"),
  saveTags: document.getElementById("save-tags"),
  cancelTags: document.getElementById("cancel-tags"),
  tagStatus: document.getElementById("tag-status"),
  messages: document.getElementById("messages"),
  rawConversation: document.getElementById("raw-conversation"),
  pathMode: document.getElementById("path-mode"),
  exportConversation: document.getElementById("export-conversation"),
  exportChatOnly: document.getElementById("export-chat-only"),
  exportChatSplit: document.getElementById("export-chat-split"),
  importStatus: document.getElementById("import-status"),
  importPathForm: document.getElementById("import-path-form"),
  importPathInput: document.getElementById("import-path"),
  importFileForm: document.getElementById("import-file-form"),
  importFileInput: document.getElementById("import-file"),
  themeToggle: document.getElementById("theme-toggle"),
  themeLabel: document.getElementById("theme-label"),
  themeIcon: document.querySelector(".theme-icon"),
  readingFormat: document.getElementById("reading-format"),
  chatFindInput: document.getElementById("chat-find-input"),
  importDrawer: document.getElementById("import-drawer"),
};

const chatFind = new window.ArchiveChatFind({
  root: elements.messages,
  input: elements.chatFindInput,
  previous: document.getElementById("chat-find-previous"),
  next: document.getElementById("chat-find-next"),
  clear: document.getElementById("chat-find-clear"),
  status: document.getElementById("chat-find-status"),
});

function renderMessageBody(content, source) {
  content.classList.remove("is-formatted");
  if (state.readingFormat === "formatted" && ["user", "assistant"].includes(source.role)) {
    window.ArchiveMarkdown.renderInto(content, source.text);
  } else {
    content.textContent = source.text;
  }
}

function formatTimestamp(unixSeconds) {
  if (typeof unixSeconds !== "number") {
    return "n/a";
  }
  const date = new Date(unixSeconds * 1000);
  return date.toLocaleString();
}

function setStatus(message, isError = false) {
  elements.importStatus.textContent = message || "";
  elements.importStatus.classList.toggle("is-error", isError);
  state.listErrorVisible = false;
}

function setListError(message) {
  setStatus(message, true);
  state.listErrorVisible = true;
}

function clearListError() {
  if (state.listErrorVisible) {
    setStatus("");
  }
}

function syncLoadMoreControl() {
  elements.loadMore.disabled = state.loadingList
    || !state.listReady
    || state.offset + state.limit >= state.total;
}

function applyListCriteria({
  query = state.query,
  mode = state.mode,
  activityFrom = state.activityFrom,
  activityThrough = state.activityThrough,
} = {}) {
  const changed = query !== state.query
    || mode !== state.mode
    || activityFrom !== state.activityFrom
    || activityThrough !== state.activityThrough;
  state.query = query;
  state.mode = mode;
  state.activityFrom = activityFrom;
  state.activityThrough = activityThrough;
  if (changed) {
    state.listCriteriaVersion += 1;
    state.listReady = false;
    syncLoadMoreControl();
  }
  return state.listCriteriaVersion;
}

function applyTheme(theme) {
  if (theme === "dark") {
    document.documentElement.setAttribute("data-theme", "dark");
    elements.themeLabel.textContent = "Light";
    elements.themeIcon.textContent = "☼";
    elements.themeToggle.setAttribute("aria-label", "Switch to light theme");
    elements.themeToggle.setAttribute("aria-pressed", "true");
  } else {
    document.documentElement.removeAttribute("data-theme");
    elements.themeLabel.textContent = "Dark";
    elements.themeIcon.textContent = "◐";
    elements.themeToggle.setAttribute("aria-label", "Switch to dark theme");
    elements.themeToggle.setAttribute("aria-pressed", "false");
  }
}

function initializeTheme() {
  const savedTheme = localStorage.getItem("cev_theme");
  if (savedTheme === "dark" || savedTheme === "light") {
    applyTheme(savedTheme);
    return;
  }
  const prefersDark = window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches;
  applyTheme(prefersDark ? "dark" : "light");
}

async function fetchJson(url, options = {}) {
  const response = await fetch(url, options);
  const payload = await response.json();
  if (!response.ok) {
    const error = payload && payload.error ? payload.error : `Request failed (${response.status})`;
    const requestError = new Error(error);
    requestError.status = response.status;
    throw requestError;
  }
  return payload;
}

function makeChip(label) {
  const chip = document.createElement("span");
  chip.className = "chip";
  chip.textContent = label;
  return chip;
}

function coerceTags(value) {
  if (!Array.isArray(value)) {
    return [];
  }
  return value
    .filter((tag) => typeof tag === "string" && tag.trim())
    .map((tag) => tag.trim());
}

function normalizeTagInput(value) {
  const tags = [];
  const seen = new Set();
  const candidates = String(value || "").normalize("NFKC").split(/[,\r\n]+/);

  for (const candidate of candidates) {
    const tag = candidate.trim().replace(/\s+/g, " ");
    if (!tag) {
      continue;
    }
    if ([...tag].length > MAX_TAG_LENGTH) {
      return {
        error: `Each tag can be at most ${MAX_TAG_LENGTH} characters.`,
        tags: [],
      };
    }
    const key = tag.toLocaleLowerCase();
    if (!seen.has(key)) {
      seen.add(key);
      tags.push(tag);
    }
  }

  if (tags.length > MAX_TAGS) {
    return {
      error: `A conversation can have at most ${MAX_TAGS} tags.`,
      tags: [],
    };
  }

  return { error: "", tags };
}

function setTagStatus(message, isError = false) {
  elements.tagStatus.classList.toggle("is-error", isError);
  elements.tagStatus.setAttribute("role", isError ? "alert" : "status");
  elements.tagStatus.setAttribute("aria-live", isError ? "assertive" : "polite");
  elements.tagStatus.textContent = message || "";
}

function setTagEditorBusy(isBusy) {
  state.tagSaving = isBusy;
  elements.tagEditor.setAttribute("aria-busy", isBusy ? "true" : "false");
  elements.tagInput.disabled = isBusy;
  elements.saveTags.disabled = isBusy;
  elements.cancelTags.disabled = isBusy;
  elements.editTags.disabled = isBusy;
}

function closeTagEditor({ returnFocus = false, preserveStatus = false } = {}) {
  state.tagEditorOpen = false;
  state.tagEditorConversationId = null;
  elements.tagEditor.classList.add("hidden");
  elements.editTags.setAttribute("aria-expanded", "false");
  elements.tagInput.removeAttribute("aria-invalid");
  if (!preserveStatus) {
    setTagStatus("");
  }
  if (returnFocus) {
    elements.editTags.focus();
  }
}

function openTagEditor() {
  if (state.tagSaving || !state.selectedConversationId) {
    return;
  }
  state.tagEditorOpen = true;
  state.tagEditorConversationId = state.selectedConversationId;
  elements.tagInput.value = state.selectedTags.length
    ? `${state.selectedTags.join(", ")}, `
    : "";
  elements.tagInput.removeAttribute("aria-invalid");
  elements.tagEditor.classList.remove("hidden");
  elements.editTags.setAttribute("aria-expanded", "true");
  setTagStatus("");
  elements.tagInput.focus();
  elements.tagInput.setSelectionRange(elements.tagInput.value.length, elements.tagInput.value.length);
}

async function searchByTag(tag) {
  elements.searchInput.value = tag;
  elements.searchMode.value = "tag";
  const criteriaVersion = applyListCriteria({ query: tag, mode: "tag" });
  const loaded = await loadConversations({ reset: true });
  if (criteriaVersion !== state.listCriteriaVersion) {
    return;
  }
  if (!loaded) {
    setTagStatus("Could not refresh the tagged conversations.", true);
    return;
  }
  const noun = state.total === 1 ? "conversation" : "conversations";
  setTagStatus(`Showing ${state.total.toLocaleString()} ${noun} tagged “${tag}”.`);
  if (window.matchMedia("(max-width: 900px)").matches) {
    elements.searchInput.focus({ preventScroll: true });
    elements.searchForm.scrollIntoView({ behavior: "smooth", block: "start" });
  }
}

function tagEditorHasUnsavedChanges() {
  if (!state.tagEditorOpen) {
    return false;
  }
  const current = normalizeTagInput(elements.tagInput.value);
  return Boolean(current.error) || JSON.stringify(current.tags) !== JSON.stringify(state.selectedTags);
}

function canLeaveTagEditor() {
  if (state.tagSaving) {
    setTagStatus("Please wait for the tags to finish saving.");
    return false;
  }
  if (!tagEditorHasUnsavedChanges()) {
    return true;
  }
  return window.confirm("Discard your unsaved tag changes?");
}

function renderDetailTags(value) {
  const tags = coerceTags(value);
  state.selectedTags = tags;
  clearChildren(elements.detailTags);

  if (tags.length === 0) {
    const empty = document.createElement("span");
    empty.className = "tag-empty";
    empty.textContent = "No tags yet.";
    elements.detailTags.appendChild(empty);
    elements.editTags.textContent = "Add tags";
    return;
  }

  tags.forEach((tag) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "tag-pill";
    button.textContent = tag;
    button.setAttribute("aria-label", `Show conversations tagged ${tag}`);
    button.addEventListener("click", () => searchByTag(tag));
    elements.detailTags.appendChild(button);
  });
  elements.editTags.textContent = "Edit tags";
}

function clearChildren(node) {
  while (node.firstChild) {
    node.removeChild(node.firstChild);
  }
}

function appendHighlightedText(node, value) {
  const parts = String(value || "").split(/(<mark>|<\/mark>)/i);
  let activeMark = null;
  parts.forEach((part) => {
    if (part.toLowerCase() === "<mark>") {
      activeMark = document.createElement("mark");
      node.appendChild(activeMark);
    } else if (part.toLowerCase() === "</mark>") {
      activeMark = null;
    } else if (part) {
      (activeMark || node).appendChild(document.createTextNode(part));
    }
  });
}

function titleCase(value) {
  const text = String(value || "unknown");
  return text.charAt(0).toUpperCase() + text.slice(1);
}

function makeStat(value, label) {
  const item = document.createElement("span");
  item.className = "stats-item";
  const valueNode = document.createElement("span");
  valueNode.className = "stats-value";
  valueNode.textContent = value;
  const labelNode = document.createElement("span");
  labelNode.className = "stats-label";
  labelNode.textContent = label;
  item.appendChild(valueNode);
  item.appendChild(labelNode);
  return item;
}

function describeImport(payload) {
  if (payload.status === "skipped" && payload.reason === "duplicate_export") {
    return "That file is already in the archive. Nothing was duplicated.";
  }
  if (payload.status === "imported") {
    const total = payload.conversation_count || 0;
    const added = payload.new_conversation_count || 0;
    const updated = payload.updated_conversation_count || 0;
    const noun = total === 1 ? "conversation" : "conversations";
    if (total === 1 && added === 0 && updated === 0) {
      return "The conversation was already up to date.";
    }
    return `Imported ${total.toLocaleString()} ${noun} — ${added.toLocaleString()} new, ${updated.toLocaleString()} updated.`;
  }
  return "Import finished.";
}

function localDateFromValue(value, dayOffset = 0) {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value);
  if (!match) {
    return null;
  }
  const result = new Date(Number(match[1]), Number(match[2]) - 1, Number(match[3]) + dayOffset);
  return Number.isNaN(result.getTime()) ? null : result;
}

function activityDateBounds(fromValue, throughValue = "") {
  const start = localDateFromValue(fromValue);
  const inclusiveEnd = localDateFromValue(throughValue || fromValue);
  const end = localDateFromValue(throughValue || fromValue, 1);
  if (!start || !inclusiveEnd || !end || inclusiveEnd < start) {
    return null;
  }
  return {
    start: start.getTime() / 1000,
    end: end.getTime() / 1000,
  };
}

function formatActivityDate(value) {
  const date = localDateFromValue(value);
  return date ? date.toLocaleDateString() : value;
}

function syncActivityFilterControls() {
  const hasFrom = Boolean(elements.activityFrom.value);
  elements.activityThrough.disabled = !hasFrom;
  elements.activityThrough.min = elements.activityFrom.value || "";
  elements.clearActivityDates.disabled = !hasFrom;
}

function createConversationCard(item) {
  const card = document.createElement("button");
  const tags = coerceTags(item.tags);
  card.type = "button";
  card.className = "conversation-card";
  if (item.id === state.selectedConversationId) {
    card.classList.add("active");
    card.setAttribute("aria-current", "true");
  }
  card.dataset.id = item.id;
  const cardTitle = item.title || "Untitled conversation";

  const title = document.createElement("span");
  title.className = "conversation-title";
  appendHighlightedText(title, item.title_highlight || item.title || "(untitled conversation)");

  const sub = document.createElement("span");
  sub.className = "conversation-sub";
  const activityTime = typeof item.activity_time === "number" ? item.activity_time : null;
  const leadingTime = activityTime === null
    ? `updated ${formatTimestamp(item.update_time)}`
    : `activity ${formatTimestamp(activityTime)}`;
  const subItems = [leadingTime, `messages ${item.message_count || 0}`];
  if (typeof item.activity_message_count === "number") {
    const noun = item.activity_message_count === 1 ? "message" : "messages";
    const rangeLabel = state.activityThrough && state.activityThrough !== state.activityFrom
      ? "in range"
      : "on date";
    subItems.push(`${item.activity_message_count} ${noun} ${rangeLabel}`);
  }
  if (item.default_model_slug) {
    subItems.push(item.default_model_slug);
  }
  card.dataset.baseAriaLabel = [cardTitle, ...subItems].join(". ");
  setConversationCardAccessibleLabel(card, tags);
  subItems.forEach((part) => {
    const span = document.createElement("span");
    span.textContent = part;
    sub.appendChild(span);
  });

  card.appendChild(title);
  card.appendChild(sub);

  if (tags.length > 0) {
    const tagRow = document.createElement("span");
    tagRow.className = "conversation-card-tags";
    tagRow.setAttribute("aria-hidden", "true");
    tags.slice(0, 3).forEach((tag) => {
      const tagPill = document.createElement("span");
      tagPill.className = "conversation-card-tag";
      tagPill.textContent = tag;
      tagRow.appendChild(tagPill);
    });
    if (tags.length > 3) {
      const overflow = document.createElement("span");
      overflow.className = "conversation-card-tag conversation-card-tag-more";
      overflow.textContent = `+${tags.length - 3}`;
      tagRow.appendChild(overflow);
    }
    card.appendChild(tagRow);
  }

  if (item.snippet) {
    const snippet = document.createElement("span");
    snippet.className = "conversation-snippet";
    appendHighlightedText(snippet, item.snippet);
    card.appendChild(snippet);
  }

  card.addEventListener("click", () => {
    if (item.id === state.selectedConversationId) {
      return;
    }
    if (!canLeaveTagEditor()) {
      return;
    }
    closeTagEditor();
    state.selectedConversationId = item.id;
    highlightSelectedConversation();
    loadConversation(item.id);
  });

  return card;
}

function setConversationCardAccessibleLabel(card, tags) {
  const baseLabel = card.dataset.baseAriaLabel || "Conversation";
  const accessibleTags = tags.length > 3
    ? `${tags.slice(0, 3).join(", ")}, and ${tags.length - 3} more`
    : tags.join(", ");
  card.setAttribute(
    "aria-label",
    accessibleTags ? `${baseLabel}. Tags: ${accessibleTags}.` : baseLabel,
  );
}

function updateConversationCardTags(conversationId, value) {
  const tags = coerceTags(value);
  const card = Array.from(document.querySelectorAll(".conversation-card"))
    .find((candidate) => candidate.dataset.id === conversationId);
  if (!card) {
    return;
  }

  const oldTagRow = card.querySelector(".conversation-card-tags");
  if (oldTagRow) {
    oldTagRow.remove();
  }

  if (tags.length > 0) {
    const tagRow = document.createElement("span");
    tagRow.className = "conversation-card-tags";
    tagRow.setAttribute("aria-hidden", "true");
    tags.slice(0, 3).forEach((tag) => {
      const tagPill = document.createElement("span");
      tagPill.className = "conversation-card-tag";
      tagPill.textContent = tag;
      tagRow.appendChild(tagPill);
    });
    if (tags.length > 3) {
      const overflow = document.createElement("span");
      overflow.className = "conversation-card-tag conversation-card-tag-more";
      overflow.textContent = `+${tags.length - 3}`;
      tagRow.appendChild(overflow);
    }
    const snippet = card.querySelector(".conversation-snippet");
    card.insertBefore(tagRow, snippet || null);
  }
  setConversationCardAccessibleLabel(card, tags);
}

async function reloadLoadedConversationsPreservingPosition() {
  const criteriaVersion = state.listCriteriaVersion;
  const targetOffset = state.offset;
  const previousScrollTop = elements.conversationList.scrollTop;
  const firstPageLoaded = await loadConversations({ reset: true });
  if (criteriaVersion !== state.listCriteriaVersion) {
    return false;
  }
  if (!firstPageLoaded) {
    elements.conversationList.scrollTop = previousScrollTop;
    return false;
  }
  for (
    let nextOffset = state.limit;
    nextOffset <= targetOffset && nextOffset < state.total;
    nextOffset += state.limit
  ) {
    if (criteriaVersion !== state.listCriteriaVersion) {
      return false;
    }
    const pageLoaded = await loadConversations({ reset: false, offset: nextOffset });
    if (criteriaVersion !== state.listCriteriaVersion) {
      return false;
    }
    if (!pageLoaded) {
      elements.conversationList.scrollTop = previousScrollTop;
      return false;
    }
  }
  elements.conversationList.scrollTop = previousScrollTop;
  return true;
}

function highlightSelectedConversation() {
  document.querySelectorAll(".conversation-card").forEach((card) => {
    const isActive = card.dataset.id === state.selectedConversationId;
    card.classList.toggle("active", isActive);
    if (isActive) {
      card.setAttribute("aria-current", "true");
    } else {
      card.removeAttribute("aria-current");
    }
  });
}

function renderConversations(items, append = false) {
  if (!append) {
    clearChildren(elements.conversationList);
  }
  if (!append && items.length === 0) {
    const empty = document.createElement("div");
    empty.className = "list-empty";
    const title = document.createElement("strong");
    const hasFilters = Boolean(state.query || state.activityFrom);
    title.textContent = hasFilters ? "Nothing surfaced." : "Your archive is empty.";
    const hint = document.createElement("span");
    hint.textContent = hasFilters
      ? "Try another phrase or loosen the filters."
      : "Choose Add conversations above to import your first ChatGPT JSON file.";
    empty.appendChild(title);
    empty.appendChild(hint);
    elements.conversationList.appendChild(empty);
  }
  items.forEach((item) => {
    elements.conversationList.appendChild(createConversationCard(item));
  });
  syncLoadMoreControl();
  const modeLabels = {
    normal: "best match",
    fuzzy: "typo tolerant",
    title: "titles only",
    tag: "tags only",
  };
  const activeLabels = [];
  if (state.query) {
    activeLabels.push(modeLabels[state.mode] || state.mode);
  }
  if (state.activityFrom) {
    const through = state.activityThrough || state.activityFrom;
    const dateLabel = through === state.activityFrom
      ? formatActivityDate(state.activityFrom)
      : `${formatActivityDate(state.activityFrom)}–${formatActivityDate(through)}`;
    activeLabels.push(dateLabel);
  }
  elements.listMeta.textContent = `${state.total} total (${activeLabels.join(" · ") || "default"})`;
}

async function loadConversations({ reset = false, offset = null } = {}) {
  if (state.loadingList) {
    if (!reset) {
      return false;
    }
    state.pendingListReset = true;
    return new Promise((resolve) => {
      state.pendingListResolvers.push(resolve);
    });
  }
  state.loadingList = true;
  syncLoadMoreControl();
  elements.conversationList.setAttribute("aria-busy", "true");
  let success = false;
  const requestCriteriaVersion = state.listCriteriaVersion;
  const requestOffset = reset ? 0 : (offset ?? state.offset);
  if (reset) {
    state.listReady = false;
  }
  try {
    const params = new URLSearchParams({
      limit: String(state.limit),
      offset: String(requestOffset),
    });
    if (state.query) {
      params.set("q", state.query);
      params.set("mode", state.mode);
    }
    if (state.activityFrom) {
      const bounds = activityDateBounds(state.activityFrom, state.activityThrough);
      if (!bounds) {
        throw new Error("Through date cannot be before From date.");
      }
      params.set("activity_start", String(bounds.start));
      params.set("activity_end", String(bounds.end));
    }

    const payload = await fetchJson(`/api/conversations?${params.toString()}`);
    if (requestCriteriaVersion === state.listCriteriaVersion) {
      clearListError();
      state.offset = requestOffset;
      state.total = payload.total;
      state.listReady = true;
      renderConversations(payload.items, !reset && requestOffset > 0);
      success = true;
    }
  } catch (error) {
    if (reset && requestCriteriaVersion === state.listCriteriaVersion) {
      state.offset = 0;
      state.listReady = false;
    }
    if (requestCriteriaVersion === state.listCriteriaVersion) {
      setListError(error.message);
    }
  } finally {
    state.loadingList = false;
    elements.conversationList.setAttribute("aria-busy", "false");
    syncLoadMoreControl();
  }

  if (state.pendingListReset) {
    state.pendingListReset = false;
    const pendingResolvers = state.pendingListResolvers.splice(0);
    const pendingSuccess = await loadConversations({ reset: true });
    pendingResolvers.forEach((resolve) => resolve(pendingSuccess));
    return pendingSuccess;
  }
  return success;
}

function renderConversationDetail(payload) {
  const conversation = payload.conversation;
  elements.detailEmpty.classList.add("hidden");
  elements.detailView.classList.remove("hidden");

  elements.detailTitle.textContent = conversation.title || "(untitled conversation)";
  const preserveTagEditor = state.tagEditorOpen
    && state.tagEditorConversationId === conversation.id;
  if (!preserveTagEditor) {
    setTagEditorBusy(false);
    closeTagEditor();
    renderDetailTags(conversation.tags || []);
  }
  clearChildren(elements.detailMeta);

  const chips = [
    `id ${conversation.id}`,
    `messages ${conversation.message_count}`,
    `user ${conversation.user_message_count}`,
    `assistant ${conversation.assistant_message_count}`,
    `tool ${conversation.tool_message_count}`,
    `system ${conversation.system_message_count}`,
    `updated ${formatTimestamp(conversation.update_time)}`,
    `first seen ${conversation.first_seen_at}`,
    `last seen ${conversation.last_seen_at}`,
  ];
  if (conversation.default_model_slug) {
    chips.push(`model ${conversation.default_model_slug}`);
  }
  if (conversation.current_node) {
    chips.push(`current node ${conversation.current_node}`);
  }

  chips.forEach((label) => elements.detailMeta.appendChild(makeChip(label)));

  clearChildren(elements.messages);
  payload.messages.forEach((message) => {
    const card = document.createElement("article");
    card.className = "message-card";
    card.dataset.role = message.role || "unknown";

    const head = document.createElement("div");
    head.className = "message-head";

    const roleLockup = document.createElement("div");
    roleLockup.className = "message-role-lockup";
    const roleMark = document.createElement("span");
    roleMark.className = "role-mark";
    roleMark.setAttribute("aria-hidden", "true");

    const role = document.createElement("p");
    role.className = "message-role";
    role.textContent = titleCase(message.role);
    roleLockup.appendChild(roleMark);
    roleLockup.appendChild(role);

    const time = document.createElement("time");
    time.className = "message-time";
    time.textContent = formatTimestamp(message.create_time);
    if (typeof message.create_time === "number") {
      time.dateTime = new Date(message.create_time * 1000).toISOString();
    }

    head.appendChild(roleLockup);
    if (message.role === "assistant" && message.model_slug) {
      const model = document.createElement("p");
      model.className = "message-model";
      model.textContent = message.model_slug;
      model.setAttribute("aria-label", `Model: ${message.model_slug}`);
      head.appendChild(model);
    }
    head.appendChild(time);
    card.appendChild(head);

    const meta = document.createElement("div");
    meta.className = "message-meta";
    const metaBits = [
      `node ${message.node_id}`,
      message.parent_id ? `parent ${message.parent_id}` : null,
      message.content_type ? `content ${message.content_type}` : null,
      message.model_slug ? `model ${message.model_slug}` : null,
      message.recipient ? `recipient ${message.recipient}` : null,
      message.status ? `status ${message.status}` : null,
      message.is_current_path ? "current path" : "branch message",
    ].filter(Boolean);
    metaBits.forEach((label) => meta.appendChild(makeChip(label)));

    const content = document.createElement("div");
    content.className = "message-content";
    const source = { text: message.text_content || "(no text extractable content)", role: message.role };
    messageSources.set(content, source);
    renderMessageBody(content, source);
    card.appendChild(content);

    const metaDetails = document.createElement("details");
    metaDetails.className = "message-meta-details";
    const metaSummary = document.createElement("summary");
    metaSummary.textContent = "Message details";
    metaDetails.appendChild(metaSummary);
    metaDetails.appendChild(meta);
    card.appendChild(metaDetails);

    const rawDetails = document.createElement("details");
    rawDetails.className = "message-raw";
    const summary = document.createElement("summary");
    summary.textContent = "Raw message JSON";
    rawDetails.appendChild(summary);
    const rawPre = document.createElement("pre");
    rawPre.textContent = JSON.stringify(message.raw || message.raw_json || {}, null, 2);
    rawDetails.appendChild(rawPre);
    card.appendChild(rawDetails);

    elements.messages.appendChild(card);
  });

  const rawConversation = payload.raw_conversation || {};
  elements.rawConversation.textContent = JSON.stringify(rawConversation, null, 2);
  chatFind.refresh({ scroll: Boolean(elements.chatFindInput.value.trim()) });
}

async function saveConversationTags(tags) {
  const conversationId = state.selectedConversationId;
  if (!conversationId) {
    return;
  }

  const requestVersion = ++state.tagSaveRequestVersion;
  let saveFailed = false;
  setTagEditorBusy(true);
  setTagStatus("Saving tags...");

  try {
    const payload = await fetchJson(`/api/conversations/${encodeURIComponent(conversationId)}/tags`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ tags }),
    });
    if (requestVersion !== state.tagSaveRequestVersion || conversationId !== state.selectedConversationId) {
      return;
    }

    const savedTags = coerceTags(Array.isArray(payload.tags) ? payload.tags : tags);
    renderDetailTags(savedTags);
    setTagEditorBusy(false);
    closeTagEditor({ returnFocus: true, preserveStatus: true });
    setTagStatus(savedTags.length === 0 ? "All tags removed." : "Tags saved.");
    updateConversationCardTags(conversationId, savedTags);
    if (state.query && (state.mode === "normal" || state.mode === "tag")) {
      await reloadLoadedConversationsPreservingPosition();
    }
  } catch (error) {
    if (requestVersion !== state.tagSaveRequestVersion || conversationId !== state.selectedConversationId) {
      return;
    }
    if (error.status === 400) {
      elements.tagInput.setAttribute("aria-invalid", "true");
    }
    saveFailed = true;
    setTagStatus(error.message, true);
  } finally {
    if (requestVersion === state.tagSaveRequestVersion) {
      setTagEditorBusy(false);
      if (
        conversationId === state.selectedConversationId
        && state.tagEditorOpen
        && saveFailed
      ) {
        elements.tagInput.focus();
      }
    }
  }
}

async function loadConversation(conversationId) {
  const requestVersion = ++state.detailRequestVersion;
  try {
    const params = new URLSearchParams({
      path: state.pathMode,
      include_raw: "1",
      hide_empty_system: "1",
    });
    const payload = await fetchJson(`/api/conversations/${conversationId}?${params.toString()}`);
    if (requestVersion !== state.detailRequestVersion || conversationId !== state.selectedConversationId) {
      return;
    }
    renderConversationDetail(payload);
    if (window.matchMedia("(max-width: 900px)").matches && !elements.chatFindInput.value.trim()) {
      elements.detailView.scrollIntoView({ behavior: "smooth", block: "start" });
    }
  } catch (error) {
    setStatus(error.message, true);
  }
}

async function refreshStats() {
  try {
    const stats = await fetchJson("/api/stats");
    const numberFormat = new Intl.NumberFormat();
    const lastImport = stats.last_imported_at
      ? new Date(stats.last_imported_at).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" })
      : "Never";
    clearChildren(elements.stats);
    elements.stats.appendChild(makeStat(numberFormat.format(stats.conversation_count), "Conversations"));
    elements.stats.appendChild(makeStat(numberFormat.format(stats.message_count), "Messages"));
    elements.stats.appendChild(makeStat(numberFormat.format(stats.export_count), "Imports"));
    elements.stats.appendChild(makeStat(lastImport, "Last import"));
  } catch (error) {
    elements.stats.textContent = `Stats unavailable: ${error.message}`;
  }
}

async function importFromPath(pathValue) {
  setStatus("Import started...");
  const payload = await fetchJson("/api/import-path", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path: pathValue }),
  });
  setStatus(describeImport(payload));
  await refreshStats();
  await loadConversations({ reset: true });
}

async function importFromFile(file) {
  setStatus("Import started...");
  const formData = new FormData();
  formData.append("file", file);
  const payload = await fetchJson("/api/import-file", {
    method: "POST",
    body: formData,
  });
  setStatus(describeImport(payload));
  await refreshStats();
  await loadConversations({ reset: true });
}

elements.searchForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  applyListCriteria({
    mode: elements.searchMode.value,
    query: elements.searchInput.value.trim(),
  });
  await loadConversations({ reset: true });
});

elements.readingFormat.addEventListener("change", () => {
  state.readingFormat = elements.readingFormat.value === "original" ? "original" : "formatted";
  localStorage.setItem("cev_reading_format", state.readingFormat);
  elements.messages.querySelectorAll(".message-content").forEach(content => {
    renderMessageBody(content, messageSources.get(content));
  });
  chatFind.refresh({ preserveActive: true });
});

document.addEventListener("click", event => {
  if (elements.importDrawer.open && !elements.importDrawer.contains(event.target)) {
    elements.importDrawer.open = false;
  }
});

elements.importDrawer.addEventListener("keydown", event => {
  if (event.key === "Escape" && elements.importDrawer.open) {
    elements.importDrawer.open = false;
    elements.importDrawer.querySelector("summary").focus();
  }
});

elements.editTags.addEventListener("click", () => {
  if (state.tagEditorOpen) {
    elements.tagInput.focus();
    return;
  }
  openTagEditor();
});

elements.cancelTags.addEventListener("click", () => {
  if (!state.tagSaving) {
    closeTagEditor({ returnFocus: true });
  }
});

elements.tagEditor.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (state.tagSaving) {
    return;
  }
  const result = normalizeTagInput(elements.tagInput.value);
  if (result.error) {
    elements.tagInput.setAttribute("aria-invalid", "true");
    setTagStatus(result.error, true);
    elements.tagInput.focus();
    return;
  }
  elements.tagInput.removeAttribute("aria-invalid");
  await saveConversationTags(result.tags);
});

elements.tagInput.addEventListener("input", () => {
  elements.tagInput.removeAttribute("aria-invalid");
  if (elements.tagStatus.textContent) {
    setTagStatus("");
  }
});

elements.tagInput.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && !state.tagSaving) {
    event.preventDefault();
    closeTagEditor({ returnFocus: true });
    return;
  }
  if (event.key === "Enter" && !event.shiftKey && !event.isComposing && !state.tagSaving) {
    event.preventDefault();
    elements.tagEditor.requestSubmit();
  }
});

window.addEventListener("beforeunload", (event) => {
  if (state.tagSaving || tagEditorHasUnsavedChanges()) {
    event.preventDefault();
    event.returnValue = "";
  }
});

elements.activityFrom.addEventListener("change", async () => {
  if (!elements.activityFrom.value) {
    elements.activityThrough.value = "";
  } else if (elements.activityThrough.value && elements.activityThrough.value < elements.activityFrom.value) {
    elements.activityThrough.value = "";
  }
  applyListCriteria({
    activityFrom: elements.activityFrom.value,
    activityThrough: elements.activityThrough.value,
  });
  syncActivityFilterControls();
  await loadConversations({ reset: true });
});

elements.activityThrough.addEventListener("change", async () => {
  applyListCriteria({ activityThrough: elements.activityThrough.value });
  await loadConversations({ reset: true });
});

elements.clearActivityDates.addEventListener("click", async () => {
  elements.activityFrom.value = "";
  elements.activityThrough.value = "";
  applyListCriteria({ activityFrom: "", activityThrough: "" });
  syncActivityFilterControls();
  await loadConversations({ reset: true });
});

elements.loadMore.addEventListener("click", async () => {
  if (state.loadingList || !state.listReady || state.offset + state.limit >= state.total) {
    return;
  }
  await loadConversations({ reset: false, offset: state.offset + state.limit });
});

elements.pathMode.addEventListener("change", async () => {
  state.pathMode = elements.pathMode.value;
  if (state.selectedConversationId) {
    await loadConversation(state.selectedConversationId);
  }
});

elements.exportConversation.addEventListener("click", () => {
  if (!state.selectedConversationId) {
    return;
  }
  window.location.href = `/api/conversations/${state.selectedConversationId}/export`;
});

elements.exportChatOnly.addEventListener("click", () => {
  if (!state.selectedConversationId) {
    return;
  }
  const params = new URLSearchParams({ path: state.pathMode });
  window.location.href = `/api/conversations/${state.selectedConversationId}/export-chat?${params.toString()}`;
});

elements.exportChatSplit.addEventListener("click", () => {
  if (!state.selectedConversationId) {
    return;
  }
  const params = new URLSearchParams({
    path: state.pathMode,
    target_tokens: "30000",
  });
  window.location.href = `/api/conversations/${state.selectedConversationId}/export-chat-split?${params.toString()}`;
});

elements.importPathForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const pathValue = elements.importPathInput.value.trim();
  if (!pathValue) {
    setStatus("Please provide a path.", true);
    return;
  }
  try {
    await importFromPath(pathValue);
  } catch (error) {
    setStatus(error.message, true);
  }
});

elements.importFileForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const file = elements.importFileInput.files && elements.importFileInput.files[0];
  if (!file) {
    setStatus("Please choose a file to upload.", true);
    return;
  }
  try {
    await importFromFile(file);
  } catch (error) {
    setStatus(error.message, true);
  }
});

elements.themeToggle.addEventListener("click", () => {
  const isDark = document.documentElement.getAttribute("data-theme") === "dark";
  const nextTheme = isDark ? "light" : "dark";
  applyTheme(nextTheme);
  localStorage.setItem("cev_theme", nextTheme);
});

document.addEventListener("keydown", async (event) => {
  if ((event.ctrlKey || event.metaKey) && !event.altKey && !event.shiftKey
      && event.key.toLowerCase() === "f" && state.selectedConversationId
      && !elements.detailView.classList.contains("hidden")) {
    event.preventDefault();
    elements.chatFindInput.focus();
    elements.chatFindInput.select();
    return;
  }
  const target = event.target;
  const isTyping = target instanceof HTMLInputElement || target instanceof HTMLTextAreaElement || target instanceof HTMLSelectElement;
  if (event.key === "/" && !isTyping) {
    event.preventDefault();
    elements.searchInput.focus();
  }
  if (event.key === "Escape" && document.activeElement === elements.searchInput && elements.searchInput.value) {
    elements.searchInput.value = "";
    applyListCriteria({ query: "" });
    await loadConversations({ reset: true });
  }
});

async function bootstrap() {
  initializeTheme();
  state.readingFormat = localStorage.getItem("cev_reading_format") === "original" ? "original" : "formatted";
  elements.readingFormat.value = state.readingFormat;
  syncActivityFilterControls();
  await refreshStats();
  await loadConversations({ reset: true });
}

bootstrap();
