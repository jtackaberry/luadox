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

import re
import typing
from dataclasses import dataclass, field
from typing import NewType, Tuple, Type, Dict, List, Union, Generic, TypeVar, Pattern, Any, Optional, Generator

from .log import log

class Tag:
    """
    Base class for all tag objects.
    """
    # The default type name is based on the class name, but this allows subclasses to
    # override type name if the class name takes a different form.
    _type: Union[str, None] = None

    @property
    def type(self):
        return self._type or self.__class__.__name__.lower()[:-3]

@dataclass
class UnrecognizedTag(Tag):
    """
    This is a special type that's yielded when we encounter an unrecognized tag during tag
    parsing.  It allows the caller to decide how to handle things while not interrupting
    processing of other tags that occur on the same line.
    """
    name: str

#
# These are the tags that influence LuaDox processing/rendering.
#

@dataclass
class CollectionTag(Tag):
    name: str

@dataclass
class SectionTag(CollectionTag):
    pass

@dataclass
class ClassTag(CollectionTag):
    pass

@dataclass
class ModuleTag(CollectionTag):
    pass

@dataclass
class TableTag(CollectionTag):
    pass

@dataclass
class WithinTag(Tag):
    name: str

@dataclass
class FieldTag(Tag):
    name: str
    desc: str

@dataclass
class AliasTag(Tag):
    name: str

@dataclass
class CompactTag(Tag):
    elements: List[str] = field(default_factory=lambda: ['fields', 'functions'])

@dataclass
class FullnamesTag(Tag):
    pass

@dataclass
class InheritsTag(Tag):
    superclass: str

@dataclass
class MetaTag(Tag):
    value: str

@dataclass
class ScopeTag(Tag):
    name: str

@dataclass
class RenameTag(Tag):
    name: str

@dataclass
class DisplayTag(Tag):
    name: str

@dataclass
class TypeTag(Tag):
    types: List[str]

@dataclass
class OrderTag(Tag):
    whence: str
    anchor: Optional[str] = None


#
# Content tags
#

@dataclass
class CodeTag(Tag):
    lang: Optional[str] = None

@dataclass
class UsageTag(CodeTag):
    pass

@dataclass
class ExampleTag(CodeTag):
    pass

@dataclass
class AdmonitionTag(Tag):
    title: Optional[str] = None

@dataclass
class NoteTag(AdmonitionTag):
    pass

@dataclass
class WarningTag(AdmonitionTag):
    pass

@dataclass
class SeeTag(Tag):
    refs: List[str]

@dataclass
class ParamTag(Tag):
    types: List[str]
    name: str
    desc: Optional[str] = None

@dataclass
class ReturnTag(Tag):
    types: List[str]
    desc: Optional[str] = None

T = TypeVar('T')
VarString = NewType('VarString', str)
class PipeList(Generic[T]):
    pass

class ParseError(ValueError):
    pass

TagMapType = Dict[str, Tuple[Type[Tag], Dict[str, type]]]

