"""Ficha de una estación y lugares cercanos, combinando BiciMAD, Callejero y Wikidata."""

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
