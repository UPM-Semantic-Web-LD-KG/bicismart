"""Consultas a Wikidata (query.wikidata.org) con caché en disco en `data/`.

- Calles: las enlazadas desde el grafo con owl:sameAs (coordenadas, imagen, a quién homenajean).
- Lugares de interés de Madrid por categoría (P31/P279*), para buscar destinos por nombre o tipo.
"""

import json
import logging
import re
import threading
from string import Template

import httpx

from . import graph, trace

log = logging.getLogger(__name__)

ENDPOINT = "https://query.wikidata.org/sparql"
USER_AGENT = "BiciSmart/0.1 (UPM Semantic Web coursework)"
ENTITY = "http://www.wikidata.org/entity/"
DATA = graph.ROOT / "data"
STREETS_CACHE = DATA / "wikidata-streets.json"
PLACES_CACHE = DATA / "wikidata-places.json"

# Centro y radio de la búsqueda de lugares: Puerta del Sol, cubre todas las estaciones.
CENTER = (40.4168, -3.7038)
RADIUS_KM = 12

# clave de búsqueda -> (etiqueta, clase de Wikidata)
CATEGORIES = {
    "museo": ("Museo", "Q33506"),
    "parque": ("Parque", "Q22698"),
    "jardin": ("Jardín", "Q1107656"),
    "plaza": ("Plaza", "Q174782"),
    "monumento": ("Monumento", "Q4989906"),
    "iglesia": ("Iglesia", "Q16970"),
    "metro": ("Estación de metro", "Q928830"),
    "estacion": ("Estación de tren", "Q55488"),
}

PREFIXES = """PREFIX wd:       <http://www.wikidata.org/entity/>
PREFIX wdt:      <http://www.wikidata.org/prop/direct/>
PREFIX wikibase: <http://wikiba.se/ontology#>
PREFIX bd:       <http://www.bigdata.com/rdf#>
PREFIX geo:      <http://www.opengis.net/ont/geosparql#>
"""

STREETS_QUERY = Template(PREFIXES + """
# Datos de calles enlazadas desde el grafo (owl:sameAs): coordenadas, imagen y a quién homenajean.
SELECT ?item ?itemLabel ?itemDescription ?coord ?image
       ?namedAfter ?namedAfterLabel ?namedAfterDescription ?namedAfterImage
WHERE {
  VALUES ?item { $items }
  OPTIONAL { ?item wdt:P625 ?coord }
  OPTIONAL { ?item wdt:P18 ?image }
  OPTIONAL {
    ?item wdt:P138 ?namedAfter .
    OPTIONAL { ?namedAfter wdt:P18 ?namedAfterImage }
  }
  SERVICE wikibase:label { bd:serviceParam wikibase:language "es,en". }
}""")

PLACES_QUERY = Template(PREFIXES + """
# Lugares de tipo $label (P31 de $cls o de una subclase) a menos de $radius km de Sol.
SELECT ?item ?itemLabel ?itemDescription ?coord ?image
WHERE {
  { SELECT ?type WHERE { ?type wdt:P279* wd:$cls } }
  SERVICE wikibase:around {
    ?item wdt:P625 ?coord .
    bd:serviceParam wikibase:center "Point($lon $lat)"^^geo:wktLiteral .
    bd:serviceParam wikibase:radius "$radius" .
  }
  ?item wdt:P31 ?type .
  OPTIONAL { ?item wdt:P18 ?image }
  SERVICE wikibase:label { bd:serviceParam wikibase:language "es,en". }
}""")

_lock = threading.Lock()
_streets: dict[str, dict] = {}
_places: list[dict] = []
status = {"places": "pending", "streets": "pending"}


