<template>
  <div class="dw">
    <PageHeader title="🔬 数据工作台" desc="数据概览 · 在线编辑 · 时序对齐" />

    <el-tabs v-model="tab">
      <!-- ═══ Tab 1: 数据概览 ═══ -->
      <el-tab-pane label="📋 数据概览" name="profile">
        <el-card>
          <template #header>
            <div class="card-hd">
              <span>选择数据集查看概览</span>
              <el-select v-model="profileDsId" placeholder="选择数据集" filterable style="width:300px" @change="loadProfile">
                <el-option v-for="ds in datasets" :key="ds.id" :label="`#${ds.id} ${ds.name}`" :value="ds.id" />
              </el-select>
            </div>
          </template>

          <div v-if="profileLoading" style="text-align:center;padding:40px"><el-icon class="is-loading"><Loading /></el-icon> 加载概览中...</div>

          <div v-if="profileData" class="profile-result">
            <el-row :gutter="12" class="overview-summary">
              <el-col :xs="12" :md="6"><el-card><el-statistic title="总行数" :value="profileData.total_rows" /></el-card></el-col>
              <el-col :xs="12" :md="6"><el-card><el-statistic title="总列数" :value="profileData.total_cols" /></el-card></el-col>
              <el-col :xs="12" :md="6"><el-card><el-statistic title="数值字段" :value="profileData.numeric_cols?.length||0" /></el-card></el-col>
              <el-col :xs="12" :md="6"><el-card><el-statistic title="整体缺失率" :value="missingPct" suffix="%" /></el-card></el-col>
            </el-row>

            <el-card class="overview-card">
              <template #header><span>字段概览</span></template>
              <div class="field-overview-hint">点击字段最左侧箭头，可查看该字段为空的具体记录。</div>
              <el-table :data="profileData.field_overview || []" row-key="name" stripe max-height="420" size="small" @expand-change="onFieldExpand">
                <el-table-column type="expand" width="46">
                  <template #default="{ row }">
                    <div v-if="!row.missing_count" class="missing-empty">该字段没有缺失记录。</div>
                    <div v-else class="missing-records">
                      <div class="missing-records-title">字段“{{ row.name }}”共有 {{ row.missing_count }} 条缺失记录</div>
                      <el-skeleton v-if="missingState(row.name).loading" :rows="3" animated />
                      <template v-else>
                        <el-table :data="missingState(row.name).rows || []" border size="small" max-height="240">
                          <el-table-column v-for="column in missingState(row.name).columns || []" :key="column.key" :prop="column.key" :label="column.label" min-width="120" />
                        </el-table>
                        <el-pagination
                          v-if="missingState(row.name).total > missingPageSize"
                          small
                          background
                          layout="total, prev, pager, next"
                          :total="missingState(row.name).total"
                          :page-size="missingPageSize"
                          :current-page="missingState(row.name).page || 1"
                          class="missing-pagination"
                          @current-change="(page) => loadMissingRecords(row, page)"
                        />
                      </template>
                    </div>
                  </template>
                </el-table-column>
                <el-table-column prop="name" label="字段名" min-width="160" fixed />
                <el-table-column prop="role" label="字段类型" width="100">
                  <template #default="{ row }">
                    <el-tag size="small" :type="row.role === 'time' ? 'warning' : row.role === 'numeric' ? 'success' : 'info'">
                      {{ row.role === 'time' ? '时间' : row.role === 'numeric' ? '数值' : '文本' }}
                    </el-tag>
                  </template>
                </el-table-column>
                <el-table-column prop="dtype" label="存储类型" min-width="110" />
                <el-table-column prop="non_null_count" label="非空数" width="100" />
                <el-table-column label="缺失率" width="100"><template #default="{ row }">{{ formatPercent(row.missing_rate) }}</template></el-table-column>
                <el-table-column prop="unique_count" label="不同值" width="100" />
                <el-table-column prop="min" label="最小值" min-width="110" />
                <el-table-column prop="max" label="最大值" min-width="110" />
              </el-table>
            </el-card>

            <el-card class="overview-card">
              <template #header><span>数据样例（前 5 行，只读）</span></template>
              <el-table :data="profileData.sample_rows || []" stripe border max-height="300" size="small">
                <el-table-column v-for="column in profileData.sample_columns || []" :key="column" :prop="column" :label="column" min-width="130" show-overflow-tooltip />
              </el-table>
            </el-card>

            <el-card class="overview-card next-step-card">
              <template #header><span>下一步操作</span></template>
              <el-button type="primary" plain @click="goToDataModule('/data/cleaning')">清洗分析</el-button>
              <el-button type="primary" plain @click="goToDataModule('/data/analysis')">算法数据分析</el-button>
              <el-button type="primary" plain @click="goToDataModule('/data/feature-engineering')">特征工程</el-button>
            </el-card>
          </div>
        </el-card>
      </el-tab-pane>

      <!-- ═══ Tab 2: 数据编辑 ═══ -->
      <el-tab-pane label="✏️ 数据编辑" name="editor">
        <el-card>
          <template #header>
            <div class="card-hd">
              <span>在线编辑数据集</span>
              <div style="display:flex;gap:8px">
                <el-select v-model="editorDsId" placeholder="选择数据集" filterable style="width:250px" @change="loadEditor">
                  <el-option v-for="ds in datasets" :key="ds.id" :label="`#${ds.id} ${ds.name}`" :value="ds.id" />
                </el-select>
              </div>
            </div>
          </template>

          <el-alert
            title="原数据集保持只读"
            description="页面中的修改只保存在当前编辑区。可另存为新的数据集，或下载编辑后的 CSV；不会覆盖原数据集。"
            type="info"
            show-icon
            :closable="false"
            class="editor-safety-alert"
          />

          <div v-if="editorDsId" class="editor-actions">
            <el-input
              v-model="editorOutputName"
              placeholder="新数据集名称"
              maxlength="128"
              show-word-limit
              style="width: 300px"
            />
            <el-button
              type="primary"
              @click="saveEditorCopy"
              :loading="saveLoading"
              :disabled="!canExportEditor"
            >💾 保存为新数据集</el-button>
            <el-button
              type="success"
              plain
              @click="downloadEditorCsv"
              :loading="downloadLoading"
              :disabled="!canExportEditor"
            >⬇ 下载编辑结果</el-button>
            <el-button @click="addRow" :disabled="!editorCols.length">+ 添加行</el-button>
            <el-button @click="addColumn" :disabled="!editorCols.length">+ 添加列</el-button>
            <el-tag v-if="editorDirty" type="warning">有未导出的修改</el-tag>
          </div>

          <div v-if="editorLoading" style="text-align:center;padding:40px">
            <el-icon class="is-loading"><Loading /></el-icon> 加载中...
          </div>

          <div v-if="editorRows.length > 0" class="editor-table-wrap">
            <el-table :data="editorRows" stripe border max-height="500" size="small" style="width:100%">
              <el-table-column type="index" width="50" fixed label="#" />
              <el-table-column v-for="col in editorCols" :key="col" :prop="col" :label="col" min-width="120">
                <template #default="{ row }">
                  <el-input v-model="row[col]" size="small" placeholder="(空)" @change="editorDirty=true" />
                </template>
              </el-table-column>
              <el-table-column label="操作" width="70" fixed="right">
                <template #default="{ $index }">
                  <el-button size="small" type="danger" @click="removeRow($index)">✕</el-button>
                </template>
              </el-table-column>
            </el-table>
            <div style="margin-top:8px;color:var(--tx2);font-size:12px">
              源数据共 {{ editorTotal }} 条 · 已加载源数据 {{ editorLoadedOriginalCount }} 条 · 当前编辑区 {{ editorRows.length }} 条 ·
              <el-button size="small" text @click="loadMoreEditor" :disabled="editorLoadedOriginalCount >= editorTotal">加载更多</el-button>
              <span v-if="editorLoadedOriginalCount < editorTotal">（保存或下载时会自动补齐未加载的数据）</span>
            </div>
          </div>
        </el-card>
      </el-tab-pane>

      <!-- ═══ Tab 3: 数据对齐 ═══ -->
      <el-tab-pane label="⏱️ 数据对齐" name="align">
        <el-card>
          <template #header><span>时序数据对齐工具</span></template>
          <el-row :gutter="16">
            <el-col :span="12">
              <h4>源数据集</h4>
              <el-select v-model="alignSrcId" placeholder="选择源数据集" filterable style="width:100%">
                <el-option v-for="ds in datasets" :key="ds.id" :label="`#${ds.id} ${ds.name}`" :value="ds.id" :disabled="Number(ds.id) === Number(alignTgtId)" />
              </el-select>
              <el-form-item label="时间列" style="margin-top:8px">
                <el-select v-model="alignSrcCol" placeholder="选择时间列" style="width:100%">
                  <el-option v-for="c in srcTimeColumns" :key="c" :label="c" :value="c" />
                </el-select>
              </el-form-item>
              <el-alert v-if="alignSrcId && !srcTimeColumns.length" title="源数据集未识别到时间列" type="warning" :closable="false" show-icon />
            </el-col>
            <el-col :span="12">
              <h4>目标数据集</h4>
              <el-select v-model="alignTgtId" placeholder="选择目标数据集" filterable style="width:100%">
                <el-option v-for="ds in datasets" :key="ds.id" :label="`#${ds.id} ${ds.name}`" :value="ds.id" :disabled="Number(ds.id) === Number(alignSrcId)" />
              </el-select>
              <el-form-item label="时间列" style="margin-top:8px">
                <el-select v-model="alignTgtCol" placeholder="选择时间列" style="width:100%">
                  <el-option v-for="c in tgtTimeColumns" :key="c" :label="c" :value="c" />
                </el-select>
              </el-form-item>
              <el-alert v-if="alignTgtId && !tgtTimeColumns.length" title="目标数据集未识别到时间列" type="warning" :closable="false" show-icon />
            </el-col>
          </el-row>
          <el-row :gutter="16" style="margin-top:12px">
            <el-col :span="6">
              <el-form-item label="对齐方式">
                <el-select v-model="alignMethod" style="width:100%">
                  <el-option label="匹配最近时间记录" value="nearest" />
                  <el-option label="按时间线性计算" value="linear" />
                  <el-option label="使用之前的记录" value="ffill" />
                  <el-option label="使用之后的记录" value="bfill" />
                </el-select>
              </el-form-item>
            </el-col>
            <el-col :span="6">
              <el-form-item label="生成时间间隔">
                <el-select v-model="alignFreq" style="width:100%" clearable placeholder="使用源数据时间点">
                  <el-option label="1分钟" value="1min" />
                  <el-option label="5分钟" value="5min" />
                  <el-option label="15分钟" value="15min" />
                  <el-option label="1小时" value="1h" />
                  <el-option label="1天" value="1d" />
                </el-select>
              </el-form-item>
            </el-col>
            <el-col :span="6">
              <el-form-item label="最大时间差">
                <el-select v-model="alignTolerance" style="width:100%">
                  <el-option label="1分钟" value="1min" />
                  <el-option label="5分钟（推荐）" value="5min" />
                  <el-option label="15分钟" value="15min" />
                  <el-option label="1小时" value="1h" />
                  <el-option label="1天" value="1d" />
                </el-select>
              </el-form-item>
            </el-col>
            <el-col :span="6" style="display:flex;align-items:flex-end;padding-bottom:18px">
              <el-button type="primary" @click="doAlign" :loading="alignLoading" :disabled="!alignReady">⏱️ 开始对齐</el-button>
            </el-col>
          </el-row>

          <div v-if="alignResult" style="margin-top:16px">
            <el-alert
              :title="`对齐完成：${alignResult.fully_matched_rows}/${alignResult.aligned_rows} 行完整匹配（${formatMatchRate(alignResult.match_rate)}）`"
              :description="`${alignResult.time_start} ~ ${alignResult.time_end}`"
              type="success"
              :closable="false"
              show-icon
            />
            <el-alert
              v-for="(warning, index) in alignResult.warnings || []"
              :key="'align-warning-' + index"
              :title="warning"
              type="warning"
              :closable="false"
              show-icon
              style="margin-top:8px"
            />
            <el-alert
              v-if="alignResult.ai_assistance?.summary"
              :title="`🤖 AI 对齐结果解读（${alignResult.ai_assistance.provider || 'DeepSeek'}）`"
              :description="alignResult.ai_assistance.summary"
              :type="alignResult.ai_assistance.assessment === 'good' ? 'success' : alignResult.ai_assistance.assessment === 'poor' ? 'error' : 'warning'"
              :closable="false"
              show-icon
              class="alignment-ai-alert"
            />
            <el-alert
              v-else-if="alignResult.ai_assistance?.warning"
              :title="alignResult.ai_assistance.warning"
              type="warning"
              :closable="false"
              show-icon
              class="alignment-ai-alert"
            />
            <div class="alignment-checks">
              <div class="alignment-checks-title">对齐结果检查</div>
              <el-table :data="alignResult.alignment_checks || []" border size="small">
                <el-table-column label="结果" width="90"><template #default="{ row }"><el-tag size="small" :type="row.status === 'passed' ? 'success' : 'warning'">{{ row.status === 'passed' ? '正常' : '需关注' }}</el-tag></template></el-table-column>
                <el-table-column prop="name" label="检查项" min-width="150" />
                <el-table-column prop="expected" label="判断标准" min-width="190" />
                <el-table-column prop="actual" label="本次结果" min-width="190" />
              </el-table>
            </div>
            <el-descriptions :column="5" border size="small" style="margin-top:12px">
              <el-descriptions-item label="输出行数">{{ alignResult.aligned_rows }}</el-descriptions-item>
              <el-descriptions-item label="完整匹配">{{ alignResult.fully_matched_rows }}</el-descriptions-item>
              <el-descriptions-item label="未匹配">{{ alignResult.unmatched_rows }}</el-descriptions-item>
              <el-descriptions-item label="平均时间差">{{ formatSeconds(alignResult.mean_time_delta_seconds) }}</el-descriptions-item>
              <el-descriptions-item label="最大时间差">{{ formatSeconds(alignResult.max_time_delta_seconds) }}</el-descriptions-item>
            </el-descriptions>
            <div class="align-save-actions">
              <el-input v-model="alignOutputName" maxlength="128" show-word-limit placeholder="新数据集名称" style="width:320px" />
              <el-button size="small" type="primary" @click="saveAlign" :loading="alignSaving" :disabled="!alignOutputName.trim()">💾 保存为新数据集</el-button>
              <el-button size="small" type="success" @click="downloadAligned" :disabled="!savedAlignId">⬇ 下载 CSV</el-button>
            </div>
            <el-table :data="alignResult.preview||[]" stripe border max-height="400" size="small" style="margin-top:12px">
              <el-table-column v-for="col in alignColumns" :key="col" :prop="col" :label="col" min-width="120" />
            </el-table>
            <div v-if="alignResult.preview_truncated" class="align-preview-note">
              仅展示前 {{ alignResult.preview_rows }} 行，保存和下载包含全部 {{ alignResult.aligned_rows }} 行
            </div>
          </div>
        </el-card>
      </el-tab-pane>
    </el-tabs>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, watch } from 'vue'
