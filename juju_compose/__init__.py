#!/usr/bin/env python
# -*- coding: utf-8 -*-
import argparse
import json
import logging
import os
import sys

import blessings
from collections import OrderedDict
from .path import path
import tactics
from .config import ComposerConfig
from bundletester import fetchers
import utils

log = logging.getLogger("composer")


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


class InterfaceFetcher(fetchers.LocalFetcher):
    @classmethod
    def can_fetch(cls, url):
        # Search local path first, then
        # the interface webservice
        if url.startswith("interface:"):
            url = url[10:]
            search_path = [path(os.getcwd()) / "interfaces",
                           os.environ.get("JUJU_REPOSITORY", ".")]
            cp = os.environ.get("INTERFACE_PATH")
            if cp:
                search_path.extend(cp.split(os.pathsep))
            for part in search_path:
                p = (path(part) / url).normpath()
                if p.exists():
                    return dict(path=p)

            # XXX: Attempt to use a real WS
            return fetchers.GithubFetcher.can_fetch(url)
        return {}

    def fetch(self, dir_):
        if hasattr(self, "path"):
            return super(InterfaceFetcher, self).fetch(dir_)
        elif hasattr(self, "repo"):
            # use the github fetcher for now
            u = self.url[10:]
            f = fetchers.get_fetcher(u)
            if hasattr(f, "repo"):
                basename = path(f.repo).name.splitext()[0]
            else:
                basename = u
            res = f.fetch(dir_)
            target = dir_ / basename
            if res != target:
                target.rmtree_p()
                path(res).rename(target)
            return target


fetchers.FETCHERS.insert(0, InterfaceFetcher)


class Configable(object):
    CONFIG_FILE = None
    CONFIG_KLASS = ComposerConfig

    def __init__(self):
        self._config = self.CONFIG_KLASS()
        self.config_file = None

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


class Interface(Configable):
    CONFIG_FILE = "interface.yaml"

    def __init__(self, url, target_repo, name=None):
        super(Interface, self).__init__()
        self.url = url
        self.target_repo = target_repo
        self.directory = None
        self._name = name

    def __repr__(self):
        return "<Interface {}:{}>".format(self.url, self.directory)

    @property
    def name(self):
        if self._name:
            return self._name
        if self.url.startswith("interface:"):
            return self.url[10:]
        return self.url

    def fetch(self):
        try:
            fetcher = fetchers.get_fetcher(self.url)
        except fetchers.FetchError:
            # We might be passing a local dir path directly
            # which fetchers don't currently  support
            self.directory = path(self.url)
        else:
            if isinstance(fetcher, fetchers.LocalFetcher) \
                    and not hasattr(fetcher, "repo"):
                self.directory = path(fetcher.path)
            else:
                self.directory = path(fetcher.fetch(self.target_repo))

        if not self.directory.exists():
            raise OSError(
                "Unable to locate {}. "
                "Do you need to set INTERFACE_PATH?".format(
                    self.url))

        self.config_file = self.directory / self.CONFIG_FILE
        self._name = self.config.name
        return self

    def install(self, kind, name):
        """Kind is provides, requires or peer, name is the name in the charm"""
        pass


class Layer(Configable):
    CONFIG_FILE = "composer.yaml"

    def __init__(self, url, target_repo):
        super(Layer, self).__init__()
        self.url = url
        self.target_repo = target_repo
        self.directory = None

    def __repr__(self):
        return "<Layer {}:{}>".format(self.url, self.directory)

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

        if not self.directory.exists():
            raise OSError(
                "Unable to locate {}. "
                "Do you need to set JUJU_REPOSITY or COMPOSER_PATH?".format(
                    self.url))

        self.config_file = self.directory / self.CONFIG_FILE
        return self


