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

import os
import shutil
from typing import List, Optional

from ..log import log
from ..parse import *
from ..reference import *
from ..utils import files_str_to_list

class Renderer:
    """
    Base class for renderers
    """
    def __init__(self, parser: Parser):
        self.parser = parser
        self.config = parser.config
        self.ctx = parser.ctx

    def copy_files_from_config(self, section: str, option: str, outdir: str) -> None:
        for fname in files_str_to_list(self.config.get(section, option, fallback='')):
            if not os.path.exists(fname):
                log.critical('%s file "%s" does not exist, skipping', option, fname)
                continue
            shutil.copy(fname, outdir)

    def render(self, toprefs: List[TopRef], outdir: Optional[str]) -> None: # pyright: ignore
        """
        Renders all toprefs to the given output directory (or file, depending on the
        renderer).

        It's the caller's obligation to have passed these toprefs through the prerenderer.
        """
        raise NotImplemented
