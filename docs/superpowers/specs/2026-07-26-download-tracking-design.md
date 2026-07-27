# Download Tracking Manifest — Design

Date: 2026-07-26

## Problem

`kobo-book-downloader get /dir/ --all` and `pick` redownload every selected book on
every run. There is no record of what has already been fetched, so re-running the
tool against an existing library re-pulls and overwrites the whole thing.

## Solution

A per-output-directory JSON manifest records what has been downloaded. The manifest
is authoritative for *which revision* was downloaded; the filesystem is authoritative
for *whether the file still exists*. A book is skipped only when both agree.

## Manifest File

Location: `<outdir>/.kobo-downloader.json`, where `<outdir>` is the directory given
on the command line.

```json
{
  "version": 1,
  "books": {
    "<EntitlementId>": {
      "revisionId": "01234567-89ab-cdef-0123-456789abcdef",
      "fileName": "Joseph Heller - Catch-22.epub",
      "downloadedAt": "2026-07-26T14:03:11Z",
      "revisionDate": "2024-11-02"
    }
  }
}
```

Entries are keyed on the stable entitlement/product ID rather than `RevisionId`, so a
revised book is recognized as the same book. `fileName` is relative to the manifest's
own directory, so the library stays portable when moved. `revisionDate` is Kobo's
date for the revision, stored as `YYYY-MM-DD`.

The file is written atomically (temp file in the same directory, then `os.replace`)
after each successful download. An interrupted batch therefore retains a correct
record of everything already pulled.

## Skip Decision

Applied per book for directory-target downloads (`get /dir/ --all`, `get /dir/ <id>`,
and `pick`). Evaluated in order:

1. **Not in manifest.** If the computed target filename already exists on disk, adopt
   it: write a manifest entry using the current library's `RevisionId` and skip the
   download. Otherwise download.
2. **In manifest, recorded file missing from disk.** Download. Deleting a book causes
   it to be fetched again.
3. **In manifest, file present, `RevisionId` matches.** Skip.
4. **In manifest, file present, `RevisionId` differs.** Download as a revision.

Rule 1 is what migrates an existing library. On the first run after this change, books
already on disk under their current computed names are adopted with no downloads. A
book whose filename does not match what the current sanitizer produces is downloaded
once, which is exactly today's behavior.

## Revisions

When Kobo reports a new `RevisionId` for a book already in the manifest, the new
revision is written to a **new file alongside the original**:

```
Joseph Heller - Catch-22.epub                    <- original, never modified
Joseph Heller - Catch-22 Revised 2024-11-02.epub <- new revision
```

The date is Kobo's revision date from the entitlement metadata, falling back to the
current date when that field is absent or unparseable. If the resulting name is
already taken — two revisions bearing the same date — ` v2` is appended, then ` v3`,
and so on. The unsuffixed name is reserved for the first file bearing that date.

The manifest entry is updated to point at the newest file and `RevisionId`. Superseded
files are never modified or deleted.

## Explicit Filenames Are An Override

`get /dir/book.epub <id>` names the output file explicitly. That form always downloads
to exactly that path and never consults the skip logic — an explicit request is treated
as an override. It still writes a manifest entry in `/dir/`.

## Structure

New module `kobo-book-downloader/Manifest.py`, following the conventions of
`Settings.py` (tab indentation, PascalCase methods, `Load`/`Save` pair). It knows only
about the manifest file — no Kobo API or download concerns.

```
Manifest( directoryPath )
  GetEntry( entitlementId )                              -> dict | None
  IsDownloaded( entitlementId, revisionId, fileName )    -> bool
  Record( entitlementId, revisionId, fileName, revisionDate )
  MakeRevisedFileName( baseName, revisionDate )          -> collision-free name
```

The on-disk existence check lives inside `IsDownloaded`, keeping the "a file on disk is
evidence of a completed download" rule in one place rather than duplicated across call
sites.

`Commands.py` changes:

- `__GetAllBooks` and `__DownloadPickedBooks` construct a `Manifest` for the output
  directory and consult it per book.
- `__GetBook` records to the manifest but skips only when the target was a directory.
- Two new statics beside `__GetBookAuthor` / `__IsBookArchived` extract the entitlement
  ID and the revision date, isolating the API field names.

## Error Handling

- **Corrupt or unreadable manifest** — warn, treat as empty, continue. A bad manifest
  degrades to current behavior and never blocks a download.
- **Manifest save failure** — warn, do not fail the run. The book is on disk; losing the
  record costs one redundant download later.
- **Missing entitlement ID in the API response** — fall back to using `RevisionId` as the
  key and note it. Revision tracking degrades for that book; skip-if-exists still works.
- **Missing or unparseable revision date** — fall back to the current date.
- **Download failure** — no manifest entry is written, so the book is retried on the next
  run. This composes with the existing per-book failure handling (commit `2b987a7`).

## Testing

The repository has no test suite, and this change does not justify introducing one. The
behavior is verified by hand against a real library, covering the skip matrix:

- fresh download writes a manifest entry
- an existing file with no manifest entry is adopted without downloading
- deleting a tracked file causes a redownload
- a changed `RevisionId` produces a `Revised <date>` file, original untouched
- a same-date collision produces a ` v2` suffix
- a corrupt manifest degrades to full-download behavior without crashing

## Open Question For Implementation

The field names for the entitlement ID and the revision date in the `library_sync`
response are assumptions — `Kobo.py` references neither. Before wiring up revision
detection, dump one entitlement's JSON and confirm the actual field names. If they are
absent, the documented fallbacks keep the tool working, but revision detection would
silently never fire, so this must be confirmed rather than assumed.
