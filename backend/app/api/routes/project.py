"""Project files: save / open ``.svaproj`` and the rolling session snapshot.

Everything here is synchronous file I/O on small JSON documents, so these are
plain routes instead of queued jobs (which would serialise behind a render).
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.schemas.project import (
    ProjectDeleteResult,
    ProjectEnvelope,
    ProjectListResult,
    ProjectOpenRequest,
    ProjectSaveRequest,
)
from app.services import project_service

router = APIRouter(tags=["project"])


def _fail(exc: Exception, status: int = 422) -> HTTPException:
    return HTTPException(status_code=status, detail=str(exc))


@router.post("/api/project/save", response_model=ProjectEnvelope)
def save(request: ProjectSaveRequest) -> ProjectEnvelope:
    try:
        return project_service.save_project(request.project, request.path)
    except project_service.ProjectError as exc:
        raise _fail(exc) from exc
    except OSError as exc:
        raise _fail(exc, 500) from exc


@router.post("/api/project/open", response_model=ProjectEnvelope)
def open_project(request: ProjectOpenRequest) -> ProjectEnvelope:
    try:
        return project_service.open_project(request.path)
    except FileNotFoundError as exc:
        raise _fail(exc, 404) from exc
    except project_service.ProjectError as exc:
        raise _fail(exc) from exc


@router.get("/api/project/list", response_model=ProjectListResult)
def list_projects() -> ProjectListResult:
    return project_service.list_projects()


@router.delete("/api/project/file", response_model=ProjectDeleteResult)
def delete_project(path: str) -> ProjectDeleteResult:
    cleared = project_service.delete_project(path)
    if not cleared:
        raise HTTPException(status_code=404, detail=f"Project not found: {path}")
    return ProjectDeleteResult(cleared=True)


@router.get("/api/project/session", response_model=ProjectEnvelope)
def read_session() -> ProjectEnvelope:
    """The autosave to resume; an empty envelope when there is none."""
    return project_service.read_session() or ProjectEnvelope()


@router.put("/api/project/session", response_model=ProjectEnvelope)
def write_session(request: ProjectSaveRequest) -> ProjectEnvelope:
    try:
        return project_service.write_session(request.project)
    except project_service.ProjectError as exc:
        raise _fail(exc) from exc
    except OSError as exc:
        raise _fail(exc, 500) from exc


@router.delete("/api/project/session", response_model=ProjectDeleteResult)
def clear_session() -> ProjectDeleteResult:
    return ProjectDeleteResult(cleared=project_service.clear_session())


__all__ = ["router"]
