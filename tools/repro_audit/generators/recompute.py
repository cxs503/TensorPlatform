"""repro_audit.generators.recompute —— 派生型生成器的共享引擎（零领域语义）。

**"派生型生成器"** 指的是：工件的头部汇总量（关键数字）由工件内部记录的明细行
按一条受限派生式重算出来。它回答的问题不是"这个数字是谁造的"，而是
"这个数字能不能从随它一起提交的原始行重算回来"——手改头部、裁剪明细、把整段
数字敲进去，都会在重放时翻红。

每个节点一个最小生成器（``gen_node_*.py``）只钉住三个常量（输入工件、输出工件、
通道清单），重算逻辑全部在这里：

* 输入：**提交树内**的工件 JSON（明细行）；
* 派生：``channels.json`` 里该工件每个通道的 ``expr``，交给 ``derive.evaluate``
  的 AST 白名单求值器（无 eval 面、无随机、无时间）；
* 输出：``{"artifact", "channels": {channel_id: value}, "inputs_sha256",
  "engine_sha256"}`` —— 两次运行逐字节相同。

**纪律**：本模块不写回被审计工件（输出永远另路），也不含任何学科词/船型/仓名；
通道清单是数据。派生式的合法性由 ``derive`` 的 AST 白名单兜底。
"""
from __future__ import annotations

import hashlib
import json
import os
import sys

from . import derive

__all__ = ["load_channels_for", "recompute", "run_node", "ENGINE_VERSION"]

ENGINE_VERSION = "p167w2-n10/2026-11-11"


def _sha256_file(path: str) -> str | None:
    try:
        h = hashlib.sha256()
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


def load_channels_for(artifact: str, channels_path: str) -> list[dict]:
    """从冻结通道清单里取出属于 ``artifact`` 的通道（顺序稳定）。"""
    with open(channels_path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    channels = data["channels"] if isinstance(data, dict) else data
    return [c for c in channels if c["artifact"] == artifact]


def recompute(root: dict, channels: list[dict]) -> dict:
    """按通道表达式重算头部值。任一通道求值失败即整体失败（fail-closed）。"""
    out: dict[str, object] = {}
    for ch in channels:
        expr = ch.get("expr")
        if not expr:
            raise ValueError(f"channel without expr: {ch['channel_id']}")
        out[ch["channel_id"]] = derive.evaluate(expr, root)
    return out


def run_node(input_path: str, output_path: str, channels_path: str | None = None,
             repo_root: str | None = None, artifact_rel: str | None = None) -> dict:
    """把 ``input_path`` 的明细行重算成 ``output_path``（不触碰输入工件）。

    ``artifact_rel``：通道清单里登记的被审计工件路径。取证重放时入量路径会被
    改到别处（``REPRO_INPUT``），此时仍按登记的工件取通道，否则取不到通道。
    """
    repo_root = os.path.abspath(repo_root or os.getcwd())
    channels_path = channels_path or os.path.join(
        repo_root, "artifacts/repro_audit/channels.json")
    if artifact_rel is None:
        artifact_rel = os.path.relpath(os.path.abspath(input_path), repo_root).replace(os.sep, "/")
    channels = load_channels_for(artifact_rel, channels_path)
    if not channels:
        raise SystemExit(f"no channel registered for {artifact_rel}")

    with open(input_path, "r", encoding="utf-8") as fh:
        root = json.load(fh)
    payload = {
        "artifact": artifact_rel,
        "engine": ENGINE_VERSION,
        "engine_sha256": _sha256_file(derive.__file__),
        "inputs_sha256": _sha256_file(input_path),
        "channels": recompute(root, channels),
    }
    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    body = json.dumps(payload, sort_keys=True, indent=2, ensure_ascii=False) + "\n"
    with open(output_path, "w", encoding="utf-8") as fh:
        fh.write(body)
    return payload


def main(argv: list[str] | None = None) -> int:  # pragma: no cover - CLI 壳
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) < 2:
        print("usage: recompute.py INPUT ARTIFACT OUTPUT ARTIFACT [CHANNELS_JSON]",
              file=sys.stderr)
        return 2
    payload = run_node(argv[0], argv[1], argv[2] if len(argv) > 2 else None)
    print(json.dumps({k: payload[k] for k in ("artifact", "inputs_sha256")}, sort_keys=True))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())