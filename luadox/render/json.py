# Copyright 2023 Jason Tackaberry
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

__all__ = ['JSONRenderer']

import json
import os
from typing import Tuple, List, Dict, Any

from ..log import log
from ..parse import *
from ..reference import *
from ..utils import *
from .base import Renderer

class JSONRenderer(Renderer):
    def _generate(self, toprefs: List[TopRef]) -> Dict[str, Any]:
        project: Dict[str, Any] = {
            'apiVersion': 'v1alpha1',
            'kind': 'luadox',
        }
        name = self.config.get('project', 'name', fallback=None)
        if name:
            project['name'] = name
        title = self.config.get('project', 'title', fallback=None)
        if title:
            project['title'] = title

        classes = project.setdefault('classes', [])
        modules = project.setdefault('modules', [])
        manuals = project.setdefault('manuals', [])

        for topref in toprefs:
            if isinstance(topref, ClassRef):
                classes.append(self._render_classmod(topref))
            elif isinstance(topref, ModuleRef):
                modules.append(self._render_classmod(topref))
            elif isinstance(topref, ManualRef):
                manuals.append(self._render_manual(topref))
        return project

    def _render_content(self, content: Content) -> List[Dict[str, Any]]:
        output = []
        for elem in content:
            if isinstance(elem, Markdown):
                md = self._render_markdown(elem)
                if md['value']:
                    output.append(md)
            elif isinstance(elem, Admonition):
                output.append({
                    'type': 'admonition',
                    'level': elem.type,
                    'title': elem.title,
                    'content': self._render_content(elem.content),
                })
            elif isinstance(elem, SeeAlso):
                output.append({
                    'type': 'see',
                    'refs': [{'refid': refid} for refid in elem.refs]
                })
        return output

    def _render_types(self, types: List[str]) -> List[Dict[str, Any]]:
        resolved: list[dict[str, Any]] = []
        for tp in types:
            ref = self.parser.resolve_ref(tp)
            if ref:
                resolved.append({
                    'name': tp,
                    'refid': ref.id,
                })
            else:
                resolved.append({'name': tp})
        return resolved

    def _render_markdown(self, md: Markdown) -> Dict[str, Any]:
        return {
            'type': 'markdown',
            'value': md.get().strip(),
        }

    def _render_section(self, colref: CollectionRef, **kwargs) -> Dict[str, Any]:
        section: Dict[str, Any] = {
            'id': colref.id,
            'type': colref.type,
            'symbol': colref.symbol,
            'heading': colref.heading,
        }
        section.update({k:v for k, v in kwargs.items() if v})
        content = self._render_content(colref.content)
        if content:
            section['content'] = content
        return section

    def _init_topref(self, topref: TopRef, **kwargs) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
        sections: List[Dict[str, Any]] = []
        out: Dict[str, Any] = {
            'id': topref.id,
            'type': topref.type,
            'name': topref.name,
        }
        # Include kwargs-provided fields ahead of sections
        out.update({k:v for k, v in kwargs.items() if v})
        out['sections'] = sections
        return out, sections

    def _render_classmod(self, topref: TopRef) -> Dict[str, Any]:
        hierarchy = None
        if isinstance(topref, ClassRef):
            h = topref.hierarchy
            if len(h) > 1:
                hierarchy = [{'name': ref.name, 'refid': ref.id} for ref in h]

        out, sections = self._init_topref(topref, hierarchy=hierarchy)

        for colref in topref.collections:
            self.ctx.update(ref=colref)
            section = self._render_section(colref, compact=colref.compact)
            sections.append(section)

            fields: List[Dict[str, Any]] = []
            for ref in colref.fields:
                field = self._render_field(ref)
                fields.append(field)
            if fields:
                section['fields'] = fields

            functions: List[Dict[str, Any]] = []
            for ref in colref.functions:
                func = self._render_field(ref)
                if ref.params:
                    params = func.setdefault('params', [])
                    for name, types, content in ref.params:
                        param: dict[str, Any] = {'name': name}
                        params.append(param)
                        if types:
                            param['types'] = self._render_types(types)
                        if content:
                            param['content'] = self._render_content(content)
                if ref.returns:
                    returns = func.setdefault('returns', [])
                    for types, content in ref.returns:
                        ret: dict[str, Any] = {}
                        returns.append(ret)
                        if types:
                            ret['types'] = self._render_types(types)
                        if content:
                            ret['content'] = self._render_content(content)
                functions.append(func)
            if functions:
                section['functions'] = functions

        return out

    def _render_field(self, ref: FieldRef) -> Dict[str, Any]:
        field: dict[str, Any] = {
            'id': ref.id,
            'name': ref.name,
            'display': ref.display,
        }
        if ref.types:
            field['types'] = self._render_types(ref.types)
        if ref.meta:
            field['meta'] = ref.meta
        content = self._render_content(ref.content)
        if content:
            field['content'] = content
        return field

    def _render_manual(self, topref: ManualRef) -> Dict[str, Any]:
        out, sections = self._init_topref(topref)
        for colref in topref.collections:
            self.ctx.update(ref=colref)
            section = self._render_section(colref, level=colref.level)
            sections.append(section)
        return out

    def _get_outfile(self, dst: str, ext: str = '.json') -> str:
        if not dst:
            dst = './luadox' + ext
            log.warn('"out" is not defined in config file, assuming %s', dst)
        if not os.path.isfile(dst) and not dst.endswith(ext):
            dst = os.path.join(dst, 'luadox' + ext)
        dirname = os.path.dirname(dst)
        if dirname and not os.path.exists(dirname):
            os.makedirs(dirname, exist_ok=True)
        log.info('rendering to %s', dst)
        return dst


    def render(self, toprefs: List[TopRef], dst: str) -> None:
        """
        Renders toprefs as JSON to the given output directory or file.
        """
        project = self._generate(toprefs)
        outfile = self._get_outfile(dst)
        with open(outfile, 'w') as f:
            json.dump(project, f, indent=2)
