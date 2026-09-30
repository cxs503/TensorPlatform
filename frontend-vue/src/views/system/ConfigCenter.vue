<template>
<div class="pg"><div class="hd"><h2>⚙️ 配置中心</h2><el-button @click="fetchConfigs">刷新</el-button></div>
<el-alert title="配置中心目前尚未接入数据库持久化，已关闭刷新后会丢失的新增和编辑入口。" type="warning" :closable="false" show-icon class="notice" />
<el-row :gutter="16" class="st">
  <el-col :span="6"><el-card><el-statistic title="配置项" :value="configs.length"/></el-card></el-col>
  <el-col :span="6"><el-card><el-statistic title="作用域" :value="envCount"/></el-card></el-col>
</el-row>
<el-card><el-table :data="configs" stripe v-loading="loading">
  <el-table-column prop="name" label="配置名" width="220"/><el-table-column prop="application" label="应用" width="140"><template #default="{row}">{{row.application||'-'}}</template></el-table-column>
  <el-table-column label="作用域" width="110"><template #default="{row}"><el-tag size="small">{{row.scope}}</el-tag></template></el-table-column>
  <el-table-column label="类型" width="110"><template #default="{row}"><el-tag type="info" size="small">{{row.config_type}}</el-tag></template></el-table-column>
  <el-table-column label="状态" width="100"><template #default="{row}"><el-tag :type="row.status==='released'?'success':'info'" size="small">{{row.status}}</el-tag></template></el-table-column>
  <el-table-column prop="version" label="版本" width="80"/><el-table-column prop="description" label="描述" show-overflow-tooltip/><el-table-column prop="updated_at" label="更新时间" width="180"/>
  <el-table-column label="操作" width="100"><template #default><el-button size="small" disabled>未接入</el-button></template></el-table-column>
</el-table></el-card>
</div>
</template>
<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'; import { ElMessage } from 'element-plus'; import { get } from '@/api/legacy';
const loading=ref(false),configs=ref<any[]>([]);
const envCount=computed(()=>new Set(configs.value.map((c:any)=>c.scope)).size);
onMounted(()=>fetchConfigs());
async function fetchConfigs(){loading.value=true;try{const d=await get('/config/configs');configs.value=Array.isArray(d)?d:(d.items||[])}catch(e:any){configs.value=[];ElMessage.error(e?.message||'配置加载失败')}finally{loading.value=false}}
</script>
<style scoped>.pg{padding:24px}.hd{display:flex;justify-content:space-between;align-items:center;margin-bottom:20px}.hd h2{margin:0;font-size:20px}.notice,.st{margin-bottom:20px}</style>
