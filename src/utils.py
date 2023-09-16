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
    'Sentinel', 'Content', 'ContentFragment', 'Markdown', 'Admonition', 'SeeAlso',
    'recache', 'get_first_sentence', 'get_indent_level', 'strip_trailing_comment',
]

import enum
import re
import string
from dataclasses import dataclass
from functools import lru_cache
from typing import Tuple, List, Callable, Optional, Pattern

# Common abbreviations with periods that are considered when determining what is the
# first sentence of a markdown block.
ABBREV = {
    'e': ('e.g.', 'eg.', 'etc.', 'et al.'),
    'i': ('i.e.', 'ie.'),
    'v': ('vs.',),
}
# Used for detecting word boundaries. Anything *not* in this set can be considered as a
# word boundary.
WORD_CHARS = set(string.ascii_lowercase)

# Callback type used by content objects for postprocessing finalized content. Used for
# converting refs to markdown links.
PostProcessFunc = Optional[Callable[[str], str]]

class ContentFragment:
    """
    Base class for elements of a Content list.
    """
    pass


class Markdown(ContentFragment):
    """
    Represents a markdown string.
    """
    def __init__(self, value: Optional[str] = None, postprocess: Optional[PostProcessFunc]=None):
        # Lines accumulated via append()
        self._lines = [value] if value is not None else []
        self._postprocess = postprocess
        # Cached postprocessed value
        # append() is called between get() calls (this case is rare or nonexistent)
        self._value: str|None = None

    def append(self, s: str) -> 'Markdown':
        """
        Appends a line to the markdown string.  Cannot be called after get().
        """
        assert(self._value is None)
        self._lines.append(s)
        return self

    def rstrip(self) -> 'Markdown':
        """
        Removes trailing whitespace from the current set of lines added by append().
        """
        self._lines = ['\n'.join(self._lines).rstrip()]
        return self

    def get(self) -> str:
        """
        Returns the final markdown string, postprocessed if a postprocessor was passed during initialization.

        append() cannot be called after this point.
        """
        if self._value is None:
            md = '\n'.join(self._lines)
            if self._postprocess:
                md = self._postprocess(md)
            self._value = md
            del self._lines[:]
        return self._value


@dataclass
class Admonition(ContentFragment):
    """
    A @note or @warning admonition tag.
    """
    type: str
    title: str
    content: 'Content'


@dataclass
class SeeAlso(ContentFragment):
    """
    A @see tag.
    """
    # List of ref ids.
    refs: List[str]


class Content(List[ContentFragment]):
    """
    Parsed and prerendered content. The prerender stage resolves all references to
    'luadox:' markdown links.

    Content is captured as a list of content fragments -- the most common of which is
    Markdown -- where fragments are different types of objects that the renderer needs to
    decide how to translate.
    """
    def __init__(self, *args, postprocess: PostProcessFunc = None):
        super().__init__(*args)
        self._md_postprocess = postprocess
        self._first = None

    def get_first_sentence(self, pop=False) -> str:
        """
        Returns the first sentence from the content.  If pop is True then the content
        is updated in-place to remove the sentence that was returned.
        """
        if len(self) == 0:
            return ''
        e = self[0]
        if not isinstance(e, Markdown):
            return ''
        first, remaining = get_first_sentence(e.get())
        if pop:
            if remaining:
                self[0] = Markdown(remaining)
            else:
                self.pop(0)
        return first

    def md(self, postprocess: PostProcessFunc = None) -> Markdown:
        """
        Convenience method that returns the last fragment in the content list if it's a
        Markdown, or creates and appends a new one if the last element isn't Markdown.
        """
        if len(self) > 0 and isinstance(self[-1], Markdown):
            md = self[-1]
            assert(isinstance(md, Markdown))
        else:
            md = Markdown(postprocess=postprocess or self._md_postprocess)
            self.append(md)
        return md


class Sentinel(enum.Enum):
    """
    Type friendly sentinel to distinguish between None and lack of argument.
    """
    UNDEF = object()


@lru_cache(maxsize=None)
def recache(pattern: str, flags: int = 0) -> Pattern[str]:
    """
    Returns a compiled regexp pattern, caching the result for subsequent invocations.
    """
    return re.compile(pattern, flags)


def get_first_sentence(s: str) -> Tuple[str, str]:
    """
    Returns a 2-tuple of the first sentence from the given markdown, and all remaining.
    """
    # This is fairly low level looking code, but it performs reasonably well for what it
    # does.
    l = s.lower()
    end = len(l) - 1
    last = ''
    n = 0
    while n <= end:
        c = l[n]
        if c == '\n' and last == '\n':
            # Treat two consecutive newlines as a sentence terminator.
            break
        elif c == '.':
            # Is this period followed by whitespace or EOL?
            if n == end or l[n+1] == ' ' or l[n+1] == '\n':
                # Found end-of-sentence.
                break
        elif c in ABBREV and last not in WORD_CHARS:
            # This character appears to start a word of an abbreviation we want to handle.
            # If the next set of characters matches an abbrevation variation, skip over
            # it.
            for abbr in ABBREV[c]:
                if l[n:n+len(abbr)] == abbr:
                    # Subtract 1 from the abbrevation length since we're adding 1 below
                    n += len(abbr) - 1
                    break
        last = l[n]
        n += 1
    else:
        # Didn't break out of while loop so we weren't able to find end-of-sentence.
        # Consider the entire given string as the first sentence.
        return s, ''

    # If we're here, n represents the position of the end of first sentence.
    return s[:n], s[n+1:].strip()


def get_indent_level(s: str) -> int:
    """
    Returns the number of spaces on left side of the string.
    """
    m = recache(r'^( *)').search(s)
    return len(m.group(1)) if m else 0


def strip_trailing_comment(line: str) -> str:
    return recache(r'--.*').sub('', line)

