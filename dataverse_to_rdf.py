"""
Harvest the KNAW-HuC collection on Dataverse.nl and publish it as an RDF
knowledge graph that follows the NDE "Requirements for Datasets" profile
(https://docs.nde.nl/requirements-datasets/), which is the NDE spec that
governs how to describe datasets in schema.org RDF (the sibling
SCHEMA-AP-NDE profile at https://docs.nde.nl/schema-profile/ covers
heritage *objects* -- CreativeWork/Person/Place -- not dataset records,
so it does not apply here).

Meant to be run on a schedule (e.g. weekly via cron): it re-harvests the
full collection and overwrites the output files, so each run is a fresh
snapshot rather than an incremental diff.

Setup:
    pip install -r requirements.txt
    export DATAVERSE_API_KEY=...   # or pass --api-key

Uses the standard Dataverse REST API (Search API + native dataset API,
see https://guides.dataverse.org/en/6.2/api/) with the API key sent as
X-Dataverse-key. Nothing more than that: no browser, no scraping.

Note: dataverse.nl currently sits behind an Anubis bot-check at the
reverse-proxy layer, which returns an HTML "Access Denied" page for
scripted requests to *any* URL, /api/ included, even with a valid API
key -- this is in front of Dataverse's own auth, not part of it. This
script does not attempt to work around that; if you hit it, this is
expected until dataverse.nl/DANS allowlists this harvester (ask them,
mentioning the endpoints above and that this is read-only, low-volume,
scheduled metadata access with a valid API token).

Author affiliations are resolved to a canonical ROR (Research Organization
Registry) organization node wherever possible -- a direct ROR URI in the
field, a hand-curated alias for KNAW-HuC's own constituent institutes, or
a live match against ROR's affiliation-matching API, in that order.
Whatever doesn't resolve falls back to a locally-minted node and is
counted in report.txt, one line per distinct free-text value -- these are
genuine Dataverse metadata gaps, tracked as issues at
https://github.com/knaw-iisg/metadata-quality.

The dataset's DataCatalog membership walks the *full* Dataverse collection
hierarchy (via ownerId chains, not just the immediate parent dataverse),
and @lang tagging uses Dataverse's own per-dataset "Language" field when
set, falling back to --lang otherwise.

Known compliance gaps (see report.txt after a run):
  - schema:license is only emitted when Dataverse exposes a machine
    license URI; datasets published under free-text "termsOfUse" only
    are skipped and flagged in the report.
  - Creators without an ORCID/ISNI/VIAF/GND identifier get a locally
    minted fragment IRI rather than an authority-linked one -- unlike
    organizations, there's no safe automated way to match a bare name to
    the right individual, so this isn't attempted.
  - schema:spatialCoverage is emitted as a plain-named Place, not linked
    to a GeoNames URI (Dataverse doesn't record one).
  - This script produces the RDF dump; it doesn't itself serve
    resolvable per-dataset RDF URIs (NDE's "publication level 1"). The
    DOIs already resolve to the Dataverse HTML landing pages; serving
    the dump through a SPARQL endpoint (e.g. QLever) would satisfy the
    optional third publication level.
"""

import argparse
import hashlib
import os
import re
import sys
import time
from pathlib import Path

import requests
from rdflib import Graph, Literal, URIRef
from rdflib.namespace import RDF, XSD, SDO

BASE_URL = "https://dataverse.nl"
DEFAULT_ALIAS = "KNAW-HuC"
DEFAULT_LANG = "en"
REQUEST_DELAY = 0.2  # seconds between per-dataset API calls, be polite
OUTPUT_DIR = Path(__file__).resolve().parent / "data" / "derived" / "knaw-huc"

PUBLISHER_IRI = URIRef("https://huc.knaw.nl/")
PUBLISHER_NAME = "KNAW Humanities Cluster"

IISG_ID_BASE = "https://iisg.amsterdam/id/dataset/"
IISG_NATIVE_VIEWER = URIRef("https://iisg.amsterdam/vocab/nativeViewer")

