<template>
  <div class="page-root"><div class="page-header"><h2>✅ 合规管理</h2></div>
  <el-alert title="当前仅提供合规检查项目录，尚未接入自动核验器；平台不会生成模拟的通过或未通过结论。" type="warning" :closable="false" show-icon class="notice" />
  <el-row :gutter="16" class="stats-row">
    <el-col :span="6"><el-card><el-statistic title="合规检查项" :value="checks.length"/></el-card></el-col>
    <el-col :span="6"><el-card><el-statistic title="通过" :value="checks.filter(c=>c.status==='pass').length"/></el-card></el-col>
    <el-col :span="6"><el-card><el-statistic title="未通过" :value="checks.filter(c=>c.status==='fail').length"/></el-card></el-col>
    <el-col :span="6"><el-card><el-statistic v-if="evaluatedChecks.length" title="合规率" :value="complianceRate" :precision="1"><template #suffix>%</template></el-statistic><div v-else class="unavailable-stat"><span>合规率</span><strong>--</strong></div></el-card></el-col>
  </el-row>
  <el-card><el-table :data="checks" stripe v-loading="loading">
    <el-table-column prop="name" label="检查项" width="180"/><el-table-column prop="category" label="分类" width="100"><template #default="{row}"><el-tag size="small">{{row.category}}</el-tag></template></el-table-column>
    <el-table-column prop="standard" label="标准" width="120"/><el-table-column prop="status" label="状态" width="110"><template #default="{row}"><el-tag :type="statusType(row.status)" size="small">{{statusLabel(row.status)}}</el-tag></template></el-table-column>
    <el-table-column label="详情" min-width="220" show-overflow-tooltip><template #default="{row}">{{row.details || row.description}}</template></el-table-column><el-table-column prop="last_checked" label="检查时间" width="180"><template #default="{row}">{{row.last_checked||'未检查'}}</template></el-table-column>
    <el-table-column label="操作" width="100"><template #default><el-button size="small" disabled>未接入</el-button></template></el-table-column>
  </el-table></el-card>
  </div>
</template>
<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'; import { ElMessage } from 'element-plus'; import { get } from '@/api/legacy';
const loading=ref(false),checks=ref<any[]>([]);
const evaluatedChecks=computed(()=>checks.value.filter(c=>['pass','fail','warning'].includes(c.status)));
const complianceRate=computed(()=>{const p=evaluatedChecks.value.filter(c=>c.status==='pass').length;return evaluatedChecks.value.length?p/evaluatedChecks.value.length*100:0});
onMounted(()=>{fetchChecks()});
async function fetchChecks(){loading.value=true;try{const d=await get('/compliance/checks');checks.value=Array.isArray(d)?d:(d.items||[])}catch(e:any){checks.value=[];ElMessage.error(e?.message||'合规检查项加载失败')}finally{loading.value=false}}
function statusType(s:string){return s==='pass'?'success':s==='fail'?'danger':s==='warning'?'warning':'info'}
function statusLabel(s:string){return s==='pass'?'通过':s==='fail'?'未通过':s==='warning'?'警告':s==='not_applicable'?'未接入核验':'未检查'}
</script>
<style scoped>.page-root{padding:24px}.page-header{display:flex;justify-content:space-between;align-items:center;margin-bottom:20px}.page-header h2{margin:0;font-size:20px}.notice,.stats-row{margin-bottom:20px}.unavailable-stat{display:flex;flex-direction:column;gap:8px;color:#909399}.unavailable-stat strong{font-size:24px;color:#303133}</style>
