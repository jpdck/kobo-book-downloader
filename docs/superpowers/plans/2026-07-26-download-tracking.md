# Download Tracking Manifest Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop `kobo-book-downloader` from redownloading books it has already fetched, by tracking downloads in a per-directory JSON manifest.

**Architecture:** A new `Manifest.py` module owns a `<outdir>/.kobo-downloader.json` file mapping a stable book key to the revision and filename downloaded. `Commands.py` consults it before each directory-target download. The manifest is authoritative for *which revision* was fetched; the filesystem is authoritative for *whether the file still exists*. A book is skipped only when both agree.

**Tech Stack:** Python 3, standard library only (`json`, `os`, `datetime`, `tempfile`). No new dependencies. The repo has no test framework and none is being introduced — verification is by running real code, as specified in the design.

**Spec:** `docs/superpowers/specs/2026-07-26-download-tracking-design.md`

## Global Constraints

- **Indentation is tabs**, matching every existing file in `kobo-book-downloader/`.
- **Method naming is PascalCase**; private helpers use the `__DoubleUnderscore` prefix. Follow `Settings.py` as the reference for module shape.
- **No new third-party dependencies.** `requirements.txt` must not change.
- **Standard library only** in `Manifest.py`. It must not import `Kobo`, `Globals`, or `colorama`, so it stays runnable and verifiable in isolation.
- **Manifest filename:** `.kobo-downloader.json` — exact string.
- **Manifest schema version:** integer `1`.
- **Date format** for `revisionDate` and revised filenames: `YYYY-MM-DD`.
- **Revised filename suffix:** ` Revised <date>`, then ` v2`, ` v3` on collision. The unsuffixed name is reserved for the first file bearing that date.
- **A manifest failure must never abort a download.** Every read/write error degrades to a warning.
- Run Python as `python3` from the repo root; the module lives in `kobo-book-downloader/`, which is the program's import root.

---

### Task 1: Confirm the entitlement ID and revision date field names

This is the first task because the answer decides the manifest key used by every
later task. The design assumes `library_sync` exposes a stable per-book entitlement
ID and a revision date; `Kobo.py` references neither. Confirm before building on it.

**Priority note:** getting *a* download tracked correctly outranks revision detection.
If the revision date is missing, revisions degrade gracefully and that is acceptable.
If the entitlement ID is missing, **key on the computed filename, not RevisionId** —
keying on RevisionId would make a revised book look brand new and redownload it,
reintroducing the exact bug this plan fixes.

**Files:**
- Create: `scratch_dump_entitlement.py` (temporary, deleted in Step 4)

**Interfaces:**
- Consumes: nothing.
- Produces: a decision recorded in the plan — the exact JSON path for the book key
  (`BOOK_KEY_FIELD`) and the revision date (`REVISION_DATE_FIELD`), consumed by Tasks 4 and 5.

- [ ] **Step 1: Write a script that dumps one entitlement**

Create `scratch_dump_entitlement.py` in the repo root:

```python
import json
import logging
import sys

sys.path.insert( 0, "kobo-book-downloader" )

from Globals import Globals
from Kobo import Kobo
from Settings import Settings

logging.basicConfig( level = logging.WARNING )
Globals.Logger = logging.getLogger( "dump" )
Globals.Settings = Settings()
Globals.Kobo = Kobo()
Globals.Kobo.LoadInitializationSettings()

bookList = Globals.Kobo.GetMyBookList()
for entitlement in bookList:
	newEntitlement = entitlement.get( "NewEntitlement" )
	if newEntitlement is None:
		continue
	if newEntitlement.get( "BookMetadata" ) is None:
		continue
	print( json.dumps( entitlement, indent = 4 ) )
	break
```

- [ ] **Step 2: Run it and capture the output**

Run: `python3 scratch_dump_entitlement.py > /tmp/entitlement.json 2>&1; head -100 /tmp/entitlement.json`

