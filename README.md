# The Archive

A local reader and searchable archive for ChatGPT JSON exports, with a portable
Windows edition for people who want to extract a ZIP and double-click the app.

## Download for Windows

Open [Releases](https://github.com/Kadwynn/the-archive/releases), download
the Windows portable ZIP, and choose **Extract All**. Open the extracted folder
and double-click **The Archive.exe**. Python and the required libraries are included.

Choose **Add conversations** to import your own JSON. The release ZIP starts empty;
your archive is created in a `data` folder beside the executable. Use **Quit app**
before moving or backing up that folder. Share the original clean ZIP when giving
someone an empty copy.

Read [the full user guide](packaging/README.txt) for import instructions, the
optional Chrome exporter, search, tags, exports, backups, and updates.

## Features

- Full ChatGPT exports and compatible individual conversation JSON imports.
- Incremental merging that retains chats missing from newer exports.
- Keyword, title, typo-tolerant, and tag searches, plus activity-date filters.
- Formatted Markdown, original-text view, and visible model identifiers.
- Find within the selected chat, excluding metadata and raw JSON.
- Manual tags, current/all branch views, and Markdown, split ZIP, and JSON exports.
- Dark and light themes, with local fonts and Markdown libraries for offline use.

Imports are manual. The app runs locally and does not synchronize with ChatGPT.
This Windows build is unsigned. Unusual reasoning/tool/media content may render
as text or placeholders. Individual extracted messages are capped at 80,000
characters in the reader/search/readable exports; raw JSON retains source fields.

## Run from source

Use Python 3.11 or later. From this repository on Windows:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe portable.py
```

Source runs keep their development archive in `.portable-build/development/data`
so they do not open a different existing archive accidentally. The packaged app
keeps its archive beside the executable instead.

## Build and test

See [packaging instructions](packaging/README.md) for the isolated build
environment, empty-release audit, and frozen-application smoke tests.

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests
```

Browser module tests in `tests/*.cjs` use Node.js and Playwright. Application
tests use synthetic fixtures and temporary databases.

Third-party library and font licenses are preserved beside their files and in
the portable release's `LICENSES` folder.
