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

__all__ = ['RefType', 'Reference', 'Context']

import re
import enum
from typing import Optional, Union, List, Dict, Any

from .log import log

class RefType(enum.Enum):
    UNKNOWN = ''
    MODULE = 'module'
    CLASS = 'class'
    FUNCTION = 'function'
    FIELD = 'field'
    SECTION = 'section'
    TABLE = 'table'
    MANUAL = 'manual'
    SEARCH = 'search'


class Context:
    """
    Keeps track of current file and line being processed.

    There is a single instance held by Parser that's used throughout the program.
    """
    def __init__(self):
        self.file: Optional[str] = None
        self.line: Optional[int] = None
        self.ref: Optional[Reference] = None

    # TODO: this method only takes file, line, and ref args, but we need
    # way to discriminate between explicitly set None or not set at all
    # if we switch away from kwargs
    def update(self, **kwargs) -> None:
        for k, v in kwargs.items():
            setattr(self, k, v)
        if kwargs.get('ref'):
            assert(isinstance(self.ref, Reference))
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
    def __init__(self, parser_refs: Dict[str, 'Reference'], file: str, **kwargs) -> None:
        # The refs dict from the Parser object that created us.  Used to resolve ancestor
        # references (topref and hierarchy)
        self.parser_refs = parser_refs

        # Lua source file the ref was parsed from
        self.file: str = file

        # These are mandatory attributes: all Reference objects must have values assigned,
        # so for type purposes we don't allow None, although we'll initialize to the zero
        # value for that type.
        #
        # The type of reference this is: 'module', 'class', 'field', 'section', 'manual'
        # or a special 'search' type for the search page.
        self.type: RefType = RefType.UNKNOWN
        # The original as-parsed name of the reference.  This is like the name property
        # but whereas name is normalized (e.g. Class:method is normalized to Class.method),
        # the symbol is how it appears in code (e.g. Class:method) and is used for
        # display purposes.
        #
        # All References must have symbols. 
        self.symbol: str = ''
        # Whether this is an implicitly generated module reference (i.e. a module that
        # lacks a @module tag but yet has documented elements).
        self.implicit = False
        # Number of nested scope levels this reference belongs to, where -1 is an implicit
        # module.  Note that this is different from flags['level'] (which indicates the
        # level of a heading).
        self.level = 0

        # These are optional attributes, which are only set depending on the type.
        #
        # Line number from the above file where the ref was declared
        self.line: Optional[int] = None
        # A stack of Reference objects this ref is contained within. Used to resolve names by
        # crawling up the scope stack.
        self.scopes: Optional[list[Reference]] = None
        # Name of symbol for @within
        self.within: Optional[str] = None
        # The (string) name of the section this Reference belongs to.
        self.section: Optional[str] = None
        # The Reference object (of type 'section') for the above section name
        self.sectionref: Optional[Reference] = None

        # A dict that can be used from the outside to store some external metadata
        # about the Reference.  For example, Parser._add_reference() uses it to
        # determine of the ref had already been added, and Render.preprocess() uses
        # it to store a flag as to whether the ref has any renderable content.
        self.userdata: dict[str, Any] = {}
        # Contextual information depending on type (e.g. for functions it's information
        # about arguments).
        self.extra: list[str] = []
        # A list of lines containing the documented content for this section.  Each element
        # is a 2-tuple in the form (line number, text) where line number is the specific line
        # in self.file where the comment appears, and text is in markdown format.
        self.content: list[tuple[int, str]] = []
        # A map of modifiers that apply to this Reference that affect how it is rendered
        self.flags: dict[str, Any] = {}

        # Internal caches for computed properties
        #
        # Fully scoped and normalized reference name
        self._name: str|None = None
        # Name of the top-level symbol this Reference belongs to
        self._topsym: str|None = None
        # Display name of the Referencae name
        self._display: str|None = None
        # Compact form of display name (topsym stripped)
        self._display_compact: str|None = None

        if kwargs:
            self.update(**kwargs)

    def __repr__(self) -> str:
        return 'Reference(type={}, _name={}, symbol={}, file={}, line={})'.format(
            self.type.value, self._name, self.symbol, self.file, self.line
        )

    def update(self, **kwargs) -> None:
        if 'type' in kwargs:
            RefType(kwargs['type'])
        for k, v in kwargs.items():
            setattr(self, k, v)
        self._name = None
        self._topsym = None
        self._display = None

    @property
    def scope(self) -> Union['Reference', None]:
        """
        The immediate scope of the Reference, or None if this Reference has no
        containing scope (i.e. implicit module references, manual, and search).
        """
        return self.scopes[-1] if self.scopes else None

    @property
    def name(self) -> str:
        """
        The fully qualified proper name by which this Reference can be linked.
        """
        if not self._name:
            self._set_name()
            assert(self._name)
        return self._name

    @name.setter
    def name(self, name: str) -> None:
        self._name = name

    @property
    def topsym(self) -> str:
        """
        Returns the symbol of the top-level
        """
        if not self._topsym:
            self._set_topsym()
            assert(self._topsym)
        return self._topsym

    @property
    def topref(self) -> 'Reference':
        """
        Returns the Reference object for the top-level resource this ref
        belongs to.
        """
        # If there are no scopes, we *are* the topref
        return self if not self.scopes else self.parser_refs[self.topsym]

    @property
    def display(self) -> str:
        if not self._display:
            self._set_name()
            assert(self._display is not None)
        return self._display

    @property
    def display_compact(self) -> str:
        display: str|None = self.flags.get('display')
        assert(isinstance(self.symbol, str))
        assert(isinstance(self.topsym, str))
        if display:
            return display
        elif self.symbol.startswith(self.topsym):
            return self.symbol[len(self.topsym):].lstrip(':.')
        else:
            return self.symbol

    @property
    def hierarchy(self) -> List['Reference']:
        if self.type != RefType.CLASS:
            return []
        else:
            clsrefs: list[Reference] = [self]
            while clsrefs[0].flags.get('inherits'):
                superclass = self.parser_refs.get(clsrefs[0].flags['inherits'])
                if not superclass:
                    break
                else:
                    clsrefs.insert(0, superclass)
            return clsrefs

    def _set_name(self) -> None:
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
        assert(self.symbol)

        # Construct fully qualified reference name, using the explicitly provided display
        # name if provided.
        display: str|None = self.flags.get('display')
        # Determine if there is an explicit @scope for this reference or the section
        # we belong to.
        scope: str|None = self.flags.get('scope')
        # If we were @rename'd
        rename: str|None = self.flags.get('rename')
        default_scope_name = self.scope.symbol if self.scope else self.symbol

        if self.type in (RefType.FIELD, RefType.FUNCTION):
            # Function and field types *must* have a scope
            assert(self.scopes)
            assert(self.scope is not None)
            # Heuristic: if scope is a class and this field is under a static table, then
            # we consider it a metaclass static field and remove the 'static' part.
            if self.scope.type == RefType.CLASS and '.static.' in self.symbol:
                self.symbol = self.symbol.replace('.static', '')

            symbol = (rename or self.symbol).replace(':', '.')
            if not scope and self.sectionref:
                # No explicit scope defined on this ref, use the scope specified by the
                # section we're contained within, if available.
                scope = self.sectionref.flags.get('scope')
            if scope:
                # User-defined scope.
                symbol: str = re.split(r'[.:]', self.symbol)[-1]
                if scope != '.':
                    delim = ':' if ':' in self.symbol else '.'
                    symbol = '{}{}{}'.format(scope, delim, symbol)
                self.symbol = symbol
                self._name = symbol
                display = display or symbol
            elif '.' not in symbol:
                # Derive full name based on ref scope.
                parts: list[str] = []
                for s in reversed(self.scopes):
                    parts.insert(0, s.name)
                    if s.type in (RefType.CLASS, RefType.MODULE) or '.' in s.name:
                        # We've found a topref, so we're done.
                        break
                self._name = '{}.{}'.format(default_scope_name, symbol)
            else:
                self._name = symbol
        elif self.type == RefType.MANUAL or (self.scope and self.scope.type == RefType.MANUAL):
            if self.scope:
                # Section within manual
                self._name = '{}.{}'.format(default_scope_name, self.symbol)
            else:
                # Top level file
                self._name = default_scope_name
                display = display or self._name
        else:
            self._name = self.symbol

        if rename:
            if '.' in rename:
                self.symbol = rename
            elif self.symbol:
                self.symbol = ''.join(re.split(r'([.:])', self.symbol)[:-1]) + rename

        if not display:
            # Make LSP happy (self.symbol may have been redefined above0
            assert(self.symbol is not None)
            assert(default_scope_name)
            if '.' not in self.symbol and ':' not in self.symbol and scope != '.':
                display = (scope or default_scope_name) + '.' + self.symbol
            else:
                display = self.symbol
        self._display = display

    def _set_topsym(self) -> None:
        """
        Derives the top-level symbol for this ref.

        Class and module refs are inherently top-level.  Others, like fields, depend
        on their scope.  For example the top-level symbol for a field of some class
        is the class name.
        """
        assert(self.type != RefType.UNKNOWN)
        # Find the class or module this Reference is associated with.
        # If type is a class or module then it is by definition associated with itself.
        if self.type in (RefType.CLASS, RefType.MODULE) or not self.scope:
            self._topsym = self.name
        elif self.scope.type in (RefType.MANUAL, RefType.SEARCH):
            self._topsym = self.scope.symbol
        else:
            assert(self.scopes)
            for s in reversed(self.scopes):
                if s.type == RefType.CLASS or s.type == RefType.MODULE:
                    self._topsym = s.name
                    break
            else:
                log.error('%s:%s: could not determine which class or module %s belongs to', self.file, self.line, self.name)
