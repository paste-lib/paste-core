from .env import BaseEnv
from .env import DefaultEnv
from .manifest import Manifest


class Runtime(object):
    _runtime_instance = None

    def __init__(self, env=None):
        super(Runtime, self).__init__()
        self._env_instance = env if isinstance(env, BaseEnv) else DefaultEnv()
        if self._env_instance:
            Manifest.load()

    @property
    def env(self):
        return self._env_instance

    @property
    def has_started(self):
        return bool(self._runtime_instance)

    @classmethod
    def get(cls):
        if not cls._runtime_instance.has_started:
            raise RuntimeError('the paste runtime has not been started')

        return cls._runtime_instance

    @classmethod
    def start(cls, env=None):
        if cls._runtime_instance is None:
            cls._runtime_instance = cls(env)

        return cls._runtime_instance