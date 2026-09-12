"""Offline reply-service tests for command binding, persistence and UI parsing."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock
import xml.etree.ElementTree as ET
from match_replies.storage import Store
from match_replies.controller import Controller
from match_replies.hinge import messages, merge, match_rows

class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.store=Store(Path(self.tmp.name)/'state.sqlite3')
        self.backend=Mock()
        self.match={'name':'Example','key':'identity','preview':'Hello'}
        self.backend.list_matches.return_value=[self.match]
        self.backend.history.return_value={'coverage':'ui_boundary_verified','fingerprint':'v1','messages':[{'sender':'match','text':'Hello'}]}
        self.backend.send.side_effect=lambda m,h,t,claim:'sent' if claim() else 'uncertain'
        self.generate=Mock(return_value=['One?','Two?','Three?'])
        self.controller=Controller(self.store,self.backend,self.generate)
        self.seq=0
    def command(self,text,reply_to=''):
        self.seq+=1;sid=f'in{self.seq}'
        self.store.accept(sid,text,reply_to)
        event=self.store.claim('inbox')
        state,out=self.controller.handle(event)
        self.store.finish(sid,state,out)
        while (row:=self.store.claim('outbox')):
            self.store.notification(row['id'],'sent','out'+str(row['id']))
        return state,out
    def test_context_regeneration_invalidates_old_selection(self):
        self.command('Begin');state,_=self.command('Example');old=state['revision']
        self.command('Add context');state,_=self.command('Do not ask her out yet')
        self.assertNotEqual(old,state['revision'])
        self.assertEqual(self.generate.call_args.args[1],['Do not ask her out yet'])
        self.command(old+' 3');self.backend.send.assert_not_called()
        self.command(state['revision']+' 3');self.backend.send.assert_called_once()
    def test_bare_choice_without_reply_binding_never_sends(self):
        self.command('Begin');self.command('1');self.command('3')
        self.backend.send.assert_not_called()
    def test_duplicate_inbound_and_durable_send_claim(self):
        self.assertTrue(self.store.accept('same','Begin'))
        self.assertFalse(self.store.accept('same','Begin'))
        self.assertTrue(self.store.claim_send('attempt',{}))
        self.assertFalse(self.store.claim_send('attempt',{}))
    def test_changed_conversation_regenerates_without_sending(self):
        self.command('Begin');state,_=self.command('Example')
        self.backend.history.return_value={'fingerprint':'v2','messages':[{'sender':'match','text':'New'}]}
        state,out=self.command(state['revision']+' 2')
        self.backend.send.assert_not_called();self.assertIn('changed',out[0])
    def test_crash_recovery_does_not_replay_choice(self):
        self.store.accept('a','3');self.store.claim('inbox');self.store.recover()
        self.assertIsNone(self.store.claim('inbox'))
    def test_parser_uses_speaker_labels_and_visual_order(self):
        root=ET.fromstring('<hierarchy><node content-desc=" You: New?. " bounds="[100,400][500,450]"/><node content-desc=" Example: Hello. " bounds="[20,200][350,250]"/></hierarchy>')
        self.assertEqual(messages(root,'Example'),[{'sender':'match','text':'Hello'},{'sender':'me','text':'New?'}])
    def test_sequence_overlap_retains_repeated_messages(self):
        a={'sender':'me','text':'Hi'};b={'sender':'match','text':'Yes'}
        self.assertEqual(merge([a,b],[b,a]),[a,b,a])
        with self.assertRaises(RuntimeError):merge([a,a],[a,a])
        with self.assertRaises(RuntimeError):merge([a],[b])
    def test_only_your_turn_rows(self):
        root=ET.fromstring('''<hierarchy><node text="Your turn (1)" bounds="[0,200][300,230]"/>
          <node clickable="true" long-clickable="true" bounds="[0,250][570,350]"><node text="Example"/><node text="Hello"/></node>
          <node text="Hidden (1)" bounds="[0,400][300,430]"/>
          <node clickable="true" long-clickable="true" bounds="[0,450][570,550]"><node text="Hidden person"/></node></hierarchy>''')
        self.assertEqual([m['name'] for m in match_rows(root)],['Example'])

if __name__=='__main__':unittest.main()

class WebhookTests(unittest.TestCase):
    def test_signature_owner_and_duplicate_delivery(self):
        from match_replies.web import create_app
        from twilio.request_validator import RequestValidator
        with tempfile.TemporaryDirectory() as tmp:
            store=Store(Path(tmp)/'db')
            config={'token':'test-token','url':'https://example.test/whatsapp',
                    'owner':'whatsapp:+15550000001','sender':'whatsapp:+15550000002'}
            client=create_app(store,config).test_client()
            data={'From':config['owner'],'To':config['sender'],'Body':'Begin','MessageSid':'SMtest'}
            self.assertEqual(client.post('/whatsapp',data=data).status_code,403)
            signature=RequestValidator(config['token']).compute_signature(config['url'],data)
            for _ in range(2):
                self.assertEqual(client.post('/whatsapp',data=data,headers={'X-Twilio-Signature':signature}).status_code,200)
            self.assertIsNotNone(store.claim('inbox'));self.assertIsNone(store.claim('inbox'))
            data['From']='whatsapp:+15550000003';data['MessageSid']='SMother'
            signature=RequestValidator(config['token']).compute_signature(config['url'],data)
            self.assertEqual(client.post('/whatsapp',data=data,headers={'X-Twilio-Signature':signature}).status_code,403)

class DraftValidationTests(unittest.TestCase):
    def test_full_history_and_context_passed_and_style_enforced(self):
        from unittest.mock import patch
        from match_replies.drafts import generate
        history={'coverage':'ui_boundary_verified','messages':[{'sender':'match','text':'Hello'}]}
        with patch('match_replies.drafts.generate_json',return_value=({'replies':['One?','Two?','Three?'],'recommended':1},{})) as api:
            self.assertEqual(len(generate(history,['Do not ask for a date'],'test')),3)
            payload=json.loads(api.call_args.args[0]['input'])
            self.assertEqual(payload['additional_context'],['Do not ask for a date'])
            self.assertEqual(payload['conversation'],history['messages'])
        with patch('match_replies.drafts.generate_json',return_value=({'replies':['One—two?','Two?','Three?'],'recommended':1},{})):
            with self.assertRaises(ValueError):generate(history,[],'test')
        with self.assertRaises(ValueError):generate({'coverage':'partial'},[],'test')

class SendGuardTests(unittest.TestCase):
    def test_attempt_is_claimed_before_click_and_never_retried(self):
        from contextlib import contextmanager
        from unittest.mock import patch
        from match_replies.hinge import Hinge, COMPOSER
        h=Hinge();match={'name':'Example','key':'x'}
        expected={'fingerprint':'old'};h.read=Mock(return_value=expected)
        root=ET.fromstring(f'''<hierarchy><node text="Example" bounds="[200,40][330,80]"/>
        <node resource-id="{COMPOSER}" text="Draft?" bounds="[17,1084][481,1147]"/>
        <node content-desc="Send" enabled="true" bounds="[490,1080][550,1147]"/>
        <node content-desc=" Example: Hello. " bounds="[20,300][250,350]"/></hierarchy>''')
        driver=Mock();driver.get_window_size.return_value={'height':1230}
        field=Mock();field.text='';driver.find_elements.return_value=[field]
        field.send_keys.side_effect=lambda text:setattr(field,'text',text)
        @contextmanager
        def connected():yield driver
        h.connect=connected
        claim=Mock(return_value=True)
        def failing_click(*args):
            claim.assert_called_once()
            raise RuntimeError('uncertain click')
        with patch('match_replies.hinge.root_for',return_value=root), patch('match_replies.hinge.tap',side_effect=failing_click) as tap:
            self.assertEqual(h.send(match,expected,'Draft?',claim),'uncertain')
            tap.assert_called_once()
        field.text='';claim.reset_mock();claim.return_value=False
        with patch('match_replies.hinge.root_for',return_value=root),patch('match_replies.hinge.tap') as tap:
            with self.assertRaisesRegex(RuntimeError,'attempt already exists'): h.send(match,expected,'Draft?',claim)
            tap.assert_not_called()

class ReactionTests(unittest.TestCase):
    def test_reaction_suffix_is_metadata_not_message_text(self):
        root=ET.fromstring('<hierarchy><node content-desc=" You: Looking forward to friday!. Example liked this message" bounds="[100,200][500,250]"/></hierarchy>')
        self.assertEqual(messages(root,'Example'),[{'sender':'me','text':'Looking forward to friday!','liked_by':'match'}])

class CancellationTests(unittest.TestCase):
    def test_cancel_invalidates_queued_choice_but_not_started_attempt(self):
        with tempfile.TemporaryDirectory() as tmp:
            store=Store(Path(tmp)/'db')
            store.accept('choice','abc 3')
            store.accept('cancel','Cancel')
            self.assertEqual(store.claim('inbox')['sid'],'cancel')
            self.assertIsNone(store.claim('inbox'))
            self.assertFalse(store.accept('cancel','Cancel'))

class ReplyToTests(unittest.TestCase):
    setUp=ServiceTests.setUp
    command=ServiceTests.command
    def test_reply_to_current_notification_authorizes_exact_choice(self):
        self.command('Begin');self.command('Example')
        self.command('2',reply_to='out2')
        self.backend.send.assert_called_once()
        self.assertEqual(self.backend.send.call_args.args[2],'Two?')
    def test_failed_notification_cannot_authorize_reply(self):
        self.command('Begin');state,_=self.command('Example')
        self.store.delivery('out2','undelivered')
        self.command(state['revision']+' 2')
        self.backend.send.assert_not_called()
