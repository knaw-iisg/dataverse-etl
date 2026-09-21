# Changelog

## v1.1.0 — 2026-09-21

Restores three fields dropped in the v1.0.0 rewrite, closing
[#2](https://github.com/knaw-iisg/dataverse-etl/issues/2). Additive only —
none of these touch dataset identity, authority resolution, or class
choices from v1.0.0.

- **Dataset contact**: `schema:contactPoint` → `schema:ContactPoint`
  (name, and email when Dataverse's API actually exposes one), with
  affiliation resolved through the same ROR pipeline as authors.
- **Per-file checksum**: `iisg:checksumAlgorithm` + `iisg:checksumValue`
  on each `schema:DataDownload`, modeled as two properties rather than an
  MD5-only one since the algorithm varies (both MD5 and SHA-1 observed on
  this collection).
- **`kindOfData` / `subtitle`**: standard Dataverse citation fields, now
  emitted as `discovery#kindOfData` / `discovery#subtitle` (DDI Discovery
  vocab, matching the vocab already used by the legacy export).

See `documents/changes_dataverse_rdf_migration_v1.md` for the full
old-vs-new data model comparison these changes follow up on.

## v1.0.0 — 2026-09-21

Initial NDE-aligned rewrite of the KNAW-HuC Dataverse harvester: DOI-based
dataset IRIs, ORCID-linked creators, ROR-resolved organizations, and
standard schema.org classes (`DataDownload`, `Place`, `DataCatalog`)
replacing the legacy IISG export's opaque numeric IDs, unauthenticated
free-text people/orgs, and custom vocabulary.
