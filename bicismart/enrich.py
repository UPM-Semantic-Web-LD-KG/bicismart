"""Ficha de una estación, estaciones y lugares cercanos, combinando BiciMAD, Callejero y Wikidata."""

from . import geo, graph, trace, wikidata
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
        found = [
            {**p, "distance": round(d), "link": f"https://www.wikidata.org/wiki/{p['qid']}",
             "categories": [wikidata.CATEGORIES[c][0] for c in p["categories"]]}
            for d, p in geo.by_distance(places, lat, lon, k=limit, max_m=radius_m)
        ]
        entry["rows"] = len(found)
    return found


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
    stations = graph.query_cached("stations")
    others = [s for s in stations if s["id"] != station_id and (s["usable"] or not usable_only)]
    found = [_station_row(s, d) for d, s in geo.by_distance(others, lat, lon, k=limit)]
    trace.note(
        f"Se eligen las {limit} más cercanas" + (" utilizables" if usable_only else "")
        + f"\na la estación {station_id} en línea recta (haversine), sin contarla a ella."
    )
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
    stations = graph.query_cached("stations")
    others = [s for s in stations if s["id"] != exclude]
    found = [
        {**_station_row(s, d), "lat": s["lat"], "lon": s["lon"]}
        for d, s in geo.by_distance_to_any(others, points, max_m=radius_m)
    ]
    if len(points) == 1:
        where = "de ({:.5f}, {:.5f}) ".format(*points[0])
    else:
        where = f"del más cercano de estos {len(points)} puntos:\n" + "".join(
            f"  ({lat:.5f}, {lon:.5f})\n" for lat, lon in points
        )
    trace.note(
        f"Se eligen las que están a {radius_m} m o menos\n{where}en línea recta (haversine), "
        "de la más cercana a la más lejana"
        + (f", sin contar la estación {exclude}." if exclude else ".")
    )
    return found
