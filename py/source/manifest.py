#!/usr/bin/env python

import cPickle
import functools
import operator
import traceback
import os

import logging

log = logging.getLogger('paste')

from ..util import OrderedDict, OrderedSet, content_type_helper

from .runtime import Runtime
env = Runtime.get().env

from . import module as paste_module, primer as paste_primer


class ContentTypeManifest(object):
    def __init__(self, content_type, manifest, sorted_deps):
        super(ContentTypeManifest, self).__init__()
        self.content_type = content_type
        self.manifest = manifest
        self.sorted_deps = sorted_deps
        self._primer = None

    @property
    def primer(self):
        if self._primer is None:
            self._primer = paste_primer.PrimerHelper.get_content_type_primer(self.content_type)
        return self._primer


class Manifest(object):
    _instance = None

    class ParseException(Exception):
        pass

    class OpenException(Exception):
        pass

    def __init__(self, manifest=None, sorted_deps=None):
        super(Manifest, self).__init__()
        self._manifest = manifest or dict((primer.content_type.type, {})
                                          for primer in paste_primer.PrimerHelper.primers)
        self._sorted_deps = sorted_deps or dict((primer.content_type.type, None)
                                                for primer in paste_primer.PrimerHelper.primers)

    def __hash__(self):
        ordered_manifest = OrderedDict(sorted(self._manifest.iteritems(), key=lambda k: k[0]))
        manifest_hash = hash(cPickle.dumps(sorted([
            (content_type, sorted([(module_name, hash(module))
                                   for (module_name, module) in content_type_manifest.iteritems()],
                                  key=lambda tup: tup[0]))
            for (content_type, content_type_manifest) in ordered_manifest.iteritems()
        ]), protocol=cPickle.HIGHEST_PROTOCOL))

        sorted_dep_hash = hash(cPickle.dumps(
            sorted(self._sorted_deps.iteritems(), key=operator.itemgetter(1)),
            protocol=cPickle.HIGHEST_PROTOCOL)
        )

        return hash(u'%s|%s' % (manifest_hash, sorted_dep_hash))

    def get_manifest(self, content_type):
        return self._manifest.setdefault(content_type.type, {})

    def get_sorted_deps(self, content_type):
        sorted_deps = self._sorted_deps.get(content_type.type)
        if not sorted_deps:
            self._sorted_deps[content_type] = self._sort_modules(
                [module for (module_name, module) in self.get_manifest(content_type).iteritems()]
            )

        return sorted_deps

    def _path_to_modules(self, path, content_type):
        modules = []
        for dir_path, dir_names, file_names in os.walk(path):
            dir_names[:] = [dir_name
                            for dir_name in dir_names
                            if dir_name not in env.excluded_dirs + (env.build_prefix,)]
            for module_file in file_names:
                if module_file.endswith(content_type.file_extension):
                    module = paste_module.Module(os.path.normpath(dir_path + os.sep + module_file))
                    modules.append(module)
        return modules

    def _clean_unprimed_modules(self, local_manifest):
        unprimed_modules = [
            (module_name, module, paste_primer.PrimerHelper.get_content_type_primer(
                content_type_helper.type_to_content_type(content_type_key)
            ))
            for content_type_key, modules_dict in self._manifest.iteritems()
            for module_name, module in modules_dict.iteritems()
            if not local_manifest.get(content_type_key)
            or not local_manifest.get(content_type_key, {}).get(module_name)]

        for (module_name, module, primer) in unprimed_modules:
            if primer.unprime(module).removed and not env.versioning:
                # leave old module names if versioning is on, else delete it
                del self._manifest[primer.content_type.type][module_name]

    def _prime_modules(self, modules, content_type):
        primed_modules = []
        primer = paste_primer.PrimerHelper.get_content_type_primer(content_type)
        if not primer:
            log.error('No primer found for %r' % content_type.__dict__)
            return primed_modules

        existing_manifest = self.get_manifest(content_type)
        for index, module in enumerate(modules):
            primed_module = primer.prime(module, existing_manifest=existing_manifest)

            if primed_module:
                if None in primed_module.dependencies:
                    log.error('Dependency of NoneType found in %s.' % primed_module.source_path)
                primed_modules.append(primed_module)
        return primed_modules

    def _sort_modules(self, modules):
        def topo_sort(name_dep_dict):
            for k, v in name_dep_dict.iteritems():
                v.discard(k)

            # get all unique values and back fill missing deps
            extra_items_in_deps = functools.reduce(OrderedSet.union, name_dep_dict.values()) - OrderedSet(
                name_dep_dict.keys())
            name_dep_dict.update(
                dict((item, OrderedSet()) for item in extra_items_in_deps))
            while True:
                ordered = OrderedSet(
                    item for item, dep in name_dep_dict.items() if not dep)
                if not ordered:
                    break
                yield sorted(ordered)
                name_dep_dict = dict(
                    (item, (dep - ordered)) for (item, dep) in name_dep_dict.iteritems() if item not in ordered
                )
            if name_dep_dict:
                log.error("circular dependency %r" % name_dep_dict)

        if not modules:
            return []

        name_dep_dict = dict((module.name, module.dependencies) for module in modules)
        topo_sort_nodes = [super_node for super_node in topo_sort(name_dep_dict)]
        flat_top_sorted = [node for super_node in topo_sort_nodes for node in sorted(super_node)]

        def walk_tree(dep, dep_set=None):
            if dep_set is None:
                dep_set = OrderedSet()

            for dep in name_dep_dict[dep]:
                dep_set.add(dep)
                dep_set |= walk_tree(dep, dep_set)

            return dep_set

        # backfill dependencies
        for module_name in reversed(flat_top_sorted):
            module = next((module for module in modules if module.name == module_name), None)
            if module is None:
                log.error('Required module for %s is missing a name declaration.' % module_name)
            else:
                module.dependencies |= walk_tree(module_name)

        sorted_module_paths = []
        for module_name in flat_top_sorted:
            module = next((module for module in modules if module.name == module_name), None)
            if module:
                sorted_module_paths.append((module_name, module.path, module.version))

        return sorted_module_paths

    def build(self, **options):
        local_manifest = {}
        for content_type, path in env.content_type_paths + env.internal_lib_paths:
            raw_modules = self._path_to_modules(path, content_type)
            primed_modules = self._prime_modules(raw_modules, content_type, **options)

            local_manifest[content_type.type] = local_manifest.get(content_type.type, {})
            self._manifest[content_type.type] = self._manifest.get(content_type.type, {})

            content_type_module_dict = dict((module.name, module) for module in primed_modules)
            local_manifest[content_type.type].update(content_type_module_dict)
            self._manifest[content_type.type].update(content_type_module_dict)

        self._clean_unprimed_modules(local_manifest)
        self._sorted_deps = dict(
            (
                content_type_key,
                self._sort_modules([module for (module_name, module) in module_dict.iteritems()])
            ) for (content_type_key, module_dict) in self._manifest.iteritems()
        )

    def serialize(self):
        return {
            'manifest': dict(
                (
                    content_type,
                    dict((module_name, module.serialize())
                         for (module_name, module) in content_type_manifest.iteritems())
                ) for (content_type, content_type_manifest) in self._manifest.iteritems()
            ),
            'sorted_deps': self._sorted_deps
        }

    def save(self):
        build_file = open(self._build_path(), 'wb')
        try:
            build_file.write(cPickle.dumps(self.serialize(), protocol=cPickle.HIGHEST_PROTOCOL))
        finally:
            build_file.close()

    @classmethod
    def _build_path(cls):
        return os.path.normpath(
            (env.build_area if env.build_area else os.path.dirname(os.path.abspath(__file__)))
            + os.sep + env.build_prefix + '.pkl'
        )


    @classmethod
    def deserialize(cls, obj):
        manifest = dict(
            (content_type, dict(
                (module_name, paste_module.Module.deserialize(serialized_module))
                for (module_name, serialized_module) in serialized_manifest.iteritems()
            ))
            for (content_type, serialized_manifest) in obj.get('manifest', {}).iteritems()
        )
        return cls(
            manifest=manifest,
            sorted_deps=obj.get('sorted_deps', None)
        )

    @classmethod
    def load(cls):
        if cls._instance is None:
            if not env.compile_mode and os.path.exists(cls._build_path()):
                try:
                    read_file = open(cls._build_path(), "rb")
                except Exception, e:
                    raise cls.OpenException(e)

                try:
                    cls._instance = cls.deserialize(cPickle.loads(str(read_file.read())))
                except Exception, e:
                    raise cls.ParseException(e)
                finally:
                    read_file.close()

        return cls._instance

    @classmethod
    def get_content_type_manifest(cls, content_type):
        try:
            instance = cls.load()
        except cls.OpenException, e:
            instance = None
            log.error('Error opening manifest file. e=' + traceback.format_exc())
        except cls.ParseException, e:
            instance = None
            log.error('Error parsing manifest file. e=' + traceback.format_exc())

        if instance is None:
            log.error('Creating manifest from scratch')
            instance = Manifest()
            instance.build()
            cls._instance = instance

        return ContentTypeManifest(
            content_type,
            instance.get_manifest(content_type), instance.get_sorted_deps(content_type)
        )
