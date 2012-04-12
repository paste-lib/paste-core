import cPickle
import hashlib
import operator
import traceback
import os
import re
import stat
import sys

import logging
log = logging.getLogger('paste')

from ..util import OrderedSet, content_type_helper

from .runtime import Runtime
env = Runtime.get().env


class Module(object):
    DEFAULT_VERSION = 1.0

    def __init__(self, source_path, source_checksum=None, name=None, dependencies=None,
                 last_modified=None, checksum=None, path=None, byte_size=None, version=None,
                 prev_versions=None, version_removed=None):

        super(Module, self).__init__()

        self.version = version or self.DEFAULT_VERSION
        self._prev_versions = prev_versions or []  # serialized

        # Note: paths will be relative to the app root

        self._contents = None
        self._source_contents = None
        self._source_checksum = source_checksum

        if os.path.isabs(source_path):
            source_path = os.path.relpath(source_path,
                                          os.path.normpath(env.app_root))
        self.source_path = source_path

        self._name = name
        self._dependencies = OrderedSet(sorted(dependencies)) if dependencies else OrderedSet()

        # if a final path has been supplied, make sure it's legit
        self._path = None
        if path:
            if os.path.isabs(path) and os.path.exists(path):
                self._path = os.path.relpath(path, os.path.normpath(env.app_root))
            elif os.path.exists(os.path.normpath(os.path.normpath(env.app_root) + os.sep + path)):
                self._path = path
        path_contents = self._read_file(self._path) if self._path and (checksum or byte_size) else None

        if path_contents and checksum:
            path_checksum = hashlib.md5(path_contents).hexdigest()
            if path_checksum != checksum:
                log.debug(u'Checksums do not match, keeping passed value. path=%s; path_checksum="%s; checksum=%s' % (
                    self._path, path_checksum, checksum))
        self._checksum = checksum

        if path_contents and byte_size:
            path_byte_size = sys.getsizeof(path_contents, 0)
            if path_byte_size != byte_size:
                log.debug(
                    u'Byte sizes do not match, keeping passed value. path=%s; path_byte_size="%s; byte_size=%s' % (
                        self._path, path_byte_size, byte_size))
        self._byte_size = byte_size

        if self._path and last_modified:
            path_last_modified = os.stat(self.abs_path or '')[stat.ST_MTIME]
            if path_last_modified != last_modified:
                log.debug(
                    u'Last modifieds do not match, keeping passed value. path=%s; path_last_modified="%s; last_modified=%s' % (
                        self._path, path_last_modified, last_modified))
        self._last_modified = last_modified

        self._version_removed = version_removed

    def __hash__(self):
        return hash(cPickle.dumps(sorted(self.serialize().iteritems(), key=operator.itemgetter(1)),
                                  protocol=cPickle.HIGHEST_PROTOCOL))

    @classmethod
    def _read_file(cls, rel_path, absolute_path=False):
        if not absolute_path:
            rel_path = os.path.normpath(
                os.path.normpath(env.app_root) + os.sep + rel_path)

        if not os.path.exists(rel_path):
            log.warning('Error reading file %s' % rel_path)
            return ''

        opened_file = open(rel_path, "rb")
        try:
            contents = opened_file.read()
        finally:
            opened_file.close()

        return contents

    @property
    def abs_source_path(self):
        return os.path.normpath(os.path.normpath(
            env.app_root) + os.sep + self.source_path) if self.source_path else None

    @property
    def source_checksum(self):
        if self._source_checksum is None:
            self._source_checksum = hashlib.md5(
                self.source_contents).hexdigest()

        return self._source_checksum

    @property
    def source_contents(self):
        if self._source_contents is None:
            self._source_contents = self._read_file(self.source_path)
        return self._source_contents

    @property
    def contents(self):
        if self._contents is None and self._path:
            self._contents = self._read_file(self._path)
        return self._contents

    def coalesce(self, existing_module):
        if isinstance(existing_module, Module):
            self.version = existing_module.version
            self._prev_versions = existing_module.serialized_versions
            self._version_removed = existing_module.version_removed
            self._last_modified = existing_module.last_modified
        else:
            log.warning(
                'Failure setting existing module values for source_path=%s' % self.source_path
            )

    def set_contents(self, contents, path=None):
        if contents:
            self._contents = contents
            self._checksum = hashlib.md5(self._contents).hexdigest()
            self._byte_size = self._byte_size = sys.getsizeof(self._contents, 0)

            content_type = content_type_helper.filename_to_content_type(self.source_path)
            if not content_type:
                raise Exception('Error decoding filetype')

            if path:
                self._path = path
            else:
                filename = os.path.basename(self.source_path).replace(content_type.file_extension, '.%s.v%s.min%s' % (
                    self._checksum, self.version, content_type.file_extension))
                build_directory = os.path.normpath(os.path.dirname(self.source_path) + os.sep + env.build_prefix)
                build_path = os.path.normpath(
                    os.path.normpath(env.build_area or env.app_root) + os.sep + build_directory)
                if not os.path.exists(build_path):
                    os.makedirs(build_path)
                self._path = os.path.normpath(
                    os.path.relpath(build_path, os.path.normpath(env.app_root)) + os.sep + filename)

    @property
    def name(self):
        return self._name

    @name.setter
    def name(self, value):
        self._name = value

    @property
    def dependencies(self):
        return self._dependencies

    @dependencies.setter
    def dependencies(self, value):
        self._dependencies = OrderedSet(sorted(value))

    @property
    def checksum(self):
        return self._checksum

    @property
    def byte_size(self):
        return self._byte_size

    @property
    def path(self):
        return self._path

    @property
    def abs_path(self):
        return os.path.normpath(os.path.normpath(
            env.app_root) + os.sep + self.path) if self.path else None

    @property
    def last_modified(self):
        if self._last_modified is None and self.abs_path:
            self._last_modified = os.stat(self.abs_path or '')[stat.ST_MTIME]

        return self._last_modified

    def pop_versions(self):
        prev_versions = []
        for i, serialized_ver in enumerate(self._prev_versions):
            prev_versions.append(self._prev_versions.pop(i))

        return prev_versions

    @property
    def version(self):
        return self._version

    @version.setter
    def version(self, value):
        try:
            self._version = float(value)
        except ValueError, err:
            log.warning(
                'Attempting to set a bad version: value=%s; e=%s' % (value, traceback.format_exc())
            )
            self._version = self._version or self.DEFAULT_VERSION

    @property
    def serialized_versions(self):
        return self._prev_versions

    def version_from_path(self):
        version = None
        if self._path:
            content_type = content_type_helper.filename_to_content_type(self.source_path)
            versioned_path = self._path.replace('.min%s' % content_type.file_extension, '')
            version_match = re.search(r"\.v(?P<version>.+)", versioned_path)
            if version_match:
                version = version_match.group('version') or None

        if version:
            version = float(version)

        return version

    def bump_version(self, module):
        self.version += 1
        self._prev_versions = module.pop_versions()
        self._prev_versions.append(module.serialize())

    def remove(self, remove_source=False):
        self._version_removed = self.version
        if remove_source and self.abs_path:
            os.remove(self.abs_path or '')

    @property
    def version_removed(self):
        return self._version_removed

    @property
    def removed(self):
        return self._version_removed is not None

    def serialize(self):
        return {
            'source_path': self.source_path,
            'source_checksum': self.source_checksum,
            'name': self.name,
            'dependencies': sorted(self.dependencies),
            'last_modified': self.last_modified,
            'checksum': self.checksum,
            'path': self.path,
            'byte_size': self.byte_size,
            'version': self.version,
            'prev_versions': self._prev_versions,
            'version_removed': self._version_removed
        }

    @classmethod
    def deserialize(cls, obj):
        return cls(obj.get('source_path'),
                   source_checksum=obj.get('source_checksum', None),
                   name=obj.get('name', None),
                   dependencies=obj.get('dependencies', None),
                   last_modified=obj.get('last_modified', None),
                   checksum=obj.get('checksum', None),
                   path=obj.get('path', None),
                   byte_size=obj.get('byte_size', None),
                   version=obj.get('version', None),
                   prev_versions=obj.get('prev_versions', None),
                   version_removed=obj.get('version_removed', None)
        )