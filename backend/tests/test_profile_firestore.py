from unittest.mock import MagicMock
import importlib
import os
import sys

# Make sure backend package is importable
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))
app_module = importlib.import_module('backend.app')


def setup_mock(monkeypatch):
    mock_client = MagicMock()
    collection = mock_client.collection.return_value
    doc_ref = collection.document.return_value
    transaction = mock_client.transaction.return_value
    monkeypatch.setattr(app_module, 'firestore_client', mock_client)
    return mock_client, doc_ref, transaction


def test_create_read_update_profile(monkeypatch):
    mock_client, doc_ref, transaction = setup_mock(monkeypatch)

    data = {'name': 'Test User'}
    app_module.create_user_profile('uid123', data)
    transaction.set.assert_called_once_with(doc_ref, data)
    transaction.commit.assert_called_once()

    doc_ref.get.return_value.to_dict.return_value = data
    profile = app_module.get_user_profile('uid123')
    assert profile == data

    updated = {'name': 'Updated User'}
    doc_ref.get.return_value.to_dict.return_value = updated
    app_module.update_user_profile('uid123', updated)
    transaction.update.assert_called_once_with(doc_ref, updated)
    assert app_module.get_user_profile('uid123') == updated