class TagParser:
    RE_TAG: Pattern[str] = re.compile(r'^ *@([^{]\S+) *(.*)')
    RE_COMMENTED_TAG: Pattern[str] = re.compile(r'^--+ *@([^{]\S+) *(.*)')

    # Dict that maps supported tags to their Tag class and arguments.  This represents
    # LuaDox's annotations.  See _get_tag_map().
    TAGMAP: TagMapType = {
        'module': (ModuleTag, {'name': str}),
        'class': (ClassTag, {'name': str, 'superclass': Optional[str]}),
        'section': (SectionTag, {'name': str}),
        'table': (TableTag, {'name': str}),
        'within': (WithinTag, {'name': str}),
        'field': (FieldTag, {'name': str, 'desc': VarString}),
        'alias': (AliasTag, {'name': str}),
        'compact': (CompactTag, {'elements': Optional[List[str]]}),
        'fullnames': (FullnamesTag, {}),
        'inherits': (InheritsTag, {'superclass': str}),
        'meta': (MetaTag, {'value': str}),
        'scope': (ScopeTag, {'name': str}),
        'rename': (RenameTag, {'name': str}),
        'display': (DisplayTag, {'name': str}),
        'type': (TypeTag, {'types': PipeList[str]}),
        'order': (OrderTag, {'whence': str, 'anchor': Optional[str]}),

        'code': (CodeTag, {'lang': Optional[str]}),
        'usage': (UsageTag, {'lang': Optional[str]}),
        'example': (ExampleTag, {'lang': Optional[str]}),
        'warning': (WarningTag, {'title': Optional[VarString]}),
        'note': (NoteTag, {'title': Optional[VarString]}),
        'see': (SeeTag, {'refs': List[str]}),
        'tparam': (ParamTag, {
            'types': PipeList[str], 'name': str, 'desc': Optional[VarString],
        }),
        'treturn': (ReturnTag, {
            'types': PipeList[str], 'desc': Optional[VarString],
        }),
    }


    def __init__(self):
        self.tagmap = self._get_tag_map()


    def _get_tag_map(self) -> TagMapType:
        """
        Returns the tag map that subsequent calls to parse() will support. Subclasses can
        implement to augment or replace LuaDox's annotation style.
        """
        return self.TAGMAP


    def _coerce_args(self, args: List[str], types: Dict[str, type]) -> Tuple[Dict[str, Any], int]:
        """
        Takes a raw list of string arguments and coerces them to the given types required
        for a tag.  A dict keyed on argument name (which corresponds to the fields in the
        tag's dataclass) is returned.

        AssertionError is raised if any argument type is invalid, or if there insufficient
        args to satisfy the mandatory fields.
        """
        outargs: Dict[str, Any] = {}
        # Convert each arg to the desired type if possible, raise if not.
        for n, (name, typ) in enumerate(types.items()):
            origin = typing.get_origin(typ)
            if origin == Union:
                subtypes = typing.get_args(typ)
                if type(None) in subtypes:
                    # This is an Optional, so we tolerate it missing
                    if n >= len(args):
                        break
                    typ = subtypes[0]
                    origin = typing.get_origin(typ)
                else:
                    # Internal problem, not related to user input
                    raise NotImplemented(f'unsupported tag arg union {typ} {subtypes}')

            try:
                arg = args[n]
            except IndexError:
                raise AssertionError(f'requires at least {n+1} arguments')

            if typ == int:
                assert arg.isdigit(), f'argument {n} must be a number'
                outargs[name] = int(arg)
            elif typ == VarString:
                outargs[name] = ' '.join(args[n:])
                return outargs, len(args)
            elif origin:
                # Handle generic types. Only the first subtype is considered.
                subtype = typing.get_args(typ)[0]
                if origin == PipeList:
                    outargs[name] = [subtype(x) for x in arg.split('|')]
                elif origin == list:
                    # Similar to VarString, consolidate everything after into a single list
                    outargs[name] = [subtype(x) for x in args[n:]]
                    return outargs, len(args)
            elif typ == str:
                outargs[name] = arg
            else:
                raise NotImplemented(f'unsupported tag arg type {typ}')

        return outargs, len(outargs)


    def parse(self, line: str, file: str, lineno: int, require_comment=True) -> Generator[Tag, None, None]:
        """
        Looks for a @tag in the given raw line of code, and returns the appropriate
        tag object if found, or None otherwise.
        """
        m = (self.RE_COMMENTED_TAG if require_comment else self.RE_TAG).search(line)
        if not m:
            return
        tag, args = m.groups()
        try:
            tagcls, argtypes = self.tagmap[tag]
        except KeyError:
            # Unrecognized tag, let caller decide by yielding this special tag type
            yield UnrecognizedTag(tag)
            return

        args = [arg.strip() for arg in args.split()]
        try:
            kwargs, consumed = self._coerce_args(args, argtypes)
            if len(args) > consumed:
                log.warning('%s:%s: tag @%s takes %d args but received %d, ignoring extra args', file, lineno, tag, len(argtypes), len(args))
            yield from self._handle_tag(tagcls, kwargs, file, lineno)
        except AssertionError as e:
            raise ParseError(f'@{tag} is invalid: {e.args[0]}')


    def _handle_tag(self, tagcls: Type[Tag], kwargs: Dict[str, Any], file: str, lineno: int) -> Generator[Tag, None, None]: # pyright: ignore
        """
        Yields one or more Tag objects given the tag class and arguments.  This is where
        subclasses can translate/transform tags to support non-LuaDox annotations.

        The base class implementation handles LuaDox annotations, but also supports
        LuaCATS annotations when it can be done in a transparent manner.
        """
        if tagcls == ClassTag and kwargs['name'].endswith(':'):
            # Support "@class name: parent" form used by LuaCATS/EmmyLua by generating
            # implicit @inherits.
            assert 'superclass' in kwargs, 'class name ends with colon but tag is missing parent class argument'
            yield ClassTag(name=kwargs['name'].rstrip(':'))
            yield InheritsTag(superclass=kwargs['superclass'])
        else:
            yield tagcls(**kwargs)

