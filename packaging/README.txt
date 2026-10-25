THE ARCHIVE
Portable Windows edition - user guide

The Archive keeps ChatGPT conversations in a searchable collection on your
computer. You can import full data exports or compatible single-chat JSON
files, read them comfortably, add your own tags, and export individual chats.

Imports are manual. When a conversation continues in ChatGPT, export it again
and import the newer file to bring your local archive up to date.

CONTENTS
  1. Getting started
  2. Importing a full ChatGPT data export
  3. Importing one conversation with ChatGPT Exporter
  4. What happens when you import again
  5. Finding conversations, tags, and dates
  6. Reading and searching inside a chat
  7. Adding tags
  8. Exporting a conversation
  9. Backups, moving, updating, and sharing
 10. Known limitations and troubleshooting


1. GETTING STARTED
------------------
This edition is for 64-bit Windows 10 or 11. Python and the required libraries
are included. You do not need an installer, a terminal, or an Archive account.

1. Right-click the downloaded ZIP and choose Extract All.
2. Open the extracted folder and double-click The Archive.exe.
3. Your default browser opens the app. Choose Add conversations at the top.
4. Select a supported .json file and choose Add to archive.
5. Wait for the import status, then choose a conversation on the left.

Keep The Archive.exe and the _internal folder together. Use a folder you can
save into, such as Documents or a folder on a USB drive. Extract the whole ZIP
before opening the app.

The reader works offline. Getting a new export from ChatGPT, installing the
optional browser exporter, and opening external links need internet access.

Use Quit app to stop The Archive. Closing the browser tab alone leaves the
local app running. Another double-click reopens the same running copy.


2. IMPORTING A FULL CHATGPT DATA EXPORT
------------------------------------
Download your ChatGPT data export, then extract that ZIP as well. Find the
conversation JSON file, usually named conversations.json.

In The Archive, choose Add conversations, select that .json file, and choose
Add to archive. Import the JSON file itself, rather than the ZIP or chat.html.

If one export contains numbered conversation files, such as
conversations-001.json and conversations-002.json, import every part separately,
in number order. Wait for each import to finish before choosing the next file.
Other account-information JSON files are not conversation exports.

If you have several exports from different dates, start with the oldest export
and work forward to the newest. Import all numbered parts of each export
before moving on to the next export date. This gives newer information the
last say when fields have changed.

Large exports can take a while. Leave the app open until the status says the
import has finished. Keep your original export files as a separate backup.


3. IMPORTING ONE CONVERSATION WITH CHATGPT EXPORTER
-------------------------------------------------
This is an optional Chrome walkthrough. An extension is not needed to import
the full data exports described above, and The Archive opens in your default
browser independently of the browser used to make the export.

1. In Chrome, install the Tampermonkey extension from the Chrome Web Store:
   https://chromewebstore.google.com/detail/tampermonkey/dhdgffkkebhmkfjojejmpbldmpobfkfo?hl=en&pli=1

2. Install the ChatGPT Exporter userscript by pionxzh from its publisher page:
   https://greasyfork.org/de/scripts/456055-chatgpt-exporter
   Follow the publisher's current setup instructions, including any requested
   Allow User Scripts setting for Tampermonkey.

3. Open the conversation you want to save on the ChatGPT website. Use the
   exporter to export that conversation. Choose JSON, then OpenAI official
   format when asked which JSON format to use.

4. Save the resulting .json file. In The Archive, choose Add conversations,
   select the file, and choose Add to archive.

Other JSON formats offered by an exporter, such as TavernAI/SillyTavern or
Ooba, have different structures. Choose OpenAI official format for this app.
An HTML, Markdown, or plain-text export cannot be imported as a conversation.

ChatGPT Exporter and Tampermonkey are separate third-party projects. They are
not included in this ZIP. Their menus and setup requirements may change;
follow the linked publisher instructions if the steps look different.

Publisher documentation:
https://github.com/pionxzh/chatgpt-exporter


4. WHAT HAPPENS WHEN YOU IMPORT AGAIN
-----------------------------------
The Archive builds a growing collection rather than replacing it with each
new file. It uses conversation IDs to recognise chats it has already seen.

- New conversations are added.
- Existing conversations are merged with newly imported information, including
  continued chats and additional message branches.
- Chats missing from a newer export are kept in The Archive.
- An identical file that has already been imported is skipped.
- Your manually added tags survive later imports of the same conversation.

Re-exporting a conversation and importing it again normally updates the
existing archived conversation instead of adding a second copy. Export tools
that change or omit conversation IDs may behave differently or be unsupported.

The Archive retains what was previously imported. It cannot recover a message
that was never present in any file you imported. Importing affects the local
archive, not your conversations on the ChatGPT website.


5. FINDING CONVERSATIONS, TAGS, AND DATES
--------------------------------------
Use Search the archive on the left to find a conversation, then select it to
read it. Use Load more when more results are available.

The Match by menu offers:

- Best match: search conversation titles, message text, and your own tags.
- Typo tolerant: try this when you are unsure of a word's spelling.
- Titles only: search conversation titles.
- Tags only: search your manually added labels. Exact names rank first, but
  partial or word matches can also appear. Matching ignores letter case.

Clicking a displayed tag starts a Tags only search for that label.

For dates, open Activity window below the search controls:

- Choose From alone to show activity on one day.
- Add Through to show an inclusive date range: the whole final day counts.
- Dates use your computer/browser's local time.
- You can combine the date filter with a keyword or tag search, or use it alone.
- Use Clear in Activity window to remove the date filter.

