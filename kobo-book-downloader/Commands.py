from Globals import Globals
from Kobo import Kobo, KoboException
from Manifest import Manifest

import colorama

import datetime
import json
import os

class Commands:
	# It wasn't possible to format the main help message to my liking, so using a custom one.
	# This was the most annoying:
	#
	# commands:
	#   command <-- absolutely unneeded text
	#     get     List unread books
	#     list    Get book
	#
	# See https://stackoverflow.com/questions/13423540/ and https://stackoverflow.com/questions/11070268/
	@staticmethod
	def ShowUsage():
		usage = \
"""Kobo book downloader and DRM remover

Usage:
  kobo-book-downloader [--help] command ...

Commands:
  get      Download book
  info     Show the location of the configuration file
  list     List your books
  pick     Download books using interactive selection
  wishlist List your wish listed books

Optional arguments:
  -h, --help      Show this help message and exit
  --verbose       Print debugging information
  --audiobooks    Work with audiobooks instead of books (get, list and pick)
  --all-formats   Work with both books and audiobooks (get, list and pick)

Books and audiobooks:
  get, list and pick handle books unless told otherwise. Audiobooks are downloaded as a
  directory per book, holding the audio files and a chapters.json listing the chapters.

Examples:
  kobo-book-downloader get /dir/book.epub 01234567-89ab-cdef-0123-456789abcdef   Download book
  kobo-book-downloader get /dir/ 01234567-89ab-cdef-0123-456789abcdef            Download book and name the file automatically
  kobo-book-downloader get /dir/ --all                                           Download all your books
  kobo-book-downloader get /dir/ --all --redownload                              Download all your books again, ignoring the download record
  kobo-book-downloader get /dir/ --all --audiobooks                              Download all your audiobooks
  kobo-book-downloader get /dir/ --all --all-formats                             Download all your books and audiobooks
  kobo-book-downloader info                                                      Show the location of the program's configuration file
  kobo-book-downloader list                                                      List your unread books
  kobo-book-downloader list --all                                                List all your books
  kobo-book-downloader list --all --audiobooks                                   List all your audiobooks
  kobo-book-downloader list --help                                               Get additional help for the list command (it works for get and pick too)
  kobo-book-downloader pick /dir/                                                Interactively select unread books to download
  kobo-book-downloader pick /dir/ --all                                          Interactively select books to download
  kobo-book-downloader pick /dir/ --audiobooks                                   Interactively select audiobooks to download
  kobo-book-downloader wishlist                                                  List your wish listed books"""

		print( usage )

	@staticmethod
	def __GetBookAuthor( book: dict ) -> str:
		# The "book" endpoint omits this field entirely for some products, unlike
		# library_sync which always supplies it.
		contributors = book.get( "ContributorRoles" ) or []

		authors = []
		for contributor in contributors:
			role = contributor.get( "Role" )
			if role == "Author":
				authors.append( contributor[ "Name" ] )

		# Unfortunately the role field is not filled out in the data returned by the "library_sync" endpoint, so we only
		# use the first author and hope for the best. Otherwise we would get non-main authors too. For example Christopher
		# Buckley beside Joseph Heller for the -- terrible -- novel Catch-22.
		if len( authors ) == 0 and len( contributors ) > 0:
			authors.append( contributors[ 0 ][ "Name" ] )

		return " & ".join( authors )

	# "books" (the default) keeps the original behaviour of every existing invocation,
	# "audiobooks" selects only audiobooks, "all" takes both.
	@staticmethod
	def __MatchesFormatFilter( isAudiobook: bool, formatFilter: str ) -> bool:
		if formatFilter == "all":
			return True

		if formatFilter == "audiobooks":
			return isAudiobook

		return not isAudiobook

	@staticmethod
	def GetFormatFilter( audiobooksOnly: bool, allFormats: bool ) -> str:
		if allFormats:
			return "all"

		if audiobooksOnly:
			return "audiobooks"

		return "books"

	# library_sync returns audiobooks alongside books, under their own keys. Everything
	# downstream works off the metadata dictionary, so this is the only place that has
	# to know which of the two an entitlement is.
	@staticmethod
	def __GetEntitlementContent( newEntitlement: dict ):
		bookMetadata = newEntitlement.get( "BookMetadata" )
		if bookMetadata is not None:
			return bookMetadata, False

		audiobookMetadata = newEntitlement.get( "AudiobookMetadata" )
		if audiobookMetadata is not None:
			return audiobookMetadata, True

		return None, False

	@staticmethod
	def __IsAudiobookEntitlement( newEntitlement: dict ) -> bool:
		return newEntitlement.get( "AudiobookMetadata" ) is not None

	# Books and audiobooks keep their entitlement under differently named keys.
	@staticmethod
	def __GetEntitlement( newEntitlement: dict ) -> dict:
		entitlement = newEntitlement.get( "BookEntitlement" )
		if entitlement is not None:
			return entitlement

		return newEntitlement.get( "AudiobookEntitlement" ) or {}

	@staticmethod
	def __SanitizeFileName( fileName: str ) -> str:
		result = ""
		for c in fileName:
			if c.isalnum() or " ,;.!(){}[]#$'-+@_".find( c ) >= 0:
				result += c

		result = result.strip( " ." )
		result = result[ :100 ] # Limit the length -- mostly because of Windows. It would be better to do it on the full path using MAX_PATH.
		return result

	@staticmethod
	def __MakeFileNameForBook( book: dict ) -> str:
		fileName = ""

		author = Commands.__GetBookAuthor( book )
		if len( author ) > 0:
			fileName = author + " - "

		fileName += book[ "Title" ]
		fileName = Commands.__SanitizeFileName( fileName )
		fileName += ".epub"

		return fileName

	# The book equivalent appends ".epub"; an audiobook becomes a directory of parts,
	# so the sanitized name is used as-is.
	@staticmethod
	def __MakeDirectoryNameForAudiobook( audiobook: dict ) -> str:
		directoryName = ""

		author = Commands.__GetBookAuthor( audiobook )
		if len( author ) > 0:
			directoryName = author + " - "

		directoryName += audiobook[ "Title" ]
		directoryName = Commands.__SanitizeFileName( directoryName )

		# Sanitizing can strip a title down to nothing, and an empty directory name
		# would write the parts straight into the output directory.
		if len( directoryName ) == 0:
			directoryName = "Audiobook"

		return directoryName

	# Chapter titles come from the manifest's Navigation list, whose PartId indexes into
	# the Spine. Most audiobooks number the parts from zero, but some number them from
	# one, which would shift every title onto the wrong part. Detect that by looking at
	# the range actually used rather than trusting either convention.
	@staticmethod
	def __GetAudiobookChapterTitles( manifest: dict, partCount: int ) -> dict:
		navigation = manifest.get( "Navigation" ) or []

		partIds = [ navigationItem.get( "PartId" ) for navigationItem in navigation ]
		partIds = [ partId for partId in partIds if isinstance( partId, int ) ]

		# One-based only if nothing refers to part zero and something refers to the part
		# one past the end -- both true together mean the whole list is shifted up by one.
		offset = 0
		if len( partIds ) > 0 and min( partIds ) == 1 and max( partIds ) == partCount:
			offset = 1

		titles = {}
		for navigationItem in navigation:
			partId = navigationItem.get( "PartId" )
			title = navigationItem.get( "Title" )
			if isinstance( partId, int ) and title:
				titles[ partId - offset ] = title

		return titles

	@staticmethod
	def __MakeAudiobookPartFileName( index: int, chapterTitles: dict, partCount: int ) -> str:
		# Zero padded so the parts sort correctly in any file browser or player.
		digits = max( 3, len( "%d" % partCount ) )
		number = str( index + 1 ).rjust( digits, "0" )

		title = Commands.__SanitizeFileName( chapterTitles.get( index, "" ) )
		if len( title ) > 0:
			return "%s - %s.mp3" % ( number, title )

		return "%s.mp3" % number

	# The sidecar keeps the chapter data that the file names can't carry -- durations,
	# and the original titles before sanitizing removed characters from them.
	@staticmethod
	def __WriteAudiobookChapters( manifest: dict, outputDirectoryPath: str, chapterTitles: dict ) -> None:
		chapters = []
		spine = manifest.get( "Spine" ) or []

		for index, spineItem in enumerate( spine ):
			chapters.append( {
				"part": index + 1,
				"title": chapterTitles.get( index, "" ),
				"duration": spineItem.get( "Duration" ),
				"fileName": Commands.__MakeAudiobookPartFileName( index, chapterTitles, len( spine ) ),
			} )

		filePath = os.path.join( outputDirectoryPath, "chapters.json" )

		try:
			with open( filePath, "w", encoding = "utf-8" ) as f:
				f.write( json.dumps( chapters, indent = 4 ) )
		except OSError as e:
			# The audio is already on disk; losing the sidecar must not fail the run.
			print( "Warning: could not write '%s' (%s)." % ( filePath, e ) )

	@staticmethod
	def __DownloadAudiobookTracked( manifest: Manifest, newEntitlement: dict, audiobookMetadata: dict, outputPath: str, redownload: bool = False ) -> None:
		revisionId = audiobookMetadata[ "RevisionId" ]
		directoryName = Commands.__MakeDirectoryNameForAudiobook( audiobookMetadata )
		bookKey = Commands.__GetBookKey( newEntitlement, audiobookMetadata, directoryName )

		if not redownload and manifest.IsDownloaded( bookKey, revisionId, directoryName ):
			print( colorama.Style.DIM + ( "Skipping already downloaded audiobook '%s'." % directoryName ) + colorama.Style.RESET_ALL )
			return

		revisionDate = Commands.__GetRevisionDate( newEntitlement, audiobookMetadata )

		# A revised audiobook goes into its own directory, leaving the existing one
		# untouched, exactly as a revised book becomes a separate file.
		entry = manifest.GetEntry( bookKey )
		if not redownload and entry is not None and entry.get( "fileName" ):
			directoryName = manifest.MakeRevisedFileName( entry[ "fileName" ], revisionDate, False )

		outputDirectoryPath = os.path.join( outputPath, directoryName )
		print( "Downloading audiobook to '%s'." % outputDirectoryPath )

		manifestUrl = Kobo.GetAudiobookManifestUrl( audiobookMetadata )
		audiobookManifest = Globals.Kobo.GetAudiobookManifest( manifestUrl )
		partCount = len( audiobookManifest.get( "Spine" ) or [] )
		chapterTitles = Commands.__GetAudiobookChapterTitles( audiobookManifest, partCount )

		makePartFileName = lambda index, spineItem: Commands.__MakeAudiobookPartFileName( index, chapterTitles, partCount )

		downloadedPartCount = Globals.Kobo.DownloadAudiobook( audiobookManifest, outputDirectoryPath, audiobookMetadata[ "Title" ], makePartFileName )
		Commands.__WriteAudiobookChapters( audiobookManifest, outputDirectoryPath, chapterTitles )

		print( "Downloaded %d part(s)." % downloadedPartCount )
		manifest.Record( bookKey, revisionId, directoryName, revisionDate )

	@staticmethod
	def __IsBookArchived( newEntitlement: dict ) -> bool:
		bookEntitlement = newEntitlement.get( "BookEntitlement" )
		if bookEntitlement is None:
			return False

		isRemoved = bookEntitlement.get( "IsRemoved" )
		if isRemoved is None:
			return False

		return isRemoved

	@staticmethod
	def __GetBookKey( newEntitlement: dict, bookMetadata: dict, fileName: str ) -> str:
		# Keyed on an identifier that is stable across revisions, so a revised book
		# is recognized as the same book rather than a new one. The file name is the
		# last resort: keying on RevisionId would make every revision look like a new
		# book and download it again, which is the bug the manifest exists to fix.
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
		# today when absent -- revision detection still works, the label is just the
		# date we noticed rather than the date it changed.
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

	@staticmethod
	def __DownloadTracked( manifest: Manifest, newEntitlement: dict, bookMetadata: dict, outputPath: str, redownload: bool = False, revisionId: str = None ) -> None:
		# The "book" endpoint returns product metadata with no RevisionId, so a
		# single-book download passes in the id it was invoked with. library_sync
		# does include it, which is why --all can rely on the metadata.
		if revisionId is None:
			revisionId = bookMetadata[ "RevisionId" ]

		fileName = Commands.__MakeFileNameForBook( bookMetadata )
		bookKey = Commands.__GetBookKey( newEntitlement, bookMetadata, fileName )

		# --redownload overwrites in place: the point is to replace a file that is
		# suspected bad, so it deliberately skips the revised-name path below.
		if redownload:
			outputFilePath = os.path.join( outputPath, fileName )
			print( "Redownloading book to '%s'." % outputFilePath )
			Globals.Kobo.Download( revisionId, Kobo.DisplayProfile, outputFilePath )
			manifest.Record( bookKey, revisionId, fileName, Commands.__GetRevisionDate( newEntitlement, bookMetadata ) )
			return

		if manifest.IsDownloaded( bookKey, revisionId, fileName ):
			print( colorama.Style.DIM + ( "Skipping already downloaded book '%s'." % fileName ) + colorama.Style.RESET_ALL )
			return

		revisionDate = Commands.__GetRevisionDate( newEntitlement, bookMetadata )
		entry = manifest.GetEntry( bookKey )

		# A book we already have under a different revision becomes a new file
		# alongside the original, which is never modified or deleted. Based on the
		# manifest rather than on disk: the earlier file may have been moved away,
		# and reusing its name would silently overwrite it if it comes back.
		if entry is not None and entry.get( "fileName" ):
			fileName = manifest.MakeRevisedFileName( entry[ "fileName" ], revisionDate )

		outputFilePath = os.path.join( outputPath, fileName )
		print( "Downloading book to '%s'." % outputFilePath )
		Globals.Kobo.Download( revisionId, Kobo.DisplayProfile, outputFilePath )

		manifest.Record( bookKey, revisionId, fileName, revisionDate )

	# A revision id on its own doesn't say whether it belongs to a book or an
	# audiobook, and the "book" endpoint only answers for books. Look it up in the
	# library so that "get <id>" works for either without the user having to say which.
	@staticmethod
	def __FindAudiobookEntitlement( revisionId: str ):
		for entitlement in Globals.Kobo.GetMyBookList():
			newEntitlement = entitlement.get( "NewEntitlement" )
			if newEntitlement is None:
				continue

			audiobookMetadata = newEntitlement.get( "AudiobookMetadata" )
			if audiobookMetadata is None:
				continue

			if audiobookMetadata.get( "RevisionId" ) == revisionId:
				return newEntitlement, audiobookMetadata

		return None, None

	# isAudiobook is passed in by callers that already know (pick, which has just listed
	# the library); None means look it up.
	@staticmethod
	def __GetBook( revisionId: str, outputPath: str, redownload: bool = False, isAudiobook = None ) -> None:
		if os.path.isdir( outputPath ):
			newEntitlement, audiobookMetadata = ( None, None )
			if isAudiobook is not False:
				newEntitlement, audiobookMetadata = Commands.__FindAudiobookEntitlement( revisionId )

			manifest = Manifest( outputPath )

			if audiobookMetadata is not None:
				Commands.__DownloadAudiobookTracked( manifest, newEntitlement, audiobookMetadata, outputPath, redownload )
				return

			book = Globals.Kobo.GetBookInfo( revisionId )
			Commands.__DownloadTracked( manifest, {}, book, outputPath, redownload, revisionId )
			return

		# An audiobook is a directory of parts, so there is no single file to write and
		# no sensible way to honour an explicit file name.
		if isAudiobook or ( isAudiobook is None and Commands.__FindAudiobookEntitlement( revisionId )[ 1 ] is not None ):
			raise KoboException( "'%s' is an audiobook, which is downloaded as a directory of audio files. Give an existing directory as the output path instead of a file name." % revisionId )

		# An explicit file name is an override: download exactly what was asked for.
		parentPath = os.path.dirname( outputPath )
		if not os.path.isdir( parentPath ):
			raise KoboException( "The parent directory ('%s') of the output file must exist." % parentPath )

		print( "Downloading book to '%s'." % outputPath )
		Globals.Kobo.Download( revisionId, Kobo.DisplayProfile, outputPath )

	@staticmethod
	def __GetAllBooks( outputPath: str, redownload: bool = False, formatFilter: str = "books" ) -> None:
		if not os.path.isdir( outputPath ):
			raise KoboException( "The output path must be a directory when downloading all books." )

		bookList = Globals.Kobo.GetMyBookList()
		manifest = Manifest( outputPath )
		failureCount = 0

		for entitlement in bookList:
			newEntitlement = entitlement.get( "NewEntitlement" )
			if newEntitlement is None:
				continue

			bookMetadata, isAudiobook = Commands.__GetEntitlementContent( newEntitlement )
			if bookMetadata is None:
				continue

			if not Commands.__MatchesFormatFilter( isAudiobook, formatFilter ):
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
				if isAudiobook:
					Commands.__DownloadAudiobookTracked( manifest, newEntitlement, bookMetadata, outputPath, redownload )
				else:
					Commands.__DownloadTracked( manifest, newEntitlement, bookMetadata, outputPath, redownload )
			except KoboException as e:
				# One unavailable book (a sample, a withdrawn title) shouldn't abort the whole library.
				failureCount += 1
				print( colorama.Fore.LIGHTRED_EX + ( "Skipping book: %s" % e ) + colorama.Fore.RESET )

		if failureCount > 0:
			print( colorama.Fore.LIGHTYELLOW_EX + ( "%d book(s) could not be downloaded, see the messages above." % failureCount ) + colorama.Fore.RESET )

	@staticmethod
	def GetBookOrBooks( revisionId: str, outputPath: str, getAll: bool, redownload: bool = False, formatFilter: str = "books" ) -> None:
		revisionIdIsSet = ( revisionId is not None ) and len( revisionId ) > 0

		if getAll:
			if revisionIdIsSet:
				raise KoboException( "Got unexpected book identifier parameter ('%s')." % revisionId )

			Commands.__GetAllBooks( outputPath, redownload, formatFilter )
		else:
			if not revisionIdIsSet:
				raise KoboException( "Missing book identifier parameter. Did you mean to use the --all parameter?" )

			Commands.__GetBook( revisionId, outputPath, redownload )

	@staticmethod
	def __IsBookRead( newEntitlement: dict ) -> bool:
		readingState = newEntitlement.get( "ReadingState" )
		if readingState is None:
			return False

		statusInfo = readingState.get( "StatusInfo" )
		if statusInfo is None:
			return False

		status = statusInfo.get( "Status" )
		return status == "Finished"

	@staticmethod
	def __GetBookList( listAll: bool, formatFilter: str = "books" ) -> list:
		bookList = Globals.Kobo.GetMyBookList()
		rows = []

		for entitlement in bookList:
			newEntitlement = entitlement.get( "NewEntitlement" )
			if newEntitlement is None:
				continue

			bookMetadata, isAudiobook = Commands.__GetEntitlementContent( newEntitlement )
			if bookMetadata is None:
				continue

			if not Commands.__MatchesFormatFilter( isAudiobook, formatFilter ):
				continue

			bookEntitlement = Commands.__GetEntitlement( newEntitlement )
			if len( bookEntitlement ) == 0:
				continue

			# Skip saved previews.
			if bookEntitlement.get( "Accessibility" ) == "Preview":
				continue

			# Skip refunded books.
			if bookEntitlement.get( "IsLocked" ):
				continue

			if ( not listAll ) and Commands.__IsBookRead( newEntitlement ):
				continue

			book = [ bookMetadata[ "RevisionId" ],
				bookMetadata[ "Title" ],
				Commands.__GetBookAuthor( bookMetadata ),
				Commands.__IsBookArchived( newEntitlement ),
				isAudiobook ]
			rows.append( book )

		rows = sorted( rows, key = lambda columns: columns[ 1 ].lower() )
		return rows

	@staticmethod
	def ListBooks( listAll: bool, formatFilter: str = "books" ) -> None:
		rows = Commands.__GetBookList( listAll, formatFilter )
		for columns in rows:
			revisionId = colorama.Style.DIM + columns[ 0 ] + colorama.Style.RESET_ALL
			title = colorama.Style.BRIGHT + columns[ 1 ] + colorama.Style.RESET_ALL

			author = columns[ 2 ]
			if len( author ) > 0:
				title += " by " + author

			archived = columns[ 3 ]
			if archived:
				title += colorama.Fore.LIGHTYELLOW_EX + " (archived)" + colorama.Fore.RESET

			print( "%s \t %s" % ( revisionId, title ) )

	@staticmethod
	def __ListBooksToPickFrom( rows: list ) -> None:
		longestIndex = len( "%d" % len( rows ) )

		for index, columns in enumerate( rows ):
			alignedIndexText = str( index + 1 ).rjust( longestIndex, ' ' )

			title = colorama.Style.BRIGHT + columns[ 1 ] + colorama.Style.RESET_ALL

			author = columns[ 2 ]
			if len( author ) > 0:
				title += " by " + author

			archived = columns[ 3 ]
			if archived:
				title += colorama.Fore.LIGHTYELLOW_EX + " (archived)" + colorama.Fore.RESET

			print( "%s. %s" % ( alignedIndexText, title ) )

	@staticmethod
	def __GetPickedBookRows( rows: list ) -> list:
		print( """\nEnter the number of the book(s) to download. Use comma or space to list multiple. Enter "all" to download all of them.""" )
		indexText = input( "Books: " )

		if indexText == "all":
			return rows

		indexList = indexText.replace( " ", "," ).split( "," )
		rowsToDownload = []

		for indexText in indexList:
			try:
				index = int( indexText.strip() ) - 1
				if index >= 0 and index < len( rows ):
					rowsToDownload.append( rows[ index ] )
			except Exception:
				pass

		return rowsToDownload

	@staticmethod
	def __DownloadPickedBooks( outputPath: str, rows: list, redownload: bool = False ) -> None:
		for columns in rows:
			revisionId = columns[ 0 ]
			title = columns[ 1 ]
			author = columns[ 2 ]
			archived = columns[ 3 ]
			isAudiobook = columns[ 4 ]

			if archived:
				if len( author ) > 0:
					title += " by " + author

				print( colorama.Fore.LIGHTYELLOW_EX + ( "Skipping archived book %s." % title ) + colorama.Fore.RESET )
			else:
				try:
					Commands.__GetBook( revisionId, outputPath, redownload, isAudiobook )
				except KoboException as e:
					print( colorama.Fore.LIGHTRED_EX + ( "Skipping book: %s" % e ) + colorama.Fore.RESET )

	@staticmethod
	def PickBooks( outputPath: str, listAll: bool, redownload: bool = False, formatFilter: str = "books" ) -> None:
		rows = Commands.__GetBookList( listAll, formatFilter )
		Commands.__ListBooksToPickFrom( rows )
		rowsToDownload = Commands.__GetPickedBookRows( rows )
		Commands.__DownloadPickedBooks( outputPath, rowsToDownload, redownload )

	@staticmethod
	def ListWishListedBooks() -> None:
		rows = []

		wishList = Globals.Kobo.GetMyWishList()
		for wishListEntry in wishList:
			productMetadata = wishListEntry.get( "ProductMetadata" )
			if productMetadata is None:
				continue

			book = productMetadata.get( "Book" )
			if book is None:
				continue

			title = colorama.Style.BRIGHT + book[ "Title" ] + colorama.Style.RESET_ALL
			author = Commands.__GetBookAuthor( book )
			isbn = book.get( "ISBN", "" )

			row = title
			if len( author ) > 0:
				row += " by " + author
			if len( isbn ) > 0:
				row += " (ISBN: %s)" % isbn

			rows.append( row )

		rows = sorted( rows, key = lambda row: row.lower() )
		print( "\n".join( rows ) )

	@staticmethod
	def Info():
		print( "The configuration file is located at:\n%s" % Globals.Settings.SettingsFilePath )
