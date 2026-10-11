<template>
  <div class="lineage-graph-container">
    <div class="lineage-toolbar">
      <el-button size="small" @click="loadLineage" :loading="loading">刷新数据</el-button>
      <el-button size="small" @click="fitGraph">适配视图</el-button>
      <span class="lineage-stats" v-if="nodes.length">
        节点: {{ nodes.length }} | 边: {{ edges.length }}
      </span>
    </div>
    <div ref="graphEl" class="lineage-graph"></div>
    <el-empty v-if="!nodes.length && !loading" description="暂无血缘数据" />
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted, onUnmounted, nextTick } from 'vue'
import http from '@/api/request'

const graphEl = ref<HTMLElement | null>(null)
const nodes = ref<any[]>([])
const edges = ref<any[]>([])
const loading = ref(false)
let svg: any = null
let simulation: any = null

async function loadLineage() {
  loading.value = true
  try {
    const res: any = await http.get('/datahub/lineage/full')
    nodes.value = res.nodes || []
    edges.value = res.edges || []
    await nextTick()
    drawGraph()
  } catch (e) { console.error(e) }
  finally { loading.value = false }
}

function drawGraph() {
  if (!graphEl.value || !nodes.value.length) return
  const el = graphEl.value
  const W = el.clientWidth || 800
  const H = el.clientHeight || 500
  el.innerHTML = ''

  // Simple SVG-based D3-like force layout
  const xmlns = 'http://www.w3.org/2000/svg'
  svg = document.createElementNS(xmlns, 'svg')
  svg.setAttribute('width', '100%')
  svg.setAttribute('height', '100%')
  svg.setAttribute('viewBox', `0 0 ${W} ${H}`)
  el.appendChild(svg)

  // Defs for arrow
  const defs = document.createElementNS(xmlns, 'defs')
  defs.innerHTML = `<marker id="arrow" viewBox="0 0 10 10" refX="20" refY="5" markerWidth="6" markerHeight="6" orient="auto"><path d="M 0 0 L 10 5 L 0 10 z" fill="#999"/></marker>`
  svg.appendChild(defs)

  // Colors by type
  const colors: Record<string, string> = { dataset: '#67C23A', model: '#409EFF', training_job: '#E6A23C' }

  // Simple layout
  const pos: Record<string, { x: number; y: number }> = {}
  const n = nodes.value.length
  nodes.value.forEach((nd, i) => {
    const angle = (2 * Math.PI * i) / n
    const r = Math.min(W, H) * 0.35
    pos[nd.id] = { x: W / 2 + r * Math.cos(angle), y: H / 2 + r * Math.sin(angle) }
  })

  // Draw edges
  for (const e of edges.value) {
    const line = document.createElementNS(xmlns, 'line')
    const src = pos[e.source], tgt = pos[e.target]
    if (!src || !tgt) continue
    line.setAttribute('x1', src.x); line.setAttribute('y1', src.y)
    line.setAttribute('x2', tgt.x); line.setAttribute('y2', tgt.y)
    line.setAttribute('stroke', '#ccc'); line.setAttribute('stroke-width', '1.5')
    line.setAttribute('marker-end', 'url(#arrow)')
    svg.appendChild(line)
  }

  // Draw nodes
  for (const nd of nodes.value) {
    const p = pos[nd.id]; if (!p) continue
    const g = document.createElementNS(xmlns, 'g')
    const circle = document.createElementNS(xmlns, 'circle')
    circle.setAttribute('cx', p.x); circle.setAttribute('cy', p.y)
    circle.setAttribute('r', '20')
    circle.setAttribute('fill', colors[nd.type] || '#909399')
    circle.setAttribute('stroke', '#fff'); circle.setAttribute('stroke-width', '2')
    g.appendChild(circle)
    const text = document.createElementNS(xmlns, 'text')
    text.setAttribute('x', p.x); text.setAttribute('y', p.y + 5)
    text.setAttribute('text-anchor', 'middle'); text.setAttribute('fill', '#fff')
    text.setAttribute('font-size', '10'); text.textContent = (nd.label || '').slice(0, 6)
    g.appendChild(text)
    // Label below
    const label = document.createElementNS(xmlns, 'text')
    label.setAttribute('x', p.x); label.setAttribute('y', p.y + 35)
    label.setAttribute('text-anchor', 'middle'); label.setAttribute('fill', '#606266')
    label.setAttribute('font-size', '10'); label.textContent = (nd.label || '').slice(0, 10)
    g.appendChild(label)
    svg.appendChild(g)
  }
}

function fitGraph() { drawGraph() }

onMounted(() => { loadLineage() })
onUnmounted(() => { nodes.value = []; edges.value = [] })
</script>

<style scoped>
.lineage-graph-container { height: 500px; position: relative; }
.lineage-toolbar { display: flex; gap: 8px; align-items: center; margin-bottom: 10px; }
.lineage-stats { color: #909399; font-size: 13px; margin-left: auto; }
.lineage-graph { width: 100%; height: 450px; border: 1px solid #ebeef5; border-radius: 8px; background: #fafbfc; }
</style>
