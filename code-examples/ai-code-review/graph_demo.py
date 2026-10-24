# /// script
# requires-python = ">=3.13"
# dependencies = [
#   "code-review-graph @ git+https://github.com/tirth8205/code-review-graph.git@343ab7cea29d47a3e805381e09f8243a43d43d88",
# ]
# ///
"""A synthetic, local graph experiment; does not call an LLM or install hooks."""

import argparse
import importlib.metadata
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile


SOURCE_SHA = "343ab7cea29d47a3e805381e09f8243a43d43d88"
SAFE_STORE = '''def get_order(db, tenant_id, order_id):
    return db.execute(
        "SELECT id, tenant_id FROM orders WHERE tenant_id = ? AND id = ?",
        (tenant_id, order_id),
    ).fetchone()
'''
UNSAFE_STORE = '''def get_order(db, tenant_id, order_id):
    return db.execute(
        "SELECT id, tenant_id FROM orders WHERE id = ?",
        (order_id,),
    ).fetchone()
'''
FIXTURE = {
    "store.py": SAFE_STORE,
    "handlers.py": '''from store import get_order

def get_order_handler(db, identity, order_id):
    return get_order(db, identity["tenant_id"], order_id)
''',
    "test_orders.py": '''import sqlite3
import unittest
from handlers import get_order_handler

class OrderTests(unittest.TestCase):
    def test_own_order(self):
        db = sqlite3.connect(":memory:")
        db.execute("CREATE TABLE orders(id TEXT, tenant_id TEXT)")
        db.execute("INSERT INTO orders VALUES ('a1', 'A'), ('b1', 'B')")
        self.assertEqual(get_order_handler(db, {"tenant_id": "A"}, "a1"), ("a1", "A"))
        db.close()
''',
    "jobs.py": '''from store import get_order

JOBS = {"export": get_order}

def run_job(db, name, tenant_id, order_id):
    return JOBS[name](db, tenant_id, order_id)
''',
    "health.py": '''def health():
    return "ok"
''',
}


def run(root, *args):
    return subprocess.run(args, cwd=root, capture_output=True, text=True, check=True)


def cross_tenant_denied(root):
    # Load this version in a fresh process, with no stale imports/bytecode.
    source = '''import sqlite3
from handlers import get_order_handler
db = sqlite3.connect(":memory:")
db.execute("CREATE TABLE orders(id TEXT, tenant_id TEXT)")
db.execute("INSERT INTO orders VALUES ('a1', 'A'), ('b1', 'B')")
print(get_order_handler(db, {"tenant_id": "A"}, "b1") is None)
'''
    return run(root, sys.executable, "-B", "-c", source).stdout.strip() == "True"


def sanitize(value, root):
    if isinstance(value, str):
        return value.replace(str(root), "<demo>")
    if isinstance(value, list):
        return [sanitize(item, root) for item in value]
    if isinstance(value, dict):
        return {sanitize(key, root): sanitize(item, root) for key, item in value.items()}
    return value


def experiment(root):
    # CRG_HOME redirects this tool's registry; it does not change HOME or agent settings.
    os.environ["CRG_HOME"] = str(root / "tool-state")
    os.environ["CRG_DATA_DIR"] = str(root / ".code-review-graph")
    os.environ["CRG_REPO_ROOT"] = str(root)
    os.environ["CRG_SERIAL_PARSE"] = "1"
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"

    from code_review_graph.tools.build import build_or_update_graph
    from code_review_graph.tools.query import get_impact_radius, query_graph
    from code_review_graph.tools.review import get_review_context

    for name, source in FIXTURE.items():
        (root / name).write_text(source, encoding="utf-8")
    (root / ".gitignore").write_text(".code-review-graph/\ntool-state/\n__pycache__/\n")
    run(root, "git", "-c", "init.defaultBranch=main", "init", "--quiet")
    run(root, "git", "add", ".")
    run(root, "git", "-c", "user.name=Graph Demo", "-c", "user.email=demo@example.invalid",
        "-c", "commit.gpgsign=false", "commit", "--quiet", "-m", "Synthetic baseline")

    built = build_or_update_graph(full_rebuild=True, repo_root=str(root), postprocess="minimal")
    assert built["status"] == "ok", built
    run(root, sys.executable, "-B", "-m", "unittest", "test_orders")
    assert cross_tenant_denied(root)
    target = str(root / "store.py") + "::get_order"
    name_query = query_graph("callers_of", "get_order", repo_root=str(root))
    before = query_graph("callers_of", target, repo_root=str(root))
    assert before["status"] == "ok", before

    (root / "store.py").write_text(UNSAFE_STORE, encoding="utf-8")
    updated = build_or_update_graph(repo_root=str(root), base="HEAD", postprocess="minimal")
    assert updated["status"] == "ok", updated
    after = query_graph("callers_of", target, repo_root=str(root))
    assert after["status"] == "ok", after
    references = query_graph("references_to", target, repo_root=str(root))
    impact = get_impact_radius(changed_files=["store.py"], repo_root=str(root), base="HEAD")
    context = get_review_context(changed_files=["store.py"], repo_root=str(root), base="HEAD")
    run(root, sys.executable, "-B", "-m", "unittest", "test_orders")
    unsafe_denied = cross_tenant_denied(root)
    assert not unsafe_denied

    (root / "store.py").write_text(SAFE_STORE, encoding="utf-8")
    run(root, sys.executable, "-B", "-m", "unittest", "test_orders")
    repaired_denied = cross_tenant_denied(root)
    assert repaired_denied

    def caller_names(response):
        return sorted({row.get("name", "") for row in response.get("results", [])})

    assert caller_names(before) == caller_names(after) == ["get_order_handler"]
    distribution = importlib.metadata.distribution("code-review-graph")
    direct_url = json.loads(distribution.read_text("direct_url.json") or "{}")
    assert direct_url.get("vcs_info", {}).get("commit_id") == SOURCE_SHA, "Run with the pinned Git dependency"

    return sanitize({
        "scope": "Synthetic graph and SQLite experiment; no model review or end-to-end token measurement",
        "source_sha": SOURCE_SHA,
        "python": sys.version.split()[0],
        "graph_version": importlib.metadata.version("code-review-graph"),
        "grammar_pack_version": importlib.metadata.version("tree-sitter-language-pack"),
        "fixture_source_files": sorted(FIXTURE),
        "full_build": {key: built.get(key) for key in ("status", "files_parsed", "total_nodes", "total_edges", "errors")},
        "incremental_update": {key: updated.get(key) for key in ("status", "files_updated", "changed_files", "dependent_files", "errors")},
        "caller_names_before": caller_names(before),
        "caller_names_after": caller_names(after),
        "bare_name_query_status": name_query["status"],
        "caller_query": after,
        "reference_query": references,
        "impact": impact,
        "review_context": context,
        "behavior": {
            "safe_existing_own_order_test_passes": True,
            "safe_cross_tenant_denied": True,
            "unsafe_existing_own_order_test_passes": True,
            "unsafe_cross_tenant_denied": unsafe_denied,
            "repaired_cross_tenant_denied": repaired_denied,
            "repaired_existing_own_order_test_passes": True,
        },
    }, root)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="Write a sanitized JSON report outside the temporary fixture")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="code-review-graph-demo-") as directory:
        result = experiment(Path(directory).resolve())
    rendered = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")


if __name__ == "__main__":
    main()
