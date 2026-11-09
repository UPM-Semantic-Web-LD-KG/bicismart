"""API y web de BiciSmart.

Los endpoints solo abren el registro de consultas (`trace`) y piden la respuesta a geocode,
enrich o explain. NotFound y BadRequest (errors.py) se responden aquí con 404 y 400.
"""

import threading
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import enrich, explain, geocode, trace, wikidata
from .errors import BadRequest, NotFound

STATIC = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(_app: FastAPI):
    threading.Thread(target=wikidata.prefetch, daemon=True).start()
    yield


app = FastAPI(title="BiciSmart", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.exception_handler(NotFound)
async def not_found(_request: Request, error: NotFound) -> JSONResponse:
    return JSONResponse({"detail": str(error)}, status_code=404)


@app.exception_handler(BadRequest)
async def bad_request(_request: Request, error: BadRequest) -> JSONResponse:
    return JSONResponse({"detail": str(error)}, status_code=400)


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/api/status")
def status():
    return {**wikidata.status, "placeCount": len(wikidata.places())}


@app.get("/api/stations")
def stations():
    return [enrich.station_summary(s) for s in geocode.all_stations()]


@app.get("/api/search")
def search(q: str, lat: float | None = None, lon: float | None = None):
    queries = trace.start()
    near = (lat, lon) if lat is not None and lon is not None else None
    return {"results": geocode.search(q, near), "queries": queries}


@app.get("/api/route")
def route(
    origin_ref: str = Query(alias="from"),
    destination_ref: str = Query(alias="to"),
    origin_label: str | None = Query(None, alias="fromLabel"),
    destination_label: str | None = Query(None, alias="toLabel"),
):
    queries = trace.start()
    answer = explain.route(origin_ref, destination_ref, origin_label, destination_label)
    return {**answer, "queries": queries}


@app.get("/api/explore")
def explore(ref: str, label: str | None = None, radius: str = "500"):
    """Estaciones a 300, 500 o 1000 m de un lugar (FR2), ver `explain.explore`."""
    queries = trace.start()
    return {**explain.explore(ref, label, radius), "queries": queries}


@app.get("/api/station/{station_id}")
def station(station_id: str):
    queries = trace.start()
    return {**explain.station(station_id), "queries": queries}
