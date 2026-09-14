"""
Punctul UNIC de comutare între storage local și Firestore (secțiunea 16, R-1602).

Restul aplicației importă doar `get_repository()` de aici și lucrează prin
interfața `Repository` din repository.py — nu vede niciodată SQL sau Firestore
direct. Când se trece pe server, se schimbă STORAGE_BACKEND=firestore în .env
și nimic altceva în cod nu trebuie atins.
"""
from __future__ import annotations

from functools import lru_cache

from app.config import MEDIU
from app.storage.repository import Repository


@lru_cache(maxsize=1)
def get_repository() -> Repository:
    if MEDIU.storage_backend == "firestore":
        from app.storage.firestore_repo import FirestoreRepository
        return FirestoreRepository()
    from app.storage.sqlite_repo import SqliteRepository
    return SqliteRepository(MEDIU.sqlite_path_absolut())
