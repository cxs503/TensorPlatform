#!/usr/bin/env python3
"""Portable launcher for the upstream autonomous worker; explicit configuration only."""
import argparse
import importlib.util
import os
from pathlib import Path
import runpy
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--model")
    parser.add_argument("--claim-null", action="store_true")
    args = parser.parse_args()
    os.environ.setdefault("FACTORY_PROFILE", str(ROOT / "config/factory.example.json"))
    from services.factory_profile import get_factory_profile
    profile = get_factory_profile()
    missing = [key for key in ("TENSOR_API_BASE", "TENSOR_AGENT_USER", "TENSOR_AGENT_PASS",
                               "LLM_BASE_URL", "LLM_API_KEY") if not os.getenv(key)]
    if not (args.model or os.getenv("LLM_MODEL")):
        missing.append("LLM_MODEL or --model")
    if missing:
        parser.error("Configure " + ", ".join(missing) + " before starting the worker")
    if sys.version_info < (3, 12):
        parser.error("The upstream OpenHands bridge requires a separate Python 3.12+ environment")
    for module in ("openhands.sdk", "openhands.tools", "litellm"):
        try:
            found = importlib.util.find_spec(module)
        except ModuleNotFoundError:
            found = None
        if found is None:
            parser.error(f"Worker dependency missing: {module}")
    for project in profile.projects.values():
        if not (Path(project["path"]) / ".git").exists():
            parser.error("Every factory project must point to an existing Git checkout")
    print("Factory worker configuration present; provider connectivity and execution are not yet verified.")
    if args.check:
        return
    os.environ.setdefault("LLM_GATEWAY_STATE", str(profile.state_root / "llm_gateway_state.json"))
    sys.path.insert(0, str(ROOT / "tools/agent"))
    sys.argv = [str(ROOT / "tools/agent/tensor_agent.py")]
    if args.model:
        sys.argv += ["--model", args.model]
    if args.claim_null:
        sys.argv.append("--claim-null")
    runpy.run_path(sys.argv[0], run_name="__main__")


if __name__ == "__main__":
    main()
