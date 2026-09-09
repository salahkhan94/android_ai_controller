"""Test the one-profile runner's mandatory approval boundary without live APIs.

Use synthetic draft artifacts and simulated pipeline stages to verify cancellation,
candidate selection, file-change rejection, session cleanup, and single submission.
"""

from contextlib import redirect_stdout
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
import xml.etree.ElementTree as ET

from run import ask_approval, pipeline
from test_comment_drafts import profile, result


def artifacts(root):
    data = profile()
    data['profile_label'] = 'Skip Example'
    source = Path(root) / 'sample' / 'profile.json'
    source.parent.mkdir()
    source.write_text(json.dumps(data))
    draft = {'status': 'drafts_pending_review', 'source_profile': str(source),
             'source_scan_id': 'sample', 'source_sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
             'settings': {'max_chars': 180}, 'drafts': result()}
    path = Path(root) / 'drafts.json'
    path.write_text(json.dumps(draft))
    return data, path


class RunnerTests(unittest.TestCase):
    def test_display_selection_and_explicit_approval(self):
        with tempfile.TemporaryDirectory() as tmp, redirect_stdout(io.StringIO()) as output:
            _, path = artifacts(tmp)
            replies = iter(['c1', 'SEND'])
            approval = ask_approval(path, lambda _: next(replies))
            self.assertEqual(approval['candidate_id'], 'c1')
            self.assertIn('Their response: Start a bakery', output.getvalue())
            self.assertIn(approval['comment'], output.getvalue())

    def test_empty_eof_and_unknown_input_cancel(self):
        with tempfile.TemporaryDirectory() as tmp, redirect_stdout(io.StringIO()):
            _, path = artifacts(tmp)
            for response in ['', 'no', 'maybe']:
                self.assertIsNone(ask_approval(path, lambda _: response))
            self.assertIsNone(ask_approval(path, Mock(side_effect=EOFError)))

    def test_file_change_during_review_invalidates_approval(self):
        with tempfile.TemporaryDirectory() as tmp, redirect_stdout(io.StringIO()):
            _, path = artifacts(tmp)
            def edit_then_approve(_):
                path.write_text(path.read_text() + ' ')
                return 'SEND'
            with self.assertRaisesRegex(RuntimeError, 'approval discarded'):
                ask_approval(path, edit_then_approve)

    def test_orchestration_cancel_and_approved_uncertain_send(self):
        for reply in ['', 'SEND']:
            with self.subTest(reply=reply), tempfile.TemporaryDirectory() as tmp, redirect_stdout(io.StringIO()):
                data, path = artifacts(tmp)
                first, second = Mock(), Mock()
                connect = Mock(side_effect=[first, second])
                candidate = result()['candidates'][1]
                prepared = {'preparation_path': str(Path(tmp) / 'preparation.json'),
                            'candidate_id': 'c2', 'item_id': candidate['item_id'],
                            'comment': candidate['comment'], 'source_scan_id': 'sample'}
                with patch('run.read_profile', return_value=(ET.fromstring('<hierarchy/>'), 'Skip Example')), \
                        patch('run.scan_profile', return_value=data), \
                        patch('run.generate_drafts', return_value=path), \
                        patch('run.prepare', return_value=prepared) as prepare, \
                        patch('run.submit', return_value={'status': 'uncertain_profile_advanced'}) as submit:
                    def ask(_):
                        first.quit.assert_called_once()
                        prepare.assert_not_called()
                        submit.assert_not_called()
                        return reply
                    record = pipeline(connect, model='test', output_root=tmp, ask=ask)
                    if not reply:
                        self.assertEqual(record['status'], 'cancelled_without_sending')
                        prepare.assert_not_called()
                        submit.assert_not_called()
                        self.assertEqual(connect.call_count, 1)
                    else:
                        self.assertEqual(record['status'], 'uncertain_profile_advanced')
                        submit.assert_called_once()
                        self.assertTrue(prepare.call_args.kwargs['defer_readback'])
                        self.assertTrue(submit.call_args.kwargs['send'])
                        second.quit.assert_called_once()

    def test_mismatched_preparation_never_submits(self):
        with tempfile.TemporaryDirectory() as tmp, redirect_stdout(io.StringIO()):
            data, path = artifacts(tmp)
            with patch('run.read_profile', return_value=(ET.fromstring('<hierarchy/>'), 'Skip Example')), \
                    patch('run.scan_profile', return_value=data), \
                    patch('run.generate_drafts', return_value=path), \
                    patch('run.prepare', return_value={'preparation_path': 'unused', 'candidate_id': 'c2',
                          'item_id': 'sample:prompt-1', 'comment': 'Changed text', 'source_scan_id': 'sample'}), \
                    patch('run.submit') as submit:
                with self.assertRaisesRegex(RuntimeError, 'does not match approval'):
                    pipeline(Mock(return_value=Mock()), model='test', output_root=tmp, ask=lambda _: 'SEND')
                submit.assert_not_called()
