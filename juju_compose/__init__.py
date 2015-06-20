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

class InterfaceFetcher(fetchers.Fetcher):
    @classmethod
    def can_fetch(cls, url):
        # XXX: local interfaces?
        if url.startswith("interface:"):
            p = url[10:]
            return dict(path=p)
        return {}

    def fetch(self, dir_):
        # We need to fetch to repo.deps and then map the proper endpoint code
        # into the charm based on metadata (which will have to be the final metadtap)
        # resolve the interface uri from a known http endpoint
        pass

fetchers.FETCHERS.insert(0, InterfaceFetcher)

class Interface(object):
    def __init__(self, url, target_repo):
        self.url = url
        self.target_repo = target_repo
        self.directory = None

    def __repr__(self):
        return "<Interface {}:{}>".format(self.url, self.directory)

    def fetch(self):
        pass

    def install(self, kind, name):
        """Kind is provides, requires or peer, name is the name in the charm"""
        pass



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
        # And anything it includes from will be placed here
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
        results = {"charms": [], "interfaces": []}
        self.fetch_dep(charm, results)
        # results should now be a bottom up list
        # of deps. Using the in order results traversal
        # we can build out our plan for each file in the
        # output charm
        results["charms"].append(charm)
        return results

    def fetch_dep(self, charm, results):
        # Recursively fetch and scan charms
        # This returns a plan for each file in the result
        basecharms = charm.config.get('includes', [])
        if not basecharms:
            # no deps, this is possible for any base
            # but questionable for the target
            return

        if isinstance(basecharms, str):
            basecharms = [basecharms]

        for base in basecharms:
            if base.startswith("interface:"):
                iface = Interface(base, self.deps).fetch()
                results["interfaces"].append(iface)
            else:
                base_charm = Charm(base, self.deps).fetch()
                self.fetch_dep(base_charm, results)
                results["charms"].append(base_charm)

    def build_tactics(self, entry, charm, layers, index, output_files):
        # Delegate to the config object, it's rules
        # will produce a tactic
        relname = entry.relpath(charm.directory)
        current = charm.config.tactic(entry, layers, index, self.target)
        existing = output_files.get(relname)
        if existing is not None:
            tactic = current.combine(existing)
        else:
            tactic = current
        print entry, relname, tactic
        output_files[relname] = tactic

    def formulate_plan(self, layers):
        """Build out a plan for each file in the various composed
        layers, taking into account config at each layer"""
        output_files = OrderedDict()
        for i, charm in enumerate(layers["charms"]):
            logging.info("Processing charm layer: %s", charm.directory.name)
            # walk the charm, consulting the config
            # and creating an entry
            # later charms in the list might modify
            # the contributions of charms before it
            # (as they act as basec``lasses)
            # actually invoke it
            list(utils.walk(charm.directory,
                       self.build_tactics,
                       charm=charm,
                       layers=layers["charms"],
                       index=i,
                       output_files=output_files))
        self.plan = [t for t in output_files.values() if t]

        # Interface includes don't directly map to output files
        # as they are computed in combination with the metadata.yaml
        charm_meta = output_files.get("metadata.yaml")
        if charm_meta:
            for iface in layers["interfaces"]:
                iface_tactics = iface.config.tactic(iface, charm_meta, self.target)
                self.plan.extend(iface_tactics)
        elif not charm_meta and layers["interfaces"]:
            raise ValueError("Includes interfaces but no metadata.yaml to bind them")

        if self.log_level == "DEBUG":
            self.dump(layers)
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
        layers = self.fetch()
        self.formulate_plan(layers)
        self.exec_plan()

    def validate(self):
        p = self.target_dir / ".composer.manifest"
        if not p.exists():
            return
        a, c, d = utils.delta_signatures(p)
        for f in a:
            logging.warn("Added unexpected file, should be in a base layer: %s", f)
        for f in c:
            logging.warn("Changed file owned by another layer: %s", f)
        for f in d:
            logging.warn("Deleted a file owned by another layer: %s", f)
        if a or c or d:
            if self.force:
                logging.info("Continuing with known changes to target layer.  Changes will be overwritten")
            else:
                raise ValueError("Unable to continue due to unexpected modifications")


    def dump(self, layers):
        print "REPO:", self.charm, self.target_dir
        print "Layers:"
        for l in layers["charms"]:
            print "\t", l
        print "Interfaces:"
        for i in layers["interfaces"]:
            print "\t", i
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
