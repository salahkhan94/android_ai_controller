"""Observed Hinge match-list/chat extraction and guarded reply submission.

Uses explicit speaker accessibility labels. Unsupported layouts stop rather than
invent message direction or omit history. No profile-like runner code is changed.
"""
import hashlib
import json
import logging
import re
import time
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path
from observation import capture_observation
from prepare_comment import node_xpath
from profile_items import bounds
from .device import session, ROOT

COMPOSER='co.hinge.app:id/messageComposition'

def fingerprint(value):
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True).encode()).hexdigest()

def root_for(driver):
    if driver.current_package!='co.hinge.app': raise RuntimeError('Open Hinge in the emulator.')
    root=ET.fromstring(driver.page_source)
    if any(n.get('text')=='No network connection' for n in root.iter()):
        raise RuntimeError('Hinge reports no network connection. Reconnect the emulator first.')
    return root

def tap(driver, root, node):
    selector=node_xpath(root,list(root.iter()).index(node))
    elements=driver.find_elements('xpath',selector)
    if len(elements)!=1 or not elements[0].is_displayed(): raise RuntimeError('UI target is no longer unique.')
    driver.execute_script('mobile: clickGesture',{'elementId':elements[0].id})
    time.sleep(.4)

def swipe(driver,direction):
    size=driver.get_window_size()
    driver.execute_script('mobile: swipeGesture',{'left':int(size['width']*.25),'top':int(size['height']*.19),
        'width':int(size['width']*.5),'height':int(size['height']*.55),'direction':direction,'percent':.4,'speed':400})
    time.sleep(.3)

def geometry(root):
    return [(n.get('text',''),n.get('content-desc',''),n.get('bounds')) for n in root.iter()
            if n.get('package')=='co.hinge.app' and (n.get('text') or n.get('content-desc'))]

def header(root,name):
    titles=[n for n in root.iter() if n.get('text')==name and bounds(n.get('bounds')) and bounds(n.get('bounds'))[1]<110]
    if len(titles)!=1 or not any(n.get('resource-id')==COMPOSER for n in root.iter()):
        raise RuntimeError('Selected conversation identity is not exposed.')

def match_rows(root, expected_count=None):
    parents={c:p for p in root.iter() for c in p}
    headers=[n for n in root.iter() if re.fullmatch(r'Your turn \(\d+\)',n.get('text',''))]
    # Caller preserves the section across scrolls. Rows are recognized only under
    # a visible section heading, preventing accidental inclusion of Hidden.
    if len(headers)>1 or (not headers and expected_count is None): raise RuntimeError('Your turn heading is not exposed; list completeness unknown.')
    start=bounds(headers[0].get('bounds'))[3] if headers else 94
    ends=[bounds(n.get('bounds'))[1] for n in root.iter() if re.match(r'(Hidden|Their turn) \(',n.get('text','')) and bounds(n.get('bounds'))]
    end=min(ends) if ends else 1081
    result=[]
    for n in root.iter():
        rect=bounds(n.get('bounds'))
        if n.get('clickable')!='true' or n.get('long-clickable')!='true' or not rect or not start<=rect[1]<end: continue
        texts=[c.get('text') for c in n.iter() if c.get('text')]
        if not texts: continue
        name=texts[0]; preview='\n'.join(texts[1:])
        result.append({'name':name,'preview':preview,'key':fingerprint([name,preview]),'_node':n})
    if len({m['name'].casefold() for m in result})!=len(result):
        raise RuntimeError('Duplicate match names need additional UI identity evidence; no thread selected.')
    return result

