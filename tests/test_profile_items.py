import unittest
import xml.etree.ElementTree as ET

from profile_items import ProfileInventory, extract_items


def fixture(buttons=1, enabled="true", duplicate=False):
    root = ET.Element("hierarchy")
    page = ET.SubElement(root, "node", bounds="[0,0][570,1230]")
    card = ET.SubElement(page, "node", bounds="[28,300][542,700]")
    ET.SubElement(card, "node", {"bounds": "[28,300][542,700]",
                                "content-desc": "Prompt: Test. Answer: A response"})
    if duplicate:
        ET.SubElement(card, "node", {"content-desc": "Prompt: Another. Answer: Different"})
    for _ in range(buttons):
        wrapper = ET.SubElement(card, "node")
        ET.SubElement(wrapper, "node", {"class": "android.widget.Button", "content-desc": "Like prompt",
                                      "bounds": "[461,600][528,667]", "displayed": "true",
                                      "clickable": "true", "enabled": enabled})
    return root, page


class ItemTests(unittest.TestCase):
    def extract(self, root):
        return extract_items(root, "obs-1", {"width": 570, "height": 1230})

    def test_card_scoped_button_and_observation_reference(self):
        root, _ = fixture()
        item = self.extract(root)[0]
        self.assertEqual(item["status"], "control_visible_in_observation")
        self.assertEqual(item["observation_id"], "obs-1")
        self.assertEqual(item["card_bounds"], [28, 300, 542, 700])
        self.assertTrue(item["requires_fresh_resolution"])

    def test_missing_button_does_not_borrow_from_page(self):
        root, page = fixture(buttons=0)
        ET.SubElement(page, "node", {"class": "android.widget.Button", "content-desc": "Like prompt"})
        self.assertEqual(self.extract(root)[0]["status"], "control_not_exposed")

    def test_multiple_buttons_ambiguous(self):
        root, _ = fixture(buttons=2)
        self.assertEqual(self.extract(root)[0]["status"], "ambiguous")

    def test_disabled_button_not_ready(self):
        root, _ = fixture(enabled="false")
        self.assertEqual(self.extract(root)[0]["status"], "control_not_ready")

    def test_multiple_prompts_in_container_rejected(self):
        root, _ = fixture(duplicate=True)
        self.assertEqual(self.extract(root)[0]["status"], "ambiguous")

    def test_duplicate_observations_merge_but_duplicate_cards_fail(self):
        root, _ = fixture()
        items = self.extract(root)
        inventory = ProfileInventory("scan")
        inventory.add(items)
        inventory.add(items)
        self.assertEqual(len(inventory.items), 1)
        self.assertEqual(len(inventory.items[0]["observations"]), 2)
        with self.assertRaises(ValueError):
            inventory.add(items + items)
