import logging
import json
import yaml

import utils


class Tactic(object):
    """
    Tactics are first considered in the context of the config layer being
    called the config layer will attempt to (using its author provided info)
    create a tactic for a given file. That will later be intersected with any
    later layers to create a final single plan for each element of the output
    charm.

    Callable that will implement some portion of the charm composition
    Subclasses should implement __str__ and __call__ which should take whatever
    actions are needed.
    """
    kind = "static"  # used in signatures

    def __init__(self, entity, layers, index, target):
        self.index = index
        self.entity = entity
        self.layers = layers
        self._target = target
        self.warnings = []

    def __call__(self):
        raise NotImplementedError

    def __str__(self):
        return "{}: {} -> {}".format(
            self.__class__.__name__, self.entity, self.target_file)

    @property
    def source(self):
        """The file in the bottom most layer being processed"""
        return self.layers[max(self.index - 1, 0)]

    @property
    def current(self):
        """The file in the current layer under consideration"""
        return self.layers[self.index]

    @property
    def target(self):
        """The output file path()"""
        return self._target

    @property
    def relpath(self):
        return self.entity.relpath(self.current.directory)

    @property
    def target_file(self):
        target = self.target.directory / self.relpath
        return target

    @property
    def layer_name(self):
        return self.current.directory.name

    @property
    def source_layer(self):
        return self.source.directory.name

    def find(self, name):
        for layer in reversed(self.layers[:self.index]):
            f = layer.directory / name
            if f.exists():
                return f
        return None

    @property
    def config(self):
        # Return the config of the layer *above* you
        # as that is the one that controls your compositing
        if self.index + 1 < len(self.layers):
            return self.layers[self.index + 1].config
        return None

    def combine(self, existing):
        """Produce a tactic informed by the last tactic for an entry.
        This is when a rule in a higher level charm overrode something in
        one of its bases for example."""
        return self

    @classmethod
    def trigger(cls, relpath):
        """Should the rule trigger for a given path object"""
        return False

    def sign(self):
        """return sign in the form {relpath: (origin layer, SHA256)}
        """
        target = self.target_file
        sig = {}
        if target.exists() and target.isfile():
            sig[self.relpath] = (self.layer_name,
                                 self.kind,
                                 utils.sign(self.target_file))
        return sig

    def verify(self):
        pass

    def lint(self):
        pass


class CopyTactic(Tactic):
    def __call__(self):
        target = self.target_file
        logging.debug("Copying %s: %s", self.layer_name, target)
        # Ensure the path exists
        target.dirname().makedirs_p()
        if self.entity.isdir():
            return
        self.entity.copy2(target)

    def __str__(self):
        return "Copy {}".format(self.entity)

    @classmethod
    def trigger(cls, relpath):
        return True


class SerializedTactic(Tactic):
    kind = "dynamic"

    def __init__(self, *args, **kwargs):
        super(SerializedTactic, self).__init__(*args, **kwargs)
        self.data = None

    def combine(self, existing):
        existing()
        if existing.data is not None:
            self.data = existing.data.copy()
        return self

    def __call__(self):
        data = self.load(self.entity.open())
        # self.data represents the product of previous layers
        if self.data:
            data = utils.deepmerge(self.data, data)

        # Now apply any rules from config
        config = self.config
        if config:
            section = config.get(self.section)
            if section:
                dels = section.get('deletes', [])
                if self.prefix:
                    namespace = data[self.prefix]
                else:
                    namespace = data
                for key in dels:
                    utils.delete_path(key, namespace)
        self.data = data
        self.dump(data)

class YAMLTactic(SerializedTactic):
    """Rule Driven YAML generation"""
    prefix = None

    def load(self, fn):
        return yaml.safe_load(fn)

    def dump(self, data):
        yaml.safe_dump(data, self.target_file.open('w'),
                       default_flow_style=False)


class JSONTactic(SerializedTactic):
    """Rule Driven JSON generation"""
    prefix = None

    def load(self, fn):
        return json.load(fn)

    def dump(self, data):
        json.dump(data, self.target_file.open('w'), indent=2)


class ComposerYAML(YAMLTactic):
    def __call__(self):
        # rewrite inherits to be the current source
        data = yaml.load(self.entity.open())
        # The split should result in the series/charm path only
        # XXX: there will be strange interactions with cs: vs local:
        data['is'] = ["/".join(self.current.directory.splitall()[-2:])]
        del data['inherits']
        self.dump(data)

    @classmethod
    def trigger(cls, relpath):
        return relpath == "composer.yaml"


class MetadataYAML(YAMLTactic):
    """Rule Driven metadata.yaml generation"""
    section = "metadata"

    @classmethod
    def trigger(cls, relpath):
        return relpath == "metadata.yaml"


class ConfigYAML(MetadataYAML):
    """Rule driven config.yaml generation"""
    section = "config"
    prefix = "options"

    @classmethod
    def trigger(cls, relpath):
        return relpath == "config.yaml"


class HookTactic(CopyTactic):
    """Rule Generated Hooks"""
    def __str__(self):
        return "Handling Hook {}".format(self.entity)

    @classmethod
    def trigger(cls, relpath):
        return relpath.dirname() == "hooks"


class ActionTactic(HookTactic):
    def __str__(self):
        return "Handling Action {}".format(self.entity)

    @classmethod
    def trigger(cls, relpath):
        return relpath.dirname() == "actions"


def load_tactic(dpath, basedir):
    """Load a tactic from the current layer using a dotted path. The last
    element in the path should be a Tactic subclass
    """
    obj = utils.load_class(dpath, basedir)
    if not issubclass(obj, Tactic):
        raise ValueError("Expected to load a tactic for %s" % dpath)
    return obj


DEFAULT_TACTICS = [
    MetadataYAML,
    ConfigYAML,
    HookTactic,
    ActionTactic,
    ComposerYAML,
    CopyTactic
]
