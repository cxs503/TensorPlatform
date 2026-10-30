"""m371 shift figures: all numbers computed from agent_work/m371/ledger.json
(which is itself derived from GET /tensor/missions?limit=50)."""
import json, re, os
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np

BASE = os.path.dirname(os.path.abspath(__file__))
led = json.load(open(os.path.join(BASE, 'ledger.json')))
OUT = os.path.join(BASE, 'fig')
os.makedirs(OUT, exist_ok=True)

# ---------- fig1: mission-level verdict composition ----------
kinds = ['diag', 'probe']
labels = {'PASS': 'PASS', 'HONEST_FAIL': 'HONEST_FAIL', 'GATE_FAIL': 'GATE_FAIL(infra)', 'RUNNING': 'RUNNING'}
vorder = ['PASS', 'HONEST_FAIL', 'GATE_FAIL', 'RUNNING']
colors = {'PASS': '#2e7d32', 'HONEST_FAIL': '#ef6c00', 'GATE_FAIL': '#c62828', 'RUNNING': '#546e7a'}
counts = {k: {v: 0 for v in vorder} for k in kinds}
for m in led['missions']:
    if m['kind'] in counts:
        counts[m['kind']][m['verdict']] += 1

fig, ax = plt.subplots(figsize=(7.2, 3.6))
bottom = np.zeros(len(kinds))
for v in vorder:
    vals = np.array([counts[k][v] for k in kinds], dtype=float)
    ax.bar([k.upper() for k in kinds], vals, bottom=bottom, label=labels[v], color=colors[v])
    for i, val in enumerate(vals):
        if val > 0:
            ax.text(i, bottom[i] + val / 2, int(val), ha='center', va='center',
                    color='white', fontsize=9, fontweight='bold')
    bottom += vals
ax.set_ylabel('missions')
ax.set_title('m371 sweep-line missions by verdict (n=%d case missions)' % led['case_missions'], fontsize=10)
ax.legend(fontsize=8, frameon=False)
ax.spines[['top', 'right']].set_visible(False)
for i, k in enumerate(kinds):
    ax.text(i, bottom[i] + 0.3, 'total %d' % int(bottom[i]), ha='center', fontsize=8)
fig.tight_layout()
fig.savefig(os.path.join(OUT, 'fig1_verdicts.png'), dpi=170)
plt.close(fig)

# ---------- fig2: per-case attempts + latest verdict ----------
pc = sorted(led['per_case'], key=lambda x: x['case'])
names = [p['case'] for p in pc]
att = [p['attempts'] for p in pc]
vcol = [colors.get(p['latest_verdict'], '#546e7a') for p in pc]
fig, ax = plt.subplots(figsize=(8.4, 4.4))
ax.barh(range(len(pc)), att, color=vcol)
ax.set_yticks(range(len(pc)))
ax.set_yticklabels(['%s [%s]' % (p['case'], p['latest_verdict']) for p in pc], fontsize=7.5)
ax.set_xlabel('sweep attempts in window (m%d..m%d)' % (min(m['id'] for m in led['missions']),
                                                       max(m['id'] for m in led['missions'])))
ax.set_title('m371 per-case attempts and latest verdict (n=%d cases)' % len(pc), fontsize=10)
for i, a in enumerate(att):
    ax.text(a + 0.05, i, str(a), va='center', fontsize=7.5)
handles = [plt.Rectangle((0, 0), 1, 1, color=colors[v]) for v in ['PASS', 'HONEST_FAIL', 'GATE_FAIL']]
ax.legend(handles, ['PASS', 'HONEST_FAIL', 'GATE_FAIL(infra)'], fontsize=8, frameon=False, loc='lower right')
ax.spines[['top', 'right']].set_visible(False)
ax.set_xlim(0, max(att) + 1)
fig.tight_layout()
fig.savefig(os.path.join(OUT, 'fig2_case_attempts.png'), dpi=170)
plt.close(fig)

# ---------- fig3: grid ladders quoted in API mission output vs 3% gate ----------
def pcts(mid, exclude=(3.0,)):
    m = next(x for x in led['missions'] if x['id'] == mid)
    vals = [float(v) for v in re.findall(r'(\d+\.\d+)%', m['snippet'])]
    out = []
    for v in vals:
        if v in exclude:
            continue
        if out and any(abs(v - o) < 1e-12 for o in out):   # keep first mention of each value
            continue
        out.append(v)
    return out

wo = pcts(346)          # womersley: 4.890 / 2.934 (H=29 alpha=8 / alpha=4)
ca = pcts(351)          # cavity_re100 convergence series
fig, axes = plt.subplots(1, 2, figsize=(8.6, 3.6))
ax = axes[0]
ax.plot(range(1, len(wo) + 1), wo, 'o-', color='#ef6c00', label='womersley (quoted from m346)')
ax.axhline(3.0, ls='--', color='#c62828', label='3% acceptance gate')
for i, v in enumerate(wo, 1):
    ax.annotate('%.3f%%' % v, (i, v), textcoords='offset points', xytext=(4, 5), fontsize=8)
ax.set_xlabel('configuration index (coarse -> finer)')
ax.set_ylabel('relative error [%]')
ax.set_title('womersley: HONEST_FAIL at coarse config', fontsize=9)
ax.legend(fontsize=7.5, frameon=False)
ax.spines[['top', 'right']].set_visible(False)
ax = axes[1]
ax.plot(range(1, len(ca) + 1), ca, 's-', color='#2e7d32', label='cavity_re100 ladder (quoted from m351)')
ax.axhline(3.0, ls='--', color='#c62828', label='3% acceptance gate')
for i, v in enumerate(ca, 1):
    ax.annotate('%.3f%%' % v, (i, v), textcoords='offset points', xytext=(4, 5), fontsize=8)
ax.set_xlabel('configuration index (nx=32 -> 48 -> baseline -> 192)')
ax.set_ylabel('max |u| deviation [%]')
ax.set_title('cavity: monotone decrease below gate', fontsize=9)
ax.legend(fontsize=7.5, frameon=False)
ax.spines[['top', 'right']].set_visible(False)
fig.suptitle('m371 sweep line: quoted error ladders vs 3% gate (numbers regex-extracted from API)', fontsize=9.5)
fig.tight_layout()
fig.savefig(os.path.join(OUT, 'fig3_ladders.png'), dpi=170)
plt.close(fig)

print('figs:', sorted(os.listdir(OUT)))
print('womersley ladder extracted:', wo)
print('cavity ladder extracted:', ca)
print('verdict counts:', counts)