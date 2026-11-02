import json
from pathlib import Path
from unittest.mock import patch
import pytest

ROOT = Path(__file__).resolve().parents[1]

def test_20_plus_real_pdf_cases():
    from pypdf import PdfReader
    cases=json.loads((ROOT/'eval/golden_cases.json').read_text())
    assert len(cases)>=20
    for case in cases:
        pdf=ROOT/'policies'/case['expected_source']
        assert pdf.is_file()
        content=' '.join(p.extract_text() or '' for p in PdfReader(str(pdf)).pages)
        assert case['evidence_phrase'].casefold() in content.casefold(),case['id']

def test_citation_metadata_has_page_and_department():
    from backend.rag.hybrid import citation_metadata
    item=citation_metadata({'id':'abc','source':'01_Shipping_and_Delivery_SOP.pdf','page':3,'chunk':2,'visibility':'customer'})
    assert item['page']==3 and item['department']=='Shipping & Delivery'

def test_gmail_mailer_no_plaintext_file():
    from backend.auth.mailer import send_token
    with patch.dict('os.environ', {'FIXFLOW_SMTP_USER':'test@gmail.com','FIXFLOW_SMTP_APP_PASSWORD':'app-password'}):
        with patch('smtplib.SMTP_SSL') as smtp:
            send_token('customer@example.com','verify','SECRET_CODE')
            msg=smtp.return_value.__enter__.return_value.send_message.call_args.args[0]
            assert 'SECRET_CODE' in msg.get_content()
            assert msg['To']=='customer@example.com'
    assert 'local_mailbox.txt' not in (ROOT/'backend/main.py').read_text()

def test_password_reset_policy_is_enforced():
    from backend.main import validate_registration
    from fastapi import HTTPException
    with pytest.raises(HTTPException):
        validate_registration('x@example.com','weakpassword')

def test_retriever_reuses_bm25():
    import inspect
    from backend.rag.hybrid import HybridRetriever
    source=inspect.getsource(HybridRetriever.search)
    assert 'BM25Okapi(' not in source
    assert 'faiss.read_index(' not in source

def test_frontend_api_is_configurable():
    assert 'FIXFLOW_API_URL' in (ROOT/'frontend/api_client.py').read_text()
