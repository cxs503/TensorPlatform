"""班次 m242 派单（硬上限 3 单，仅 TensorLBM 扫荡类）。

goal 中的证据数字全部取自平台 API（docs/_sdaa_sweep_m242.json 由
scripts/ops/sdaa_sweep_audit.py 程序化生成，零手抄）。
"""
import json
import os
import urllib.request

BASE = os.environ.get('TENSOR_API_BASE', 'http://127.0.0.1:8010/api/v1')
USER = os.environ.get('TENSOR_AGENT_USER', 'eco_admin')
PWD = os.environ.get('TENSOR_AGENT_PASS', '')


def api(path, body=None, token=None):
    url = f"{BASE}/{path.lstrip('/')}"
    data = json.dumps(body).encode() if body is not None else None
    headers = {'Content-Type': 'application/json'}
    if token:
        headers['Authorization'] = f'Bearer {token}'
    req = urllib.request.Request(url, headers=headers, data=data, method='POST' if data else 'GET')
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode())


def main():
    d = json.load(open('docs/_sdaa_sweep_m242.json'))
    cases = d['cases']

    def ev(case, mid):
        rec = [r for r in d['missions'] if r['id'] == mid][0]
        return rec['verdict'], (rec['verdict_evidence'] or '')[:160].replace('\n', ' '), rec['text'][:700]

    payloads = []
    for case, mid, focus in [
        ('sphere_re100_mem', 202,
         '判据真红：m202 诊断结论为 honest-FAIL（3 轮累计仍不能稳定进入 3% 判据），D=40 Cd=1.12198 err=2.77%、D=60 Cd=1.11946 err=2.54%、收敛阶 p≈0.21，D=80 预测 1.43%、D=160 预测 0.36%。诊断方向：区分 (a) 经验阻力关联基准本身的偏差、(b) 每档仅 12k 步的统计噪声（窗口漂移）、(c) D3Q19 壁面处理带来的 O(1/D) 一阶误差。要求：冻结原判据（Cd 相对误差 ≤3% + 单调收敛，不得改口径），补 D=80/160 两档并给出统计误差棒与窗口收敛表。'),
        ('suboff_sail_resistance', 109,
         '环境类 FAIL：m109 判定 HONEST-FAIL（SDAA 内存分配器瓶颈）。证据：L∈{48,56,64,72,80,90,100,110} setup 全部 OK（峰值 0.38–4.47 GiB），但 L=64/90/100/110 在首个 collide step 全部 OOM（卡 14.94 GiB），根因指向 SDAA allocator reservation 策略而非案例内存需求。诊断方向：对标定过的 L=48 做显存剖面（逐 buffer 生命周期）→ 尝试流式/分块 collide、fp32 checkpoint 复用、避免一次性分配 f/f_post 双缓冲；目标至少稳定跑通 L∈{90,100} 并给出 Cd 对 L 的收敛表。若仍 OOM，如实记录剖面证据并给出可跑通的 L 档与误差，不改判据。'),
        ('thermal_cavity', 68,
         '判据真红（步数不足）：m68 结论为 0/6 判据 PASS，诚实报告 FAIL，原因写明「步数不足未达稳态」。诊断方向：对 Ra=1e5/Ra=1e6（按案例 README 冻结档位）延长步数至稳态，加入窗口漂移判据（末窗 Nu 漂移阈值）与 de Vahl Davis 1983 参考值逐项对比；给出 Nu、u_max、v_max 的步数收敛阶梯表，确认是否为壁面/热边界实现问题。判据口径不得修改。'),
    ]:
        v, snippet, text = ev(case, mid)
        payloads.append({
            'goal': (f"SDAA 验证案例扫荡——案例 {case}（TensorLBM benchmarks/verified/{case}）"
                     f"【班次 m242 派发·诊断+修复绕行】\n\n"
                     f"平台 API 记录（零手抄）：案例 {case} 最新完工单 m{mid}，判据={v}，证据片段「{snippet}」。\n\n"
                     f"失败证据原文（摘自 m{mid} result）：\n{text}\n\n"
                     f"{focus}\n\n"
                     f"硬约束：只改 TensorLBM 工作树内文件；不改判据口径、不隐藏 FAIL；"
                     f"产物必须落在 artifacts/ 或 benchmarks/verified/{case}/ 下并含设备证据行"
                     f"（torch.sdaa.get_device_name(0) 实值）；发布论文级报告（六要素，含误差判定逐项回答是否<3%）；"
                     f"结束时必须调用 finish。"),
            'repo': 'TensorLBM',
            'base_ref': 'main',
            'runner': 'native',
        })

    tok = api('auth/login', {'username': USER, 'password': PWD})
    tok = tok.get('access_token')
    out = []
    for p in payloads:
        try:
            r = api('tensor/missions', p, token=tok)
            out.append({'id': r.get('id'), 'status': r.get('status'), 'repo': r.get('repo')})
        except Exception as e:  # noqa
            out.append({'error': str(e)})
    print(json.dumps(out, ensure_ascii=False, indent=1))


if __name__ == '__main__':
    main()