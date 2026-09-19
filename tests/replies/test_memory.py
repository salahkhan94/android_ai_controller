"""Persistent match identity, cached profiles, user instructions and draft isolation."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock
from match_replies.storage import Store
from match_replies.memory import Memory
from match_replies.controller import Controller


class MemoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.store=Store(Path(self.tmp.name)/'db');self.memory=Memory(self.store)
        self.match={'name':'Example','key':'preview-one'}
        self.history={'coverage':'ui_boundary_verified','fingerprint':'v1','messages':[
            {'sender':'opening_context','text':'Original prompt'},
            {'sender':'match','text':'Hi'}]}

    def test_new_preview_reuses_record_and_changes_fail_closed(self):
        r=self.memory.sync(self.match,self.history)
        later={**self.history,'messages':self.history['messages']+[{'sender':'me','text':'Hello'}]}
        updated=self.memory.sync({**self.match,'key':'preview-two'},later)
        self.assertEqual(r['id'],updated['id'])
        self.assertEqual(len(updated['history']['messages']),3)
        self.memory.sync(self.match,later)
        self.assertEqual(len(self.memory.load(r['id'])['history']['messages']),3)
        with self.assertRaises(RuntimeError): self.memory.sync(self.match,self.history)
        with self.assertRaises(RuntimeError): self.memory.sync(self.match,{**self.history,'messages':[
            {'sender':'opening_context','text':'Other prompt'},{'sender':'match','text':'Hi'}]})

    def test_restart_preserves_profile_and_context_not_candidates(self):
        backend=Mock();backend.list_matches.return_value=[self.match];backend.history.return_value=self.history
        loader=Mock(return_value={'texts':['Books'],'visual_observations':[{'description':'A beach'}]})
        gen=Mock(return_value=['One?','Two?','Three?'])
        c=Controller(self.store,backend,gen,self.memory,loader)
        counter=0
        def command(text):
            nonlocal counter
            counter+=1;sid=str(counter)
            self.store.accept(sid,text);event=self.store.claim('inbox')
            state,out=c.handle(event);self.store.finish(sid,state,out)
            while row:=self.store.claim('outbox'):self.store.notification(row['id'],'sent',str(row['id']))
            return state
        command('Begin');command('Example');command('Add context');command('No dates yet')
        self.store.reset_session()
        c=Controller(self.store,backend,gen,Memory(self.store),loader)
        command('Begin');state=command('Example')
        loader.assert_called_once()
        self.assertEqual(gen.call_args.args[1],['No dates yet'])
        self.assertIn('profile_context',gen.call_args.args[0])
        record=self.memory.load(state['memory_id'])
        self.assertNotIn('replies',record)
        self.assertEqual(record['history'],self.history)
        command('Refresh profile');self.assertEqual(loader.call_count,2)

class ProfileContextTests(unittest.TestCase):
    def test_vision_input_and_saved_observations(self):
        from unittest.mock import patch
        from match_replies.profile_context import describe
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp,'screen.png').write_bytes(b'image-bytes')
            profile={'evidence':[tmp],'texts':['Prompt: A. Answer: Books']}
            with patch('match_replies.profile_context.generate_json',return_value=({'description':'Books on a shelf.'},{})) as model:
                result=describe(profile,'test-model')
            request=model.call_args.args[0]
            self.assertEqual(request['input'][0]['content'][0]['type'],'input_image')
            self.assertTrue(request['input'][0]['content'][0]['image_url'].startswith('data:image/png;base64,'))
            self.assertEqual(result['visual_observations'][0]['source'],tmp)
            self.assertEqual(result['analysis_model'],'test-model')

    def test_draft_request_includes_profile_and_limits(self):
        import json
        from unittest.mock import patch
        from match_replies.drafts import generate
        history={'coverage':'ui_boundary_verified','messages':[{'sender':'match','text':'Hi'}],
                 'profile_context':{'texts':['Books'], 'visual_observations':[{'description':'Beach'}],
                                    'limitations':['Still frames only'],'captured_at':'today','evidence':['private-path']}}
        with patch('match_replies.drafts.generate_json',return_value=({'replies':['One?','Two?','Three?'],'recommended':1},{})) as model:
            generate(history,['No dates'],'test-model')
        data=json.loads(model.call_args.args[0]['input'])
        self.assertEqual(data['profile_context']['texts'],['Books'])
        self.assertEqual(data['profile_context']['limitations'],['Still frames only'])
        self.assertNotIn('evidence',data['profile_context'])

class RelativeTimestampTests(MemoryTests):
    def test_legacy_timestamps_do_not_block_new_messages(self):
        original={**self.history,'messages':[{'sender':'opening_context','text':'Yesterday 7:05PM'}]+self.history['messages']}
        record=self.memory.sync(self.match,original)
        record['context']=['Remember this'];record['profile']={'texts':['Books']};self.memory.save(record)
        fresh={**self.history,'messages':[{'sender':'opening_context','text':'Today 1:53AM'}]+self.history['messages']+[{'sender':'me','text':'Hello'},{'sender':'match','text':'New reply'}]}
        updated=self.memory.sync(self.match,fresh)
        self.assertEqual(updated['id'],record['id'])
        self.assertEqual(updated['context'],['Remember this'])
        self.assertEqual(updated['profile'],{'texts':['Books']})

    def test_real_timestamp_message_is_preserved(self):
        from match_replies.memory import core
        self.assertEqual(core([{'sender':'match','text':'Today 1:53AM'}]),[{'sender':'match','text':'Today 1:53AM'}])

class NotificationPanelTests(unittest.TestCase):
    def test_complete_legacy_panel_is_ignored_only_in_opening_context(self):
        from match_replies.memory import core
        labels=['Get notifications from Bridget only',
                'Timing is everything. This will not turn on notifications for other matches.',
                'Enable for Bridget']
        conversation=[{'sender':'opening_context','text':'Real prompt'},
                      {'sender':'match','text':'Hello'}]
        panel=[{'sender':'opening_context','text':text} for text in labels]
        self.assertEqual(core(conversation[:1]+panel+conversation[1:],'Bridget'),conversation)
        self.assertNotEqual(core(conversation+panel,'Other'),conversation)
        self.assertNotEqual(core(conversation+panel[:1],'Bridget'),conversation)
        real=[{'sender':'match','text':text} for text in labels]
        self.assertEqual(core(real,'Bridget'),real)
