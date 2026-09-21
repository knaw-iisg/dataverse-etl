# Dataverse RDF data model: old vs. new

Comparison of the IISG legacy Dataverse export
(`data/source/https___iisg.amsterdam_graph_dataverse.trig`) against the new
KNAW-HuC harvest (`data/derived/knaw-huc/knaw-huc-dataverse.ttl`).

Both describe the same underlying thing — IISG/KNAW-HuC's Dataverse holdings
as schema.org RDF — but they're different generations of the same idea, and
the new one is a more disciplined, standards-conforming rebuild rather than
an incremental change.

## 1. Serialization & scope

| | Old (`data/source/https___iisg.amsterdam_graph_dataverse.trig`) | New (`data/derived/knaw-huc/knaw-huc-dataverse.ttl`) |
|---|---|---|
| Format | TriG, one named graph `<https://iisg.amsterdam/graph/dataverse>` | Plain Turtle, no named graph |
| Source system | IISG's own (now-retired) Dataverse instance | dataverse.nl / KNAW-HuC collection |
| Scale | 369 datasets, ~168k lines | 413 datasets, ~241k triples |

## 2. Dataset identity

- **Old**: `<https://iisg.amsterdam/id/dataset/10043>` — an opaque internal
  sequence number; `schema:identifier` is a **bare literal string**
  `"10043"` (not even a URI).
- **New**: `<https://iisg.amsterdam/id/dataset/10.34894/NQOASN>` — same
  iisg.amsterdam namespace, but the path segment is the dataset's DOI
  suffix; `schema:identifier` is now an actual **IRI**,
  `<https://doi.org/10.34894/NQOASN>` (this matches the recent commit "Add
  schema:identifier for DOIs"). The dataset's own IRI is still minted under
  iisg.amsterdam rather than the DOI itself — per the module docstring,
  IISG doesn't control dataverse.nl, so it can't claim the DOI as its own
  identity, but it now bakes the DOI into the local IRI instead of using a
  meaningless counter.
- A one-time crosswalk (`build_legacy_crosswalk.py` →
  `legacy-crosswalk.trig`) bridges the two: for each old numeric-ID dataset
  it resolves the legacy handle and asserts `owl:sameAs` to the new
  DOI-based IRI, since the old sequence numbers are permanently frozen
  (source instance is dead).

## 3. Files / distributions

- **Old**: `Dataset —hasPart→ DigitalDocument` *and*
  `Dataset —distribution→` (same target, duplicated edge), each file typed
  `schema:DigitalDocument` with **custom** properties: `iisg:md5`,
  `schema:contentType`, plus sometimes
  `iisg:originalFileFormat`/`originalFormatLabel` for Dataverse-derived
  tabular formats. Files live in a separate namespace
  (`/id/file/dataverse-{ds}-{n}`).
- **New**: only `schema:distribution`, files typed `schema:DataDownload`
  (the correct schema.org class for a file), fragment IRIs under the
  dataset (`#file-{id}`), using **standard** properties:
  `schema:contentUrl`, `schema:contentSize`, `schema:encodingFormat`. Adds
  per-file `schema:isAccessibleForFree` (restricted vs. open files) — not
  tracked at all in the old model. Drops the checksum and
  original-format vocab entirely.

## 4. People / roles

- **Old**: three distinct locally-minted person roles per dataset —
  `schema:creator`, `schema:contactPoints`, and depositor via
  `<http://id.loc.gov/vocabulary/relators/dpt>` — each with
  `givenName`/`familyName`/`name`/`email`, no external authority linking.
- **New**: only `schema:creator` survives; contactPoint and depositor are
  dropped entirely. Creators resolve to a real **ORCID IRI** when
  Dataverse supplies one (so the same person is deduplicated across
  datasets, e.g. `<https://orcid.org/0000-0002-8666-0047>` reused),
  otherwise falls back to a per-dataset fragment IRI with just
  `schema:name` (no given/family split). Per the docstring, matching bare
  names to an authority ID isn't attempted automatically — that's tracked
  in `report.txt` ("no creator authority identifier").

## 5. Organizations / affiliation

- **Old**: `schema:affiliation` is always a free-text literal
  (`"International Institute of Social History"`).
- **New**: resolved to a canonical **ROR IRI**
  (`<https://ror.org/05dq4pp56>`) wherever possible — direct ROR URI,
  hand-curated KNAW-HuC alias, or live ROR affiliation-match — with
  unresolved names falling back to locally-minted `Organization` nodes
  under `dataverse.nl/dataverse/KNAW-HuC#org-*` (23 distinct unresolved
  orgs, tracked in `report.txt`).

## 6. Geography

- **Old**: `schema:geoWithin` with a precise **WKT polygon** bounding box
  (custom `triply.cc/wkt/polygon` datatype).
- **New**: `schema:spatialCoverage` → `schema:Place` node with just a plain
  `schema:name` (e.g. `"Russian Federation"`) — standard schema.org shape,
  but loses actual geometry (Dataverse's API doesn't expose one, per the
  docstring).

## 7. Collection hierarchy

- **Old**: single edge, immediate parent only —
  `Dataverse —hasPart→ Dataset`.
- **New**: bidirectional `schema:includedInDataCatalog` (dataset→catalog) /
  `schema:dataset` (catalog→dataset), and walks the **full ownerId
  ancestor chain**, not just the immediate parent — so a dataset can
  belong to several catalogs at once (e.g. both `KNAW-HuC` and `RISTAT`).

## 8. Dropped/added fields

- **Dropped entirely**: `iisg:md5`,
  `iisg:originalFileFormat`/`originalFormatLabel`,
  `iisg:accessRequestRequired`, `iisg:publicationCitation`,
  `iisg:continent`, `discovery#kindOfData`, `discovery#subtitle`,
  `dc:source`, `schema:comment`, `schema:addressCountry`,
  `schema:uploadDate`.
- **Added**: `schema:citation`, `schema:version`, `schema:publisher`
  (always `<https://huc.knaw.nl/>`), `schema:isAccessibleForFree`,
  DOI-based `schema:identifier`.
- **Kept largely as-is**: `schema:name`, `schema:description`,
  `schema:keywords`, `schema:license` (now consistently a real license URI
  — CC0/CC-BY-SA — vs. the old model's link to a Dataverse *terms-of-use
  HTML page*, which isn't a machine-readable license and is why 38 new
  datasets are now flagged as "missing license" instead of silently
  carrying a fake one), `schema:temporalCoverage` (same ISO interval
  string format), `iisg:nativeViewer` (still the DOI/handle landing-page
  link).

## Bottom line

The new model is a deliberate move from a bespoke, IISG-internal vocabulary
with opaque local IDs toward a leaner set of core schema.org properties
aligned to NDE's "Requirements for Datasets" profile, with real-world
authority linking (ORCID, ROR, DOI) replacing free-text/local IDs wherever
the source data allows it — at the cost of dropping some old fields
(checksums, precise geometry, contact/depositor roles) that either don't
fit the target profile or aren't exposed by the dataverse.nl API.
