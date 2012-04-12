#!/usr/bin/env python
import traceback

import os
import abc
import hashlib
import re

import logging
from paste.service import compressor

log = logging.getLogger('paste')

from ..util import OrderedSet, content_type_helper

from .runtime import Runtime
env = Runtime.get().env

from . import module as paste_module


class Primer(object):
    __metaclass__ = abc.ABCMeta

    def __init__(self):
        super(Primer, self).__init__()

    @abc.abstractproperty
    def content_type(self):
        raise NotImplementedError('A content type must be set')

    def find_existing_module(self, existing_manifest, module):
        existing_module = None
        if existing_manifest and existing_manifest.get(module.name):
            existing_module = existing_manifest.get(module.name)

        return existing_module

    def can_prime(self, existing_module, module):
        requires_compression = True
        if existing_module:
            if existing_module.source_checksum == module.source_checksum:
                module.coalesce(existing_module)
                module.set_contents(existing_module.contents)
                requires_compression = False
        return requires_compression

    def set_primed_content(self, existing_module, module, primed_contents, path=None):
        if env.compile_mode:
            module.set_contents(primed_contents, path=path)
            return

        primed_checksum = hashlib.md5(primed_contents).hexdigest()

        if existing_module:
            module.version = (existing_module.version_from_path() or module.version)
            if env.versioning and primed_checksum != existing_module.checksum:
                log.debug('Incrementing version=%s; new_version=%s; path=%s' % (
                    module.version,
                    (module.version + 1),
                    module.abs_path
                ))
                module.bump_version(existing_module)

            if existing_module.checksum == primed_checksum:
                module.coalesce(existing_module)

        module.set_contents(primed_contents, path)

        if existing_module and existing_module.abs_path and os.path.exists(existing_module.abs_path):
            if not env.versioning:
                log.debug('Removing old primed_path=%s; source=%s.' % (
                    existing_module.abs_path,
                    module.source_path
                ))
                os.remove(existing_module.abs_path)

    @abc.abstractmethod
    def prime(self, module, existing_manifest=None):
        return paste_module.Module.deserialize(module.serialize())

    def unprime(self, module):
        if module.abs_path and os.path.exists(module.abs_path):
            log.debug('Removing unprimed module path=%s; source=%s; versioning:%s' % (
                module.abs_path,
                module.source_path,
                env.versioning
            ))
            module.remove(remove_source=not env.versioning)
        return module

    @classmethod
    def read_primed(cls, path):
        content_file = open(os.path.normpath(os.path.normpath(env.app_root) + os.sep + path), "rb")
        try:
            contents = content_file.read()
        except IOError, e:
            contents = ''
            log.warning('Could not read file %s. e=%s' % (path, traceback.format_exc()))
        finally:
            content_file.close()

        return contents


class JavascriptPrimer(Primer):
    JS_COMMENT_EXPR = re.compile(r'/\*\*.*?@(?:module|require).*?\*/', re.S | re.M)
    JSDOC_MODULE_EXPR = re.compile(r'@(?P<type>module|requires)\s(?P<name>[\w||/.].+)')
    CLOSURE_COMPILATION_EXPR = re.compile(r'@(?P<type>compilation_level)\s(?P<value>[\w].+)')

    @property
    def content_type(self):
        return content_type_helper.JAVASCRIPT

    def _parse_file(self, contents):
        module_name = None
        dependencies = OrderedSet()
        closure_compilation_level = 'SIMPLE_OPTIMIZATIONS'
        # search the comments of the current file. look for defined modules and dependencies via @module and @requires
        for match in self.JS_COMMENT_EXPR.findall(contents):
            # look for a custom compilation level in the file (jsdoc syntax)
            for compilation_level in self.CLOSURE_COMPILATION_EXPR.finditer(match):
                closure_compilation_level = compilation_level.group('value')
                break

            # find jsdoc the modules and dependencies
            for dependency in self.JSDOC_MODULE_EXPR.finditer(match):
                if dependency.group('type').lower() == 'requires':
                    dependencies.update([dependency.group('name').replace('/', '.')])
                else:
                    module_name = dependency.group('name').replace('/', '.')

        return module_name, dependencies, closure_compilation_level

    def prime(self, module, existing_manifest=None, build_docs=False):
        closure_compilation_level = None

        if not module.name or not module.dependencies:
            module_name, dependencies, closure_compilation_level = self._parse_file(module.source_contents)

            module.name = module_name
            module.dependencies = dependencies

        if not module.name:
            log.debug('Cannot parse module name, skipping path %s' % module.source_path)
            return None

        existing_module = self.find_existing_module(existing_manifest, module)
        requires_compression = self.can_prime(existing_module, module)

        if requires_compression or not module.path or not os.path.exists(module.abs_path):
            if not closure_compilation_level:
                module_name, dependencies, closure_compilation_level = self._parse_file(module.source_contents)

            if env.compile_mode:
                self.set_primed_content(existing_module, module, module.source_contents, module.source_path)
            else:
                primed_contents = None
                try:
                    primed_contents = '%s' % (
                        compressor.compress(
                            module.source_contents, 'js', '--compilation_level ' + closure_compilation_level
                        )
                    )
                finally:
                    if not primed_contents:
                        log.warning('Priming failure at:  %s' % module.source_path)

                self.set_primed_content(existing_module, module, primed_contents)

                compressed_file = open(module.abs_path, 'wb')
                try:
                    compressed_file.write(module.contents)
                finally:
                    compressed_file.close()

            log.debug('Primed source=%s; primed_path=%s.' % (module.source_path, module.path))

        return super(JavascriptPrimer, self).prime(module, existing_manifest)


