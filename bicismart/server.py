"""API y web de BiciSmart."""

import threading
from contextlib import asynccontextmanager
from dataclasses import replace
from pathlib import Path

from fastapi import FastAPI, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import enrich, geocode, routing, trace, wikidata
from .errors import BadRequest, NotFound
from .geocode import Endpoint

STATIC = Path(__file__).parent / "static"
EXPLORE_RADII = (300, 500, 1000)  # metros


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


def _endpoint(endpoint: Endpoint) -> dict:
    return {
        "label": endpoint.label,
        "lat": endpoint.lat,
        "lon": endpoint.lon,
        "source": endpoint.source,
        "note": endpoint.note,
        "approximate": endpoint.approximate,
        "fixedStations": bool(endpoint.stations),
    }


def _contributions(origin: Endpoint, destination: Endpoint, cards: list[dict], nearby: list[dict]) -> list[dict]:
    """Qué aporta cada fuente a esta ruta y qué se podría hacer solo con el CSV de BiciMAD.

    `csv` lleva el veredicto ("yes", "partial", "no" o None si no se aplica) y el texto que se
    muestra.
    """
    rows = [{
        "what": "Estaciones utilizables, posición y bases",
        "source": "bicimad",
        "how": "bm:BikeStation, bm:hasPoint, bm:hasState",
        "csv": {"verdict": "yes", "text": "Sí"},
    }]
    for role, endpoint in (("Origen", origin), ("Destino", destination)):
        if endpoint.csv:
            rows.append({
                "what": f"{role}: {endpoint.label}",
                "source": endpoint.source,
                "how": endpoint.note,
                "csv": endpoint.csv,
            })
    if any(b["source"] == "callejero" for card in cards for b in card["blocks"]):
        rows.append({
            "what": "Calle, distrito y código postal de las estaciones",
            "source": "callejero",
            "how": "bm:locatedOnStreet → bm:Street (bm:districtCode, bm:postalCode)",
            "csv": {"verdict": "no", "text": "No: el CSV de BiciMAD no tiene distrito ni código postal."},
        })
    if any(b["source"] == "wikidata" for card in cards for b in card["blocks"]):
        rows.append({
            "what": "Historia de la calle: a quién homenajea, imagen",
            "source": "wikidata",
            "how": "bm:Street owl:sameAs → Wikidata (P138, P18)",
            "csv": {"verdict": "no", "text": "No"},
        })
    if nearby:
        rows.append({
            "what": "Lugares de interés junto al destino",
            "source": "wikidata",
            "how": "Wikidata (P31/P279*, P625) y distancia a la estación",
            "csv": {"verdict": "no", "text": "No"},
        })
    rows.append({
        "what": "Tiempos y trazado por las calles",
        "source": "osrm",
        "how": "OSRM sobre OpenStreetMap (servicio externo)",
        # Sin veredicto: OSRM no usa el grafo, así que daría lo mismo solo con el CSV.
        "csv": {"verdict": None, "text": "Igual: no depende del grafo"},
    })
    return rows


@app.get("/api/route")
def route(
    origin_ref: str = Query(alias="from"),
    destination_ref: str = Query(alias="to"),
    origin_label: str | None = Query(None, alias="fromLabel"),
    destination_label: str | None = Query(None, alias="toLabel"),
):
    queries = trace.start()
    origin = geocode.resolve(origin_ref, origin_label)
    destination = geocode.resolve(destination_ref, destination_label)
    plan = routing.plan(origin, destination)
    cards = []
    if plan["bike"]:
        # Fichas de la estación de salida y la de llegada, cada una con 3 alternativas
        # utilizables por si no hay bicis o huecos.
        cards = [
            enrich.station_card(plan["bike"][key]["id"], nearest=3, usable_only=True)
            for key in ("fromStation", "toStation")
        ]
    final_leg = plan["bike"]["legs"][-1] if plan["recommended"] == "bike" else plan["walk"]
    nearby = [
        place for place in enrich.nearby_places(*final_leg["geometry"][-1])
        if f"place:{place['qid']}" != destination_ref
    ]

    return {
        **plan,
        "origin": _endpoint(origin),
        "destination": _endpoint(destination),
        "cards": cards,
        "nearby": nearby,
        "contributions": _contributions(origin, destination, cards, nearby),
        "queries": queries,
    }


def _explore_contributions(center: Endpoint) -> list[dict]:
    """Qué aporta cada fuente a la exploración de un lugar frente al CSV de BiciMAD."""
    rows = []
    if center.csv:
        rows.append({
            "what": f"Centro: {center.label}",
            "source": center.source,
            "how": center.note,
            "csv": center.csv,
        })
    rows.append({
        "what": "Estaciones dentro del radio: posición, bases y estado",
        "source": "bicimad",
        "how": "bm:BikeStation, bm:hasPoint, bm:hasState y distancia en línea recta",
        "csv": {"verdict": "yes", "text": "Sí, si se conoce el centro"},
    })
    return rows


@app.get("/api/explore")
def explore(ref: str, label: str | None = None, radius: str = "500"):
    """Estaciones a 300, 500 o 1000 m de un lugar, de la más cercana a la más lejana (FR2).

    `ref` es una referencia de `geocode` (calle, lugar, estación o punto). Si es una estación,
    no cuenta en la lista: es el centro. Si es una calle con estaciones, el radio se mide desde
    cada una de ellas (`centers`), porque una calle larga no tiene un punto medio útil.
    """
    queries = trace.start()
    if not radius.isdigit() or int(radius) not in EXPLORE_RADII:
        raise BadRequest("El radio tiene que ser de 300, 500 o 1000 m.")
    radius_m = int(radius)
    center = geocode.resolve(ref, label)

    kind, _, value = ref.partition(":")
    street_stations = center.stations if kind == "street" and center.stations else []
    if street_stations:
        # La nota de geocode habla de rutas; aquí explica desde dónde se mide el radio.
        count = len(street_stations)
        center = replace(center, note=(
            f"La calle tiene {count} "
            + ("estación BiciMAD utilizable" if count == 1 else "estaciones BiciMAD utilizables")
            + " (bm:locatedOnStreet): las distancias se miden desde "
            + ("ella." if count == 1 else "la más cercana de ellas.")
        ))
        centers = [(s["lat"], s["lon"]) for s in street_stations]
    else:
        centers = [(center.lat, center.lon)]

    stations = enrich.stations_within(centers, radius_m, exclude=value if kind == "station" else None)
    on_street = {s["id"] for s in street_stations}
    for s in stations:
        s["onStreet"] = s["id"] in on_street

    return {
        "center": _endpoint(center),
        "centers": [list(point) for point in centers],
        "radius": radius_m,
        "stations": stations,
        "contributions": _explore_contributions(center),
        "queries": queries,
    }


@app.get("/api/station/{station_id}")
def station(station_id: str):
    queries = trace.start()
    card = enrich.station_card(station_id)
    return {
        "station": card,
        "nearby": enrich.nearby_places(card["lat"], card["lon"]),
        "queries": queries,
    }
