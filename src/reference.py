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

__all__ = [
    'RefT', 'Reference', 'CollectionRef', 'TopRef',
    'ModuleRef', 'ClassRef', 'ManualRef', 'SectionRef', 'TableRef',
    'FunctionRef', 'FieldRef'
]

import re
from dataclasses import dataclass, field, fields
from typing import TypeVar, Optional, Union, List, Tuple, Dict, Any

from .log import log
from .utils import Content

# Used for generics taking Reference types
RefT = TypeVar('RefT', bound='Reference')

@dataclass
class Reference:
    """
    Reference is the base class to anything that can be, uh, referenced.  It is the basis
    of all documentable elements, and applies to modules, classes, fields, functions,
    sections, manual pages, etc.

    A special type of reference called a top-level reference -- or "topref" -- is anything
    that will be rendered into its own separate page in the documentation. All references
    can be traced back to a topref.

    A Reference can be globally resolved by the combination of its topref (determines
    the page the ref exists on) and its name (which uniquely identifies the ref on the
    top-level page).

    One of the Reference subclasses should normally be used (e.g. ClassRef), which are
    considered typed references.  Reference itself however may be directly instantiated
    (called an untyped reference) and later converted to a typed reference by using the
    clone_from() class method on one of the subclasses.
    """
    #
    # All Reference instances must have values assigned, so for type purposes we don't
    # allow None, although we'll initialize to the zero value for that type.
    #

    # The refs dict from the Parser object that created us.  Used to resolve ancestor
    # references (topref and hierarchy)
    parser_refs: Dict[str, 'Reference']
    # Lua source file the ref was parsed from
    file: str
    # Default type, subclasses override
    type: str = ''
    # The original as-parsed name of the reference.  This is like the name property
    # but whereas name is normalized (e.g. Class:method is normalized to Class.method),
    # the symbol is how it appears in code (e.g. Class:method) and is used for
    # display purposes. All References must have symbols. 
    symbol: str = ''
    # Whether this is an implicitly generated module reference (i.e. a module that
    # lacks a @module tag but yet has documented elements).
    implicit: bool = False
    # Number of nested scope levels this reference belongs to, where -1 is an implicit
    # module.  Note that this is different from flags['level'] (which indicates the
    # level of a heading).
    level: int = 0

    #
    # These are optional attributes, which are only set depending on the type.
    #

    # Line number from the above file where the ref was declared
    line: Optional[int] = None
    # A stack of Reference objects this ref is contained within. Used to resolve names by
    # crawling up the scope stack.
    scopes: Optional[List['Reference']] = None
    # Name of symbol for @within
    within: Optional[str] = None
    # The collection the ref belongs to
    collection: Optional['Reference'] = None

    # A dict that can be used from the outside to store some external metadata
    # about the Reference.  For example, Parser._add_reference() uses it to
    # determine of the ref had already been added, and the pre-render stage uses
    # it to store a flag as to whether the ref has any renderable content.
    userdata: Dict[str, Any] = field(default_factory=dict)
    # Contextual information depending on type (e.g. for functions it's information
    # about arguments).
    extra: List[str] = field(default_factory=list)
    # A list of lines containing the documented content for this collection.  Each element
    # is a 2-tuple in the form (line number, text) where line number is the specific line
    # in self.file where the comment appears, and text is in markdown format.
    raw_content: List[Tuple[int, str]] = field(default_factory=list)
    # The processed (from raw) content which is set during the prerender stage
    content: Content = field(default_factory=Content)
    # A map of modifiers that apply to this Reference that affect how it is rendered,
    # mostly from @tags.  These are accumulated in the flags dict until all parsing
    # is done and then the parser process stage will convert these to proper fields in the
    # respective typed ref.
    flags: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def clone_from(cls, ref: 'Reference', **kwargs) -> 'Reference':
        """
        Creates a new Reference instance that clones attributes from the given ref object,
        and sets (or overrides) attributes via kwargs.

        This method can be used to create a typed reference (instance of a Reference
        subclass) from an untyped reference (Reference instance).
        """
        # We must only clone fields that are allowed by the target class.
        allowed = {f.name for f in fields(cls)}
        args = {
            k: v for k, v in ref.__dict__.items() 
                 if k in allowed and k[0] != '_' and k != 'type'
        }
        args.update(kwargs)
        return cls(**args)

    def __post_init__(self):
        # Fully scoped and normalized reference name (cached from _set_name())
        self._name: str|None = None
        # Original symbol as 
        self._symbol: str|None = None
        # Name of the top-level symbol this Reference belongs to (cached from
        # _set_topsym())
        self._topsym: str|None = None
        # Display name of the Reference name (cached from _set_name())
        self._display: str|None = None

    def __str__(self) -> str:
        return '{}(type={}, _name={}, symbol={}, file={}, line={} impl={})'.format(
            self.__class__.__name__,
            self.type, self._name, self.symbol, self.file, self.line, self.implicit
        )

    def clear_cache(self):
        self._name = None
        self._symbol = None
        self._topsym = None
        self._display = None

    @property
    def scope(self) -> Union['Reference', None]:
        """
        The immediate scope of the Reference, or None if this Reference has no containing
        scope.

        Scopes are modules, classes, tables, or manual pages.  Other ref types such as
        sections can't be scopes.

        Implicit modules and manual pages are the only typed refs without a scope.  Other
        top refs (classes and explicit modules) will have the implicit module as their
        scope.
        """
        return self.scopes[-1] if self.scopes else None

    @property
    def name(self) -> str:
        """
        The fully qualified proper name by which this Reference can be linked.  The
        name is not necessarily globally unique, but *is* unique within its topref.
        """
        if not self._name:
            self._set_name()
            assert(self._name)
        return self._name

    @property
    def id(self) -> str:
        """
        A globally unique identifier of the Reference.
        """
        return f'{self.topref.type}#{self.topsym}#{self.name}'

    @property
    def topsym(self) -> str:
        """
        Returns the symbol name of our top-level reference.

        This does *not* honor @within.
        """
        if not self._topsym:
            self._set_topsym()
            assert(self._topsym)
        return self._topsym

    @property
    def topref(self) -> 'Reference':
        """
        Returns the Reference object for the top-level reference this ref
        belongs to.  If we're already a top-level Ref (e.g. class or module)
        then self is returned.

        This does *not* honor @within.
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
        """
        Compact form of display name (topsym stripped)
        """
        display: str|None = self.flags.get('display')
        if display:
            return display
        else:
            assert(isinstance(self.symbol, str))
            assert(isinstance(self.topsym, str))
            if self.symbol.startswith(self.topsym):
                return self.symbol[len(self.topsym):].lstrip(':.')
            else:
                return self.symbol

    def _apply_rename(self) -> None:
        """
        Applies a @rename tag to the symbol attribute.
        """
        assert(self.symbol)
        if not self._symbol:
            # Retain original symbol in case rename is specified
            self._symbol = self.symbol

        # If we were @rename'd
        rename_tag: str|None = self.flags.get('rename')
        if rename_tag:
            if '.' in rename_tag:
                # Fully qualified name provided, take it as-is
                self.symbol = rename_tag
            else:
                # Non-qualified name provided, take it as relative to the current symbol
                self.symbol = ''.join(re.split(r'([.:])', self._symbol)[:-1]) + rename_tag


    def _set_name(self) -> None:
        """
        Derives the fully qualified name for this reference based on the scope.  Ref names
        necessarily *globally* unique, but they must be unique within a given top-level
        reference.  This is because the main purpose of the name is to be used as link
        anchors within a given page, and each top-level ref gets its own page.
        """
        self._apply_rename()
        self._name = self.symbol
        self._display = self.flags.get('display') or self.symbol


    def _set_topsym(self) -> None:
        """
        Determines (and caches) the top-level symbol for this ref.

        Class and module refs are inherently top-level.  Others, like fields, depend
        on their scope.  For example the top-level symbol for a field of some class
        is the class name.
        """
        # This is the default logic for non-top-level refs, which crawls the reference's
        # scopes upward until a TopRef instance is encountered.  TopRef subclass
        # overrides.
        assert(self.scopes)
        for s in reversed(self.scopes):
            if isinstance(s, TopRef):
                self._topsym = s.name
                break
        else:
            log.error('%s:%s: could not determine which class or module %s belongs to', self.file, self.line, self.name)


#
# Typed References follow. Typed refs are cloned from untyped refs by the parser once the
# type is known.
#
# Fields defined by typed refs are actually populated during the parser's process stage
# (after all file parsing has completed, but before rendering).


@dataclass
class FieldRef(Reference):
    type: str = 'field'

    # User-defined meta value (parsed from @meta via flags)
    meta: Optional[str] = None
    # Renderable display name that takes tags such as @fullnames into account
    title: str = ''
    # Allowed types for this field, which can be empty if no @type tag
    types: List[str] = field(default_factory=list)

    def _set_name(self) -> None:
        """
        Derive fully qualified field name (relative to topref).  For class fields (i.e. attributes), these will be qualified based on the class name.
        """
        # Field types must have a scope
        assert(self.scopes and self.scope)

        self._apply_rename()

        # Heuristic: if scope is a class and this field is under a static table, then
        # we consider it a metaclass static field and remove the 'static' part.
        if isinstance(self.scope, ClassRef) and '.static.' in self.symbol:
            self.symbol = self.symbol.replace('.static', '')

        # The display name for this ref, initialized to the @display tag value if provided
        display: str|None = self.flags.get('display')
        # For the ref name, start with the symbol for now.
        name: str = self.symbol

        # Determine if there is an explicit @scope for this reference or the collection we
        # belong to.
        scope_tag: str|None = self.flags.get('scope')
        if not scope_tag and self.collection:
            # No explicit scope defined on this ref, use the scope specified by the
            # collection we're contained within, if available.
            scope_tag = self.collection.flags.get('scope')
        if scope_tag:
            # @scope tag was given. Take the tail end of the symbol as we're going to
            # requalify it under the @scope value.
            symbol: str = re.split(r'[.:]', self.symbol)[-1]
            if scope_tag != '.':
                # Non-global scope.  Determine what delimiter we should use based on the
                # original symbol.
                delim = ':' if ':' in self.symbol else '.'
                symbol = f'{scope_tag}{delim}{symbol}'
            self.symbol = symbol
            name = symbol
            display = display or symbol
        elif '.' not in self.symbol:
            # No @scope given, but we need to qualify the name based on the (unqualified)
            # symbol and scope.
            name = f'{self.scope.symbol}.{self.symbol}'
            display = display or name

        self._name = name.replace(':', '.')
        self._display = display or self.symbol


# It's a bit dubious to subclass FieldRef here -- functions are obviously not fields --
# but in practice they are handled very similarly, so we're taking the easy way out on
# this one.
@dataclass
class FunctionRef(FieldRef):
    type: str = 'function'

    # List of (name, types, docstring)
    params: List[Tuple[str, List[str], Content]] = field(default_factory=list)
    # List of (types, docstring)j
    returns: List[Tuple[List[str], Content]] = field(default_factory=list)


@dataclass
class CollectionRef(Reference):
    """
    A collection can fields and functions, 
    """
    heading: str = ''
    # List of 'functions' and/or 'fields' to indicate which should be rendered in compact
    # form
    compact: List[str] = field(default_factory=list)
    functions: List['FunctionRef'] = field(default_factory=list)
    fields: List['FieldRef'] = field(default_factory=list)

@dataclass
class TopRef(CollectionRef):
    """
    Represents a top-level reference such as class or module.
    """
    # Ordered list of collections within this topref, which respects @within and @reorder
    collections: List[CollectionRef] = field(default_factory=list)

    def _set_topsym(self) -> None:
        # By default, the topref of a topref is itself
        self._topsym = self.name


@dataclass
class ManualRef(TopRef):
    type: str = 'manual'

@dataclass
class ModuleRef(TopRef):
    type: str = 'module'

@dataclass
class ClassRef(TopRef):
    type: str = 'class'

    @property
    def hierarchy(self) -> List['Reference']:
        clsrefs: list[Reference] = [self]
        while clsrefs[0].flags.get('inherits'):
            superclass = self.parser_refs.get(clsrefs[0].flags['inherits'])
            if not superclass:
                break
            else:
                clsrefs.insert(0, superclass)
        return clsrefs


@dataclass
class SectionRef(CollectionRef):
    type: str = 'section'
    # For sections within manuals, this is the heading level
    level: int = 0

    def _set_name(self) -> None:
        """
        Fully qualified (relative to topref) name of the section. 

        Manual sections are qualified based on the manual page name.  @sections are *not*
        implicitly qualified, however, which means it's up to the user to ensure global
        uniqueness if cross-page section references are needed.
        """
        if isinstance(self.scope, ManualRef):
            # We are a section within a manual
            self._name = '{}.{}'.format(self.scope.symbol, self.symbol)
            self._display = self.flags.get('display') or self.symbol
        else:
            super()._set_name()

@dataclass
class TableRef(CollectionRef):
    type: str = 'table'

