from __future__ import annotations

import hashlib
import json
from pathlib import Path

from ..repository import JobRepository
from ..schemas import KnowledgeBaseStatus


SUPPORTED_KB_EXTENSIONS = {".txt", ".md", ".json", ".csv", ".log"}


class OfflineKnowledgeBase:
    def __init__(self, repository: JobRepository, kb_root: Path) -> None:
        self.repository = repository
        self.kb_root = kb_root
        self.kb_root.mkdir(parents=True, exist_ok=True)

    def reindex(self) -> KnowledgeBaseStatus:
        self.repository.reset_kb()
        for path in self._iter_files():
            content = self._read_text(path)
            if not content.strip():
                continue
            checksum = hashlib.sha256(content.encode("utf-8", errors="ignore")).hexdigest()
            doc_id = self.repository.add_kb_document(str(path), checksum)
            for order, chunk in enumerate(self._chunk_text(content), start=1):
                chunk_id = self.repository.add_kb_chunk(doc_id, order, chunk, token_count=len(chunk.split()))
                self.repository.set_kb_chunk_meta(
                    chunk_id,
                    hash_value=hashlib.sha256(chunk.encode("utf-8", errors="ignore")).hexdigest(),
                    vector_dim=0,
                )
        docs, chunks, indexed_at = self.repository.kb_status()
        return KnowledgeBaseStatus(
            documents=docs,
            chunks=chunks,
            indexed_at=indexed_at,
            kb_root=str(self.kb_root),
        )

    def status(self) -> KnowledgeBaseStatus:
        docs, chunks, indexed_at = self.repository.kb_status()
        return KnowledgeBaseStatus(
            documents=docs,
            chunks=chunks,
            indexed_at=indexed_at,
            kb_root=str(self.kb_root),
        )

    def _iter_files(self) -> list[Path]:
        files: list[Path] = []
        for path in self.kb_root.rglob("*"):
            if not path.is_file():
                continue
            if path.suffix.lower() not in SUPPORTED_KB_EXTENSIONS:
                continue
            files.append(path)
        return sorted(files)

    @staticmethod
    def _read_text(path: Path) -> str:
        if path.suffix.lower() == ".json":
            try:
                payload = json.loads(path.read_text(encoding="utf-8", errors="ignore"))
                return json.dumps(payload, ensure_ascii=False, indent=2)
            except json.JSONDecodeError:
                return path.read_text(encoding="utf-8", errors="ignore")
        return path.read_text(encoding="utf-8", errors="ignore")

    @staticmethod
    def _chunk_text(text: str, chunk_size: int = 900) -> list[str]:
        normalized = " ".join(text.split())
        if len(normalized) <= chunk_size:
            return [normalized]
        chunks: list[str] = []
        cursor = 0
        while cursor < len(normalized):
            chunks.append(normalized[cursor : cursor + chunk_size])
            cursor += chunk_size
        return chunks
