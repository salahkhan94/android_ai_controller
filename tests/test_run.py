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

class ProfileLoopTests(unittest.TestCase):
    def test_advancement_continues_with_identity_guard_until_exhausted(self):
        from run import run_profiles
        outcomes = [
            {'status': 'uncertain_profile_advanced', 'approval': {'profile_label': 'Skip A'}},
            {'status': 'confirmed_by_ui', 'approval': {'profile_label': 'Skip B'}},
            {'status': 'likes_exhausted'}]
        with patch('run.pipeline', side_effect=outcomes) as one, redirect_stdout(io.StringIO()):
            self.assertEqual(run_profiles(Mock(), model='test')['status'], 'likes_exhausted')
        self.assertEqual([c.kwargs['previous_label'] for c in one.call_args_list], [None, 'Skip A', 'Skip B'])

    def test_cancellation_uncertain_outcome_and_limit_stop_loop(self):
        from run import run_profiles
        for status in ['cancelled_without_sending', 'uncertain_after_attempt', 'stopped_profile_not_advanced']:
            with patch('run.pipeline', return_value={'status': status}) as one, redirect_stdout(io.StringIO()):
                self.assertEqual(run_profiles(Mock(), model='test')['status'], status)
                one.assert_called_once()
        with patch('run.pipeline', return_value={'status': 'uncertain_profile_advanced'}) as one, redirect_stdout(io.StringIO()):
            run_profiles(Mock(), model='test', max_profiles=1)
            one.assert_called_once()

    def test_previous_profile_blocks_before_scan_and_generation(self):
        with tempfile.TemporaryDirectory() as tmp, redirect_stdout(io.StringIO()), \
                patch('run.read_profile', return_value=(ET.fromstring('<hierarchy/>'), 'Skip A')), \
                patch('run.scan_profile') as scan, patch('run.generate_drafts') as generate:
            driver = Mock()
            result = pipeline(Mock(return_value=driver), model='test', output_root=tmp, previous_label='Skip A')
            self.assertEqual(result['status'], 'stopped_profile_not_advanced')
            scan.assert_not_called()
            generate.assert_not_called()
            driver.quit.assert_called_once()

    def test_quota_notice_stops_before_scan(self):
        with tempfile.TemporaryDirectory() as tmp, redirect_stdout(io.StringIO()), \
                patch('run.scan_profile') as scan:
            driver = Mock(current_package='co.hinge.app', page_source='<hierarchy><node text="You’re out of Likes!"/></hierarchy>')
            result = pipeline(Mock(return_value=driver), model='test', output_root=tmp)
            self.assertEqual(result['status'], 'likes_exhausted')
            scan.assert_not_called()
            driver.quit.assert_called_once()

    def test_rose_zero_and_upsell_are_not_like_exhaustion(self):
        from submit_comment import likes_exhausted, outcome
        root = ET.fromstring('<hierarchy><node text="0"/><node text="Send a Rose instead?"/><node text="Get unlimited Likes"/></hierarchy>')
        self.assertFalse(likes_exhausted(root))
        root = ET.fromstring('<hierarchy><node text="You’ve used all your Likes for today"/></hierarchy>')
        self.assertEqual(outcome(root, 'Skip A'), 'likes_exhausted')

class AutomaticSelectionTests(unittest.TestCase):
    def test_prints_details_and_selects_recommendation_without_input(self):
        from run import automatic_selection
        with tempfile.TemporaryDirectory() as tmp, redirect_stdout(io.StringIO()) as output:
            _, path = artifacts(tmp)
            selected = automatic_selection(path)
            self.assertEqual(selected['candidate_id'], 'c2')
            self.assertEqual(selected['authorization_mode'], 'automatic_user_configured')
            self.assertIn('Start a bakery', output.getvalue())
            for c in result()['candidates']:
                self.assertIn(c['comment'], output.getvalue())
            self.assertIn('Chosen comment', output.getvalue())

    def test_automatic_pipeline_never_requests_input(self):
        with tempfile.TemporaryDirectory() as tmp, redirect_stdout(io.StringIO()):
            data, path = artifacts(tmp)
            c = result()['candidates'][1]
            prepared = {'preparation_path': 'receipt', 'candidate_id': 'c2',
                        'item_id': c['item_id'], 'comment': c['comment'], 'source_scan_id': 'sample'}
            with patch('run.read_profile', return_value=(ET.fromstring('<hierarchy/>'), 'Skip Example')), \
                    patch('run.scan_profile', return_value=data), \
                    patch('run.generate_drafts', return_value=path), \
                    patch('run.prepare', return_value=prepared), \
                    patch('run.submit', return_value={'status': 'uncertain_profile_advanced'}) as send:
                ask = Mock(side_effect=AssertionError('Must not ask'))
                pipeline(Mock(return_value=Mock()), model='test', output_root=tmp, automatic=True, ask=ask)
                ask.assert_not_called()
                send.assert_called_once()

class FailureDiagnosticsTests(unittest.TestCase):
    def test_empty_generation_error_records_stage_and_locations(self):
        with tempfile.TemporaryDirectory() as tmp, redirect_stdout(io.StringIO()):
            with patch('run.read_profile', return_value=(ET.fromstring('<hierarchy/>'), 'Skip Example')), \
                    patch('run.scan_profile', return_value=profile()), \
                    patch('run.generate_drafts', side_effect=ValueError()), \
                    patch('run.prepare') as prepare, patch('run.submit') as submit:
                with self.assertRaises(ValueError):
                    pipeline(Mock(return_value=Mock()), model='test', output_root=tmp)
                record = json.loads(next(Path(tmp).glob('run_*/run.json')).read_text())
                self.assertEqual(record['stage'], 'generating_comments')
                self.assertTrue(record['error_message_empty'])
                self.assertTrue(record['error_locations'])
                self.assertEqual(set(record['error_locations'][0]), {'file', 'line', 'function'})
                prepare.assert_not_called()
                submit.assert_not_called()
