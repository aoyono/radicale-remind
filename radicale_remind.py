# Remind, Abook, Taskwarrior Storage backend for Radicale
#
# Copyright (C) 2013-2018  Jochen Sprickerhof
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.
"""Remind, Abook, Taskwarrior Storage backend for Radicale"""

from abook import Abook
from colorsys import hsv_to_rgb
from contextlib import contextmanager
from icstask import IcsTask
from os.path import basename, dirname, expanduser, join
from pytz import timezone
from radicale.item import Item
from radicale.pathutils import sanitize_path
from radicale.storage import BaseCollection
from remind import Remind
from time import gmtime, strftime


class Collection(BaseCollection):
    """Collection stored in adapters for Remind, Abook, Taskwarrior"""

    def __init__(self, path, filename=None, adapter=None):
        self.path = sanitize_path(path).strip('/')
        self.filename = filename
        self.adapter = adapter

    @classmethod
    def static_init(cls):
        """init collection copy"""
        cls.adapters = []
        cls.filesystem_folder = expanduser(cls.configuration.get('storage', 'filesystem_folder'))

        if cls.configuration.has_option('storage', 'remind_file'):
            tz = None
            if cls.configuration.has_option('storage', 'remind_timezone'):
                tz = timezone(cls.configuration.get('storage', 'remind_timezone'))
            month = cls.configuration.getint('storage', 'remind_lookahead_month', fallback=15)
            cls.adapters.append(Remind(cls.configuration.get('storage', 'remind_file'), tz, month=month))

        if cls.configuration.has_option('storage', 'abook_file'):
            cls.adapters.append(Abook(cls.configuration.get('storage', 'abook_file')))

        if cls.configuration.has_option('storage', 'task_folder'):
            cls.adapters.append(IcsTask(cls.configuration.get('storage', 'task_folder')))

    @classmethod
    def discover(cls, path, depth="0"):
        """Discover a list of collections under the given ``path``."""

        if path.count('/') < 3:
            yield cls(path)

            if depth != '0':
                for adapter in cls.adapters:
                    for filename in adapter.get_filesnames():
                        yield cls(filename.replace(cls.filesystem_folder, ''), filename, adapter)
            return

        filename = join(cls.filesystem_folder, dirname(path).strip('/'))
        collection = None

        for adapter in cls.adapters:
            if filename in adapter.get_filesnames():
                collection = cls(path, filename, adapter)
                break

        if not collection:
            return

        if path.endswith('/'):
            yield collection

            if depth != '0':
                for uid in collection._list():
                    yield collection._get(uid)
            return

        if basename(path) in collection._list():
            yield collection._get(basename(path))
            return

    @classmethod
    def move(cls, item, to_collection, to_href):
        """Move an object.

        ``item`` is the item to move.

        ``to_collection`` is the target collection.

        ``to_href`` is the target name in ``to_collection``. An item with the
        same name might already exist.

        """
        if item.collection.path == to_collection.path and item.href == to_href:
            return

        to_collection.adapter.move_vobject(to_href, item.collection.filename, to_collection.filename)

    def get_multi(self, hrefs):
        """Fetch multiple items.

        It's not required to return the requested items in the correct order.
        Duplicated hrefs can be ignored.

        Returns tuples with the href and the item or None if the item doesn't
        exist.

        """
        return ((x[0], self._convert(x)) for x in self.adapter.to_vobjects(self.filename, hrefs))

    def get_all(self):
        """Fetch all items."""
        return (self._convert(x) for x in self.adapter.to_vobjects(self.filename))

    def _list(self):
        """List collection items."""
        if not self.adapter:
            self.logger.warning("No adapter for collection: %r, please provide a full path", self.path)
            return
        for uid in self.adapter.get_uids(self.filename):
            yield uid

    def _convert(self, elem):
        """Fetch a single item."""
        return Item(collection=self, vobject_item=elem[1], href=elem[0], last_modified=self.last_modified, etag=elem[2])

    def _get(self, href):
        """Fetch a single item."""
        item, etag = self.adapter.to_vobject_etag(self.filename, href)
        return self._convert((href, item, etag))

    def upload(self, href, item):
        """Upload a new or replace an existing item."""
        if href in self.adapter.get_uids(self.filename):
            uid = self.adapter.replace_vobject(href, item.vobject_item, self.filename)
        else:
            uid = self.adapter.append_vobject(item.vobject_item, self.filename)
        try:
            return self._get(uid)
        except KeyError:
            self.logger.warning("Unable to find uploaded event, maybe increase remind_lookahead_month")

    def delete(self, href=None):
        """Delete an item."""
        self.adapter.remove(href, self.filename)

    def _get_color(self):
        files = self.adapter.get_filesnames()
        index = files.index(self.filename)
        rgb = hsv_to_rgb((index / len(files) + 1 / 3) % 1.0, 0.5, 1.0)
        r, g, b = (int(255 * x) for x in rgb)
        return '#{r:02x}{g:02x}{b:02x}'.format(**locals())

    def get_meta(self, key=None):
        """Get metadata value for collection."""
        if self.adapter:
            meta = self.adapter.get_meta()
            meta['D:displayname'] = basename(self.path)
            meta['ICAL:calendar-color'] = self._get_color()
        else:
            meta = {}
        return meta.get(key) if key else meta

    @property
    def last_modified(self):
        """Get the HTTP-datetime of when the collection was modified."""
        return strftime('%a, %d %b %Y %H:%M:%S +0000', gmtime(self.adapter.last_modified()))

    @classmethod
    @contextmanager
    def acquire_lock(cls, mode, user=None):
        """Set a context manager to lock the whole storage."""
        yield
