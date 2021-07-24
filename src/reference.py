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

__all__ = ['Reference', 'Context']

import re

from .log import log

class Context:
    """
    Keeps track of current file and line being processed.

    There is a single instance held by Parser that's used throughout the program.
    """
    def __init__(self):
        self.file = None
        self.line = None
        self.ref = None

    def update(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)
        if kwargs.get('ref'):
            if 'file' not in kwargs:
                self.file = self.ref.file
            if 'line' not in kwargs:
                self.line = self.ref.line


class Reference:
    """
    A Reference is anything that can be, uh, referenced.  It is the basis of all
    documentable elements, and applies to modules, classes, fields, functions,
    sections, manual pages, etc.

    A special type of reference called a top-level reference -- or "topref" --
    is anything that will be rendered into its own separate page in the documentation.
    All references can be traced back to a topref.

    A Reference can be globally resolved by the combination of its topref (determines
    the page the ref exists on) and its name (which uniquely identifies the ref on the
    top-level page).
    """
    def __init__(self, parser, **kwargs):
        # The Parser object that created us.  Primarily used to resolve related
        # references, such as toprefs.
        self.parser = parser

        # A dict that can be used from the outside to store some external metadata
        # about the Reference.  For example, Parser._add_reference() uses it to
        # determine of the ref had already been added, and Render.preprocess() uses
        # it to store a flag as to whether the ref has any renderable content.
        self.userdata = {}

        # Whether this is an implicitly generated module reference (i.e. a module that
        # lacks a @module tag but yet has documented elements).
        self.implicit = False
        # The type of reference this is: 'module', 'class', 'field', 'section', 'manual'
        # or a special 'search' type for the search page.
        self.type = None
        # Lua source file the ref was parsed from
        self.file = None
        # Line number from the above file where the ref was declared
        self.line = None
        # A stack of Reference objects this ref is contained within. Used to resolve names by
        # crawling up the scope stack.
        self.scopes = None
        # Name of symbol for @within
        self.within = None
        # The (string) name of the section this Reference belongs to.
        self.section = None
        # The Reference object (of type 'section') for the above section name
        self.sectionref = None
        # The original as-parsed name of the reference.  This is like the name property
        # but whereas name is normalized (e.g. Class:method is normalized to Class.method),
        # the symbol is how it appears in code (e.g. Class:method) and is used for
        # display purposes.
        #
        # Some References such as type=manual don't have symbols.
        self.symbol = None
        # Contextual information depending on type (e.g. for functions it's information
        # about arguments).
        self.extra = None
        # A list of lines containing the documented content for this section.  Each element
        # is a 2-tuple in the form (line number, text) where line number is the specific line
        # in self.file where the comment appears, and text is in markdown format.
        self.content = []
        # A map of modifiers that apply to this Reference that affect how it is rendered
        self.flags = {}

        # Internal caches for computed properties
        #
        # Fully scoped and normalized reference name
        self._name = None
        # Name of the top-level symbol this Reference belongs to
        self._topsym = None
        # Display name of the Referencae name
        self._display = None
        # Compact form of display name (topsym stripped)
        self._display_compact = None

        if kwargs:
            self.update(**kwargs)

    def __repr__(self):
        return 'Reference(type={}, _name={}, symbol={}, file={}, line={})'.format(
            self.type, self._name, self.symbol, self.file, self.line
        )

    def update(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)
        if self.type == 'classmod':
            self.type = 'class'
        self._name = None
        self._topsym = None
        self._display = None

    @property
    def scope(self):
        """
        The immediate scope of the Reference, or None if this Reference has no
        containing scope (i.e. implicit module references, manual, and search).
        """
        return self.scopes[-1] if self.scopes else None

    @property
    def name(self):
        """
        The fully qualified proper name by which this Reference can be linked.
        """
        if not self._name:
            self._set_name()
        return self._name

    @property
    def topsym(self):
        """
        Returns the symbol of the top-level
        """
        if not self._topsym:
            self._set_topsym()
        return self._topsym

    @property
    def topref(self):
        """
        Returns the Reference object for the top-level resource this ref
        belongs to.
        """
        # If there are no scopes, we *are* the topref
        return self if not self.scopes else self.parser.refs[self.topsym]

    @property
    def display(self):
        if not self._display:
            self._set_name()
        return self._display

    @property
    def display_compact(self):
        display = self.flags.get('display')
        if display:
            return display
        elif self.symbol.startswith(self.topsym):
            return self.symbol[len(self.topsym):].lstrip(':.')
        else:
            return self.symbol

    @property
    def hierarchy(self):
        if self.type != 'class':
            return []
        else:
            classes = [self]
            while classes[0].flags.get('inherits'):
                superclass = self.parser.refs.get(classes[0].flags['inherits'])
                if not superclass:
                    break
                else:
                    classes.insert(0, superclass)
            return classes

    def _set_name(self):
        """
        Derives the fully qualified name for this reference based on the scope.  These aren't
        necessarily *globally* unique, but they must be unique within a given top-level
        reference.  This is because the main purpose of the name is to be used as link
        anchors within a given page, and each top-level ref gets its own page.

        For class functions and fields, these will be qualified based on the class name.  Manual
        sections are qualified based on the manual page name.  @sections are *not* implicitly
        qualified, however, which means it's up to the user to ensure global uniqueness if
        cross-page references are needed.
        """
        assert(self.type and (self.type in ('manual', 'search') or self.symbol))

        # Construct fully qualified reference name, using the explicitly provided display
        # name if provided.
        display = self.flags.get('display')
        # Determine if there is an explicit @scope for this reference or the section
        # we belong to.
        scope = self.flags.get('scope')
        # If we were @rename'd
        rename = self.flags.get('rename')
        default_scope_name = self.scope.symbol if self.scope else self.symbol

        if self.type in ('field', 'function'):
            # Function and field types *must* have a scope
            assert(self.scopes)
            # Heuristic: if scope is a class and this field is under a static table, then
            # we consider it a metaclass static field and remove the 'static' part.
            if self.scope.type == 'class' and '.static.' in self.symbol:
                self.symbol = self.symbol.replace('.static', '')

            symbol = (rename or self.symbol).replace(':', '.')
            if not scope and self.sectionref:
                # No explicit scope defined on this ref, use the scope specified by the
                # section we're contained within, if available.
                scope = self.sectionref.flags.get('scope')
            if scope:
                # User-defined scope.
                symbol = re.split(r'[.:]', self.symbol)[-1]
                if scope != '.':
                    delim = ':' if ':' in self.symbol else '.'
                    symbol = '{}{}{}'.format(scope, delim, symbol)
                self.symbol = symbol
                self._name = symbol
                display = display or symbol
            elif '.' not in symbol:
                # Derive full name based on ref scope.
                parts = []
                for s in reversed(self.scopes):
                    parts.insert(0, s.name)
                    if s.type in ('class', 'module') or '.' in s.name:
                        # We've found a topref, so we're done.
                        break
                maybe = '{}.{}'.format('.'.join(parts), symbol)
                self._name = '{}.{}'.format(default_scope_name, symbol)
            else:
                self._name = symbol
        elif self.type == 'manual' or (self.scope and self.scope.type == 'manual'):
            if self.scope:
                # Section within manual
                self._name = '{}.{}'.format(default_scope_name, self.symbol)
            else:
                # Top level file
                self._name = default_scope_name
                display = display or self._name
        else:
            if rename:
                self.symbol = rename
            self._name = self.symbol

        if not display:
            if '.' not in self.symbol and ':' not in self.symbol and scope != '.':
                display = (scope or default_scope_name) + '.' + self.symbol
            else:
                display = self.symbol
        self._display = display

    def _set_topsym(self):
        """
        Derives the top-level symbol for this ref.

        Class and module refs are inherently top-level.  Others, like fields, depend
        on their scope.  For example the top-level symbol for a field of some class
        is the class name.
        """
        assert(self.type)
        # Find the class or module this Reference is associated with.
        # If type is a class or module then it is by definition associated with itself.
        if self.type in ('class', 'module') or not self.scope:
            self._topsym = self.name
        elif self.scope.type in ('manual', 'search'):
            self._topsym = self.scope.symbol
        else:
            assert(self.scopes)
            for s in reversed(self.scopes):
                if s.type == 'class' or s.type == 'module':
                    self._topsym = s.name
                    break
            else:
                log.error('%s:%s: could not determine which class or module %s belongs to', self.file, self.line, self.name)
                return
        return self