Expected: a single entitlement as pretty-printed JSON. If it fails with an
authentication error, the login flow must be completed first via
`python3 kobo-book-downloader list` — this is a real credential requirement, not a
code defect. Report the failure rather than working around it.

- [ ] **Step 3: Identify the two fields**

Search the captured JSON for candidate keys:

Run: `grep -n -i "entitlementid\|crossrevisionid\|productid\|revisionid\|dateadded\|lastmodified\|publicationdate\|revisiondate" /tmp/entitlement.json`

Record the findings:

- **Book key.** Look inside `BookEntitlement` and `BookMetadata` for an ID that is
  stable across revisions. `CrossRevisionId` is the strongest candidate — the name
  states it spans revisions. `EntitlementId` and `ProductId` are alternatives.
  Choose the first present, preferring `CrossRevisionId`.
  If none is present, set the key to the computed filename (see the priority note above).
- **Revision date.** Candidates are `PublicationDate`, `LastModified`, or
  `DateLastUpdated` on `BookMetadata` or `BookEntitlement`. If none is present,
  revision detection falls back to the current date, and that is acceptable.

Write both answers into a comment at the top of `Manifest.py` in Task 2, so the
decision is recorded in the code rather than lost.

- [ ] **Step 4: Delete the scratch script and commit nothing**

```bash
rm scratch_dump_entitlement.py /tmp/entitlement.json
```

This task produces a decision, not a commit. The scratch script must not be
committed — it prints an entire entitlement, which contains account-linked metadata.

---

### Task 2: Create Manifest.py with load and save

**Files:**
- Create: `kobo-book-downloader/Manifest.py`

**Interfaces:**
- Consumes: the field-name decision from Task 1 (recorded as a comment only).
- Produces:
  - `Manifest( directoryPath: str )` — constructor, loads on init, never raises.
  - `Manifest.FileName` — class attribute, the string `".kobo-downloader.json"`.
  - `Manifest.Save() -> None` — atomic write, never raises.
  - `self.Books: dict` — the entitlement-key → entry mapping.
  - `self.DirectoryPath: str` — the directory the manifest lives in.

- [ ] **Step 1: Write the module**

Create `kobo-book-downloader/Manifest.py`. Note the file uses **tabs**:

```python
import json
import os
import tempfile

# Field names confirmed in Task 1 against a live library_sync response:
#   Book key:      <record the confirmed field name here>
#   Revision date: <record the confirmed field name here>

class Manifest:
	FileName = ".kobo-downloader.json"
	Version = 1

	def __init__( self, directoryPath: str ):
		self.DirectoryPath = directoryPath
		self.FilePath = os.path.join( directoryPath, Manifest.FileName )
		self.Books = {}

		self.Load()

	def Load( self ) -> None:
		if not os.path.isfile( self.FilePath ):
			return

		try:
			with open( self.FilePath, "r", encoding = "utf-8" ) as f:
				jsonObject = json.loads( f.read() )
		except ( OSError, ValueError ) as e:
			# A corrupt manifest must never block downloading. Degrade to empty.
			print( "Warning: could not read '%s' (%s). Treating it as empty." % ( self.FilePath, e ) )
			return

		books = jsonObject.get( "books" )
		if isinstance( books, dict ):
			self.Books = books

	def Save( self ) -> None:
		jsonObject = {
			"version": Manifest.Version,
			"books": self.Books,
		}

		# Write to a temporary file in the same directory then replace, so an
		# interrupted run can't leave a half-written manifest behind.
		try:
			with tempfile.NamedTemporaryFile(
				mode = "w",
				encoding = "utf-8",
				dir = self.DirectoryPath,
				prefix = Manifest.FileName,
				suffix = ".tmp",
				delete = False,
			) as f:
				temporaryPath = f.name
				f.write( json.dumps( jsonObject, indent = 4, sort_keys = True ) )

			os.replace( temporaryPath, self.FilePath )
		except OSError as e:
			# Losing the record costs one redundant download later. The book is
			# already on disk, so this must not fail the run.
			print( "Warning: could not write '%s' (%s)." % ( self.FilePath, e ) )
```

