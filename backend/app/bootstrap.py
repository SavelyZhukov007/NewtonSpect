from __future__ import annotations

from dataclasses import dataclass

from .config import Settings, get_settings
from .db import init_db
from .repository import JobRepository
from .services.paths import StorageService
from .services.pipeline import PipelineRunner


@dataclass
class Container:
    settings: Settings
    repository: JobRepository
    storage: StorageService

    def build_pipeline(self) -> PipelineRunner:
        return PipelineRunner(
            settings=self.settings,
            repository=self.repository,
            storage=self.storage,
        )


def build_container() -> Container:
    settings = get_settings()
    init_db(settings.db_path)
    repository = JobRepository(settings.db_path)
    storage = StorageService(settings.storage_root)
    return Container(settings=settings, repository=repository, storage=storage)
