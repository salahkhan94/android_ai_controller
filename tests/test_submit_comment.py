"""Test Phase 5 single-attempt recording, target validation, and uncertainty.

Synthetic drivers simulate successful checks and a timeout after clicking.
No test sends a real message or connects to an emulator.
"""

from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
import xml.etree.ElementTree as ET

from submit_comment import claim_attempt, outcome, send_control, submit


def composer():
    return ET.fromstring('''<hierarchy><card>
      <node content-desc="Prompt: Test. Answer: Response"/>
      <node content-desc="Edit comment"/>
      <node class="android.widget.Button" content-desc="Send like with message"
        enabled="true" displayed="true" bounds="[205,797][520,854]"/>
      </card><node content-desc="Skip Example"/></hierarchy>''')


class SubmitTests(unittest.TestCase):
    @patch('submit_comment.time.sleep')
    def test_upsell_continuation_is_recorded_and_never_selects_rose(self, sleep):
        profile = {'profile_label': 'Skip Example'}
        target = {'title': 'Test', 'response': 'Response'}
        candidate = {'comment': 'Selected comment'}
        driver = Mock()
        driver.current_package = 'co.hinge.app'
        driver.get_window_size.return_value = {'width': 570, 'height': 1230}
        send_button, standard_like = Mock(), Mock()
        send_button.id = 'send-button'
        send_button.get_attribute.return_value = 'Send like with message'
        standard_like.id = 'standard-like'
        driver.find_elements.side_effect = [[send_button], [standard_like]]
        from unittest.mock import PropertyMock
        with patch.object(type(driver), 'page_source', new_callable=PropertyMock, create=True) as source, \
                tempfile.TemporaryDirectory() as tmp, redirect_stdout(io.StringIO()), \
                patch('submit_comment.load_prepared', return_value=(profile, target, candidate)), \
                patch('submit_comment.verify_text', return_value=composer()), \
                patch('submit_comment.read_profile', return_value=(composer(), 'Skip Example')), \
                patch('submit_comment.capture_observation', return_value=(Path(tmp), {'consistency': 'unchanged'})):
            source.side_effect = ['<root><node text="Send a Rose instead?"/><node text="Send Like anyway"/></root>',
                                  '<root><node text="Like sent"/></root>']
            record = submit(driver, 'preparation.json', send=True, output_root=tmp)
            self.assertEqual(record['status'], 'confirmed_by_ui')
            self.assertTrue(record['standard_like_confirmation_attempted'])
            clicked = [c.args[1]['elementId'] for c in driver.execute_script.call_args_list]
            self.assertEqual(clicked, ['send-button', 'standard-like'])

    def test_claim_is_exclusive_and_persistent(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / 'attempt.json'
            claim_attempt(p, {'status': 'attempted_outcome_unknown'})
            with self.assertRaises(FileExistsError):
                claim_attempt(p, {})
            self.assertEqual(json.loads(p.read_text())['status'], 'attempted_outcome_unknown')

    def test_profile_advance_is_not_confirmation(self):
        self.assertEqual(outcome(ET.fromstring('<root><node content-desc="Skip Other"/></root>'),
                                 'Skip Example'), 'uncertain_profile_advanced')
        self.assertEqual(outcome(ET.fromstring('<root><node text="Like sent"/></root>'),
                                 'Skip Example'), 'confirmed_by_ui')
        sheet = ET.fromstring('<root><node text="Send a Rose instead?"/><node text="Send Like anyway"/></root>')
        self.assertEqual(outcome(sheet, 'Skip Example'), 'pending_rose_upsell_not_confirmed')

    def test_target_validation_rejects_other_prompt_and_clipping(self):
        size = {'width': 570, 'height': 1230}
        with self.assertRaises(RuntimeError):
            send_control(composer(), {'title': 'Wrong', 'response': 'Response'}, size)
        root = composer()
        root[0][-1].set('bounds', '[205,1100][520,1200]')
        with self.assertRaises(RuntimeError):
            send_control(root, {'title': 'Test', 'response': 'Response'}, size)

    def test_check_never_clicks_and_timeout_attempt_cannot_repeat(self):
        profile = {'profile_label': 'Skip Example'}
        target = {'title': 'Test', 'response': 'Response'}
        candidate = {'comment': 'Selected comment'}
        driver = Mock()
        driver.get_window_size.return_value = {'width': 570, 'height': 1230}
        control = Mock()
        control.get_attribute.return_value = 'Send like with message'
        control.is_enabled.return_value = True
        control.is_displayed.return_value = True
        control.id = 'send-control'
        driver.find_elements.return_value = [control]
        driver.execute_script.side_effect = TimeoutError('ambiguous transport failure')
        with tempfile.TemporaryDirectory() as tmp, redirect_stdout(io.StringIO()), \
                patch('submit_comment.load_prepared', return_value=(profile, target, candidate)), \
                patch('submit_comment.verify_text', return_value=composer()), \
                patch('submit_comment.read_profile', return_value=(composer(), 'Skip Example')), \
                patch('submit_comment.capture_observation', return_value=(Path(tmp), {'consistency': 'unchanged'})):
            checked = submit(driver, 'preparation.json', output_root=tmp)
            self.assertEqual(checked['status'], 'ready_not_sent')
            driver.execute_script.assert_not_called()
            attempted = submit(driver, 'preparation.json', send=True, output_root=tmp)
            self.assertEqual(attempted['status'], 'uncertain_after_attempt')
            self.assertEqual(driver.execute_script.call_count, 1)
            with self.assertRaisesRegex(RuntimeError, 'already recorded'):
                submit(driver, 'preparation.json', send=True, output_root=tmp)
            self.assertEqual(driver.execute_script.call_count, 1)