- [ ] **Step 2: Verify load-of-nothing, save, and reload round-trip**

Run:

```bash
python3 -c '
import sys, os, tempfile, json
sys.path.insert(0, "kobo-book-downloader")
from Manifest import Manifest

d = tempfile.mkdtemp()
m = Manifest(d)
assert m.Books == {}, "fresh manifest should be empty"

m.Books["key1"] = {"revisionId": "r1", "fileName": "a.epub"}
m.Save()

path = os.path.join(d, ".kobo-downloader.json")
assert os.path.isfile(path), "manifest file should exist"
assert json.load(open(path))["version"] == 1, "version should be 1"

m2 = Manifest(d)
assert m2.Books["key1"]["revisionId"] == "r1", "should round-trip"
assert not [f for f in os.listdir(d) if f.endswith(".tmp")], "no temp files left behind"
print("PASS: load/save round-trip")
'
```

Expected: `PASS: load/save round-trip`

- [ ] **Step 3: Verify a corrupt manifest degrades instead of crashing**

Run:

```bash
python3 -c '
import sys, os, tempfile
sys.path.insert(0, "kobo-book-downloader")
from Manifest import Manifest

d = tempfile.mkdtemp()
open(os.path.join(d, ".kobo-downloader.json"), "w").write("{ this is not json")

m = Manifest(d)
assert m.Books == {}, "corrupt manifest should degrade to empty"
print("PASS: corrupt manifest degrades")
'
```

Expected: a warning line, then `PASS: corrupt manifest degrades`. It must not raise.

- [ ] **Step 4: Commit**

```bash
git add kobo-book-downloader/Manifest.py
git commit -m "Add Manifest module for tracking downloaded books"
```

---

### Task 3: Add revised-filename generation

**Files:**
- Modify: `kobo-book-downloader/Manifest.py`

**Interfaces:**
- Consumes: `Manifest` from Task 2.
- Produces: `Manifest.MakeRevisedFileName( baseName: str, revisionDate: str ) -> str`
  — returns a filename that does not currently exist in `self.DirectoryPath`.
  `baseName` is the original filename including its `.epub` extension.
  Consumed by Task 5.

- [ ] **Step 1: Add the method to the Manifest class**

Append inside the `Manifest` class:

```python
	def MakeRevisedFileName( self, baseName: str, revisionDate: str ) -> str:
		root, extension = os.path.splitext( baseName )
		candidate = "%s Revised %s%s" % ( root, revisionDate, extension )

		# The unsuffixed name is reserved for the first file bearing this date.
		# Two revisions on the same day get " v2", " v3", and so on.
		version = 2
		while os.path.exists( os.path.join( self.DirectoryPath, candidate ) ):
			candidate = "%s Revised %s v%d%s" % ( root, revisionDate, version, extension )
			version += 1

		return candidate
```

- [ ] **Step 2: Verify the base case and the collision case**

Run:

```bash
python3 -c '
import sys, os, tempfile
sys.path.insert(0, "kobo-book-downloader")
from Manifest import Manifest

d = tempfile.mkdtemp()
m = Manifest(d)
base = "Joseph Heller - Catch-22.epub"

first = m.MakeRevisedFileName(base, "2024-11-02")
assert first == "Joseph Heller - Catch-22 Revised 2024-11-02.epub", first

open(os.path.join(d, first), "w").close()
second = m.MakeRevisedFileName(base, "2024-11-02")
assert second == "Joseph Heller - Catch-22 Revised 2024-11-02 v2.epub", second

open(os.path.join(d, second), "w").close()
third = m.MakeRevisedFileName(base, "2024-11-02")
assert third == "Joseph Heller - Catch-22 Revised 2024-11-02 v3.epub", third

other = m.MakeRevisedFileName(base, "2025-01-15")
assert other == "Joseph Heller - Catch-22 Revised 2025-01-15.epub", other
print("PASS: revised filename generation")
'
```

Expected: `PASS: revised filename generation`