Activity means the recorded dates of conversations or their messages, such as
creation or update dates. It does not mean that the message text mentions that
date. Use Activity window rather than typing a date range into the keyword box.

Archive-wide search can find text in alternate message branches. If a passage
is missing from the selected chat view, try Messages -> All branches.


6. READING AND SEARCHING INSIDE A CHAT
------------------------------------
The default Formatted view displays Markdown headings, emphasis, lists,
quotes, code, and tables. Choose View -> Original text to see the original
message text with its formatting symbols. The app remembers this view choice.

The model identifier appears beneath Assistant when the imported data supplies
model information. Message details and Raw message JSON contain extra metadata
and the stored technical data for a reply.

Messages -> Current branch shows the conversation's recorded current path.
All branches also shows alternate or regenerated replies retained in the
export, which can make the conversation look repetitive.

With a chat open, press Ctrl+F or use Find in this chat:

- Matches are highlighted and the counter shows your current result.
- Enter or the down-arrow button goes to the next match.
- Shift+Enter or the up-arrow button goes to the previous match.
- Escape, or the clear button, clears the chat search.

This search checks message bodies in the selected branch view. It excludes
metadata and raw JSON, including raw JSON you have expanded. In Original text
view, visible Markdown symbols are part of the searchable text.

The Light/Dark button changes the reader theme.


7. ADDING TAGS
-------------
Open a conversation and choose Add tags, or Edit tags if it already has labels.
Type labels separated by commas or new lines, then choose Save tags. Enter
saves; Shift+Enter starts a new line. Cancel discards your unsaved edits.

Examples: recipes, travel, writing, favourite, research

To remove a tag, remove it in the editor and save. Up to 30 tags can be added
to one conversation, with up to 80 characters in each tag.

Tags belong to your local archive. They survive re-imports and are included
when you back up the data folder. They are separate from the imported source
JSON and are not included in individual conversation exports.


8. EXPORTING A CONVERSATION
--------------------------
Open a chat and choose one of the export buttons:

Readable .md
  Saves a Markdown document containing user and assistant text. It is useful
  for reading, editing, or opening in another Markdown-compatible application.
  The portable edition labels speakers User and Assistant.

Split .zip
  Saves a ZIP containing numbered Markdown files for a long chat. Extract it
  and read the parts in order. Splitting aims for manageable text chunks and
  keeps each message together, so one unusually long message may exceed the
  approximate chunk size.

Original .json
  Saves the full stored conversation data, including retained metadata and
  branches. If the conversation has been imported several times, this is its
  merged archive version, not an untouched copy of one earlier input file.
  The conversation is wrapped in a JSON array and can be imported again.

Readable .md and Split .zip follow the Messages -> Current branch/All branches
selection. They contain user/assistant chat text rather than every system or
tool entry. Original .json always exports the whole stored conversation,
regardless of the selected branch view. None of these exports include your
locally added tags; back up the data folder to keep those as well.


9. BACKUPS, MOVING, UPDATING, AND SHARING
--------------------------------------
The first launch creates a data folder beside The Archive.exe. Your imported
chats and tags are stored in data\master_export.db. Treat that folder as private.

For a backup or move:
1. Use Quit app first.
2. Copy the whole data folder to keep your archive and tags. You can also copy
   the entire extracted app folder to move both the app and the archive.
3. Keep The Archive.exe and _internal together when moving the app.

For an update:
1. Quit the old app and extract the new release into a separate folder.
2. Copy your old data folder into the new app folder before opening it.
3. Open the new app and check your chats. Keep the old folder as a backup
   until you are happy with the update.

To give someone an empty copy, share the original release ZIP. After you
import chats, your extracted folder contains your personal archive. Do not
re-zip that populated folder for other people.


10. KNOWN LIMITATIONS AND TROUBLESHOOTING
---------------------------------------
- Reasoning text included by an exporter, tool calls, and unusual message types
  may appear as plain text, JSON, placeholders, or awkwardly formatted entries.
  Different exporters include different fields; the app can only show what
  the imported files contain.

- Images, audio, attachments, and interactive tools are not fully replayed.
  Image references may appear as links or placeholders. Images are not loaded
  automatically while reading; an external image link needs internet access.

- Individual extracted messages are limited to 80,000 characters. Very long
  messages can be shortened in the reader, searches, and readable exports.
  Raw message JSON and Original .json retain the imported source fields.

- Not every JSON export format is compatible. For ChatGPT Exporter, check
  that you selected JSON -> OpenAI official format. For a full data export,
  choose the conversation JSON file rather than an account-information file.

- If a chat or passage is hard to find, clear any Activity window filter,
  check the Match by mode, try Typo tolerant, or switch to All branches.

- If the browser tab was closed, double-click The Archive.exe to reopen it.
  If startup fails, check that the ZIP was fully extracted, the _internal
  folder is beside the executable, and the folder can be written to.

- Startup errors may be recorded in data\portable.log. Review that file before
  sharing it, because errors can include local file paths.

This edition is unsigned. Windows and managed computers apply their normal
checks to unfamiliar apps. Third-party licenses are in the LICENSES folder.

LICENSING
---------
The Archive's original application code and documentation are released under
the Unlicense. The standard license text is in LICENSE beside the executable.
Bundled libraries and fonts retain their own licenses and notices. Imported
conversations, attachments, and other user data are not covered by the app's
license.

External exporter links and instructions checked on 25 October 2026.
