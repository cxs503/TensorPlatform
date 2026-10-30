"""Generate architecture and flow diagrams for m597 mission lifecycle."""
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch
import numpy as np
import os

OUTPUT_DIR = "/srv/tensor/agent/work/m597/artifacts/m597"
os.makedirs(OUTPUT_DIR, exist_ok=True)


def draw_architecture():
    """Figure 1: Architecture diagram of mission lifecycle system."""
    fig, ax = plt.subplots(1, 1, figsize=(14, 9))
    ax.set_xlim(0, 14)
    ax.set_ylim(0, 9)
    ax.axis('off')
    ax.set_title('Mission Lifecycle Architecture (m597)', fontsize=16, fontweight='bold', pad=20)

    # Color scheme
    colors = {
        'api': '#4CAF50',
        'db': '#2196F3',
        'worker': '#FF9800',
        'owner': '#9C27B0',
        'gate': '#F44336',
        'bg': '#F5F5F5',
    }

    # Owner box
    owner_box = FancyBboxPatch((0.5, 7), 2.5, 1.2, boxstyle="round,pad=0.1",
                                facecolor=colors['owner'], alpha=0.3, edgecolor=colors['owner'], linewidth=2)
    ax.add_patch(owner_box)
    ax.text(1.75, 7.6, 'Owner / Admin', ha='center', va='center', fontsize=12, fontweight='bold')

    # API Layer
    api_box = FancyBboxPatch((4, 5.5), 6, 2.5, boxstyle="round,pad=0.1",
                              facecolor=colors['api'], alpha=0.15, edgecolor=colors['api'], linewidth=2)
    ax.add_patch(api_box)
    ax.text(7, 7.7, 'API Layer (FastAPI)', ha='center', va='center', fontsize=13, fontweight='bold', color=colors['api'])

    # API endpoints
    endpoints = [
        ('POST /suspend', 4.5, 7),
        ('POST /resume', 4.5, 6.4),
        ('POST /write-off', 7, 7),
        ('GET /probe', 7, 6.4),
        ('GET /orphan-queue', 9.5, 7),
        ('POST /necessity-gate', 9.5, 6.4),
    ]
    for label, x, y in endpoints:
        ep_box = FancyBboxPatch((x-0.8, y-0.2), 1.6, 0.4, boxstyle="round,pad=0.05",
                                 facecolor='white', edgecolor=colors['api'], linewidth=1)
        ax.add_patch(ep_box)
        ax.text(x, y, label, ha='center', va='center', fontsize=8)

    # Necessity Gate
    gate_box = FancyBboxPatch((4, 3.5), 6, 1.5, boxstyle="round,pad=0.1",
                               facecolor=colors['gate'], alpha=0.15, edgecolor=colors['gate'], linewidth=2)
    ax.add_patch(gate_box)
    ax.text(7, 4.7, 'Necessity Gate (3-Check)', ha='center', va='center', fontsize=12, fontweight='bold', color=colors['gate'])
    checks = ['(a) Covered?', '(b) Mechanical?', '(c) In-flight?']
    for i, check in enumerate(checks):
        ax.text(5 + i*2, 4.0, check, ha='center', va='center', fontsize=9,
                bbox=dict(boxstyle='round', facecolor='white', edgecolor=colors['gate']))

    # Database Layer
    db_box = FancyBboxPatch((1, 0.5), 12, 2.5, boxstyle="round,pad=0.1",
                             facecolor=colors['db'], alpha=0.15, edgecolor=colors['db'], linewidth=2)
    ax.add_patch(db_box)
    ax.text(7, 2.7, 'Database Layer (PostgreSQL)', ha='center', va='center', fontsize=13, fontweight='bold', color=colors['db'])

    tables = [
        ('tensor_dev_missions\n(+ lifecycle cols)', 2.5, 1.5),
        ('credit_ledger', 5.5, 1.5),
        ('mission_lifecycle_log', 8.5, 1.5),
        ('necessity_gate_log\n+ mechanical_fix_log', 11.5, 1.5),
    ]
    for label, x, y in tables:
        t_box = FancyBboxPatch((x-1.2, y-0.5), 2.4, 1.0, boxstyle="round,pad=0.05",
                                facecolor='white', edgecolor=colors['db'], linewidth=1)
        ax.add_patch(t_box)
        ax.text(x, y, label, ha='center', va='center', fontsize=8)

    # Worker box
    worker_box = FancyBboxPatch((11, 7), 2.5, 1.2, boxstyle="round,pad=0.1",
                                 facecolor=colors['worker'], alpha=0.3, edgecolor=colors['worker'], linewidth=2)
    ax.add_patch(worker_box)
    ax.text(12.25, 7.6, 'Worker (tp_worker)', ha='center', va='center', fontsize=12, fontweight='bold')

    # Arrows
    arrow_style = dict(arrowstyle='->', color='gray', linewidth=1.5)
    # Owner → API
    ax.annotate('', xy=(4, 7), xytext=(3, 7.6), arrowprops=arrow_style)
    ax.text(3.3, 7.5, 'suspend/stop', fontsize=7, color='gray')
    # Worker → API
    ax.annotate('', xy=(10, 7), xytext=(11, 7.6), arrowprops=arrow_style)
    ax.text(10.2, 7.5, 'heartbeat/probe', fontsize=7, color='gray')
    # API → DB
    ax.annotate('', xy=(7, 3), xytext=(7, 5.5), arrowprops=arrow_style)
    # API → Gate
    ax.annotate('', xy=(7, 5), xytext=(7, 5.5), arrowprops=dict(arrowstyle='->', color=colors['gate'], linewidth=1.5))
    # Gate → DB
    ax.annotate('', xy=(7, 3), xytext=(7, 3.5), arrowprops=arrow_style)

    plt.tight_layout()
    plt.savefig(os.path.join(OUTPUT_DIR, 'fig1_architecture.png'), dpi=150, bbox_inches='tight')
    plt.close()
    print("Saved fig1_architecture.png")