- [ ] **Step 3: Commit**

```bash
git add kobo-book-downloader/Manifest.py
git commit -m "Add revised filename generation to Manifest"
```

---

### Task 4: Add the skip decision and recording

**Files:**
- Modify: `kobo-book-downloader/Manifest.py`

**Interfaces:**
- Consumes: `Manifest` from Tasks 2 and 3.
- Produces:
  - `Manifest.GetEntry( bookKey: str ) -> dict | None`
  - `Manifest.IsDownloaded( bookKey: str, revisionId: str, fileName: str ) -> bool`
  - `Manifest.Record( bookKey, revisionId, fileName, revisionDate ) -> None` — records
    and saves immediately.
  Consumed by Task 5.

`IsDownloaded` implements rules 1–4 of the spec's skip decision, including adoption
of an existing on-disk file. Keeping it in one method is deliberate: the rule "a file
on disk is evidence of a completed download" must not be duplicated across call sites.

- [ ] **Step 1: Add the methods to the Manifest class**

Add `import datetime` to the imports at the top of the file, then append inside the class:

```python
	def GetEntry( self, bookKey: str ):
		return self.Books.get( bookKey )

	def IsDownloaded( self, bookKey: str, revisionId: str, fileName: str ) -> bool:
		entry = self.Books.get( bookKey )

		if entry is None:
			# Rule 1: not tracked. If the computed file is already on disk it was
			# downloaded by an earlier version of this tool -- adopt it rather than
			# fetching the whole library again.
			if os.path.isfile( os.path.join( self.DirectoryPath, fileName ) ):
				self.Record( bookKey, revisionId, fileName, None )
				return True

			return False

		recordedFileName = entry.get( "fileName", "" )

		# Rule 2: tracked, but the file is gone. Deleting a book redownloads it.
		if not os.path.isfile( os.path.join( self.DirectoryPath, recordedFileName ) ):
			return False

		# Rule 3 skips and rule 4 downloads a new revision.
		return entry.get( "revisionId" ) == revisionId

	def Record( self, bookKey: str, revisionId: str, fileName: str, revisionDate ) -> None:
		entry = {
			"revisionId": revisionId,
			"fileName": fileName,
			"downloadedAt": datetime.datetime.now( datetime.timezone.utc ).strftime( "%Y-%m-%dT%H:%M:%SZ" ),
		}

		if revisionDate is not None:
			entry[ "revisionDate" ] = revisionDate

		self.Books[ bookKey ] = entry
		self.Save()
```

- [ ] **Step 2: Verify all four skip rules**

Run:

```bash
python3 -c '
import sys, os, tempfile
sys.path.insert(0, "kobo-book-downloader")
from Manifest import Manifest

d = tempfile.mkdtemp()
m = Manifest(d)
name = "Book.epub"

# Rule 1a: untracked, not on disk -> download.
assert m.IsDownloaded("k1", "r1", name) is False, "untracked+absent should download"

# Rule 1b: untracked, on disk -> adopt and skip.
open(os.path.join(d, name), "w").close()
assert m.IsDownloaded("k1", "r1", name) is True, "untracked+present should adopt"
assert m.GetEntry("k1")["revisionId"] == "r1", "adoption should record the revision"

# Rule 3: tracked, present, same revision -> skip.
assert m.IsDownloaded("k1", "r1", name) is True, "same revision should skip"

# Rule 4: tracked, present, different revision -> download.
assert m.IsDownloaded("k1", "r2", name) is False, "new revision should download"

# Rule 2: tracked, file deleted -> download.
os.remove(os.path.join(d, name))
assert m.IsDownloaded("k1", "r1", name) is False, "deleted file should redownload"

print("PASS: all four skip rules")
'
```

Expected: `PASS: all four skip rules`

- [ ] **Step 3: Verify adoption survives a reload**

Run:

