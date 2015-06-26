from .tactics import DEFAULT_TACTICS, load_tactic

import fnmatch

import yaml


class ComposerConfig(dict):
    """Defaults for controlling the generator, each layer in
    the inclusion graph can provide values, including things
    like overrides, or warnings if things are overridden that
    shouldn't be.
    """
    def __init__(self, *args, **kwargs):
        super(ComposerConfig, self).__init__(*args, **kwargs)
        self._tactics = []

    def __getattr__(self, key):
        return self[key]

    def configure(self, config_file):
        data = yaml.load(config_file.open())
        if data:
            self.update(data)
        self.validate()
        # look at any possible imports and use them to build tactics
        tactics = self.get('tactics')
        basedir = config_file.dirname()
        if tactics:
            for name in tactics:
                tactic = load_tactic(name, basedir)
                self._tactics.append(tactic)
        return self

    def configured(self):
        return bool(len(self) > 0)

    def validate(self):
        return True

    def tactics(self):
        # XXX: combine from config layer
        return self._tactics + DEFAULT_TACTICS[:]

    def tactic(self, entity, current, target, next_config):
        # There are very few special file types we do anything with
        # metadata.yaml
        # config.yaml
        # hooks
        # actions
        # XXX: resources.yaml
        # anything else
        bd = current.directory
        # Ignore handling
        if next_config:
            ignores = next_config.get('ignore')
            if ignores:
                for ignore in ignores:
                    if fnmatch.fnmatch(entity.relpath(bd), ignore):
                        return None

        for tactic in self.tactics():
            if tactic.trigger(entity.relpath(bd)):
                return tactic(target=target, entity=entity,
                              current=current, config=next_config)
        return None


class InterfaceConfig(dict):
    def __init__(self, *args, **kwargs):
        super(InterfaceConfig, self).__init__(*args, **kwargs)
        self._tactics = []

    def __getattr__(self, key):
        return self[key]

    def configure(self, config_file):
        data = yaml.load(config_file.open())
        if data:
            self.update(data)
        self.validate()
        # look at any possible imports and use them to build tactics
        tactics = self.get('tactics')
        basedir = config_file.dirname()
        if tactics:
            for name in tactics:
                tactic = load_tactic(name, basedir)
                self._tactics.append(tactic)
        return self

    def configured(self):
        return bool(len(self) > 0)

    def validate(self):
        return True

    def tactics(self):
        # XXX: combine from config layer
        return self._tactics + DEFAULT_TACTICS[:]

    def tactic(self, iface, relation_name, target, next_config):
        """Interfaces produce tactics using the metadata.yaml
        and their interface repo"""
        bd = iface.directory

        for tactic in self.tactics():
            if tactic.trigger(entity.relpath(bd)):
                return tactic(target=target, entity=entity,
                              current=current, config=next_config)
        return None
