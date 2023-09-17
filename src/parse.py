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

__all__ = ['Context', 'Parser', 'ParseError']
import sys
import os
import re
from configparser import ConfigParser
from typing import IO, Optional, Union, Tuple, List, Dict, Type, Match

from .log import log
from .reference import *
from .utils import *

# TODO: better vararg support
ParseFuncResult = Tuple[Union[str, None], Union[List[str], None]] 

COLLECTION_TAGS: Dict[str, Type[Reference]] = {
    'section': SectionRef,
    'classmod': ClassRef,
    'class': ClassRef,
    'module': ModuleRef,
    'table': TableRef,
}

class Context:
    """
    Keeps track of current file and line being processed.

    There is a single instance held by Parser that's used throughout the program.
    """
    UNDEF = Sentinel.UNDEF
    def __init__(self):
        self.file: Optional[str] = None
        self.line: Optional[int] = None
        self.ref: Optional[Reference] = None

    def update(self, file: Union[str, None, Sentinel]=UNDEF,
               line: Union[int, None, Sentinel]=UNDEF,
               ref: Union['Reference', None, Sentinel]=UNDEF) -> None:
        if ref is not Sentinel.UNDEF:
            self.ref = ref
            # If ref is valid, set the current file/line based on the given ref.
            if ref:
                assert(isinstance(ref, Reference))
                self.file = ref.file
                self.line = ref.line
        if file is not Sentinel.UNDEF:
            self.file = file
        if line is not Sentinel.UNDEF:
            self.line = line


class ReferenceDict(dict):
    """
    Dictionary keyed by ref type whose value is a list of references of that same type.

    This is a simple pattern to improve type clarity.
    """
    def __getitem__(self, k: Type[RefT]) -> List[RefT]:
        return super().__getitem__(k)

class ParseError(ValueError):
    pass