```bash
python3 -c '
import sys, os, tempfile
sys.path.insert(0, "kobo-book-downloader")
from Manifest import Manifest

d = tempfile.mkdtemp()
open(os.path.join(d, "Book.epub"), "w").close()

Manifest(d).IsDownloaded("k1", "r1", "Book.epub")
assert Manifest(d).GetEntry("k1")["revisionId"] == "r1", "adoption should persist"
print("PASS: adoption persists")
'
```

Expected: `PASS: adoption persists`

- [ ] **Step 4: Commit**

```bash
git add kobo-book-downloader/Manifest.py
git commit -m "Add skip decision and recording to Manifest"
```

---

### Task 5: Wire the manifest into Commands.py

**Files:**
- Modify: `kobo-book-downloader/Commands.py`

**Interfaces:**
- Consumes: the full `Manifest` API from Tasks 2–4, and the field names from Task 1.
- Produces: skip behavior in `__GetAllBooks`, `__DownloadPickedBooks`, and the
  directory-target branch of `__GetBook`.

- [ ] **Step 1: Add the import and two metadata helpers**

Add to the imports at the top of `Commands.py`:

```python
from Manifest import Manifest
```

Add these statics to the `Commands` class, beside `__IsBookArchived`. **Replace the
field names with the ones confirmed in Task 1.**

```python
	@staticmethod
	def __GetBookKey( newEntitlement: dict, bookMetadata: dict, fileName: str ) -> str:
		# Keyed on an identifier that is stable across revisions, so a revised book
		# is recognized as the same book rather than a new one. Field names confirmed
		# in Task 1. The filename is the last resort: keying on RevisionId would make
		# every revision look like a new book and redownload it.
		bookEntitlement = newEntitlement.get( "BookEntitlement" ) or {}

		for source, field in ( ( bookMetadata, "CrossRevisionId" ),
			( bookEntitlement, "CrossRevisionId" ),
			( bookEntitlement, "EntitlementId" ),
			( bookMetadata, "ProductId" ) ):
			value = source.get( field )
			if value:
				return str( value )

		return fileName

	@staticmethod
	def __GetRevisionDate( newEntitlement: dict, bookMetadata: dict ) -> str:
		# Kobo's date for the revision, used to name revised files. Falls back to
		# today when absent -- revision detection still works, the label is just
		# the date we noticed rather than the date it changed.
		bookEntitlement = newEntitlement.get( "BookEntitlement" ) or {}

		for source, field in ( ( bookMetadata, "PublicationDate" ),
			( bookEntitlement, "LastModified" ),
			( bookMetadata, "LastModified" ) ):
			value = source.get( field )
			if isinstance( value, str ) and len( value ) >= 10:
				candidate = value[ :10 ]
				try:
					datetime.datetime.strptime( candidate, "%Y-%m-%d" )
					return candidate
				except ValueError:
					continue

		return datetime.datetime.now().strftime( "%Y-%m-%d" )
```

Add `import datetime` to the imports at the top of `Commands.py`.

- [ ] **Step 2: Add a shared download-or-skip helper**

Add this static to `Commands`. Both `__GetAllBooks` and `__DownloadPickedBooks` call
it, so the skip and revision rules exist in exactly one place:

```python
	@staticmethod
	def __DownloadTracked( manifest: Manifest, newEntitlement: dict, bookMetadata: dict, outputPath: str ) -> None:
		revisionId = bookMetadata[ "RevisionId" ]
		fileName = Commands.__MakeFileNameForBook( bookMetadata )
		bookKey = Commands.__GetBookKey( newEntitlement, bookMetadata, fileName )

		if manifest.IsDownloaded( bookKey, revisionId, fileName ):
			print( colorama.Style.DIM + ( "Skipping already downloaded book '%s'." % fileName ) + colorama.Style.RESET_ALL )
			return

		revisionDate = Commands.__GetRevisionDate( newEntitlement, bookMetadata )
		entry = manifest.GetEntry( bookKey )

		# A book we already have under a different revision becomes a new file
		# alongside the original, which is never modified or deleted.
		if entry is not None and os.path.isfile( os.path.join( outputPath, entry.get( "fileName", "" ) ) ):
			fileName = manifest.MakeRevisedFileName( entry[ "fileName" ], revisionDate )

		outputFilePath = os.path.join( outputPath, fileName )
		print( "Downloading book to '%s'." % outputFilePath )
		Globals.Kobo.Download( revisionId, Kobo.DisplayProfile, outputFilePath )

		manifest.Record( bookKey, revisionId, fileName, revisionDate )
```

