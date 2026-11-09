"""Ficha de una estación, estaciones y lugares cercanos, combinando BiciMAD, Callejero y Wikidata."""

from contextlib import contextmanager

from . import geo, geocode, graph, trace, wikidata
from .names import district_names, pretty

STATES = {
    "IN_SERVICE": "En servicio",
    "NOT_IN_SERVICE": "Fuera de servicio",
    "PLANNED": "Planificada",
    "END_OF_LIFE": "Retirada",
}


def station_card(station_id: str) -> dict:
    rows = graph.query("station_detail", id=station_id)
    if not rows:
        raise LookupError(f"No existe la estación {station_id}.")
    s = rows[0]

    blocks = [{
        "source": "bicimad",
        "title": "Estación",
        "items": [
            ["Estado", STATES.get(s["state"], s["state"]) + ("" if s["activated"] else " (desactivada)")],
            ["Bases", str(s["capacity"])],
            ["Bases inutilizables", str(s["unavailable"])],
            ["Coordenadas", f"{s['lat']:.5f}, {s['lon']:.5f}"],
        ],
    }]

    if s["street"]:
        districts = district_names(s["districts"])
        items = [["Calle", pretty(s["streetName"])]]
        if districts:
            items.append(["Distritos" if "," in districts else "Distrito", districts])
        if s["postalCodes"]:
            items.append(["Código postal", ", ".join(sorted(s["postalCodes"].split(",")))])
        blocks.append({"source": "callejero", "title": "Callejero", "items": items})

    if s["wikidata"] and (facts := wikidata.street_facts(wikidata.qid(s["wikidata"]))):
        blocks.append({
            "source": "wikidata",
            "title": facts.get("label") or pretty(s["streetName"]),
            "description": facts.get("description"),
            "image": facts.get("image"),
            "link": f"https://www.wikidata.org/wiki/{facts['qid']}",
            "namedAfter": [
                {**person, "link": f"https://www.wikidata.org/wiki/{person['qid']}"}
                for person in facts.get("namedAfter", [])
            ],
        })

    return {
        "id": s["id"],
        "name": s["name"],
        "lat": s["lat"],
        "lon": s["lon"],
        "usable": s["usable"],
        "blocks": blocks,
    }


def nearby_places(lat: float, lon: float, radius_m: float = 500, limit: int = 6) -> list[dict]:
    """Lugares de Wikidata a menos de `radius_m` metros, del más cercano al más lejano."""
    places = wikidata.places()
    with trace.record(
        "wikidata", "wikidata_places",
        wikidata.places_query("museo")
        + f"\n\n# … y una consulta igual para cada categoría: {', '.join(wikidata.CATEGORIES)}."
        + f"\n# Se usan los resultados en caché y se filtran a {radius_m:.0f} m del punto.",
        cached=True,
    ) as entry:
        found = sorted(
            ((geo.distance_m(lat, lon, p["lat"], p["lon"]), p) for p in places),
            key=lambda pair: pair[0],
        )
        found = [
            {**p, "distance": round(d), "link": f"https://www.wikidata.org/wiki/{p['qid']}",
             "categories": [wikidata.CATEGORIES[c][0] for c in p["categories"]]}
            for d, p in found if d <= radius_m
        ][:limit]
        entry["rows"] = len(found)
    return found


@contextmanager
def _stations(describe: str):
    """Todas las estaciones del grafo y su entrada `stations` en el panel de consultas.

    Si aún no estaban en memoria, `graph.query` ya registra la consulta al cargarlas. Si lo
    estaban, se añade una entrada de caché que termina con `describe`: qué se hace con ellas.
    Devuelve `(estaciones, entrada)`; quien llama rellena `entrada["rows"]`.
    """
    if geocode.all_stations.cache_info().currsize == 0:
        yield geocode.all_stations(), {}
        return
    stations = geocode.all_stations()
    with trace.record(
        "local", "stations",
        (graph.QUERIES / "stations.rq").read_text(encoding="utf-8")
        + f"\n# Se usan las {len(stations)} estaciones en caché y {describe}",
        cached=True,
    ) as entry:
        yield stations, entry


def _station_row(station: dict, distance: float) -> dict:
    return {
        "id": station["id"],
        "name": station["name"],
        "distance": round(distance),
        "capacity": station["capacity"],
        "usable": station["usable"],
    }


def nearest_stations(
    station_id: str, lat: float, lon: float, limit: int = 5, usable_only: bool = False
) -> list[dict]:
    """Las `limit` estaciones más cercanas a una estación, sin contarla, en línea recta.

    Con `usable_only` solo cuentan las que se pueden usar: es lo que interesa como alternativa
    a una estación de la ruta.
    """
    describe = (
        f"se eligen las {limit} más cercanas" + (" utilizables" if usable_only else "")
        + f"\n# a la estación {station_id} en línea recta (haversine), sin contarla a ella."
    )
    with _stations(describe) as (stations, entry):
        others = [s for s in stations if s["id"] != station_id and (s["usable"] or not usable_only)]
        found = [
            _station_row(s, geo.distance_m(lat, lon, s["lat"], s["lon"]))
            for s in geo.nearest(others, lat, lon, limit)
        ]
        entry["rows"] = len(found)
    return found


def stations_within(
    points: list[tuple[float, float]], radius_m: int, exclude: str | None = None
) -> list[dict]:
    """Estaciones a `radius_m` metros o menos de alguno de los `points`, en línea recta y de la
    más cercana a la más lejana. Incluye las que no se pueden usar.

    La distancia de cada estación es la del punto más cercano: una calle con estaciones se mide
    desde todas ellas, no desde un punto medio. `exclude` es el id de una estación que no
    cuenta: la que hace de centro.
    """
    if len(points) == 1:
        where = "de ({:.5f}, {:.5f})".format(*points[0])
    else:
        where = f"del más cercano de estos {len(points)} puntos:\n#   " + "\n#   ".join(
            f"({lat:.5f}, {lon:.5f})" for lat, lon in points
        ) + "\n#"
    describe = (
        f"se eligen las que están a {radius_m} m o menos\n# {where} en línea recta (haversine), "
        "de la más cercana a la más lejana"
        + (f", sin contar la estación {exclude}." if exclude else ".")
    )
    with _stations(describe) as (stations, entry):
        by_distance = sorted(
            (
                (min(geo.distance_m(lat, lon, s["lat"], s["lon"]) for lat, lon in points), s)
                for s in stations if s["id"] != exclude
            ),
            key=lambda pair: pair[0],
        )
        found = [
            {**_station_row(s, d), "lat": s["lat"], "lon": s["lon"]}
            for d, s in by_distance if d <= radius_m
        ]
        entry["rows"] = len(found)
    return found
