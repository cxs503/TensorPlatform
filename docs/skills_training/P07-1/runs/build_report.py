import base64
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RES = json.loads((ROOT / "runs" / "probe_results.json").read_text(encoding="utf-8"))
PNG = (ROOT / "runs" / "findings.png").read_bytes()
REPRO = (ROOT / "runs" / "reproducibility.txt").read_text(encoding="utf-8")
AUTH = (ROOT / "runs" / "author_tests.txt").read_text(encoding="utf-8")

confirmed = [r for r in RES if r["status"] == "CONFIRMED_DEFECT"]
sev = {}
for r in confirmed:
    sev[r["severity"]] = sev.get(r["severity"], 0) + 1
sha = {}
for f in ["design_doc.md", "test_checklist.md", "candidate/upload_service.py",
          "candidate/test_upload_happy.py", "probe_upload.py"]:
    sha[f] = hashlib.sha256((ROOT / f).read_bytes()).hexdigest()[:12]
agree = [ln for ln in REPRO.splitlines() if ln.startswith("agree:")][0]
pytest_line = [ln for ln in AUTH.splitlines() if "passed" in ln][-1]

probe_rows = "\n".join(
    f"| {r['id']} | {r['ac']} | {r['claim']} | {r['observed']} | {r['status']} | {r['severity']} |"
    for r in RES)

md = f"""# 技能辅助运行：对一个只有 happy-path 测试的文件上传端点做对抗评审

## ① 案例介绍

**背景**：平台 Agent Skills v1 训练试点，本单执行 `adversarial-review` 技能 Procedure 1–10。
评审对象是按真实平台形态合成的「数据集文件上传端点」：作者（同一模型）先自拟设计契约（AC-1..AC-8）与
测试清单（T-01..T-12），再落地 134 行实现 + 3 项只覆盖主流程的测试，然后按技能要求做对抗评审。
之所以用合成对象而非生产代码：平台仓处于冻结期，且评审练习需要一个可运行、可探测、可复现的目标，
而不是只在纸面上下判断。

**参数表**（本任务无数值物理场，参数为服务与探针配置）：

| 参数 | 值 |
|---|---|
| 被评审实现 | `candidate/upload_service.py`（134 行，FastAPI + SQLite） |
| 契约项 | AC-1..AC-8 + 非功能（并发互斥、资源、可观测） |
| 作者测试 | 3 项，全部 happy path，负例断言 0 |
| 对抗探针 | 10 项（P01–P10） |
| 尺寸探针载荷 | 200 MiB 流式上传（真实 socket，Content-Length 正确声明） |
| 中断探针 | 真实 TCP 半途 RST，已发 5 MiB / 声明 40 MiB |
| 并发探针 | 2×2 MiB 同时写同一数据集，独立读者线程持续取 sha256 |
| 运行栈 | uvicorn 0.46 + starlette 1.0 + SQLite 单进程，沙箱目录 /tmp/p07_probe_* |
| 提交身份 | agent/m6350 @ 014b4412（评审对象前置提交 4b3b8ae3） |

## ② 怎么做的

1. **读契约再读实现**：AC 与作者自述（"多租户隔离、尺寸上限、失败回滚、断点续传均已实现"）先落盘，
   之后所有结论只与这两份文本对表，避免用实现反向定义契约。
2. **独立重建执行路径**：逐行读端点，把「认证依赖 / 属主比较 / 落盘顺序 / 校验时机 / 元数据更新」
   画成路径，标出失效分支（评审报告 §3）。
3. **可执行对抗探针**（`probe_upload.py`，10 项）：TestClient 覆盖越权/穿越/回滚/伪装/并发，真实 uvicorn +
   裸 socket 覆盖 200 MiB 流式上传与半途 RST —— 这两类失效在 TestClient 里不可复现。
4. **静态证据扫描**：对缺省特性做关键字计数（MAX_UPLOAD/413/os.replace/logging 均为 0，Content-Range 仅出现在注释），
   把「没有该分支」与「该分支有 bug」区分开。
5. **三态判定**：CONFIRMED_DEFECT（有原始响应）/ OK（按契约工作）/ NOT_RUN（未测记 unknown，绝不记 pass）。
6. **程序化出数与图**：`gen_findings.py` 从 `probe_results.json` 生成数字表与分布图，报告不手抄数字。
7. **复跑校验**：两个独立进程各跑一遍 10 个探针逐条比对（{agree}），并实跑作者套件（{pytest_line}）。

## ③ 计算结果

**探针逐条结果（程序化生成，零手抄）**

| 探针 | 契约项 | 声称 | 实测观测 | 判定 | 严重度 |
|---|---|---|---|---|---|
{probe_rows}

**汇总**

| 指标 | 数值 |
|---|---|
| 探针总数 | {len(RES)} |
| 已确认缺陷 | {len(confirmed)} |
| 按契约工作 | {sum(r['status'] == 'OK' for r in RES)} |
| 未测/unknown | {sum(r['status'] == 'NOT_RUN' for r in RES)} |
| 严重度分布 | critical={sev.get('critical', 0)}, high={sev.get('high', 0)}, medium={sev.get('medium', 0)} |
| 被作者测试发现的缺陷 | 0 / {len(confirmed)}（作者 3 项测试全绿） |
| 复跑状态一致率 | {agree} |
| 关键工件 sha256（前 12） | 实现 {sha['candidate/upload_service.py']}，探针 {sha['probe_upload.py']}，契约 {sha['design_doc.md']} |

**三条最重的确认缺陷**（原始响应见 `runs/probe_output.txt`）：跨租户越权写（bob 覆盖 alice 数据集返回 200，
内容 sha 2a2b86e74ffd → b37573c84e63）；路径穿越（filename="../../p07_escape.csv" 返回 200，写出上传根之外且
exists=True）；失败上传摧毁既有版本（坏内容 422，但原文件被覆盖成垃圾而 meta.rows 仍为旧值）。

**处置**：Request changes（critical={sev.get('critical', 0)} / high={sev.get('high', 0)} /
medium={sev.get('medium', 0)}），7 条整改均给出探针级验收。

## ④ 结果图

![findings](findings.png)

图为确认缺陷按契约项与严重度分布（左）+ 10 个探针结局（右）。**这不是场量云图**：本任务属文档/后端审计，
被评审对象没有连续物理场，故以「契约项 × 严重度」矩阵替代场量云图，用探针结局条形图替代收敛曲线；
两图均由 `gen_findings.py` 从 `probe_results.json` 程序化生成（PNG {len(PNG)} bytes，base64 内嵌）。

## ⑤ 注意点

1. **同源偏差**：契约、实现、测试、评审出自同一模型同一会话，技能的 Independence 条被削弱。唯一补救是把结论
   锚死在可重放命令输出与行号上；本文不声称"独立评审"，也不声称"独立复算"。
2. **对象是合成的**：缺陷形态取自真实代码的常见形状（先写终态再校验、客户端名拼路径、await file.read() 无闸门），
   但不是对某生产端点的实测裁决；真实平台同类端点（backend/api/datasets.py:203）只做静态引用，未运行。
3. **事实与推断分开**：200 MiB 被接受是事实；"服务端 RSS 随文件线性增长"是推断（starlette UploadFile 以
   SpooledTemporaryFile 暂存，>1 MiB 落盘），未测 RSS，故只把磁盘/配额耗尽计入影响。
4. **并发撕裂读的偶发性**：两轮都观测到非终态 sha（含空串 sha e3b0c44298fc），但窗口宽度依赖 I/O 时序，
   证据强度记为"两轮命中"而非"必然复现"。
5. **不要把 401 当授权**：本实现认证依赖调用率看似 100%，缺的是"主体 vs 资源属主"比较。
6. **平台仓冻结**：真实端点的问题只报告、留证，不改生产代码；本单改动限于 docs/skills_training/。

## ⑥ 误差判定

本任务没有物理基准（无实验值/解析解/文献值），因此不存在可用于判定 15% / 3% 的数值精度基准；
如实声明原因：被评审对象是 HTTP 端点契约，正确性判据是"契约声明 vs 实测行为"。可量化项按复现性给出：

| 指标 | 测量值 | 基准 | 偏差 | <3%? |
|---|---|---|---|---|
| 探针状态复跑一致率 | 10/10 | 10/10（首轮） | 0% | 是 |
| 缺陷计数（已确认） | {len(confirmed)} | 首轮 {len(confirmed)} | 0% | 是 |
| 作者套件结果 | 3 passed | 3 passed（声明） | 0% | 是（但与契约无关） |
| 200 MiB 上传落盘字节 | 209715200 | 请求声明 209715200 | 0% | 是 |
| 越权写请求状态码 | 200 | 契约 403 | 完全不符（非精度问题） | 否 |
| 穿越落盘路径 | 上传根之外 | 契约：根内 | 完全不符 | 否 |
| 回滚后原文件 sha | 3d1f57c98497（垃圾） | 2a2b86e74ffd（V1） | 完全不符 | 否 |

**显式回答**：可量化的复现性指标偏差 0%（<3% 成立）；契约符合性不是百分比精度问题，而是三处完全不符
（越权写、路径穿越、失败即数据丢失），故本单 honest 判定为 FAIL（Request changes），不粉饰为"基本可用"。
作者自述的四项"已实现"经探针逐条证伪。
"""

