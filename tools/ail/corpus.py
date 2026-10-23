"""真实记录抽取器：把三个仓库的真实工件读成 Record 列表（带来源与 sha256）。

纪律：
- **只读**。任何写入都抛 AILCorpusError；抽取到的快照写到本包 corpus/ 下（受控目录）。
- 每条记录带 source（路径 + json_pointer/章节 + sha256 + retrieved_at），
  保证每个判分数字都能回到原文件核对（AIL_DATA_SPEC §3）。
- 抽取是确定性的：同文件同内容 → 同 Record 集合、同顺序，不读 mtime 之外的易变状态。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

ECOSYSTEM_ROOT = Path(os.environ.get("AIL_ECO_ROOT", "/srv/tensor/shared/tensor_eco"))
PLATFORM_ROOT = Path(__file__).resolve().parents[2]
AGENT_MEMORY_ROOT = Path(os.environ.get(
    "AIL_AGENT_MEMORY_ROOT", str(ECOSYSTEM_ROOT / "agent_memory" / "platform-operator")))

# 抽取时点（写入记录的 valid_from / retrieved_at 依据）。固定值保证可复现；
# 真实运行时由命令行 --retrieved-at 覆盖。
DEFAULT_RETRIEVED_AT = "2026-10-23T07:00:00Z"


class AILCorpusError(RuntimeError):
    """语料读取失败（缺文件/越权写/内容不可解析）。"""


@dataclass
class Record:
    record_id: str            # 稳定 ID（来源内唯一）
    corpus: str               # tensorplatform | tensorphysics | tensordem
    source_path: str          # 相对生态根的可读路径（或绝对路径，见 kind）
    pointer: str              # json pointer 或 markdown 章节标识
    sha256: str               # 来源文件内容哈希
    retrieved_at: str
    kind_hint: str            # 抽取器对记录类型的建议（state/decision/evidence/...）
    title: str
    raw_text: str             # 原样文本（A0 原始历史臂直接用它）
    value: object | None = None
    topic_hint: str | None = None
    tags: list[str] = field(default_factory=list)
    # 记录自身携带的时间（事件发生时点）。缺省则退化为 retrieved_at。
    # 有它才能把"同一主题随时间演进"（SPEC C2）与"真矛盾"（C1）区分开。
    valid_from: str | None = None

    def to_dict(self) -> dict:
        return {"record_id": self.record_id, "corpus": self.corpus,
                "source_path": self.source_path, "pointer": self.pointer,
                "sha256": self.sha256, "retrieved_at": self.retrieved_at,
                "kind_hint": self.kind_hint, "title": self.title,
                "raw_text": self.raw_text, "value": self.value,
                "topic_hint": self.topic_hint, "tags": list(self.tags)}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_json(path: Path) -> tuple[dict, str]:
    if not path.is_file():
        raise AILCorpusError(f"语料文件不存在：{path}")
    raw = path.read_text(encoding="utf-8")
    return json.loads(raw), sha256_file(path)


def _read_text(path: Path) -> tuple[str, str]:
    if not path.is_file():
        raise AILCorpusError(f"语料文件不存在：{path}")
    return path.read_text(encoding="utf-8"), sha256_file(path)


_TS_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})")


def _norm_ts(value) -> str | None:
    """把来源里的时间规范成 SPEC 要求的秒精度 UTC（容忍 +00:00 / 微秒 / 本地偏移）。"""
    if not isinstance(value, str):
        return None
    match = _TS_RE.match(value.strip())
    if not match:
        return None
    return "%s-%s-%sT%s:%s:%sZ" % match.groups()


def _slug(text: str, *, limit: int = 40) -> str:
    slug = re.sub(r"[^a-z0-9_]+", "_", text.casefold()).strip("_")
    return (slug[:limit] or "x")


def _rel(path: Path) -> str:
    """优先相对生态根，避免把本机绝对路径写进工件。"""
    try:
        return str(path.relative_to(ECOSYSTEM_ROOT))
    except ValueError:
        try:
            return str(path.relative_to(PLATFORM_ROOT))
        except ValueError:
            return str(path)


def _pointer_value(doc: dict, pointer: str):
    cur = doc
    for part in pointer.strip("/").split("/"):
        if isinstance(cur, list):
            cur = cur[int(part)]
        else:
            cur = cur[part]
    return cur


def _mk(*, record_id: str, corpus: str, path: Path, pointer: str, sha: str,
        retrieved_at: str, kind_hint: str, title: str, raw_text: str,
        value=None, topic_hint: str | None = None, tags=None,
        valid_from: str | None = None) -> Record:
    return Record(record_id=record_id, corpus=corpus, source_path=_rel(path),
                  pointer=pointer, sha256=sha, retrieved_at=retrieved_at,
                  kind_hint=kind_hint, title=title, raw_text=raw_text, value=value,
                  topic_hint=topic_hint, tags=list(tags or []), valid_from=valid_from)


# ── TensorPlatform ────────────────────────────────────────────────────────
def extract_tensorplatform(*, retrieved_at: str = DEFAULT_RETRIEVED_AT) -> list[Record]:
    records: list[Record] = []
    log_dir = PLATFORM_ROOT / "operator_log"

    # (1) m192 班次真实读数（json pointer 逐项取值，值必须逐字保留）
    readings_name = "2026-10-23.m192.readings.json"
    readings_path = log_dir / readings_name
    doc, sha = _read_json(readings_path)
    sh = readings_path.name
    specs = [
        ("inflight", "state", "/1_inflight/count", "platform.shift.inflight_count",
         "m192 班次在飞任务数", "在飞任务数是必须逐字精确的状态量"),
        ("guardian", "state", "/2_guardian/pgrep_total", "platform.guardian.count_identity",
         "守护进程实测总数", "守护数三量等式是巡检判据核心"),
        ("guardian_base", "state", "/2_guardian/base_instances",
         "platform.guardian.base_instances", "守护基线实例数", "基线 6 + 池化槽 10 = 16"),
        ("relay", "state", "/3_relay/http_code", "platform.relay.http_code",
         "LLM 中继 HTTP 状态码", "中继健康判据"),
        ("relay_t", "evidence", "/3_relay/time_total", "platform.relay.time_total",
         "中继响应耗时（秒）", "判据要求 < 1s"),
        ("cooldown", "state", "/4_cooldown/expired", "platform.cooldown.expired",
         "冷却态是否已过期", "冷却态清理判据"),
        ("failed24", "state", "/5_terminal_24h/failed_count",
         "platform.shift.failed_24h", "24h 内 failed 任务数", "失败面统计"),
        ("gw_unavail", "state", "/5_terminal_24h/gateway_unavailable_count",
         "platform.shift.gateway_unavailable_24h", "24h 内 gateway_unavailable 数",
         "17 of 18 failed 是关键归因"),
        ("approved", "state", "/approvals/approved", "platform.approvals.approved",
         "审批已批准数", "审批面状态"),
        ("pending", "state", "/approvals/pending", "platform.approvals.pending",
         "审批待处理数", "审批面状态"),
        ("rejected", "state", "/approvals/rejected", "platform.approvals.rejected",
         "审批已驳回数", "审批面状态"),
        ("model", "fact", "/model", "platform.shift.model", "本班模型",
         "班次元信息"),
    ]
    for sid, kind, ptr, topic, title, why in specs:
        value = _pointer_value(doc, ptr)
        records.append(_mk(record_id=f"m192-readings-{sid}", corpus="tensorplatform",
                           path=readings_path, pointer=f"{sh}#{ptr}", sha=sha,
                           retrieved_at=retrieved_at, kind_hint=kind, title=title,
                           raw_text=json.dumps({ptr: value}, ensure_ascii=False),
                           value=value, topic_hint=topic, tags=["m192", "operator_log", why]))

    # (2) m192 判据报告（逐条 check → 判据证据）
    report_name = "2026-10-23.m192.validation_report.json"
    report_path = log_dir / report_name
    rdoc, rsha = _read_json(report_path)
    for idx, check in enumerate(rdoc["checks"]):
        records.append(_mk(
            record_id=f"m192-check-{idx}-{check['name']}", corpus="tensorplatform",
            path=report_path, pointer=f"{report_name}#/checks/{idx}", sha=rsha,
            retrieved_at=retrieved_at, kind_hint="evidence",
            title=f"判据 {check['name']}",
            raw_text=json.dumps(check, ensure_ascii=False),
            value=str(check["passed"]).lower(),
            topic_hint=f"platform.checks.{check['name']}",
            tags=["m192", "validation_report"]))

    # (3) 10-21 班次读数（更早的原始历史，用于 A0 臂与时间演进/取代场景）
    old_path = log_dir / "2026-10-21.readings.json"
    odoc, osha = _read_json(old_path)
    for idx, item in enumerate(odoc["items"][:24]):
        records.append(_mk(
            record_id=f"m156-readings-{idx}-{item['name']}", corpus="tensorplatform",
            path=old_path, pointer=f"{old_path.name}#/items/{idx}", sha=osha,
            retrieved_at=retrieved_at, kind_hint="state",
            title=f"2026-10-21 读数 {item['name']}",
            raw_text=json.dumps(item, ensure_ascii=False),
            value=str(item.get("stdout", "")).strip()[:80],
            topic_hint=f"platform.shift_20261021.{item['name']}",
            tags=["m156", "operator_log", "history"]))

    # (4) 常驻记忆机器账本（只读；终态事实）
    mem_path = AGENT_MEMORY_ROOT / "memory.jsonl"
    if mem_path.is_file():
        mem_sha = sha256_file(mem_path)
        for lineno, line in enumerate(mem_path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                # 真实账本存在非 JSON 行（手工追加）。**不静默丢弃**：作为不可解析记录入册，
                # verification 记 unknown，让"答案可能缺依据"这件事在检索结果里可见。
                records.append(_mk(
                    record_id=f"opmem-{lineno}-raw", corpus="tensorplatform", path=mem_path,
                    pointer=f"memory.jsonl#{lineno}", sha=mem_sha, retrieved_at=retrieved_at,
                    kind_hint="lesson", title="常驻记忆非 JSON 行（手工追加）",
                    raw_text=line.strip()[:1500], value=None,
                    topic_hint="platform.agent_memory.unparsed_line",
                    tags=["agent_memory", "unparsed"],
                    valid_from=_norm_ts(line.strip()[1:21]) if line.strip().startswith("[")
                    else None))
                continue
            records.append(_mk(
                record_id=f"opmem-{lineno}", corpus="tensorplatform", path=mem_path,
                pointer=f"memory.jsonl#{lineno}", sha=mem_sha, retrieved_at=retrieved_at,
                # 账本行是**终态事实**（状态值），不是"决策"：把它标成 decision 会让
                # 大量同构历史条目在 intent=decision 检索里拿到满结构分，挤掉真正相关的条目。
                kind_hint="state" if entry.get("status") else "fact",
                title=f"m{entry.get('mission')} 终态 {entry.get('status')}",
                raw_text=json.dumps(entry, ensure_ascii=False),
                value=entry.get("status"),
                # 账本里既有 mission 级记录，也有 event 级/无 mission 的记录：
                # topic 必须始终是合法点分小写，因此按实际字段退化。
                topic_hint=(f"platform.mission.{entry['mission']}.terminal_status"
                            if entry.get("mission") is not None else
                            f"platform.agent_memory.event.{_slug(str(entry.get('event') or entry.get('status') or 'record'))}"),
                tags=["agent_memory", "terminal_status"
                      if entry.get("mission") is not None else "event"],
                valid_from=_norm_ts(entry.get("ts"))))

    # (5) 交接笔记（context.md 的 ## / ### 标题块 → 决策/下一步记录）
    ctx_path = AGENT_MEMORY_ROOT / "context.md"
    if ctx_path.is_file():
        ctx_text, ctx_sha = _read_text(ctx_path)
        section = None
        buffer: list[str] = []
        blocks: list[tuple[str, str]] = []
        for line in ctx_text.splitlines():
            if line.startswith("## ") or line.startswith("### "):
                if section is not None:
                    blocks.append((section, "\n".join(buffer).strip()))
                section, buffer = line.lstrip("#").strip(), []
            elif section is not None:
                buffer.append(line)
        if section is not None:
            blocks.append((section, "\n".join(buffer).strip()))
        for idx, (heading, body) in enumerate(blocks):
            if not body:
                continue
            # §12 的 L1 层要保住"下一步"：交接笔记里的待办/结论段落必须标成 next_action /
            # decision，而不是一律当 decision —— 否则"下一班先做什么"检索不到。
            lowered = heading.casefold()
            kind = ("next_action" if any(k in lowered for k in ("待办", "todo", "下一步", "next"))
                    else "lesson" if any(k in lowered for k in ("教训", "坑", "issue"))
                    else "decision")
            records.append(_mk(
                record_id=f"opctx-{idx}", corpus="tensorplatform", path=ctx_path,
                pointer=f"context.md#section:{heading}", sha=ctx_sha,
                retrieved_at=retrieved_at, kind_hint=kind,
                title=f"交接笔记章节：{heading}",
                raw_text=f"## {heading}\n{body}"[:3000],
                value=heading, topic_hint=f"platform.handoff.{_slug(heading, limit=32)}",
                tags=["agent_memory", "handoff"]))
            # 段落/条目级记录：交接笔记的每一段都是**可独立判分的断言**
            # （"守护按 Σ=16 读 workers.json" 这类细节不能被整段吞掉）。
            for item_index, (paragraph, para_kind) in enumerate(_paragraphs(body)):
                clause = _first_clause(paragraph, 110)
                if not clause:
                    continue
                item_kind = "next_action" if para_kind == "todo" else kind
                records.append(_mk(
                    record_id=f"opctx-{idx}-item-{item_index}", corpus="tensorplatform",
                    path=ctx_path,
                    pointer=f"context.md#section:{heading}/paragraph:{item_index}",
                    sha=ctx_sha, retrieved_at=retrieved_at, kind_hint=item_kind,
                    title=f"{heading} / 第 {item_index + 1} 段",
                    raw_text=paragraph[:900], value=clause,
                    topic_hint=(f"platform.handoff.{_slug(heading, limit=20)}"
                                f".p{item_index}"),
                    tags=["agent_memory", "handoff", para_kind]))

    return records


# ── TensorPhysics ─────────────────────────────────────────────────────────
def extract_tensorphysics(*, retrieved_at: str = DEFAULT_RETRIEVED_AT) -> list[Record]:
    records: list[Record] = []
    base = ECOSYSTEM_ROOT / "TensorPhysics"
    goal_path = base / "outputs/wedge-icebreaking-s0/research_goal.json"
    goal, gsha = _read_json(goal_path)
    records.append(_mk(
        record_id="phys-goal-objective", corpus="tensorphysics", path=goal_path,
        pointer="research_goal.json#/objective", sha=gsha, retrieved_at=retrieved_at,
        kind_hint="constraint", title="S0 goal objective",
        raw_text=json.dumps({"objective": goal["objective"]}, ensure_ascii=False),
        value=goal["constraints"]["stage"], topic_hint="physics.s0.stage",
        tags=["wedge_icebreaking", "s0"]))
    for key, topic, title in (("device", "physics.s0.device", "S0 设备约束"),
                              ("coupling", "physics.s0.coupling", "S0 耦合模式")):
        records.append(_mk(
            record_id=f"phys-goal-{key}", corpus="tensorphysics", path=goal_path,
            pointer=f"research_goal.json#/constraints/{key}", sha=gsha,
            retrieved_at=retrieved_at, kind_hint="constraint", title=title,
            raw_text=json.dumps({key: goal["constraints"][key]}, ensure_ascii=False),
            value=goal["constraints"][key], topic_hint=topic,
            tags=["wedge_icebreaking", "s0"]))
    for idx, crit in enumerate(goal["acceptance_criteria"]):
        records.append(_mk(
            record_id=f"phys-goal-ac{idx}", corpus="tensorphysics", path=goal_path,
            pointer=f"research_goal.json#/acceptance_criteria/{idx}", sha=gsha,
            retrieved_at=retrieved_at, kind_hint="constraint",
            title=f"验收标准 {idx}", raw_text=json.dumps(crit, ensure_ascii=False),
            value=crit[:80], topic_hint=f"physics.s0.acceptance_0{idx + 1}",
            tags=["wedge_icebreaking", "acceptance"]))

    rep_path = base / "outputs/wedge-icebreaking-s0/s0_report.json"
    rep, rsha = _read_json(rep_path)
    records.append(_mk(
        record_id="phys-report-decision", corpus="tensorphysics", path=rep_path,
        pointer="s0_report.json#/decision", sha=rsha, retrieved_at=retrieved_at,
        kind_hint="decision", title="S0 判决", raw_text=json.dumps(
            {"decision": rep["decision"], "scientific_status": rep["scientific_status"]},
            ensure_ascii=False), value=rep["decision"], topic_hint="physics.s0.decision",
        tags=["wedge_icebreaking", "decision"]))
    records.append(_mk(
        record_id="phys-report-sci", corpus="tensorphysics", path=rep_path,
        pointer="s0_report.json#/scientific_status", sha=rsha, retrieved_at=retrieved_at,
        kind_hint="constraint", title="S0 科学状态",
        raw_text=json.dumps({"scientific_status": rep["scientific_status"]},
                            ensure_ascii=False), value=rep["scientific_status"],
        topic_hint="physics.s0.scientific_status", tags=["wedge_icebreaking"]))
    records.append(_mk(
        record_id="phys-report-next", corpus="tensorphysics", path=rep_path,
        pointer="s0_report.json#/next_stage", sha=rsha, retrieved_at=retrieved_at,
        kind_hint="next_action", title="S0 下一阶段", raw_text=json.dumps(
            {"next_stage": rep["next_stage"]}, ensure_ascii=False),
        value=rep["next_stage"][:80], topic_hint="physics.s0.next_stage",
        tags=["wedge_icebreaking", "next_action"]))
    for leg_index, leg in enumerate(rep["legs"]):
        records.append(_mk(
            record_id=f"phys-leg-{leg['leg']}", corpus="tensorphysics", path=rep_path,
            pointer=f"s0_report.json#/legs/{leg_index}", sha=rsha,
            retrieved_at=retrieved_at, kind_hint="state",
            title=f"腿 {leg['leg']} 校验状态",
            raw_text=json.dumps({k: leg[k] for k in ("leg", "run_id", "validation_status",
                                                     "solver_returncode", "next_action")},
                                ensure_ascii=False),
            value=leg["validation_status"],
            topic_hint=f"physics.s0.leg.{leg['leg']}.validation_status",
            tags=["wedge_icebreaking", "leg"]))
        for check_index, check in enumerate(leg.get("checks") or []):
            records.append(_mk(
                record_id=f"phys-leg-{leg['leg']}-check-{check_index}", corpus="tensorphysics",
                path=rep_path, pointer=f"s0_report.json#/legs/{leg_index}/checks/{check_index}",
                sha=rsha, retrieved_at=retrieved_at, kind_hint="evidence",
                title=f"腿 {leg['leg']} 检查 {check.get('name', check_index)}",
                raw_text=json.dumps(check, ensure_ascii=False)[:1200],
                value=check.get("ok", check.get("passed")),
                topic_hint=f"physics.s0.leg.{leg['leg']}.check.{_slug(str(check.get('name', check_index)))}",
                tags=["wedge_icebreaking", "check"]))
    return records


# ── TensorDEM ─────────────────────────────────────────────────────────────
def extract_tensordem(*, retrieved_at: str = DEFAULT_RETRIEVED_AT) -> list[Record]:
    records: list[Record] = []
    base = ECOSYSTEM_ROOT / "TensorDEM"
    for name in ("three_point_bending_cases", "ucs_cases", "mechanics_cases",
                 "specimen_response_cases", "platen_compression_cases", "cases"):
        path = base / f"benchmarks/dem3d/{name}.json"
        if not path.is_file():
            continue
        doc, sha = _read_json(path)
        cases = doc.get("cases") or []
        for idx, case in enumerate(cases):
            case_id = str(case.get("id", idx))
            case_slug = _slug(case_id)
            records.append(_mk(
                record_id=f"dem-{name}-{idx}-{case_id}", corpus="tensordem",
                path=path, pointer=f"{name}.json#/cases/{idx}", sha=sha,
                retrieved_at=retrieved_at, kind_hint="state",
                title=f"DEM case {case_id}",
                raw_text=json.dumps(case, ensure_ascii=False)[:2000],
                value=case_id,
                topic_hint=f"dem.case.{case_slug}.id",
                tags=["dem3d", name, "case"]))
            # 配置项逐个落成可检索条目：判据数字必须能精确取回（不是埋在 JSON 文本里）
            for cfg_key, cfg_value in (case.get("config") or {}).items():
                if isinstance(cfg_value, (dict, list)):
                    continue
                records.append(_mk(
                    record_id=f"dem-{name}-{idx}-{case_id}-cfg-{cfg_key}", corpus="tensordem",
                    path=path, pointer=f"{name}.json#/cases/{idx}/config/{cfg_key}", sha=sha,
                    retrieved_at=retrieved_at, kind_hint="evidence",
                    title=f"{case_id} 配置 {cfg_key}",
                    raw_text=json.dumps({cfg_key: cfg_value}, ensure_ascii=False),
                    value=cfg_value,
                    topic_hint=f"dem.case.{case_slug}.config.{_slug(cfg_key)}",
                    tags=["dem3d", name, "config"]))
            # 验收阈值同理
            for acc_key, acc_value in (case.get("acceptance") or {}).items():
                if isinstance(acc_value, (dict, list)):
                    continue
                records.append(_mk(
                    record_id=f"dem-{name}-{idx}-{case_id}-acc-{acc_key}", corpus="tensordem",
                    path=path, pointer=f"{name}.json#/cases/{idx}/acceptance/{acc_key}", sha=sha,
                    retrieved_at=retrieved_at, kind_hint="evidence",
                    title=f"{case_id} 验收 {acc_key}",
                    raw_text=json.dumps({acc_key: acc_value}, ensure_ascii=False),
                    value=acc_value,
                    topic_hint=f"dem.case.{case_slug}.acceptance.{_slug(acc_key)}",
                    tags=["dem3d", name, "acceptance"]))
        if doc.get("protocol"):
            records.append(_mk(
                record_id=f"dem-{name}-protocol", corpus="tensordem", path=path,
                pointer=f"{name}.json#/protocol", sha=sha, retrieved_at=retrieved_at,
                kind_hint="constraint", title=f"{name} 协议",
                raw_text=json.dumps({"protocol": doc["protocol"], "scope": doc.get("scope")},
                                    ensure_ascii=False),
                value=doc["protocol"], topic_hint=f"dem.protocol.{name}",
                tags=["dem3d", name]))

    docs = [
        ("docs/dem3d_energy_audit.md", "dem.energy_audit",
         "能量审计的验证边界", "constraint"),
        ("docs/verification_campaigns.md", "dem.campaigns",
         "验证 campaign 的范围说明", "constraint"),
        ("docs/dem3d_ucs_benchmark.md", "dem.ucs_benchmark",
         "UCS 基准范围", "fact"),
        ("docs/dem3d_three_point_bending.md", "dem.three_point_bending",
         "三点弯基准范围", "fact"),
    ]
    for rel, topic_stem, title, kind in docs:
        path = base / rel
        if not path.is_file():
            continue
        text, sha = _read_text(path)
        paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
        # 跳过纯标题段落，取第一段真正的正文（避免 value 变成 "## Purpose"）
        body = next((p for p in paragraphs if not p.startswith("#") and len(p) > 40),
                    paragraphs[0])
        records.append(_mk(
            record_id=f"dem-doc-{topic_stem.replace('.', '-')}", corpus="tensordem", path=path,
            pointer=f"{rel}#intro", sha=sha, retrieved_at=retrieved_at, kind_hint=kind,
            title=title, raw_text=body[:1500], value=_first_clause(body),
            topic_hint=f"{topic_stem}.scope", tags=["dem3d", "docs"]))
        # 章节级条目：把每个 markdown 二级标题下的正文单独成条（判据句必须能精确检索）
        for heading, section_body in _markdown_sections(text):
            if len(section_body) < 60:
                continue
            records.append(_mk(
                record_id=f"dem-doc-{topic_stem.replace('.', '-')}-{_slug(heading, limit=28)}",
                corpus="tensordem", path=path,
                pointer=f"{rel}#section:{heading}", sha=sha, retrieved_at=retrieved_at,
                kind_hint="constraint" if kind == "constraint" else "fact",
                title=f"{title} / {heading}",
                raw_text=f"## {heading}\n{section_body}"[:2000],
                value=_first_clause(section_body),
                topic_hint=f"{topic_stem}.{_slug(heading, limit=28)}",
                tags=["dem3d", "docs", _slug(heading, limit=20)]))
    return records


def _first_clause(text: str, limit: int = 80) -> str:
    """取首句的关键子句作为 value（单行、≤ limit 码点）。"""
    flat = re.sub(r"\s+", " ", text).strip()
    for sep in ("。", ";", ";", "."):
        head, found, _rest = flat.partition(sep)
        if found and len(head) >= 12:
            flat = head
            break
    return flat if len(flat) <= limit else flat[:limit]


_TODO_MARKERS = ("待办", "下一班", "todo", "next", "沿用", "先判")


def _paragraphs(body: str) -> list[tuple[str, str]]:
    """把章节正文切成段落（空行分段；编号列表项各自成段）。

    返回 [(文本, 标记)]，标记为 'todo'（含下一步语义）或 'para'。
    """
    blocks: list[str] = []
    buffer: list[str] = []
    for line in body.splitlines():
        stripped = line.strip()
        if not stripped:
            if buffer:
                blocks.append(" ".join(buffer).strip())
                buffer = []
            continue
        # 编号/项目符号项：新起一段（保留其缩进续行）
        if re.match(r"^(\d+[.)]|[-*+])\s", stripped) and buffer:
            blocks.append(" ".join(buffer).strip())
            buffer = [stripped]
        else:
            buffer.append(stripped)
    if buffer:
        blocks.append(" ".join(buffer).strip())
    out: list[tuple[str, str]] = []
    for block in blocks:
        lowered = block.casefold()
        marker = "todo" if any(m in lowered for m in _TODO_MARKERS) else "para"
        out.append((block, marker))
    return out


def _markdown_sections(text: str) -> list[tuple[str, str]]:
    """按 `## ` 切章节，返回 [(标题, 正文)]。"""
    sections: list[tuple[str, str]] = []
    heading: str | None = None
    buffer: list[str] = []
    for line in text.splitlines():
        if line.startswith("## "):
            if heading is not None:
                sections.append((heading, "\n".join(buffer).strip()))
            heading, buffer = line[3:].strip(), []
        elif heading is not None:
            buffer.append(line)
    if heading is not None:
        sections.append((heading, "\n".join(buffer).strip()))
    return sections


EXTRACTORS = {
    "tensorplatform": extract_tensorplatform,
    "tensorphysics": extract_tensorphysics,
    "tensordem": extract_tensordem,
}


def extract_all(*, retrieved_at: str = DEFAULT_RETRIEVED_AT) -> list[Record]:
    records: list[Record] = []
    for name in ("tensorplatform", "tensorphysics", "tensordem"):
        records.extend(EXTRACTORS[name](retrieved_at=retrieved_at))
    return records


def write_snapshot(records: list[Record], path: Path, *, retrieved_at: str) -> dict:
    """把记录写成快照（含来源清单）。只允许写到调用方给的受控路径。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    sources: dict[str, str] = {}
    for rec in records:
        sources.setdefault(rec.source_path, rec.sha256)
    payload = {
        "schema_version": 1,
        "retrieved_at": retrieved_at,
        "ecosystem_root": str(ECOSYSTEM_ROOT),
        "note": ("记录快照：由 scripts/ail_build_corpus.py 从三个仓库的真实工件只读抽取。"
                 "sha256 为抽取时来源文件内容哈希，可用 sha256sum 复核。"),
        "sources": [{"path": p, "sha256": s} for p, s in sorted(sources.items())],
        "record_count": len(records),
        "counts_by_corpus": {c: sum(1 for r in records if r.corpus == c)
                             for c in ("tensorplatform", "tensorphysics", "tensordem")},
        "records": [r.to_dict() for r in records],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return payload


def load_snapshot(path: Path) -> tuple[list[Record], dict]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    records = [Record(**rec) for rec in payload["records"]]
    return records, payload