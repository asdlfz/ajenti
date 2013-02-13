import imp
import os
import logging
import traceback
import subprocess
import sys

from ajenti.util import *


@public
class PluginLoadError (Exception):
    pass


@public
class PluginCrashed (PluginLoadError):
    def __init__(self, e):
        self.e = e
        self.traceback = traceback.format_exc()

    def __str__(self):
        return 'crashed: %s' % self.e


@public
class Dependency (object):
    class Unsatisfied (PluginLoadError):
        def __init__(self):
            PluginLoadError.__init__(self, None)
            self.dependency = None

        def reason(self):
            pass

        def __str__(self):
            return '%s (%s)' % (self.dependency.__class__, self.reason())

    def satisfied(self):
        return False

    def build_exception(self):
        exception = self.Unsatisfied()
        exception.dependency = self
        return exception

    def check(self):
        if not self.satisfied():
            exception = self.build_exception()
            raise exception


@public
class ModuleDependency (Dependency):
    description = 'Python module'

    class Unsatisfied (Dependency.Unsatisfied):
        def reason(self):
            return '%s' % self.dependency.module_name

    def __init__(self, module_name):
        self.module_name = module_name

    def satisfied(self):
        if self.module_name in sys.modules:
            return True
        try:
            __import__(self.module_name)
            return True
        except:
            return False

    def __str__(self):
        return self.module_name


@public
class PluginDependency (Dependency):
    description = 'Plugin'

    class Unsatisfied (Dependency.Unsatisfied):
        def reason(self):
            return '%s' % self.dependency.plugin_name

    def __init__(self, plugin_name):
        self.plugin_name = plugin_name

    def satisfied(self):
        # get_order() only contains successfully loaded plugins
        return self.plugin_name in manager.get_order()

    def __str__(self):
        return self.plugin_name


@public
class BinaryDependency (Dependency):
    description = 'Application binary'

    class Unsatisfied (Dependency.Unsatisfied):
        def reason(self):
            return '%s' % self.dependency.binary_name

    def __init__(self, binary_name):
        self.binary_name = binary_name

    def satisfied(self):
        return subprocess.call(['which', self.binary_name]) == 0

    def __str__(self):
        return self.binary_name


@public
class PluginManager:
    """
    Handles plugin loading and unloading
    """

    __classes = {}
    __plugins = {}
    __order = []
    __instances = {}

    def register_interface(self, iface):
        setattr(iface, '__ajenti_interface', True)

    def register_implementation(self, impl):
        impl._implements = []
        for cls in impl.mro():
            if hasattr(cls, '__ajenti_interface'):
                self.__classes.setdefault(cls, []).append(impl)
                impl._implements.append(cls)

    def get_implementations(self, iface):
        return self.__classes.setdefault(iface, [])

    def get_instances(self, cls):
        return self.__instances.setdefault(cls, [])

    def get_instance(self, cls):
        if not cls in self.__instances:
            return self.instantiate(cls)
        return self.__instances[cls][0]

    def instantiate(self, cls, *args, **kwargs):
        instance = cls(*args, **kwargs)
        last_init = None
        for base in reversed(cls.mro()):
            if hasattr(base, 'init'):
                init = getattr(base, 'init')
                if init != last_init:
                    init(instance)
                    last_init = init

        for iface in cls._implements + [cls]:
            self.__instances.setdefault(iface, []).append(instance)

        return instance

    # Plugin loader
    def get_all(self):
        return self.__plugins

    def get_order(self):
        return self.__order

    def load_all(self):
        path = os.path.split(__file__)[0]
        for item in os.listdir(path):
            if not '.' in item:
                if not item in self.__plugins:
                    self.load_recursive(item)

    def get_plugins_root(self):
        return os.path.split(__file__)[0]

    def resolve_path(self, name):
        path = os.path.join(self.get_plugins_root(), name)
        if os.path.exists(path):
            return path
        return None

    def load_recursive(self, name):
        while True:
            try:
                self.load(name)
                return
            except PluginDependency.Unsatisfied, e:
                if e.dependency.plugin_name in manager.get_all():
                    if manager.get_all()[e.dependency.plugin_name].crash:
                        manager.get_all()[name].crash = e
                        logging.warn(' *** Plugin dependency unsatisfied: %s -> %s' % \
                            (name, e.dependency.plugin_name))
                        return
                try:
                    logging.debug('Preloading plugin dependency: %s' % e.dependency.plugin_name)
                    self.load_recursive(e.dependency.plugin_name)
                except:
                    raise

    def load(self, name):
        """
        Loads given plugin
        """
        logging.debug('Loading plugin %s' % name)
        try:
            try:
                mod = imp.load_module('ajenti.plugins.%s' % name, *imp.find_module(name, [self.get_plugins_root()]))
                logging.debug('  == %s ' % mod.info.title)
            except Exception, e:
                # TOTAL CRASH
                from ajenti.api import PluginInfo
                info = PluginInfo(name=name, crash=e)
                self.__plugins[name] = info
                raise PluginCrashed(e)

            info = mod.info
            info.module = mod
            info.active = False
            info.name = name
            info.crash = None
            if hasattr(mod, 'init'):
                info.init = mod.init
            self.__plugins[name] = info

            for dependency in info.dependencies:
                dependency.check()
            info.active = True

            try:
                info.init()
            except Exception, e:
                raise PluginCrashed(e)

            if name in self.__order:
                self.__order.remove(name)
            self.__order.append(name)
        except PluginDependency.Unsatisfied, e:
            raise
        except PluginCrashed, e:
            logging.warn(' *** Plugin crashed: %s' % e)
            print e.traceback
            info.crash = e
        except PluginLoadError, e:
            logging.warn(' *** Plugin failed to load: %s' % e)
            info.crash = e


manager = PluginManager()
