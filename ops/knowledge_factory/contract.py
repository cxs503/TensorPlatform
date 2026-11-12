"""工作包契约：owner YAML 骨架 ↔ 可注入工包（零领域语义，全部形状来自 config/contracts 文件）。"""
import json
import os

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_CONFIG = os.path.join(HERE, "config.json")
DEFAULT_TEMPLATE = os.path.join(HERE, "contracts.template.json")


def load_config(path=None):
    with open(path or os.getenv("KF_CONFIG", DEFAULT_CONFIG), encoding="utf-8") as f:
        return json.load(f)


def load_contracts(cfg=None, template=None):
    """工序契约台账。KBM-RCI-F1-SPEC 产物放进 contract_dir/<type>.json 即覆盖模板同名工序。"""
    cfg = cfg or load_config()
    with open(template or DEFAULT_TEMPLATE, encoding="utf-8") as f:
        contracts = json.load(f)
    cdir = os.path.join(repo_root(), cfg["contract"]["contract_dir"])
    if os.path.isdir(cdir):
        for name in sorted(os.listdir(cdir)):
            if not name.endswith(".json"):
                continue
            ttype = os.path.splitext(name)[0]
            with open(os.path.join(cdir, name), encoding="utf-8") as f:
                contracts[ttype] = json.load(f)
    return contracts


def repo_root():
    return os.path.dirname(os.path.dirname(HERE))


def package_id(report_id, task_type):
    return f"wp-{report_id}-{task_type}"


def build_package(report, task_type, lane, cfg, contracts, mission_id=None, stratum=None,
                  source_field="markdown"):
    """把（报告, 工序, 泳道）装配成一份标准工作包（契约 + 输入指纹）。"""
    import hashlib

    body = report["markdown"] if source_field == "markdown" else report.get(source_field, "")
    digest = "sha256:" + hashlib.sha256(body.encode("utf-8")).hexdigest()
    tpl = contracts[task_type]
    pkg = {
        "package_id": package_id(report["id"], task_type),
        "task_type": tpl["id"] if "id" in tpl else cfg["task_types"][task_type]["id"],
        "contract_version": cfg["contract"]["version"],
        "mission_id": str(mission_id) if mission_id else None,
        "lane": lane,
        "input": {
            "report_id": str(report["id"]),
            "source_uri": f"tensor://reports/{report['id']}",
            "source_field": source_field,
            "source_hash": digest,
            "title": report.get("title"),
            "scenario_key": report.get("scenario_key"),
            "chars": len(body),
        },
        "objective": tpl["objective"],
        "constraints": tpl["constraints"],
        "output": tpl["output"],
        "acceptance": tpl["acceptance"],
        "review": tpl["review"],
        "delivery": tpl["delivery"],
        "notes": tpl.get("task_note", ""),
    }
    if stratum:
        pkg["notes"] = (pkg["notes"] + f" ｜分层={stratum}").strip()
    return pkg


def render_goal(pkg, cfg):
    """把工作包契约全文注入 mission goal（Agent 不需要另查契约）。"""
    ch = cfg["result_channel"]
    yaml_text = yaml.safe_dump(pkg, allow_unicode=True, sort_keys=False,
                               default_flow_style=False, width=100)
    return (
        f"【锚点=KBM-RCI-F1-FACTORY·工作包 {pkg['package_id']}】\n"
        f"现场指令（owner 2026-11-12 生产模式令）：你被派到一条报告→知识产线的单一工序上。"
        f"本单只做下面这一个工作包，不做别的。\n\n"
        f"=== 边界（硬约束）===\n"
        f"· 你只碰 report_id={pkg['input']['report_id']} 这一份报告；不得读/写/覆盖其它 report_id "
        f"（互不覆盖是产线正确性的前提）。\n"
        f"· 不得改写上游或其它工作包的产物；不得把结论标成 verified（verification 由平台门计算）。\n"
        f"· 零手抄：结果里的每个数字/引文必须能在报告原文里逐字定位，不许凭记忆重述。\n\n"
        f"=== 标准工作包契约（YAML 全文，机器门按此校验）===\n{yaml_text}\n"
        f"=== 原文获取（不许凭记忆）===\n"
        f"· 平台接口：GET $TENSOR_API_BASE/tensor/reports/{pkg['input']['report_id']}"
        f"（Bearer=$TENSOR_AGENT_USER 登录拿到的 token），取 `markdown` 字段。\n"
        f"· 或本机库：psql -d tensorplatform -tA -c \"select markdown from tensor_reports "
        f"where id={pkg['input']['report_id']}\"。\n"
        f"· 引文必须逐字来自该 markdown（平台独立重算 sha256 与子串，做二次确认）。\n\n"
        f"=== 交付方式（唯一结果信道）===\n{ch['howto']}\n"
        f"报告标题：`{ch['report_title_prefix']} {pkg['package_id']}`；"
        f"tags 必须包含 `{ch['tag_prefix']}{pkg['package_id']}`。\n"
        f"```json 块的结构见 schemas/kf_result.v1.schema.json（package_id/task_type/report_id/"
        f"provenance/outputs）。provenance.source_hash 必须与 source_uri 所指报告的 markdown "
        f"字节哈希一致（平台会独立重算，不一致判门失败）。\n\n"
        f"=== 报告正文 ===\n"
        f"六段：①本工序做了什么 ②怎么做的（步骤/工具/关键设置）③产出物清单（输出文件名→要点）"
        f"④证据定位表（claim/字段 → 原文引文片段）⑤注意点与局限 ⑥自检：逐条对照 acceptance 打勾/叉，"
        f"如有失败项如实写（honest-FAIL 不粉饰）。\n"
        f"repro_cmd 写清一键复跑命令。工作树根=你的仓库检出；产物落 worktree 内。"
    )


def enabled_task_types(contracts):
    """契约到位的工序才可派（03/05 等 SPEC）。"""
    return [t for t, c in contracts.items()
            if isinstance(c, dict) and c.get("status") in ("active", "platform_skeleton")]