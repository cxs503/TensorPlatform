"""PR-9（AOG P1）：任务平面基础设施定义门禁。

P0 审计（报告 #3554）：任务平面宿主 cron/nohup 临时化，重装=失忆。
本套件把「守护收进库」钉成可红门：

1. **三方一致**：manifest 清单 = supervise.py 拉起集 = 本文件固定清单
   ——少收一个守护就是失忆复发，多收一个就是未审变更；
2. **脚本存在**：manifest 引用的每个脚本真实在库；
3. **净化扫描**：任务平面目录不得出现字面量凭据（本门在收库时点真实
   抓到过三处：PGPASSWORD 字面量×1+默认值×3、平台账密×1——能红实证）；
4. **语法**：全部 .py 可编译、.sh 过 bash -n；
5. **compose 结构**：单服务 + restart 策略 + healthcheck + 挂载库内源；
6. **env 覆盖**：脚本引用的环境变量 ⊆ env 模板键集（漏键=起不来）。
"""
from __future__ import annotations

import pathlib
import re
import subprocess

import pytest

import yaml

REPO = pathlib.Path(__file__).resolve().parents[2]
TASKPLANE = REPO / "ops" / "taskplane"
DEPLOY = REPO / "deploy" / "taskplane"
MANIFEST = TASKPLANE / "taskplane.manifest.yaml"
SUPERVISE = TASKPLANE / "supervise.py"
COMPOSE = DEPLOY / "docker-compose.taskplane.yml"
ENV_EXAMPLE = DEPLOY / "taskplane.env.example"

# 固定清单（2026-11-04 生产 ps 快照定谳；增删守护须三处同步）
PINNED = {
    "agent-directory", "campaign-dispatcher", "credit-ledger",
    "fleet-governor", "fleet-supervisor", "guide-tracker",
    "merge-governor", "paper-line", "report-qa", "report-watch",
    "runtime-sync", "selfstarter", "shadow-merge", "tp-autoheal",
    "wave-cycle", "auto-approver", "eco-patrol", "tp-pusher",
}
DAEMON_FIELDS = {"name", "script", "cadence", "enabled", "live", "note"}


def _manifest():
    return yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))


def _supervise_lists():
    """静态抽取 supervise.py 的 DAEMONS 与 GATED 脚本集。"""
    src = SUPERVISE.read_text(encoding="utf-8")
    daemon_block = re.search(r"DAEMONS = \[(.*?)\]", src, re.S).group(1)
    gated_block = re.search(r"GATED = \{(.*?)\}", src, re.S).group(1)
    daemons = set(re.findall(r'"([\w.]+\.(?:py|sh))"', daemon_block))
    gated = set(re.findall(r'"([\w.]+\.(?:py|sh))"\s*:', gated_block))
    return daemons, gated


class TestThreeWayAgreement:
    """manifest = supervise.py = 固定清单（失忆复发即红）。"""

    def test_manifest_pins_match_hardcoded_list(self):
        m = _manifest()
        assert set(m["pinned_daemons"]) == PINNED, "固定清单与 manifest 锚不一致"

    def test_manifest_entries_match_pins(self):
        m = _manifest()
        names = {d["name"] for d in m["daemons"]}
        assert names == set(m["pinned_daemons"]), "manifest daemons 与锚清单不一致"

    def test_manifest_fields_complete(self):
        for d in _manifest()["daemons"]:
            missing = DAEMON_FIELDS - set(d)
            assert not missing, f"{d.get('name')} 缺字段 {missing}"
            # YAML 1.1 布尔陷阱：enabled: true 解析为 True，归一化后判
            assert str(d["enabled"]).lower() in ("true", "gated"), d["name"]
            assert d["live"] in ("running", "stopped"), d["name"]

    def test_supervise_starts_exactly_enabled_plus_gated(self):
        m = _manifest()
        enabled_scripts = {d["script"] for d in m["daemons"]
                           if str(d["enabled"]).lower() == "true"}
        gated_scripts = {d["script"] for d in m["daemons"]
                         if str(d["enabled"]).lower() == "gated"}
        daemons, gated = _supervise_lists()
        assert daemons == enabled_scripts, (
            f"supervise DAEMONS 与 manifest enabled 集不一致: "
            f"多={daemons - enabled_scripts} 少={enabled_scripts - daemons}")
        assert gated == gated_scripts, (
            f"supervise GATED 与 manifest gated 集不一致: "
            f"多={gated - gated_scripts} 少={gated_scripts - gated}")

    def test_supervise_gated_vars_covered_in_env_example(self):
        env = ENV_EXAMPLE.read_text(encoding="utf-8")
        src = SUPERVISE.read_text(encoding="utf-8")
        gated_block = re.search(r"GATED = \{(.*?)\}", src, re.S).group(1)
        for var in re.findall(r'"\w+\.(?:py|sh)":\s*"(\w+)"', gated_block):
            assert re.search(rf"^{var}=1", env, re.M) or re.search(rf"^{var}=0", env, re.M), \
                f"门控变量 {var} 未在 env 模板声明"


class TestScriptsPresent:
    def test_every_manifest_script_exists(self):
        for d in _manifest()["daemons"]:
            assert (TASKPLANE / d["script"]).is_file(), f"{d['script']} 不在库内"

    def test_fleet_roster_tracked(self):
        # fleet.json 是编制表（配置），不在 *_state.json 忽略面
        assert (TASKPLANE / "fleet.json").is_file(), "tp_worker 编制表未收库"


