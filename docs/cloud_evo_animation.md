# 云图演化动画·标准渲染器 (FEAT-SIM-DATAHOUSE)

## 交付
- `backend/tools/cloud_evo.py` —— 标准渲染器：sim-assets 事件（场快照序列 npz/npy/vtk）→ mp4 演化动画 + 海报 png + manifest。2D 标量/矢量场通吃，colorbar+时间戳+case 名。
- `backend/tools/cloud_evo_fieldgen.py` —— 场数据生成器（Kelvin 尾迹/偶极子势流，物理动机非随机）。
- `backend/services/cloud_evo_capture.py` —— 捕获守护钩子：新事件入库即自动渲染，失败降级不阻捕获。
- `backend/api/cloud_evo_gallery.py` —— gallery 页 API（可播）。
- `backend/middleware/auth_guard.py` —— 新增 cloud-evo gallery 免鉴权图床白名单（复用 papers 模式）。

## 验证
- 20 个事件（12 标量 + 8 矢量）全部渲染 OK，gallery 索引 20 条。
- 红队：损坏 npz → 优雅降级（empty_field），不崩。
- 空场/单帧 → 降级有日志。
- 本机无 ffmpeg → gif 降级；生产 Dockerfile 装 ffmpeg → mp4。
