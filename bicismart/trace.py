"""Registro de las consultas hechas durante una petición, para el panel «¿por qué ontología?»."""

import time
from contextlib import contextmanager
from contextvars import ContextVar

_entries: ContextVar[list[dict] | None] = ContextVar("trace", default=None)


def start() -> list[dict]:
    """Empieza un registro nuevo para la petición en curso y lo devuelve."""
    entries: list[dict] = []
    _entries.set(entries)
    return entries


@contextmanager
def record(source: str, name: str, text: str, cached: bool = False):
    """Mide una consulta y la añade al registro de la petición (si hay uno abierto).

    `source` es "local" (grafo RDF del grupo), "wikidata" u "osrm". Quien llama rellena
    `entry["rows"]` con el número de resultados.
    """
    entry = {"source": source, "name": name, "text": text, "rows": None, "ms": 0.0, "cached": cached}
    began = time.perf_counter()
    try:
        yield entry
    finally:
        entry["ms"] = round((time.perf_counter() - began) * 1000, 1)
        entries = _entries.get()
        if entries is not None:
            entries.append(entry)


def note(comment: str) -> None:
    """Comenta en la última consulta registrada qué se hace con sus filas.

    Se llama justo después de la consulta, así que el comentario sale igual tanto si se ha
    ejecutado como si el resultado estaba en caché (`graph.query_cached`).
    """
    entries = _entries.get()
    if entries:
        lines = "\n".join(f"# {line}" for line in comment.splitlines())
        entries[-1]["text"] = entries[-1]["text"].rstrip() + "\n\n" + lines