class Composer(object):
    """
    Handle the processing of overrides, implements the policy of ComposerConfig
    """
    def __init__(self):
        self.config = ComposerConfig()
        self.force = False

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
        layer = Layer(self.charm, self.deps).fetch()
        if not layer.configured:
            raise ValueError("The top level layer needs a "
                             "valid composer.yaml file")
        # Manually create a layer object for the output
        self.target = Layer(self.name, self.repo)
        self.target.directory = self.target_dir
        return self.fetch_deps(layer)

    def fetch_deps(self, layer):
        results = {"layers": [], "interfaces": []}
        self.fetch_dep(layer, results)
        # results should now be a bottom up list
        # of deps. Using the in order results traversal
        # we can build out our plan for each file in the
        # output layer
        results["layers"].append(layer)
        return results

    def fetch_dep(self, layer, results):
        # Recursively fetch and scan layers
        # This returns a plan for each file in the result
        baselayers = layer.config.get('includes', [])
        if not baselayers:
            # no deps, this is possible for any base
            # but questionable for the target
            return

        if isinstance(baselayers, str):
            baselayers = [baselayers]

        for base in baselayers:
            if base.startswith("interface:"):
                iface = Interface(base, self.deps).fetch()
                results["interfaces"].append(iface)
            else:
                base_layer = Layer(base, self.deps).fetch()
                self.fetch_dep(base_layer, results)
                results["layers"].append(base_layer)

    def build_tactics(self, entry, current, config, output_files):
        # Delegate to the config object, it's rules
        # will produce a tactic
        relname = entry.relpath(current.directory)
        current = current.config.tactic(entry, current, self.target, config)
        existing = output_files.get(relname)
        if existing is not None:
            tactic = current.combine(existing)
        else:
            tactic = current
        output_files[relname] = tactic

    def plan_layers(self, layers, output_files):
        for i, layer in enumerate(layers["layers"]):
            log.info("Processing layer: %s", layer.directory.name)
            # walk the layer, consulting the config
            # and creating an entry
            # later layers in the list might modify
            # the contributions of layers before it
            # (as they act as basec``lasses)
            # actually invoke it
            if i + 1 < len(layers["layers"]):
                config = layers["layers"][i + 1].config
            else:
                config = None
            list(e for e in utils.walk(layer.directory,
                                       self.build_tactics,
                                       current=layer,
                                       config=config,
                                       output_files=output_files))
        plan = [t for t in output_files.values() if t]
        return plan

    def plan_interfaces(self, layers, output_files, plan):
        # Interface includes don't directly map to output files
        # as they are computed in combination with the metadata.yaml
        charm_meta = output_files.get("metadata.yaml")
        if charm_meta:
            meta = charm_meta()
            target_config = layers["layers"][-1].config
            specs = []
            used_interfaces = set()
            for kind in ("provides", "requires", "peer"):
                for k, v in meta.get(kind, {}).items():
                    # ex: ["provides", "db", "mysql"]
                    specs.append([kind, k, v["interface"]])
                    used_interfaces.add(v["interface"])

            for iface in layers["interfaces"]:
                if iface.name not in used_interfaces:
                    # we shouldn't include something the charm doesn't use
                    log.warn("composer.yaml includes {} which isn't "
                             "used in metadata.yaml".format(
                                 iface.name))
                    continue
                for kind, relation_name, interface_name in specs:
                    # COPY phase
                    plan.append(
                        tactics.InterfaceCopy(iface, relation_name,
                                              self.target, target_config)
                    )
                    # Link Phase
                    plan.append(
                        tactics.InterfaceBind(iface, relation_name, kind,
                                              self.target, target_config))
        elif not charm_meta and layers["interfaces"]:
            raise ValueError(
                "Includes interfaces but no metadata.yaml to bind them")

    def formulate_plan(self, layers):
        """Build out a plan for each file in the various composed
        layers, taking into account config at each layer"""
        output_files = OrderedDict()
        self.plan = self.plan_layers(layers, output_files)
        self.plan_interfaces(layers, output_files, self.plan)
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
                    # We use a read (into memory phase to make layer comps
                    # simpler)
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
            log.warn(
                "Added unexpected file, should be in a base layer: %s", f)
        for f in c:
            log.warn(
                "Changed file owned by another layer: %s", f)
        for f in d:
            log.warn(
                "Deleted a file owned by another layer: %s", f)
        if a or c or d:
            if self.force is False:
                log.info(
                    "Continuing with known changes to target layer. "
                    "Changes will be overwritten")
            else:
                raise ValueError(
                    "Unable to continue due to unexpected modifications")

    def __call__(self):
        self.find_or_create_repo()
        self.validate()
        self.generate()


def main(args=None):
    terminal = blessings.Terminal()
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

    clifmt = utils.ColoredFormatter(
        terminal,
        '%(name)s: %(message)s')
    root_logger = logging.getLogger()
    clihandler = logging.StreamHandler(sys.stdout)
    clihandler.setFormatter(clifmt)
    if isinstance(composer.log_level, str):
        composer.log_level = composer.log_level.upper()
    root_logger.setLevel(composer.log_level)
    root_logger.addHandler(clihandler)

    composer()


if __name__ == '__main__':
    main()
