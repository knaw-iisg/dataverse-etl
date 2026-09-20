"""
One-time reconciliation between IISG's old, now-retired Dataverse instance
(separate from dataverse.nl, identified by handles minted under
hdl.handle.net/10622/) and the current dataverse.nl-hosted collection this
project harvests. Not part of the weekly cron -- the old instance is gone
and its sequential IDs (https://iisg.amsterdam/id/dataset/{legacy_id}) will
never grow, so this only needs to run once (or again if the source export
changes).

Input: a .trig(.gz) export of the old https://iisg.amsterdam/graph/dataverse
graph (data/source/ by convention, gitignored -- ask IISG for a fresh export
if you don't have one). For every legacy dataset, its iisg:nativeViewer
handle is resolved live (concurrently, ~369 requests to hdl.handle.net) to
find the dataverse.nl DOI it now redirects to.

Output: legacy-crosswalk.trig, asserting for each resolved legacy dataset:
    <legacy>  a schema:Dataset ;
              owl:sameAs <new-doi-based-id> ;
              iisg:nativeViewer <https://doi.org/...> ;  # the DOI, not the
                                                          # handle -- handles
                                                          # can be deprecated,
                                                          # DOIs are the
                                                          # durable identifier
                                                          # now
              schema:identifier "<handle>"^^xsd:anyURI ,
                                "<doi-url>"^^xsd:anyURI .
    <new-doi-based-id> owl:sameAs <legacy> .

A handful of legacy handles point at the old instance's own domain
(datasets.iisg.amsterdam), which no longer resolves in DNS -- those are
printed as a manual-follow-up list rather than guessed at (e.g. by title
matching), since that could misattribute a legacy ID to the wrong dataset.
"""

import argparse
import concurrent.futures
import gzip
import re
import sys
from pathlib import Path

import requests
from rdflib import RDF, XSD, Graph, Literal, Namespace, URIRef
from rdflib.namespace import OWL, SDO

from dataverse_to_rdf import IISG_ID_BASE, IISG_NATIVE_VIEWER

IISG = Namespace("https://iisg.amsterdam/vocab/")
CROSSWALK_GRAPH = URIRef("https://iisg.amsterdam/graph/dataverse-legacy-crosswalk")

HANDLE_PATTERN = re.compile(
    r'<https://iisg\.amsterdam/id/dataset/(\d+)> <https://iisg\.amsterdam/vocab/nativeViewer> "([^"]+)"'
)
DOI_PATTERN = re.compile(r"(?:doi\.org/|persistentId=doi:)(10\.\d+/\S+)")


def extract_legacy_handles(trig_path):
    opener = gzip.open if trig_path.suffix == ".gz" else open
    pairs = []
    with opener(trig_path, "rt", encoding="utf-8") as f:
        for line in f:
            m = HANDLE_PATTERN.search(line)
            if m:
                pairs.append((m.group(1), m.group(2)))
    return pairs


def resolve_handle(pair):
    legacy_id, handle_url = pair
    try:
        r = requests.head(handle_url, allow_redirects=True, timeout=15)
        final_url = r.url
        if "doi.org/" not in final_url and "persistentId=doi:" not in final_url:
            r = requests.get(handle_url, allow_redirects=True, timeout=15)
            final_url = r.url
    except requests.RequestException as e:
        return legacy_id, handle_url, None, str(e)
    m = DOI_PATTERN.search(final_url)
    doi_suffix = m.group(1) if m else None
    return legacy_id, handle_url, doi_suffix, None if doi_suffix else final_url


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trig", type=Path, default=Path("data/source/https___iisg.amsterdam_graph_dataverse.trig.gz"))
    parser.add_argument("--out", type=Path, default=Path("legacy-crosswalk.trig"))
    args = parser.parse_args()

    pairs = extract_legacy_handles(args.trig)
    print(f"Found {len(pairs)} legacy datasets with a nativeViewer handle.")

    g = Graph(identifier=CROSSWALK_GRAPH)
    unresolved = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=15) as ex:
        for i, (legacy_id, handle_url, doi_suffix, note) in enumerate(ex.map(resolve_handle, pairs), 1):
            if doi_suffix:
                legacy = URIRef(f"{IISG_ID_BASE}{legacy_id}")
                new = URIRef(f"{IISG_ID_BASE}{doi_suffix}")
                doi_url = URIRef(f"https://doi.org/{doi_suffix}")
                g.add((legacy, RDF.type, SDO.Dataset))
                g.add((legacy, OWL.sameAs, new))
                g.add((new, OWL.sameAs, legacy))
                g.add((legacy, IISG_NATIVE_VIEWER, doi_url))
                g.add((legacy, SDO.identifier, Literal(handle_url, datatype=XSD.anyURI)))
                g.add((legacy, SDO.identifier, Literal(str(doi_url), datatype=XSD.anyURI)))
                # Reasoner-independent fallback alongside owl:sameAs: a plain
                # literal cross-reference any consumer can match on, even one
                # that doesn't do sameAs inference (e.g. the live Triply/DRUID
                # lookup didn't show any sign of it when tested).
                g.add((legacy, SDO.identifier, Literal(str(new), datatype=XSD.anyURI)))
                g.add((new, SDO.identifier, Literal(str(legacy), datatype=XSD.anyURI)))
            else:
                unresolved.append((legacy_id, handle_url, note))
            if i % 50 == 0:
                print(f"  resolved {i}/{len(pairs)}", file=sys.stderr)

    ds_count = len(list(g.subjects(RDF.type, SDO.Dataset)))
    print(f"\nResolved {ds_count} legacy datasets to a current DOI.")
    print(f"Unresolved: {len(unresolved)}")
    for legacy_id, handle_url, note in unresolved:
        print(f"  legacy id {legacy_id} ({handle_url}): {note}")

    g.serialize(str(args.out), format="trig")
    print(f"\nWrote {len(g)} triples to {args.out}")


if __name__ == "__main__":
    main()
