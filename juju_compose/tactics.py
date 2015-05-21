import logging
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
    def __init__(self, entity, layers, index, target):
        self.index = index
        self.entity = entity
        self.layers = layers
        self._target = target
        self.warnings = []

    def __call__(self):
        raise NotImplementedError

    def __str__(self):
        raise NotImplementedError

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
        return ComposerConfig()

    def combine(self, existing):
        """Produce a tactic informed by the last tactic for an entry.
        This is when a rule in a higher level charm overrode something in
        one of its bases for example."""
        return self

    @classmethod
    def trigger(cls, relpath):
        """Should the rule trigger for a given path object"""
        return False


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


class ComposerYAML(Tactic):
    def __call__(self):
        # rewrite inherits to be the current source
        data = yaml.load(self.entity.open())
        data['inherits'] = ["/".join(self.current.directory.splitall()[-2:])]
        yaml.safe_dump(data, self.target_file.open('w'),
                       default_flow_style=False)


class YAMLTactic(Tactic):
    """Rule Driven YAML generation"""
    prefix = None

    def __call__(self):
        target = self.target_file
        current = yaml.safe_load(self.entity.open())
        meta = (self.current.directory / self.entity.basename())
        basemeta = (self.source.directory / self.entity.basename())
        if basemeta.exists():
            basemeta = yaml.safe_load(basemeta.open())
        if meta.exists():
            existing = yaml.safe_load(meta.open())
        else:
            existing = {}

        if basemeta:
            data = utils.deepmerge(basemeta, existing)
            data = utils.deepmerge(data, current)
        else:
            data = utils.deepmerge(existing, current)

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
                    del namespace[key]
        yaml.safe_dump(data, target.open('w'), default_flow_style=False)


class MetadataYAML(YAMLTactic):
    """Rule Driven metadata.yaml generation"""
    section = "metadata"

    def __str__(self):
        return "Generating metadata.yaml"

    @classmethod
    def trigger(cls, relpath):
        return relpath == "metadata.yaml"


class ConfigYAML(MetadataYAML):
    """Rule driven config.yaml generation"""
    section = "config"
    prefix = "options"

    def __str__(self):
        return "Generating config.yaml"

    @classmethod
    def trigger(cls, relpath):
        return relpath == "config.yaml"


class HookTactic(Tactic):
    """Rule Generated Hooks"""
    def __call__(self):
        target = self.target_file
        target.dirname().makedirs_p()
        if self.entity.isdir():
            return
        if self.entity.ext == ".pre" or self.entity.ext == ".post":
            # we are looking at the entry for a hook wrapper
            # we'll have to look at the lower layer to replace its
            # hook.
            main = self.find(self.relpath.stripext())
            if not main:
                # we couldn't find the hook they want to pre/post
                logging.warn(
                    "Attempt to divert hook %s failed, original missing",
                    self.entity)
                return
            # XXX: This is not smart enough to divert the same hook more than
            # once through multiple layers though that is desirable down the
            # road.
            # create the wrapper
            # divert the main hook
            main.copy2(target.stripext() + "." + self.layer_name)
            self.entity.copy2(target)
            # and write the bash wrapper
            hook = (self.target.directory / self.relpath.stripext())
            hook.write_text("""#!/bin/bash
set -e
[ -e {hook}.pre ] && {hook}.pre
{hook}.{layer}
[ -e {hook}.post ] && {hook}.post
            """.format(hook=self.relpath.stripext(), layer=self.layer_name))
        else:
            self.entity.copy2(target)

    def __str__(self):
        return "Handling Hook {}".format(self.entity)

    @classmethod
    def trigger(cls, relpath):
        return relpath.dirname() == "hooks"


class ActionTactic(HookTactic):
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
    CopyTactic
]