def _read(path) -> object | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _write(path, data) -> None:
    DATA.mkdir(exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


def _run(name: str, text: str) -> list[dict]:
    with trace.record("wikidata", name, text) as entry:
        response = httpx.post(
            ENDPOINT,
            data={"query": text},
            headers={"User-Agent": USER_AGENT, "Accept": "application/sparql-results+json"},
            timeout=90,
        )
        response.raise_for_status()
        rows = [
            {var: value["value"] for var, value in binding.items()}
            for binding in response.json()["results"]["bindings"]
        ]
        entry["rows"] = len(rows)
    return rows


def _point(wkt: str | None) -> tuple[float, float] | None:
    """'Point(lon lat)' -> (lat, lon)."""
    match = re.match(r"Point\(([-\d.eE]+) ([-\d.eE]+)\)", wkt or "")
    return (float(match[2]), float(match[1])) if match else None


def _thumb(url: str | None) -> str | None:
    return f"{url}?width=480" if url else None


def qid(iri: str) -> str:
    return iri.removeprefix(ENTITY)


def street_query(qids: list[str]) -> str:
    return STREETS_QUERY.substitute(items=" ".join(f"wd:{q}" for q in qids))


def _fetch_streets(qids: list[str]) -> None:
    facts: dict[str, dict] = {q: {"qid": q} for q in qids}
    for row in _run("wikidata_streets", street_query(qids)):
        fact = facts[qid(row["item"])]
        fact.setdefault("label", row.get("itemLabel"))
        fact.setdefault("description", row.get("itemDescription"))
        if (point := _point(row.get("coord"))) and "lat" not in fact:
            fact["lat"], fact["lon"] = point
        if row.get("image"):
            fact.setdefault("image", _thumb(row["image"]))
        if row.get("namedAfter"):
            named = fact.setdefault("namedAfter", {})
            if qid(row["namedAfter"]) not in named:
                named[qid(row["namedAfter"])] = {
                    "qid": qid(row["namedAfter"]),
                    "label": row.get("namedAfterLabel"),
                    "description": row.get("namedAfterDescription"),
                    "image": _thumb(row.get("namedAfterImage")),
                }
    for fact in facts.values():
        fact["namedAfter"] = list(fact.get("namedAfter", {}).values())
    with _lock:
        _streets.update(facts)
        _write(STREETS_CACHE, _streets)


def street_facts(street_qid: str) -> dict | None:
    """Datos de Wikidata de una calle enlazada; los consulta si no están en la caché."""
    with _lock:
        cached = _streets.get(street_qid)
    if cached is None:
        try:
            _fetch_streets([street_qid])
        except httpx.HTTPError as error:
            log.warning("Wikidata no disponible para %s: %s", street_qid, error)
            return None
        with _lock:
            return _streets.get(street_qid)
    with trace.record("wikidata", "wikidata_streets", street_query([street_qid]), cached=True) as entry:
        entry["rows"] = 1
    return cached


def places_query(key: str) -> str:
    label, cls = CATEGORIES[key]
    return PLACES_QUERY.substitute(
        label=label.lower(), cls=cls, lat=CENTER[0], lon=CENTER[1], radius=RADIUS_KM
    )


def _fetch_places() -> list[dict]:
    places: dict[str, dict] = {}
    for key in CATEGORIES:
        for row in _run(f"wikidata_places_{key}", places_query(key)):
            point = _point(row.get("coord"))
            label = row.get("itemLabel")
            if not point or not label or re.fullmatch(r"Q\d+", label):
                continue
            place = places.setdefault(qid(row["item"]), {
                "qid": qid(row["item"]),
                "label": label,
                "description": row.get("itemDescription"),
                "lat": point[0],
                "lon": point[1],
                "image": _thumb(row.get("image")),
                "categories": [],
            })
            if key not in place["categories"]:
                place["categories"].append(key)
    return list(places.values())


def places() -> list[dict]:
    with _lock:
        return _places


def place(place_qid: str) -> dict | None:
    return next((p for p in places() if p["qid"] == place_qid), None)


def record_cached_places(key: str, rows: int) -> None:
    """Deja constancia en el panel de que se han usado los lugares cacheados de una categoría."""
    with trace.record("wikidata", f"wikidata_places_{key}", places_query(key), cached=True) as entry:
        entry["rows"] = rows


def prefetch() -> None:
    """Carga las cachés de disco y consulta en Wikidata lo que falte.

    De las calles se descargan solo las enlazadas en el grafo que no estén ya en la caché,
    así que basta con arrancar la app tras actualizar el grafo.
    """
    global _places
    cached_streets = _read(STREETS_CACHE)
    if isinstance(cached_streets, dict):
        with _lock:
            _streets.update(cached_streets)
    try:
        linked = {qid(row["wikidata"]) for row in graph.query("linked_streets")}
        with _lock:
            missing = sorted(linked - _streets.keys())
        if missing:
            _fetch_streets(missing)
        status["streets"] = "ready"
    except httpx.HTTPError as error:
        log.warning("No se han podido descargar las calles de Wikidata: %s", error)
        status["streets"] = "ready" if _streets else "error"

    cached_places = _read(PLACES_CACHE)
    if isinstance(cached_places, list):
        with _lock:
            _places = cached_places
        status["places"] = "ready"
        return
    status["places"] = "loading"
    try:
        fetched = _fetch_places()
    except httpx.HTTPError as error:
        log.warning("No se han podido descargar los lugares de Wikidata: %s", error)
        status["places"] = "error"
        return
    with _lock:
        _places = fetched
        _write(PLACES_CACHE, fetched)
    status["places"] = "ready"
