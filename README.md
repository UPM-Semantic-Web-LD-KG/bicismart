# BiciSmart

Aplicación web local que, dados un origen y un destino, busca la combinación más rápida
andar → BiciMAD → andar. Usa el grafo RDF del grupo (BiciMAD + Callejero, con `owl:sameAs` a
Wikidata) y muestra qué parte de cada respuesta no se podría obtener solo con el CSV de BiciMAD.

## Datos

`graph/` contiene una copia de dos ficheros de la entrega del grupo, tomados del commit
[`0d2cd80`](https://github.com/roroal23/semantic_web_2026/tree/0d2cd80d5a2ad222af8364c1b7cb8b694a09458b/HandsOn/Group03)
de `roroal23/semantic_web_2026` (rama `Group03-Final`):

- `bicimad-with-links-updated.ttl` (de `rdf/`): estaciones, calles y enlaces a Wikidata.
- `bicismart-ontology-updated.ttl` (de `ontology/`): la ontología `bm:`.

Para actualizarlos, se copian de nuevo esos dos ficheros y se cambia el commit de este apartado.

Fuentes originales: [BiciMAD](https://datos.madrid.es) y el Callejero oficial, del portal de datos
abiertos del Ayuntamiento de Madrid, y [Wikidata](https://www.wikidata.org) (CC0).

## Ejecutar

```bash
uv run main.py
```

Abrir http://127.0.0.1:8000. En el primer arranque se descargan de Wikidata los datos de las
calles enlazadas y los lugares de interés de Madrid (unos 15 s) y se guardan en `data/`. En los
siguientes solo se descargan las calles enlazadas que falten. Para refrescarlo todo basta con
borrar esa carpeta.

## Qué hace

- **Destino semántico**: una calle del Callejero (el número se ignora, porque el grafo modela calles
  y no portales), un lugar de Wikidata ("Museo del Prado"), un tipo de lugar ("un parque") o una
  estación. Una calle con estaciones (`bm:locatedOnStreet`) se usa directamente como extremo del
  trayecto. Si no tiene estaciones, se sitúa con sus coordenadas de Wikidata (`owl:sameAs` → P625)
  o, si no, de forma aproximada por su distrito y barrio.
- **Ruta**: solo se usan las estaciones activadas, en servicio y con bases útiles (consulta
  `usable_stations.rq`). Se prueban las 5 más cercanas a cada extremo y se elige el par más rápido
  con distancias en línea recta. Después se piden a OSRM (FOSSGIS, OpenStreetMap) el trazado y los
  tiempos reales de esos tramos y de ir andando. Si OSRM no responde, se muestran las estimaciones
  marcadas como "estimado".
- **Itinerario enriquecido**: calle, distrito y código postal de las estaciones (Callejero);
  a quién homenajea la calle y su imagen (Wikidata); lugares de interés junto al destino.
  Cada dato lleva una etiqueta con su fuente.
- **Panel "¿Por qué ontología?"**: tabla con lo que aporta cada fuente frente al CSV de BiciMAD
  y la lista de consultas ejecutadas (SPARQL local, Wikidata y OSRM), con su texto.

## Estructura

- `graph/`: grafo y ontología del grupo (ver «Datos»).
- `bicismart/queries/*.rq`: consultas SPARQL sobre el grafo del grupo.
- `bicismart/graph.py`: carga el grafo en Oxigraph y ejecuta las consultas.
- `bicismart/wikidata.py`: consultas a Wikidata y caché en `data/`.
- `bicismart/geocode.py`: búsqueda y resolución de origen y destino.
- `bicismart/routing.py`: elección de estaciones y llamadas a OSRM.
- `bicismart/enrich.py`: ficha de estación y lugares cercanos.
- `bicismart/server.py`: API (FastAPI).
- `bicismart/static/`: web (Leaflet).
