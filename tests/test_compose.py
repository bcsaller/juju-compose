from juju_compose.path import path
import json
import logging
import os
import unittest
import juju_compose
import yaml

import pkg_resources


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
        self.assertEquals(cyaml_data['is'], ['trusty/tester'])

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
        self.assertEquals(data["README.md"], [
            u'tester',
            "static",
            u'cfac20374288c097975e9f25a0d7c81783acdbc8124302ff4a731a4aea10de99'])

        self.assertEquals(data['metadata.yaml'], [
            u'tester',
            "dynamic",
            u'60a517b47b001b4ac63048576148c3487f7c3a9ce70322f756218c3ca337275d'])

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
        config  = yaml.load(cy.open())
        self.assertEquals(config["includes"], ["trusty/a", "interface:mysql"])
        self.assertEquals(config["is"], ["trusty/foo"])

        # We can even run it more than once
        composer()
        cy = base / "composer.yaml"
        config  = yaml.load(cy.open())
        self.assertEquals(config["includes"], ["trusty/a", "interface:mysql"])
        self.assertEquals(config["is"], ["trusty/foo"])



if __name__ == '__main__':
    logging.basicConfig()
    unittest.main()
