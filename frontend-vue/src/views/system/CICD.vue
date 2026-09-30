<template>
  <div class="page-root"><div class="page-header"><h2>🚀 CI/CD 流水线</h2></div>
  <el-alert title="CI/CD Runner尚未配置，模拟执行已关闭。接入Runner后才会开放创建和运行。" type="warning" :closable="false" show-icon class="notice" />
  <el-row :gutter="16" class="stats-row">
    <el-col :span="6"><el-card><el-statistic title="流水线" :value="pipelines.length"/></el-card></el-col>
    <el-col :span="6"><el-card><el-statistic title="运行中" :value="pipelines.filter(p=>p.status==='running').length"/></el-card></el-col>
    <el-col :span="6"><el-card><el-statistic title="成功率" :value="successRate" :precision="1"><template #suffix>%</template></el-statistic></el-card></el-col>
  </el-row>
  <el-card><el-table :data="pipelines" stripe v-loading="loading">
    <el-table-column prop="name" label="流水线名" width="180"/><el-table-column prop="project_id" label="项目" width="140"/>
    <el-table-column label="阶段" min-width="180"><template #default="{row}">{{ (row.stages||[]).map((s:any)=>s.name).join(' → ') || '-' }}</template></el-table-column>
    <el-table-column prop="status" label="状态" width="120"><template #default="{row}"><el-tag :type="statusType(row.status)" size="small">{{row.status}}</el-tag></template></el-table-column>
    <el-table-column label="上次运行" width="180"><template #default="{row}">{{ row.last_run?.status || row.last_run || '未运行' }}</template></el-table-column>
    <el-table-column prop="updated_at" label="更新时间" width="180"/>
    <el-table-column label="操作" width="220"><template #default="{row}">
      <el-button size="small" type="primary" disabled>Runner未接入</el-button><el-button size="small" type="danger" @click="delPipeline(row.id)">删除</el-button>
    </template></el-table-column>
  </el-table></el-card>
  </div>
</template>
<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'; import { ElMessage } from 'element-plus'; import { get, del } from '@/api/legacy';
const loading=ref(false),pipelines=ref<any[]>([]);
const successRate=computed(()=>{const s=pipelines.value.filter(p=>p.status==='success').length;return pipelines.value.length?s/pipelines.value.length*100:0});
function statusType(s:string){return s==='success'?'success':s==='failed'?'danger':s==='running'?'warning':'info'}
onMounted(()=>{fetchPipelines()});
async function fetchPipelines(){loading.value=true;try{const d=await get('/cicd');pipelines.value=Array.isArray(d)?d:(d.items||[])}catch(e:any){pipelines.value=[];ElMessage.error(e?.message||'流水线加载失败')}finally{loading.value=false}}
async function delPipeline(id:string){try{await del(`/cicd/${id}`);ElMessage.success('已删除');fetchPipelines()}catch(e:any){ElMessage.error(e?.message||'失败')}}
</script>
<style scoped>.page-root{padding:24px}.page-header{display:flex;justify-content:space-between;align-items:center;margin-bottom:20px}.page-header h2{margin:0;font-size:20px}.notice,.stats-row{margin-bottom:20px}</style>
