"""Offline structural and approval-boundary regression tests; no API calls."""
from pathlib import Path
import ast
import os
import pytest
from backend.rag.customer_policy import approved_passages, is_approved_customer_passage
from backend.rag.customer_runtime import customer_mode

ROOT = Path(__file__).resolve().parents[1]


def test_approved_identity_and_tampering():
    docs = approved_passages()
    assert len(docs) == len({d['id'] for d in docs}) == 12
    for doc in docs:
        assert is_approved_customer_passage(doc)
        for field in ('source', 'page', 'text', 'version', 'visibility', 'approval'):
            bad = dict(doc)
            bad[field] = 'forged'
            assert not is_approved_customer_passage(bad)


def test_runtime_mode_fail_closed(monkeypatch):
    monkeypatch.setenv('FIXFLOW_CUSTOMER_INDEX_MODE', 'unknown')
    with pytest.raises(ValueError):
        customer_mode()
    monkeypatch.setenv('FIXFLOW_CUSTOMER_INDEX_MODE', 'approved_only')
    assert customer_mode() == 'approved_only'


def test_runtime_entrypoints_connected():
    for path in ('backend/services/help_center.py', 'backend/worker/main.py'):
        source = (ROOT / path).read_text(encoding='utf-8')
        assert 'create_customer_retriever()' in source
        ast.parse(source)
    catalog = (ROOT / 'backend/services/citation_catalog.py').read_text(encoding='utf-8')
    assert 'verify_index(DEFAULT_INDEX)' in catalog
    assert 'customer_mode() == "approved_only"' in catalog


def test_guard_retains_exact_approved_safety_excerpt():
    source = (ROOT / 'backend/services/grounding_guard.py').read_text(encoding='utf-8')
    assert 'if is_approved_customer_passage(passage):' in source
    assert 'safe.append(dict(passage))' in source


def test_no_implicit_index_build_or_activation():
    runtime = (ROOT / 'backend/rag/customer_runtime.py').read_text(encoding='utf-8')
    assert 'build_staging(' not in runtime
    assert 'activate(' not in runtime
    assert 'rollback(' not in runtime


def test_temp_directory_activation_and_rollback(monkeypatch, tmp_path):
    from backend.rag import customer_index
    monkeypatch.setattr(customer_index, 'verify_index', lambda _: ({}, None))
    active, stage, backup = (tmp_path / n for n in ('active', 'stage', 'backup'))
    active.mkdir()
    (active / 'marker').write_text('old')
    stage.mkdir()
    (stage / 'marker').write_text('new')
    with pytest.raises(PermissionError):
        customer_index.activate(stage, active_dir=active, backup_dir=backup)
    assert (active / 'marker').read_text() == 'old'
    result = customer_index.activate(stage, active_dir=active, backup_dir=backup, approved=True)
    assert (active / 'marker').read_text() == 'new'
    assert (backup / 'marker').read_text() == 'old'
    with pytest.raises(PermissionError):
        customer_index.rollback(active_dir=active, backup_dir=backup)
    result = customer_index.rollback(active_dir=active, backup_dir=backup, approved=True)
    assert (active / 'marker').read_text() == 'old'
    assert (Path(result['displaced']) / 'marker').read_text() == 'new'


def test_activation_failure_restores_old_index(monkeypatch, tmp_path):
    from backend.rag import customer_index
    active, stage, backup = (tmp_path / n for n in ('active', 'stage', 'backup'))
    active.mkdir()
    (active / 'marker').write_text('old')
    stage.mkdir()
    (stage / 'marker').write_text('new')
    def verify(path):
        if Path(path) == active:
            raise ValueError('simulated verification failure')
        return {}, None
    monkeypatch.setattr(customer_index, 'verify_index', verify)
    with pytest.raises(ValueError, match='simulated'):
        customer_index.activate(stage, active_dir=active, backup_dir=backup, approved=True)
    assert (active / 'marker').read_text() == 'old'
