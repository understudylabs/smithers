"""TS-shape runtime CLI: `smithers-ts up | approve | deny | inspect | ps`.

Independent from the v1.0.0 ``smithers-py`` CLI in ``__main__.py``. Uses
the ``smithers_py.runtime`` runner against TS-shape workflows authored
with ``create_smithers`` + ``WorkflowNode``-style trees.

Examples:

    # Run a workflow.py against an input JSON file.
    smithers-ts up workflow.py --input '{"workload":"demo"}' --db smithers.db

    # Approve a paused gate.
    smithers-ts approve <runId> --note "lgtm" --by "luis"

    # Inspect a run.
    smithers-ts inspect <runId>

    # List recent runs.
    smithers-ts ps
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import signal
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from .runner import (
    RunStatus,
    WorkflowError,
    approve_run,
    deny_run,
    inspect_run,
    list_runs,
    run_workflow,
)
from .store import Store


_DEFAULT_DB = "smithers.db"


# ----- Workflow module loading ------------------------------------------------


def _load_workflow_module(path: str):
    """Import a Python file by path. Returns the module object.

    If the file is inside a Python package (its directory contains
    ``__init__.py``), walks up to the package root, adds the *parent* of
    that root to ``sys.path``, and imports by dotted module path so
    package-relative imports (``from .components import ...``) work.
    Otherwise falls back to a standalone file load.
    """
    file_path = Path(path).resolve()
    if not file_path.exists():
        raise SystemExit(f"workflow file not found: {file_path}")

    # Walk up from the file's directory while __init__.py exists to find
    # the package root.
    pkg_chain: List[str] = []
    cursor = file_path.parent
    while (cursor / "__init__.py").exists():
        pkg_chain.append(cursor.name)
        cursor = cursor.parent

    if pkg_chain:
        sys.path.insert(0, str(cursor))
        dotted = ".".join(reversed(pkg_chain)) + "." + file_path.stem
        module = importlib.import_module(dotted)
        return module

    spec = importlib.util.spec_from_file_location(
        f"_smithers_user_{file_path.stem}", str(file_path)
    )
    if spec is None or spec.loader is None:
        raise SystemExit(f"could not load workflow module from {file_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _find_workflow_functions(module) -> List[Callable[..., Any]]:
    """Find all functions decorated with @config.workflow in a module."""
    out: List[Callable[..., Any]] = []
    for name in dir(module):
        if name.startswith("_"):
            continue
        obj = getattr(module, name)
        if callable(obj) and getattr(obj, "_smithers_workflow", False) is True:
            out.append(obj)
    return out


def _resolve_workflow(
    module, name: Optional[str]
) -> Callable[..., Any]:
    """Pick which workflow to run. Single-workflow modules just work; for
    multi-workflow modules, pass ``--workflow NAME``."""
    candidates = _find_workflow_functions(module)
    if not candidates:
        raise SystemExit(
            f"no @config.workflow function found in {module.__file__!r}; "
            "decorate the workflow function with @config.workflow"
        )
    if name is None:
        if len(candidates) > 1:
            names = ", ".join(c.__name__ for c in candidates)
            raise SystemExit(
                f"multiple workflows found ({names}); "
                "pass --workflow NAME to disambiguate"
            )
        return candidates[0]
    for c in candidates:
        if c.__name__ == name:
            return c
    names = ", ".join(c.__name__ for c in candidates)
    raise SystemExit(f"workflow {name!r} not found; available: {names}")


def _load_input(raw: Optional[str]) -> Dict[str, Any]:
    if raw is None:
        return {}
    raw = raw.strip()
    if raw.startswith("@"):
        path = Path(raw[1:])
        if not path.exists():
            raise SystemExit(f"input file not found: {path}")
        return json.loads(path.read_text())
    return json.loads(raw)


# ----- Commands ---------------------------------------------------------------


def cmd_up(args: argparse.Namespace) -> int:
    module = _load_workflow_module(args.workflow_file)
    workflow_fn = _resolve_workflow(module, args.workflow)
    input_payload = _load_input(args.input) if not args.resume else (
        _load_input(args.input) if args.input is not None else None
    )

    # SIGINT (Ctrl-C) handler — mark the run as cancelled and exit.
    # We capture the run id below; the signal handler closes over it.
    _cancel_state: Dict[str, Any] = {"run_id": args.run_id, "db_path": args.db}

    def _on_sigint(signum, frame):  # noqa: ARG001 - signature is fixed
        run_id = _cancel_state.get("run_id")
        if run_id:
            try:
                store = Store(_cancel_state["db_path"])
                store.connect()
                store.update_run_status(run_id, "cancelled")
            except Exception:  # noqa: BLE001 - best-effort cleanup
                pass
        print(
            "\n[smithers-ts] interrupted; run "
            f"{_cancel_state.get('run_id') or '(no id yet)'} "
            "marked as cancelled (if it was already started)",
            file=sys.stderr,
        )
        sys.exit(130)

    signal.signal(signal.SIGINT, _on_sigint)

    try:
        result = run_workflow(
            workflow_fn,
            input=input_payload,
            db_path=args.db,
            run_id=args.run_id,
            resume=args.resume,
            force=args.force,
        )
    except WorkflowError as exc:
        print(f"smithers-ts: {exc}", file=sys.stderr)
        return 2

    # Reset signal handler so post-run printing isn't interrupted weirdly.
    signal.signal(signal.SIGINT, signal.SIG_DFL)

    print(json.dumps(_summarize(result), indent=2, default=str))
    if result.status == RunStatus.PAUSED:
        print(
            "\nWaiting on approval. Next:\n"
            f"  smithers-ts approve {result.run_id} --note 'lgtm'\n"
            f"  smithers-ts up {args.workflow_file} --run-id {result.run_id} --resume\n",
            file=sys.stderr,
        )
        return 3
    if result.status == RunStatus.FAILED:
        return 1
    return 0


def cmd_approve(args: argparse.Namespace) -> int:
    approval = approve_run(
        args.run_id,
        db_path=args.db,
        node_id=args.node,
        note=args.note,
        decided_by=args.by,
    )
    print(json.dumps(approval.__dict__, indent=2, default=str))
    return 0


def cmd_deny(args: argparse.Namespace) -> int:
    approval = deny_run(
        args.run_id,
        db_path=args.db,
        node_id=args.node,
        note=args.note,
        decided_by=args.by,
    )
    print(json.dumps(approval.__dict__, indent=2, default=str))
    return 0


def cmd_inspect(args: argparse.Namespace) -> int:
    try:
        info = inspect_run(args.run_id, db_path=args.db)
    except WorkflowError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(json.dumps(info, indent=2, default=str))
    return 0


def cmd_ps(args: argparse.Namespace) -> int:
    rows = list_runs(db_path=args.db, status=args.status, limit=args.limit)
    if args.json:
        print(json.dumps(rows, indent=2, default=str))
        return 0
    if not rows:
        print("(no runs)")
        return 0
    print(f"{'RUN ID':<48} {'WORKFLOW':<24} {'STATUS':<10} STARTED")
    for r in rows:
        started = r["started_at"]
        print(
            f"{r['run_id']:<48} {r['workflow_name'][:24]:<24} {r['status']:<10} "
            f"{started:.0f}"
        )
    return 0


# ----- Helpers ---------------------------------------------------------------


def _summarize(result) -> Dict[str, Any]:
    return {
        "run_id": result.run_id,
        "workflow_name": result.workflow_name,
        "status": result.status.value,
        "output": result.output,
        "error": result.error,
        "pending_approvals": [
            {
                "approval_id": a.approval_id,
                "node_id": a.node_id,
                "title": a.title,
                "summary": a.summary,
                "kind": a.kind,
                "on_deny": a.on_deny,
            }
            for a in result.pending_approvals
        ],
        "output_rows": [
            {
                "node_id": r["node_id"],
                "schema_version": r["schema_version"],
                "output_name": r["output_name"],
            }
            for r in result.output_rows
        ],
    }


# ----- Parser -----------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="smithers-ts",
        description="TS-shape Smithers workflow runner (Python).",
    )
    parser.add_argument(
        "--db", default=_DEFAULT_DB, help=f"SQLite DB path (default: {_DEFAULT_DB})"
    )

    sub = parser.add_subparsers(dest="cmd", required=True)

    up = sub.add_parser("up", help="Run a workflow file")
    up.add_argument("workflow_file", help="Path to a Python file with a @config.workflow function")
    up.add_argument("--workflow", help="Workflow function name (required if multiple defined)")
    up.add_argument(
        "--input",
        "-i",
        help='Input payload. Either a JSON string or @path/to/file.json',
    )
    up.add_argument("--run-id", help="Explicit run ID; required with --resume")
    up.add_argument(
        "--resume",
        action="store_true",
        help="Resume an existing run by its run-id",
    )
    up.add_argument(
        "--force",
        action="store_true",
        help=(
            "Take over a run that is still marked 'running' "
            "(e.g., after a crash). Without this, refusing to resume "
            "an in-flight run is the safety default."
        ),
    )
    up.set_defaults(func=cmd_up)

    appr = sub.add_parser("approve", help="Approve a paused gate")
    appr.add_argument("run_id")
    appr.add_argument("--node", help="Specific node id (required if multiple pending)")
    appr.add_argument("--note", help="Approval note")
    appr.add_argument("--by", help="Approver identity")
    appr.set_defaults(func=cmd_approve)

    deny = sub.add_parser("deny", help="Deny a paused gate")
    deny.add_argument("run_id")
    deny.add_argument("--node")
    deny.add_argument("--note")
    deny.add_argument("--by")
    deny.set_defaults(func=cmd_deny)

    insp = sub.add_parser("inspect", help="Inspect a run's state and output rows")
    insp.add_argument("run_id")
    insp.set_defaults(func=cmd_inspect)

    ps = sub.add_parser("ps", help="List recent runs")
    ps.add_argument("--status", choices=["running", "paused", "completed", "failed", "cancelled"])
    ps.add_argument("--limit", type=int, default=50)
    ps.add_argument("--json", action="store_true")
    ps.set_defaults(func=cmd_ps)

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
