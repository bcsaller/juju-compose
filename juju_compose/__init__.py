#!/usr/bin/env python
# -*- coding: utf-8 -*-
import argparse
import logging
import os

from collections import OrderedDict
from .path import path
from .config import ComposerConfig
from bundletester import fetchers


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
        if isinstance(fetcher, fetchers.LocalFetcher):
            self.directory = path(fetcher.path)
        else:
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
