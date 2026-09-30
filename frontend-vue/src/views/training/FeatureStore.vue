<template>
  <div class="fs-page">
    <PageHeader title="特征存储" desc="特征组注册 · 在线/离线特征 · 特征检索" />

    <el-row :gutter="16" class="stats">
      <el-col :xs="8" :sm="6"><el-card shadow="hover"><div class="sv">{{ groups.length }}</div><div class="sl">特征组</div></el-card></el-col>
      <el-col :xs="8" :sm="6"><el-card shadow="hover"><div class="sv ok">{{ totalFeatures }}</div><div class="sl">特征总数</div></el-card></el-col>
      <el-col :xs="8" :sm="6"><el-card shadow="hover"><div class="sv">{{ onlineCount }}</div><div class="sl">在线特征</div></el-card></el-col>
      <el-col :xs="8" :sm="6"><el-card shadow="hover"><div class="sv">Feast</div><div class="sl">引擎</div></el-card></el-col>
    </el-row>

    <el-row :gutter="16">
      <el-col :span="10">
        <el-card shadow="never" class="section">
          <template #header><div style="display:flex;justify-content:space-between"><span>特征组</span><el-button size="small" type="primary" @click="showCreate=true">注册</el-button></div></template>
          <div class="group-list">
            <div v-for="g in groups" :key="g.id" class="group-item" :class="{active:selectedGroup?.id===g.id}" @click="selectGroup(g)">
              <div class="gname">{{ g.name }}</div>
              <div class="gmeta">{{ g.features?.length||0 }} 特征 · {{ g.entity_name }}</div>
            </div>
          </div>
          <el-empty v-if="!groups.length" description="暂无特征组" :image-size="60" />
        </el-card>
      </el-col>

      <el-col :span="14">
        <el-card v-if="selectedGroup" shadow="never" class="section">
          <template #header><span>{{ selectedGroup.name }}</span></template>
          <el-descriptions :column="2" border size="small">
            <el-descriptions-item label="实体">{{ selectedGroup.entity_name }}</el-descriptions-item>
            <el-descriptions-item label="特征数">{{ selectedGroup.features?.length||0 }}</el-descriptions-item>
            <el-descriptions-item label="描述" :span="2">{{ selectedGroup.description||'-' }}</el-descriptions-item>
          </el-descriptions>
          <el-divider />
          <h4>特征列表</h4>
          <el-table :data="selectedGroup.features||[]" size="small" stripe max-height="300">
            <el-table-column prop="name" label="特征名" width="180" />
            <el-table-column prop="dtype" label="类型" width="100" />
            <el-table-column prop="description" label="说明" min-width="200" />
          </el-table>
        </el-card>
        <el-card v-else shadow="never" class="section">
          <el-empty description="选择或注册一个特征组" :image-size="80" />
        </el-card>
      </el-col>
    </el-row>

    <!-- 注册弹窗 -->
    <el-dialog v-model="showCreate" title="注册特征组" width="550px">
      <el-form :model="createForm" label-width="80px">
        <el-form-item label="名称"><el-input v-model="createForm.name" placeholder="user_features" /></el-form-item>
        <el-form-item label="实体"><el-input v-model="createForm.entity_name" placeholder="default" /></el-form-item>
        <el-form-item label="描述"><el-input v-model="createForm.description" type="textarea" :rows="2" /></el-form-item>
        <el-divider>特征定义</el-divider>
        <div v-for="(f,i) in createForm.features" :key="i" style="display:flex;gap:8px;margin-bottom:8px;align-items:center">
          <el-input v-model="f.name" placeholder="特征名" size="small" style="width:150px" />
          <el-select v-model="f.dtype" size="small" style="width:120px">
            <el-option label="float32" value="float32" /><el-option label="int64" value="int64" /><el-option label="string" value="string" />
          </el-select>
          <el-input v-model="f.description" placeholder="说明" size="small" style="flex:1" />
          <el-button type="danger" size="small" circle @click="createForm.features.splice(i,1)">✕</el-button>
        </div>
        <el-button size="small" @click="createForm.features.push({name:'',dtype:'float32',description:''})">+ 添加特征</el-button>
      </el-form>
      <template #footer><el-button @click="showCreate=false">取消</el-button><el-button type="primary" @click="createGroup" :loading="creating">注册</el-button></template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, computed, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { get, post } from '@/api/legacy'
import PageHeader from '@/components/PageHeader.vue'

const groups = ref<any[]>([])
const selectedGroup = ref<any>(null)
const showCreate = ref(false)
const creating = ref(false)
const createForm = reactive({ name: '', entity_name: 'default', description: '', features: [{ name: '', dtype: 'float32', description: '' }] })

const totalFeatures = computed(() => groups.value.reduce((s, g) => s + (g.features?.length || 0), 0))
const onlineCount = computed(() => groups.value.filter(g => g.online).length)

async function loadGroups() {
  try { const r: any = await get('/feature-store/feature-groups').then(r => Array.isArray(r) ? {items: r, total: r.length} : r); groups.value = r.items || r || [] }
  catch { /* */ }
}

function selectGroup(g: any) { selectedGroup.value = g }

async function createGroup() {
  creating.value = true
  try {
    const features = createForm.features.filter(f => f.name.trim())
    await post('/feature-store/feature-groups', { ...createForm, features })
    ElMessage.success('特征组注册成功')
    showCreate.value = false
    createForm.features = [{ name: '', dtype: 'float32', description: '' }]
    createForm.name = ''; createForm.description = ''
    loadGroups()
  } catch { ElMessage.error('注册失败') }
  finally { creating.value = false }
}

onMounted(loadGroups)
</script>

<style scoped>
.fs-page { max-width: 1200px; padding: 20px; }
.stats { margin-bottom: 16px; }
.sv { font-size: 28px; font-weight: 700; text-align: center; }
.sv.ok { color: #67C23A; }
.sl { font-size: 12px; color: #909399; margin-top: 4px; text-align: center; }
.section { margin-bottom: 16px; }
.group-list { max-height: 400px; overflow-y: auto; }
.group-item { padding: 12px; border-bottom: 1px solid #ebeef5; cursor: pointer; }
.group-item:hover,.group-item.active { background: #ecf5ff; }
.gname { font-weight: 600; font-size: 14px; }
.gmeta { font-size: 12px; color: #909399; margin-top: 4px; }
h4 { margin: 8px 0; }
</style>
