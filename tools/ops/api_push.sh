#!/usr/bin/env bash
# Git Data API 推送：github.com:443 断、api.github.com 通时的既有配方。
# 用法：api_push.sh <repo-owner/repo> <branch> <commit-sha> <base-sha-40hex>
# 产出：基于 base-sha、含 commit-sha 相对其父的文件变更的新 commit，推到 branch。
set -euo pipefail
REPO="$1"; BR="$2"; SHA="$3"; BASE_SHA="$4"
TOK=$(cat "${GH_TOKEN_FILE:-/tmp/gh_token}")
api() { # method path data —— 瞬时断连重试 3 次，返回体非 JSON 才放弃
  local m="$1" p="$2" d="${3:-}" out i
  for i in 1 2 3; do
    if [ -n "$d" ]; then
      out=$(curl -s --max-time 60 -X "$m" -H "Authorization: token $TOK" \
        -H "Accept: application/vnd.github+json" \
        "https://api.github.com/repos/$REPO/$p" -d "$d")
    else
      out=$(curl -s --max-time 60 -H "Authorization: token $TOK" \
        -H "Accept: application/vnd.github+json" \
        "https://api.github.com/repos/$REPO/$p")
    fi
    if echo "$out" | python3 -c 'import json,sys;json.load(sys.stdin)' 2>/dev/null; then
      printf "%s" "$out"
      return 0
    fi
    sleep 5
  done
  printf "%s" "$out"
  return 1
}
blob_post() { # 文件 → blob sha（大文件超 ARG_MAX，须走临时文件 @data-binary）
  local f="$1" tmp i out
  tmp=$(mktemp)
  python3 - "$f" "$tmp" <<'PY'
import base64, json, sys
p, out = sys.argv[1], sys.argv[2]
b64 = base64.b64encode(open(p, "rb").read()).decode()
json.dump({"content": b64, "encoding": "base64"}, open(out, "w"))
PY
  for i in 1 2 3; do
    out=$(curl -s --max-time 120 -X POST -H "Authorization: token $TOK" \
      -H "Accept: application/vnd.github+json" \
      "https://api.github.com/repos/$REPO/git/blobs" --data-binary @"$tmp")
    if printf "%s" "$out" | python3 -c 'import json,sys;print(json.load(sys.stdin)["sha"])' 2>/dev/null; then
      rm -f "$tmp"; return 0
    fi
    sleep 5
  done
  rm -f "$tmp"; printf "%s" "$out"; return 1
}
cd "$(git rev-parse --show-toplevel)"
PARENT=$(git rev-parse "$SHA^")
echo "local commit $SHA parent $PARENT  (remote base $BASE_SHA)"
# 1) 改动文件清单（含新增；多 commit 分支=对 base 全量 diff）
mapfile -t FILES < <(git diff --name-only "$BASE_SHA" "$SHA")
echo "files: ${FILES[*]}"
# 2) base tree（base commit 的 git commit 树）
BASE_TREE=$(api GET "commits/$BASE_SHA" | python3 -c 'import json,sys;print(json.load(sys.stdin)["commit"]["tree"]["sha"])')
echo "base tree $BASE_TREE"
# 3) blob 上传 + tree 条目
ENTRIES='['
first=1
for f in "${FILES[@]}"; do
  BLOB_SHA=$(blob_post "$f")
  [ $first -eq 1 ] || ENTRIES+=','
  first=0
  ENTRIES+="{\"path\":\"$f\",\"mode\":\"100644\",\"type\":\"blob\",\"sha\":\"$BLOB_SHA\"}"
  echo "  blob $f -> $BLOB_SHA"
done
ENTRIES+=']'
# 4) tree + commit
TREE_SHA=$(api POST git/trees "{\"base_tree\":\"$BASE_TREE\",\"tree\":$ENTRIES}" \
  | python3 -c 'import json,sys;print(json.load(sys.stdin)["sha"])')
MSG=$(git log -1 --format=%B "$SHA" | python3 -c 'import json,sys;print(json.dumps(sys.stdin.read().strip()))')
CMT=$(api POST git/commits "{\"message\":$MSG,\"tree\":\"$TREE_SHA\",\"parents\":[\"$BASE_SHA\"]}")
NEW_SHA=$(echo "$CMT" | python3 -c 'import json,sys;print(json.load(sys.stdin)["sha"])')
echo "api commit -> $NEW_SHA"
# 5) 远端分支在否
EXST=$(api GET "git/ref/heads/$BR" | python3 -c 'import json,sys;d=json.load(sys.stdin);print(d.get("object",{}).get("sha",""))' 2>/dev/null || true)
if [ -n "$EXST" ] && [ "$EXST" != "None" ]; then
  api PATCH "git/refs/heads/$BR" "{\"sha\":\"$NEW_SHA\",\"force\":false}" | python3 -c 'import json,sys;d=json.load(sys.stdin);print("updated ref:",d.get("object",{}).get("sha") or d)'
else
  api POST git/refs "{\"ref\":\"refs/heads/$BR\",\"sha\":\"$NEW_SHA\"}" | python3 -c 'import json,sys;d=json.load(sys.stdin);print("created ref:",d.get("object",{}).get("sha") or d)'
fi
