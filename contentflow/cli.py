from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from .metrics import build_recap
from .providers import build_provider
from .workflow import ContentMarketingWorkflow


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="ContentFlow local batch and metrics utility."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    local_run = subparsers.add_parser(
        "run-local",
        help="Run the reproducible local batch workflow without external publishing.",
    )
    local_run.add_argument(
        "--brief",
        type=Path,
        default=PROJECT_ROOT / "examples" / "brief.json",
    )
    local_run.add_argument(
        "--knowledge",
        type=Path,
        default=PROJECT_ROOT / "knowledge",
    )
    local_run.add_argument(
        "--workspace",
        type=Path,
        default=Path(os.getenv("CONTENTFLOW_WORKSPACE", ".contentflow")),
    )
    local_run.add_argument(
        "--provider",
        choices=("mock", "openai-compatible"),
        default=os.getenv("CONTENTFLOW_PROVIDER", "mock"),
    )

    recap = subparsers.add_parser("recap", help="Analyze exported platform metrics.")
    recap.add_argument(
        "--metrics",
        type=Path,
        default=PROJECT_ROOT / "examples" / "metrics.json",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    if args.command == "run-local":
        workflow = ContentMarketingWorkflow(
            workspace=args.workspace,
            provider=build_provider(args.provider),
        )
        result = workflow.run(load_json(args.brief), args.knowledge)
        summary = {
            "run_id": result["run_id"],
            "mode": result["mode"],
            "platforms": [
                {
                    "platform": item["platform"],
                    "status": item["status"],
                    "review_issues": item["review"]["issues"],
                }
                for item in result["contents"]
            ],
            "queue_size": len(result["publish_queue"]),
            "output_path": result["output_path"],
        }
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return

    print(
        json.dumps(
            build_recap(load_json(args.metrics)),
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
