"""Firestore helper functions for NutriApp backend."""
from __future__ import annotations

import os
from typing import Any, Dict

try:
    from google.cloud import firestore  # type: ignore
except Exception:  # pragma: no cover - allow missing dependency
    firestore = None  # type: ignore


if firestore:
    firestore_client = firestore.Client(project=os.getenv("GCP_PROJECT_ID"))
else:  # pragma: no cover - fallback for missing library
    from unittest.mock import MagicMock  # type: ignore
    firestore_client = MagicMock()


def get_user_profile(uid: str) -> Dict[str, Any]:
    """Return user profile stored in Firestore."""
    doc_ref = firestore_client.collection("user_profiles").document(uid)
    doc = doc_ref.get()
    return doc.to_dict() or {}


def create_user_profile(uid: str, data: Dict[str, Any]) -> Dict[str, Any]:
    """Create a new user profile inside a transaction."""
    doc_ref = firestore_client.collection("user_profiles").document(uid)
    transaction = firestore_client.transaction()
    transaction.set(doc_ref, data)
    transaction.commit()
    return data


def update_user_profile(uid: str, data: Dict[str, Any]) -> Dict[str, Any]:
    """Update an existing user profile atomically."""
    doc_ref = firestore_client.collection("user_profiles").document(uid)
    transaction = firestore_client.transaction()
    transaction.update(doc_ref, data)
    transaction.commit()
    updated = doc_ref.get().to_dict() or {}
    return updated