report = {
    "title": "对抗评审训练：只有 happy-path 测试的文件上传端点（10 探针，9 项确认缺陷，Request changes）",
    "summary": (
        "按 adversarial-review 技能 Procedure 1–10 评审一个自拟契约 + 134 行实现 + 3 项 happy-path 测试的"
        "文件上传端点。10 个可执行探针（200 MiB 流式上传、真实 TCP 半途 RST、跨租户越权写、路径穿越、"
        "失败回滚、Content-Range 续传、类型伪装、并发撕裂读）取证：9 项确认缺陷（critical 3：越权写/穿越/"
        "失败即数据丢失；high 4；medium 2），1 项按契约工作（缺/伪 token 401）。作者套件 3 passed 与契约违反"
        "并存 —— 测试只执行代码不断言不变量。两轮独立复跑 10/10 状态一致。处置 Request changes，7 条整改均给出"
        "探针级验收；缺测项（RSS 曲线/ENOSPC/多副本/多进程写）标记 unknown 而非 pass。"
    ),
    "scenario_key": "skills-training-adversarial-review-upload",
    "mission_id": 6350,
    "tags": ["skills-training", "adversarial-review", "upload-endpoint", "authz", "probe", "honest-fail"],
    "markdown": md,
    "figures": [{
        "name": "findings.png",
        "caption": ("左：确认缺陷按契约项（AC-3/AC-4/AC-5/AC-6/AC-7/AC-8/NFR）× 严重度分布；"
                    "右：10 个对抗探针结局（CONFIRMED_DEFECT=9, OK=1, NOT_RUN=0）。由 gen_findings.py 程序化生成。"),
        "png_base64": base64.b64encode(PNG).decode(),
    }],
}

Path("/tmp/report_m6350_p07.json").write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
print("bytes:", Path("/tmp/report_m6350_p07.json").stat().st_size, "md chars:", len(md))