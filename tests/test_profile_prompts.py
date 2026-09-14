"""Test XML prompt parsing and profile traversal with a simulated driver.

Cover response preservation, deduplication, unexpected formats, bounded
scrolling, profile changes, return-to-top continuity, and viewport-only mode.
Simulated swipes and mocked waits keep these tests independent of an emulator.
"""

import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

from print_profile_prompts import Prompt, collect_prompts, extract_prompts


def screen(*descriptions, name="Example"):
    root = ET.Element("hierarchy", {"package": "co.hinge.app"})
    ET.SubElement(root, "node", {"package": "co.hinge.app", "class": "android.widget.Button", "content-desc": f"Skip {name}"})
    for description in descriptions:
        ET.SubElement(root, "node", {"package": "co.hinge.app", "content-desc": description})
    return ET.tostring(root, encoding="unicode")


class Driver:
    current_package = "co.hinge.app"

    def __init__(self, screens):
        self.screens = iter(screens)
        self.page_source = next(self.screens)
        self.directions = []

    def get_window_size(self):
        return {"width": 570, "height": 1230}

    def execute_script(self, command, args):
        assert command == "mobile: swipeGesture"
        self.directions.append(args["direction"])
        self.page_source = next(self.screens)


class PromptTests(unittest.TestCase):
    def test_parsing_preserves_answer_and_deduplicates(self):
        desc = "Prompt: My ideal day. Answer: Coffee & books. Answer: still coffee!"
        root = ET.fromstring(screen(desc, desc, "Like prompt"))
        self.assertEqual(extract_prompts(root), [Prompt("My ideal day", "Coffee & books. Answer: still coffee!")])

    def test_unknown_format_fails_explicitly(self):
        with self.assertRaises(ValueError):
            extract_prompts(ET.fromstring(screen("Prompt: Missing answer. Answer: ")))

    def test_title_only_card_is_skipped_and_written_prompt_retained(self):
        root = ET.fromstring(screen('Prompt: Which do we have in common',
                                   'Prompt: A. Answer: One'))
        self.assertEqual(extract_prompts(root), [Prompt('A', 'One')])
        from profile_items import extract_items
        self.assertEqual([i['title'] for i in extract_items(root, 'test',
                          {'width': 570, 'height': 1230})], ['A'])

    @patch("print_profile_prompts.time.sleep")
    def test_scans_from_top_includes_bottom_and_deduplicates(self, sleep):
        a = "Prompt: A. Answer: One"
        b = "Prompt: B. Answer: Two"
        driver = Driver([screen(b), screen(a), screen(a), screen(a, b), screen(b), screen(b)])
        self.assertEqual(collect_prompts(driver), [Prompt("A", "One"), Prompt("B", "Two")])
        self.assertEqual(driver.directions, ["down", "down", "up", "up", "up"])

    @patch("print_profile_prompts.time.sleep")
    def test_profile_change_stops_scan(self, sleep):
        driver = Driver([screen(), screen(name="Different")])
        with self.assertRaisesRegex(RuntimeError, "Profile changed"):
            collect_prompts(driver)

    @patch("print_profile_prompts.time.sleep")
    def test_scroll_limit_does_not_report_completion(self, sleep):
        driver = Driver([screen(), screen("Prompt: A. Answer: One")])
        with self.assertRaisesRegex(RuntimeError, "Scroll limit"):
            collect_prompts(driver, max_scrolls=1)

    def test_visible_only_never_scrolls(self):
        driver = Driver([screen("Prompt: A. Answer: One")])
        self.assertEqual(collect_prompts(driver, visible_only=True), [Prompt("A", "One")])
        self.assertEqual(driver.directions, [])

    @patch("print_profile_prompts.time.sleep")
    def test_return_check_detects_same_name_content_change(self, sleep):
        a = screen("Prompt: A. Answer: Original")
        b = screen("Prompt: A. Answer: Changed")
        driver = Driver([a, a, a, b, b])
        with self.assertRaisesRegex(RuntimeError, "top content changed"):
            collect_prompts(driver, verify_return=True)

    @patch("print_profile_prompts.time.sleep")
    def test_return_check_ignores_collapsible_filter_controls(self, sleep):
        a = screen("Prompt: A. Answer: Original")
        b = screen("Prompt: A. Answer: Original", "Dating Preferences", "Age filter options")
        driver = Driver([a, a, a, b, b])
        self.assertEqual(collect_prompts(driver, verify_return=True), [Prompt("A", "Original")])

    @patch("print_profile_prompts.time.sleep")
    def test_transient_signals_banner_is_not_profile_content(self, sleep):
        plain = screen("Prompt: A. Answer: Original")
        root = ET.fromstring(plain)
        ET.SubElement(root, 'node', {'package': 'co.hinge.app',
                                   'text': 'Example shows thoughtful signals'})
        banner = ET.tostring(root, encoding='unicode')
        driver = Driver([banner, banner, banner, plain, plain])
        observations = []
        self.assertEqual(collect_prompts(driver, verify_return=True,
                         on_observation=observations.append), [Prompt('A', 'Original')])
        self.assertEqual(ET.tostring(observations[-1]), ET.tostring(ET.fromstring(plain)))

    @patch("print_profile_prompts.time.sleep")
    def test_other_text_changes_still_fail_continuity(self, sleep):
        plain = screen('Prompt: A. Answer: Original')
        root = ET.fromstring(plain)
        ET.SubElement(root, 'node', {'package': 'co.hinge.app', 'text': 'A personal detail'})
        changed = ET.tostring(root, encoding='unicode')
        driver = Driver([plain, plain, plain, changed, changed])
        with self.assertRaisesRegex(RuntimeError, 'top content changed'):
            collect_prompts(driver, verify_return=True)

