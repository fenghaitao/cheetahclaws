#!/usr/bin/env python3
"""Rebuild the graphify knowledge graph for this project — graph + report + wiki + vault notes.

AST-only extraction: deterministic, runs 100% locally, costs 0 LLM tokens.

Usage:
    python scripts/rebuild_graph.py [PROJECT_DIR] [options]

Examples:
    python scripts/rebuild_graph.py                 # current dir -> all outputs
    python scripts/rebuild_graph.py . --skip-obsidian
    python scripts/rebuild_graph.py . --vault-dir ~/vault/graphify/cheetahclaws

Outputs (under PROJECT_DIR/graphify-out/):
    graph.json        queryable graph        -> `graphify query "..."`
    GRAPH_REPORT.md   god nodes + metrics
    wiki/             index.md + one article per community / god node
    <vault-dir>/      one Obsidian note per node (default ~/vault/graphify/<project>)

Community labels live in scripts/community_labels.json, keyed by each community's
representative (highest-degree) node label — NOT by community id, which is unstable
across rebuilds. New/changed communities whose representative node isn't in the file
fall back to "Community <id>"; add an entry keyed by that node to name them.
"""
import argparse
import json
import shutil
import sys
from pathlib import Path

# Allow running under the system python: re-exec under the pipx venv that has graphify.
# (The venv python shares the system interpreter binary — only its site-packages differ —
# so we re-exec via the venv path, not by comparing executables, and guard against loops.)
try:
    from graphify.extract import collect_files, extract
except ModuleNotFoundError:
    import os
    if not os.environ.get("_GRAPHIFY_REEXEC"):
        venv = Path.home() / ".local/share/pipx/venvs/graphifyy/bin/python"
        if venv.exists():
            os.environ["_GRAPHIFY_REEXEC"] = "1"
            os.execv(str(venv), [str(venv), *sys.argv])
    sys.exit("graphify not installed. Install with: pipx install graphifyy")

from graphify.build import build_from_json
from graphify.cluster import cluster, score_all
from graphify.analyze import god_nodes, surprising_connections, suggest_questions
from graphify.report import generate
from graphify.export import to_json, to_obsidian
from graphify.wiki import to_wiki


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("project_dir", nargs="?", default=".", help="project root (default: .)")
    p.add_argument("--vault-dir", default=None,
                   help="Obsidian notes dir (default: ~/vault/graphify/<project-name>)")
    p.add_argument("--labels", default=None,
                   help="community labels JSON (default: <script-dir>/community_labels.json)")
    p.add_argument("--skip-wiki", action="store_true", help="don't generate the wiki")
    p.add_argument("--skip-obsidian", action="store_true", help="don't write vault notes")
    args = p.parse_args()

    root = Path(args.project_dir).resolve()
    out = root / "graphify-out"
    out.mkdir(exist_ok=True)
    project = root.name
    vault_dir = Path(args.vault_dir).expanduser() if args.vault_dir \
        else Path.home() / "vault" / "graphify" / project
    labels_path = Path(args.labels) if args.labels \
        else Path(__file__).resolve().parent / "community_labels.json"

    # 1. Extract (AST) + build + cluster
    files = collect_files(root)
    print(f"Collected {len(files)} files")
    extraction = extract(files)
    extraction.setdefault("input_tokens", 0)
    extraction.setdefault("output_tokens", 0)
    G = build_from_json(extraction)
    communities = cluster(G)
    cohesion = score_all(G, communities)
    gods = god_nodes(G)
    surprises = surprising_connections(G, communities)

    # 2. Labels — keyed by each community's representative (highest-degree) node,
    #    so names survive community-id renumbering when the codebase changes.
    stored = json.loads(labels_path.read_text()) if labels_path.exists() else {}
    deg = dict(G.degree())
    labels, unlabeled = {}, []
    for cid, nodes in communities.items():
        rep = max(nodes, key=lambda n: deg.get(n, 0))
        rep_label = G.nodes[rep].get("label", rep)
        if rep_label in stored:
            labels[cid] = stored[rep_label]
        else:
            labels[cid] = f"Community {cid}"
            unlabeled.append(cid)
    if unlabeled:
        print(f"  note: {len(unlabeled)} communities have no stored label "
              f"(using 'Community N'). Add names to {labels_path.name} keyed by the "
              f"community's representative node.")

    questions = suggest_questions(G, communities, labels)
    tokens = {"input": 0, "output": 0}
    total_words = sum(len(n.get("label", "").split()) for n in extraction["nodes"])
    detection = {"files": {"code": [str(f) for f in files]},
                 "total_files": len(files), "total_words": total_words}

    # 3. Report + graph.json (always)
    report = generate(G, communities, cohesion, labels, gods, surprises,
                      detection, tokens, str(root), suggested_questions=questions)
    (out / "GRAPH_REPORT.md").write_text(report)
    to_json(G, communities, str(out / "graph.json"))
    print(f"Graph: {G.number_of_nodes()} nodes, {G.number_of_edges()} edges, "
          f"{len(communities)} communities -> {out}/graph.json + GRAPH_REPORT.md")

    # 4. Wiki
    if not args.skip_wiki:
        wiki_dir = out / "wiki"
        if wiki_dir.exists():
            shutil.rmtree(wiki_dir)        # avoid stale articles from renamed communities
        nw = to_wiki(G, communities, str(wiki_dir),
                     community_labels=labels, cohesion=cohesion, god_nodes_data=gods)
        print(f"Wiki: {nw} articles + index.md -> {wiki_dir}")

    # 5. Obsidian per-node notes
    if not args.skip_obsidian:
        if vault_dir.exists():
            shutil.rmtree(vault_dir)       # wipe old (possibly relabeled) notes
        no = to_obsidian(G, communities, str(vault_dir),
                         community_labels=labels, cohesion=cohesion)
        print(f"Obsidian: {no} notes -> {vault_dir}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
