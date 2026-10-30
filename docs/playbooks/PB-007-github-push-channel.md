# PB-007 99→GitHub 推送通道与令牌坑

**通道**：99 容器 → github.com:443 `git push` 直连超时；api.github.com 可达。
正解 = **Git Data API**：POST /git/blobs → POST /git/trees(base_tree) → POST /git/commits → POST/PATCH /git/refs。
只推 agent/* 分支，main 永不碰（合并归 owner）。

**令牌三坑（全部实测咬过）**：
1. 凭据文件解析正则必须锚定 `//user:token@` 结构——`:(^[@]+)@` 会匹配 https:// 的 scheme 冒号，
   捕获 102 字符垃圾 → 401（排障法：双侧 md5 对账锁定文件无辜、正则有罪）；
2. `tr -d "\n\r"` 在 sh 双引号里=删除字母 n 和 r，会咬令牌；
3. heredoc/嵌套引号会把 f-string 表达式弄坏——脚本一律 stdin 管道部署（docker exec -i python3 - < file）。

**CI 查询**：/actions/runs?branch=agent/m<N>&per_page=10 按 head_sha 前缀匹配；pending 下轮再查。
