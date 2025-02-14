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

__all__ = ['HTMLRenderer']

import sys
import os
import re
import mimetypes
from contextlib import contextmanager
from typing import Union, Tuple, List, Callable, Generator, Type, Optional

import commonmark.blocks
import commonmark_extensions.tables

from ..assets import assets
from ..log import log
from ..reference import *
from ..parse import *
from ..utils import *
from .base import Renderer

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

# Effectively disable implicit code blocks
commonmark.blocks.CODE_INDENT = 1000

class CustomRendererWithTables(commonmark_extensions.tables.RendererWithTables):
    def __init__(self, renderer: 'HTMLRenderer', *args, **kwargs):
        self.renderer = renderer
        self.parser = renderer.parser
        super().__init__(*args, **kwargs)

    def make_table_node(self, _):
        return '<table class="user">'

    def link(self, node, entering):
        if node.destination.startswith('luadox:'):
            refid = node.destination[7:]
            # If this raises KeyError it indicates a bug in the parser code
            ref = self.parser.refs_by_id[refid]
            node.destination = self.renderer._get_ref_href(ref)
        super().link(node, entering)

# https://github.com/GovReady/CommonMark-py-Extensions/issues/3#issuecomment-756499491
# Thanks to hughdavenport
class TableWaitingForBug3(commonmark_extensions.tables.Table):
    @staticmethod
    def continue_(parser, _=None):
        ln = parser.current_line
        if not parser.indented and commonmark.blocks.peek(ln, parser.next_nonspace) == "|":
            parser.advance_next_nonspace()
            parser.advance_offset(1, False)
        elif not parser.indented and commonmark.blocks.peek(ln, parser.next_nonspace) not in ("", ">", "`", None):
            pass
        else:
            return 1
        return 0
commonmark.blocks.Table = TableWaitingForBug3 # pyright: ignore


