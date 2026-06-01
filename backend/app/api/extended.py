from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from ..bootstrap import Container
from ..schemas import (
    GlossaryResponse,
    GlossaryUpsertRequest,
    KnowledgeBaseStatusResponse,
    PersonMergeRequest,
    PersonRegistryResponse,
    PersonSplitRequest,
)
from ..services.knowledge_base import OfflineKnowledgeBase

router = APIRouter(prefix="/api/v1", tags=["extended"])


def _container(request: Request) -> Container:
    return request.app.state.container  # type: ignore[attr-defined]


@router.get("/glossary", response_model=GlossaryResponse)
def list_glossary(request: Request) -> GlossaryResponse:
    container = _container(request)
    return GlossaryResponse(items=container.repository.list_glossary_terms())


@router.post("/glossary", response_model=GlossaryResponse)
def upsert_glossary(payload: GlossaryUpsertRequest, request: Request) -> GlossaryResponse:
    container = _container(request)
    container.repository.upsert_glossary_term(
        source=payload.source.strip(),
        target=payload.target.strip(),
        locale=payload.locale.strip() or "global",
    )
    return GlossaryResponse(items=container.repository.list_glossary_terms())


@router.delete("/glossary/{term_id}", response_model=GlossaryResponse)
def delete_glossary(term_id: str, request: Request) -> GlossaryResponse:
    container = _container(request)
    ok = container.repository.delete_glossary_term(term_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Glossary term not found")
    return GlossaryResponse(items=container.repository.list_glossary_terms())


@router.get("/person-registry", response_model=PersonRegistryResponse)
def get_person_registry(request: Request) -> PersonRegistryResponse:
    container = _container(request)
    return PersonRegistryResponse(items=container.repository.list_person_registry())


@router.post("/person-registry/merge", response_model=PersonRegistryResponse)
def merge_person_registry(payload: PersonMergeRequest, request: Request) -> PersonRegistryResponse:
    container = _container(request)
    ok = container.repository.merge_person_registry(
        payload.source_registry_id, payload.target_registry_id
    )
    if not ok:
        raise HTTPException(status_code=404, detail="Source or target registry entry not found")
    return PersonRegistryResponse(items=container.repository.list_person_registry())


@router.post("/person-registry/split", response_model=PersonRegistryResponse)
def split_person_registry(payload: PersonSplitRequest, request: Request) -> PersonRegistryResponse:
    container = _container(request)
    ok = container.repository.split_person_registry_alias(payload.registry_id, payload.alias_to_split)
    if not ok:
        raise HTTPException(status_code=400, detail="Unable to split person registry alias")
    return PersonRegistryResponse(items=container.repository.list_person_registry())


@router.get("/kb/status", response_model=KnowledgeBaseStatusResponse)
def get_kb_status(request: Request) -> KnowledgeBaseStatusResponse:
    container = _container(request)
    kb = OfflineKnowledgeBase(container.repository, container.storage.kb_dir)
    return KnowledgeBaseStatusResponse(status=kb.status())


@router.post("/kb/reindex", response_model=KnowledgeBaseStatusResponse)
def reindex_kb(request: Request) -> KnowledgeBaseStatusResponse:
    container = _container(request)
    kb = OfflineKnowledgeBase(container.repository, container.storage.kb_dir)
    status = kb.reindex()
    return KnowledgeBaseStatusResponse(status=status)
