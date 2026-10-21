"""Fixed reproducible chart renderer; only reads a bounded measurement JSON."""
import json
import sys
from pathlib import Path
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

def render(source,target):
    data=json.loads(Path(source).read_text())
    labels=['passed','failure','error','skipped','expected_failure','unexpected_success']
    counts=[data['counts'][k] for k in labels]
    if any(type(n) is not int or not 0<=n<=512 for n in counts):raise ValueError('invalid counts')
    fig,ax=plt.subplots(figsize=(9,4),layout='constrained')
    bars=ax.bar(labels,counts,color=['#287f59','#bc3939','#ce6e25','#858585','#607b9a','#a23978'])
    ax.bar_label(bars);ax.set_ylabel('Test methods');ax.set_title('Collected software tests (not business acceptance)')
    ax.tick_params(axis='x',labelsize=8);ax.set_ylim(0,max(counts+[1])*1.2)
    fig.savefig(target,dpi=140,metadata={'Software':'TensorPlatform fixed test chart v1'});plt.close(fig)

if __name__=='__main__':render(sys.argv[1],sys.argv[2])
