"""Test Phase 4 provenance checks and fresh UI resolution using synthetic data.

These tests never connect to Hinge or submit a like. Composer checks are tested
against synthetic XML shaped like the inspected application UI.
"""

import hashlib
import json
from pathlib import Path
import tempfile
import unittest
import xml.etree.ElementTree as ET
from unittest.mock import Mock, patch

from prepare_comment import composer_field, dismiss_keyboard, fill_composer, load_selection, node_xpath, prepare, read_editing_composer, read_unobscured_profile, reveal_composer
from test_comment_drafts import profile, result


class PreparationTests(unittest.TestCase):
    def test_bottom_card_editor_above_navigation_needs_no_scroll(self):
        root = self.composer()
        root[0][1].set('bounds', '[50,911][520,1005]')
        ET.SubElement(root, 'node', {'content-desc': 'Discover', 'bounds': '[0,1081][114,1164]'})
        driver = Mock()
        driver.get_window_size.return_value = {'width': 570, 'height': 1230}
        with patch('prepare_comment.read_unobscured_profile', return_value=(root, 'Skip Example')):
            reveal_composer(driver, {'profile_label': 'Skip Example'},
                            {'title': 'Test', 'response': 'Response'})
        driver.execute_script.assert_not_called()

    @patch('prepare_comment.time.sleep')
    def test_long_prompt_scrolls_to_editor_below_viewport(self, sleep):
        clipped = ET.fromstring('<hierarchy><node content-desc="Prompt: Test. Answer: Response"/></hierarchy>')
        driver = Mock()
        driver.get_window_size.return_value = {'width': 570, 'height': 1230}
        with patch('prepare_comment.read_unobscured_profile', side_effect=[
                (clipped, 'Skip Example'), (self.composer(), 'Skip Example')]):
            reveal_composer(driver, {'profile_label': 'Skip Example'},
                            {'title': 'Test', 'response': 'Response'})
        driver.execute_script.assert_called_once()
        self.assertEqual(driver.execute_script.call_args.args[1]['direction'], 'up')
        driver.press_keycode.assert_not_called()

    def composer(self, title="Test", send_label="Send like"):
        root = ET.Element('hierarchy')
        card = ET.SubElement(root, 'node')
        ET.SubElement(card, 'node', {'content-desc': f'Prompt: {title}. Answer: Response'})
        ET.SubElement(card, 'node', {'content-desc': 'Edit comment', 'bounds': '[50,600][520,700]'})
        ET.SubElement(card, 'node', {'content-desc': send_label})
        ET.SubElement(root, 'node', {'class': 'android.widget.Button', 'content-desc': 'Skip Example'})
        return root

    def test_composer_target_and_updated_send_label(self):
        target = {'title': 'Test', 'response': 'Response'}
        for label in ['Send like', 'Send like with message']:
            field, _ = composer_field(self.composer(send_label=label), target)
            self.assertEqual(field.get('content-desc'), 'Edit comment')
        with self.assertRaisesRegex(RuntimeError, 'different or ambiguous'):
            composer_field(self.composer(title='Other'), target)

    def test_composer_matches_extraction_whitespace_normalization(self):
        root = self.composer()
        root[0][0].set('content-desc', 'Prompt: Test. Answer: Response ')
        composer_field(root, {'title': 'Test', 'response': 'Response'})
        with self.assertRaisesRegex(RuntimeError, 'different or ambiguous'):
            composer_field(root, {'title': 'Test', 'response': 'Different'})

    def test_unrelated_prompt_outside_composer_is_rejected(self):
        root = self.composer()
        card = root[0]
        prompt = card[0]
        card.remove(prompt)
        root.append(prompt)
        with self.assertRaises(RuntimeError):
            composer_field(root, {'title': 'Test', 'response': 'Response'})

    def test_keyboard_may_hide_skip_but_target_and_package_must_match(self):
        root = self.composer()
        root.remove(root[-1])
        driver = Mock(current_package='co.hinge.app', page_source=ET.tostring(root, encoding='unicode'))
        target = {'title': 'Test', 'response': 'Response'}
        read_editing_composer(driver, target)
        with self.assertRaisesRegex(RuntimeError, 'different or ambiguous'):
            read_editing_composer(driver, {'title': 'Other', 'response': 'Response'})
        driver.current_package = 'other.app'
        with self.assertRaisesRegex(RuntimeError, 'foreground'):
            read_editing_composer(driver, target)

    @patch('prepare_comment.time.sleep')
    def test_stale_keyboard_flag_never_causes_second_back(self, sleep):
        visible = ET.tostring(self.composer(), encoding='unicode')
        root = self.composer()
        root.remove(root[-1])
        hidden = ET.tostring(root, encoding='unicode')
        driver = Mock(current_package='co.hinge.app')
        driver.is_keyboard_shown.return_value = True
        from unittest.mock import PropertyMock
        with patch.object(type(driver), 'page_source', new_callable=PropertyMock,
                          create=True, side_effect=[hidden, visible, visible]):
            dismiss_keyboard(driver)
            dismiss_keyboard(driver)
        driver.press_keycode.assert_called_once_with(4)

    @patch('prepare_comment.time.sleep')
    def test_delayed_keyboard_rechecks_but_changed_profile_never_retries(self, sleep):
        missing = RuntimeError('Cannot identify the current Discover profile; stopping.')
        expected = (self.composer(), 'Skip Example')
        with patch('prepare_comment.dismiss_keyboard') as dismiss, \
                patch('prepare_comment.read_profile', side_effect=[missing, expected]):
            self.assertEqual(read_unobscured_profile(Mock(), 'Skip Example'), expected)
            self.assertEqual(dismiss.call_count, 2)
        with patch('prepare_comment.dismiss_keyboard') as dismiss, \
                patch('prepare_comment.read_profile', side_effect=RuntimeError('Profile changed during reading; stopping.')):
            with self.assertRaisesRegex(RuntimeError, 'Profile changed'):
                read_unobscured_profile(Mock(), 'Skip Example')
            dismiss.assert_called_once()

    @patch('prepare_comment.time.sleep')
    def test_missing_identity_retry_is_bounded(self, sleep):
        with patch('prepare_comment.dismiss_keyboard'), \
                patch('prepare_comment.read_profile', side_effect=RuntimeError(
                    'Cannot identify the current Discover profile; stopping.')) as read:
            with self.assertRaisesRegex(RuntimeError, 'Cannot identify'):
                read_unobscured_profile(Mock(), 'Skip Example')
            self.assertEqual(read.call_count, 4)

    @patch('prepare_comment.time.sleep')
    def test_clipboard_verification_and_restore_without_submission(self, sleep):
        for actual in ['A Unicode draft: café ☕', 'phase4-stale-value']:
            with self.subTest(actual=actual):
                comment = 'A Unicode draft: café ☕'
                driver = Mock()
                driver.current_package = 'co.hinge.app'
                driver.page_source = ET.tostring(self.composer(), encoding='unicode')
                driver.get_window_size.return_value = {'width': 570, 'height': 1230}
                field = Mock()
                field.id = 'field'
                field.get_attribute.return_value = 'Edit comment'
                driver.find_elements.return_value = [field]
                driver.get_clipboard_text.side_effect = ['previous clipboard', actual]
                if actual == comment:
                    fill_composer(driver, {'profile_label': 'Skip Example'},
                                  {'title': 'Test', 'response': 'Response'}, comment)
                else:
                    with self.assertRaisesRegex(RuntimeError, 'verified exactly'):
                        fill_composer(driver, {'profile_label': 'Skip Example'},
                                      {'title': 'Test', 'response': 'Response'}, comment)
                self.assertEqual(driver.set_clipboard_text.call_args.args, ('previous clipboard',))
                keys = [call.args[0] for call in driver.press_keycode.call_args_list]
                self.assertEqual(keys.count(279), 1)
                self.assertNotIn(66, keys)  # No Enter.
                self.assertEqual(driver.execute_script.call_args_list[0].args,
                                 ('mobile: clickGesture', {'elementId': 'field'}))
                field.click.assert_not_called()

    def test_selection_rejects_modified_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "profile.json"
            source.write_text(json.dumps(profile()))
            draft = {"status": "drafts_pending_review", "source_profile": str(source),
                     "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                     "source_scan_id": "sample", "settings": {"max_chars": 180}, "drafts": result()}
            path = Path(tmp) / "drafts.json"
            path.write_text(json.dumps(draft))
            self.assertEqual(load_selection(path, "recommended")[2]["candidate_id"], "c2")
            with self.assertRaisesRegex(ValueError, "Unknown candidate"):
                load_selection(path, "c4")
            source.write_text(source.read_text() + " ")
            with self.assertRaisesRegex(ValueError, "Source profile changed"):
                load_selection(path, "c2")

    @patch('prepare_comment.time.sleep')
    def test_runner_entry_pastes_once_and_defers_copy_to_submission(self, sleep):
        driver = Mock(current_package='co.hinge.app',
                      page_source=ET.tostring(self.composer(), encoding='unicode'))
        driver.get_window_size.return_value = {'width': 570, 'height': 1230}
        field = Mock()
        field.get_attribute.return_value = 'Edit comment'
        driver.find_elements.return_value = [field]
        driver.get_clipboard_text.return_value = 'previous'
        fill_composer(driver, {'profile_label': 'Skip Example'},
                      {'title': 'Test', 'response': 'Response'}, 'Draft', verify=False)
        keys = [c.args[0] for c in driver.press_keycode.call_args_list]
        self.assertEqual(keys.count(279), 1)
        self.assertNotIn(31, keys)
        driver.get_clipboard_text.assert_called_once()
        driver.set_clipboard_text.assert_called_with('previous')

    @patch('prepare_comment.time.sleep')
    def test_focus_scroll_loss_stops_before_keys_and_restores_clipboard(self, sleep):
        root = self.composer()
        driver = Mock()
        driver.current_package = 'co.hinge.app'
        driver.page_source = '<hierarchy/>'
        driver.is_keyboard_shown.return_value = False
        field = Mock()
        field.get_attribute.return_value = 'Edit comment'
        driver.find_elements.return_value = [field]
        driver.get_clipboard_text.return_value = 'original'
        target = {'title': 'Test', 'response': 'Response'}
        _, selector = composer_field(root, target)
        with patch('prepare_comment.reveal_composer', return_value=(root, selector)), \
                patch('prepare_comment.read_profile', side_effect=[
                    (root, 'Skip Example'), (ET.fromstring('<hierarchy/>'), 'Skip Example')]):
            with self.assertRaisesRegex(RuntimeError, 'after focusing'):
                fill_composer(driver, {'profile_label': 'Skip Example'}, target, 'Draft')
        driver.press_keycode.assert_not_called()
        driver.set_clipboard_text.assert_called_with('original')

    def test_preparation_failure_saves_failing_screen_and_stage(self):
        with tempfile.TemporaryDirectory() as tmp, \
                patch('prepare_comment.load_selection', return_value=(
                    {'scan_id': 'sample', 'profile_label': 'Skip Example'},
                    {'item_id': 'item'}, {'candidate_id': 'c1', 'comment': 'Draft'})), \
                patch('prepare_comment.read_profile', return_value=(self.composer(), 'Skip Example')), \
                patch('prepare_comment.reveal_composer'), \
                patch('prepare_comment.fill_composer', side_effect=RuntimeError('focus lost')), \
                patch('prepare_comment.capture_observation', side_effect=[
                    (Path(tmp) / 'before', {}), (Path(tmp) / 'failure', {})]):
            with self.assertRaisesRegex(RuntimeError, 'focus lost'):
                prepare(Mock(), 'drafts.json', 'c1', resume_composer=True, output_root=tmp)
            receipt = json.loads(next(Path(tmp).glob('preparation_*/preparation.json')).read_text())
            self.assertEqual(receipt['stage'], 'entering_and_verifying_text')
            self.assertEqual(receipt['failure_capture'], str(Path(tmp) / 'failure'))
            self.assertFalse(receipt['submitted'])

    def test_node_path_distinguishes_same_class_siblings(self):
        root = ET.fromstring('<hierarchy><node/><node><button/><button/></node></hierarchy>')
        self.assertEqual(node_xpath(root, 4), '/hierarchy/node[2]/button[2]')