CC_LICENSES = {
    "CC0-1.0": "https://creativecommons.org/publicdomain/zero/1.0/",
    "CC-BY-4.0": "https://creativecommons.org/licenses/by/4.0/",
    "CC-BY-SA-4.0": "https://creativecommons.org/licenses/by-sa/4.0/",
    "CC-BY-NC-4.0": "https://creativecommons.org/licenses/by-nc/4.0/",
    "CC-BY-ND-4.0": "https://creativecommons.org/licenses/by-nd/4.0/",
    "CC-BY-NC-SA-4.0": "https://creativecommons.org/licenses/by-nc-sa/4.0/",
    "CC-BY-NC-ND-4.0": "https://creativecommons.org/licenses/by-nc-nd/4.0/",
}

ID_SCHEME_URI = {
    "ORCID": "https://orcid.org/{}",
    "ISNI": "https://isni.org/isni/{}",
    "VIAF": "https://viaf.org/viaf/{}",
    "GND": "https://d-nb.info/gnd/{}",
}

LANG_TO_BCP47 = {
    "english": "en", "dutch": "nl", "german": "de", "french": "fr",
    "russian": "ru", "spanish": "es", "italian": "it", "portuguese": "pt",
}

ROR_API = "https://api.ror.org/v2/organizations"
ROR_URL_RE = re.compile(r"^https?://ror\.org/([0-9a-z]+)/?$", re.IGNORECASE)

# KNAW-HuC's own constituent institutes: hand-curated because ROR's affiliation
# matcher is deliberately conservative and won't confidently resolve common
# abbreviations/typos ("IISG", "Huygens Insitute") that make up a large share
# of this collection's author affiliations. Everything else falls through to
# the live ROR affiliation-matching API.
KNOWN_ROR_ALIASES = {
    "huygens": "https://ror.org/04x6kq749",
    "huygens ing": "https://ror.org/04x6kq749",
    "huygens institute": "https://ror.org/04x6kq749",
    "huygens insitute": "https://ror.org/04x6kq749",
    "huygens instituut": "https://ror.org/04x6kq749",
    "huygens.knaw.nl": "https://ror.org/04x6kq749",
    "huygens institute for the history and culture of the netherlands": "https://ror.org/04x6kq749",
    "huygens institute for history and culture of the netherlands": "https://ror.org/04x6kq749",
    "iisg": "https://ror.org/05dq4pp56",
    "iish": "https://ror.org/05dq4pp56",
    "international institute of social history": "https://ror.org/05dq4pp56",
    "meertens": "https://ror.org/05kaxyq51",
    "meertens institute": "https://ror.org/05kaxyq51",
    "meertens instituut": "https://ror.org/05kaxyq51",
    "dans": "https://ror.org/008pnp284",
    "dans-knaw": "https://ror.org/008pnp284",
    "data archiving and networked services": "https://ror.org/008pnp284",
    "knaw": "https://ror.org/043c0p156",
    "royal netherlands academy of arts and sciences": "https://ror.org/043c0p156",
    # the cluster itself has no distinct ROR (only parent KNAW does) --
    # point these at the same huc.knaw.nl node used for schema:publisher.
    "knaw humanities cluster": str(PUBLISHER_IRI),
    "knaw-huc": str(PUBLISHER_IRI),
    "humanities cluster": str(PUBLISHER_IRI),
    "huc-di": str(PUBLISHER_IRI),
}


# --- ROR (Research Organization Registry) client -----------------------------

def ror_canonical_name(cache, ror_uri):
    if ror_uri not in cache:
        ror_id = ror_uri.rstrip("/").rsplit("/", 1)[-1]
        name = None
        try:
            r = requests.get(f"{ROR_API}/{ror_id}", timeout=15)
            if r.status_code == 200:
                names = r.json().get("names", [])
                name = next((n["value"] for n in names if "ror_display" in n.get("types", [])), None)
        except requests.RequestException:
            pass
        cache[ror_uri] = name
    return cache[ror_uri]


