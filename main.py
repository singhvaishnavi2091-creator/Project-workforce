import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.config import settings
from app.database import Base, SessionLocal, engine
from app.routers import (
    attendance,
    auth,
    availability,
    duty_assignments,
    duty_types,
    org_units,
    out_of_unit,
    personnel,
    ranks,
    standing_appointments,
)
from app.seed import seed_default_admin


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        seed_default_admin(db)
    finally:
        db.close()
    yield


app = FastAPI(title="UMMS API", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(Exception)
async def handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
    # Unhandled exceptions raised past this point in Starlette's default
    # error path skip CORSMiddleware, so the browser reports an opaque
    # "Failed to fetch" instead of the real 500 + message. Handling it here
    # keeps the response inside the normal (CORS-aware) path.
    logging.exception("Unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})

app.include_router(attendance.router)
app.include_router(auth.router)
app.include_router(availability.router)
app.include_router(duty_assignments.router)
app.include_router(duty_types.router)
app.include_router(org_units.router)
app.include_router(out_of_unit.router)
app.include_router(personnel.router)
app.include_router(ranks.router)
app.include_router(standing_appointments.router)
