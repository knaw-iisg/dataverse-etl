# KNAW-HuC Dataverse → NDE RDF

This project produces two related RDF graphs describing IISG/KNAW-HuC's
Dataverse holdings, following NDE's
[Requirements for Datasets](https://docs.nde.nl/requirements-datasets/)
profile (`schema:Dataset` / `schema:DataCatalog`). Note: the *other* NDE
spec, [SCHEMA-AP-NDE](https://docs.nde.nl/schema-profile/), covers heritage
*objects* (CreativeWork/Person/Place), not dataset records — it doesn't
apply here.

## The two graphs, and how they relate

| | **1. Current collection** | **2. Legacy crosswalk** |
|---|---|---|
| Script | `dataverse_to_rdf.py` | `build_legacy_crosswalk.py` |
| Output | `data/derived/knaw-huc/knaw-huc-dataverse.ttl` (+ `.jsonld`) | `legacy-crosswalk.trig` |
| Source | live [KNAW-HuC collection](https://dataverse.nl/dataverse/KNAW-HuC) on dataverse.nl, via the REST API | a static `.trig` export of IISG's old, now-retired Dataverse instance |
| Runs | weekly, on a schedule (fresh snapshot every time) | once, or again only if a new legacy export shows up |
| Dataset IRIs | `https://iisg.amsterdam/id/dataset/{DOI-suffix}` | `https://iisg.amsterdam/id/dataset/{old-numeric-id}` |

IISG used to run its own Dataverse instance, identified by sequential
numeric IDs and `hdl.handle.net` handles. That instance is gone — the
collection now lives on dataverse.nl under KNAW-HuC, identified by DOIs.
Graph 2 bridges the two: for each legacy dataset, it resolves its old
handle (live, via `hdl.handle.net`) to find the DOI it now redirects to,
then asserts `owl:sameAs` between the legacy IRI and graph 1's DOI-based
IRI for the same dataset — so anyone still holding a reference to the old
IDs can follow it through to the current record. Graph 2 is static once
built; it doesn't need re-running unless IISG produces a fresh export of
the old instance.

## Setup

```bash
pip install -r requirements.txt
```

## Creating graph 1: the current collection

```bash
export DATAVERSE_API_KEY=your-token   # optional, see below
python dataverse_to_rdf.py
```

The API key lets the harvest see restricted datasets' metadata; without
it, restricted datasets will be incomplete or skipped. Also writes
`report.txt` (counts processed, and known compliance gaps: missing
licenses, creators without an authority ID, etc.).

Useful flags: `--limit 10` for a quick test run, `--alias`/`--lang` to
target a different collection or default language.

To keep this graph current, schedule it weekly — each run re-harvests the
whole collection and overwrites the output, rather than diffing:

```
0 5 * * 1 cd /path/to/dataverse-etl && DATAVERSE_API_KEY=... /path/to/venv/bin/python dataverse_to_rdf.py >> harvest.log 2>&1
```

## Creating graph 2: the legacy crosswalk

Only needed once (or again if you get a new export of the old instance).
Ask IISG for a `.trig(.gz)` export of the old
`https://iisg.amsterdam/graph/dataverse` graph, place it under
`data/source/` (gitignored), then:

```bash
python build_legacy_crosswalk.py --trig data/source/<export>.trig.gz
```

This makes ~369 live requests to `hdl.handle.net` to resolve each legacy
handle to its current DOI, so it takes a few minutes. Datasets it can't
resolve automatically are printed for manual follow-up — see
`build_legacy_crosswalk.py`'s module docstring for the handful already
resolved by hand.

## What this doesn't cover

Both scripts here only ever produce static files (`.ttl`/`.jsonld`/`.trig`)
— a bulk dump you load somewhere else. That satisfies NDE's core
requirement ("publishers *must* make their dataset descriptions available
in RDF"), but not two further things the spec recommends (*should*, not
*must*):

- **Resolvable per-dataset IRIs.** Every dataset here gets an identity
  like `https://iisg.amsterdam/id/dataset/10.34894/NQOASN`, but that URI
  isn't backed by a live web server in this project — visiting it
  resolves to nothing. (Each dataset's *DOI* does resolve, but to the
  Dataverse.nl landing page, not to this graph.)
- **A live SPARQL endpoint** over the data, so it can be queried instead
  of downloaded and loaded elsewhere first.

Neither gap is filled by this repo — it's covered by IISG's existing
Triply/DRUID infrastructure loading this dump, not by anything here.

See the module docstrings in `dataverse_to_rdf.py` and
`build_legacy_crosswalk.py` for the specific mapping decisions and known
gaps (license, creator identifiers, spatial coverage, ROR/affiliation
resolution, collection hierarchy).
