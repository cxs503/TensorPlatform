# verifier_assets — 验证资产（版本化、校验和锁定）

本目录存放**验证测试所依赖的、不可再生的证据**：参考实现（MMG、SOLAS 破舱稳性）、
平台黑盒探针存档（水曲线、推进 open-water fixture）、判决工件与图。

## 为什么放在这里，而不是 `artifacts/`

合并门禁会滤除 `artifacts/**`（各 mission 分支合并记录里可见「N 个文件被门禁滤除」）。
历史上本目录对应文件放在 `artifacts/verifier/`、`artifacts/verif_maneuvering/`、
`artifacts/verif/damage_stability/`、`artifacts/m1584/`，合并后文件消失，而**消费它们的
验证测试保留了下来**，于是 12 项验证测试由「通过」退化为 `FileNotFoundError` /
`ModuleNotFoundError`（2026-11-01 实测，见 docs/verification/verifier_asset_restoration_m1802.md）。

`tests/` 不受门禁滤除，故把资产迁到此处；消费方测试仍保留 `artifacts/` 作为回退路径，
因此已经手工恢复过 artifacts 的环境不会被破坏。

## 纪律

1. 资产只增不删；删除等同关闭对应验证门。
2. 新增/更新资产必须同步 `manifest.json` 的 `sha256`（`tests/test_verifier_assets_integrity.py`
   会在校验和不符时失败，防止资产被无声替换）。
3. 重采集：
   - 水曲线：`python3 tools/verifier/geometry/audit_hydro_curves.py --live`（默认写回本目录）
   - 推进 open-water：见 `tools/verifier/propulsion_openwater_gate.py`，探针结果落 `propulsion/`
4. 采集时间/环境须写进资产自身的 `provenance` 字段（若格式允许），保证可追溯。