import { downloadFile, get, post } from '@/api/legacy'
import { ElMessage } from 'element-plus'
import { useRouter } from 'vue-router'

const tab = ref('profile')
const router = useRouter()

// ── 公共数据集列表 ──
const datasets = ref<any[]>([])
async function loadDatasets() {
  try { const d = await get('/datasets?limit=200'); datasets.value = Array.isArray(d)?d:(d.items||[]) }
  catch(e:any){}
}
onMounted(() => loadDatasets())

// ═══════════ Tab 1: 数据概览 ═══════════
const profileDsId = ref(''), profileLoading = ref(false), profileData = ref<any>(null)
const missingStates = ref<Record<string, any>>({})
const missingPageSize = 10
const missingPct = computed(() => {
  if (!profileData.value) return 0
  const missing = profileData.value.missing || {}
  const total = profileData.value.total_rows * profileData.value.total_cols
  if (!total) return 0
  const missingTotal = Object.values(missing).reduce((sum:number, value:any) => sum + Number(value), 0)
  return (missingTotal / total * 100).toFixed(1)
})
const formatPercent = (value: any) => `${(Number(value || 0) * 100).toFixed(1)}%`

async function loadProfile() {
  if (!profileDsId.value) return
  profileLoading.value = true
  profileData.value = null
  missingStates.value = {}
  try { profileData.value = await get(`/datasets/${profileDsId.value}/profile`) }
  catch(e:any) { ElMessage.error(e?.message || '加载数据概览失败') }
  finally { profileLoading.value = false }
}

