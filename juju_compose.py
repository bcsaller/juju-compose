#!/usr/bin/env python
# -*- coding: utf-8 -*-
import argparse
import copy
import fnmatch
import logging
import os
import yaml

from collections import OrderedDict
from path import path
from bundletester import fetchers


def deepmerge(dest, src):
    """
    Deep merge of two dicts.

    This is destructive (`dest` is modified), but values
    from `src` are passed through `copy.deepcopy`.
    """
    for k, v in src.iteritems():
        if dest.get(k) and isinstance(v, dict):
            deepmerge(dest[k], v)
        else:
            dest[k] = copy.deepcopy(v)
    return dest


class ComposerConfig(dict):
    """Defaults for controlling the generator, each layer in
    the inheritance graph can provide values, including things
    like overrides, or warnings if things are overridden that
    shouldn't be.
    """
    def __getattr__(self, key):
        return self[key]

    def configure(self, config_file):
        data = yaml.load(config_file.open())
        if data:
            self.update(data)
        self.validate()
        return self

    def configured(self):
        return bool(len(self) > 0)

    def validate(self):
        return True

    def tactic(self, entity, layers, index, target):
        # There are very few special file types we do anything with
        # metadata.yaml
        # config.yaml
        # hooks
        # actions
        # XXX: resources.yaml
        # anything else
        bd = layers[index].directory
        triggers = [
            (entity.relpath(bd) == "metadata.yaml", MetadataYAML),
            (entity.relpath(bd) == "config.yaml", ConfigYAML),
            (entity.relpath(bd) == "composer.yaml", ComposerYAML),
            (entity.dirname().relpath(bd) == "hooks", HookTactic),
            #  (entity.relpath(bd) == "actions.yaml", ActionsYAML),
            (entity.dirname().relpath(bd) == "actions", HookTactic),
            (True, CopyTactic)
        ]

        # Ignore handling
        nextLayer = None
        if index + 1 < len(layers):
            nextLayer = layers[index + 1]
            ignores = nextLayer.config.get('ignore')
            if ignores:
                for ignore in ignores:
                    triggers.insert(0, (
                        fnmatch.fnmatch(
                            entity.relpath(bd), ignore), None))

        for trigger, rule in triggers:
            if trigger is True:
                # The entity matched a trigger, we now need to figure
                # out the ramifications, by asking the rule
                # XXX: passing wrong args currently
                if not rule:
                    return None
                return rule(target=target, entity=entity,
                            layers=layers, index=index)


class Charm(object):
    def __init__(self, url, target_repo):
        self.url = url
        self.target_repo = target_repo
        self.directory = None
        self._config = ComposerConfig()
        self.config_file = None

    def __repr__(self):
        return "<Charm {}:{}>".format(self.url, self.directory)

    def fetch(self):
        fetcher = fetchers.get_fetcher(self.url)
        self.directory = path(fetcher.fetch(self.target_repo))
        metadata = self.directory / "metadata.yaml"
        if not metadata.exists():
            logging.warn("{} has no metadata.yaml, is it a charm".format(
                self.url))
        self.config_file = self.directory / "composer.yaml"
        return self

    @property
    def config(self):
        if self._config.configured():
            return self._config
        if self.config_file and self.config_file.exists():
            self._config.configure(self.config_file)
        return self._config

    @property
    def configured(self):
        return bool(self.config is not None and self.config.configured())


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


class CopyTactic(Tactic):
    def __call__(self):
        rel = self.entity.relpath(self.current.directory)
        target = self.target.directory / rel
        logging.debug("Copying %s:%s to %s", self.layer_name, rel, target)
        # Ensure the path exists
        target.dirname().makedirs_p()
        if self.entity.isdir():
            return
        self.entity.copy2(target)

    def __str__(self):
        return "Copy {}".format(self.entity)


class ComposerYAML(Tactic):
    def __call__(self):
        # rewrite inherits to be the current source
        data = yaml.load(self.entity.open())
        data['inherits'] = ["/".join(self.current.directory.splitall()[-2:])]
        rel = self.entity.relpath(self.current.directory)
        target = self.target.directory / rel
        yaml.safe_dump(data, target.open('w'), default_flow_style=False)


class YAMLTactic(Tactic):
    """Rule Driven YAML generation"""
    prefix = None

    def __call__(self):
        rel = self.entity.relpath(self.current.directory)
        target = self.target.directory / rel
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
            data = deepmerge(basemeta, existing)
            data = deepmerge(data, current)
        else:
            data = deepmerge(existing, current)

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


