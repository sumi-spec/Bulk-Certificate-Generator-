"""API v1 Router Aggregator."""

from fastapi import APIRouter

from app.api.v1.endpoints.certificates import router as certificates_router
from app.api.v1.endpoints.jobs import router as jobs_router

api_v1_router = APIRouter()
api_v1_router.include_router(jobs_router)
api_v1_router.include_router(certificates_router)