const missingState = (field: string) => missingStates.value[field] || {}

async function loadMissingRecords(row: any, page = 1) {
  if (!profileDsId.value || !row?.name || !row.missing_count) return
  const field = String(row.name)
  const previous = missingState(field)
  missingStates.value = { ...missingStates.value, [field]: { ...previous, loading: true, page } }
  try {
    const offset = (page - 1) * missingPageSize
    const result = await get(`/datasets/${profileDsId.value}/missing-records?column=${encodeURIComponent(field)}&offset=${offset}&limit=${missingPageSize}`)
    missingStates.value = { ...missingStates.value, [field]: { ...result, loading: false, page } }
  } catch (e: any) {
    missingStates.value = { ...missingStates.value, [field]: { ...previous, loading: false, page } }
    ElMessage.error(e?.message || '读取缺失记录失败')
  }
}

function onFieldExpand(row: any, expandedRows: any[]) {
  const expanded = (expandedRows || []).some((item: any) => item?.name === row?.name)
  if (expanded && row?.missing_count && !missingState(String(row.name)).rows) loadMissingRecords(row)
}

function goToDataModule(path: string) {
  router.push({ path, query: profileDsId.value ? { dataset_id: String(profileDsId.value) } : undefined })
}

// ═══════════ Tab 2: 数据编辑 ═══════════
const SOURCE_ROW_INDEX = Symbol('sourceRowIndex')
const editorDsId = ref<any>(''), editorLoading = ref(false), saveLoading = ref(false), downloadLoading = ref(false)
const editorRows = ref<any[]>([]), editorCols = ref<string[]>([]), editorTotal = ref(0), editorDirty = ref(false)
const editorLoadedOriginalCount = ref(0)
const editorOutputName = ref('')
const canExportEditor = computed(() =>
  Boolean(editorDsId.value)
  && editorCols.value.length > 0
  && (editorRows.value.length > 0 || editorLoadedOriginalCount.value < editorTotal.value)
  && Boolean(editorOutputName.value.trim())
  && !saveLoading.value
  && !downloadLoading.value
)

