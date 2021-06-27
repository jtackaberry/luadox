import os
from datetime import datetime
from zipfile import ZipFile

def abspath_to_zippath(path):
    """
    If the program is being executed from a bundled zip, returns the given path (which
    is converted to an absolute path) as a relative path from the zip bundle, which can
    then be passed to get_file_from_zip().

    If not being run from a zip bundle, then None is returned.
    """
    path = os.path.abspath(path)
    try:
        zippath = abspath_to_zippath.zippath
    except AttributeError:
        try:
            zippath = abspath_to_zippath.zippath = os.path.abspath(__loader__.archive)
        except AttributeError:
            zippath = abspath_to_zippath.zippath = None

    if zippath and path.startswith(zippath + os.path.sep):
        relpath = path[len(zippath) + 1:]
        # Zip path separator is always /, even on Windows.
        return relpath.replace(os.path.sep, '/')


def get_file_from_zip(path, filename, newerthan=None):
    """
    Returns file metadata and a file handle to a file from within the zip bundle.
    The filename is read relative to the path, where path is expected to be returned
    by abspath_to_zippath().

    If newerthan is not None, then it's a unix timestamp and the file handle will
    not be returned unless the bundled file's mtime is newer than this timestamp.
    (File metadata will still be returned, but the file won't be opened.)  This is
    useful for cache freshness tests.

    :returns: a 3-tuple consisting of zipfile.ZipInfo instance, datetime instance of file
        mtime, and the file object of the opened file or None if newerthan is defined and
        the file is older than this timestamp
    :raises: FileNotFoundError if the requested file is not contained in the zip bundle
    """
    try:
        zipfile, files = get_file_from_zip.zipfile, get_file_from_zip.files
    except AttributeError:
        get_file_from_zip.zipfile = zipfile = ZipFile(__loader__.archive)
        get_file_from_zip.files = files = dict((i.filename, i) for i in get_file_from_zip.zipfile.infolist())

    # Don't use os.path.join here because zipfile uses / as a path sep even on
    # Windows.
    filename = (path.rstrip(os.path.sep) + '/' + filename).replace(os.path.sep, '/')
    if filename not in files:
        raise FileNotFoundError

    info = files[filename]
    mtime = datetime(*info.date_time)
    if newerthan and mtime < datetime.utcfromtimestamp(newerthan):
        return info, mtime, None
    else:
        return info, mtime, zipfile.open(filename)


def get_asset_contents(fname):
    """
    Returns the contents of a file in the assets directory, which works whether the
    program is running from a zip bundle or directly from filesystem.
    """
    assets_dir = os.path.join(os.path.dirname(__file__), '../assets')
    path = abspath_to_zippath(assets_dir)
    if path:
        _, _, f = get_file_from_zip(path, fname)
        return f.read().decode('utf8')
    else:
        f = open(os.path.join(assets_dir, fname))
        return f.read()