def draw_lifecycle_flow():
    """Figure 2: Mission lifecycle state machine."""
    fig, ax = plt.subplots(1, 1, figsize=(14, 8))
    ax.set_xlim(0, 14)
    ax.set_ylim(0, 8)
    ax.axis('off')
    ax.set_title('Mission Lifecycle State Machine (m597)', fontsize=16, fontweight='bold', pad=20)

    # States
    states = {
        'queued': (2, 6, '#4CAF50'),
        'claimed': (5, 6, '#8BC34A'),
        'running': (8, 6, '#FFC107'),
        'suspended': (5, 4, '#FF5722'),
        'awaiting_acceptance': (11, 6, '#03A9F4'),
        'succeeded': (2, 1.5, '#4CAF50'),
        'failed': (5, 1.5, '#F44336'),
        'stopped': (8, 1.5, '#9E9E9E'),
        'revoked': (11, 1.5, '#795548'),
        'orphan': (13, 4, '#607D8B'),
    }

    for name, (x, y, color) in states.items():
        circle = plt.Circle((x, y), 0.6, facecolor=color, alpha=0.3, edgecolor=color, linewidth=2)
        ax.add_patch(circle)
        ax.text(x, y, name, ha='center', va='center', fontsize=8, fontweight='bold')

    # Transitions
    transitions = [
        ('queued', 'claimed', 'claim'),
        ('claimed', 'running', 'start'),
        ('running', 'suspended', 'suspend\n(budget freeze)'),
        ('suspended', 'queued', 'resume\n(budget continue)'),
        ('running', 'awaiting_acceptance', 'complete\n(request acceptance)'),
        ('running', 'succeeded', 'complete\n(no acceptance)'),
        ('running', 'failed', 'complete\n/ heartbeat timeout'),
        ('queued', 'orphan', '>48h no claim'),
        ('running', 'failed', 'heartbeat\n>30min stale'),
        ('queued', 'stopped', 'stop'),
        ('running', 'stopped', 'stop'),
        ('awaiting_acceptance', 'succeeded', 'accept'),
        ('awaiting_acceptance', 'failed', 'reject'),
    ]

    for src, dst, label in transitions:
        sx, sy, _ = states[src]
        dx, dy, _ = states[dst]
        # Calculate arrow endpoints (from edge of circles)
        angle = np.arctan2(dy - sy, dx - sx)
        x1 = sx + 0.6 * np.cos(angle)
        y1 = sy + 0.6 * np.sin(angle)
        x2 = dx - 0.6 * np.cos(angle)
        y2 = dy - 0.6 * np.sin(angle)

        ax.annotate('', xy=(x2, y2), xytext=(x1, y1),
                    arrowprops=dict(arrowstyle='->', color='#555', linewidth=1.2))
        # Label at midpoint
        mx, my = (x1 + x2) / 2, (y1 + y2) / 2
        ax.text(mx, my + 0.15, label, ha='center', va='center', fontsize=6,
                bbox=dict(boxstyle='round,pad=0.1', facecolor='white', edgecolor='none', alpha=0.8))

    # Terminal states box
    terminal_box = FancyBboxPatch((0.5, 0.3), 13, 0.8, boxstyle="round,pad=0.1",
                                   facecolor='#FFCDD2', alpha=0.3, edgecolor='#F44336', linewidth=1, linestyle='--')
    ax.add_patch(terminal_box)
    ax.text(7, 0.7, 'Terminal States → Write-off Hook (credit_ledger + worktree recyclable)',
            ha='center', va='center', fontsize=10, fontweight='bold', color='#F44336')

    plt.tight_layout()
    plt.savefig(os.path.join(OUTPUT_DIR, 'fig2_lifecycle_flow.png'), dpi=150, bbox_inches='tight')
    plt.close()
    print("Saved fig2_lifecycle_flow.png")


