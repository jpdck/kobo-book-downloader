import datetime
import json
import os
import tempfile

# Tracks which books have already been downloaded into a directory, so that
# re-running the program doesn't fetch the whole library again.
#
# The manifest is authoritative: once a book is recorded it is never downloaded
# again, regardless of whether the file is still on disk. Only a new revision, or
# an explicit --redownload, fetches a book a second time. The file system is
# consulted just once, to adopt files left by an earlier version of this tool.
#
# Books are keyed on CrossRevisionId, confirmed present on every entitlement in a
# live library_sync response. See Commands.__GetBookKey -- this module takes
# whatever key it is given and never looks at a Kobo response itself.

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

		# Rule 2: tracked. The manifest alone decides -- a book recorded as
		# downloaded is never fetched again, even if the file has since been moved,
		# renamed or deleted. Deliberate: the common case for a missing file is that
		# it was filed away into a library elsewhere, and redownloading it would
		# undo that every run. Use --redownload to force a fresh copy.
		#
		# Rule 3 still downloads a new revision, which is a different file rather
		# than a second copy of this one.
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
