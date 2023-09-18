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

__all__ = ['YAMLRenderer']

from typing import List

import yaml
try:
    from yaml import CDumper as Dumper
except ImportError:
    from yaml import Dumper

from ..parse import *
from ..reference import *
from ..utils import *
from .json import JSONRenderer

def str_representer(dumper: Dumper, data: str, **kwargs):
    """
    Represents strings containing newlins as a YAML block scalar.
    """
    if data.count('\n') >= 1:
        kwargs['style'] = '|'
    return dumper.represent_scalar('tag:yaml.org,2002:str', data, **kwargs)


class YAMLRenderer(JSONRenderer):

    def render(self, toprefs: List[TopRef], dst: str) -> None:
        """
        Renders toprefs as YAML to the given output directory or file.
        """
        # Register the custom string representer for block strings
        Dumper.add_representer(str, str_representer)
        project = self._generate(toprefs)
        outfile = self._get_outfile(dst, ext='.yaml')
        with open(outfile, 'w') as f:
            yaml.dump(project, stream=f, sort_keys=False, allow_unicode=True, Dumper=Dumper)
