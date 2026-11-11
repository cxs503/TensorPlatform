#!/usr/bin/env bash
# 提交出口（pre-commit 钩子）：扫 git 索引快照，命中即拦。
# 安装：ln -sf ../../scripts/pre_commit_secret_gate.sh .git/hooks/pre-commit
set -euo pipefail
ROOT="$(git rev-parse --show-toplevel)"
exec python3 "$ROOT/tools/secret_hygiene/cli.py" --root "$ROOT" staged --mode block
