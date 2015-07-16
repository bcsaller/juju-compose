from juju_compose.path import path
from ruamel import yaml
import json
import juju_compose
import logging
import os
import pkg_resources
import responses
import unittest

class TestCompose(unittest.TestCase):
    def setUp(self):
        dirname = pkg_resources.resource_filename(__name__, ".")
        os.environ["COMPOSER_PATH"] = path(dirname)
        os.environ["INTERFACE_PATH"] = path(dirname) / "interfaces"
        path("out").rmtree_p()

    def tearDown(self):
        path("out").rmtree_p()

    def test_tester_compose(self):
        """Integration suite"""
        composer = juju_compose.Composer()
        composer.log_level = "WARNING"
        composer.output_dir = "out"
        composer.series = "trusty"
        composer.name = "foo"
        composer.charm = "tests/trusty/tester"
        composer()
        base = path('out/trusty/foo')
        self.assertTrue(base.exists())

        # Verify ignore rules applied
        self.assertFalse((base / ".bzr").exists())

        # Metadata should have combined provides fields
        metadata = base / "metadata.yaml"
        self.assertTrue(metadata.exists())
        metadata_data = yaml.load(metadata.open())
        self.assertIn("shared-db", metadata_data['provides'])
        self.assertIn("storage", metadata_data['provides'])

        # Config should have keys but not the ones in deletes
        config = base / "config.yaml"
        self.assertTrue(config.exists())
        config_data = yaml.load(config.open())['options']
        self.assertIn("bind-address", config_data)
        self.assertNotIn("vip", config_data)

        cyaml = base / "composer.yaml"
        self.assertTrue(cyaml.exists())
        cyaml_data = yaml.load(cyaml.open())
        self.assertEquals(cyaml_data['includes'], ['trusty/mysql'])
        self.assertEquals(cyaml_data['is'], 'trusty/tester')

        self.assertTrue((base / "hooks/config-changed").exists())

        # Files from the top layer as overrides
        start = base / "hooks/start"
        self.assertTrue(start.exists())
        self.assertIn("Overridden", start.text())

        self.assertTrue((base / "README.md").exists())
        self.assertEqual("dynamic tactics", (base / "README.md").text())

        sigs = base / ".composer.manifest"
        self.assertTrue(sigs.exists())
        data = json.load(sigs.open())
        self.assertEquals(data['signatures']["README.md"], [
            u'trusty/tester',
            "static",
            u'cfac20374288c097975e9f25a0d7c81783acdbc8124302ff4a731a4aea10de99'])

        self.assertEquals(data["signatures"]['metadata.yaml'], [
            u'trusty/tester',
            "dynamic",
            u'ecb80da834070599ac81190e78448440b442d4eda9cea2e4af3a1db58e60e400'])

    def test_regenerate_inplace(self):
        # take a generated example where a base layer has changed
        # regenerate in place
        # make some assertions
        composer = juju_compose.Composer()
        composer.log_level = "WARNING"
        composer.output_dir = "out"
        composer.series = "trusty"
        composer.name = "foo"
        composer.charm = "tests/trusty/b"
        composer()
        base = path('out/trusty/foo')
        self.assertTrue(base.exists())

        # verify the 1st gen worked
        self.assertTrue((base / "a").exists())
        self.assertTrue((base / "README.md").exists())

        # now regenerate from the target
        composer = juju_compose.Composer()
        composer.log_level = "WARNING"
        composer.output_dir = "out"
        composer.series = "trusty"
        # The generate target and source are now the same
        composer.name = "foo"
        composer.charm = "out/trusty/foo"
        composer()
        base = path('out/trusty/foo')
        self.assertTrue(base.exists())

        # Check that the generated composer makes sense
        cy = base / "composer.yaml"
        config = yaml.load(cy.open())
        self.assertEquals(config["includes"], ["trusty/a", "interface:mysql"])
        self.assertEquals(config["is"], "trusty/b")

        # We can even run it more than once
        composer()
        cy = base / "composer.yaml"
        config = yaml.load(cy.open())
        self.assertEquals(config["includes"], ["trusty/a", "interface:mysql"])
        self.assertEquals(config["is"], "trusty/b")

        # We included an interface, we should be able to assert things about it
        # in its final form as well
        provides = base / "hooks/relations/mysql/provides.py"
        requires = base / "hooks/relations/mysql/requires.py"
        self.assertTrue(provides.exists())
        self.assertTrue(requires.exists())

        # and that we generated the hooks themselves
        for kind in ["joined", "changed", "broken", "departed"]:
            self.assertTrue((base / "hooks" /
                             "mysql-relation-{}".format(kind)).exists())

        # and ensure we have an init file (the interface doesn't its added)
        init = base / "hooks/relations/mysql/__init__.py"
        self.assertTrue(init.exists())


    @responses.activate
    def test_remote_interface(self):
        responses.add(responses.GET, "http://localhost:8888/api/v1/interface/pgsql",
                body='''{
                      "id": "pgsql",
                      "name": "pgsql4",
                      "repo": "https://github.com/bcsaller/juju-relation-pgsql.git",
                      "_id": {
                          "$oid": "55a471959c1d246feae487e5"
                      },
                      "version": 1
                      }''',
                  content_type="application/json")
        composer = juju_compose.Composer()
        composer.log_level = "WARNING"
        composer.output_dir = "out"
        composer.series = "trusty"
        composer.name = "foo"
        composer.charm = "tests/trusty/c-reactive"
        composer()
        base = path('out/trusty/foo')
        self.assertTrue(base.exists())

        # basics
        self.assertTrue((base / "a").exists())
        self.assertTrue((base / "README.md").exists())
        # show that we pulled the interface from github
        init = base / "hooks/relations/pgsql/__init__.py"
        self.assertTrue(init.exists())
        main = base / "hooks/reactive/main.py"
        self.assertTrue(main.exists())


if __name__ == '__main__':
    logging.basicConfig()
    unittest.main()