def ror_match_affiliation(cache, text):
    if text not in cache:
        result = None
        try:
            r = requests.get(ROR_API, params={"affiliation": text}, timeout=15)
            if r.status_code == 200:
                chosen = next((it for it in r.json().get("items", []) if it.get("chosen")), None)
                if chosen:
                    org = chosen["organization"]
                    name = next(
                        (n["value"] for n in org["names"] if "ror_display" in n.get("types", [])),
                        org["id"],
                    )
                    result = (org["id"], name)
        except requests.RequestException:
            pass
        cache[text] = result
    return cache[text]


# --- Dataverse REST API client ----------------------------------------------

def build_session(api_key=None):
    session = requests.Session()
    session.headers.update({"Accept": "application/json"})
    if api_key:
        session.headers.update({"X-Dataverse-key": api_key})
    return session


def http_get_json(session, url, params=None, retries=3, backoff=5):
    for attempt in range(retries):
        r = session.get(url, params=params, timeout=60)
        content_type = r.headers.get("content-type", "")
        if r.status_code == 200 and "json" in content_type:
            return r.json()
        if "text/html" in content_type and "Anubis" in r.text[:2000]:
            raise RuntimeError(
                f"Blocked by dataverse.nl's Anubis bot-check on {url} (status {r.status_code}). "
                "This is an edge-layer check in front of the whole site, not a Dataverse API "
                "permission issue -- an API key does not clear it. This will start working once "
                "this harvester's IP/user agent is allowlisted."
            )
        if attempt < retries - 1:
            print(f"  [retry {attempt + 1}] {url} -> {r.status_code}, retrying in {backoff}s", file=sys.stderr)
            time.sleep(backoff)
            continue
        r.raise_for_status()
        raise RuntimeError(f"Non-JSON response from {url} (status {r.status_code})")


# --- Dataverse harvesting -----------------------------------------------------

def list_dataset_pids(session, alias, per_page=100, limit=None):
    # The search API's pagination windows can overlap as the index shifts
    # between requests, occasionally returning the same dataset on two
    # pages -- dedupe defensively rather than re-processing it twice.
    pids = []
    seen = set()
    start = 0
    while True:
        payload = http_get_json(session, f"{BASE_URL}/api/search", params={
            "q": "*", "subtree": alias, "type": "dataset", "per_page": per_page, "start": start,
        })["data"]
        items = payload["items"]
        for it in items:
            pid = it["global_id"]
            if pid in seen:
                continue
            seen.add(pid)
            pids.append({"pid": pid, "dv_alias": it.get("identifier_of_dataverse")})
            if limit and len(pids) >= limit:
                return pids
        start += per_page
        if start >= payload["total_count"] or not items:
            break
    return pids


def fetch_dataset(session, pid):
    return http_get_json(
        session, f"{BASE_URL}/api/datasets/:persistentId/",
        params={"persistentId": pid},
    )["data"]


def get_dataverse_info(session, cache, ident):
    if ident not in cache:
        cache[ident] = http_get_json(session, f"{BASE_URL}/api/dataverses/{ident}")["data"]
    return cache[ident]


def resolve_ancestor_chain(session, cache, alias, top_id, max_depth=12):
    """Dataverses from `alias` up to (not including) top_id, closest-first."""
    chain = []
    current, seen = alias, set()
    for _ in range(max_depth):
        if current in seen:
            break
        seen.add(current)
        d = get_dataverse_info(session, cache, current)
        if d["id"] == top_id:
            break
        chain.append(d)
        owner = d.get("ownerId")
        if owner is None:
            break
        current = owner
    return chain


# --- Field helpers --------------------------------------------------------

def field(fields, name):
    for f in fields:
        if f["typeName"] == name:
            return f["value"]
    return None


def sub(item, name):
    v = item.get(name)
    return v["value"] if v else None