class TestSanitized:
    """净化扫描：字面量凭据即红（曾真实抓到三处）。"""

    FILES = sorted(p for p in TASKPLANE.rglob("*")
                   if p.is_file() and p.suffix in (".py", ".sh", ".yaml", ".yml", ".json")
                   and ".git" not in p.parts) + [
        DEPLOY / "Dockerfile", COMPOSE, ENV_EXAMPLE]

    def test_no_literal_credentials(self):
        offenders = []
        patterns = [
            (r"ghp_[A-Za-z0-9]{16,}", "GitHub PAT"),
            (r"gho_[A-Za-z0-9]{16,}", "GitHub OAuth"),
            (r"AKIA[0-9A-Z]{16}", "AWS key"),
            (r"sk-[A-Za-z0-9]{20,}", "API key"),
        ]
        for f in self.FILES:
            text = f.read_text(encoding="utf-8", errors="ignore")
            for pat, label in patterns:
                for m in re.finditer(pat, text):
                    offenders.append(f"{f.name}: {label}")
            # PGPASSWORD 只允许空默认的环境变量形态
            for m in re.finditer(r'PGPASSWORD[^=\n]*=\s*["\']([^"\']*)["\']', text):
                if m.group(1).strip():
                    offenders.append(f"{f.name}: PGPASSWORD 非空字面量")
            # json.dumps 登录载荷不得携带字面量密码
            for m in re.finditer(r'"password":\s*"([^"]+)"', text):
                if not m.group(1).startswith(('" + pw', "pw", '" + ')):
                    offenders.append(f"{f.name}: 登录载荷字面量密码")
        assert offenders == [], f"任务平面存在字面量凭据: {sorted(set(offenders))}"

    def test_env_example_has_no_values(self):
        # 敏感键必须为空；非敏感键只允许本机回环 URL/用户名/0-1 开关等已知良性默认
        benign = {"ECO_ADMIN_USER": "eco_admin"}
        for line in ENV_EXAMPLE.read_text(encoding="utf-8").splitlines():
            m = re.match(r"^([A-Z_]+)=(.*)$", line.strip())
            if not m:
                continue
            key, val = m.group(1), m.group(2)
            if any(s in key for s in ("PASS", "TOKEN", "SECRET", "KEY")):
                assert val == "", f"敏感键 {key} 不得有默认值"
            elif val:
                ok = (val.startswith("http://127.0.0.1") or val in ("0", "1")
                      or benign.get(key) == val)
                assert ok, f"env 模板疑似携带真实值: {line}"


class TestSyntax:
    def test_python_scripts_compile(self):
        for f in sorted(TASKPLANE.glob("*.py")):
            compile(f.read_bytes(), str(f), "exec")

    def test_shell_scripts_pass_bash_n(self):
        for f in sorted(TASKPLANE.glob("*.sh")):
            r = subprocess.run(["bash", "-n", str(f)], capture_output=True)
            assert r.returncode == 0, f"{f.name}: {r.stderr.decode()[:200]}"

    def test_manifest_and_compose_parse(self):
        yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))
        yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))


class TestComposeStructure:
    def test_single_taskplane_service_with_supervisor(self):
        c = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
        assert set(c["services"]) == {"taskplane"}, "任务平面=单服务统一监护"
        svc = c["services"]["taskplane"]
        assert svc["restart"] == "unless-stopped", "容器级重启策略必须声明"
        assert "healthcheck" in svc, "healthcheck 必须声明（双锚探活）"
        assert svc["env_file"] == "taskplane.env"
        assert any("ops/taskplane:/root/tp-agent" in v for v in svc["volumes"]), \
            "库内源必须挂载为守护路径假设 /root/tp-agent"
        assert svc["command"] == ["python3", "/root/tp-agent/supervise.py"]

    def test_healthcheck_anchors_both_planes(self):
        hc = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))["services"]["taskplane"]["healthcheck"]
        cmd = " ".join(hc["test"])
        assert "merge_governor" in cmd and "fleet_supervisor" in cmd, \
            "探活须同时锚治理面与舰队面"

    def test_dockerfile_installs_runtime_tools(self):
        df = (DEPLOY / "Dockerfile").read_text(encoding="utf-8")
        for tool in ("git", "postgresql-client", "procps"):
            assert tool in df, f"Dockerfile 缺 {tool}（worktree/psql/探活依赖）"


class TestEnvCoverage:
    def test_script_env_refs_covered_by_example(self):
        wanted = set()
        for f in sorted(TASKPLANE.glob("*.py")) + sorted(TASKPLANE.glob("*.sh")):
            src = f.read_text(encoding="utf-8", errors="ignore")
            wanted |= set(re.findall(r'environ\.get\("([A-Z_]+)"', src))
            wanted |= set(re.findall(r'getenv\("([A-Z_]+)"', src))
        declared = set(re.findall(r"^([A-Z_]+)=", ENV_EXAMPLE.read_text(encoding="utf-8"), re.M))
        # supervise.py 自身的门控变量与 TP_BASE 已在模板/结构门覆盖
        missing = wanted - declared - {"TP_BASE"}
        assert not missing, f"脚本引用的环境变量未进 env 模板: {sorted(missing)}"
