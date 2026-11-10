<template>
  <div class="tf-page">
    <HeroPanel>
      <template #title>知识专栏 <span class="tf-hero-mint">· knowledge assets</span></template>
      <template #sub>
        TensorKnowledge 知识对象的总览面：四态信任漏斗（生产 → 证据 → 评审 → 发布）、领域分布、活跃命题榜；
        知识地图可下钻到单条 claim 的证据链，点开源文件直读<em>文件:行号@commit</em>。
        所有数字经 KE API 读取，未发布对象只显示状态标记，不显示正文。
      </template>
      <template #extra>
        <span>引擎 {{ overview.engine?.base || '—' }}</span>
        <span v-if="overview.generated_at">取数 {{ overview.generated_at }}</span>
        <span v-if="chain.tk_commit">知识库 @ {{ chain.tk_commit }}</span>
        <el-button link size="small" @click="refresh">刷新</el-button>
      </template>
    </HeroPanel>

    <el-tabs v-model="tab" class="kc-tabs" @tab-change="onTab">
      <!-- ───────────────────────── tab1 总览 ───────────────────────── -->
      <el-tab-pane label="总览" name="overview">
        <div v-if="!overview.available && !overview.checked_at" class="tf-card kc-loading">加载中…</div>
        <div v-else-if="!overview.available" class="tf-card kc-offline">
          <b>引擎离线</b>
          <p>KE API 不可达（{{ overview.engine?.base || '—' }}）{{ overview.detail ? '：' + overview.detail : '' }}</p>
          <p class="kc-dim">占位而非白屏：离线时本栏不展示任何数字，避免把空值读成零。</p>
          <el-button size="small" @click="refresh">重试</el-button>
        </div>

        <template v-else>
          <div class="tf-card pp-stats">
            <div class="pp-stat"><b>{{ n(overview.stats?.objects) }}</b><span>知识对象</span></div>
            <div class="pp-stat"><b>{{ n(overview.stats?.by_kind?.claim) }}</b><span>claim 命题</span></div>
            <div class="pp-stat"><b>{{ n(overview.stats?.by_kind?.evidence) }}</b><span>evidence 证据</span></div>
            <div class="pp-stat"><b>{{ n(overview.stats?.by_kind?.provenance) }}</b><span>provenance</span></div>
            <div class="pp-stat ok"><b>{{ n(overview.stats?.edges) }}</b><span>证据边</span></div>
            <div class="pp-stat warn"><b>{{ n(overview.stats?.review_events) }}</b><span>评审事件</span></div>
            <div class="pp-meta">{{ overview.domains?.length || 0 }} 个领域</div>
          </div>

          <div class="tf-card">
            <h3 class="pp-h">四态信任漏斗（生产 → 证据 → 评审 → 发布）</h3>
            <div v-for="row in overview.funnel || []" :key="row.key" class="kc-funnel-row">
              <span class="kc-funnel-label">{{ row.label }}</span>
              <div class="kc-funnel-track">
                <div class="kc-funnel-fill" :class="'is-' + row.key" :style="{ width: barPct(row.count) }" />
              </div>
              <b class="kc-funnel-count">{{ n(row.count) }}</b>
              <span class="kc-funnel-hint">{{ row.hint }}</span>
            </div>
            <p class="kc-dim">
              口径：生产=对象总数；证据=证据态已判定（扣除 unassessed/not-assessed）；
              评审=已进入评审态；发布=publication_status=published。
            </p>
          </div>

          <div class="kc-grid">
            <div class="tf-card">
              <h3 class="pp-h">领域分布</h3>
              <el-table :data="overview.domains || []" size="small" :max-height="320">
                <el-table-column prop="domain" label="领域" min-width="180" show-overflow-tooltip />
                <el-table-column prop="count" label="对象" width="80" />
                <el-table-column prop="claim" label="claim" width="80" />
                <el-table-column prop="evidence" label="evidence" width="90" />
                <el-table-column prop="provenance" label="prov" width="70" />
              </el-table>
            </div>

            <div class="tf-card">
              <h3 class="pp-h">按日增长（对象 created_at）</h3>
              <div class="kc-growth">
                <div v-for="g in overview.growth || []" :key="g.date" class="kc-growth-col">
                  <div class="kc-growth-bar" :style="{ height: growthH(g.count) }" :title="`${g.date} · ${g.count}`" />
                  <span class="kc-growth-n">{{ g.count }}</span>
                  <span class="kc-growth-d">{{ g.date.slice(5) }}</span>
                </div>
              </div>
              <p v-if="!(overview.growth || []).length" class="kc-dim">暂无带时间戳的对象。</p>
            </div>
          </div>

          <div class="kc-grid">
            <div class="tf-card">
              <h3 class="pp-h">被引最多（supports / qualifies / contextualizes / mentions / derived-from）</h3>
              <el-table :data="overview.cited || []" size="small" :max-height="360">
                <el-table-column prop="citations" label="被引" width="70" />
                <el-table-column label="命题" min-width="260">
                  <template #default="{ row }">
                    <span class="kc-mono">{{ row.claim_id }}</span>
                    <div v-if="row.content_hidden" class="kc-hidden">未发布 · 正文隐藏</div>
                    <div v-else class="kc-statement">{{ row.statement }}</div>
                  </template>
                </el-table-column>
                <el-table-column label="证据态" width="120">
                  <template #default="{ row }">
                    <el-tag size="small" effect="plain" :type="statusType(row.evidence_status)">{{ row.evidence_status || '—' }}</el-tag>
                  </template>
                </el-table-column>
                <el-table-column label="" width="80">
                  <template #default="{ row }">
                    <el-button link size="small" @click="openChain(row.claim_id)">证据链</el-button>
                  </template>
                </el-table-column>
              </el-table>
            </div>

            <div class="tf-card">
              <h3 class="pp-h">最近被证伪（relation = refutes）</h3>
              <el-table :data="overview.refuted || []" size="small" :max-height="360">
                <el-table-column prop="accessed_at" label="取证" width="110" />
                <el-table-column label="命题 / 反证来源" min-width="260">
                  <template #default="{ row }">
                    <span class="kc-mono">{{ row.claim_id }}</span>
                    <div v-if="row.content_hidden" class="kc-hidden">未发布 · 正文隐藏</div>
                    <div v-else class="kc-statement">{{ row.statement }}</div>
                    <div class="kc-dim">{{ row.citation }}</div>
                    <div class="kc-mono kc-dim">{{ row.locator }}</div>
                  </template>
                </el-table-column>
                <el-table-column label="" width="80">
                  <template #default="{ row }">
                    <el-button link size="small" @click="openChain(row.claim_id)">证据链</el-button>
                  </template>
                </el-table-column>
              </el-table>
              <p v-if="!(overview.refuted || []).length" class="kc-dim">当前知识库没有 refutes 关系。</p>
            </div>
          </div>
        </template>
      </el-tab-pane>

      <!-- ───────────────────────── tab2 知识地图 ───────────────────────── -->
      <el-tab-pane label="知识地图" name="map">
        <div v-if="!mapData.available && !mapData.checked_at" class="tf-card kc-loading">加载中…</div>
        <div v-else-if="!mapData.available" class="tf-card kc-offline">
          <b>引擎离线</b>
          <p>知识地图需要 KE API（{{ overview.engine?.base || '—' }}）。</p>
          <el-button size="small" @click="loadMap">重试</el-button>
        </div>

        <div v-else class="kc-map">
          <div class="tf-card kc-map-nav">
            <h3 class="pp-h">领域 → 类型 → 命题</h3>
            <div v-for="d in mapData.domains || []" :key="d.domain" class="kc-domain">
              <button type="button" class="kc-domain-btn" :class="{ 'is-active': activeDomain === d.domain }"
                      @click="selectDomain(d.domain)">
                <span>{{ d.domain }}</span><b>{{ d.total }}</b>
              </button>
              <div v-if="activeDomain === d.domain" class="kc-kind-list">
                <button v-for="k in d.kinds" :key="k.kind" type="button" class="kc-kind-btn"
                        :class="{ 'is-active': activeKind === k.kind }" @click="activeKind = k.kind">
                  {{ k.kind }} <b>{{ k.count }}</b>
                </button>
              </div>
            </div>
            <p v-if="!(mapData.domains || []).length" class="kc-dim">知识库还没有对象。</p>
          </div>

          <div class="tf-card kc-map-list">
            <h3 class="pp-h">{{ activeDomain || '—' }} · {{ activeKind }}</h3>
            <el-table :data="activeClaims" size="small" :max-height="520" highlight-current-row
                      @row-click="onRowClick">
              <el-table-column label="对象" min-width="240">
                <template #default="{ row }">
                  <span class="kc-mono">{{ row.id }}</span>
                  <div v-if="row.content_hidden" class="kc-hidden">未发布 · 正文隐藏</div>
                  <div v-else class="kc-statement">{{ row.statement }}</div>
                </template>
              </el-table-column>
              <el-table-column prop="citations" label="被引" width="70" />
              <el-table-column label="证据态" width="120">
                <template #default="{ row }">
                  <el-tag size="small" effect="plain" :type="statusType(row.evidence_status)">{{ row.evidence_status || '—' }}</el-tag>
                </template>
              </el-table-column>
              <el-table-column label="发布态" width="110">
                <template #default="{ row }">
                  <el-tag size="small" effect="plain" :type="pubType(row.publication_status)">{{ row.publication_status || '—' }}</el-tag>
                </template>
              </el-table-column>
              <el-table-column label="证据链" width="90">
                <template #default="{ row }">
                  <el-button v-if="isClaim(row.id)" link size="small" @click.stop="openChain(row.id)">展开</el-button>
                  <span v-else class="kc-dim">—</span>
                </template>
              </el-table-column>
            </el-table>
            <p class="kc-dim">证据链只挂在 claim 上；evidence / provenance 记录本身是链条节点，无下钻。</p>
          </div>

          <div class="tf-card kc-map-chain">
            <h3 class="pp-h">证据链面板</h3>
            <p v-if="!chain.claim_id" class="kc-dim">点左侧任一命题，展开 claim → evidence → 源文件:行号@commit。</p>
            <template v-else>
              <div class="kc-chain-head">
                <span class="kc-mono">{{ chain.claim_id }}</span>
                <el-tag v-if="chain.claim?.content_hidden" size="small" type="info" effect="plain">未发布 · 正文隐藏</el-tag>
                <el-tag size="small" effect="plain" :type="statusType(chain.claim?.evidence_status)">
                  {{ chain.claim?.evidence_status || '未判定' }}
                </el-tag>
                <span class="kc-dim">链接 {{ (chain.links || []).length }} · 完整链 {{ chain.complete ? '是' : '否' }}</span>
              </div>
              <p v-if="!chain.claim?.content_hidden" class="kc-statement">{{ chain.claim?.statement }}</p>
              <div v-if="chain.claim?.record_source" class="kc-src-row">
                <span class="kc-dim">claim 记录</span>
                <el-button link size="small" @click="openSource(chain.claim.record_source.path, chain.claim.record_source.line)">
                  {{ chain.claim.record_source.path }}:{{ chain.claim.record_source.line }}
                </el-button>
                <span class="kc-mono kc-dim">@{{ chain.claim.record_source.commit || '—' }}</span>
              </div>

              <div v-for="(l, i) in chain.links || []" :key="l.evidence_id" class="kc-link">
                <div class="kc-link-head">
                  <span class="kc-link-n">{{ i + 1 }}</span>
                  <el-tag size="small" effect="plain" :type="relationType(l.relation)">{{ l.relation }}</el-tag>
                  <span class="kc-mono">{{ l.evidence_id }}</span>
                  <el-tag size="small" effect="plain" :type="statusType(l.assessment_status)">{{ l.assessment_status || '—' }}</el-tag>
                  <el-tag v-if="l.publication_status" size="small" effect="plain" :type="pubType(l.publication_status)">{{ l.publication_status }}</el-tag>
                </div>
                <div class="kc-link-src">
                  <span>{{ l.source?.citation || '（无来源）' }}</span>
                  <a v-if="l.source?.url" :href="l.source.url" target="_blank" rel="noopener" class="kc-ext">源链接 ↗</a>
                </div>
                <div class="kc-src-row">
                  <span class="kc-dim">定位</span>
                  <span class="kc-mono">{{ l.locator?.value || '—' }}</span>
                  <span class="kc-mono kc-dim">hash {{ shortHash(l.locator?.content_hash) }}</span>
                  <el-button v-if="l.record_source" link size="small"
                             @click="openSource(l.record_source.path, l.record_source.line)">
                    记录直读 {{ l.record_source.path }}:{{ l.record_source.line }}
                  </el-button>
                </div>
                <pre v-if="l.locator?.quote" class="kc-quote">{{ l.locator.quote }}</pre>
                <p v-else-if="l.locator?.quote_hidden" class="kc-hidden">未发布 · 引文正文隐藏（定位与哈希照常可核）</p>
              </div>
              <p v-if="!(chain.links || []).length" class="kc-dim">该命题尚无证据链接。</p>
              <p v-if="(chain.dangling_evidence_refs || []).length" class="kc-warn">
                悬空证据引用：{{ chain.dangling_evidence_refs.join('、') }}
              </p>
            </template>
          </div>
        </div>
      </el-tab-pane>

      <!-- ────────────────── tab3 检索与训练资产 ────────────────── -->
      <el-tab-pane label="检索与训练资产" name="assets">
        <div class="tf-card">
          <h3 class="pp-h">知识检索（KE /search，只读演示面）</h3>
          <div class="kc-search-row">
            <el-input v-model="q" placeholder="检索命题陈述 / 证据引文 / 领域，例如 lattice" clearable
                      :disabled="!overview.available" @keyup.enter="runSearch" />
            <el-button type="primary" :loading="searching" :disabled="!overview.available" @click="runSearch">检索</el-button>
          </div>
          <p v-if="!overview.available" class="kc-dim">引擎离线：检索框停用（不返回假结果）。</p>
          <template v-else-if="searchRes.count !== null">
            <p class="kc-dim">命中 {{ searchRes.count }} 条（查询 “{{ searchRes.q }}”）</p>
            <el-table :data="searchRes.items || []" size="small" :max-height="360">
              <el-table-column label="对象" min-width="300">
                <template #default="{ row }">
                  <span class="kc-mono">{{ row.id }}</span>
                  <span class="kc-tag-gap">{{ row.kind }}</span>
                  <div v-if="row.content_hidden" class="kc-hidden">未发布 · 正文隐藏</div>
                  <div v-else-if="row.statement" class="kc-statement">{{ row.statement }}</div>
                  <div v-else class="kc-dim">{{ row.snippet }}</div>
                </template>
              </el-table-column>
              <el-table-column prop="domain" label="领域" width="170" show-overflow-tooltip />
              <el-table-column label="证据态" width="120">
                <template #default="{ row }">
                  <el-tag size="small" effect="plain" :type="statusType(row.evidence_status)">{{ row.evidence_status || '—' }}</el-tag>
                </template>
              </el-table-column>
              <el-table-column label="发布态" width="110">
                <template #default="{ row }">
                  <el-tag size="small" effect="plain" :type="pubType(row.publication_status)">{{ row.publication_status || '—' }}</el-tag>
                </template>
              </el-table-column>
              <el-table-column label="溯源" width="90">
                <template #default="{ row }">
                  <el-button v-if="row.kind === 'claim'" link size="small" @click="openChain(row.id)">证据链</el-button>
                  <span v-else class="kc-dim">—</span>
                </template>
              </el-table-column>
            </el-table>
          </template>
        </div>

        <div class="tf-card">
          <h3 class="pp-h">训练资产（导出三件套 manifest 摘要）</h3>
          <p v-if="!assets.available" class="kc-dim">
            训练资产清单不可用：{{ assets.reason === 'asset_dir_missing'
              ? '导出目录不存在（' + (assets.detail || '—') + '）' : '未找到 manifest 文件' }}
          </p>
          <template v-else>
            <el-table :data="assets.manifests || []" size="small">
              <el-table-column prop="kind" label="件" width="90" />
              <el-table-column prop="version" label="版本" width="110" />
              <el-table-column prop="generated_at" label="生成时间" width="180" />
              <el-table-column prop="upstream_commit" label="上游 commit" width="120" />
              <el-table-column prop="total_claims" label="命题" width="80" />
              <el-table-column prop="included" label="入集" width="80" />
              <el-table-column label="状态分布" min-width="200">
                <template #default="{ row }">
                  <span class="kc-mono">{{ distText(row.status_distribution || row.tolerance_distribution) }}</span>
                </template>
              </el-table-column>
              <el-table-column label="sha256" width="110">
                <template #default="{ row }">
                  <span class="kc-mono">{{ row.sha256_8 || '—' }}</span>
                </template>
              </el-table-column>
            </el-table>
            <p class="kc-dim">目录 {{ assets.directory }}；sha256 取导出工件前缀 8 位（manifest 不含哈希，按文件实测）。</p>
          </template>
        </div>
      </el-tab-pane>
    </el-tabs>

    <el-drawer v-model="srcDrawer" size="52%" :title="srcTitle">
      <div v-if="srcError" class="kc-dim">{{ srcError }}</div>
      <template v-else>
        <div class="kc-src-meta">
          <span class="kc-mono">{{ srcView.path }}</span>
          <span class="kc-dim">@{{ srcView.commit || '—' }} · 共 {{ srcView.total_lines }} 行 · 焦点 {{ srcView.focus }}</span>
          <a v-if="srcView.path" class="kc-ext" :href="rawSourceUrl" target="_blank" rel="noopener">新窗口免鉴权直读 ↗</a>
        </div>
        <pre class="kc-src-body"><span v-for="ln in srcView.lines || []" :key="ln.n"
          :class="{ 'is-focus': ln.n === srcView.focus }"><i>{{ ln.n }}</i>{{ ln.text }}
