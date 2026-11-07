"""四中心最小视图（PR-10 首期）前端面机器判据——零假数据 / 四页可达 / 未实现如实标注。

判据
----
F1 四页可达：路由表注册了 ``center/organization|quality|capability|innovation`` 四条，
   四个 view 文件存在，且各自 import 的是能与路由同名的组件文件。
F2 单一真值绑定：每个 view 只调 ``centersApi.<center>()``（不自己拼 URL、不自己算分母），
   且 ``api/index.ts`` 里的路径与后端 ``api/tensor_centers.py`` 的路由逐字一致
   （前端契约 ↔ 后端路由的静态对拍）。
F3 零假数据：四页 + 共享板组件里没有 mock/faker/随机数/写死的数字文案；
   template 的**纯数字文本节点**一个都不许有（`>42<` 这种假数占比一律判红）。
F4 未实现如实标注：创新页与共享板必须把 ``status=not_implemented`` 渲染成「未实现」，
   且未实现项不得渲染出数字。
F5 阴性对照：把一条真实指标文案改成裸数字后，F3 扫描器必须变红（证扫描器非摆设）。

全程离线、只读工作树文本，不访问网络/数据库。
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CONSOLE = ROOT / "frontend-console" / "src"
ROUTER = CONSOLE / "router" / "index.ts"
API_CLIENT = CONSOLE / "api" / "index.ts"
BOARD = CONSOLE / "components" / "CenterBoard.vue"
BACKEND = ROOT / "backend" / "api" / "tensor_centers.py"

CENTERS = {
    "organization": "OrganizationCenterView.vue",
    "quality": "QualityCenterView.vue",
    "capability": "CapabilityCenterView.vue",
    "innovation": "InnovationCenterView.vue",
}

MOCK_PATTERNS = [
    r"\bmock\b", r"\bfaker\b", r"Math\.random", r"\bdummy\b",
    r"lorem ipsum", r"hardcode", r"\bstub\b",
]


def _view(center: str) -> str:
    return (CONSOLE / "views" / CENTERS[center]).read_text(encoding="utf-8")


def _template(source: str) -> str:
    m = re.search(r"<template>(.*)</template>", source, re.S)
    assert m, "SFC 必须有 <template>"
    return m.group(1)


def _text_nodes(template: str) -> list[str]:
    """剥离标签，返回剩下的文本节点（用于抓裸数字假数据）。"""
    return [n.strip() for n in re.split(r"<[^>]*>", template) if n.strip()]


# --------------------------------------------------------------------------- #
# F1 四页可达
# --------------------------------------------------------------------------- #
def test_router_registers_four_centers():
    router = ROUTER.read_text(encoding="utf-8")
    for center in CENTERS:
        assert f"path: 'center/{center}'" in router, f"路由缺 center/{center}"
        assert f"@/views/{CENTERS[center].removesuffix('.vue')}.vue" in router, center


def test_four_view_files_exist_and_import_board():
    for center, filename in CENTERS.items():
        path = CONSOLE / "views" / filename
        assert path.is_file(), f"缺视图 {filename}"
        src = path.read_text(encoding="utf-8")
        assert "components/CenterBoard.vue" in src, f"{filename} 未用共享真值板"
        assert "HeroPanel.vue" in src, f"{filename} 缺页头"


# --------------------------------------------------------------------------- #
# F2 单一真值绑定（前端契约 ↔ 后端路由静态对拍）
# --------------------------------------------------------------------------- #
def test_each_view_binds_its_own_center_endpoint():
    for center in CENTERS:
        src = _view(center)
        assert f"centersApi.{center}(" in src, f"{center} 页未绑定 centersApi.{center}()"
        # 不许自己发请求/拼 URL（绕过统一载荷就绕过了审计链）
        assert "http.get(" not in src and "axios" not in src, f"{center} 页自己发请求"
        assert "centersApi" in src
    client = API_CLIENT.read_text(encoding="utf-8")
    backend = BACKEND.read_text(encoding="utf-8")
    for center in CENTERS:
        assert f"{center}: () => http.get<any>('/tensor/centers/{center}')" in client, center
        assert f'@router.get("/{center}"' in backend, f"后端缺 /tensor/centers/{center}"


def test_backend_is_registered_in_tensor_domain():
    tensor_domain = (ROOT / "backend" / "api" / "domain" / "tensor.py").read_text(encoding="utf-8")
    assert "tensor_centers import router" in tensor_domain
    assert "router.include_router(tensor_centers_router)" in tensor_domain


# --------------------------------------------------------------------------- #
# F3 零假数据
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("path", [BOARD] + [CONSOLE / "views" / f for f in CENTERS.values()],
                         ids=lambda p: p.name)
def test_no_mock_tokens(path):
    src = path.read_text(encoding="utf-8")
    for pattern in MOCK_PATTERNS:
        assert not re.search(pattern, src, re.I), f"{path.name} 出现 mock 味 token: {pattern}"


@pytest.mark.parametrize("center", list(CENTERS))
def test_template_has_no_numeric_text_node(center):
    """写死的数字文案＝假数据的最短路径：template 文本节点不许是纯数字。"""
    template = _template(_view(center))
    offenders = [n for n in _text_nodes(template) if re.fullmatch(r"[\d.,%\s]+", n)]
    assert not offenders, f"{center} 页模板出现裸数字文本节点（假数据嫌疑）：{offenders}"


def test_no_numeric_literals_in_script_metric_arrays():
    """脚本里也不许预置数字指标（例如 metrics=[{value: 12}]）。"""
    for center in CENTERS:
        src = _view(center)
        assert not re.search(r"value:\s*\d", src), f"{center} 页脚本内出现写死的指标值"


def test_board_renders_only_backend_values():
    src = BOARD.read_text(encoding="utf-8")
    # 展示值只走 display(payload 里的 metric)，且缺测/未实现都有明确的非数字出口
    assert "props.payload" in src
    assert "m.status === 'not_implemented'" in src
    assert "return '未实现'" in src
    assert "return '—'" in src
    assert "row.source" in src, "指标必须同屏展示 SQL 口径（审计链）"


# --------------------------------------------------------------------------- #
# F4 未实现如实标注
# --------------------------------------------------------------------------- #
def test_innovation_page_marks_unimplemented():
    src = _view("innovation")
    assert "未实现" in src
    assert "not_implemented" in src
    assert "implemented" in src


def test_board_renders_not_implemented_list():
    src = BOARD.read_text(encoding="utf-8")
    assert "not_implemented" in src
    assert "未实现" in src
    # 未实现面必须有出口（没有就明说没有），不能靠留空装作没有这一面
    assert "本中心无「未实现」声明项" in src


# --------------------------------------------------------------------------- #
# F5 阴性对照：扫描器必须能红
# --------------------------------------------------------------------------- #
def test_numeric_scanner_has_teeth():
    fake_template = "<div><span>42</span><span>{{ m.value }}</span></div>"
    offenders = [n for n in _text_nodes(fake_template) if re.fullmatch(r"[\d.,%\s]+", n)]
    assert offenders == ["42"], "扫描器抓不到裸数字 → F3 是恒真摆设"
    assert not [n for n in _text_nodes("<div>{{ m.value }}</div>")
                if re.fullmatch(r"[\d.,%\s]+", n)]