"""Job inspection and cancellation."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from app.api.jobs import jobs

router = APIRouter(tags=["jobs"])


@router.get("/api/jobs")
def list_jobs(limit: int = 20) -> dict[str, Any]:
    return {"jobs": [job.model_dump(mode="json") for job in jobs.list(limit=limit)]}


@router.get("/api/jobs/{job_id}")
def get_job(job_id: str) -> dict[str, Any]:
    job = jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Unknown job {job_id}")
    return job.model_dump(mode="json")


@router.post("/api/jobs/{job_id}/cancel")
def cancel_job(job_id: str) -> dict[str, Any]:
    if jobs.get(job_id) is None:
        raise HTTPException(status_code=404, detail=f"Unknown job {job_id}")
    return {"job_id": job_id, "cancelled": jobs.cancel(job_id)}


__all__ = ["router"]
