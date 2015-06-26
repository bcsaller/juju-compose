import logging
import json
import yaml

from .path import path
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

    def __init__(self, entity, current, target, config):
        self.entity = entity
        self._current = current
        self._target = target
        self._raw_data = None
        self._config = config
        self.warnings = []

    def __call__(self):
        raise NotImplementedError

    def __str__(self):
        return "{}: {} -> {}".format(
            self.__class__.__name__, self.entity, self.target_file)

    @property
    def current(self):
        """The file in the current layer under consideration"""
        return self._current

    @property
    def target(self):
        """The target (final) layer."""
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
    def config(self):
        # Return the config of the layer *above* you
        # as that is the one that controls your compositing
        return self._config

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

    def lint(self):
        pass

    def read(self):
        return None


class CopyTactic(Tactic):
    def __call__(self):
        target = self.target_file
        logging.debug("Copying %s: %s", self.layer_name, target)
        # Ensure the path exists
        target.dirname().makedirs_p()
        if self.entity.isdir():
            return
        if (self.entity != target) and not target.exists() or not self.entity.samefile(target):
            data = self.read()
            if data:
                target.write_bytes(data)
                self.entity.copymode(target)
            else:
                self.entity.copy2(target)

    def __str__(self):
        return "Copy {}".format(self.entity)

    @classmethod
    def trigger(cls, relpath):
        return True


class InterfaceCopy(Tactic):
    def __init__(self, interface, relation_name, target, config):
        self.interface = interface
        self.relation_name = relation_name
        self._target = target
        self._config = config

    def __call__(self):
        # copy the entire tree into the
        # hooks/relations/<interface>
        # directory
        target = self.target / "hooks/relations" / self.interface.name
        logging.debug("Copying Interface %s: %s", self.interface.name, target)
        # Ensure the path exists
        if target.exists():
            # XXX: fix this to do actual updates
            return
        self.interface.directory.copytree(target)

    def __str__(self):
        return "Copy Interface {}".format(self.interface.name)

    def sign(self):
        """return sign in the form {relpath: (origin layer, SHA256)}
        """
        sigs = {}
        for entry, sig in utils.walk(self.interface.directory, utils.sign, kind="files"):
            sigs[entry] = (self.interface.url, "static", sig)
        return sigs


class InterfaceBind(InterfaceCopy):
    def __init__(self, interface, relation_name, kind, target, config):
        self.interface = interface
        self.relation_name = relation_name
        self.kind = kind
        self._target = target
        self._config = config

    DEFAULT_BINDING = """#!/usr/bin/env python
import os
from charmhelpers.core.reactive import main
main('{}')
"""

    def __call__(self):
        for hook in ['joined', 'changed', 'broken', 'departed']:
            target = self.target / "hooks" / "{}-relation-{}".format(
                self.relation_name, hook)
            target.write_text(self.DEFAULT_BINDING.format(self.relation_name))
            target.chmod(0755)

    def __str__(self):
        return "BInd Interface {}".format(self.interface.name)



class ManifestTactic(Tactic):
    @classmethod
    def trigger(cls, relpath):
        return relpath == ".composer.manifest"

    def __call__(self):
        # Don't copy manifests, they are regenerated
        pass


class SerializedTactic(Tactic):
    kind = "dynamic"

    def __init__(self, *args, **kwargs):
        super(SerializedTactic, self).__init__(*args, **kwargs)
        self.data = None

    def combine(self, existing):
        # Invoke the previous tactic
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
        return data

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
    def read(self):
        self._raw_data = self.load(self.entity.open())

    def __call__(self):
        # rewrite includes to be the current source
        data = self._raw_data
        if data is None:
            return
        # The split should result in the series/charm path only
        # XXX: there will be strange interactions with cs: vs local:
        data['is'] = ["/".join(self.current.directory.splitall()[-2:])]
        inc = data.get('includes', [])
        norm = []
        for i in inc:
            if ":" in i:
                norm.append(i)
            else:
                # Attempt to normalize to a repository base
                norm.append("/".join(path(i).splitall()[-2:]))
        if norm:
            data['includes'] = norm
        self.dump(data)
        return data

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
    ManifestTactic,
    MetadataYAML,
    ConfigYAML,
    HookTactic,
    ActionTactic,
    ComposerYAML,
    CopyTactic
]