def normalize_license(license_block):
    if not license_block:
        return None
    rid = license_block.get("rightsIdentifier")
    if rid and rid in CC_LICENSES:
        return CC_LICENSES[rid]
    uri = license_block.get("uri")
    if uri:
        return uri.replace("http://", "https://", 1)
    return None


# --- RDF construction -------------------------------------------------------

class GraphBuilder:
    def __init__(self, lang):
        self.g = Graph()
        self.lang = lang
        self.dataverse_cache = {}
        self.org_cache = {str(PUBLISHER_IRI): PUBLISHER_IRI}
        self.ror_id_cache = {}
        self.ror_affil_cache = {}
        self.report = {
            "processed": 0,
            "errors": [],
            "skipped_deaccessioned": [],
            "missing_license": [],
            "malformed_identifier": [],
            "no_creator_id": 0,
            "no_description": [],
            "unresolved_affiliation": {},
        }

    def top_catalog(self, alias):
        catalog = URIRef(f"{BASE_URL}/dataverse/{alias}")
        self.g.add((catalog, RDF.type, SDO.DataCatalog))
        self.g.add((catalog, SDO.name, Literal(f"{PUBLISHER_NAME} Dataverse Collection", lang=self.lang)))
        self.g.add((catalog, SDO.description, Literal(
            "Research datasets published by the KNAW Humanities Cluster and its constituent "
            "institutes on the DataverseNL platform, spanning demographic, social and "
            "economic history.", lang=self.lang)))
        self.g.add((catalog, SDO.publisher, PUBLISHER_IRI))
        self.g.add((PUBLISHER_IRI, RDF.type, SDO.Organization))
        self.g.add((PUBLISHER_IRI, SDO.name, Literal(PUBLISHER_NAME, lang=self.lang)))
        return catalog

    def add_dataverse_chain(self, chain, top_catalog):
        """chain: dataverse info dicts, closest-to-dataset first (see
        resolve_ancestor_chain). Returns their DataCatalog URIRefs, same
        order, with isPartOf links up to top_catalog already added."""
        catalogs = []
        prev = top_catalog
        for d in reversed(chain):  # build top-down so isPartOf chains correctly
            cid = d["id"]
            if cid in self.dataverse_cache:
                catalog = self.dataverse_cache[cid]
            else:
                catalog = URIRef(f"{BASE_URL}/dataverse/{d['alias']}")
                self.g.add((catalog, RDF.type, SDO.DataCatalog))
                self.g.add((catalog, SDO.name, Literal(d.get("name") or d["alias"], lang=self.lang)))
                self.dataverse_cache[cid] = catalog
            self.g.add((catalog, SDO.isPartOf, prev))
            prev = catalog
            catalogs.append(catalog)
        catalogs.reverse()
        return catalogs

    def _ensure_org(self, uri_str, display_name, lang, raw_name):
        org = URIRef(uri_str)
        if uri_str not in self.org_cache:
            self.g.add((org, RDF.type, SDO.Organization))
            name_lang = "en" if uri_str.startswith("https://ror.org/") or uri_str == str(PUBLISHER_IRI) else lang
            self.g.add((org, SDO.name, Literal(display_name, lang=name_lang)))
            self.org_cache[uri_str] = org
        if raw_name and raw_name != display_name:
            self.g.add((org, SDO.alternateName, Literal(raw_name, lang=lang)))
        return org

    def org_node(self, raw_name, lang):
        if not raw_name:
            return None
        name = raw_name.strip()
        norm = name.lower()

        ror_url = ROR_URL_RE.match(name)
        if ror_url:
            uri = f"https://ror.org/{ror_url.group(1)}"
            display = ror_canonical_name(self.ror_id_cache, uri) or uri
            return self._ensure_org(uri, display, lang, None)

        if norm in KNOWN_ROR_ALIASES:
            uri = KNOWN_ROR_ALIASES[norm]
            display = ror_canonical_name(self.ror_id_cache, uri) if uri.startswith("https://ror.org/") else PUBLISHER_NAME
            return self._ensure_org(uri, display or name, lang, name)

        match = ror_match_affiliation(self.ror_affil_cache, name)
        if match:
            uri, display = match
            return self._ensure_org(uri, display, lang, name)

        self.report["unresolved_affiliation"][name] = self.report["unresolved_affiliation"].get(name, 0) + 1
        slug = hashlib.sha1(name.encode("utf-8")).hexdigest()[:10]
        uri = f"{BASE_URL}/dataverse/{DEFAULT_ALIAS}#org-{slug}"
        return self._ensure_org(uri, name, lang, None)

    def add_dataset(self, data, catalogs, top_catalog):
        pid = data["persistentUrl"]
        ds = URIRef(pid)

        if data.get("protocol") == "doi" and data.get("authority") and data.get("identifier"):
            doi_suffix = f"{data['authority']}{data.get('separator', '/')}{data['identifier']}"
            iisg_id = URIRef(f"{IISG_ID_BASE}{doi_suffix}")
            self.g.add((iisg_id, IISG_NATIVE_VIEWER, ds))

        lv = data["latestVersion"]
        blocks = lv["metadataBlocks"]
        citation = blocks.get("citation", {}).get("fields", [])

        # Dataverse's "language" field describes the dataset's content, not
        # its metadata -- but it's the only per-dataset language signal
        # available, so use it for @lang tagging too, falling back to the
        # configured default when it's empty or unrecognized.
        lang_codes = [
            LANG_TO_BCP47[v.strip().lower()] for v in (field(citation, "language") or [])
            if v.strip().lower() in LANG_TO_BCP47
        ]
        lang = lang_codes[0] if lang_codes else self.lang
        for code in lang_codes:
            self.g.add((ds, SDO.inLanguage, Literal(code)))

        self.g.add((ds, RDF.type, SDO.Dataset))
        self.g.add((ds, SDO.includedInDataCatalog, top_catalog))
        self.g.add((top_catalog, SDO.dataset, ds))
        for cat in catalogs:
            self.g.add((ds, SDO.includedInDataCatalog, cat))
            self.g.add((cat, SDO.dataset, ds))

        title = field(citation, "title") or data.get("identifier", pid)
        self.g.add((ds, SDO.name, Literal(title, lang=lang)))

        descriptions = field(citation, "dsDescription") or []
        desc_text = "\n\n".join(
            sub(d, "dsDescriptionValue") for d in descriptions if sub(d, "dsDescriptionValue")
        )
        if desc_text:
            self.g.add((ds, SDO.description, Literal(desc_text, lang=lang)))
        else:
            self.report["no_description"].append(pid)

        self.g.add((ds, SDO.publisher, PUBLISHER_IRI))

        license_uri = normalize_license(lv.get("license"))
        if license_uri:
            self.g.add((ds, SDO.license, URIRef(license_uri)))
        else:
            self.report["missing_license"].append(pid)

        if data.get("publicationDate"):
            self.g.add((ds, SDO.datePublished, Literal(data["publicationDate"], datatype=XSD.date)))
        if lv.get("lastUpdateTime"):
            self.g.add((ds, SDO.dateModified, Literal(lv["lastUpdateTime"], datatype=XSD.dateTime)))
        date_deposit = field(citation, "dateOfDeposit") or data.get("publicationDate")
        if date_deposit:
            self.g.add((ds, SDO.dateCreated, Literal(date_deposit, datatype=XSD.date)))
        if lv.get("versionNumber") is not None:
            self.g.add((ds, SDO.version, Literal(f"{lv['versionNumber']}.{lv.get('versionMinorNumber', 0)}")))

        for kw_source, sub_field in (("subject", None), ("keyword", "keywordValue")):
            values = field(citation, kw_source) or []
            for v in values:
                text = sub(v, sub_field) if sub_field else v
                if text:
                    self.g.add((ds, SDO.keywords, Literal(text)))

        publication = field(citation, "publication") or []
        for p in publication:
            cite = sub(p, "publicationCitation")
            if cite:
                self.g.add((ds, SDO.citation, Literal(cite)))

        geo = blocks.get("geospatial", {}).get("fields", [])
        coverage = field(geo, "geographicCoverage") or []
        for i, c in enumerate(coverage):
            country = sub(c, "country")
            if country:
                place = URIRef(f"{pid}#place-{i}")
                self.g.add((place, RDF.type, SDO.Place))
                self.g.add((place, SDO.name, Literal(country, lang=lang)))
                self.g.add((ds, SDO.spatialCoverage, place))

        time_period = field(citation, "timePeriodCovered") or []
        for tp in time_period:
            tstart, tend = sub(tp, "timePeriodCoveredStart"), sub(tp, "timePeriodCoveredEnd")
            if tstart or tend:
                self.g.add((ds, SDO.temporalCoverage, Literal(f"{tstart or ''}/{tend or ''}")))

        authors = field(citation, "author") or []
        has_authority_id = False
        for a in authors:
            name = sub(a, "authorName")
            if not name:
                continue
            scheme = sub(a, "authorIdentifierScheme")
            ident = sub(a, "authorIdentifier")
            template = ID_SCHEME_URI.get(scheme) if scheme else None
            if template and ident and re.fullmatch(r"\S+", ident):
                person = URIRef(template.format(ident))
                has_authority_id = True
            elif template and ident:
                # Malformed at the source (e.g. spaces in an ISNI) rather than
                # cleaned up here -- flagged so it gets fixed in Dataverse, the
                # way https://doi.org/10.34894/X0D9QQ's ISNI was.
                self.report["malformed_identifier"].append(f"{pid}: {name} ({scheme} {ident!r})")
                slug = hashlib.sha1(name.encode("utf-8")).hexdigest()[:10]
                person = URIRef(f"{pid}#creator-{slug}")
            else:
                slug = hashlib.sha1(name.encode("utf-8")).hexdigest()[:10]
                person = URIRef(f"{pid}#creator-{slug}")
            self.g.add((person, RDF.type, SDO.Person))
            self.g.add((person, SDO.name, Literal(name, lang=lang)))
            affiliation = sub(a, "authorAffiliation")
            org = self.org_node(affiliation, lang)
            if org:
                self.g.add((person, SDO.affiliation, org))
            self.g.add((ds, SDO.creator, person))
        if authors and not has_authority_id:
            self.report["no_creator_id"] += 1

        files = lv.get("files", [])
        any_public, any_restricted = False, False
        for f in files:
            df = f.get("dataFile", {})
            fid = df.get("id")
            if fid is None:
                continue
            restricted = f.get("restricted", False)
            any_public, any_restricted = any_public or not restricted, any_restricted or restricted
            dl = URIRef(f"{pid}#file-{fid}")
            self.g.add((dl, RDF.type, SDO.DataDownload))
            self.g.add((ds, SDO.distribution, dl))
            name = df.get("filename") or f.get("label")
            if name:
                self.g.add((dl, SDO.name, Literal(name)))
            if df.get("contentType"):
                self.g.add((dl, SDO.encodingFormat, Literal(df["contentType"])))
            if df.get("filesize") is not None:
                self.g.add((dl, SDO.contentSize, Literal(str(df["filesize"]))))
            if not restricted:
                self.g.add((dl, SDO.contentUrl, URIRef(f"{BASE_URL}/api/access/datafile/{fid}")))
            else:
                self.g.add((dl, SDO.isAccessibleForFree, Literal(False)))
        if files and not any_public and any_restricted:
            self.g.add((ds, SDO.isAccessibleForFree, Literal(False)))

        self.report["processed"] += 1


