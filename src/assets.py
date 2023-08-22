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

import os
import posixpath
import glob
import hashlib
from zipfile import ZipFile

class Assets:
    def __init__(self, path):
        try:
            self.zipfile = ZipFile(__loader__.archive)
        except AttributeError:
            # Not currently executing out of a zip bundle
            self.zipfile = None

        if self.zipfile:
            self.zippath = os.path.abspath(__loader__.archive)
            assert path.startswith(self.zippath + os.path.sep), 'assets path not found in zip bundle'
            self.path = path[len(self.zippath) + 1:].replace(os.path.sep, '/')
            files = [i.filename for i in self.zipfile.infolist()
                     if i.filename.startswith(self.path) and not i.is_dir()]
            self._join = posixpath.join
        else:
            self.path = path
            self._join = os.path.join
            files = [f for f in glob.glob(os.path.join(path, '**'), recursive=True) if not os.path.isdir(f)]

        # Strip path prefix from files list.
        self.files = [f[len(self.path)+1:] for f in files]

    def open(self, fname):
        path = self._join(self.path, fname)
        if self.zipfile:
            return self.zipfile.open(path)
        else:
            return open(path, 'rb')

    def get(self, fname):
        with self.open(fname) as f:
            return f.read()

    def hash(self):
        h = hashlib.sha256()
        for f in sorted(self.files):
            h.update(self.get(f))
        return h.hexdigest()

assets = Assets(os.path.abspath(os.path.join(os.path.dirname(__file__), '../assets')))
