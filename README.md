## kobo-book-downloader

With kobo-book-downloader you can download your purchased [Kobo](https://www.kobo.com/) books and remove the Digital Rights Management (DRM) protection from them. The resulting [EPUB](https://en.wikipedia.org/wiki/EPUB) files can be read with, amongst others, [KOReader](https://github.com/koreader/koreader). It can download your audiobooks too — see [Audiobooks](#audiobooks).

Unlike [obok.py](https://github.com/apprenticeharper/DeDRM_tools/blob/master/Other_Tools/Kobo/obok.py), kobo-book-downloader doesn't require any pre-downloading through a Kobo e-reader or application.

kobo-book-downloader is a command line program. It looks like this:

![Screenshot](https://raw.githubusercontent.com/TnS-hun/kobo-book-downloader/master/screenshot.png)

## Installation

kobo-book-downloader requires [Python 3+](https://www.python.org/). Make sure that you have it installed. You can verify it by running `python --version` from the terminal.

Use Git to clone this repository or [download it](https://github.com/TnS-hun/kobo-book-downloader/archive/master.zip) as a zip. If you downloaded it as a zip then you have to extract it.

From your terminal enter the directory where kobo-book-downloader is then run `pip install -r requirements.txt` to install its dependencies.

It has been tested on Linux but it should work on other platforms too.

## Usage

To interactively select from your unread books to download:
```
python kobo-book-downloader pick /dir/
```
To interactively select from all of your books to download:
```
python kobo-book-downloader pick /dir/ --all
```
To list your unread books:
```
python kobo-book-downloader list
```
To list all your books:
```
python kobo-book-downloader list --all
```
To download a book:
```
python kobo-book-downloader get /dir/book.epub 01234567-89ab-cdef-0123-456789abcdef
```
To download a book and name the file automatically:
```
python kobo-book-downloader get /dir/ 01234567-89ab-cdef-0123-456789abcdef
```
To download all your books:
```
python kobo-book-downloader get /dir/ --all
```
To list all your books from your wish list:
```
python kobo-book-downloader wishlist
```
To show the location of the program's configuration file:
```
python kobo-book-downloader info
```
Running the program without any arguments will show the help:
```
python kobo-book-downloader
```
To get additional help for the **list** command (it works for **get** and **pick** too):
```
python kobo-book-downloader list --help
```

## Audiobooks

The **get**, **list** and **pick** commands handle books unless you ask for audiobooks. Add `--audiobooks` for audiobooks instead of books, or `--all-formats` for both. Without either flag the commands behave exactly as before.

To list your audiobooks:
```
python kobo-book-downloader list --all --audiobooks
```
To download all of your audiobooks:
```
python kobo-book-downloader get /dir/ --all --audiobooks
```
To download everything, books and audiobooks alike:
```
python kobo-book-downloader get /dir/ --all --all-formats
```

An audiobook is delivered as a set of audio files rather than a single one, so each is downloaded into its own directory, named after the author and title:

```
/dir/Bridget E. Baker - Ensnared/
    001 - Opening Credits.mp3
    002 - Prologue.mp3
    003 - Chapter 1.mp3
    ...
    chapters.json
```

The `chapters.json` file lists each part with its chapter title and duration in seconds, keeping the full titles even where the file names had to be shortened.

Audiobooks are not DRM protected, so nothing has to be removed from them. Since the output is a directory, the output path for an audiobook must be a directory rather than a file name.

Individual parts already present are kept, so an interrupted download continues where it left off instead of fetching the whole audiobook again.

## Download tracking

When downloading into a directory, kobo-book-downloader records what it has fetched in a `.kobo-downloader.json` file in that directory, and skips books it has already downloaded.

The record is authoritative: once a book has been downloaded it is never fetched again, **even if you move, rename or delete the file**. This is deliberate, so that filing books away into a library elsewhere doesn't cause them to be redownloaded on every run.

Books already present in the directory are adopted on the first run without being redownloaded, as long as their filenames match the ones the program generates.

To download books again regardless of the record, pass `--redownload` to `get` or `pick`. It overwrites the existing files in place:
```
python kobo-book-downloader get /dir/ --all --redownload
```

When Kobo publishes a new revision of a book you already have, the new revision is saved alongside the original as `Title Revised YYYY-MM-DD.epub`. The original file is never modified or deleted.

Naming an output file explicitly (`get /dir/book.epub <id>`) always downloads, and is never skipped.

## Notes

kobo-book-downloader uses the same web-based activation method to login as the Kobo e-readers. You will have to open an activation link -- that uses the official [Kobo](https://www.kobo.com/) site -- in your browser and enter the code, then you might need to login too if kobo.com asks you to. Once kobo-book-downloader has successfully logged in, it won't ask for the activation again. kobo-book-downloader doesn't store your Kobo password in any form, it works with access tokens.

The program was made out of frustration with my workflow (purchase book on Kobo, turn on WiFi on the router, exit from KOReader, start Nickel from the Kobo start menu, turn on WiFi on the Kobo e-reader, wait till the downloading and other syncing finishes, turn off the WiFi on the e-reader, turn off the WiFi on the router, connect the e-reader via USB, run obok.py, copy the book to the e-reader, power off the e-reader, start KOReader, and finally start reading).

The DRM removal code is based on Physisticated's [obok.py](https://github.com/apprenticeharper/DeDRM_tools/blob/master/Other_Tools/Kobo/obok.py). Thank you!
