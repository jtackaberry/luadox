# Copyright 2021-2023 Jason Tackaberry
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import sys

# First order of business is to ensure we are running a compatible version of Python.
if sys.hexversion < 0x03080000:
    print('FATAL: Python 3.8 or later is required.')
    sys.exit(1)

import os
import re
import argparse
import shlex
import glob
import locale
from configparser import ConfigParser
from typing import Generator, Union, Dict, Tuple, Set

from .log import log
from .parse import *
from .prerender import Prerenderer
from .render import RENDERERS

try:
    # version.py is generated at build time, so we are running from the proper
    # distribution.
    from .version import __version__  # pyright: ignore
except ImportError:
    # Running from local tree, use dummy value.
    __version__ = 'x.x.x-dev'

# A type used for mapping a user-defined Lua module name to a set of paths (or glob
# expressions).  The module name is split on '.' so the dict key is a tuple, but the
# modulie name can also be None if the user didn't provide any explicit module name, in
# which case the module name will be inferred.
BasePathsType = Dict[Union[Tuple[str, ...], None], Set[str]]

class FullHelpParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        sys.stderr.write('error: %s\n' % message)
        self.print_help()
        sys.exit(2)


def get_file_by_module(module, bases: BasePathsType) -> Union[str, None]:
    """
    Attempts to discover the lua source file for the given module name that was
    required relative to the given base paths.

    If the .lua file was found, its full path is returned, otherwise None is
    returned.
    """
    modparts = module.split('.')
    for aliasparts, paths in bases.items():
        alias_matches = modparts[:len(aliasparts)] == list(aliasparts) if aliasparts else False
        for base in paths:
            if alias_matches and aliasparts is not None:
                # User-defined module name for this path matches the requested
                # module name.  Strip away the intersecting components of the
                # module name and check this path for what's left.  For example,
                # if we're loading foo.bar, alias=foo, base=../src, then we
                # check ../src/bar.lua.
                remaining = modparts[len(aliasparts):]
                p = os.path.join(base, *remaining) + '.lua'
                if os.path.exists(p):
                    return os.path.abspath(p)
            # No module name alias, or alias didn't match the requested module.
            # First treat the requested module as immediately subordinate to
            # the given path.  For example, we're loading foo.bar and base is
            # ../src, then we check ../src/foo/bar.lua
            p = os.path.join(base, *modparts) + '.lua'
            if os.path.exists(p):
                return os.path.abspath(p)
            # Next check to see if the first component of the module name is
            # the same as the base directory name and if so strip it off and
            # look for remaining.  For example, we're loading foo.bar and base is
            # ../foo, then we check ../foo/bar.lua
            baseparts = os.path.split(base)
            if modparts[0] == baseparts[-1]:
                p = os.path.join(base, *modparts[1:]) + '.lua'
                if os.path.exists(p):
                    return p


def crawl(parser: Parser, path: str, follow: bool, seen: Set[str], bases: BasePathsType, encoding: str) -> None:
    """
    Parses all Lua source files starting with the given path and recursively
    crawling all files referenced in the code via 'require' statements.
    """
    if os.path.isdir(path):
        # Passing a directory implies follow
        follow = True
        path = os.path.join(path, 'init.lua')
        if not os.path.exists(path):
            log.critical('directory given, but %s does not exist', path)
            sys.exit(1)
    path = os.path.abspath(path)
    if path in seen:
        return
    seen.add(path)
    log.info('parsing %s', path)
    requires = parser.parse_source(open(path, encoding=encoding))
    if follow:
        for r in requires:
            newpath = get_file_by_module(r, bases)
            if not newpath:
                log.error('could not discover source file for module %s', r)
            else:
                crawl(parser, newpath, follow, seen, bases, encoding)


def get_config(args: argparse.Namespace) -> ConfigParser:
    """
    Consolidates command line arguments and config file, returning a ConfigParser
    instance that has the reconciled configuration such that command line arguments
    take precedence
    """
    config = ConfigParser(inline_comment_prefixes='#')
    config.add_section('project')
    config.add_section('manual')
    if args.config:
        if not os.path.exists(args.config):
            log.fatal('config file "%s" does not exist', args.config)
            sys.exit(1)
        config.read_file(open(args.config))
    if args.files:
        config.set('project', 'files', '\n'.join(args.files))
    if args.nofollow:
        config.set('project', 'follow', 'false')
    for prop in ('name', 'out', 'css', 'favicon', 'encoding', 'hometext', 'renderer'):
        if getattr(args, prop):
            config.set('project', prop, getattr(args, prop))
    if args.manual:
        for spec in args.manual:
            id, fname = spec.split('=')
            config.set('manual', id, fname)
    return config


