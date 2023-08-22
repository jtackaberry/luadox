# Copyright 2021 Jason Tackaberry
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

# First order of business is to ensure we are running a compatible
# version of Python.
if sys.hexversion < 0x03050000:
    print('FATAL: Python 3.5 or later is required.')
    sys.exit(1)

import os
import re
import configparser
import shutil
import argparse
import shlex
import glob
import locale

from .log import log
from .assets import assets
from .parse import *
from .render import *

try:
    # version.py is generated at build time, so we are running from the proper
    # distribution.
    from .version import __version__  # pyright: ignore
except ImportError:
    # Running from local tree, use dummy value.
    __version__ = 'x.x.x-dev'

# Files from the assets directory to be copied
ASSETS = [
    'luadox.css',
    'prism.css',
    'prism.js',
    'js-search.min.js',
    'search.js',
    'img/i-left.svg',
    'img/i-right.svg',
    'img/i-download.svg',
    'img/i-github.svg',
    'img/i-gitlab.svg',
    'img/i-bitbucket.svg',
]

class FullHelpParser(argparse.ArgumentParser):
    def error(self, message):
        sys.stderr.write('error: %s\n' % message)
        self.print_help()
        sys.exit(2)


def get_file_by_module(module, bases):
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
            if alias_matches:
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


def crawl(parser, path, follow, seen, bases, encoding):
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


def get_config(args):
    """
    Consolidates command line arguments and config file, returning a ConfigParser
    instance that has the reconciled configuration such that command line arguments
    take precedence
    """
    config = configparser.ConfigParser(inline_comment_prefixes='#')
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
    for prop in ('name', 'outdir', 'css', 'favicon', 'encoding', 'hometext'):
        if getattr(args, prop):
            config.set('project', prop, getattr(args, prop))
    if args.manual:
        for spec in args.manual:
            id, fname = spec.split('=')
            config.set('manual', id, fname)
    return config


def get_files(config):
    """
    Generates the files/directories to parse based on config.
    """
    filelines = config.get('project', 'files', fallback='').strip().splitlines()
    for line in filelines:
        for spec in shlex.split(line):
            for modalias, globexpr in re.findall(r'(?:([^/\\]+)=)?(.*)', spec):
                for fname in glob.glob(globexpr):
                    yield modalias, fname


def copy_file_from_config(section, option, outdir):
    fname = config.get(section, option, fallback=None)
    if not fname:
        return
    if not os.path.exists(fname):
        log.fatal('%s file "%s" does not exist', option, fname)
        sys.exit(1)
    else:
        shutil.copy(fname, outdir)


def main():
    global config
    p = FullHelpParser(prog='luadox')
    p.add_argument('-c', '--config', type=str, metavar='FILE',
                   help='Luadox configuration file')
    p.add_argument('-n', '--name', action='store', type=str, metavar='NAME',
                   help='Project name (default Lua Project)')
    p.add_argument('--hometext', action='store', type=str, metavar='TEXT',
                   help='Home link text on the top left of every page')
    p.add_argument('-o', '--outdir', action='store', type=str, metavar='DIRNAME',
                   help='Directory name for rendered files, created if necessary (default ./out)')
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

    # Derive a set of base paths based on the input files that will act as search
    # paths for crawling
    bases = {}
    for alias, fname in files:
        fname = os.path.abspath(fname)
        aliasparts = tuple(alias.split('.')) if alias else None
        bases.setdefault(aliasparts, set()).add(fname if os.path.isdir(fname) else os.path.dirname(fname))

    parser = Parser(config)
    encoding = config.get('project', 'encoding', fallback=locale.getpreferredencoding())
    try:
        # Parse given files/directories, with following if enabled.
        follow = config.get('project', 'follow', fallback='true').lower() in ('true', '1', 'yes')
        seen = set()
        for _, fname in files:
            crawl(parser, fname, follow, seen, bases, encoding)
        pages = config.items('manual') if config.has_section('manual') else []
        for scope, path in pages:
            parser.parse_manual(scope, open(path, encoding=encoding))
    except Exception as e:
        log.exception('unhandled error parsing around %s:%s: %s', parser.ctx.file, parser.ctx.line, e)
        sys.exit(1)

    outdir = config.get('project', 'outdir', fallback=None)
    if not outdir:
        log.warn('outdir is not defined in config file, assuming ./out')
        outdir = 'out'
    os.makedirs(outdir, exist_ok=True)

    copy_file_from_config('project', 'css', outdir)
    copy_file_from_config('project', 'favicon', outdir)

    renderer = Renderer(parser)
    try:
        log.info('preprocessing %d pages', len(parser.topsyms))
        for (_, name), ref in parser.topsyms.items():
            renderer.preprocess(ref)

        for (_, name), ref in parser.topsyms.items():
            if ref.userdata.get('empty') and ref.implicit:
                # Reference has no content and it was also implicitly generated, so we don't render it.
                log.info('not rendering empty %s %s', ref.type, ref.name)
                continue
            if ref.type == 'manual' and ref.name == 'index':
                typedir = outdir
            else:
                typedir = os.path.join(outdir, ref.type)
            os.makedirs(typedir, exist_ok=True)
            outfile = os.path.join(typedir, name + '.html')
            log.info('rendering %s %s -> %s', ref.type, name, outfile)
            html = renderer.render(ref)
            with open(outfile, 'w', encoding='utf8') as f:
                f.write(html)

        js = renderer.render_search_index()
        with open(os.path.join(outdir, 'index.js'), 'w', encoding='utf8') as f:
            f.write(js)

        html = renderer.render_search_page()
        with open(os.path.join(outdir, 'search.html'), 'w', encoding='utf8') as f:
            f.write(html)

        if not parser.get_reference('manual', 'index'):
            # The user hasn't specified an index manual page, so we generate a blank
            # landing page that at least presents the sidebar with available links.
            html = renderer.render_landing_page()
            with open(os.path.join(outdir, 'index.html'), 'w', encoding='utf8') as f:
                f.write(html)

        for name in ASSETS:
            outfile = os.path.join(outdir, name)
            if os.path.dirname(name):
                os.makedirs(os.path.dirname(outfile), exist_ok=True)
            with open(outfile, 'wb') as f:
                f.write(assets.get(name))
    except Exception as e:
        log.exception('unhandled error rendering around %s:%s: %s', parser.ctx.file, parser.ctx.line, e)
