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

__all__ = ['Parser']
import sys
import os
import re
import collections

from .log import log
from .reference import *

# TODO: better vararg support

class Parser:
    """
    Parses lua source files and standalone manual pages for later rendering
    by a Renderer instance.
    """
    def __init__(self, config):
        self.config = config
        # A complete list of all Reference objects keyed by reference type.
        self.parsed = {
            'module': [],
            'class': [],
            'function': [],
            'field': [],
            'section': [],
            'table': [],
            'manual': []
        }
        # A dict of only top-level References ("toprefs"), keyed by a 2-tuple of (type,
        # name) where type is one of 'class', 'module', or 'manual' and name is the fully
        # qualified name of the reference.
        #
        # (type, name) -> Reference
        self.topsyms = collections.OrderedDict()

        # Maps top-level symbols to all its sections, where each value is a dict mapping
        # section name to a Reference object.  The order that the sections are defined
        # is preserved.
        #
        # topsym -> OrderedDict(sectionname -> Reference)
        self.sections = {}
        # A dict of all Reference objects, keyed by fully qualified name.
        #
        # name -> Reference
        self.refs = {}

        # This holds the context of the current file and reference being processed
        self.ctx = Context()


    def _next_line(self, strip=True):
        """
        Returns the next line from the file last passed to parse_source().  Lines
        are stripped of any trailing comments.
        """
        n, line = next(self.feed, (None, None))
        self.ctx.update(line=n)
        if line is not None:
            return n, self._strip_trailing_comment(line) if strip else line.strip()
        else:
            return None, None

    def _strip_trailing_comment(self, line):
        return re.sub(r'--.*', '', line)


    def _parse_function(self, line):
        """
        Looks for a function signature in the given raw line of code, and returns
        the name and a list of arguments if found, or a 2-tuple of Nones if not
        found.
        """
        # Form: function foo(bar, baz)
        m = re.search(r'''\bfunction *([^\s(]+) *\(([^)]*)(\))?''', line)
        if not m:
            # Look for form: foo = function(bar, baz)
            m = re.search(r'''(\S+) *= *function *\(([^)]*)(\))?''', line)
        if not m:
            # Not a function (or not one we could recognize at least)
            return None, None
        name, argstr, terminated = m.groups()
        arguments = [arg.strip() for arg in argstr.replace(' ', '').split(',') if arg.strip()]
        while not terminated:
            # The function signature is spread across multiple lines
            n, line = self._next_line()
            if line is None:
                log.error('%s:%s: function definition is truncated', self.ctx.file, n)
                return None, None
            m = re.search(r'''([^)]*)(\))?''', line)
            if m:
                argstr, terminated = m.groups()
                arguments.extend([arg.strip() for arg in argstr.replace(' ', '').split(',') if arg.strip()])
        return name, arguments


    def _parse_field(self, line):
        """
        Looks for a field assignment in the given raw line of code, and returns the
        name of the field, or a 2-tuple of Nones if no field was found.

        A 2-tuple is returned to be consistent with other _parse_() functions,
        but the second return value is always None.
        """
        # Fields in the form [foo] = bar
        m = re.search(r'''\[([^]]+)\] *=''', line)
        if m:
            return re.sub(r'''['"]''', '', m.group(1)), None
        m = re.search(r'''\b([\S\.]+) *=''', line)
        if m:
            return m.group(1), None
        else:
            return None, None


    def _parse_tag(self, line, require_comment=True):
        """
        Looks for a @tag in the given raw line of code, and returns the name of
        the tag and any arguments as a list, or a 2-tuple of Nones if no tag was
        found.
        """
        m = re.search(r'^%s *@([^{]\S+) *(.*)' % ('--+' if require_comment else ''), line)
        if m:
            tag, args = m.groups()
            return tag, [arg.strip() for arg in args.split()]
        else:
            return None, None


    def _add_reference(self, ref, modref=None):
        """
        Registers the given Reference object with the parser.

        If duplicate references are found, and error is logged and the original
        Reference is not replaced, but no exception is raised.

        If ref belongs to a scope that hasn't been added before, then the given
        modref is automatically registered so ref is properly anchored to some topref.
        """
        if ref.userdata.get('added'):
            # Reference was already added
            log.error('%s:%s: reference "%s" with the same name already exists', ref.file, ref.line, ref.name)
            return

        # Register the class, module, or manual page as a top-level symbol
        if ref.type in ('class', 'module', 'manual'):
            if ref.name in self.topsyms:
                log.error('%s:%s: %s conflicts with another class or module', ref.file, ref.line, ref.name)
            else:
                self.topsyms[(ref.type, ref.name)] = ref
        else:
            if not ref.scopes:
                log.fatal('%s:%s: could not determine scope', ref.file, ref.line)
                sys.exit(1)
            # This is not a top-level type.
            for scope in reversed(ref.scopes):
                if (scope.type, scope.name) in self.topsyms:
                    break
            else:
                if modref:
                    log.warning(
                        '%s:%s: implicitly adding module "%s" due to @%s; recommend adding explicit @module beforehand',
                        ref.file, ref.line, modref.name, ref.type
                    )
                    self._add_reference(modref)

        # Register the section against the class/module.
        if ref.type in ('section', 'class', 'module', 'table'):
            if ref.topsym not in self.sections:
                self.sections[ref.topsym] = collections.OrderedDict()
            sections = self.sections[ref.topsym]
            # Only add the ref to the sections list (well, ordered dict) if it
            # doesn't already exist.  The first occurrence sets the order where
            # the section appears, and subsequent @section tags can exist to
            # act as a kind of @within that applies to all subsequent functions and fields.
            if ref.symbol not in sections:
                sections[ref.symbol] = ref

        if ref.type == 'field':
            if ref.symbol.startswith('self.'):
                ref.symbol = ref.symbol[5:]
        self.parsed[ref.type].append(ref)
        if ref.name in self.refs:
            conflict = None
            # Sections between topsyms can conflict in name, but if a section conflicts
            # with some other reference in the same topsym we should complain.
            for sectref in self.sections[ref.topsym].values():
                if sectref != ref and sectref.name == ref.name:
                    conflict = sectref
                    break
            if not conflict and ref.type != 'section':
                conflict = self.refs[ref.name]
            if conflict and conflict != ref:
                log.error('%s:%s: %s "%s" conflicts with %s name at %s:%s',
                          ref.file, ref.line, ref.type, ref.name, conflict.type, conflict.file, conflict.line)
        else:
            self.refs[ref.name] = ref
        ref.userdata['added'] = True

    def _check_disconnected_reference(self, ref):
        """
        Logs a warning if the reference is disconnected (that is, a documentation
        block that is not associated with any symbol).
        """
        if ref and not ref.userdata.get('added'):
            if ref.symbol:
                return True
            # Potentially disconnected comment stanza here, but let's first check to see if there's
            # any text in the comments, otherwise a blank --- would warn somewhat pointlessly.
            content = ''.join(line.lstrip('-').strip() for (_, line) in ref.content)
            if content:
                log.warning('%s:%s: comment block is not connected with any section, ignoring', ref.file, ref.line)
        return False

    def parse_source(self, f):
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
        path = getattr(f, 'name', '<generated>')

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
        modref = Reference(self, file=path, line=1, type='module', symbol=modname, implicit=True, level=-1)
        scopes = [modref]

        # List of modules that were discovered via a 'require' statement in the given
        # Lua source file. This is returned, and the caller can then attempt to discover
        # the source file for the given module and call parse_source() on that.
        requires = []
        # Last section
        section = None

        # Whether we should try to discover field/function from the next line
        # of code.  Usually this will be True but e.g. if we encounter a
        # @class tag, we don't want to treat it as a field.
        parse_next_code_line = True
        # Tracks current number of open braces that haven't been closed.
        table_level = 0
        # Tags which will generate a new section.
        section_tags = 'section', 'classmod', 'class', 'module', 'table'
        # The current Reference object.
        ref = None
        # Reference to the current section, default to implicit module ref
        sectionref = modref
        self.ctx.update(file=path)
        while True:
            n, line = self._next_line(False)
            if line is None:
                break
            self.ctx.update(line=n)
            if re.search(r'^(---[^-]|---+$)', line) and not ref:
                # Starting a content block for something to be included in the docs.  Create
                # a new Reference, against which we will accumulate all comments and other
                # modifier tags.  The Reference is finally added when the comment block is
                # terminated (either by a blank line or a line of code).
                ref = Reference(self, file=path, line=n, scopes=scopes)
                self.ctx.update(ref=ref)
            if line.startswith('--'):
                if ref:
                    tag, args = self._parse_tag(line)
                    if tag in section_tags:
                        section = args[0]
                        ref.update(
                            type=tag, line=n, scopes=scopes, symbol=args[0],
                            section=section, extra=args[1:],
                            sectionref=sectionref,
                            level=table_level,
                        )
                        sectionref = ref
                    if tag == 'within':
                        ref.update(within=args[0])
                    elif tag == 'section':
                        # Nothing special needed here.
                        pass
                    elif tag == 'classmod' or tag == 'class':
                        if scopes[-1].type == 'class':
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
                        #scopes.append(Scope('table', section, table_level, last_section))
                        scopes.append(ref)
                        parse_next_code_line = False
                    elif tag == 'field':
                        if not args:
                            raise ValueError('@field not in form: @field <name> <description...>')
                        # Inject a field type Reference with the given arguments.  Here we also
                        # make a shallow copy of the current scopes otherwise the popping below
                        # that occurs when the table concludes will end up modifying the scopes
                        # here after the fact.
                        f = Reference(
                            self, type=tag, file=path, line=n, scopes=scopes[:],
                            symbol=args[0], section=section, sectionref=sectionref
                        )
                        f.content.append((n, ' '.join(args[1:])))
                        self._add_reference(f, modref)
                    elif tag == 'alias':
                        self.refs[args[0]] = ref
                    elif tag == 'compact':
                        ref.flags['compact'] = args or ['fields', 'functions']
                    elif tag == 'fullnames':
                        ref.flags['fullnames'] = True
                    elif tag in ('meta', 'scope', 'rename', 'inherits', 'display'):
                        oldname = ref._name
                        ref.flags[tag] = ' '.join(args)
                        # Some of these tags can affect display or name, so call update() to
                        # clear any cached attributes.
                        ref.update()
                        if tag == 'rename' and ref.type == scopes[-1].type and ref.symbol == scopes[-1].name:
                            # The current reference matches the last scope, so rename this scope.
                            scopes[-1].name = ref.flags[tag]
                    elif tag in ('type',):
                        ref.flags[tag] = args[0].split('|')
                    elif tag in ('order',):
                        ref.flags[tag] = args
                    else:
                        ref.content.append((n, line))
            else:
                line = self._strip_trailing_comment(line)
                # FIXME: nested table tracking doesn't support --[[ ]]-- style content.
                table_level += line.count('{')
                table_level -= line.count('}')
                while scopes[-1].type == 'table' and table_level <= scopes[-1].level:
                    scopes.pop()
                    section = scopes[-1].section

                if parse_next_code_line:
                    # A non-comment and non-empty line.
                    m = re.search(r'''\brequire\b *\(?['"]([^'"]+)['"]''', line)
                    if m:
                        requires.append(m.group(1))

                    if ref is None:
                        continue

                    order = ('field', 'function') if scopes[-1].type == 'table' else ('function', 'field')
                    for tp in order:
                        name, extra = getattr(self, '_parse_' + tp)(line)
                        scope = scopes[-1]
                        if tp == 'field' and scope.type == 'module' and scope.name == name:
                            # If we have a field that's the same name as the current
                            # module we don't register it, as this is a common pattern.
                            pass
                        elif name:
                            if ref.symbol:
                                log.error(
                                    '%s:%s: %s defined before %s %s has terminated; separate with a blank line',
                                    ref.file, ref.line, tp, ref.type, ref.name
                                )
                            ref.update(
                                # Create a shallow copy of current scopes so subsequent modifications
                                # don't retroactively apply.
                                type=tp, file=path, line=n, scopes=scopes[:], symbol=name,
                                section=section, sectionref=sectionref, extra=extra
                            )
                            break
                    if self._check_disconnected_reference(ref):
                        self._add_reference(ref, modref)
                    ref = None
                    self.ctx.update(ref=None)
                elif ref is not None:
                    # Break in comment terminates content block
                    if not section:
                        log.fatal('%s:%s: preceding comment block has no @section', self.ctx.file, n)
                        sys.exit(1)
                    if self._check_disconnected_reference(ref):
                        self._add_reference(ref, modref)
                    ref = None
                    self.ctx.update(ref=None)
                    parse_next_code_line = True

        self._check_disconnected_reference(ref)
        return requires


    def parse_manual(self, scope, f):
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
        path = getattr(f, 'name', '<generated>')

        # Create the top-level reference for the manual page.  Any lines in the markdown
        # before the first heading will accumulate in this topref's content.
        topref = Reference(self, file=path, line=1, type='manual', symbol=scope, source=content, level=-1)
        self._add_reference(topref)

        ref = topref
        codeblocks = 0
        for n, line in enumerate(content.splitlines(), 1):
            codeblocks += line.count('```')
            m = re.search('^(#+) *(.*) *', line)
            # If we have what looks to be a heading, make sure it's not actually contained
            # within a code block.
            if m and codeblocks % 2 == 0:
                hashes, heading = m.groups()
                level = len(hashes)
                # Only h1, h2, and h3 create section references.
                if level <= 3:
                    if ref == topref:
                        ref.flags['display'] = heading
                        ref.update()
                    symbol =  re.sub(r'[^a-zA-Z0-9 ]', '', heading.lower()).replace(' ', '_')
                    ref = Reference(self, file=path, line=n, type='section', scopes=[topref], symbol=symbol)
                    ref.flags['display'] = heading
                    ref.flags['level'] = level
                    ref.update()
                    if ref != topref:
                        self._add_reference(ref)
                    # The Reference object captures the heading title which
                    # _render_manual() handles, so skip adding the heading line to the
                    # ref's content just below.
                    continue

            ref.content.append((n, line))

    def get_reference(self, type, name):
        for ref in self.parsed[type]:
            if ref.name == name:
                return ref

    def _resolve_ref(self, name):
        """
        Finds the Reference object for the given reference name.

        The name is relative to the current context, searching up the containing
        scopes through to the top level until the reference name can be resolved.

        If the name can't be resolved then None is returned.
        """
        name = name.replace(':', '.').replace('(', '').replace(')', '')
        ref = None
        if self.ctx.ref:
            # Search the upward in the current context's scope for the given name.
            for scope in [self.ctx.ref.name] + [r.name for r in self.ctx.ref.scopes]:
                ref = self.refs.get(scope + '.' + name)
                if ref:
                    break
        if not ref:
            # Qualifying the name with the current context's scope was a bust, so now
            # look for it in the global space.
            ref = self.refs.get(name)
        if not ref and self.ctx.ref.topref.type == 'class':
            # Not found in global or context's scope, but if the current context is a
            # class then we also search up the class's hierarchy.  (The current ref may
            # be a section so we don't use it, rather use the ref's scope.)
            hierarchy = self.refs.get(self.ctx.ref.topref.name).hierarchy
            for cls in reversed(hierarchy):
                ref = self.refs.get(cls.name + '.' + name)
                if ref:
                    break
        if not ref:
            return

        if ref.within and not ref.userdata.get('within_topsym'):
            # Check to see if the @within section is in the same topsym.
            sections = self.sections[ref.topsym]
            if ref.within not in sections:
                # This reference is @within another topsym.  We need to find it.
                candidates = set()
                for (_, topsym), cmref in self.topsyms.items():
                    sections = self.sections[topsym]
                    if ref.within in sections:
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

    def _reorder_refs(self, refs, topref=None):
        """
        Reorders the given list of Reference objects according to any @order tags.
        """
        first = []
        ordered = list(refs)[:]
        last = []
        for ref in refs:
            if topref and topref != ref.topref:
                # Sanity checks that the topref for this section matches the topref
                # we wanted sections from.  A mismatch means there is a name collision
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


    def _get_sections(self, topref):
        """
        Yields section refs for the given topref while honoring user-defined ordering
        (via the @order tag)
        """
        if topref.name not in self.sections:
            return []
        sections = self.sections[topref.name].values()
        return self._reorder_refs(sections, topref)


    def _get_elements_in_section(self, tp, section, topsym):
        """
        Returns a list of Reference objects of type tp in the given section.
        Used to display a list of functions and fields within the context of
        a section.
        """
        # Sections aren't necessarily globally unique.  So first see which topsyms
        # the given section name is present within.  If it's in the given topsym,
        # we search that one for tp, otherwise we use the section from outside
        # the topsym (and log a warning if there are multiple options).
        found = set()
        for cm, sections in self.sections.items():
            for ref in sections.values():
                if section == ref.section:
                    found.add(ref.topsym)

        if len(found) <= 1:
            # Only one (or no) matches, which may or may not be in the same topsym,
            # so don't constrain subsequent search.
            topsym = None
        elif topsym not in found:
            log.warning(
                'section "%s" referenced by %s is ambiguous as it exists '
                'in multiple classes or modules (%s) but %s lacks documented %ss',
                section, topsym, ', '.join(found), topsym, tp
            )

        elems = []
        for ref in self.parsed[tp]:
            if section == (ref.within or ref.section) and (not topsym or ref.topsym == topsym):
                elems.append(ref)
        return self._reorder_refs(elems)