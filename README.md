# KNAW-HuC Dataverse → NDE RDF

Harvests every dataset in the [KNAW-HuC collection](https://dataverse.nl/dataverse/KNAW-HuC)
on Dataverse.nl and writes it out as an RDF knowledge graph (`schema:Dataset` /
`schema:DataCatalog`) following NDE's
[Requirements for Datasets](https://docs.nde.nl/requirements-datasets/) profile.

Note: the *other* NDE spec, [SCHEMA-AP-NDE](https://docs.nde.nl/schema-profile/),
covers heritage objects (CreativeWork/Person/Place), not dataset records, so it
doesn't apply to this harvest — "Requirements for Datasets" is the correct spec
for describing datasets themselves.

Uses only the standard [Dataverse REST API](https://guides.dataverse.org/en/6.2/api/)
(Search API + native dataset API) with an API key. No browser, no scraping.

- **Organizations** are resolved to a canonical [ROR](https://ror.org) IRI
  wherever possible (a direct ROR URI in the affiliation field, a
  hand-curated alias for KNAW-HuC's own institutes, or a live ROR
  affiliation-match), so `"IISG"` and `"International Institute of Social
  History"` collapse onto the same node. What doesn't resolve is counted
  in `report.txt` and tracked as data-quality issues at
  [knaw-iisg/metadata-quality](https://github.com/knaw-iisg/metadata-quality).
- **Collection hierarchy**: each dataset is linked to *every* ancestor
  Dataverse collection it sits under (walking `ownerId` chains), not just
  its immediate parent.
- **Language tagging** uses Dataverse's own per-dataset "Language" field
  when set, falling back to `--lang` (default `en`) otherwise.

## Setup

```bash
pip install -r requirements.txt
export DATAVERSE_API_KEY=your-token
```

The key is required to see restricted datasets' metadata; without it the
harvest still runs but restricted datasets will be incomplete or skipped.

### A note on dataverse.nl's bot-check

dataverse.nl currently sits behind an [Anubis](https://github.com/TecharoHQ/anubis)
bot-check at the reverse-proxy layer, which returns an HTML "Access Denied"
page for scripted requests to *any* URL — `/api/` endpoints included — even
with a valid API key, because it sits in front of Dataverse's own
authentication rather than being part of it. This script does not attempt
to work around that (confirmed in testing: launching a headless browser to
clear the challenge does work technically, but was intentionally left out —
it reads as bot-detection evasion, which isn't something to build into a
script meant to run unattended).

If a run fails with an "Anubis bot-check" error, this is expected until
dataverse.nl/DANS allowlists this harvester. Worth asking them to exempt a
fixed IP, User-Agent, or the API token above for read-only, low-volume,
scheduled metadata access — reference the Dataverse API docs linked above
to make clear this is standard API usage, not scraping.

## Run

```bash
python dataverse_to_rdf.py
```

Outputs (default `./data/derived/knaw-huc/`, gitignored):
- `knaw-huc-dataverse.ttl` — Turtle
- `knaw-huc-dataverse.jsonld` — JSON-LD
- `report.txt` — counts of datasets processed and known compliance gaps
  (missing machine-readable license, creators without an authority ID, etc.)

Useful flags: `--limit 10` for a quick test run, `--alias`/`--lang` to target
a different collection or default language.

## Weekly cron

```
0 5 * * 1 cd /path/to/dataverse-etl && DATAVERSE_API_KEY=... /path/to/venv/bin/python dataverse_to_rdf.py >> harvest.log 2>&1
```

Each run re-harvests the whole collection and overwrites the output files —
it's a fresh snapshot, not an incremental update. This will only work once
the allowlisting above is in place.

## What this doesn't cover

This produces the RDF dump ("publication level 2" in the NDE spec). It
doesn't serve resolvable per-dataset RDF URIs itself (each dataset's DOI
already resolves to its Dataverse HTML landing page). Loading the Turtle
dump into a SPARQL endpoint would satisfy the spec's optional third
publication level.

See the module docstring in `dataverse_to_rdf.py` for the specific mapping
decisions and known gaps (license, creator identifiers, spatial coverage).
