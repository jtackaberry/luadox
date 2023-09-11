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
    'Sentinel', 'get_first_sentence', 'get_indent_level', 'strip_trailing_comment'
]

import enum
import re
from typing import Tuple

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
RE_INDENT = re.compile(r'^( *)')
RE_COMMENT = re.compile(r'--.*')


class Sentinel(enum.Enum):
    """
    Type friendly sentinel to distinguish between None and lack of argument.
    """
    UNDEF = object()


def get_first_sentence(md: str) -> Tuple[str, str]:
    """
    Returns a 2-tuple of the first sentence from the given markdown, and
    all remaining.
    """
    # This is rather cheeky, but just handles these common abbreviations so they don't
    # interpreted as end-of-sentence.
    escape = lambda m: m.group(1).replace('.', '\x00')
    unescape = lambda s: s.replace('\x00', '.')
    first = RE_ABBREV.sub(escape, md)
    remaining = ''
    for pat in RE_FIRST_SENTENCE:
        m = pat.search(first)
        if m:
            first, pre = m.groups()
            remaining = pre + remaining
    # Remove period but preserve other sentence-ending punctuation from first
    # sentence
    return unescape(first).strip().rstrip('.'), unescape(remaining).strip()


def get_indent_level(s: str) -> int:
    """
    Returns the number of spaces on left side of the string.
    """
    m = RE_INDENT.search(s)
    return len(m.group(1)) if m else 0


def strip_trailing_comment(line: str) -> str:
    return RE_COMMENT.sub('', line)


