# m9028 / p234w2-n02 — 尺度效应与外推相似律成熟化

| 文件 | 作用 |
|---|---|
| `run_validation.py` | 验证驱动：构造参考真值（Schoenherr 摩擦 + Michell 兴波 + C_A）、合成水池数据、四条外推通路、5 张图 |
| `results.json` | 唯一数字源（报告全部数字由它格式化注入，零手抄） |
| `REPORT.md` | 论文级报告（六要素），由 `post_report_m9028.py` 生成 |
| `post_report_m9028.py` | 报告生成 + 平台 `POST /tensor/reports` 投递（内嵌 base64 图） |
| `figures/*.png` | 摩擦线族/三尺度外推/通路误差/螺旋桨尺度修正 |

内核：`backend/api/domain/scale_effect.py`；测试：`tests/test_scale_effect_m9028.py`（22 用例）。

复现：

```bash
python3 docs/m9028_scale_law/run_validation.py      # ~2 min（Michell θ 求积为主）
python3 -m pytest tests/test_scale_effect_m9028.py -q
```