function normalizeEditorRows(rows: any[], columns: string[], sourceOffset: number | null = null) {
  return (rows || []).map((row, rowIndex) => {
    const normalized = Object.fromEntries(columns.map((column, columnIndex) => [
      column,
      Array.isArray(row) ? (row[columnIndex] ?? '') : (row?.[column] ?? '')
    ]))
    Object.defineProperty(normalized, SOURCE_ROW_INDEX, {
      value: sourceOffset == null ? null : sourceOffset + rowIndex,
      enumerable: false,
      configurable: false,
    })
    return normalized
  })
}

async function loadEditor() {
  if (!editorDsId.value) return
  editorLoading.value = true
  editorRows.value = []
  editorCols.value = []
  editorTotal.value = 0
  editorLoadedOriginalCount.value = 0
  editorDirty.value = false
  const selected = datasets.value.find(ds => Number(ds.id) === Number(editorDsId.value))
  editorOutputName.value = `${selected?.name || '数据集'}(编辑副本)`
  try {
    const r = await get(`/datasets/${editorDsId.value}/rows?offset=0&limit=100`)
    editorCols.value = r.columns || []
    editorRows.value = normalizeEditorRows(r.rows || [], editorCols.value, 0)
    editorTotal.value = r.total || 0
    editorLoadedOriginalCount.value = (r.rows || []).length
  } catch(e:any) { ElMessage.error(e?.message||'加载失败') }
  finally { editorLoading.value = false }
}

