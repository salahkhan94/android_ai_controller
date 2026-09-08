import base64
import json
from pathlib import Path
import tempfile
import unittest

from observation import capture_observation

XML = '<hierarchy><node text="Example prompt"><node content-desc="Like" clickable="true"/></node></hierarchy>'
PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=')


class FakeDriver:
    current_package = 'co.hinge.app'
    current_activity = '.Main'

    def __init__(self, second_xml=XML):
        self.sources = iter([XML, second_xml])

    @property
    def page_source(self):
        return next(self.sources)

    def get_window_size(self):
        return {'width': 100, 'height': 200}

    def get_screenshot_as_png(self):
        return PNG


class ObservationTests(unittest.TestCase):
    def test_capture_preserves_evidence_and_relationships(self):
        with tempfile.TemporaryDirectory() as root:
            folder, metadata = capture_observation(FakeDriver(), root, 'co.hinge.app')
            self.assertEqual(metadata['consistency'], 'unchanged')
            self.assertEqual(metadata['screenshot_size'], {'width': 1, 'height': 1})
            self.assertEqual((folder / 'screen.png').read_bytes(), PNG)
            self.assertEqual((folder / 'hierarchy.xml').read_text(), XML)
            nodes = json.loads((folder / 'nodes.json').read_text())
            self.assertEqual(nodes[2]['parent_id'], nodes[1]['id'])
            self.assertTrue((folder / 'metadata.json').exists())

    def test_changed_hierarchy_is_flagged(self):
        with tempfile.TemporaryDirectory() as root:
            folder, metadata = capture_observation(FakeDriver('<hierarchy/>'), root, 'co.hinge.app')
            self.assertEqual(metadata['consistency'], 'changed')
            self.assertEqual((folder / 'hierarchy_after.xml').read_text(), '<hierarchy/>')

    def test_wrong_app_produces_no_artifacts(self):
        with tempfile.TemporaryDirectory() as root:
            with self.assertRaises(RuntimeError):
                capture_observation(FakeDriver(), root, 'another.app')
            self.assertEqual(list(Path(root).iterdir()), [])


if __name__ == '__main__':
    unittest.main()