def draw_budget_continuity():
    """Figure 3: Budget continuity through suspend/resume cycle."""
    fig, ax = plt.subplots(1, 1, figsize=(12, 6))

    # Simulate budget usage over time
    time = list(range(0, 100))
    tool_calls = []
    cumulative = 0
    for t in time:
        if t < 30:
            cumulative += 1  # Normal usage
        elif t < 60:
            pass  # Suspended - no usage
        else:
            cumulative += 1  # Resumed - continues from 30
        tool_calls.append(cumulative)

    # What would happen WITHOUT freeze (bug behavior)
    no_freeze = []
    cumulative2 = 0
    for t in time:
        if t < 30:
            cumulative2 += 1
        elif t < 60:
            pass  # Suspended
        else:
            cumulative2 += 1  # Would restart from 0 (bug)
        no_freeze.append(cumulative2)

    # Actually the bug would be resetting to 0 on resume
    no_freeze_bug = []
    cumulative3 = 0
    for t in time:
        if t < 30:
            cumulative3 += 1
        elif t < 60:
            pass
        else:
            # Bug: reset to 0 and recount
            cumulative3 = t - 60 + 1
        no_freeze_bug.append(cumulative3)

    ax.plot(time[:30], tool_calls[:30], 'b-', linewidth=2, label='Running (accumulating)')
    ax.plot(time[30:60], tool_calls[30:60], 'r-', linewidth=2, label='Suspended (frozen)')
    ax.plot(time[60:], tool_calls[60:], 'g-', linewidth=2, label='Resumed (continues from 30)')

    # Show the bug behavior
    ax.plot(time[60:], no_freeze_bug[60:], 'm--', linewidth=1.5, alpha=0.5, label='BUG: Reset to 0 (old behavior)')

    # Annotations
    ax.axvline(x=30, color='red', linestyle=':', alpha=0.5)
    ax.axvline(x=60, color='green', linestyle=':', alpha=0.5)
    ax.text(31, 35, 'SUSPEND\nbudget_frozen = {tc:30}', fontsize=9, color='red', fontweight='bold')
    ax.text(61, 35, 'RESUME\nbudget_used continues\nfrom frozen snapshot', fontsize=9, color='green', fontweight='bold')

    # Budget snapshot box
    snapshot_box = FancyBboxPatch((35, 5), 20, 10, boxstyle="round,pad=0.3",
                                   facecolor='#FFF3E0', edgecolor='#FF9800', linewidth=1.5)
    ax.add_patch(snapshot_box)
    ax.text(45, 13, 'budget_frozen snapshot:', fontsize=10, fontweight='bold', ha='center')
    ax.text(45, 11, 'tool_calls_used: 30', fontsize=9, ha='center', family='monospace')
    ax.text(45, 9.5, 'wall_seconds_used: 1800', fontsize=9, ha='center', family='monospace')
    ax.text(45, 8, 'tokens_used: 150000', fontsize=9, ha='center', family='monospace')
    ax.text(45, 6.5, '→ Resume restores these values', fontsize=8, ha='center', color='green', style='italic')

    ax.set_xlabel('Time (arbitrary units)', fontsize=12)
    ax.set_ylabel('Cumulative Tool Calls', fontsize=12)
    ax.set_title('Budget Continuity: Suspend Freezes, Resume Continues (m597)', fontsize=14, fontweight='bold')
    ax.legend(loc='upper left', fontsize=9)
    ax.set_ylim(0, 75)
    ax.grid(True, alpha=0.3)

    plt.tight_layout()
    plt.savefig(os.path.join(OUTPUT_DIR, 'fig3_budget_continuity.png'), dpi=150, bbox_inches='tight')
    plt.close()
    print("Saved fig3_budget_continuity.png")