class HTMLRenderer(Renderer):
    def __init__(self, parser: Parser):
        super().__init__(parser)

        # Create a pseudo Reference for the search page using the special 'search' type.
        # This is used to ensure the relative paths are correct. Use the name '--search'
        # as this won't conflict with any user-provided names (because Lua comments begin
        # with '--').
        ref = TopRef(parser.refs, file='search.html', symbol='--search')
        ref.flags['display'] = 'Search'
        parser.refs['--search'] = ref

        self._templates = {
            'head': assets.get('head.tmpl.html').decode('utf8'),
            'foot': assets.get('foot.tmpl.html').decode('utf8'),
            'search': assets.get('search.tmpl.html').decode('utf8'),
        }
        self._assets_version = assets.hash()[:7]

    def _get_root_path(self) -> str:
        """
        Returns the path prefix for the document root, which is relative to the
        current context.
        """
        # The topref of the current context's reference.  The path will be relative to
        # this topref's file.
        assert(self.ctx.ref)
        viatopref = self.ctx.ref.topref
        if (isinstance(viatopref, ManualRef) and viatopref.name == 'index') or \
           viatopref.symbol == '--search':
            return ''
        else:
            return '../'

    def _get_ref_link_info(self, ref: Reference) -> Tuple[str, str]:
        """
        Returns (html file name, URL fragment) of the given Reference object.
        """
        # The top-level Reference object that holds this reference, which respects @within.
        topsym: str = ref.userdata.get('within_topsym') or ref.topsym
        try:
            topref = self.parser.refs[topsym]
        except KeyError:
            raise KeyError('top-level reference "%s" not found (from "%s")' % (topsym, ref.name)) from None

        prefix = self._get_root_path()
        if not isinstance(ref.topref, ManualRef) or ref.topref.name != 'index':
            prefix += '{}/'.format(topref.type)
        if isinstance(ref.topref, ManualRef) and ref.symbol:
            # Manuals don't use fully qualified fragments.
            fragment = '#' + ref.symbol if ref.scopes else ''
        else:
            fragment = '#{}'.format(ref.name) if ref.name != ref.topsym else ''
        return prefix + topsym + '.html', fragment

    def _get_ref_href(self, ref: Reference) -> str:
        """
        Returns the href src for the given Reference object, which is directly used
        in <a> tags in the rendered content.
        """
        file, fragment = self._get_ref_link_info(ref)
        return file + fragment

    def _permalink(self, id: str) -> str:
        """
        Returns the HTML for a permalink used for directly linkable references such
        as section headings, functions, fields, etc.
        """
        return '<a class="permalink" href="#{}" title="Permalink to this definition">¶</a>'.format(id)

    def _markdown_to_html(self, md: str) -> str:
        """
        Renders the given markdown as HTML and returns the result.
        """
        parser = commonmark_extensions.tables.ParserWithTables()
        ast = parser.parse(md)
        return CustomRendererWithTables(self).render(ast)

    def _content_to_html(self, content: Content) -> str:
        output = []
        for elem in content:
            if isinstance(elem, Markdown):
                output.append(self._markdown_to_html(elem.get()))
            elif isinstance(elem, Admonition):
                inner = self._content_to_html(elem.content)
                output.append(f'<div class="admonition {elem.type}"><div class="title">{elem.title}</div><div class="body">{inner.strip()}\n</div></div>')
            elif isinstance(elem, SeeAlso):
                refs = [self.parser.refs_by_id[id] for id in elem.refs]
                md = ', '.join(self.parser.render_ref_markdown(ref) for ref in refs)
                # HTML will have <p></p> tags so strip them out first.
                html = self._markdown_to_html(md).strip()[3:-4]
                output.append(f'<div class="see">See also {html}</div>')
            else:
                raise ValueError(f'unsupported content fragment type {type(elem)}')
        return '\n'.join(output)

    def _markdown_to_text(self, md: str) -> str:
        """
        Strips markdown codes from the given Markdown and returns the result.
        """
        # Code blocks
        text = recache(r'```.*?```', re.S).sub('', md)
        # Inline preformatted code
        text = recache(r'`([^`]+)`').sub('\\1', text)
        # Headings
        text = recache(r'#+').sub('', text)
        # Bold
        text = recache(r'\*([^*]+)\*').sub('\\1', text)
        # Link or inline image
        text = recache(r'!?\[([^]]*)\]\([^)]+\)').sub('\\1', text)

        # Clean up non-markdown things.
        # Reference with custom display
        text = recache(r'@{[^|]+\|([^}]+)\}').sub('\\1', text)
        # Just a reference
        text = recache(r'@{([^}]+)\}').sub('\\1', text)
        # Consolidate multiple whitespaces
        text = recache(r'\s+').sub(' ', text)
        return text

    def _content_to_text(self, content: Content) -> str:
        """
        Strips markdown codes from the given Content and returns the result.
        """
        output = []
        for elem in content:
            if isinstance(elem, Admonition):
                output.append(self._markdown_to_text(elem.title))
                output.append(self._content_to_text(elem.content))
            elif isinstance(elem, Markdown):
                output.append(self._markdown_to_text(elem.get()))
        return '\n'.join(output).strip()


    def _types_to_html(self, types: List[str]) -> str:
        """
        Resolves references in the given list of types, and returns HTML of
        all types in a human-readable string.
        """
        resolved: list[str] = []
        for tp in types:
            ref = self.parser.resolve_ref(tp)
            if ref:
                href = self._get_ref_href(ref)
                tp = '<a href="{}">{}</a>'.format(href, tp)
            resolved.append('<em>{}</em>'.format(tp))
        if len(resolved) <= 1:
            return ''.join(resolved)
        else:
            return ', '.join(resolved[:-1]) + ' or ' + resolved[-1]

    def _render_user_links(self, root: str, out: Callable[[str], None]) -> None:
        sections = sorted(s for s in self.config.sections() if s.startswith('link'))
        for section in sections:
            img = self.config.get(section, 'icon', fallback=None)
            cls = ''
            if img:
                if img in ('download', 'github', 'gitlab', 'bitbucket'):
                    img = '{root}img/i-' + img + '.svg?' + self._assets_version
                img = '<img src="{}" alt=""/>'.format(img.replace('{root}', root))
                cls = ' iconleft'
            out('<div class="button{}"><a href="{}" title="{}">{}<span>{}</span></a></div>'.format(
                cls,
                self.config.get(section, 'url', fallback='').replace('{root}', root),
                self.config.get(section, 'tooltip', fallback=''),
                img or '',
                self.config.get(section, 'text'),
            ))

    @contextmanager
    def _render_html(self, topref: TopRef, lines: List[str]) -> Generator[
            Callable[[str], None],
            None,
            None
        ]:
        """
        A context manager that renders the page frame for the given topref, and
        yields a function that appends a line to the page within the inner
        content area.
        """
        self.ctx.update(ref=topref)
        if not topref.collections and isinstance(topref, ManualRef):
            log.critical('manual "%s" has no sections (empty doc or possible symbol collision)', topref.name)
            sys.exit(1)

        fallback_title = self.config.get('project', 'name', fallback='Lua Project')
        project_title = self.config.get('project', 'title', fallback=fallback_title)
        if isinstance(topref, ManualRef):
            # For manual pages, the page title is the first section heading
            page_title = topref.collections[0].heading
        else:
            # For everything else, we use the ref's display name
            page_title = topref.display

        html_title = '{} - {}'.format(page_title, project_title)
        # Alias to improve readability
        out = lines.append
        root = self._get_root_path()
        head: list[str] = []

        css = self.config.get('project', 'css', fallback=None)
        if css:
            # The stylesheet is always copied to doc root, so take only the filename
            _, css = os.path.split(css)
            head.append('<link href="{}{}?{}" rel="stylesheet" />'.format(root, css, self._assets_version))

        favicon = self.config.get('project', 'favicon', fallback=None)
        if favicon:
            mimetype, _ = mimetypes.guess_type(favicon)
            mimetype = ' type="{}"'.format(mimetype) if mimetype else ''
            # Favicon is always copied to doc root, so take only the filename
            _, favicon = os.path.split(favicon)
            head.append('<link rel="shortcut icon" {} href="{}{}?{}"/>'.format(mimetype, root, favicon, self._assets_version))

        out(self._templates['head'].format(
            version=self._assets_version,
            title=html_title,
            head='\n'.join(head),
            root=root,
            bodyclass='{}-{}'.format(
                # First segment of body class is the ref type, but for unknown refs (such
                # as Search page) fall back to 'other'
                topref.type or 'other',
                # Second segment is the stripped form of the ref name.
                recache(r'\W+').sub('', topref.name).lower()
            )
        ))

        toprefs = self.parser.topsyms.values()
        manual = [ref for ref in toprefs if isinstance(ref, ManualRef)]
        classes = sorted([ref for ref in toprefs if isinstance(ref, ClassRef)], key=lambda ref: ref.name)
        modules = [ref for ref in toprefs if isinstance(ref, ModuleRef)]
        # Determine prev/next buttons relative to current topref.
        found = prevref = nextref = None
        for ref in manual + classes + modules:
            if found:
                nextref = ref
                break
            elif ref.topsym == topref.name or topref.symbol == '--search':
                found = True
            else:
                prevref = ref

        hometext = self.config.get('project', 'name', fallback=project_title)
        out('<div class="topbar">')
        out('<div class="group one">')
        if self.config.has_section('manual') and self.config.get('manual', 'index', fallback=False):
            path = '' if (isinstance(topref, ManualRef) and topref.name == 'index') else '../'
            out('<div class="button description"><a href="{}index.html"><span>{}</span></a></div>'.format(path, hometext))
        else:
            out('<div class="description"><span>{}</span></div>'.format(hometext))
        out('</div>')
        out('<div class="group two">')
        self._render_user_links(root, out)
        out('</div>')
        out('<div class="group three">')
        if prevref:
            out('<div class="button iconleft"><a href="{}" title="{}"><img src="{}img/i-left.svg?{}" alt=""/><span>Previous</span></a></div>'.format(
                self._get_ref_href(prevref),
                prevref.name,
                root,
                self._assets_version
            ))
        if nextref:
            out('<div class="button iconright"><a href="{}" title="{}"><span>Next</span><img src="{}img/i-right.svg?{}" alt=""/></a></div>'.format(
                self._get_ref_href(nextref),
                nextref.name,
                root,
                self._assets_version
            ))
        out('</div>')
        out('</div>')

        # Determine section headings to construct sidebar.
        out('<div class="sidebar">')
        out('<form action="{}search.html">'.format(root))
        # out('<form onsubmit="return window.search()">'.format(root))
        out('<input class="search" name="q" type="search" placeholder="Search" />')
        out('</form>')

        if topref.collections:
            out('<div class="sections">')
            out('<div class="heading">Contents</div>')
            out('<ul>')
            for colref in topref.collections:
                if isinstance(colref, ManualRef):
                    continue
                if isinstance(colref, (ClassRef, ModuleRef)):
                    heading = '{} <code>{}</code>'.format(colref.type.title(), colref.heading)
                else:
                    heading = colref.heading
                out('<li><a href="#{}">{}</a></li>'.format(colref.symbol, heading))
            out('</ul>')
            out('</div>')

        if self.parser.parsed[ManualRef]:
            out('<div class="manual">')
            out('<div class="heading">Manual</div>')
            out('<ul>')
            for ref in self.parser.parsed[ManualRef]:
                if ref.scope:
                    # This is a section heading, or it's the index document, so don't include
                    # it in the list of manual pages.
                    continue
                cls = ' class="selected"' if ref.name == topref.name else ''
                out('<li{}><a href="{}">{}</a></li>'.format(cls, self._get_ref_href(ref), ref.heading))
            out('</ul>')
            out('</div>')

        if classes:
            out('<div class="classes">')
            out('<div class="heading">Classes</div>')
            out('<ul>')
            for ref in classes:
                cls = ' class="selected"' if ref.name == topref.name else ''
                out('<li{}><a href="{}">{}</a></li>'.format(cls, self._get_ref_href(ref), ref.display))
            out('</ul>')
            out('</div>')

        if modules:
            out('<div class="modules">')
            out('<div class="heading">Modules</div>')
            out('<ul>')
            for ref in modules:
                if ref.userdata.get('empty') and ref.implicit:
                    # Skip empty implicit module
                    continue
                cls = ' class="selected"' if ref.name == topref.name else ''
                out('<li{}><a href="{}">{}</a></li>'.format(cls, self._get_ref_href(ref), ref.name))
            out('</ul>')
            out('</div>')

        # End sidebar
        out('</div>')
        out('<div class="body">')
        try:
            yield out
        finally:
            out('</div>')
            out(self._templates['foot'].format(root=root, version=self._assets_version))

    def _render_topref(self, topref: TopRef) -> str:
        """
        Renders a topref to HTML, returning a string containing the rendered HTML.
        """
        lines = []
        with self._render_html(topref, lines) as out:
            if isinstance(topref, (ClassRef, ModuleRef)):
                self._render_classmod(topref, out)
            elif isinstance(topref, ManualRef):
                self._render_manual(topref, out)
        return '\n'.join(lines)

    def _render_manual(self, topref: ManualRef, out: Callable[[str], None]) -> None:
        """
        Renders the given manual top-level Reference as HTML, calling the given out() function
        for each line of HTML.
        """
        out('<div class="manual">')
        if topref.content:
            # Preamble
            out(self._content_to_html(topref.content))
        for secref in topref.collections:
            # Manual pages only contain SectionRefs
            assert(isinstance(secref, SectionRef))
            out('<h{} id="{}">{}'.format(secref.level, secref.symbol, secref.heading))
            out(self._permalink(secref.symbol))
            out('</h{}>'.format(secref.level))
            out(self._content_to_html(secref.content))
        out('</div>')

    def _render_classmod(self, topref: Union[ClassRef, ModuleRef], out: Callable[[str], None]) -> None:
        """
        Renders the given class or module top-level Reference as HTML, calling the given out()
        function for each line of HTML.
        """
        assert(isinstance(topref, (ClassRef, ModuleRef)))
        for colref in topref.collections:
            self.ctx.update(ref=colref)

            # First collection within a class or module is the class/module itself.
            if isinstance(colref, TopRef):
                heading = '{} <code>{}</code>'.format(colref.type.title(), colref.heading)
            else:
                heading = self._markdown_to_html(colref.heading)

            out('<div class="section">')
            out('<h2 class="{}" id="{}">{}'.format(
                colref.type,
                colref.symbol,
                # Heading converted from markdown contains paragraph tags, and it
                # isn't valid HTML for headings to contain block elements.
                heading.replace('<p>', '').replace('</p>', '')
            ))
            out(self._permalink(colref.symbol))
            out('</h2>')
            out('<div class="inner">')
            if isinstance(colref, ClassRef):
                h = colref.hierarchy
                if len(h) > 1:
                    out('<div class="hierarchy">')
                    out('<div class="heading">Class Hierarchy</div>')
                    out('<ul>')
                    for n, cls in enumerate(h):
                        if cls == colref:
                            html = cls.name
                            self_class = ' self'
                        else:
                            html = self._types_to_html([cls.name])
                            self_class = ''
                        prefix = (('&nbsp;'*(n-1)*6) + '&nbsp;└─ ') if n > 0 else ''
                        out('<li class="class{}">{}<span>{}</span></li>'.format(self_class, prefix, html))
                    out('</ul>')
                    out('</div>')

            if colref.content:
                out(self._content_to_html(colref.content))

            fields_title = 'Fields'
            fields_meta_columns = 0
            fields_has_type_column = False
            for ref in colref.fields:
                n = 0
                if isinstance(ref.scope, ClassRef):
                    fields_title = 'Attributes'
                if ref.meta:
                    n += 1
                if ref.types:
                    fields_has_type_column = True
                fields_meta_columns = max(n, fields_meta_columns)

            functions_title = 'Functions'
            functions_meta_columns = 0
            for ref in colref.functions:
                n = 0
                if isinstance(ref.scope, ClassRef) and ':' in ref.symbol:
                    functions_title = 'Methods'
                if ref.flags.get('meta'):
                    n += 1
                functions_meta_columns = max(n, functions_meta_columns)

            #
            # Output synopsis for this section.
            #
            fields_compact = 'fields' in colref.compact
            functions_compact = 'functions' in colref.compact
            if colref.functions or colref.fields:
                out('<div class="synopsis">')
                if not fields_compact:
                    out('<h3>Synopsis</h3>')
                if colref.fields:
                    if colref.functions or not fields_compact:
                        out('<div class="heading">{}</div>'.format(fields_title))
                    out('<table class="fields {}">'.format('compact' if fields_compact else ''))
                    for ref in colref.fields:
                        out('<tr>')
                        if not fields_compact:
                            out('<td class="name"><a href="#{}"><var>{}</var></a></td>'.format(ref.name, ref.title))
                        else:
                            link = self._permalink(ref.name)
                            out('<td class="name"><var id="{}">{}</var>{}</td>'.format(ref.name, ref.title, link))
                        nmeta = fields_meta_columns
                        if ref.types:
                            types = self._types_to_html(ref.types)
                            out('<td class="meta types">{}</td>'.format(types))
                        elif fields_has_type_column:
                            out('<td class="meta"></td>')
                        if ref.meta:
                            html = self._markdown_to_html(ref.meta)
                            out('<td class="meta">{}</td>'.format(html))
                            nmeta -= 1
                        while nmeta > 0:
                            out('<td class="meta"></td>')
                            nmeta -= 1

                        if not fields_compact:
                            html = self._markdown_to_html(ref.content.get_first_sentence())
                        else:
                            html = self._content_to_html(ref.content)
                        if html:
                            out('<td class="doc">{}</td>'.format(html))
                        out('</tr>')
                    out('</table>')

                if colref.functions:
                    if colref.fields or not functions_compact:
                        out('<div class="heading">{}</div>'.format(functions_title))
                    out('<table class="functions {}">'.format('compact' if functions_compact else ''))
                    for ref in colref.functions:
                        out('<tr>')
                        # For compact view, remove topsym prefix from symbol
                        display = ref.display_compact if isinstance(ref.scope, ClassRef) else ref.title
                        if not functions_compact:
                            out('<td class="name"><a href="#{}"><var>{}</var></a>()</td>'.format(ref.name, display))
                        else:
                            link = self._permalink(ref.name)
                            params = ', '.join('<em>{}</em>'.format(param) for param, _, _ in ref.params)
                            html = '<td class="name"><var id="{}">{}</var>({}){}</td>'
                            out(html.format(ref.name, display, params, link))
                        meta = functions_meta_columns
                        if ref.meta:
                            out('<td class="meta">{}</td>'.format(ref.meta))
                            meta -= 1
                        while meta > 0:
                            out('<td class="meta"></td>')
                            meta -= 1
                    
                        if not functions_compact:
                            html = self._markdown_to_html(ref.content.get_first_sentence())
                        else:
                            html = self._content_to_html(ref.content)
                        out('<td class="doc">{}</td>'.format(html))
                        out('</tr>')
                    out('</table>')
                out('</div>')

            #
            # Output fields for this section
            #
            if colref.fields and not fields_compact:
                if colref.functions:
                    out('<h3 class="fields">{}</h3>'.format(fields_title))
                out('<dl class="fields">')
                for ref in colref.fields:
                    out('<dt id="{}">'.format(ref.name))
                    out('<span class="icon"></span><var>{}</var>'.format(ref.display))
                    if ref.types:
                        types = self._types_to_html(ref.types)
                        out('<span class="tag type">{}</span>'.format(types))
                    if ref.meta:
                        out('<span class="tag meta">{}</span>'.format(ref.meta))
                    out(self._permalink(ref.name))
                    out('</dt>')
                    out('<dd>')
                    out(self._content_to_html(ref.content))
                    out('</dd>')
                out('</dl>')

            #
            # Output functions for this section
            #
            if colref.functions and not functions_compact:
                if colref.fields:
                    out('<h3 class="functions">{}</h3>'.format(functions_title))
                out('<dl class="functions">')
                for ref in colref.functions:
                    params = ', '.join('<em>{}</em>'.format(param) for param, _, _ in ref.params)
                    out('<dt id="{}">'.format(ref.name))
                    out('<span class="icon"></span><var>{}</var>({})'.format(ref.display, params))
                    if ref.meta:
                        out('<span class="tag meta">{}</span>'.format(ref.meta))
                    out(self._permalink(ref.name))
                    out('</dt>')
                    out('<dd>')
                    out(self._content_to_html(ref.content))
                    # Only show the praameters table if there's at least one documented parameter.
                    if any(types or doc for _, types, doc in ref.params):
                        out('<div class="heading">Parameters</div>')
                        out('<table class="parameters">')
                        for (param, types, doc) in ref.params:
                            out('<tr>')
                            out('<td class="name"><var>{}</var></td>'.format(param))
                            out('<td class="types">({})</td>'.format(self._types_to_html(types)))
                            out('<td class="doc">{}</td>'.format(self._content_to_html(doc)))
                            out('</tr>')
                        out('</table>')
                    if ref.returns:
                        out('<div class="heading">Return Values</div>')
                        out('<table class="returns">')
                        for n, (types, doc) in enumerate(ref.returns, 1):
                            out('<tr>')
                            if len(ref.returns) > 1:
                                out('<td class="name">{}.</td>'.format(n))
                            out('<td class="types">({})</td>'.format(self._types_to_html(types)))
                            out('<td class="doc">{}</td>'.format(self._content_to_html(doc)))
                            out('</tr>')
                        out('</table>')
                    out('</dd>')
                out('</dl>')
            # Close inner section
            out('</div>')
            # Close outer section
            out('</div>')

    def render_search_index(self) -> str:
        log.info('generating search index')
        topref = self.parser.refs['--search']
        self.ctx.update(ref=topref)
        lines = []
        out = lines.append
        def add(ref: RefT, typ: Type[RefT]):
            href = self._get_ref_href(ref)
            text = self._content_to_text(ref.content)
            title = ref.display
            if typ == SectionRef and not isinstance(ref.topref, ManualRef):
                # Non-manual sections typically use the first sentence as the section
                # title.  This heuristic uses the first sentence only if it's less than 80
                # characters, otherwise falls back to the section title.
                first, remaining = get_first_sentence(text)
                if len(first) < 80:
                    title = first
                    text = remaining
            text = text.replace('"', '\\"').replace('\n', ' ')
            title = title.replace('"', '\\"').replace('\n', ' ')
            if typ == ModuleRef:
                title = title.split('.', 1)[-1]
            out('{{path:"{}", type:"{}", title:"{}", text:"{}"}},'.format(href, typ.type, title, text))

        out('var docs = [')
        for typ in ClassRef, ModuleRef, FieldRef, FunctionRef, SectionRef:
            for ref in self.parser.parsed[typ]:
                add(ref, typ)
        out('];')
        return '\n'.join(lines)

    def render_search_page(self) -> str:
        root = self._get_root_path()
        topref = self.parser.refs['--search']
        assert(isinstance(topref, TopRef))
        lines = []
        with self._render_html(topref, lines) as out:
            out(self._templates['search'].format(root=root, version=self._assets_version))
        return '\n'.join(lines)

    def render_landing_page(self) -> str:
        """
        Returns rendered HTML for a landing page (index.html) which is used when there is
        no explicit manual page called "index" and which just returns a skeleton page with
        no body.
        """
        # A bit lazy to reuse the search topref here, but we just need a reference
        # from the same directory so the link paths are correct.
        topref = self.parser.refs['--search']
        assert(isinstance(topref, TopRef))
        lines = []
        with self._render_html(topref, lines):
            pass
        return '\n'.join(lines)


    def render(self, toprefs: List[TopRef], outdir: Optional[str]) -> None:
        """
        Renders toprefs as HTML to the given output directory.
        """
        if not outdir:
            log.warn('"out" is not defined in config file, assuming ./out/')
            outdir = 'out'
        os.makedirs(outdir, exist_ok=True)
        self.copy_file_from_config('project', 'css', outdir)
        self.copy_file_from_config('project', 'favicon', outdir)

        for ref in toprefs:
            if ref.userdata.get('empty') and ref.implicit:
                # Reference has no content and it was also implicitly generated, so we don't render it.
                log.info('not rendering empty %s %s', ref.type, ref.name)
                continue
            if isinstance(ref, ManualRef) and ref.name == 'index':
                typedir = outdir
            else:
                typedir = os.path.join(outdir, ref.type)
            os.makedirs(typedir, exist_ok=True)
            outfile = os.path.join(typedir, ref.name + '.html')
            log.info('rendering %s %s -> %s', ref.type, ref.name, outfile)
            html = self._render_topref(ref)
            with open(outfile, 'w', encoding='utf8') as f:
                f.write(html)

        js = self.render_search_index()
        with open(os.path.join(outdir, 'index.js'), 'w', encoding='utf8') as f:
            f.write(js)

        html = self.render_search_page()
        with open(os.path.join(outdir, 'search.html'), 'w', encoding='utf8') as f:
            f.write(html)

        if not self.parser.get_reference(ManualRef, 'index'):
            # The user hasn't specified an index manual page, so we generate a blank
            # landing page that at least presents the sidebar with available links.
            html = self.render_landing_page()
            with open(os.path.join(outdir, 'index.html'), 'w', encoding='utf8') as f:
                f.write(html)

        for name in ASSETS:
            outfile = os.path.join(outdir, name)
            if os.path.dirname(name):
                os.makedirs(os.path.dirname(outfile), exist_ok=True)
            with open(outfile, 'wb') as f:
                f.write(assets.get(name))