def messages(root,name):
    """Sort by screen position; Compose XML traversal is reverse chronological."""
    found=[]
    for node in root.iter():
        label=node.get('text','')
        rect=bounds(node.get('bounds'))
        if rect and re.match(r'^(Mon|Tue|Wed|Thu|Fri|Sat|Sun), ',label):
            found.append((rect[1],{'sender':'system','text':label}))
        desc=node.get('content-desc','')
        if rect and 156 <= rect[1] < 1062 and not desc.startswith((' You: ', f' {name}: ')) and re.search(r'\b(photo|image|voice|audio|video|gif|sticker)\b', desc, re.I):
            raise RuntimeError('Conversation contains an unsupported attachment; history cannot be represented completely.')
        for prefix,sender in [(' You: ','me'),(f' {name}: ','match')]:
            if desc.startswith(prefix):
                value=desc[len(prefix):]
                reaction=None
                if value.endswith('. '):
                    value=value[:-2]
                else:
                    suffix=re.search(r'\. (You|'+re.escape(name)+r') liked this message$',value)
                    if suffix:
                        reaction='me' if suffix.group(1)=='You' else 'match'
                        value=value[:suffix.start()]
                    else:
                        path=ROOT/'captures'/'matches'/('unsupported_'+uuid.uuid4().hex+'.xml')
                        path.parent.mkdir(parents=True,exist_ok=True)
                        path.write_bytes(ET.tostring(root))
                        raise RuntimeError(f'Unrecognized message accessibility suffix. Evidence: {path}')
                rect=bounds(node.get('bounds'))
                if not rect: raise RuntimeError('Message geometry missing.')
                entry={'sender':sender,'text':value}
                if reaction: entry['liked_by']=reaction
                found.append((rect[1],entry))
                break
    return [m for _,m in sorted(found,key=lambda x:x[0])]

def merge(history,view):
    if not history: return list(view)
    if not view: return history
    overlaps=[n for n in range(1,min(len(history),len(view))+1) if history[-n:]==view[:n]]
    if not overlaps: raise RuntimeError('Conversation viewport overlap was lost; refusing incomplete history.')
    if len(overlaps)>1: raise RuntimeError('Repeated messages make viewport alignment ambiguous.')
    return history+view[overlaps[0]:]