def write_report(report, path):
    lines = [
        f"datasets processed: {report['processed']}",
        f"datasets with fetch errors: {len(report['errors'])}",
        f"datasets skipped as deaccessioned: {len(report['skipped_deaccessioned'])}",
        f"datasets missing a machine-readable license: {len(report['missing_license'])}",
        f"datasets with no creator authority identifier (ORCID/ISNI/VIAF): {report['no_creator_id']}",
        f"creator identifiers malformed at the source: {len(report['malformed_identifier'])}",
        f"datasets with no description: {len(report['no_description'])}",
        f"distinct author affiliations that didn't resolve to a ROR org: {len(report['unresolved_affiliation'])}",
        "",
        "--- errors ---",
        *report["errors"],
        "",
        "--- missing license ---",
        *report["missing_license"],
        "",
        "--- malformed identifier (fix in Dataverse) ---",
        *report["malformed_identifier"],
        "",
        "--- unresolved affiliation, by frequency (not necessarily bad data -- some are genuinely small/obscure orgs) ---",
        *(f"{count:>3}x  {name!r}" for name, count in
          sorted(report["unresolved_affiliation"].items(), key=lambda kv: -kv[1])),
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--alias", default=DEFAULT_ALIAS)
    parser.add_argument("--lang", default=DEFAULT_LANG)
    parser.add_argument("--limit", type=int, default=None, help="only harvest N datasets, for testing")
    parser.add_argument("--api-key", default=os.environ.get("DATAVERSE_API_KEY"),
                         help="Dataverse API token, for restricted metadata (or set DATAVERSE_API_KEY)")
    parser.add_argument("--out-dir", type=Path, default=OUTPUT_DIR)
    args = parser.parse_args()

    args.out_dir.mkdir(parents=True, exist_ok=True)

    if not args.api_key:
        print("Warning: no API key given (--api-key or DATAVERSE_API_KEY) -- "
              "restricted datasets will be skipped/incomplete.", file=sys.stderr)
    session = build_session(args.api_key)

    print(f"Listing datasets under '{args.alias}' ...")
    datasets = list_dataset_pids(session, args.alias, limit=args.limit)
    print(f"Found {len(datasets)} datasets to harvest.")

    builder = GraphBuilder(args.lang)
    top_catalog = builder.top_catalog(args.alias)

    dv_cache = {}
    top_id = get_dataverse_info(session, dv_cache, args.alias)["id"]
    unique_aliases = sorted({e["dv_alias"] for e in datasets if e["dv_alias"]})
    print(f"Resolving collection hierarchy for {len(unique_aliases)} sub-collections ...")
    alias_to_catalogs = {}
    for alias in unique_aliases:
        try:
            chain = resolve_ancestor_chain(session, dv_cache, alias, top_id)
            alias_to_catalogs[alias] = builder.add_dataverse_chain(chain, top_catalog)
        except Exception as e:
            print(f"  WARNING: could not resolve hierarchy for '{alias}': {e}", file=sys.stderr)
            alias_to_catalogs[alias] = []

    for i, entry in enumerate(datasets, 1):
        pid = entry["pid"]
        try:
            data = fetch_dataset(session, pid)
            if not data.get("latestVersion"):
                # Deaccessioned datasets are still indexed by search but have
                # no accessible version through this endpoint.
                builder.report["skipped_deaccessioned"].append(pid)
                continue
            catalogs = alias_to_catalogs.get(entry["dv_alias"], [])
            builder.add_dataset(data, catalogs, top_catalog)
        except Exception as e:
            print(f"  [{i}/{len(datasets)}] ERROR on {pid}: {e}", file=sys.stderr)
            builder.report["errors"].append(f"{pid}: {e}")
        if i % 25 == 0 or i == len(datasets):
            print(f"  [{i}/{len(datasets)}] processed")
        time.sleep(REQUEST_DELAY)

    ttl_path = args.out_dir / "knaw-huc-dataverse.ttl"
    jsonld_path = args.out_dir / "knaw-huc-dataverse.jsonld"
    report_path = args.out_dir / "report.txt"

    builder.g.serialize(str(ttl_path), format="turtle")
    builder.g.serialize(str(jsonld_path), format="json-ld")
    write_report(builder.report, report_path)

    print(f"\nWrote {len(builder.g)} triples to:\n  {ttl_path}\n  {jsonld_path}\nReport: {report_path}")


if __name__ == "__main__":
    main()
