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

__all__ = ['Renderer']

import sys
import os
import re
import mimetypes
from contextlib import contextmanager

import commonmark.blocks
import commonmark_extensions.tables

from .assets import assets
from .log import log
from .reference import *
from .parse import *

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

    def __init__(self, parser):
        self.parser = parser
        self.config = parser.config
        self.ctx = parser.ctx

        # Create a pseudo Reference for the search page using the special 'search' type.
        # This is used to ensure the relative paths are correct. Use the name '--search'
        # as this won't conflict with any user-provided names.
        parser.refs['--search'] = Reference(parser, type='search', symbol='Search')

        self._templates = {
            'head': assets.get('head.tmpl.html').decode('utf8'),
            'foot': assets.get('foot.tmpl.html').decode('utf8'),
            'search': assets.get('search.tmpl.html').decode('utf8'),
        }
        self._assets_version = assets.hash()[:7]

    def _get_root_path(self):
        """
        Returns the path prefix for the document root, which is relative to the
        current context.
        """
        # The topref of the current context's reference.  The path will be relative to
        # this topref's file.
        viatopref = self.ctx.ref.topref
        if (viatopref.type == 'manual' and viatopref.name == 'index') or viatopref.type == 'search':
            return ''
        else:
            return '../'

    def get_indent_level(self, s):
        """
        Returns the number of spaces on left side of the string.
        """
        m = re.search(r'^( *)', s)
        return len(m.group(1)) if m else 0

    def _get_ref_link_info(self, ref):
        """
        Returns (html file name, URL fragment) of the given Reference object.
        """
        # The top-level Reference object that holds this reference, which respects @within.
        topsym = ref.userdata.get('within_topsym') or ref.topsym
        try:
            topref = self.parser.refs[topsym]
        except KeyError:
            raise KeyError('top-level reference "%s" not found (from "%s")' % (topsym, ref.name)) from None
        prefix = self._get_root_path()
        if ref.topref.type != 'manual' or ref.topref.name != 'index':
            prefix += '{}/'.format(topref.type)
        if ref.topref.type == 'manual':
            # Manuals don't use fully qualified fragments.
            fragment = '#' + ref.symbol if ref.scopes else ''
        else:
            fragment = '#{}'.format(ref.name) if ref.name != ref.topsym else ''

        return prefix + (ref.userdata.get('within_topsym') or ref.topsym) + '.html', fragment

    def _get_ref_href(self, ref):
        """
        Returns the href src for the given Reference object, which is directly used
        in <a> tags in the rendered content.
        """
        file, fragment = self._get_ref_link_info(ref)
        return file + fragment

    def _render_ref_markdown(self, ref, text, code=False):
        """
        Returns the Reference as a markdown link.

        If code is True, then the given text is wrapped in backticks.
        """
        backtick = '`' if code else ''
        return '[{tick}{text}{parens}{tick}]({href})'.format(
            tick=backtick,
            text=text or ref.name,
            parens='()' if ref.type == 'function' and not text else '',
            href=self._get_ref_href(ref)
        )

    def _render_ref_markdown_re(self, m):
        """
        Regexp callback to handle the @{refname} case.
        """
        code = (m.group(1) == '`')
        ref = self.parser._resolve_ref(m.group(2))
        if ref:
            return self._render_ref_markdown(ref, m.group(3), code=code)
        else:
            log.warning('%s:~%s: reference "%s" could not be resolved', self.ctx.file, self.ctx.line, m.group(2))
            return m.group(3) or m.group(2)

    def _render_backtick_ref_markdown_re(self, m):
        """
        Regexp callback to handle the `refname` case.
        """
        ref = self.parser._resolve_ref(m.group(1))
        if ref:
            return self._render_ref_markdown(ref, text=m.group(1), code=True)
        else:
            return '`{}`'.format(m.group(1))

    def _refs_to_markdown(self, block):
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

    def _content_to_markdown(self, content, strip_comments=True):
        """
        Converts a docstring block into markdown.

        Docstring blocks can appear in sections, or as content associated with a
        function definition or field.

        This function returns 3 values: a dict of name -> (type, docstring) for @tparam
        tags, a list of (type, docstrings) for @treturn tags, and a string holding the
        converted content to markdown.
        """
        if not content:
            return None, None, ''
        output = []
        params = {}
        returns = []

        # List of [tag, args, indent, lines]
        tagstack = []
        supported_tags = 'tparam', 'treturn', 'usage', 'example', 'code', 'see', 'warning', 'note'

        def end_tag():
            tag, args, indent, lines = tagstack.pop()
            target = tagstack[-1][3] if tagstack else output
            if tag in ('usage', 'example'):
                target.append('##### ' + tag.title())
            if tag in ('usage', 'example', 'code'):
                target.append('```lua')
                # Remove trailing newlines.
                while lines and not lines[-1].strip():
                    lines.pop()
                # Dedent all lines according to the indentation of the
                # first line.
                indent = self.get_indent_level(lines[0])
                target.extend([l[indent:] for l in lines])
                target.append('```')
            elif tag == 'tparam' and len(args) >= 2:
                types = args[0].split('|')
                name = args[1]
                params[name] = types, ' '.join(args[2:] + lines)
            elif tag == 'treturn':
                types = args[0].split('|')
                doc = ' '.join(args[1:] + lines)
                returns.append((types, doc))
            elif tag == 'see':
                refs = ['@{{{}}}'.format(see) for see in args]
                target.append('\x01\x03See also {}</div>'.format(', '.join(refs)))
            elif tag == 'warning' or tag == 'note':
                html = '\x01\x02{}\x01{}\x01{}\n</div></div>\n'
                heading = ' '.join(args) if args else tag.title()
                target.append(html.format(tag, heading, '\n'.join(lines)))

        def end_tags(all, line=None, indent=None):
            if not all:
                end_tag()
            else:
                while tagstack:
                    end_tag()
                    if line and tagstack:
                        last_tag_indent = tagstack[-1][2]
                        tagstack[-1][3].append(line)
                        line = None
            return line

        last_line = content[-1][0]
        for n, line in content:
            self.ctx.update(line=n)
            tag, args = self.parser._parse_tag(line, require_comment=strip_comments)
            if strip_comments:
                line = line.lstrip('-').rstrip()
            indent = self.get_indent_level(line)

            if tagstack:
                last_tag_indent = tagstack[-1][2]
                # Determine threshold at which we will consider the last tag to have
                # terminated.
                if tag:
                    # Any tag at the same level as the last tag (or below) will close
                    threshold = last_tag_indent
                else:
                    threshold = last_tag_indent
                if not tag and indent > threshold and line:
                    tagstack[-1][3].append(line)
                    line = None
                if n == last_line or (line and indent <= threshold):
                    line = end_tags(n == last_line, line if not tag else None, indent)

            if tag:
                tagstack.append([tag, args, indent, []])
                if tag not in supported_tags:
                    log.error('%s:%s: unknown tag @%s', self.ctx.file, n, tag)
                elif n == last_line:
                    end_tags(n == last_line)
            elif line is not None:
                if tagstack:
                    last = tagstack[-1]
                    last[3].append(line)
                else:
                    output.append(line)
        return params, returns, '\n'.join(output)

    def _get_first_sentence(self, md):
        """
        Returns a 2-tuple of the first sentence from the given markdown, and
        all remaining.
        """
        # This is rather cheeky, but just handles these common abbreviations so they don't
        # interpreted as end-of-sentence.
        escape = lambda m: m.group(1).replace('.', '\x00')
        unescape = lambda s: s.replace('\x00', '.')
        first = self.RE_ABBREV.sub(escape, md)
        remaining = ''
        for pat in self.RE_FIRST_SENTENCE:
            m = pat.search(first)
            if m:
                first, pre = m.groups()
                remaining = pre + remaining
        # Remove period but preserve other sentence-ending punctuation from first
        # sentence
        return unescape(first).strip().rstrip('.'), unescape(remaining).strip()

    def _markdown_to_html(self, md):
        """
        Renders the given markdown as HTML and returns the result.
        """
        md = self._refs_to_markdown(md)
        parser = commonmark_extensions.tables.ParserWithTables()
        ast = parser.parse(md)
        html = CustomRendererWithTables().render(ast)
        def replace_admonition(m):
            type, title = m.groups()
            return '<div class="admonition {}"><div class="title">{}</div><div class="body"><p>'.format(type, title)
        html = re.sub(r'\x01\x02([^\x01]+)\x01([^\x01]+)\x01', replace_admonition, html)
        html = html.replace('\x01\x03', '<div class="see">')
        # As a result of our disgusting abuse of commonmark, we end up with divs inside
        # paragraphs, which is invalid. Take care of this now.
        html = html.replace('<p><div', '<div').replace('</div></p>', '</div>')
        return html

    def _markdown_to_text(self, md):
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
        # Custom things like admonitions
        text = re.sub(r'\x01(\x02|\x03).*(</div>|\x01)', '', text)
        text = text.replace('</div>', '')
        # Consolidate multiple whitespaces
        text = re.sub(r'\s+', ' ', text)
        return text.strip()

    def _types_to_html(self, types):
        """
        Resolves references in the given list of types, and returns HTML of
        all types in a human-readable string.
        """
        resolved = []
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

    def preprocess(self, topref):
        """
        Preprocesses the given topref, rendering its content to HTML and storing
        the result in an HTML attribute, which is a list holding the individual
        rendered lines.
        """
        topref.html = []
        if topref.type in ('class', 'module'):
            topref.userdata['empty'] = not self._render_classmod(topref, topref.html.append)
        elif topref.type == 'manual':
            self._render_manual(topref, topref.html.append)

    def _render_user_links(self, topref, root, out):
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
    def _render_html(self, topref, lines):
        """
        A context manager that renders the page frame for the given topref, and
        yields a function that appends a line to the page within the inner
        content area.
        """
        self.ctx.update(ref=topref)
        sections = self.parser._get_sections(topref)
        if not sections and topref.type == 'manual':
            log.critical('manual "%s" has no sections (empty doc or possible symbol collision)', topref.name)
            sys.exit(1)
        title = self.config.get('project', 'title', fallback=self.config.get('project', 'name', fallback='Lua Project'))
        html_title = '{} - {}'.format(
            sections[0].display if topref.type == 'manual' else topref.name,
            title
        )
        # Alias to improve readability
        out = lines.append
        root = self._get_root_path()
        head = []
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
            bodyclass='{}-{}'.format(topref.type, re.sub(r'\W+', '', topref.name).lower())
        ))

        toprefs = self.parser.topsyms.values()
        manual = [ref for ref in toprefs if ref.type == 'manual']
        classes = sorted([ref for ref in toprefs if ref.type == 'class'], key=lambda ref: ref.name)
        modules = [ref for ref in toprefs if ref.type == 'module']
        # Determine prev/next buttons relative to current topref.
        found = prevref = nextref = None
        for ref in manual + classes + modules:
            if found:
                nextref = ref
                break
            elif ref.topsym == topref.name or topref.type == 'search':
                found = True
            else:
                prevref = ref

        hometext = self.config.get('project', 'name', fallback=title)
        out('<div class="topbar">')
        out('<div class="group one">')
        if self.config.has_section('manual') and self.config.get('manual', 'index', fallback=None):
            path = '' if (topref.type == 'manual' and topref.name == 'index') else '../'
            out('<div class="button description"><a href="{}index.html"><span>{}</span></a></div>'.format(path, hometext))
        else:
            out('<div class="description"><span>{}</span></div>'.format(hometext))
        out('</div>')
        out('<div class="group two">')
        self._render_user_links(topref, root, out)
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

        if sections:
            out('<div class="sections">')
            out('<div class="heading">Contents</div>')
            out('<ul>')
            for section in sections:
                self.ctx.update(ref=section)
                _, _, md = self._content_to_markdown(section.content)
                if section.type in ('class', 'module'):
                    section.heading = '{} <code>{}</code>'.format(section.type.title(), section.symbol)
                    section.body = md
                elif section.topref.type == 'manual':
                    section.heading = section.display
                    section.body = md
                else:
                    heading, section.body = self._get_first_sentence(md)
                    section.heading = self._markdown_to_html(heading)
                out('<li><a href="#{}">{}</a></li>'.format(section.symbol, section.heading))
            out('</ul>')
            out('</div>')

        if self.parser.parsed['manual']:
            out('<div class="manual">')
            out('<div class="heading">Manual</div>')
            out('<ul>')
            for ref in self.parser.parsed['manual']:
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

    def render(self, topref):
        """
        Renders a preprocessed topref to HTML, returning a string containing the
        rendered HTML.

        preprocess() must have been called on the topref first.
        """
        lines = []
        with self._render_html(topref, lines) as out:
            out('\n'.join(topref.html))
        return '\n'.join(lines)

    def _permalink(self, id):
        """
        Returns the HTML for a permalink used for directly linkable references such
        as section headings, functions, fields, etc.
        """
        return '<a class="permalink" href="#{}" title="Permalink to this definition">¶</a>'.format(id)

    def _render_manual(self, manualref, out):
        """
        Renders the given manual top-level Reference as HTML, calling the given out() function
        for each line of HTML.
        """
        out('<div class="manual">')
        if manualref.content:
            # Include any preamble before the first heading.
            _, _, md = self._content_to_markdown(manualref.content, strip_comments=False)
            out(self._markdown_to_html(md))
        for section in self.parser._get_sections(manualref):
            self.ctx.update(ref=section)
            level = section.flags['level']
            out('<h{} id="{}">{}'.format(level, section.symbol, section.display))
            out(self._permalink(section.symbol))
            out('</h{}>'.format(level))
            _, _, md = self._content_to_markdown(section.content, strip_comments=False)
            out(self._markdown_to_html(md))
        out('</div>')

    def _render_classmod(self, topref, out):
        """
        Renders the given class or module top-level Reference as HTML, calling the given out()
        function for each line of HTML.
        """
        has_content = False
        for section in self.parser._get_sections(topref):
            self.ctx.update(ref=section)

            # Parse out section heading and body.
            _, _, md = self._content_to_markdown(section.content)
            if section.type in ('class', 'module'):
                section.heading = '{} <code>{}</code>'.format(section.type.title(), section.symbol)
                section.body = md
            elif section.topref.type == 'manual':
                section.heading = section.display
                section.body = md
            else:
                heading, section.body = self._get_first_sentence(md)
                # Fall back to section name if there is no content for the heading.
                section.heading = self._markdown_to_html(heading) if heading.strip() else section.name

            out('<div class="section">')
            out('<h2 class="{}" id="{}">{}'.format(
                section.type,
                section.symbol,
                # Heading converted from markdown contains paragraph tags, and it
                # isn't valid HTML for headings to contain block elements.
                section.heading.replace('<p>', '').replace('</p>', '')
            ))
            out(self._permalink(section.symbol))
            out('</h2>')
            out('<div class="inner">')
            h = section.hierarchy
            if len(h) > 1:
                out('<div class="hierarchy">')
                out('<div class="heading">Class Hierarchy</div>')
                out('<ul>')
                for n, cls in enumerate(h):
                    if cls == section:
                        html = cls.name
                        self_class = ' self'
                    else:
                        html = self._types_to_html([cls.name])
                        self_class = ''
                    prefix = (('&nbsp;'*(n-1)*6) + '&nbsp;└─ ') if n > 0 else ''
                    out('<li class="class{}">{}<span>{}</span></li>'.format(self_class, prefix, html))
                out('</ul>')
                out('</div>')

            # section.heading and section.body is set by _render_html()
            if section.body:
                out(self._markdown_to_html(section.body))

            functions = list(self.parser._get_elements_in_section('function', section.section, section.topsym))
            fields = list(self.parser._get_elements_in_section('field', section.section, section.topsym))
            has_content = has_content or section.body or functions or fields
            # functions.sort(key=lambda ref: ref.name)
            # fields.sort(key=lambda ref: ref.name)

            fields_title = 'Fields'
            fields_meta_columns = 0
            fields_has_type_column = False
            for ref in fields:
                n = 0
                if ref.scope.type == 'class':
                    fields_title = 'Attributes'
                if ref.flags.get('meta'):
                    n += 1
                if ref.flags.get('type'):
                    fields_has_type_column = True
                fields_meta_columns = max(n, fields_meta_columns)

            functions_title = 'Functions'
            functions_meta_columns = 0
            for ref in functions:
                n = 0
                if ref.scope.type == 'class' and ':' in ref.symbol:
                    functions_title = 'Methods'
                if ref.flags.get('meta'):
                    n += 1
                functions_meta_columns = max(n, functions_meta_columns)

            #
            # Output synopsis for this section.
            #
            compact = section.flags.get('compact', [])
            fullnames = section.flags.get('fullnames')
            fields_compact = 'fields' in compact
            functions_compact = 'functions' in compact
            if functions or fields:
                out('<div class="synopsis">')
                if not fields_compact:
                    out('<h3>Synopsis</h3>')
                if fields:
                    if functions or not fields_compact:
                        out('<div class="heading">{}</div>'.format(fields_title))
                    out('<table class="fields {}">'.format('compact' if fields_compact else ''))
                    for ref in fields:
                        out('<tr>')
                        display = ref.name if fullnames else ref.symbol
                        if not fields_compact:
                            out('<td class="name"><a href="#{}"><var>{}</var></a></td>'.format(ref.name, display))
                        else:
                            link = self._permalink(ref.name)
                            out('<td class="name"><var id="{}">{}</var>{}</td>'.format(ref.name, display, link))
                        nmeta = fields_meta_columns
                        if ref.flags.get('type'):
                            types = self._types_to_html(ref.flags['type'])
                            out('<td class="meta types">{}</td>'.format(types))
                        elif fields_has_type_column:
                            out('<td class="meta"></td>')
                        if ref.flags.get('meta'):
                            html = self._markdown_to_html(ref.flags['meta'])
                            out('<td class="meta">{}</td>'.format(html))
                            nmeta -= 1
                        while nmeta > 0:
                            out('<td class="meta"></td>')
                            nmeta -= 1
                        _, _, ref.md = self._content_to_markdown(ref.content)
                        md = self._get_first_sentence(ref.md)[0] if not fields_compact else ref.md
                        if md:
                            out('<td class="doc">{}</td>'.format(self._markdown_to_html(md)))
                        out('</tr>')
                    out('</table>')

                if functions:
                    if fields or not functions_compact:
                        out('<div class="heading">{}</div>'.format(functions_title))
                    out('<table class="functions {}">'.format('compact' if functions_compact else ''))
                    for ref in functions:
                        out('<tr>')
                        # For compact view, remove topsym prefix from symbol
                        display = ref.display_compact if ref.scope.type == 'class' else ref.display
                        if not functions_compact:
                            out('<td class="name"><a href="#{}"><var>{}</var></a>()</td>'.format(ref.name, display))
                        else:
                            link = self._permalink(ref.name)
                            args = ', '.join('<em>{}</em>'.format(arg) for arg in ref.extra)
                            html = '<td class="name"><var id="{}">{}</var>({}){}</td>'
                            out(html.format(ref.name, display, args, link))
                        meta = functions_meta_columns
                        if ref.flags.get('meta'):
                            out('<td class="meta">{}</td>'.format(ref.flags['meta']))
                            meta -= 1
                        while meta > 0:
                            out('<td class="meta"></td>')
                            meta -= 1
                        ref.params, ref.returns, ref.md = self._content_to_markdown(ref.content)
                        md = self._get_first_sentence(ref.md)[0] if not functions_compact else ref.md
                        out('<td class="doc">{}</td>'.format(self._markdown_to_html(md)))
                        out('</tr>')
                    out('</table>')
                out('</div>')

            #
            # Output fields for this section
            #
            if fields and not fields_compact:
                if functions:
                    out('<h3 class="fields">{}</h3>'.format(fields_title))
                out('<dl class="fields">')
                for ref in fields:
                    out('<dt id="{}">'.format(ref.name))
                    out('<span class="icon"></span><var>{}</var>'.format(ref.display))
                    if ref.flags.get('type'):
                        types = self._types_to_html(ref.flags['type'])
                        out('<span class="tag type">{}</span>'.format(types))
                    if ref.flags.get('meta'):
                        out('<span class="tag meta">{}</span>'.format(ref.flags['meta']))
                    out(self._permalink(ref.name))
                    out('</dt>')
                    out('<dd>')
                    out(self._markdown_to_html(ref.md))
                    out('</dd>')
                out('</dl>')

            #
            # Output functions for this section
            #
            if functions and not functions_compact:
                if fields:
                    out('<h3 class="functions">{}</h3>'.format(functions_title))
                out('<dl class="functions">')
                for ref in functions:
                    args = ', '.join('<em>{}</em>'.format(arg) for arg in ref.extra)
                    out('<dt id="{}">'.format(ref.name))
                    out('<span class="icon"></span><var>{}</var>({})'.format(ref.display, args))
                    if ref.flags.get('meta'):
                        out('<span class="tag meta">{}</span>'.format(ref.flags['meta']))
                    out(self._permalink(ref.name))
                    out('</dt>')
                    out('<dd>')
                    out(self._markdown_to_html(ref.md))
                    if ref.params:
                        out('<div class="heading">Parameters</div>')
                        out('<table class="parameters">')
                        for arg in ref.extra:
                            try:
                                types, doc = ref.params[arg]
                            except KeyError:
                                log.warning('%s() missing @tparam for "%s" parameter', ref.name, arg)
                                types = []
                                doc = ''
                            out('<tr>')
                            out('<td class="name"><var>{}</var></td>'.format(arg))
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
        return has_content

    def render_search_index(self):
        log.info('generating search index')
        topref = self.parser.refs['--search']
        self.ctx.update(ref=topref)
        lines = []
        out = lines.append
        def add(ref, tp):
            href = self._get_ref_href(ref)
            _, _, md = self._content_to_markdown(ref.content)
            text = self._markdown_to_text(md)
            title = ref.display
            if tp == 'section' and ref.topref.type != 'manual':
                # Non-manual sections typically use the first sentence as the section
                # title.  This heuristic uses the first sentence only if it's less than 80
                # characters, otherwise falls back to the section title.
                first, remaining = self._get_first_sentence(text)
                if len(first) < 80:
                    title = first
                    text = remaining
            text = text.replace('"', '\\"').replace('\n', ' ')
            title = title.replace('"', '\\"').replace('\n', ' ')
            if tp == 'module':
                title = title.split('.', 1)[-1]
            out('{{path:"{}", type:"{}", title:"{}", text:"{}"}},'.format(href, tp, title, text))

        out('var docs = [')
        for tp in 'class', 'module', 'field', 'function', 'section':
            for ref in self.parser.parsed[tp]:
                add(ref, tp)
        out('];')
        return '\n'.join(lines)

    def render_search_page(self):
        root = self._get_root_path()
        topref = self.parser.refs['--search']
        topref.html = [self._templates['search'].format(root=root, version=self._assets_version)]
        return self.render(topref)

    def render_landing_page(self):
        # A bit lazy to reuse the search topref here, but we just need a reference
        # from the same directory so the link paths are correct.
        topref = self.parser.refs['--search']
        topref.html = []
        return self.render(topref)