class ConfigYAML(MetadataYAML):
    """Rule driven config.yaml generation"""
    section = "config"
    prefix = "options"

    def __str__(self):
        return "Generating config.yaml"


class HookTactic(Tactic):
    """Rule Generated Hooks"""
    def __call__(self):
        rel = self.entity.relpath(self.current.directory)
        target = self.target.directory / rel
        target.dirname().makedirs_p()
        if self.entity.isdir():
            return
        if self.entity.ext == ".pre" or self.entity.ext == ".post":
            # we are looking at the entry for a hook wrapper
            # we'll have to look at the lower layer to replace its
            # hook.
            main = self.find(rel.stripext())
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
            hook = (self.target.directory / rel.stripext())
            hook.write_text("""#!/bin/bash
set -e
[ -e {hook}.pre ] && {hook}.pre
{hook}.{layer}
[ -e {hook}.post ] && {hook}.post
            """.format(hook=rel.stripext(), layer=self.layer_name))
        else:
            self.entity.copy2(target)

    def __str__(self):
        return "Handling Hook {}".format(self.entity)


class Composer(object):
    """
    Handle the processing of overrides, implements the policy of ComposerConfig
    """
    def __init__(self):
        self.config = ComposerConfig()

    def configure(self, config_file):
        self.config.configure(config_file)
        self.config.validate()

    def create_repo(self):
        # Generated output will go into this directory
        base = path(self.output_dir)
        self.repo = (base / self.series).makedirs_p()
        # And anything it inherits from will be placed here
        # outside the series
        self.deps = (base / "deps" / self.series).makedirs_p()
        self.target_dir = (self.repo / self.name).mkdir_p()

    def fetch(self):
        charm = Charm(self.charm, self.deps).fetch()
        if not charm.configured:
            raise ValueError("The top level charm needs a "
                             "valid composer.yaml file")
        # Manually create a charm object for the output
        self.target = Charm(self.name, self.repo)
        self.target.directory = self.target_dir
        return self.fetch_deps(charm)

    def fetch_deps(self, charm):
        results = []
        self.fetch_dep(charm, results)
        # results should now be a bottom up list
        # of deps. Using the in order results traversal
        # we can build out our plan for each file in the
        # output charm
        results.append(charm)
        return results

    def fetch_dep(self, charm, results):
        # Recursively fetch and scan charms
        # This returns a plan for each file in the result
        basecharms = charm.config.get('inherits')
        if not basecharms:
            # no deps, this is possible for any base
            # but questionable for the target
            return

        if isinstance(basecharms, str):
            basecharms = [basecharms]

        for base in basecharms:
            base_charm = Charm(base, self.deps).fetch()
            self.fetch_dep(base_charm, results)
            results.append(base_charm)

    def formulate_plan(self, charms):
        """Build out a plan for each file in the various composed
        layers, taking into account config at each layer"""
        output_files = OrderedDict()
        # Add in the last layer so our pairwise walk works
        layers = charms + [self.target]
        for i, charm in enumerate(layers):
            logging.info("Processing charm layer: %s", charm.directory.name)
            # walk the charm, consulting the config
            # and creating an entry
            # later charms in the list might modify
            # the contributions of charms before it
            # (as they act as baseclasses)
            for entry in charm.directory.walk():
                # Delegate to the config object, it's rules
                # will produce a tactic
                relname = entry.relpath(charm.directory)
                logging.debug(relname)
                current = charm.config.tactic(entry, layers, i, self.target)
                existing = output_files.get(relname)
                if existing is not None:
                    tactic = current.combine(existing)
                else:
                    tactic = current
                output_files[relname] = tactic
        return [t for t in output_files.values() if t]

    def generate(self):
        self.create_repo()
        results = self.fetch()
        plan = self.formulate_plan(results)

        # now execute the plan
        for tactic in plan:
            tactic()


def main(args=None):
    composer = Composer()
    parser = argparse.ArgumentParser()
    parser.add_argument('-l', '--log-level', default=logging.INFO)
    parser.add_argument('-f', '--force', action="store_true")
    parser.add_argument('-o', '--output-dir',
                        default=os.environ.get("JUJU_REPOSITORY", "."))
    parser.add_argument('-s', '--series', default="trusty")
    parser.add_argument('name', help="Generate a charm of 'name' from 'charm'")
    parser.add_argument('charm', default=".")
    # Namespace will set the options as attrs of composer
    parser.parse_args(args, namespace=composer)
    logging.basicConfig(level=composer.log_level)
    composer.generate()


if __name__ == '__main__':
    main()