def draw_necessity_gate():
    """Figure 4: Necessity gate decision flow."""
    fig, ax = plt.subplots(1, 1, figsize=(12, 8))
    ax.set_xlim(0, 12)
    ax.set_ylim(0, 8)
    ax.axis('off')
    ax.set_title('Necessity Gate: Pre-Dispatch 3-Check (m597)', fontsize=14, fontweight='bold', pad=20)

    # Start
    start = FancyBboxPatch((4.5, 7), 3, 0.6, boxstyle="round,pad=0.1",
                            facecolor='#4CAF50', alpha=0.3, edgecolor='#4CAF50', linewidth=2)
    ax.add_patch(start)
    ax.text(6, 7.3, 'New Mission Request', ha='center', va='center', fontsize=10, fontweight='bold')

    # Check A
    check_a = FancyBboxPatch((4, 5.5), 4, 0.8, boxstyle="round,pad=0.1",
                              facecolor='#FFC107', alpha=0.3, edgecolor='#FFC107', linewidth=2)
    ax.add_patch(check_a)
    ax.text(6, 5.9, '(a) Already Covered?\nsame task_key succeeded?', ha='center', va='center', fontsize=9)

    # Check B
    check_b = FancyBboxPatch((4, 4), 4, 0.8, boxstyle="round,pad=0.1",
                              facecolor='#FF9800', alpha=0.3, edgecolor='#FF9800', linewidth=2)
    ax.add_patch(check_b)
    ax.text(6, 4.4, '(b) Mechanically Fixable?\nexact duplicate / typo?', ha='center', va='center', fontsize=9)

    # Check C
    check_c = FancyBboxPatch((4, 2.5), 4, 0.8, boxstyle="round,pad=0.1",
                              facecolor='#F44336', alpha=0.3, edgecolor='#F44336', linewidth=2)
    ax.add_patch(check_c)
    ax.text(6, 2.9, '(c) Same Goal In-Flight?\n>70% keyword overlap?', ha='center', va='center', fontsize=9)

    # Outcomes
    pass_box = FancyBboxPatch((9, 1), 2.5, 0.8, boxstyle="round,pad=0.1",
                               facecolor='#4CAF50', alpha=0.3, edgecolor='#4CAF50', linewidth=2)
    ax.add_patch(pass_box)
    ax.text(10.25, 1.4, 'PASS\n→ Dispatch', ha='center', va='center', fontsize=10, fontweight='bold')

    reject_a = FancyBboxPatch((0.2, 5.5), 2.5, 0.8, boxstyle="round,pad=0.1",
                               facecolor='#F44336', alpha=0.3, edgecolor='#F44336', linewidth=2)
    ax.add_patch(reject_a)
    ax.text(1.45, 5.9, 'REJECT\ncovered', ha='center', va='center', fontsize=9, fontweight='bold')

    reject_b = FancyBboxPatch((0.2, 4), 2.5, 0.8, boxstyle="round,pad=0.1",
                               facecolor='#FF5722', alpha=0.3, edgecolor='#FF5722', linewidth=2)
    ax.add_patch(reject_b)
    ax.text(1.45, 4.4, 'DOWNGRADE\nmechanical fix', ha='center', va='center', fontsize=9, fontweight='bold')

    reject_c = FancyBboxPatch((0.2, 2.5), 2.5, 0.8, boxstyle="round,pad=0.1",
                               facecolor='#FF9800', alpha=0.3, edgecolor='#FF9800', linewidth=2)
    ax.add_patch(reject_c)
    ax.text(1.45, 2.9, 'REJECT\nin-flight dup', ha='center', va='center', fontsize=9, fontweight='bold')

    # Arrows
    arrow_kw = dict(arrowstyle='->', color='#555', linewidth=1.5)
    ax.annotate('', xy=(6, 6.3), xytext=(6, 7), arrowprops=arrow_kw)
    ax.annotate('', xy=(6, 4.8), xytext=(6, 5.5), arrowprops=arrow_kw)
    ax.annotate('', xy=(6, 3.3), xytext=(6, 4), arrowprops=arrow_kw)
    # Reject arrows
    ax.annotate('', xy=(2.7, 5.9), xytext=(4, 5.9), arrowprops=dict(arrowstyle='->', color='#F44336', linewidth=1.5))
    ax.text(3.3, 6.1, 'YES', fontsize=8, color='#F44336', fontweight='bold')
    ax.annotate('', xy=(2.7, 4.4), xytext=(4, 4.4), arrowprops=dict(arrowstyle='->', color='#FF5722', linewidth=1.5))
    ax.text(3.3, 4.6, 'YES', fontsize=8, color='#FF5722', fontweight='bold')
    ax.annotate('', xy=(2.7, 2.9), xytext=(4, 2.9), arrowprops=dict(arrowstyle='->', color='#FF9800', linewidth=1.5))
    ax.text(3.3, 3.1, 'YES', fontsize=8, color='#FF9800', fontweight='bold')
    # Pass arrow
    ax.annotate('', xy=(9, 1.4), xytext=(8, 2.5), arrowprops=dict(arrowstyle='->', color='#4CAF50', linewidth=1.5))
    ax.text(8.5, 2, 'NO\n(all pass)', fontsize=8, color='#4CAF50', fontweight='bold')

    # Audit log note
    audit_box = FancyBboxPatch((3, 0.2), 6, 0.5, boxstyle="round,pad=0.1",
                                facecolor='#E3F2FD', edgecolor='#2196F3', linewidth=1)
    ax.add_patch(audit_box)
    ax.text(6, 0.45, 'All decisions logged to necessity_gate_log (auditable)', ha='center', va='center', fontsize=9, color='#2196F3')

    plt.tight_layout()
    plt.savefig(os.path.join(OUTPUT_DIR, 'fig4_necessity_gate.png'), dpi=150, bbox_inches='tight')
    plt.close()
    print("Saved fig4_necessity_gate.png")


if __name__ == '__main__':
    draw_architecture()
    draw_lifecycle_flow()
    draw_budget_continuity()
    draw_necessity_gate()
    print(f"\nAll figures saved to {OUTPUT_DIR}/")
