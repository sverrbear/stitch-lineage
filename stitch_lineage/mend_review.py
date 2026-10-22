"""The browser/runtime seam: no Metabase client or planning imports in the app."""

from typing import Any, Protocol


class MendReviewError(Exception):
    def __init__(self, message: str, status_code: int = 409):
        super().__init__(message)
        self.status_code = status_code


class MendReview(Protocol):
    def context(self) -> dict[str, Any]: ...

    def preview(self, renames: dict[str, str], actions: list[str]) -> dict[str, Any]: ...

    def apply(self, preview_id: str) -> dict[str, Any]: ...

    def result(self, preview_id: str) -> dict[str, Any]: ...