</span></pre>
      </template>
    </el-drawer>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { knowledgeApi } from '@/api'
import HeroPanel from '@/components/HeroPanel.vue'

const tab = ref('overview')
const overview = reactive<any>({ available: false })
const mapData = reactive<any>({ available: false, domains: [] })
const chain = reactive<any>({})
const searchRes = reactive<any>({ q: '', count: null, items: [] })
const assets = reactive<any>({ available: false, manifests: [] })

const activeDomain = ref('')
const activeKind = ref('claim')
const q = ref('lattice')
const searching = ref(false)

const srcDrawer = ref(false)
const srcView = reactive<any>({})
const srcError = ref('')
const srcTitle = computed(() => srcView.path ? `证据直读 · ${srcView.path}` : '证据直读')
const rawSourceUrl = computed(() =>
  srcView.path ? `/api/v1/tensor/knowledge/source/${srcView.path}?line=${srcView.focus || 1}` : '#')

const n = (v: any) => (v === null || v === undefined ? '—' : v)
const barPct = (v: any) => {
  const total = Number(overview.stats?.objects || 0)
  if (!total || !v) return '0%'
  return Math.max(2, Math.round((Number(v) / total) * 100)) + '%'
}
const growthH = (v: number) => {
  const max = Math.max(...(overview.growth || []).map((g: any) => Number(g.count) || 0), 1)
  return Math.max(4, Math.round((Number(v) / max) * 90)) + 'px'
}
const distText = (d: any) =>
  Object.entries(d || {}).map(([k, v]) => `${k}:${v}`).join(' · ') || '—'