class SchedulingCardContinuityTests(unittest.TestCase):
    def test_clipped_fixed_labels_do_not_change_identity(self):
        from print_profile_prompts import profile_content_signature
        root=ET.fromstring(screen('Prompt: A. Answer: One'))
        card=ET.SubElement(root,'node')
        for text in ('Let’s get together','Choose a time'):
            ET.SubElement(card,'node',{'package':'co.hinge.app','text':text})
        before=profile_content_signature(root,'Skip Example')
        for text in ('for','our first date'):
            ET.SubElement(card,'node',{'package':'co.hinge.app','text':text})
        self.assertEqual(before,profile_content_signature(root,'Skip Example'))
        ET.SubElement(root,'node',{'package':'co.hinge.app','text':'our first date'})
        self.assertNotEqual(before,profile_content_signature(root,'Skip Example'))

    def test_changed_prompt_still_invalidates_identity(self):
        from print_profile_prompts import profile_content_signature
        a=ET.fromstring(screen('Prompt: A. Answer: One'))
        b=ET.fromstring(screen('Prompt: A. Answer: Two'))
        self.assertNotEqual(profile_content_signature(a,'Skip Example'),profile_content_signature(b,'Skip Example'))

class VideoBoundaryTests(unittest.TestCase):
    def test_timer_updates_are_not_scroll_movement_but_geometry_is(self):
        from print_profile_prompts import scroll_fingerprint
        a=ET.fromstring(screen('Elapsed time: 17 seconds','Prompt: A. Answer: One'))
        b=ET.fromstring(screen('Elapsed time: 19 seconds','Prompt: A. Answer: One'))
        self.assertEqual(scroll_fingerprint(a),scroll_fingerprint(b))
        b[-1].set('bounds','[0,100][100,200]')
        self.assertNotEqual(scroll_fingerprint(a),scroll_fingerprint(b))
        c=ET.fromstring(screen('Elapsed time: 19 seconds','Prompt: A. Answer: Two'))
        self.assertNotEqual(scroll_fingerprint(a),scroll_fingerprint(c))

class ResizedViewportTests(unittest.TestCase):
    def top(self, y, extra=False):
        root=ET.fromstring(screen(name='Example'))
        region=ET.SubElement(root,'node',{'class':'android.view.View','scrollable':'true','bounds':f'[0,{y}][570,1081]'})
        ET.SubElement(region,'node',{'package':'co.hinge.app','content-desc':"Example's photo",'bounds':f'[28,{y}][542,{y+514}]'})
        ET.SubElement(region,'node',{'package':'co.hinge.app','text':'Shared answer','bounds':f'[28,{y+600}][542,{y+640}]'})
        if extra: ET.SubElement(region,'node',{'package':'co.hinge.app','text':'Extra answer','bounds':f'[28,{y+778}][542,1081]'})
        return root

    def test_banner_resize_compares_shared_region_without_ignoring_real_changes(self):
        from print_profile_prompts import top_content_matches
        a,b=self.top(337),self.top(260,True)
        self.assertTrue(top_content_matches(a,b,'Skip Example'))
        b[-1][1].set('text','Changed answer')
        self.assertFalse(top_content_matches(a,b,'Skip Example'))