def get_files(config: ConfigParser) -> Generator[Tuple[str, str], None, None]:
    """
    Generates the files/directories to parse based on config.
    """
    filelines = config.get('project', 'files', fallback='').strip().splitlines()
    for line in filelines:
        for spec in shlex.split(line):
            for modalias, globexpr in re.findall(r'(?:([^/\\]+)=)?(.*)', spec):
                for fname in glob.glob(globexpr):
                    yield modalias, fname


def main():
    global config
    renderer_names = ', '.join(RENDERERS)
    p = FullHelpParser(prog='luadox')
    p.add_argument('-c', '--config', type=str, metavar='FILE',
                   help='Luadox configuration file')
    p.add_argument('-n', '--name', action='store', type=str, metavar='NAME',
                   help='Project name (default Lua Project)')
    p.add_argument('--hometext', action='store', type=str, metavar='TEXT',
                   help='Home link text on the top left of every page')
    p.add_argument('-r', '--renderer', action='store', type=str, metavar='TYPE',
                   help=f'How to render the parsed content: {renderer_names} '
                   '(default: html)')
    p.add_argument('-o', '--out', action='store', type=str, metavar='PATH',
                   help='Target path for rendered files, with directories created '
                   'if necessary. For single-file renderers (e.g. json), this is '
                   ' treated as a file path if it ends with the appropriate extension '
                   '(e.g. .json) (default: ./out/ for multi-file renderers, or '
                   'luadox.<someext> for single-file renderers)')
    p.add_argument('-m', '--manual', action='store', type=str, metavar='ID=FILENAME', nargs='*',
                   help='Add manual page in the form id=filename.md')
    p.add_argument('--css', action='store', type=str, metavar='FILE',
                   help='Custom CSS file')
    p.add_argument('--favicon', action='store', type=str, metavar='FILE',
                   help='Path to favicon file')
    p.add_argument('--nofollow', action='store_true',
                   help="Disable following of require()'d files (default false)")
    p.add_argument('--encoding', action='store', type=str, metavar='CODEC', default=None,
                   help='Character set codec for input (default {})'.format(locale.getpreferredencoding()))
    p.add_argument('files', type=str, metavar='[MODNAME=]FILE', nargs='*',
                   help='List of files to parse or directories to crawl with optional module name alias')
    p.add_argument('--version', action='version', version='%(prog)s ' + __version__)

    args = p.parse_args()
    config = get_config(args)
    files = list(get_files(config))
    if not files:
        # Files are mandatory
        log.critical('no input files or directories specified on command line or config file')
        sys.exit(1)

    renderer = config.get('project', 'renderer', fallback='html')
    try:
        rendercls = RENDERERS[renderer]
    except KeyError:
        log.error('unknown renderer "%s", valid types are: %s', renderer, renderer_names)
        sys.exit(1)

    # Derive a set of base paths based on the input files that will act as search
    # paths for crawling
    bases: BasePathsType = {}
    for alias, fname in files:
        fname = os.path.abspath(fname)
        aliasparts = tuple(alias.split('.')) if alias else None
        paths = bases.setdefault(aliasparts, set())
        paths.add(fname if os.path.isdir(fname) else os.path.dirname(fname))

    parser = Parser(config)
    encoding = config.get('project', 'encoding', fallback=locale.getpreferredencoding())
    try:
        # Parse given files/directories, with following if enabled.
        follow = config.get('project', 'follow', fallback='true').lower() in ('true', '1', 'yes')
        seen: set[str] = set()
        for _, fname in files:
            crawl(parser, fname, follow, seen, bases, encoding)
        pages = config.items('manual') if config.has_section('manual') else []
        for scope, path in pages:
            parser.parse_manual(scope, open(path, encoding=encoding))
    except Exception as e:
        msg = f'error parsing around {parser.ctx.file}:{parser.ctx.line}: {e}'
        if isinstance(e, ParseError):
            log.error(msg)
        else:
            log.exception(f'unhandled {msg}')
        sys.exit(1)

    # LuaDox v1 fallback
    outdir = config.get('project', 'outdir', fallback=None)
    # LuaDox v2 just calls it 'out'
    out = config.get('project', 'out', fallback=outdir)

    renderer = rendercls(parser)
    try:
        log.info('prerendering %d pages', len(parser.topsyms))
        toprefs = Prerenderer(parser).process()
        renderer.render(toprefs, out)
    except Exception as e:
        log.exception('unhandled error rendering around %s:%s: %s', parser.ctx.file, parser.ctx.line, e)
        sys.exit(1)

    log.info('done')