const shortHash = (h?: string) => (h ? String(h).slice(0, 8) : '—')

const statusType = (v?: string) =>
  v === 'established' || v === 'well-supported' || v === 'approved' ? 'success'
    : v === 'contested' || v === 'changes-requested' ? 'warning'
      : v === 'refuted' || v === 'rejected' || v === 'revoked' ? 'danger' : 'info'
const pubType = (v?: string) => (v === 'published' ? 'success' : v === 'retracted' ? 'danger' : 'info')
const relationType = (v?: string) =>
  v === 'supports' ? 'success' : v === 'refutes' ? 'danger' : 'info'

const activeClaims = computed(() => {
  const d = (mapData.domains || []).find((x: any) => x.domain === activeDomain.value)
  const k = (d?.kinds || []).find((x: any) => x.kind === activeKind.value)
  return k?.claims || []
})

function selectDomain(domain: string) {
  activeDomain.value = domain
  const d = (mapData.domains || []).find((x: any) => x.domain === domain)
  activeKind.value = d?.kinds?.some((k: any) => k.kind === 'claim') ? 'claim' : (d?.kinds?.[0]?.kind || 'claim')
}

async function loadOverview() {
  try {
    const res = await knowledgeApi.overview()
    Object.assign(overview, res)
  } catch (e: any) {
    Object.assign(overview, {
      available: false, detail: e?.response?.data?.detail || e?.message || 'request failed',
      engine: { base: '—' }, checked_at: new Date().toISOString(),
    })
  }
}

