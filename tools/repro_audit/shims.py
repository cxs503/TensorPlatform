"""重跑探针的导入面替身（headless / dependency-light replay）。

为什么需要它：集群节点只装了 ``pytest/numpy/scipy/sympy/requests``，而历史节点的工件脚本
普遍 ``import matplotlib``（画图）和 ``import fastapi/pydantic``（端点模块被测试 import）。
这些缺件会让「数值复证」被迫判 blocked——**但缺的恰好是绘图与 HTTP 外壳，不是物理代码**。

因此本模块提供**只替换导入面、不替换数值代码**的替身：

* ``matplotlib``：所有绘图对象是 no-op 哑对象；``subplots`` 按 nrows×ncols 返回元组/数组
  （绘图调用被吞掉，``json.dump`` 的数值路径完全不动）。
* ``fastapi`` / ``pydantic`` / ``starlette``：仅在 spec 显式声明 ``import_stubs`` 时启用，
  提供 ``APIRouter``/``HTTPException``/``BaseModel``/``Field`` 等最小外壳，使被测模块能被
  import；**不做任何请求校验**，因此本审计的判据永远只用数值比对结果，不用「端点是否响应」。

诚实边界（写进报告）：替身会让「API 层行为」不可复证，只保留「数值链路可复证」。
副作用被记录在 ``result.json`` 的 ``import_stubs`` 字段，供审计链复核。
"""

from __future__ import annotations

import sys
import types
from typing import Any, List


class _Dummy:
    """万能 no-op 对象：吸收任意调用/属性/下标/上下文管理。"""

    def __init__(self, *a, **k):
        pass

    def __call__(self, *a, **k):
        return _Dummy()

    def __getattr__(self, name):
        if name.startswith("__") and name.endswith("__"):
            raise AttributeError(name)
        return _Dummy()

    def __getitem__(self, k):
        return _Dummy()

    def __setitem__(self, k, v):
        pass

    def __iter__(self):
        return iter([_Dummy(), _Dummy()])

    def __len__(self):
        return 2

    def __float__(self):
        return 0.0

    # 绘图代码常把 get_width()/get_height() 之类拿去做算术（标注偏移、文本换行）
    def __add__(self, other):
        return _Dummy()

    __radd__ = __add__

    def __sub__(self, other):
        return _Dummy()

    __rsub__ = __sub__

    def __mul__(self, other):
        return _Dummy()

    __rmul__ = __mul__

    def __truediv__(self, other):
        return _Dummy()

    __rtruediv__ = __truediv__

    def __neg__(self):
        return _Dummy()

    def __abs__(self):
        return _Dummy()

    def __lt__(self, other):
        return False

    def __le__(self, other):
        return True

    def __gt__(self, other):
        return False

    def __ge__(self, other):
        return True

    def __eq__(self, other):
        return False

    def __hash__(self):
        return id(self)

    def __int__(self):
        return 0

    def __bool__(self):
        return True

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _AxesArray:
    """``plt.subplots(n>1)`` 的返回物：支持下标 / 迭代 / ravel() / flatten()。"""

    def __init__(self, n: int):
        self._n = max(int(n), 1)

    def __getitem__(self, k):
        return _Dummy()

    def __setitem__(self, k, v):
        pass

    def __iter__(self):
        return iter([_Dummy() for _ in range(self._n)])

    def __len__(self):
        return self._n

    def ravel(self):
        return [_Dummy() for _ in range(self._n)]

    def flatten(self):
        return self.ravel()

    def __getattr__(self, name):
        return _Dummy()


def _bare_module(name: str) -> types.ModuleType:
    mod = types.ModuleType(name)

    def __getattr__(attr):  # module-level PEP 562 hook
        if attr.startswith("__") and attr.endswith("__"):
            raise AttributeError(attr)
        return _Dummy()

    mod.__getattr__ = __getattr__
    return mod