async function loadMoreEditor() {
  if (editorLoadedOriginalCount.value >= editorTotal.value) return
  const offset = editorLoadedOriginalCount.value
  try {
    const r = await get(`/datasets/${editorDsId.value}/rows?offset=${offset}&limit=100`)
    const rows = normalizeEditorRows(r.rows || [], editorCols.value, offset)
    const firstAdded = editorRows.value.findIndex(row => row[SOURCE_ROW_INDEX] == null)
    if (firstAdded >= 0) editorRows.value.splice(firstAdded, 0, ...rows)
    else editorRows.value.push(...rows)
    editorLoadedOriginalCount.value += rows.length
  } catch(e:any) { ElMessage.error(e?.message||'加载失败') }
}

async function materializeEditorRows() {
  if (editorTotal.value > 100000) {
    throw new Error('在线编辑最多支持 100000 行，请先筛选数据或使用离线工具')
  }
  const rows = [...editorRows.value]
  let offset = editorLoadedOriginalCount.value
  while (offset < editorTotal.value) {
    const limit = Math.min(5000, editorTotal.value - offset)
    const response = await get(`/datasets/${editorDsId.value}/rows?offset=${offset}&limit=${limit}`)
    const page = normalizeEditorRows(response.rows || [], editorCols.value, offset)
    if (!page.length) throw new Error(`读取源数据在第 ${offset + 1} 行中断，已取消导出以防止数据缺失`)
    rows.push(...page)
    offset += page.length
  }

  const originals = rows
    .filter(row => Number.isInteger(row[SOURCE_ROW_INDEX]))
    .sort((left, right) => left[SOURCE_ROW_INDEX] - right[SOURCE_ROW_INDEX])
  const additions = rows.filter(row => !Number.isInteger(row[SOURCE_ROW_INDEX]))
  return [...originals, ...additions].map(row =>
    Object.fromEntries(editorCols.value.map(column => [column, row[column] ?? '']))
  )
}