class Parser:
    """
    Parses lua source files and standalone manual pages for later rendering
    by a Renderer instance.
    """
    RE_TAG = re.compile(r'^ *@([^{]\S+) *(.*)')
    RE_COMMENTED_TAG = re.compile(r'^--+ *@([^{]\S+) *(.*)')
    RE_MANUAL_HEADING = re.compile(r'^(#+) *(.*) *')

    def __init__(self, config: ConfigParser) -> None:
        self.config = config
        # A complete list of all Reference objects keyed by Reference subclass type
        self.parsed = ReferenceDict({
            ModuleRef: [],
            ClassRef: [],
            FunctionRef: [],
            FieldRef: [],
            SectionRef: [],
            TableRef: [],
            ManualRef: [],
        })
        # A dict of only top-level References ("toprefs"), keyed by the fully qualified
        # name of the reference.  Note that we depend on insertion order being preserved
        # here, which is guaranteed in Python's native dict as of 3.7:
        # https://mail.python.org/pipermail/python-dev/2017-December/151283.html
        #
        # name -> TopRef
        self.topsyms: dict[str, TopRef] = {}

        # Maps each top-level symbol to its collections, where each value is a dict
        # mapping collection name to a CollectionRef object.  The order that the
        # collections are defined is preserved, as these represent collections within the
        # rendered content.
        #
        # Possibly counterintuitively, classes and modules will have themselves as its
        # first collection, in order to simplify enumerating all content in a topref (via
        # get_collections()).  This does not apply to manual pages, however.
        #
        # topsym -> (name -> CollectionRef)
        self.collections: dict[str, dict[str, CollectionRef]] = {}
        # A dict of all Reference objects, keyed by fully qualified name.
        #
        # name -> Reference
        self.refs: dict[str, Reference] = {}
        # Maps refs by their ids, rather than names
        self.refs_by_id: dict[str, Reference] = {}

        # This holds the context of the current file and reference being processed
        self.ctx = Context()


    def _next_line(self, strip=True) -> Tuple[Union[int, None], Union[str, None]]:
        """
        Returns the next line from the file last passed to parse_source().  Lines
        are stripped of any trailing comments.
        """
        n, line = next(self.feed, (None, None))
        self.ctx.update(line=n)
        if line is not None:
            return n, strip_trailing_comment(line) if strip else line.strip()
        else:
            return None, None


    def _parse_tag(self, line: str, require_comment=True) -> ParseFuncResult: 
        """
        Looks for a @tag in the given raw line of code, and returns the name of
        the tag and any arguments as a list, or a 2-tuple of Nones if no tag was
        found.
        """
        m = (self.RE_COMMENTED_TAG if require_comment else self.RE_TAG).search(line)
        if m:
            tag, args = m.groups()
            return tag, [arg.strip() for arg in args.split()]
        else:
            return None, None


    def _parse_function(self, line: str) -> ParseFuncResult:
        """
        Looks for a function signature in the given raw line of code, and returns
        the name and a list of arguments if found, or a 2-tuple of Nones if not
        found.
        """
        # Form: function foo(bar, baz)
        m = recache(r'''\bfunction *([^\s(]+) *\(([^)]*)(\))?''').search(line)
        if not m:
            # Look for form: foo = function(bar, baz)
            m = recache(r'''(\S+) *= *function *\(([^)]*)(\))?''').search(line)
        if not m:
            # Not a function (or not one we could recognize at least)
            return None, None
        name, argstr, terminated = m.groups()
        arguments = [arg.strip() for arg in argstr.replace(' ', '').split(',') if arg.strip()]
        while not terminated:
            # The function signature is spread across multiple lines
            n, nextline = self._next_line()
            if nextline is None:
                log.error('%s:%s: function definition is truncated', self.ctx.file, n)
                return None, None
            m = recache(r'''([^)]*)(\))?''').search(nextline)
            if m:
                argstr, terminated = m.groups()
                arguments.extend([arg.strip() for arg in argstr.replace(' ', '').split(',') if arg.strip()])
        return name, arguments


    def _parse_field(self, line: str) -> ParseFuncResult: 
        """
        Looks for a field assignment in the given raw line of code, and returns the
        name of the field, or a 2-tuple of Nones if no field was found.

        A 2-tuple is returned to be consistent with other _parse_() functions,
        but the second return value is always None.
        """
        # Fields in the form [foo] = bar
        m = recache(r'''\[([^]]+)\] *=''').search(line)
        if m:
            return recache(r'''['"]''').sub('', m.group(1)), None
        m = recache(r'''\b([\S\.]+) *=''').search(line)
        if m:
            return m.group(1), None
        else:
            return None, None


    def _add_reference(self, ref: Reference, modref: Optional[Reference]=None) -> None:
        """
        Registers the given Reference object with the parser.

        If duplicate references are found, and error is logged and the original
        Reference is not replaced, but no exception is raised.

        If ref belongs to a scope that hasn't been added before, then the given
        modref is automatically registered so ref is properly anchored to some topref.
        """
        # This assertion ensures we have a typed reference (subclass of Reference)
        assert(type(ref) != Reference)
        # Sanity check symbol is defined. It's a bug if it isn't.
        assert(ref.symbol)
        if ref.userdata.get('added'):
            # Reference was already added. This also indicates a bug, but it's not fatal
            # so just log the error.
            log.error('%s:%s: reference "%s" with the same name already exists', ref.file, ref.line, ref.name)
            return

        # Register the class, module, or manual page as a top-level symbol
        if isinstance(ref, TopRef):
            if ref.name in self.topsyms:
                log.error('%s:%s: %s conflicts with another class or module', ref.file, ref.line, ref.name)
            else:
                self.topsyms[ref.name] = ref
        else:
            if not ref.scopes:
                log.fatal('%s:%s: could not determine scope', ref.file, ref.line)
                sys.exit(1)
            # This is not a top-level type.
            for scope in reversed(ref.scopes):
                if scope.name in self.topsyms:
                    break
            else:
                if modref:
                    log.warning(
                        '%s:%s: implicitly adding module "%s" due to @%s; recommend adding explicit @module or @class beforehand',
                        ref.file, ref.line, modref.name, ref.type
                    )
                    self._add_reference(modref)

        # Register the collection against its top-level element.  Class and
        # module refs actually include themselves as a collection to simplify
        # get_collections(), but manual refs don't do this.
        if isinstance(ref, CollectionRef) and not isinstance(ref, ManualRef):
            if ref.topsym not in self.collections:
                self.collections[ref.topsym] = {}
            collections = self.collections[ref.topsym]
            # Only add the ref to the collections list (well, dict) if it doesn't already
            # exist.  If it does already exist, then this is a conflict which will be
            # reported in the conflict check below.
            if ref.symbol not in collections:
                collections[ref.symbol] = ref

        # For fields documented in class methods, strip the self prefix here.
        if isinstance(ref, FieldRef):
            if ref.symbol.startswith('self.'):
                ref.symbol = ref.symbol[5:]

        self.parsed[type(ref)].append(ref)
        ref.userdata['added'] = True

        if ref.name in self.refs:
            conflict = None
            # Sections between topsyms can conflict in name, but if a section conflicts
            # with some other reference in the same topsym we should complain.
            for sectref in self.collections[ref.topsym].values():
                if sectref != ref and sectref.name == ref.name:
                    conflict = sectref
                    break
            if not conflict and not isinstance(ref, SectionRef):
                conflict = self.refs[ref.name]
            if conflict and conflict != ref:
                log.error('%s:%s: %s "%s" conflicts with %s name at %s:%s',
                          ref.file, ref.line, ref.type, ref.name, conflict.type, conflict.file, conflict.line)
        else:
            self.refs[ref.name] = ref
            self.refs_by_id[ref.id] = ref

    def _check_disconnected_reference(self, ref: Union[Reference, None]) -> bool:
        """
        Logs a warning if the reference is disconnected (that is, a documentation
        block that is not associated with any symbol).
        """
        if ref and not ref.userdata.get('added'):
            if ref.symbol:
                return True
            # Potentially disconnected comment stanza here, but let's first check to see if there's
            # any text in the comments, otherwise a blank --- would warn somewhat pointlessly.
            content = ''.join(line.lstrip('-').strip() for (_, line) in ref.raw_content)
            if content:
                log.warning('%s:%s: comment block is not connected with any section, ignoring', ref.file, ref.line)
        return False

    def parse_source(self, f: IO[str]) -> List[str]:
        """
        Parses a lua source file, scanning for documented elements (in lines prefixed
        with 3 dashes), and generating the corresponding Reference objects, registering
        them with the parser.

        Once this function is called against all source files, the documentation can
        be rendered.

        Returns a list of names that were require()d within the scanned file, which can
        be used by the caller for crawling.
        """
        code = f.read()
        path = f.name if hasattr(f, 'name') else '<generated>'

        # TODO: preprocess all lines within --[[-- ... ]] comment block with --- prefixes
        # in order to support multi-line block comments.
        code = [line.strip() for line in code.splitlines()]
        self.feed = iter(enumerate(code, 1))

        # Current scope, 2-tuple of (type, name) where type can be class, module, or
        # table.  We initialize to the module name of the current file, but any @module or
        # @class tag will change the scope.
        dirname, fname = os.path.split(path)
        if fname == 'init.lua':
            modname = dirname.split('/')[-1]
        else:
            modname = fname.replace('.lua', '')
        # Reference object for last section, defaulting to one for the module itself
        modref = ModuleRef(self.refs, file=path, line=1, symbol=modname, implicit=True, level=-1)
        scopes: list[Reference] = [modref]

        # List of modules that were discovered via a 'require' statement in the given
        # Lua source file. This is returned, and the caller can then attempt to discover
        # the source file for the given module and call parse_source() on that.
        requires: list[str] = []

        # Whether we should try to discover field/function from the next line
        # of code.  Usually this will be True but e.g. if we encounter a
        # @class or @table tag, we don't want to treat it as a field.
        parse_next_code_line = True
        # Tracks current number of open braces that haven't been closed.
        table_level = 0
        # The current Reference object.
        ref: Reference|None = None
        # Reference to the current collection, defaulting to implicit module ref
        collection = modref
        self.ctx.update(file=path)
        re_start_comment_block = recache(r'^(---[^-]|---+$)')
        re_require = recache(r'''\brequire\b *\(?['"]([^'"]+)['"]''')
        while True:
            n, line = self._next_line(strip=False)
            if n is None or line is None:
                break
            self.ctx.update(line=n)
            if re_start_comment_block.search(line) and not ref:
                # Starting a content block for something to be included in the docs.
                # Create a new generic (unknown type) Reference against which we will
                # accumulate all comments and other modifier tags.  As we continue parsing
                # lines, once the type becomes known, the ref object is replaced with an
                # appropriate typed ref.  The Reference subclass instance is finally added
                # when the comment block is terminated (either by a blank line or a line
                # of code).
                ref = Reference(self.refs, file=path, line=n, scopes=scopes)
                self.ctx.update(ref=ref)
            if line.startswith('--'):
                if ref:
                    tag, args = self._parse_tag(line)
                    if tag in COLLECTION_TAGS:
                        if not args:
                            raise ParseError(f'@{tag} is missing argment')
                        ref = COLLECTION_TAGS[tag].clone_from(
                            ref, 
                            line=n,
                            scopes=scopes,
                            symbol=args[0],
                            collection=collection,
                            level=table_level,
                        )
                        # This ref becomes the new section.
                        collection = ref
                    if tag == 'within':
                        if not args:
                            raise ParseError('@within not in form: @within <collection>')
                        ref.within = args[0]
                    elif tag == 'classmod' or tag == 'class':
                        if isinstance(scopes[-1], ClassRef):
                            # This is a class being declared within an existing class scope.  We
                            # don't support nested classes, so remove the previous class from the
                            # scopes list (which affects this new class's Reference).
                            scopes.pop()
                        # This is a topref, so replace scopes list for later References, but don't
                        # append to the current scopes list as that affects the scopes for the
                        # new class Reference.
                        scopes = [scopes[0], ref]
                        parse_next_code_line = False
                    elif tag == 'module':
                        # As with class above, replace scopes list.
                        scopes = [scopes[0], ref]
                    elif tag == 'table':
                        scopes.append(ref)
                        parse_next_code_line = False
                    elif tag == 'field':
                        if not args:
                            raise ParseError('@field not in form: @field <name> <description...>')
                        # Inject a field type Reference with the given arguments.  Here we also
                        # make a shallow copy of the current scopes otherwise the popping below
                        # that occurs when the table concludes will end up modifying the scopes
                        # here after the fact.
                        field = FieldRef(
                            self.refs, file=path, line=n, scopes=scopes[:],
                            symbol=args[0], collection=collection
                        )
                        field.raw_content.append((n, ' '.join(args[1:])))
                        self._add_reference(field, modref)
                    elif tag == 'alias':
                        if not args:
                            raise ParseError('@alias not in form: @alias <name>')
                        self.refs[args[0]] = ref
                    elif tag == 'compact':
                        ref.flags['compact'] = args or ['fields', 'functions']
                    elif tag == 'fullnames':
                        ref.flags['fullnames'] = True
                    elif tag in ('meta', 'scope', 'rename', 'inherits', 'display'):
                        if not args:
                            raise ParseError(f'@{tag} is missing argument')
                        ref.flags[str(tag)] = ' '.join(args)
                        # Some of these tags can affect display or name, so clear any
                        # cached attributes.
                        ref.clear_cache()
                        if tag == 'rename' and type(ref) == type(scopes[-1]) and ref.symbol == scopes[-1].scope:
                            # The current reference matches the last scope, so rename this scope.
                            scopes[-1].flags['rename'] = ref.flags['rename']
                            scopes[-1].clear_cache()
                    elif tag in ('type',):
                        if not args:
                            raise ParseError(f'@{tag} is missing argment')
                        ref.flags[str(tag)] = args[0].split('|')
                    elif tag in ('order',):
                        if not args:
                            raise ParseError(f'@{tag} is missing argment')
                        ref.flags[str(tag)] = args
                    elif tag == 'section':
                        if not args:
                            raise ParseError(f'@{tag} is missing argment')
                        # Nothing special is otherwise needed here.
                    else:
                        ref.raw_content.append((n, line))
            else:
                # This line doesn't start with a comment, but may have one at the end
                # which we remove here.
                line = strip_trailing_comment(line)
                # Determine level of nested tables and pop scopes as needed.  We only
                # do this for non-empty lines so that we allow whitespace between a
                # @table and the table declaration.
                if line:
                    # FIXME: known limitations:
                    #   1. nested table tracking doesn't support --[[ ]]-- style content.
                    #   2. this also fails if the line has a string that contains '{' or '}'
                    #   3. if the next non-empty line of code doesn't contain '{' then
                    #      we'll assign upcoming fields to the parent instead of the table.
                    # These are tricky to avoid without fully tokenzing lua source.
                    table_level += line.count('{')
                    table_level -= line.count('}')
                    while isinstance(scopes[-1], TableRef) and \
                          table_level <= scopes[-1].level:
                        scopes.pop()
                        collection = scopes[-1]

                if parse_next_code_line:
                    # If we're here, we have a non-comment and non-empty line.
                    m = re_require.search(line)
                    if m:
                        requires.append(m.group(1))

                    if ref is None:
                        continue

                    for refcls in (FieldRef, FunctionRef):
                        name, extra = getattr(self, '_parse_' + refcls.type)(line)
                        scope = scopes[-1]
                        if refcls == FieldRef and isinstance(scope, ModuleRef) and scope.name == name:
                            # If we have a field that's the same name as the current
                            # module we don't register it, as this is a common pattern.
                            pass
                        elif name:
                            if ref.symbol:
                                log.error(
                                    '%s:%s: %s defined before %s %s has terminated; separate with a blank line',
                                    ref.file, ref.line, refcls.type, ref.type, ref.name
                                )
                            ref = refcls.clone_from(ref,
                                # Create a shallow copy of current scopes so subsequent modifications
                                # don't retroactively apply.
                                file=path, line=n, scopes=scopes[:], symbol=name,
                                collection=collection, extra=extra
                            )
                            break
                    if self._check_disconnected_reference(ref):
                        self._add_reference(ref, modref)
                    ref = None
                    self.ctx.update(ref=None)
                elif ref is not None:
                    # Break in comment terminates content block
                    # TODO: is this valid? is sectionref ever nil? test
                    if not collection:
                        log.fatal('%s:%s: preceding comment block has no @section', self.ctx.file, n)
                        sys.exit(1)
                    if self._check_disconnected_reference(ref):
                        self._add_reference(ref, modref)
                    ref = None
                    self.ctx.update(ref=None)
                    parse_next_code_line = True

        if ref and self._check_disconnected_reference(ref):
            # if isinstance(ref, ModuleRef) and 
            if not ref.userdata.get('added'):
                # If we're here, ref is an explicitly defined collection (module, class,
                # or section) that wasn't added, which must mean it doesn't contain
                # anything other than the collection's own docstring.  Because it was
                # explicitly defined, go ahead and add it now.
                self._add_reference(ref)
        return requires


    def parse_manual(self, name: str, f: IO[str]) -> None:
        """
        Parses a markdown file as a manual page.

        Markdown headings up to level 3 are turned into References that can be, er,
        referenced elsewhere in luadox comments anywhere a reference is accepted
        (e.g. @{foo}).

        The top-level symbol for the manual page is dictated by the key name in the
        config file.  For example, given:

           [manual]
             tutorial = somepage.md

        The top-level symbol for all references in somepage.md will be 'tutorial'.
        Reference objects generated from headings are named such that the heading
        text is converted to lower case and all spaces converted to underscores.
        """
        content = f.read()
        path = f.name if hasattr(f, 'name') else '<generated>'

        # Create the top-level reference for the manual page.  Any lines in the markdown
        # before the first heading will accumulate in this topref's content.
        topref = ManualRef(self.refs, file=path, line=1, symbol=name, level=-1)
        self._add_reference(topref)

        # We craft section symbols based on the heading, but there's nothing that requires
        # markdown headings to be unique.  Here we keep track of the symbols we use for
        # sections so that we can tack on a number suffix to ensure that same-named
        # headings at least have unique symbols.
        #
        # symbol name -> count
        symbols: dict[str, int] = {}

        ref = topref
        codeblocks = 0
        for n, line in enumerate(content.splitlines(), 1):
            codeblocks += line.count('```')
            m = self.RE_MANUAL_HEADING.search(line)
            # If we have what looks to be a heading, make sure it's not actually contained
            # within a code block.
            if m and codeblocks % 2 == 0:
                hashes, heading = m.groups()
                level = len(hashes)
                # Only h1, h2, and h3 create section references.
                if level <= 3:
                    if ref == topref:
                        ref.heading = heading

                    # Symbol is used for URL fragment
                    symbol = recache(r'[^a-zA-Z0-9- ]').sub('', heading.lower())
                    symbol = recache(r' +').sub('_', symbol).replace('_-_', '-')
                    # Headings don't need to be unique, so check for duplicate symbol
                    if symbol in symbols:
                        symbol = symbol + str(symbols[symbol] + 1)
                    symbols[symbol] = symbols.get(symbol, 0) + 1

                    ref = SectionRef(self.refs, file=path, line=n, scopes=[topref], symbol=symbol)
                    ref.heading = heading
                    ref.flags['level'] = level

                    if ref != topref:
                        self._add_reference(ref)
                    # The Reference object captures the heading title which
                    # _render_manual() handles, so skip adding the heading line to the
                    # ref's content just below.
                    continue

            ref.raw_content.append((n, line))


    def get_reference(self, typ: Type[Reference], name: str) -> Union[Reference, None]:
        """
        Returns the Reference object for the given type and name.
        """
        for ref in self.parsed[typ]:
            if ref.name == name:
                return ref


    def resolve_ref(self, name: str) -> Union[Reference, None]:
        """
        Finds the Reference object for the given reference name.

        The name is relative to the current context, searching up the containing
        scopes through to the top level until the reference name can be resolved.

        If the name can't be resolved then None is returned.
        """
        name = name.replace(':', '.').replace('(', '').replace(')', '')
        ref: Reference|None = None
        if self.ctx.ref:
            # Search the upward in the current context's scope for the given name.
            # ref's scopes may be None if it was an implicitly added module.
            scopes = self.ctx.ref.scopes or []
            for scope in [self.ctx.ref.name] + [r.name for r in scopes]:
                ref = self.refs.get(scope + '.' + name)
                if ref:
                    break
        if not ref:
            # Qualifying the name with the current context's scope was a bust, so now
            # look for it in the global space.
            ref = self.refs.get(name)
        if not ref and self.ctx.ref:
            # Not found in global or context's scope, but if the current context is a
            # class then we also search up the class's hierarchy.  (The current context
            # ref may be a collection so we don't use that, rather use its topref.)
            topref = self.ctx.ref.topref
            if isinstance(topref, ClassRef):
                for clsref in reversed(topref.hierarchy):
                    ref = self.refs.get(clsref.name + '.' + name)
                    if ref:
                        break

        if ref and ref.within and 'within_topsym' not in ref.userdata:
            # Check to see if the @within section is in the same topsym.
            collections = self.collections[ref.topsym]
            if ref.within not in collections:
                # This reference is @within another topsym.  We need to find it.
                candidates: set[str] = set()
                for topsym in self.topsyms:
                    collections = self.collections[topsym]
                    if ref.within in collections:
                        candidates.add(topsym)
                if len(candidates) > 1:
                    log.error('%s is @within %s which is ambiguous (in %s)', name, ref.within, ', '.join(candidates))
                else:
                    # Remember that this ref is @within a different topsym
                    ref.userdata['within_topsym'] = candidates.pop()
            else:
                # Remember that this ref is @within the same topsym
                ref.userdata['within_topsym'] = ref.topsym

        return ref


    def _reorder_refs(self, refs: List[RefT], topref: Optional[Reference]=None) -> List[RefT]:
        """
        Reorders the given list of Reference objects according to any @order tags.
        """
        # For @reorder first
        first: list[RefT] = []
        # For @reorder last
        last: list[RefT] = []
        # Everything else, which is ordered relative to other names
        ordered = refs[:]
        for ref in refs:
            if topref and topref != ref.topref:
                # Sanity checks that the topref for this section matches the topref
                # we wanted collections from.  A mismatch means there is a name collision
                # in which case _add_reference() would already have logged an error.
                ordered.remove(ref)
                continue
            order = ref.flags.get('order')
            if not order:
                continue
            ordered.remove(ref)
            if len(order) == 1:
                whence = order[0]
                if whence == 'first':
                    ordered.insert(0, ref)
                elif whence == 'last':
                    ordered.append(ref)
                continue
            else:
                whence, anchor = order[:2]
            for n, other in enumerate(ordered):
                if other.symbol == anchor:
                    if whence == 'before':
                        ordered.insert(n, ref)
                    else:
                        ordered.insert(n+1, ref)
                    break
            else:
                log.error('%s:~%s unknown @order anchor reference %s', ref.file, ref.line, anchor)
        return first + ordered + last


    def get_collections(self, topref: Reference) -> List[CollectionRef]:
        """
        Yields section refs for the given topref while honoring user-defined ordering
        (via the @order tag)
        """
        if topref.name not in self.collections:
            return []
        sections = self.collections[topref.name].values()
        return self._reorder_refs(list(sections), topref)


    def get_elements_in_collection(self, typ: Type[RefT], colref: CollectionRef) -> List[RefT]:
        """
        Returns a list of Reference objects of the requested type in the given collection.
        Used to display a list of functions and fields within the context of a collection,
        which also respects @within and @reorder tags.
        """
        # @section names aren't necessarily globally unique, so determine which topsyms
        # contain a collection with the same name (which may or may not be the same topsym
        # for the given colref).
        found: set[str] = set()
        for col in self.collections.values():
            for ref in col.values():
                if ref.name == colref.name:
                    found.add(ref.topsym)

        topsym = colref.topsym
        if len(found) <= 1:
            # Only one (or no) matches, which may or may not be in the same topsym,
            # so don't constrain subsequent search.
            topsym = None
        elif topsym not in found:
            # We have multiple top-level refs that have a collection with the same name
            # but none of them are the same topref as the given colref.  So we can't
            # reliably resolve the element list for this collection.
            log.warning(
                'collection "%s" referenced by %s is ambiguous as it exists '
                'in multiple classes or modules (%s) but %s lacks documented %ss',
                colref.name, topsym, ', '.join(found), topsym, typ.type
            )

        elems: list[RefT] = []
        for ref in self.parsed[typ]:
            if topsym and topsym != ref.topsym:
                # We're constraining the refs search to the given topref but this ref
                # doesn't belong to that topref.
                continue
            if ref.within:
                if ref.within == colref.name:
                    # @within for this ref targets this collection by name
                    elems.append(ref)
            elif ref.collection and ref.collection.name == colref.name:
                # No @within, and either the candidate ref's collection name matches the
                # given collection name.
                elems.append(ref)
        return self._reorder_refs(elems)


    def render_ref_markdown(self, ref: Reference, text: Optional[str]=None, code=False) -> str:
        """
        Returns the Reference as a markdown link, using luadox:<refid> as the link target,
        which can be further resolved by the downstream renderer.

        If code is True, then the given text is wrapped in backticks.
        """
        tick = '`' if code else ''
        parens = '()' if isinstance(ref, FunctionRef) and not text else ''
        return f'[{tick}{text or ref.name}{parens}{tick}](luadox:{ref.id})'


    def _render_ref_markdown_re(self, m: Match[str]) -> str:
        """
        Regexp callback to handle the @{refname} case.
        """
        code: bool = (m.group(1) == '`')
        ref = self.resolve_ref(m.group(2))
        if ref:
            return self.render_ref_markdown(ref, m.group(3), code=code)
        else:
            log.warning('%s:~%s: reference "%s" could not be resolved', self.ctx.file, self.ctx.line, m.group(2))
            return m.group(3) or m.group(2)


    def _render_backtick_ref_markdown_re(self, m: Match[str]) -> str:
        """
        Regexp callback to handle the `refname` case.
        """
        ref = self.resolve_ref(m.group(1))
        if ref:
            return self.render_ref_markdown(ref, text=m.group(1), code=True)
        else:
            # Couldn't resolve the ref, just return back the original text.
            return '`{}`'.format(m.group(1))


    def refs_to_markdown(self, block: str) -> str:
        """
        Replaces `refname` and @{refname} in the given block of text with
        markdown links.
        """
        # return block
        # self._xxx = getattr(self, '_xxx', 0) + len(block)
        # log.info('process 2: %s', self._xxx)
        # Resolve `ref`
        block = recache(r'(?<!`)`([^` ]+)`', re.S).sub(self._render_backtick_ref_markdown_re, block)
        # Resolve @{ref} and @{ref|text}.  Do this *after* `ref` in case the ref is in the
        # form `@{stuff}`.
        block = recache(r'(`)?@{([^}|]+)(?:\|([^}]*))?}(`)?', re.S).sub(self._render_ref_markdown_re, block)
        return block


    def parse_raw_content(self, lines: List[Tuple[int, str]], strip_comments=True) -> Tuple[
            Dict[str, Tuple[List[str], Content]],
            List[Tuple[List[str], Content]],
            Content
        ]:
        """
        Parses a docstring block into markdown.

        Docstring blocks can appear in sections, or as content associated with a
        function definition or field.

        This function returns 3 values: a dict of name -> (types, docstring) for @tparam
        tags, a list of (types, docstrings) for @treturn tags, and a string holding the
        converted content to markdown.
        """
        params: dict[str, tuple[list[str], Content]] = {}
        returns: list[tuple[list[str], Content]] = []
        # These tags take nested content
        content_tags = {'warning', 'note', 'tparam', 'treturn'}

        # We pass _refs_to_markdown() as a postprocessor for the Content (here as well as
        # below) which will resolve all references when the renderer finally fetches the
        # markdown content via the Markdown.get() method.
        #
        # List of (indent, tag, content)
        stack: list[tuple[int, str,  Content]] = [(0, '', Content(postprocess=self.refs_to_markdown))]
        # The number of columns to dedent raw lines before adding to the parsed content.
        # If None, we set this to the current line's indent level and use that as dedent
        # until reset back to None.
        dedent = None
        # We tack on a sentinel value at the end of the raw lines which forces closure of
        # all pending tags on the stack.
        for n, line in lines + [(-1, '')]:
            self.ctx.update(line=n)
            tag, args = self._parse_tag(line, require_comment=strip_comments)
            if strip_comments:
                line = line.lstrip('-').rstrip()
            indent = get_indent_level(line)

            while len(stack) > 1 and (line or n == -1):
                if stack[-1][0] < indent:
                    break
                _, done_tag, content = stack.pop()
                if done_tag in {'usage', 'example', 'code'}:
                    # Remove trailing newlines from the snippet before terminating the
                    # markdown code block.
                    content.md().rstrip().append('```')
                # Redetect dedent level based on next line.
                dedent = None

            # New content fragments are appended to the content object from the top of the
            # stack.
            content = stack[-1][2]
            if tag:
                # The Content object this tag's content will be pushed to.  For tags that
                # take content we initialize a new Content object, otherwise we just reuse
                # the last one on the stack and append to it.
                tagcontent = Content(postprocess=self.refs_to_markdown) if tag in content_tags else stack[-1][2]
                stack.append((indent, tag, tagcontent))

                if tag in {'usage', 'example', 'code'}:
                    if tag in {'usage', 'example'}:
                        # @usage and @example add a header.
                        content.md().append(f'##### {tag.title()}\n')
                    lang = 'lua' if not args else args[0]
                    content.md().append(f'```{lang}')
                    # Ensure subsequent dedent is based on the first line of the code
                    # block
                    dedent = None
                elif tag in {'warning', 'note'}:
                    heading = self.refs_to_markdown(' '.join(args) if args else tag.title())
                    content.append(Admonition(tag, heading, tagcontent))
                elif tag == 'tparam' and args and len(args) >= 2:
                    types = args[0].split('|')
                    name = args[1]
                    tagcontent.md().append(' '.join(args[2:]))
                    params[name] = types, tagcontent
                elif tag == 'treturn' and args:
                    types = args[0].split('|')
                    tagcontent.md().append(' '.join(args[1:]))
                    returns.append((types, tagcontent))
                elif tag == 'see' and args:
                    refs = [self.resolve_ref(see) for see in args]
                    content.append(SeeAlso([ref.id for ref in refs if ref]))
                else:
                    log.error('%s:%s: unknown tag @%s or missing arguments', self.ctx.file, n, tag)

            elif line is not None:
                dedent = indent if dedent is None else dedent
                content.md().append(line[dedent:])

        if len(stack) != 1:
            log.error('%s:~%s: LuaDox bug: @%s is dangling', self.ctx.file, lines[-1][0], stack[-1][1])

        return params, returns, stack[0][2]