async function loadMap() {
  try {
    const res = await knowledgeApi.map()
    Object.assign(mapData, res)
    if (res.available && !activeDomain.value && (res.domains || []).length) {
      selectDomain(res.domains[0].domain)
    }
  } catch (e: any) {
    Object.assign(mapData, {
      available: false, domains: [], detail: e?.message,
      checked_at: new Date().toISOString(),
    })
  }
}

async function loadAssets() {
  try {
    Object.assign(assets, await knowledgeApi.assets())
  } catch (e: any) {
    Object.assign(assets, { available: false, reason: 'request_failed', detail: e?.message, manifests: [] })
  }
}

const isClaim = (id?: string) => !!id && id.startsWith('tkc:')

function onRowClick(row: any) {
  if (isClaim(row?.id)) openChain(row.id)
}

async function openChain(claimId: string) {
  if (!claimId) return
  if (!isClaim(claimId)) {
    ElMessage.info('只有 claim 命题可展开证据链')
    return
  }
  tab.value = 'map'
  await loadMap()
  try {
    const res = await knowledgeApi.chain(claimId)
    Object.keys(chain).forEach((k) => delete chain[k])
    Object.assign(chain, res)
    if (!res.available) ElMessage.warning('引擎离线：证据链不可用')
    const dom = (mapData.domains || []).find((d: any) =>
      (d.kinds || []).some((k: any) => (k.claims || []).some((c: any) => c.id === claimId)))
    if (dom) selectDomain(dom.domain)
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || '证据链加载失败')
  }
}

