#!/bin/sh
# Local helper: rebuild the task-salvage corpus detached from the tool shell.
set -e
cd /root/tp-agent/work/m9398
rm -rf artifacts/task_salvage/missions artifacts/task_salvage/corpus.json artifacts/task_salvage/acceptance.json
exec python3 -m tools.task_salvage.cli collect --limit 20