- [ ] **Step 3: Rewrite `__GetAllBooks` to use it**

Replace the body of `__GetAllBooks` (currently the `for entitlement in bookList` loop
and everything after it) with:

```python
		bookList = Globals.Kobo.GetMyBookList()
		manifest = Manifest( outputPath )
		failureCount = 0

		for entitlement in bookList:
			newEntitlement = entitlement.get( "NewEntitlement" )
			if newEntitlement is None:
				continue

			bookMetadata = newEntitlement.get( "BookMetadata" )
			if bookMetadata is None:
				continue

			# Skip archived books.
			if Commands.__IsBookArchived( newEntitlement ):
				title = bookMetadata[ "Title" ]
				author = Commands.__GetBookAuthor( bookMetadata )
				if len( author ) > 0:
					title += " by " + author

				print( colorama.Fore.LIGHTYELLOW_EX + ( "Skipping archived book %s." % title ) + colorama.Fore.RESET )
				continue

			try:
				Commands.__DownloadTracked( manifest, newEntitlement, bookMetadata, outputPath )
			except KoboException as e:
				# One unavailable book (a sample, a withdrawn title) shouldn't abort the whole library.
				failureCount += 1
				print( colorama.Fore.LIGHTRED_EX + ( "Skipping book: %s" % e ) + colorama.Fore.RESET )

		if failureCount > 0:
			print( colorama.Fore.LIGHTYELLOW_EX + ( "%d book(s) could not be downloaded, see the messages above." % failureCount ) + colorama.Fore.RESET )
```

The `os.path.isdir( outputPath )` guard at the top of the method stays as it is.

- [ ] **Step 4: Make `__GetBook` track directory targets and override explicit ones**

Replace `__GetBook` with:

```python
	@staticmethod
	def __GetBook( revisionId: str, outputPath: str ) -> None:
		if os.path.isdir( outputPath ):
			book = Globals.Kobo.GetBookInfo( revisionId )
			manifest = Manifest( outputPath )
			Commands.__DownloadTracked( manifest, {}, book, outputPath )
			return

		# An explicit filename is an override: download exactly what was asked for.
		parentPath = os.path.dirname( outputPath )
		if not os.path.isdir( parentPath ):
			raise KoboException( "The parent directory ('%s') of the output file must exist." % parentPath )

		print( "Downloading book to '%s'." % outputPath )
		Globals.Kobo.Download( revisionId, Kobo.DisplayProfile, outputPath )
```

`GetBookInfo` returns the book metadata without the surrounding entitlement, so `{}`
is passed for `newEntitlement`. Both helpers tolerate it — that is why they read the
entitlement defensively with `or {}`.

- [ ] **Step 5: Route picked books through the same path**

Replace the `else` branch of `__DownloadPickedBooks` so picks share the skip logic.
Change the loop body's `else` from `Commands.GetBookOrBooks( revisionId, outputPath, False )` to:

```python
			else:
				try:
					Commands.__GetBook( revisionId, outputPath )
				except KoboException as e:
					print( colorama.Fore.LIGHTRED_EX + ( "Skipping book: %s" % e ) + colorama.Fore.RESET )
```

`__GetBook` with a directory target now consults the manifest, so picks are covered
without duplicating the rules.

- [ ] **Step 6: Verify the module imports and the help still runs**

Run: `python3 kobo-book-downloader --help`

Expected: the usage text prints with no traceback. This catches import errors,
indentation mistakes, and syntax errors before touching the network.

- [ ] **Step 7: Commit**

```bash
git add kobo-book-downloader/Commands.py
git commit -m "Skip already downloaded books using the manifest"
```