async function runSearch() {
  if (!q.value.trim()) return
  searching.value = true
  try {
    Object.assign(searchRes, await knowledgeApi.search({ q: q.value.trim(), limit: 20 }))
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || '检索失败')
  } finally {
    searching.value = false
  }
}

async function openSource(path: string, line?: number) {
  srcError.value = ''
  Object.keys(srcView).forEach((k) => delete srcView[k])
  srcDrawer.value = true
  try {
    Object.assign(srcView, await knowledgeApi.source(path, line))
  } catch (e: any) {
    srcError.value = e?.response?.data?.detail || e?.message || '直读失败'
  }
}

function onTab(name: any) {
  if (name === 'map' && !mapData.available) loadMap()
  if (name === 'assets') {
    if (!assets.available) loadAssets()
    if (overview.available && searchRes.count === null) runSearch()
  }
}

function refresh() {
  loadOverview()
  if (tab.value === 'map') loadMap()
  if (tab.value === 'assets') { loadAssets(); runSearch() }
}

onMounted(() => {
  loadOverview()
  loadAssets()
})
</script>

<style scoped>
.kc-tabs { margin-top: 4px; }
.kc-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(420px, 1fr)); gap: 14px; }
.kc-map { display: grid; grid-template-columns: 260px minmax(320px, 1fr) minmax(360px, 1.15fr); gap: 14px; align-items: start; }
@media (max-width: 1400px) { .kc-map { grid-template-columns: 1fr; } }
.kc-loading { opacity: 0.6; font-size: 13px; }
.kc-offline { border-left: 3px solid #e6a23c; }
.kc-offline p { margin: 6px 0; font-size: 13px; }
.kc-dim { opacity: 0.55; font-size: 12px; }
.kc-hidden { font-size: 12px; opacity: 0.5; font-style: italic; }
.kc-mono { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12px; }
.kc-statement { font-size: 12.5px; line-height: 1.5; opacity: 0.9; margin-top: 2px; }
.kc-tag-gap { margin-left: 8px; font-size: 12px; opacity: 0.6; }
.kc-warn { color: #e6a23c; font-size: 12px; }
.kc-funnel-row { display: grid; grid-template-columns: 46px 1fr 52px 300px; align-items: center; gap: 10px; margin: 8px 0; }
.kc-funnel-label { font-size: 13px; }
.kc-funnel-track { height: 14px; background: rgba(120, 140, 170, 0.16); border-radius: 7px; overflow: hidden; }
.kc-funnel-fill { height: 100%; border-radius: 7px; background: #6b8cff; transition: width 0.35s ease; }
.kc-funnel-fill.is-evidenced { background: #38b2ac; }
.kc-funnel-fill.is-reviewed { background: #e6a23c; }
.kc-funnel-fill.is-published { background: #67c23a; }
.kc-funnel-count { text-align: right; font-variant-numeric: tabular-nums; }
.kc-funnel-hint { font-size: 11.5px; opacity: 0.5; }
.kc-growth { display: flex; align-items: flex-end; gap: 8px; min-height: 120px; padding-top: 8px; overflow-x: auto; }
.kc-growth-col { display: flex; flex-direction: column; align-items: center; gap: 2px; min-width: 42px; }
.kc-growth-bar { width: 22px; background: linear-gradient(180deg, #6b8cff, #3f5ed6); border-radius: 3px 3px 0 0; }
.kc-growth-n { font-size: 11px; opacity: 0.75; }
.kc-growth-d { font-size: 10.5px; opacity: 0.45; }
.kc-map-nav { position: sticky; top: 76px; }
.kc-domain-btn, .kc-kind-btn {
  width: 100%; display: flex; justify-content: space-between; gap: 8px; align-items: center;
  padding: 6px 8px; margin: 3px 0; border: 1px solid transparent; border-radius: 6px;
  background: rgba(120, 140, 170, 0.1); color: inherit; font-size: 12.5px; cursor: pointer; text-align: left;
}
.kc-domain-btn.is-active, .kc-kind-btn.is-active { border-color: #6b8cff; background: rgba(107, 140, 255, 0.16); }
.kc-kind-list { padding-left: 10px; margin-bottom: 4px; }
.kc-kind-btn { font-size: 12px; }
.kc-chain-head { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; margin-bottom: 6px; }
.kc-link { border-top: 1px dashed rgba(120, 140, 170, 0.3); padding-top: 8px; margin-top: 8px; }
.kc-link-head { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.kc-link-n { width: 18px; height: 18px; border-radius: 50%; background: rgba(107, 140, 255, 0.25); font-size: 11px; display: inline-flex; align-items: center; justify-content: center; }
.kc-link-src { font-size: 12px; opacity: 0.8; margin: 4px 0; }
.kc-src-row { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; font-size: 12px; }
.kc-quote { font-size: 11.5px; line-height: 1.45; margin: 6px 0 0; padding: 8px; border-radius: 6px; background: rgba(120, 140, 170, 0.12); white-space: pre-wrap; word-break: break-word; }
.kc-ext { font-size: 12px; color: #6b8cff; text-decoration: none; margin-left: 6px; }
.kc-search-row { display: flex; gap: 10px; margin-bottom: 10px; }
.kc-src-meta { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; margin-bottom: 8px; }
.kc-src-body { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12px; line-height: 1.5; margin: 0; max-height: 70vh; overflow: auto; white-space: pre-wrap; word-break: break-word; }
.kc-src-body i { display: inline-block; width: 46px; opacity: 0.4; font-style: normal; text-align: right; margin-right: 10px; user-select: none; }
.kc-src-body .is-focus { background: rgba(107, 140, 255, 0.2); display: block; }
.tf-hero-mint { color: #67e8c3; }
</style>