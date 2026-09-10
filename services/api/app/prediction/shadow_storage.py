"""Append-only local research journal; no dependency on the application's database."""

import json
import os
from collections.abc import Iterator
from pathlib import Path
from typing import Protocol

from app.prediction.shadow_models import canonical


class ShadowJournalRepository(Protocol):
    def append(self, kind: str, key: str, payload) -> None: ...

    def records(self, kind: str | None = None) -> Iterator[dict]: ...


class InMemoryShadowJournal:
    def __init__(self):
        self._records: list[str] = []

    def append(self, kind: str, key: str, payload) -> None:
        self._records.append(canonical(dict(kind=kind, key=key, payload=payload)))

    def records(self, kind: str | None = None) -> Iterator[dict]:
        for text in self._records:
            row = json.loads(text)
            if kind is None or row["kind"] == kind:
                yield row


class JsonlShadowJournal:
    """Exclusive new file per experiment. Interrupted files are readable, never resumed."""

    def __init__(self, path: Path):
        self.path = path
        self._file = path.open("x", encoding="utf-8", newline="\n")

    def append(self, kind: str, key: str, payload) -> None:
        self._file.write(canonical(dict(kind=kind, key=key, payload=payload)) + "\n")
        self._file.flush()
        os.fsync(self._file.fileno())

    def records(self, kind: str | None = None) -> Iterator[dict]:
        yield from self.read(self.path, kind)

    @staticmethod
    def read(path: Path, kind: str | None = None) -> Iterator[dict]:
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                # Corruption/truncated tail must be investigated, not silently skipped.
                row = json.loads(line)
                if kind is None or row["kind"] == kind:
                    yield row

    def close(self):
        self._file.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