---

### Task 6: Verify against a real library and document the behavior

**Files:**
- Modify: `README.md`
- Modify: `.gitignore`

**Interfaces:**
- Consumes: everything above.
- Produces: no code interfaces. This task confirms real behavior and records it.

- [ ] **Step 1: Run against a real library twice**

Run, substituting a real output directory:

```bash
python3 kobo-book-downloader get /path/to/books/ --all
python3 kobo-book-downloader get /path/to/books/ --all
```

Expected: the first run downloads (or adopts, if books are already there); the second
run prints `Skipping already downloaded book ...` for every book and downloads nothing.

**This is the check that matters.** If the second run redownloads anything, the book
key is not stable — return to Task 1 and confirm the field name against the actual
response rather than adjusting anything downstream.

- [ ] **Step 2: Verify deletion causes a redownload**

```bash
ls /path/to/books/*.epub | head -1
```

Delete that one file, re-run the `--all` command, and confirm exactly that book is
downloaded again while the rest are skipped.

- [ ] **Step 3: Ignore the manifest in git**

Add to `.gitignore`:

```
.kobo-downloader.json
```

The manifest belongs to a book directory, not the repo, but this guards against a
library kept inside the working tree.

- [ ] **Step 4: Document the behavior in README.md**

Add after the usage examples, before the `## Notes` heading:

```markdown
## Download tracking

When downloading into a directory, kobo-book-downloader records what it has fetched
in a `.kobo-downloader.json` file in that directory, and skips books it has already
downloaded. Deleting a book from the directory causes it to be downloaded again on
the next run.

Books already present in the directory are adopted on the first run without being
redownloaded, as long as their filenames match the ones the program generates.

When Kobo publishes a new revision of a book you already have, the new revision is
saved alongside the original as `Title Revised YYYY-MM-DD.epub`. The original file is
never modified or deleted.

Naming an output file explicitly (`get /dir/book.epub <id>`) always downloads, and is
never skipped.
```

- [ ] **Step 5: Commit**

```bash
git add README.md .gitignore
git commit -m "Document download tracking behavior"
```

---

## Self-Review

**Spec coverage:**

| Spec section | Task |
|---|---|
| Manifest file, schema, atomic write | Task 2 |
| Skip decision rules 1–4, adoption | Task 4, verified Task 6 |
| Revisions, `Revised <date>`, `v2` collision | Task 3 (naming), Task 5 (wiring) |
| Explicit filenames are an override | Task 5, Step 4 |
| Structure — `Manifest.py`, `Commands.py` helpers | Tasks 2–5 |
| Error handling — corrupt manifest, save failure | Task 2, verified Step 3 |
| Error handling — missing key, missing date | Task 1 decision, Task 5 Step 1 fallbacks |
| Error handling — download failure leaves no entry | Task 5, Step 2 (`Record` after `Download`) |
| Testing — skip matrix | Tasks 2–4 inline checks, Task 6 live run |
| Open question — field names | Task 1 |

**Deviation from the skill's TDD default, stated plainly:** this repo has no test
framework, no test directory, and no test dependency. Rather than fabricate `pytest`
invocations that would fail on contact, Tasks 2–4 verify each increment by running
the real module through `python3 -c` with assertions. Every verification command in
this plan is runnable as written. Task 6 is the live check against a real library,
matching the spec's stated testing approach.

**Type consistency:** `IsDownloaded`, `GetEntry`, `Record`, and `MakeRevisedFileName`
carry the same signatures in Tasks 2–4 where they are defined and Task 5 where they
are consumed. `bookKey` is the parameter name throughout. Entry keys are `revisionId`,
`fileName`, `downloadedAt`, `revisionDate` in both `Record` and the spec's schema.

**Known ordering constraint:** Task 1 gates Task 5's field names. Tasks 2–4 do not
depend on Task 1 and can proceed while it is unresolved, since `Manifest.py` never
sees a Kobo response — it takes whatever key it is given.