class SCSSPrimer(Primer):
    MODULE_DEP_EXPR = re.compile(r'@((?P<type>module|requires)\s+"(?P<name>[\w||/.].+?))";')
    SCSS_LOAD_PATHS = [path for content_type, path in env.content_type_paths + env.internal_lib_paths]

    @property
    def content_type(self):
        return content_type_helper.SCSS

    @classmethod
    def _clean_source_contents(cls, source_contents, dependencies=None, module=None):
        def compute_module_replacement(match):
            if dependencies or module:
                name = (match.group('name') or '').replace('/', '.')
                if dependencies is not None and match.group('type').lower() == 'requires' and name:
                    dependencies.update([name])
                elif module is not None and match.group('type').lower() == 'module' and name:
                    module.name = name

            return ''

        return cls.MODULE_DEP_EXPR.sub(compute_module_replacement, source_contents)

    def prime(self, module, existing_manifest=None, build_docs=False):
        dependencies = OrderedSet()

        clean_source_contents = self._clean_source_contents(
            module.source_contents,
            dependencies=dependencies,
            module=module
        )
        module.dependencies = dependencies

        if not module.name:
            log.debug('Cannot parse module name, skipping path %s' % module.source_path)
            return None

        existing_module = self.find_existing_module(existing_manifest, module)
        _ = self.can_prime(existing_module, module)
        # we *could* check some buried property in scss.compile. but either way we have to run the compiler
        # check every time and look at the diff between the md5's when in compressed mode

        if env.compile_mode:
            # we will run the scss compilation at runtime
            self.set_primed_content(existing_module, module, clean_source_contents, path=module.source_path)
        else:
            primed_contents = compressor.compress(
                clean_source_contents,
                'css',
                '--compress',
                load_paths=self.SCSS_LOAD_PATHS
            )
            if not primed_contents:
                log.warning('Priming failure at:  %s' % module.source_path)
                return None

            pc_md5 = hashlib.md5(primed_contents).hexdigest()
            cleaned_primed_contents = self._clean_source_contents(primed_contents)
            cpc_md5 = hashlib.md5(cleaned_primed_contents).hexdigest()

            if pc_md5 != cpc_md5:
                log.warning(
                    u'Circular @module/@require/@import found at: %s' % module.source_path,
                )

            self.set_primed_content(existing_module, module, cleaned_primed_contents)

            if not os.path.exists(module.abs_path):
                compressed_file = open(module.abs_path, 'wb')
                try:
                    compressed_file.write(module.contents)
                finally:
                    compressed_file.close()

                log.debug('Primed source=%s; primed_path=%s.' % (module.source_path, module.path))

        return super(SCSSPrimer, self).prime(module, existing_manifest)

    @classmethod
    def read_primed(cls, path):
        contents = super(SCSSPrimer, cls).read_primed(path)
        if env.compile_mode:
            contents = compressor.compress(
                cls._clean_source_contents(contents),
                'css',
                load_paths=cls.SCSS_LOAD_PATHS
            )
        return contents


class CSSPrimer(SCSSPrimer):
    @property
    def content_type(self):
        return content_type_helper.CSS


_primers = (
    JavascriptPrimer(),
    SCSSPrimer(),
    CSSPrimer()
)


class PrimerHelper(object):
    primers = _primers

    JAVASCRIPT = _primers[0]
    SCSS = _primers[1]
    CSS = _primers[2]

    @classmethod
    def get_content_type_primer(cls, content_type):
        return next((primer for primer in _primers if primer.content_type == content_type), None)
