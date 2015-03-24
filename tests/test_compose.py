from path import path
import logging
import unittest
import juju_compose
import yaml


class TestCompose(unittest.TestCase):
    def setUp(self):
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
        composer.generate()
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

        # XXX: verify contents
        self.assertTrue((base / "hooks/config-changed").exists())
        self.assertTrue((base / "hooks/config-changed.pre").exists())
        self.assertTrue((base / "hooks/config-changed.mysql").exists())

        # Files from the top layer as overrides
        start = base / "hooks/start"
        self.assertTrue(start.exists())
        self.assertIn("Overridden", start.text())

        self.assertTrue((base / "README.md").exists())


if __name__ == '__main__':
    logging.basicConfig()
    unittest.main()
