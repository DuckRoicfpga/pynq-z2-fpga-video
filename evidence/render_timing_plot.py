"""Optional: render recorded data, not a new hardware measurement.

Requires matplotlib separately from the host-test dependencies.
"""
import json
from pathlib import Path
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parents[1]
data = json.loads((ROOT/'evidence/board_validation.json').read_text(encoding='utf-8'))
metrics = data['final_stats_snapshot']['metrics']
labels = ['HDMI capture + copy', 'ARM resize', 'FPGA driver + transfers', 'ARM JPEG']
values = [metrics[key] for key in ('capture_ms','resize_ms','fpga_driver_ms','jpeg_ms')]
colors = ['#2563eb','#0891b2','#7c3aed','#ea580c']
fig, ax = plt.subplots(figsize=(10.5,4.6), facecolor='white')
ax.set_facecolor('#f8fafc')
bars=ax.barh(labels,values,color=colors,height=.58)
ax.invert_yaxis()
for bar,value in zip(bars,values):
    ax.text(value+3,bar.get_y()+bar.get_height()/2,f'{value:.2f} ms',va='center',fontsize=11,color='#0f172a')
ax.set_xlim(0,max(values)*1.28)
ax.set_xlabel('Milliseconds per completed frame',fontsize=11)
ax.spines[['top','right','left']].set_visible(False)
ax.grid(axis='x',alpha=.17)
ax.set_axisbelow(True)
fig.suptitle('PYNQ-Z2: one recorded Sobel pipeline sample',fontsize=17,fontweight='bold',x=.08,ha='left',y=.98)
fig.text(.08,.89,'640 × 480 processing  |  1280 × 720 @ 60 Hz source',fontsize=11,color='#475569')
fig.text(.08,.075,f'Total: {metrics["pipeline_ms"]:.2f} ms. Core counter: {metrics["core_active_ms"]:.2f} ms (included in driver bar).',fontsize=10,color='#334155')
fig.text(.08,.025,'Source: evidence/board_validation.json. Single sample; not a percentile, average or end-to-end latency.',fontsize=9,color='#64748b')
fig.subplots_adjust(left=.25,right=.96,top=.81,bottom=.23)
output=ROOT/'docs/images'
fig.savefig(output/'pipeline-timing.svg',facecolor='white')
fig.savefig(output/'pipeline-timing.png',dpi=150,facecolor='white')
plt.close(fig)
print('PLOT_RENDERED_FROM_RECORDED_DATA')
