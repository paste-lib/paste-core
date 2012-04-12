import abc
import os

from ..util import content_type_helper

_internal_lib_paths = ((
                           content_type_helper.JAVASCRIPT,
                           os.path.join(os.path.dirname(os.path.abspath(__file__)), '..' + 'lib' + 'js' + 'source')
                       ),)


class BaseEnv(object):
    __metaclass__ = abc.ABCMeta

    internal_lib_paths = _internal_lib_paths

    @property
    @abc.abstractproperty
    def content_type_paths(self):
        raise NotImplementedError('')

    @abc.abstractproperty
    def compile_mode(self):
        raise NotImplementedError('')

    @abc.abstractproperty
    def build_area(self):
        raise NotImplementedError('')

    @abc.abstractproperty
    def build_prefix(self):
        raise NotImplementedError('')

    @abc.abstractproperty
    def root_uri(self):
        raise NotImplementedError('')

    @abc.abstractproperty
    def versioning(self):
        raise NotImplementedError('')

    @abc.abstractproperty
    def app_root(self):
        raise NotImplementedError('')

    @abc.abstractproperty
    def excluded_dirs(self):
        raise NotImplementedError('')

    @abc.abstractproperty
    def network_request_threshold(self):
        raise NotImplementedError('')


class DefaultEnv(BaseEnv):
    @property
    def content_type_paths(self):
        return []

    @property
    def compile_mode(self):
        return False

    @property
    def build_area(self):
        return None

    @property
    def build_prefix(self):
        return '_build'

    @property
    def root_uri(self):
        return '/paste/'

    @property
    def versioning(self):
        return True

    @property
    def app_root(self):
        return os.path.join(os.path.dirname(os.path.abspath(__file__)), '/..')

    @property
    def excluded_dirs(self):
        return [self.build_prefix]

    @property
    def network_request_threshold(self):
        return 1024