def _install_matplotlib() -> None:
    names = ["matplotlib", "matplotlib.pyplot", "matplotlib.cm", "matplotlib.colors",
             "matplotlib.ticker", "matplotlib.gridspec", "matplotlib.figure",
             "matplotlib.axes", "matplotlib.lines", "matplotlib.patches", "matplotlib.path",
             "matplotlib.transforms", "matplotlib.legend", "matplotlib.dates",
             "matplotlib.collections", "matplotlib.patheffects", "matplotlib.bezier",
             "matplotlib.backends", "mpl_toolkits", "mpl_toolkits.axes_grid1"]
    mods = {n: _bare_module(n) for n in names}
    py = mods["matplotlib.pyplot"]

    def subplots(nrows=1, ncols=1, *a, **k):
        n = int(nrows) * int(ncols)
        if n <= 1:
            return _Dummy(), _Dummy()
        return _Dummy(), _AxesArray(n)

    py.subplots = subplots
    for fn in ("subplot", "figure", "gcf", "gca", "savefig", "close", "show",
               "tight_layout", "subplots_adjust", "suptitle", "colorbar", "legend",
               "style"):
        setattr(py, fn, lambda *a, **k: _Dummy())
    py.rcParams = {}
    py.rcParamsDefault = {}
    py.cm = mods["matplotlib.cm"]
    mpl = mods["matplotlib"]
    mpl.pyplot = py
    mpl.rcParams = py.rcParams
    mpl.use = lambda *a, **k: None
    mpl.colors = mods["matplotlib.colors"]
    mpl.cm = mods["matplotlib.cm"]
    mpl.ticker = mods["matplotlib.ticker"]
    mpl.patches = mods["matplotlib.patches"]
    mpl.rc = lambda *a, **k: None
    for k, v in mods.items():
        sys.modules[k] = v


def _install_pydantic() -> None:
    mod = _bare_module("pydantic")

    class BaseModel:
        def __init__(self, **data):
            for k, v in data.items():
                setattr(self, k, v)

        def dict(self, *a, **k):
            return dict(self.__dict__)

        def model_dump(self, *a, **k):
            return dict(self.__dict__)

        @classmethod
        def model_validate(cls, obj, *a, **k):
            return obj

        class Config:
            arbitrary_types_allowed = True

    def Field(default=None, *a, **k):  # noqa: N802
        return default

    class validator:  # noqa: N801
        def __init__(self, *a, **k):
            pass

        def __call__(self, fn):
            return fn

    class root_validator:  # noqa: N801
        def __init__(self, *a, **k):
            pass

        def __call__(self, fn):
            return fn

    mod.BaseModel = BaseModel
    mod.Field = Field
    mod.validator = validator
    mod.root_validator = root_validator
    mod.field_validator = validator
    mod.model_validator = validator
    mod.HttpUrl = str
    mod.conint = lambda *a, **k: int
    mod.confloat = lambda *a, **k: float
    mod.constr = lambda *a, **k: str
    sys.modules["pydantic"] = mod


def _install_fastapi() -> None:
    for name in ("fastapi", "fastapi.responses", "fastapi.middleware",
                 "fastapi.middleware.cors", "starlette", "starlette.responses"):
        sys.modules[name] = _bare_module(name)

    fa = sys.modules["fastapi"]

    class APIRouter:
        def __init__(self, *a, **k):
            pass

        def _deco(self, *a, **k):
            def wrap(fn):
                return fn
            return wrap

        get = post = put = delete = patch = head = options = _deco

        def include_router(self, *a, **k):
            return None

        def add_api_route(self, *a, **k):
            return None

    class FastAPI(APIRouter):
        pass

    class HTTPException(Exception):
        def __init__(self, status_code: int = 500, detail: Any = None, *a, **k):
            super().__init__(detail)
            self.status_code = status_code
            self.detail = detail

    def _passthrough(default=None, *a, **k):
        return default

    fa.APIRouter = APIRouter
    fa.FastAPI = FastAPI
    fa.HTTPException = HTTPException
    for nm in ("Query", "Body", "Path", "Header", "Form", "File", "Depends", "Cookie",
               "Security"):
        setattr(fa, nm, _passthrough)
    fa.status = types.SimpleNamespace(HTTP_200_OK=200, HTTP_400_BAD_REQUEST=400,
                                      HTTP_404_NOT_FOUND=404, HTTP_500_INTERNAL_SERVER_ERROR=500)
    resp = sys.modules["fastapi.responses"]
    resp.JSONResponse = _Dummy
    resp.FileResponse = _Dummy
    resp.PlainTextResponse = _Dummy
    cors = sys.modules["fastapi.middleware.cors"]
    cors.CORSMiddleware = _Dummy


_STUBS = {
    "matplotlib": _install_matplotlib,
    "pydantic": _install_pydantic,
    "fastapi": _install_fastapi,
}


def install(names: List[str]) -> List[str]:
    """按名安装替身；返回实际安装的清单（写进工件，供审计链复核）。"""
    done = []
    for name in names:
        fn = _STUBS.get(name)
        if fn is None:
            continue
        fn()
        done.append(name)
    return done