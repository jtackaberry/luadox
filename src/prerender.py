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

__all__ = ['Prerenderer']

from typing import Union, Tuple, List

from .log import log
from .reference import *
from .parse import Parser
from .utils import *

class Prerenderer:
    """
    The prerender stage populates the specific typed Reference fields needed for rendering.
    generates intermediate data structures used by renderers.  All
    references are resolved, and tags (such as @param) are parsed and validated.
    """
    def __init__(self, parser: Parser):
        self.parser = parser
        self.config = parser.config
        self.ctx = parser.ctx

    def process(self) -> List[TopRef]:
        """
        Preprocesses all Reference objects created by the parser by handling all remaining tags within content docstrings, normalizing content to markdown, and returns a sorted list of toprefs for rendering.
        """
        toprefs: list[TopRef] = []
        for ref in self.parser.topsyms.values():
            if isinstance(ref, (ClassRef, ModuleRef)):
                self._do_classmod(ref)
            elif isinstance(ref, ManualRef):
                self._do_manual(ref)
            toprefs.append(ref)
        toprefs.sort(key=lambda ref: (ref.type, ref.symbol))
        return toprefs

    def _do_classmod(self, topref: Union[ClassRef, ModuleRef]):
        has_content = False
        for colref in self.parser.get_collections(topref):
            self.ctx.update(ref=colref)

            # Parse out section heading and body.
            _, _, md = self.parser.content_to_markdown(colref.content)
            if isinstance(colref, (ClassRef, ModuleRef)):
                heading = colref.symbol
                body = md
            else:
                heading, body = get_first_sentence(md)
                # Fall back to section name if there is no content for the heading.
                heading = heading.strip() or colref.name

            colref.heading = heading
            colref.body = body
            topref.collections.append(colref)

            functions = list(self.parser.get_elements_in_collection(FunctionRef, colref))
            fields = list(self.parser.get_elements_in_collection(FieldRef, colref))
            has_content = has_content or colref.body or functions or fields

            colref.compact = colref.flags.get('compact', [])
            fullnames: bool = colref.flags.get('fullnames', False)

            for ref in fields:
                self.ctx.update(ref=ref)
                _, _, md = self.parser.content_to_markdown(ref.content)
                ref.title = ref.flags.get('display') or (ref.name if fullnames else ref.symbol)
                ref.types = ref.flags.get('type', [])
                ref.meta = ref.flags.get('meta')
                ref.md = md
                colref.fields.append(ref)


            for ref in functions:
                self.ctx.update(ref=ref)
                paramsdict, returns, md = self.parser.content_to_markdown(ref.content)
                # args is as defined in the function definition in source, while params is
                # based on tags.  Log a warning for any undocumented argument as long as
                # there is at least one documented parameter.
                params: List[Tuple[str, List[str], str]] = []
                # ref.extra contains the list of parameter names as parsed from the
                # source.  Construct the params list based on 
                for param in ref.extra:
                    try:
                        params.append((param, *paramsdict[param]))
                    except KeyError:
                        params.append((param, [], ''))
                        if paramsdict:
                            log.warning('%s:%s: %s() missing @tparam for "%s" parameter', ref.file, ref.line, ref.name, param)

                ref.title = ref.display
                ref.params = params
                ref.returns = returns
                ref.meta = ref.flags.get('meta')
                ref.md = md
                colref.functions.append(ref)

        topref.userdata['empty'] = not has_content

    def _do_manual(self, topref: ManualRef):
        if topref.content:
            # Include any preamble before the first heading.
            _, _, md = self.parser.content_to_markdown(topref.content, strip_comments=False)
            topref.md = md
        for ref in self.parser.get_collections(topref):
            # Manuals only have SectionRefs
            assert(isinstance(ref, SectionRef))
            self.ctx.update(ref=ref)
            _, _, md = self.parser.content_to_markdown(ref.content, strip_comments=False)
            ref.heading = ref.display
            ref.body = md
            ref.level = int(ref.flags['level'])
            topref.collections.append(ref)