async function saveEditorCopy() {
  if (!canExportEditor.value) return
  saveLoading.value = true
  try {
    const rows = await materializeEditorRows()
    const result = await post(`/datasets/${editorDsId.value}/edited-copy`, {
      name: editorOutputName.value.trim(),
      columns: editorCols.value,
      rows,
    })
    editorDirty.value = false
    await loadDatasets()
    ElMessage.success(`已生成新数据集 #${result.dataset_id}「${result.name}」，原数据集未修改`)
  } catch(e:any) { ElMessage.error(e?.message||'保存失败') }
  finally { saveLoading.value = false }
}

function csvCell(value: any) {
  if (value == null) return ''
  const text = String(value)
  return /[",\r\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text
}

async function downloadEditorCsv() {
  if (!canExportEditor.value) return
  downloadLoading.value = true
  try {
    const rows = await materializeEditorRows()
    const lines = [
      editorCols.value.map(csvCell).join(','),
      ...rows.map(row => editorCols.value.map(column => csvCell(row[column])).join(','))
    ]
    const blob = new Blob(['\uFEFF' + lines.join('\r\n')], { type: 'text/csv;charset=utf-8' })
    const url = URL.createObjectURL(blob)
    const anchor = document.createElement('a')
    const safeName = editorOutputName.value.trim().replace(/[\\/:*?"<>|]/g, '_') || '编辑结果'
    anchor.href = url
    anchor.download = safeName.endsWith('.csv') ? safeName : safeName + '.csv'
    document.body.appendChild(anchor)
    anchor.click()
    anchor.remove()
    URL.revokeObjectURL(url)
    editorDirty.value = false
    ElMessage.success(`已下载 ${rows.length} 行编辑结果，原数据集未修改`)
  } catch(e:any) { ElMessage.error(e?.message || '下载失败') }
  finally { downloadLoading.value = false }
}

function addRow() {
  const newRow: any = {}
  editorCols.value.forEach(c => newRow[c] = '')
  Object.defineProperty(newRow, SOURCE_ROW_INDEX, { value: null, enumerable: false })
  editorRows.value.push(newRow)
  editorDirty.value = true
}

function addColumn() {
  const name = prompt('新列名:')?.trim()
  if (!name) return
  if (editorCols.value.includes(name)) {
    ElMessage.warning(`列“${name}”已经存在`)
    return
  }
  editorCols.value.push(name)
  editorRows.value.forEach(r => r[name] = '')
  editorDirty.value = true
}

function removeRow(idx: number) {
  editorRows.value.splice(idx, 1)
  editorDirty.value = true
}

// ═══════════ Tab 3: 数据对齐 ═══════════
const alignSrcId = ref(''), alignTgtId = ref(''), alignSrcCol = ref(''), alignTgtCol = ref('')
const alignMethod = ref('nearest'), alignFreq = ref(''), alignTolerance = ref('5min'), alignLoading = ref(false)
const alignResult = ref<any>(null), alignColumns = ref<string[]>([])
const srcTimeColumns = ref<string[]>([]), tgtTimeColumns = ref<string[]>([])
const alignOutputName = ref('')
const alignSaving = ref(false)
const savedAlignId = ref<number|null>(null)
const alignReady = computed(() =>
  Boolean(alignSrcId.value)
  && Boolean(alignTgtId.value)
  && Number(alignSrcId.value) !== Number(alignTgtId.value)
  && Boolean(alignSrcCol.value)
  && Boolean(alignTgtCol.value)
  && srcTimeColumns.value.includes(alignSrcCol.value)
  && tgtTimeColumns.value.includes(alignTgtCol.value)
)

function detectTimeColumns(columns: string[], rows: any[]) {
  return columns.filter((column, columnIndex) => {
    if (/(^|[_\-])(time|date|timestamp)($|[_\-])|时间|日期|时刻/i.test(column)) return true
    const values = rows
      .map(row => Array.isArray(row) ? row[columnIndex] : row?.[column])
      .filter(value => value !== null && value !== undefined && String(value).trim() !== '')
    if (!values.length) return false
    const numeric = values.every(value => Number.isFinite(Number(value)))
    if (numeric) return false
    const validDates = values.filter(value => !Number.isNaN(Date.parse(String(value)))).length
    return validDates / values.length >= 0.8
  })
}

function updateAlignmentOutputName() {
  const source = datasets.value.find(ds => Number(ds.id) === Number(alignSrcId.value))
  const target = datasets.value.find(ds => Number(ds.id) === Number(alignTgtId.value))
  alignOutputName.value = source && target ? `对齐结果(${source.name}+${target.name})` : ''
}

watch(alignSrcId, async (id) => {
  alignSrcCol.value = ''
  srcTimeColumns.value = []
  if (id && Number(id) === Number(alignTgtId.value)) alignTgtId.value = ''
  updateAlignmentOutputName()
  if (!id) return
  try {
    const r = await get(`/datasets/${id}/rows?offset=0&limit=20`)
    srcTimeColumns.value = detectTimeColumns(r.columns || [], r.rows || [])
    if (srcTimeColumns.value.length === 1) alignSrcCol.value = srcTimeColumns.value[0]
  } catch(e:any) { ElMessage.error(e?.message || '读取源数据时间列失败') }
})
watch(alignTgtId, async (id) => {
  alignTgtCol.value = ''
  tgtTimeColumns.value = []
  if (id && Number(id) === Number(alignSrcId.value)) alignSrcId.value = ''
  updateAlignmentOutputName()
  if (!id) return
  try {
    const r = await get(`/datasets/${id}/rows?offset=0&limit=20`)
    tgtTimeColumns.value = detectTimeColumns(r.columns || [], r.rows || [])
    if (tgtTimeColumns.value.length === 1) alignTgtCol.value = tgtTimeColumns.value[0]
  } catch(e:any) { ElMessage.error(e?.message || '读取目标数据时间列失败') }
})

watch(
  [alignSrcId, alignTgtId, alignSrcCol, alignTgtCol, alignMethod, alignFreq, alignTolerance],
  () => {
    alignResult.value = null
    alignColumns.value = []
    savedAlignId.value = null
  }
)

const alignmentPayload = (includeName = false) => ({
  source_id: Number(alignSrcId.value),
  target_id: Number(alignTgtId.value),
  source_time_col: alignSrcCol.value,
  target_time_col: alignTgtCol.value,
  method: alignMethod.value,
  freq: alignFreq.value || null,
  tolerance: alignTolerance.value,
  ...(includeName ? { output_name: alignOutputName.value.trim() } : {}),
})

const formatMatchRate = (value: any) => `${(Number(value || 0) * 100).toFixed(1)}%`
const formatSeconds = (value: any) => {
  if (value == null) return '—'
  const seconds = Number(value)
  if (seconds < 60) return `${seconds.toFixed(1)}秒`
  if (seconds < 3600) return `${(seconds / 60).toFixed(1)}分钟`
  return `${(seconds / 3600).toFixed(1)}小时`
}

async function doAlign() {
  if (!alignReady.value) return
  alignLoading.value = true; alignResult.value = null; savedAlignId.value = null
  try {
    const r = await post('/datasets/align', alignmentPayload())
    alignResult.value = r
    alignColumns.value = r.columns || []
    ElMessage.success(`对齐完成：${r.fully_matched_rows}/${r.aligned_rows} 行完整匹配`)
  } catch(e:any) { ElMessage.error(e?.message||'对齐失败') }
  finally { alignLoading.value = false }
}

async function saveAlign() {
  if (!alignResult.value || !alignOutputName.value.trim()) return
  alignSaving.value = true
  try {
    const r = await post('/datasets/align/save', alignmentPayload(true))
    savedAlignId.value = r.dataset_id
    await loadDatasets()
    ElMessage.success(`已保存为新数据集 #${r.dataset_id}「${r.name}」（${r.rows} 行），两份源数据均未修改`)
  } catch(e:any) { ElMessage.error(e?.message||'保存失败') }
  finally { alignSaving.value = false }
}
async function downloadAligned() {
  if (!savedAlignId.value) return
  try {
    const filename = await downloadFile(
      `/datasets/${savedAlignId.value}/download`,
      `${alignOutputName.value || '对齐结果'}.csv`
    )
    ElMessage.success(`已下载 ${filename}`)
  } catch(e:any) { ElMessage.error(e?.message || '下载失败') }
}
</script>

<style scoped>
.dw { padding: 24px; color: var(--tx); }
.card-hd { display: flex; justify-content: space-between; align-items: center; }
.profile-result { margin-top: 16px; }
.overview-summary { margin-bottom: 16px; }
.overview-card { margin-bottom: 16px; }
.field-overview-hint { margin-bottom: 8px; color: var(--el-text-color-secondary); font-size: 12px; }
.missing-empty { padding: 10px 16px; color: var(--el-text-color-secondary); font-size: 13px; }
.missing-records { padding: 10px 16px 4px; background: var(--el-fill-color-lighter); }
.missing-records-title { margin-bottom: 10px; font-size: 13px; color: var(--el-text-color-regular); }
.missing-pagination { margin-top: 10px; justify-content: flex-end; }
.next-step-card :deep(.el-card__body) { display: flex; flex-wrap: wrap; gap: 10px; }
.editor-safety-alert { margin-bottom: 12px; }
.editor-actions {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
  margin-bottom: 12px;
}
.editor-table-wrap { overflow-x: auto; }
.alignment-ai-alert {
  margin-top: 12px;
}
.alignment-checks {
  margin-top: 12px;
}
.alignment-checks-title {
  margin-bottom: 8px;
  font-size: 14px;
  font-weight: 600;
  color: var(--el-text-color-primary);
}
.alignment-ai-alert :deep(.el-alert__title),
.alignment-ai-alert :deep(.el-alert__description) {
  line-height: 1.7;
}
.align-save-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 12px;
}
.align-preview-note {
  margin-top: 8px;
  color: var(--tx2);
  font-size: 12px;
}
</style>
