# verification_harness

域无关验证脚手架：案例以 JSON 配置注册，执行后自动比对权威值并归档
sha256 + 环境指纹 + 依赖集的证据包。机制层（`model/registry/runner/evidence`）
零领域语义；合成锚点算例在 `anchors/`，配置在 `cases/`。

快速开始：

```bash
python3 -m tools.verification_harness run --cases tools/verification_harness/cases \
    --out /tmp/vh_bundle
python3 -m tools.verification_harness verify --bundle /tmp/vh_bundle
python3 -m tools.verification_harness.study --out artifacts/verification_harness
python3 generate_verification_harness_figures.py
```

设计与判据见 `docs/VERIFICATION_HARNESS.md`。
