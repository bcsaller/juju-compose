#!/usr/bin/env python
# -*- coding: utf-8 -*-
import argparse
import json
import logging
import os

from collections import OrderedDict
from .path import path
from .config import ComposerConfig
from bundletester import fetchers
import utils


class RepoFetcher(fetchers.LocalFetcher):
    @classmethod
    def can_fetch(cls, url):
        search_path = [os.getcwd(), os.environ.get("JUJU_REPOSITORY", ".")]
        cp = os.environ.get("COMPOSER_PATH")
        if cp:
            search_path.extend(cp.split(":"))

        for part in search_path:
            p = (path(part) / url).normpath()
            if p.exists():
                return dict(path=p)
        return {}

fetchers.FETCHERS.insert(0, RepoFetcher)


class Charm(object):
    def __init__(self, url, target_repo):
        self.url = url
        self.target_repo = target_repo
        self.directory = None
        self._config = ComposerConfig()
        self.config_file = None

    def __repr__(self):
        return "<Charm {}:{}>".format(self.url, self.directory)

    def __div__(self, other):
        return self.directory / other

    def fetch(self):
        try:
            fetcher = fetchers.get_fetcher(self.url)
        except fetchers.FetchError:
            # We might be passing a local dir path directly
            # which fetchers don't currently  support
            self.directory = path(self.url)
        else:
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
        self.charm = path(self.charm)
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

    def find_or_create_repo(self):
        # see if output dir is already in a repo, we can use that directly
        if self.output_dir == path(self.charm).normpath():
            # we've indicated in the cmdline that we are doing an inplace
            # update
            if self.output_dir.parent.basename() == self.series:
                # we're already in a repo
                self.repo = self.output_dir.parent.parent
                self.deps = (self.repo / "deps" / self.series).makedirs_p()
                self.target_dir = self.output_dir
                return
        self.create_repo()


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
        for i, charm in enumerate(charms):
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
                current = charm.config.tactic(entry, charms, i, self.target)
                existing = output_files.get(relname)
                if existing is not None:
                    tactic = current.combine(existing)
                else:
                    tactic = current
                output_files[relname] = tactic
        self.plan = [t for t in output_files.values() if t]
        if self.log_level == "DEBUG":
            self.dump(charms)
        return self.plan

    def exec_plan(self, plan=None):
        if not plan:
            plan = self.plan
        signatures = {}
        for phase in ['lint', 'read', '__call__', 'sign']:
            for tactic in plan:
                if phase == "lint":
                    tactic.lint()
                elif phase == "read":
                    # We use a read (into memory :-/ phase to make inplace simpler)
                    tactic.read()
                elif phase == "__call__":
                    tactic()
                elif phase == "sign":
                    sig = tactic.sign()
                    if sig:
                        signatures.update(sig)
        # write out the sigs
        sigs = self.target / ".composer.manifest"
        signatures['.composer.manifest'] = ["composer", 'dynamic', 'unchecked']
        sigs.write_text(json.dumps(signatures, indent=2))

    def generate(self):
        results = self.fetch()
        self.formulate_plan(results)
        self.exec_plan()

    def validate(self):
        p = self.target_dir / ".composer.manifest"
        if not p.exists():
            return
        a, c, d = utils.delta_signatures(p)
        for f in a:
            logging.warn("Added unepxected file, should be in a base layer: %s", f)
        for f in c:
            logging.warn("Changed file owned by another layer: %s", f)
        for f in d:
            logging.warn("Deleted a file owned by another layer: %s", f)
        if a or c or d:
            if self.force:
                logging.info("Continuing with known changes to target layer.  Changes will be overwritten")
            else:
                raise ValueError("Unable to continue due to unexpected modifications")


    def dump(self, charms):
        print "REPO:", self.charm, self.target_dir
        print "Charms:"
        for c in charms:
            print "\t", c
        print "Plan:"
        for p in self.plan:
            print "\t", p

    def __call__(self):
        self.find_or_create_repo()
        self.validate()
        self.generate()


def main(args=None):
    composer = Composer()
    parser = argparse.ArgumentParser()
    parser.add_argument('-l', '--log-level', default=logging.INFO)
    parser.add_argument('-f', '--force', action="store_true")
    parser.add_argument('-o', '--output-dir')
    parser.add_argument('-s', '--series', default="trusty")
    parser.add_argument('-n', '--name',
                        default=path(os.getcwd).dirname(),
                        help="Generate a charm of 'name' from 'charm'")
    parser.add_argument('charm', default=".", type=path)
    # Namespace will set the options as attrs of composer
    parser.parse_args(args, namespace=composer)
    if not composer.name:
        composer.name = path(composer.charm).normpath().basename()
    if not composer.output_dir:
        composer.output_dir = path(composer.charm).normpath()

    logging.basicConfig(level=composer.log_level)

    composer()


if __name__ == '__main__':
    main()
