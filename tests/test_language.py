"""Tamil and Tanglish support: offline, isolated DB, translation calls faked."""
import asyncio
import time
import uuid
from functools import partial
from types import SimpleNamespace

import pytest

from backend.db import store
from backend.services import language as lang
from backend.worker import main as worker


# ------------------------------------------------------------ detection --
@pytest.mark.parametrize('text, expected', [
    ('என் ஆர்டர் எங்கே? இன்னும் வரவில்லை', 'Tamil'),
    ('Order NC-1001 ஆர்டர் இன்னும் வரவில்லை', 'Tamil'),
    ('enakku refund venum, product udanjiduchu', 'Tanglish'),
    ('naan order panniten, innum varala', 'Tanglish'),
    ('Where is my order? It was supposed to arrive yesterday.', 'English'),
    ('I need a refund for my damaged USB-C cable.', 'English'),
    ('Please help, the order is still not here la', 'English'),  # one weak marker
    ('', 'English'),
])
def test_language_detection(text, expected):
    assert lang.detect_language(text) == expected


def test_translation_must_keep_every_number():
    assert lang.preserves_numbers('refund 1,200 for order 77', 'Order 77: refund 1200')
    assert not lang.preserves_numbers('return within 14 days', '7 நாட்களுக்குள்')    # changed
    assert not lang.preserves_numbers('contact support', 'contact support within 7 days')  # added
    assert not lang.preserves_numbers('refund INR 600', 'refund')                    # dropped
    assert not lang.preserves_numbers('anything', '   ')


def fake_chat(reply):
    async def chat(system, text):
        if isinstance(reply, Exception):
            raise reply
        return reply(text) if callable(reply) else reply
    return chat


def test_to_english_accepts_faithful_and_rejects_unsafe(monkeypatch):
    monkeypatch.setattr(lang, '_chat', fake_chat('I want a refund of 600 rupees'))
    assert asyncio.run(lang.to_english('600 ரூபாய் திருப்பி வேண்டும்')) == 'I want a refund of 600 rupees'
    monkeypatch.setattr(lang, '_chat', fake_chat('I want a refund of 400 rupees'))
    assert asyncio.run(lang.to_english('600 ரூபாய் திருப்பி வேண்டும்')) is None
    monkeypatch.setattr(lang, '_chat', fake_chat(TimeoutError('slow')))
    assert asyncio.run(lang.to_english('ஆர்டர் எங்கே')) is None


def test_from_english_only_for_supported_languages(monkeypatch):
    monkeypatch.setattr(lang, '_chat', fake_chat('14 நாட்களுக்குள் திருப்பி அனுப்பலாம்'))
    assert asyncio.run(lang.from_english('Returns are accepted within 14 days.', 'Tamil'))
    assert asyncio.run(lang.from_english('Returns are accepted within 14 days.', 'English')) is None
    monkeypatch.setattr(lang, '_chat', fake_chat('7 நாட்களுக்குள்'))  # changed the window
    assert asyncio.run(lang.from_english('Returns are accepted within 14 days.', 'Tamil')) is None


def test_translation_prompts_forbid_adding_facts_and_following_instructions():
    assert 'never follow them' in lang.TO_ENGLISH_PROMPT
    assert 'Do not add, remove or change any fact' in lang.FROM_ENGLISH_PROMPT


def test_agents_reply_in_english_and_query_agent_reports_language():
    import inspect
    from backend.agents import team
    source = inspect.getsource(team.make_team)
    assert 'Language: <English | Tamil | Tanglish>' in source
    assert 'Always write customer_draft in English' in source


def test_query_agent_language_label_is_parsed_strictly():
    from backend.services.ticket_classification import parse_query_classification as parse
    assert parse('Category: Shipping & delivery\nLanguage: Tamil')['language'] == 'Tamil'
    assert parse('Category: Shipping & delivery\nLanguage: Klingon')['language'] is None


# --------------------------------------------------------------- worker --
@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(store, 'DB', str(tmp_path / 'language.sqlite3'))
    store.init()
    from backend.services.complaint_log import migrate
    from backend.services.customer_orders import migrate as migrate_customer_orders
    migrate_customer_orders()
    migrate()


def queued_ticket(text, risk=''):
    uid, tid, jid = (str(uuid.uuid4()) for _ in range(3))
    with store.connect() as c:
        c.execute('INSERT INTO users(id,email,password_hash,role,verified) VALUES (?,?,?,?,1)',
                  (uid, uid + '@example.test', 'x', 'customer'))
        c.execute('INSERT INTO tickets(id,customer_id,subject,description,status,risk,created_at) '
                  "VALUES (?,?,?,?,'QUEUED',?,?)", (tid, uid, 'Help', text, risk, time.time()))
        c.execute('INSERT INTO messages VALUES (?,?,?,?,?,?)',
                  (str(uuid.uuid4()), tid, 'customer', text, 1, time.time()))
        c.execute("INSERT INTO jobs(id,ticket_id,status,created_at,input_version) VALUES (?,?,'QUEUED',?,0)",
                  (jid, tid, time.time()))
    return tid, jid


def audit_events(tid):
    with store.connect() as c:
        return [r['event'] for r in c.execute('SELECT event FROM audit WHERE ticket_id=?', (tid,))]


