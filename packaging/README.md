# Building the portable Windows edition

Builds use a separate environment in `.portable-build/tools`; the working viewer's
`.venv` and archive data are never inputs. From the workspace root, use an existing
64-bit Python 3.11 installation to create that environment and install the pinned
`requirements-build.txt`, then run:

```powershell
.\.portable-build\tools\Scripts\python.exe packaging\build_portable.py
```

The script creates a fresh staging directory for every build. Its source inputs
are an explicit list of app modules, the entry point, templates, and static assets.
It includes the bundled interpreter, app dependencies, fonts, and licenses, but
never copies the workspace root or an existing data directory.

Outputs live in a new timestamped folder under `release/`: the clean one-folder
app, a ZIP, a ZIP SHA-256 file, and an audit report. The distribution has no `data`
folder or database; the runtime creates them on first launch. Testing must use
a separate extraction of the ZIP so the release copy remains empty.

`BUNDLE-MANIFEST.json` records every distributed file hash except the manifest
itself, the staged source hashes, Python version, dependency versions, and build
identity. The audit validates ZIP paths, checks every file against that manifest,
rejects databases/runtime state, and checks shipped text for build-user paths.
It also reads the executable's compressed Python code to catch personal speaker
defaults or developer paths that would otherwise be invisible in a plain ZIP scan.

Recipient documentation is included in every ZIP: `START HERE.txt` gives the
quick start, while `README.txt` explains importing, searching, reading, tags,
exports, backups, updates, and known limitations. Edit their copies in this
`packaging` directory before building.

Re-audit a ZIP without building:

```powershell
.\.portable-build\tools\Scripts\python.exe packaging\build_portable.py --audit "path\to\The Archive - Windows Portable.zip"
```

To update only those two recipient documents in a previously audited ZIP:

```powershell
.\.portable-build\tools\Scripts\python.exe packaging\build_portable.py --refresh-docs "path\to\The Archive - Windows Portable.zip"
```

This creates a new timestamped release and leaves the input ZIP intact. It checks
that every executable, library, license, and interface asset keeps its original
hash, records a documentation revision in the manifest, then repeats the full
ZIP and compressed-code privacy audit. The app version remains the same because
the executable has not changed.

The build is for Windows x64 and must be tested from a moved/extracted folder,
with an empty archive, an imported synthetic chat, a second launch, and a clean
shutdown. A clean Windows computer without Python remains the strongest final
portability check. The executable is not digitally signed.