class Hinge:
    def __init__(self,udid='127.0.0.1:6555',server='http://127.0.0.1:4723',max_scrolls=80):
        self.udid,self.server,self.max_scrolls=udid,server,max_scrolls
    def connect(self): return session(self.udid,self.server)
    def matches_page(self,d):
        root=root_for(d)
        if any(n.get('resource-id')==COMPOSER for n in root.iter()):
            back=[n for n in root.iter() if n.get('content-desc')=='Back']
            if len(back)!=1: raise RuntimeError('Cannot return to Matches.')
            tap(d,root,back[0]);root=root_for(d)
        tabs=[n for n in root.iter() if n.get('content-desc','').startswith('Matches')]
        if len(tabs)!=1: raise RuntimeError('Matches tab is not unique.')
        tap(d,root,tabs[0])
        return root_for(d)
    def rows(self,d):
        root=self.matches_page(d)
        for _ in range(self.max_scrolls):
            before=geometry(root);swipe(d,'down');root=root_for(d)
            if geometry(root)==before: break
        else: raise RuntimeError('Could not reach the top of Matches.')
        heads=[n.get('text') for n in root.iter() if re.fullmatch(r'Your turn \(\d+\)',n.get('text',''))]
        if len(heads)!=1: raise RuntimeError('Your turn heading not found.')
        count=int(re.search(r'\d+',heads[0]).group())
        records={}
        for _ in range(self.max_scrolls):
            for m in match_rows(root,count): records[m['key']]={k:v for k,v in m.items() if k!='_node'}
            if len(records)==count: break
            if len(records)>count: raise RuntimeError('Match list changed while scanning.')
            before=geometry(root);swipe(d,'up');root=root_for(d)
            if geometry(root)==before: raise RuntimeError('Not all Your turn rows were accessible.')
        else: raise RuntimeError('Match list scroll limit reached.')
        result=list(records.values())
        if len({m['name'].casefold() for m in result})!=len(result):
            raise RuntimeError('Duplicate match names are not yet safely distinguishable.')
        return result
    def list_matches(self):
        with self.connect() as d:
            result=self.rows(d)
            capture_observation(d,ROOT/'captures'/'matches','co.hinge.app')
            return result
    def open(self,d,match):
        # Always re-resolve the saved row identity, even if a same-name chat
        # is already open. Header text alone is not a conversation identity.
        records=self.rows(d)
        if not any(m['key']==match['key'] for m in records): raise RuntimeError('Match list changed. Send Begin to refresh.')
        # rows() leaves the list near its end; search upward with fresh nodes.
        for _ in range(self.max_scrolls):
            root=root_for(d)
            candidates=[m for m in match_rows(root,len(records)) if m['key']==match['key']]
            if len(candidates)==1:
                tap(d,root,candidates[0]['_node']);header(root_for(d),match['name']);return
            if len(candidates)>1: raise RuntimeError('Ambiguous match row.')
            swipe(d,'down')
        raise RuntimeError('Selected match could not be exposed.')
    def read(self,d,match):
        self.open(d,match)
        # Seek oldest reachable content. No image interpretation or silent truncation.
        for step in range(self.max_scrolls):
            if step % 10 == 0: logging.info('Reading older history: scroll %s',step)
            root=root_for(d);header(root,match['name']);before=geometry(root)
            swipe(d,'down');top=root_for(d);header(top,match['name'])
            if geometry(top)==before: break
        else: raise RuntimeError('History start was not reached within the scroll limit.')
        # Preserve all opening-card text as labelled context, never infer who sent
        # a message that has no speaker label. UI boundary is not server completeness.
        opening=[]
        for n in top.iter():
            text=n.get('text','');rect=bounds(n.get('bounds'))
            if text and rect and 156<=rect[1]<1062 and not re.match(r'^(Mon|Tue|Wed|Thu|Fri|Sat|Sun),',text) and text not in ('Sent','Double tap to like a message'):
                opening.append({'sender':'opening_context','text':text})
        if not opening: raise RuntimeError('Opening conversation context is not exposed; full history cannot be verified.')
        history=messages(top,match['name'])
        for step in range(self.max_scrolls):
            if step % 10 == 0: logging.info('Merging history: scroll %s',step)
            root=root_for(d);before=geometry(root)
            swipe(d,'up');current=root_for(d);header(current,match['name'])
            view=messages(current,match['name'])
            if geometry(current)==before: break
            history=merge(history,view)
        else: raise RuntimeError('Latest conversation boundary was not reached.')
        if not history: raise RuntimeError('No labelled messages found.')
        if history[-1]['sender']!='match': raise RuntimeError('The latest message is already yours. No new incoming message to reply to.')
        all_messages=opening+history
        result={'messages':all_messages,'coverage':'ui_boundary_verified','fingerprint':fingerprint(all_messages)}
        folder,_=capture_observation(d,ROOT/'captures'/'matches','co.hinge.app')
        (folder/'conversation.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
        return result
    def history(self,match):
        with self.connect() as d:
            try: return self.read(d,match)
            except Exception:
                try: capture_observation(d,ROOT/'captures'/'matches','co.hinge.app')
                except Exception: pass
                raise
    def send(self,match,expected,text,claim):
        with self.connect() as d:
            current=self.read(d,match)
            if current['fingerprint']!=expected['fingerprint']: raise RuntimeError('Conversation changed before sending. Send Begin to refresh.')
            before=messages(root_for(d),match['name'])
            fields=d.find_elements('id',COMPOSER)
            if len(fields)!=1: raise RuntimeError('Reply editor is not unique.')
            field=fields[0]
            if field.text not in ('','Send a message'): raise RuntimeError('A draft already exists in Hinge; inspect it manually.')
            field.send_keys(text)
            if field.text!=text: raise RuntimeError('Entered reply does not match the selected text. Nothing sent.')
            root=root_for(d);header(root,match['name'])
            editor=next(n for n in root.iter() if n.get('resource-id')==COMPOSER)
            if bounds(editor.get('bounds'))[1] < d.get_window_size()['height']*.7:
                d.press_keycode(4)
                time.sleep(.5)
                root=root_for(d);header(root,match['name'])
            if messages(root,match['name'])!=before: raise RuntimeError('Messages changed while composing. Nothing sent.')
            controls=[n for n in root.iter() if n.get('content-desc')=='Send' and n.get('enabled')=='true']
            if len(controls)!=1: raise RuntimeError('Send control has not been uniquely verified. Draft left unsent.')
            # Claim is durable BEFORE the sole send click. A timeout never retries.
            if not claim(): raise RuntimeError('An attempt already exists for this conversation revision.')
            try:
                tap(d,root,controls[0])
                for _ in range(10):
                    root=root_for(d);header(root,match['name']);after=messages(root,match['name'])
                    expected_new={'sender':'me','text':text}
                    try: appended=merge(before,after)==before+[expected_new]
                    except RuntimeError: appended=False
                    editors=[n for n in root.iter() if n.get('resource-id')==COMPOSER]
                    cleared=len(editors)==1 and editors[0].get('text','') in ('','Send a message')
                    if appended and cleared:
                        capture_observation(d,ROOT/'captures'/'matches','co.hinge.app')
                        return 'sent'
                    time.sleep(.5)
            except Exception: return 'uncertain'
            return 'uncertain'
