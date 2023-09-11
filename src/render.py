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

__all__ = ['Renderer']

import sys
import os
import re
import mimetypes
from contextlib import contextmanager
from typing import Union, Match, Tuple, List, Callable, Generator, Type

import commonmark.blocks
import commonmark_extensions.tables

from .assets import assets
from .log import log
from .reference import *
from .parse import *
from .utils import *

# Effectively disable implicit code blocks
commonmark.blocks.CODE_INDENT = 1000


class CustomRendererWithTables(commonmark_extensions.tables.RendererWithTables):
    def make_table_node(self, node):
        return '<table class="user">'

# https://github.com/GovReady/CommonMark-py-Extensions/issues/3#issuecomment-756499491
# Thanks to hughdavenport
class TableWaitingForBug3(commonmark_extensions.tables.Table):
    @staticmethod
    def continue_(parser, container=None):
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


class Renderer:
    """
    Takes a Parser object and provides an interface to generate rendered HTML.
    """
    # Common abbreviations with periods that are considered when determining what is the
    # first sentence of a markdown block
    RE_ABBREV = re.compile(r'(e\.?g\.|i\.?e\.|etc\.|et al\.|vs\.)', flags=re.I|re.S)
    # Regexp patterns that progressively narrow down a markdown block to its first
    # sentence
    RE_FIRST_SENTENCE = (
        # First pass: Move everything after a paragraph break (two newlines) to
        # the remaining block
        re.compile(r'^(.*\n\s*\n)(.*)$', flags=re.S),
        # Second pass: Move (prepend) anything including and below a markdown heading
        # to the remaining block.  Fixes #6.
        re.compile(r'(.*)(?:^|\n)(#.*)', flags=re.S),
        # Final pass: take everything up to the first period as the first sentence.
        re.compile(r'^(.+?[.?!])(?: |$|\n)(.*)', flags=re.S),
    )

    def __init__(self, parser: Parser):
        self.parser = parser
        self.config = parser.config
        self.ctx = parser.ctx

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

        return prefix + (ref.userdata.get('within_topsym') or ref.topsym) + '.html', fragment

    def _get_ref_href(self, ref: Reference) -> str:
        """
        Returns the href src for the given Reference object, which is directly used
        in <a> tags in the rendered content.
        """
        file, fragment = self._get_ref_link_info(ref)
        return file + fragment

    def _render_ref_markdown(self, ref: Reference, text: str, code=False) -> str:
        """
        Returns the Reference as a markdown link.

        If code is True, then the given text is wrapped in backticks.
        """
        backtick = '`' if code else ''
        return '[{tick}{text}{parens}{tick}]({href})'.format(
            tick=backtick,
            text=text or ref.name,
            parens='()' if isinstance(ref, FunctionRef) and not text else '',
            href=self._get_ref_href(ref)
        )

    def _render_ref_markdown_re(self, m: Match[str]) -> str:
        """
        Regexp callback to handle the @{refname} case.
        """
        code: bool = (m.group(1) == '`')
        ref = self.parser._resolve_ref(m.group(2))
        if ref:
            return self._render_ref_markdown(ref, m.group(3), code=code)
        else:
            log.warning('%s:~%s: reference "%s" could not be resolved', self.ctx.file, self.ctx.line, m.group(2))
            return m.group(3) or m.group(2)

    def _render_backtick_ref_markdown_re(self, m: Match[str]) -> str:
        """
        Regexp callback to handle the `refname` case.
        """
        ref = self.parser._resolve_ref(m.group(1))
        if ref:
            return self._render_ref_markdown(ref, text=m.group(1), code=True)
        else:
            # Couldn't resolve the ref, just return back the original text.
            return '`{}`'.format(m.group(1))

    def _refs_to_markdown(self, block: str) -> str:
        """
        Replaces `refname` and @{refname} in the given block of text with
        markdown links.
        """
        # Resolve `ref`
        block = re.sub(r'(?<!`)`([^` ]+)`', self._render_backtick_ref_markdown_re, block, 0, re.S)
        # Resolve @{ref} and @{ref|text}.  Do this *after* `ref` in case the ref is in the
        # form `@{stuff}`.
        block = re.sub(r'(`)?@{([^}|]+)(?:\|([^}]*))?}(`)?', self._render_ref_markdown_re, block, 0, re.S)
        return block

    def _markdown_to_html(self, md: str) -> str:
        """
        Renders the given markdown as HTML and returns the result.
        """
        md = self._refs_to_markdown(md)
        parser = commonmark_extensions.tables.ParserWithTables()
        ast = parser.parse(md)
        html = CustomRendererWithTables().render(ast)

        def replace_admonition(m: Match[str]):
            type, title, content = m.group(1).split('\x02')
            # content = content.replace('<p></p>', '')
            return '<div class="admonition {}"><div class="title">{}</div><div class="body"><p>{}</p>\n</div></div>'.format(type, title, content.rstrip())

        html = re.sub(r'<p>\x01adm([^\x03]+)\x03\s*</p>', replace_admonition, html, flags=re.S)
        html = re.sub(r'<p>\x01see([^\x03]+)\x03\s*</p>', '<div class="see">See also \\1</div>', html, flags=re.S)
        return html

    def _markdown_to_text(self, md: str) -> str:
        """
        Strips markdown codes from the given markdown and returns the result.
        """
        # Code blocks
        text = re.sub(r'```.*?```', '', md, flags=re.S)
        # Inline preformatted code
        text = re.sub(r'`([^`]+)`', '\\1', text)
        # Headings
        text = re.sub(r'#+', '', text)
        # Bold
        text = re.sub(r'\*([^*]+)\*', '\\1', text)
        # Link or inline image
        text = re.sub(r'!?\[([^]]*)\]\([^)]+\)', '\\1', text)

        # Clean up non-markdown things.
        # Reference with custom display
        text = re.sub(r'@{[^|]+\|([^}]+)\}', '\\1', text)
        # Just a reference
        text = re.sub(r'@{([^}]+)\}', '\\1', text)
        # Replace admonissions with text elements
        text = re.sub(
            r'\x01adm[^\x02]+\x02([^\x03]+)\x03',
            lambda m: m.group(1).replace('\x02', ' '),
            text,
            flags=re.S)
        # Remove other special encoded content
        text = re.sub(r'\x01([^\x03]*)\x03', '', text)
        # Consolidate multiple whitespaces
        text = re.sub(r'\s+', ' ', text)
        return text.strip()

    def _types_to_html(self, types: List[str]) -> str:
        """
        Resolves references in the given list of types, and returns HTML of
        all types in a human-readable string.
        """
        resolved: list[str] = []
        for tp in types:
            ref = self.parser._resolve_ref(tp)
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
                re.sub(r'\W+', '', topref.name).lower()
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
                out('<li{}><a href="{}">{}</a></li>'.format(cls, self._get_ref_href(ref), ref.display))
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

    def render(self, topref: TopRef) -> str:
        """
        Renders a prerendered Page to HTML, returning a string containing the rendered
        HTML.
        """
        lines = []
        with self._render_html(topref, lines) as out:
            if isinstance(topref, (ClassRef, ModuleRef)):
                self._render_classmod(topref, out)
            elif isinstance(topref, ManualRef):
                self._render_manual(topref, out)
        return '\n'.join(lines)

    def _permalink(self, id: str) -> str:
        """
        Returns the HTML for a permalink used for directly linkable references such
        as section headings, functions, fields, etc.
        """
        return '<a class="permalink" href="#{}" title="Permalink to this definition">¶</a>'.format(id)

    def _render_manual(self, topref: ManualRef, out: Callable[[str], None]) -> None:
        """
        Renders the given manual top-level Reference as HTML, calling the given out() function
        for each line of HTML.
        """
        out('<div class="manual">')
        if topref.md:
            # Preamble
            out(self._markdown_to_html(topref.md))
        for secref in topref.collections:
            # Manual pages only contain SectionRefs
            assert(isinstance(secref, SectionRef))
            out('<h{} id="{}">{}'.format(secref.level, secref.symbol, secref.heading))
            out(self._permalink(secref.symbol))
            out('</h{}>'.format(secref.level))
            out(self._markdown_to_html(secref.body))
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

            if colref.body:
                out(self._markdown_to_html(colref.body))

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

                        md = get_first_sentence(ref.md)[0] if not fields_compact else ref.md
                        if md:
                            out('<td class="doc">{}</td>'.format(self._markdown_to_html(md)))
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
                    
                        md = get_first_sentence(ref.md)[0] if not functions_compact else ref.md
                        out('<td class="doc">{}</td>'.format(self._markdown_to_html(md)))
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
                    out(self._markdown_to_html(ref.md))
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
                    out(self._markdown_to_html(ref.md))
                    # Only show the praameters table if there's at least one documented parameter.
                    if any(types or doc for _, types, doc in ref.params):
                        out('<div class="heading">Parameters</div>')
                        out('<table class="parameters">')
                        for (param, types, doc) in ref.params:
                            out('<tr>')
                            out('<td class="name"><var>{}</var></td>'.format(param))
                            out('<td class="types">({})</td>'.format(self._types_to_html(types)))
                            out('<td class="doc">{}</td>'.format(self._markdown_to_html(doc)))
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
                            out('<td class="doc">{}</td>'.format(self._markdown_to_html(doc)))
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
        def add(ref: Reference, typ: Type[Reference]):
            href = self._get_ref_href(ref)
            _, _, md = self.parser.content_to_markdown(ref.content)
            text = self._markdown_to_text(md)
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