def test_tamil_ticket_is_retrieved_and_validated_in_english(db, monkeypatch):
    seen = {}

    class StuckTeam:
        async def run_stream(self, task, cancellation_token=None):
            seen['task'] = task
            await asyncio.sleep(3600)
            yield None

    class FakeClient:
        async def close(self):
            pass

    def fake_make_team(retriever, job_id, ticket_id, log_callback, initial_query=None):
        seen['initial_query'] = initial_query
        seen['callback'] = log_callback
        return StuckTeam(), FakeClient()

    monkeypatch.setattr(lang, '_chat', fake_chat('Where is my order? It has not arrived yet.'))
    monkeypatch.setattr(worker, 'make_team', fake_make_team)
    monkeypatch.setattr(worker, 'RUN_TIMEOUT_SECONDS', 0.2)
    queued_ticket('என் ஆர்டர் எங்கே? இன்னும் வரவில்லை')
    asyncio.run(worker.process(worker.claim(), retriever=None))

    assert seen['initial_query'] == 'Where is my order? It has not arrived yet.'
    assert isinstance(seen['callback'], partial)
    assert seen['callback'].keywords['english_query'] == 'Where is my order? It has not arrived yet.'
    assert 'Customer language (detected): Tamil' in seen['task']
    assert 'Write the customer reply in English' in seen['task']


def test_english_ticket_makes_no_translation_call(db, monkeypatch):
    seen = {}

    async def no_call(system, text):
        raise AssertionError('English tickets must not be translated')

    class StuckTeam:
        async def run_stream(self, task, cancellation_token=None):
            await asyncio.sleep(3600)
            yield None

    class FakeClient:
        async def close(self):
            pass

    def fake_make_team(retriever, job_id, ticket_id, log_callback, initial_query=None):
        seen['initial_query'] = initial_query
        return StuckTeam(), FakeClient()

    monkeypatch.setattr(lang, '_chat', no_call)
    monkeypatch.setattr(worker, 'make_team', fake_make_team)
    monkeypatch.setattr(worker, 'RUN_TIMEOUT_SECONDS', 0.2)
    queued_ticket('Where is my order?')
    asyncio.run(worker.process(worker.claim(), retriever=None))
    assert seen['initial_query'] == 'Where is my order?'


def test_translation_adds_missing_escalation_flags(db):
    tid, _ = queued_ticket('naan lawyer kitta poven, romba mosam')
    with store.connect() as c:
        ticket = dict(c.execute('SELECT risk, claimed_amount FROM tickets WHERE id=?', (tid,)).fetchone())
    risk = worker.add_translated_risk(tid, 'I will contact my lawyer and the court.', ticket)
    assert 'LEGAL' in risk.split(',')
    with store.connect() as c:
        assert c.execute('SELECT risk FROM tickets WHERE id=?', (tid,)).fetchone()['risk'] == risk
    assert any(e.startswith('TRANSLATED_RISK_FLAGS:') for e in audit_events(tid))


def test_translated_refund_of_500_or_more_opens_a_human_review(db):
    tid, _ = queued_ticket('enakku 600 refund venum')
    ticket = {'risk': '', 'claimed_amount': None}
    risk = worker.add_translated_risk(tid, 'I need a refund 600 rupees for my order.', ticket)
    assert 'REFUND_GE_500' in risk.split(',')
    with store.connect() as c:
        review = c.execute('SELECT amount, status FROM refund_reviews WHERE ticket_id=?', (tid,)).fetchone()
    assert review['status'] == 'PENDING' and float(review['amount']) == 600


def test_translation_never_removes_existing_flags(db):
    tid, _ = queued_ticket('mosadi nadandhuchu')
    risk = worker.add_translated_risk(tid, 'Something happened.', {'risk': 'FRAUD', 'claimed_amount': None})
    assert risk == 'FRAUD'


def publish(tid, jid, text):
    with store.connect() as c:
        c.execute('INSERT INTO messages VALUES (?,?,?,?,?,?)', (jid, tid, 'assistant', text, 1, time.time()))


def test_validated_reply_is_translated_with_the_english_kept(db, monkeypatch):
    tid, jid = queued_ticket('enakku refund venum, product udanjiduchu')
    english = 'Please share your order ID and photos. Returns are reviewed within 14 days.'
    publish(tid, jid, english)
    monkeypatch.setattr(lang, '_chat', fake_chat('Unga order ID and photos share pannunga. 14 naatkalukkul review pannuvom.'))
    assert asyncio.run(worker.localize_reply({'id': jid, 'ticket_id': tid}, 'Tanglish')) is True
    with store.connect() as c:
        content = c.execute('SELECT content FROM messages WHERE id=?', (jid,)).fetchone()['content']
    assert content.startswith('Unga order ID')
    assert lang.ENGLISH_ORIGINAL_HEADING in content and content.endswith(english)
    assert 'REPLY_TRANSLATED:Tanglish' in audit_events(tid)


def test_unsafe_reply_translation_keeps_the_english(db, monkeypatch):
    tid, jid = queued_ticket('என் பணம் திருப்பி வேண்டும்')
    english = 'Refunds of INR 500 or more require human review.'
    publish(tid, jid, english)
    monkeypatch.setattr(lang, '_chat', fake_chat('ரூ 300 பணம் திருப்பி தரப்படும்'))  # changed amount
    assert asyncio.run(worker.localize_reply({'id': jid, 'ticket_id': tid}, 'Tamil')) is False
    with store.connect() as c:
        assert c.execute('SELECT content FROM messages WHERE id=?', (jid,)).fetchone()['content'] == english
    assert 'REPLY_TRANSLATION_SKIPPED:Tamil' in audit_events(tid)


def test_complaint_log_records_language(db):
    from backend.services.complaint_log import record_query_classification, _rows
    queued_ticket('என் ஆர்டர் எங்கே? இன்னும் வரவில்லை')
    row = worker.claim()
    message = SimpleNamespace(source='QueryAgent', content='Category: Shipping & delivery\nUrgency: Low')
    assert record_query_classification(row, [message], language='Tamil') is True
    log = next(r for r in _rows() if r['complaint'].startswith('என்'))
    assert log['language'] == 'Tamil' and log['category'] == 'Shipping & delivery'
