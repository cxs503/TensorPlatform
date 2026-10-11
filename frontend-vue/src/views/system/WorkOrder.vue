<template>
<div class="pg"><div class="hd"><h2>📝 工单管理</h2></div>
<el-alert title="示例工单已移除；数据库持久化尚未接入，因此当前不开放工单创建和状态操作。" type="warning" :closable="false" show-icon class="notice" />
<el-row :gutter="16" class="st"><el-col :span="6"><el-card><el-statistic title="总工单" :value="orders.length"/></el-card></el-col><el-col :span="6"><el-card><el-statistic title="处理中" :value="orders.filter(o=>o.status==='in_progress').length"/></el-card></el-col><el-col :span="6"><el-card><el-statistic title="已完成" :value="orders.filter(o=>o.status==='done').length"/></el-card></el-col><el-col :span="6"><el-card><el-statistic title="紧急" :value="orders.filter(o=>o.priority==='urgent').length"/></el-card></el-col></el-row>
<el-card><el-table :data="orders" stripe v-loading="loading">
  <el-table-column prop="title" label="工单标题" min-width="180"/><el-table-column prop="type" label="类型" width="100"><template #default="{row}"><el-tag size="small">{{row.type}}</el-tag></template></el-table-column>
  <el-table-column prop="priority" label="优先级" width="90"><template #default="{row}"><el-tag :type="row.priority==='urgent'?'danger':row.priority==='high'?'warning':'info'" size="small">{{row.priority}}</el-tag></template></el-table-column>
  <el-table-column prop="assigned_to" label="处理人" width="100"/><el-table-column prop="status" label="状态" width="100"><template #default="{row}"><el-tag :type="row.status==='completed'?'success':row.status==='in_progress'?'warning':'info'" size="small">{{row.status}}</el-tag></template></el-table-column>
  <el-table-column prop="created_at" label="创建时间" width="160"/>
</el-table></el-card></div>
</template>
<script setup lang="ts">
import { ref, onMounted } from 'vue'; import { ElMessage } from 'element-plus'; import { get } from '@/api/legacy';
const loading=ref(false),orders=ref<any[]>([]);
onMounted(async()=>{loading.value=true;try{const d=await get('/work-orders');orders.value=Array.isArray(d)?d:(d.items||[])}catch(e:any){orders.value=[];ElMessage.error(e?.message||'工单列表加载失败')}finally{loading.value=false}});
</script>
<style scoped>.pg{padding:24px}.hd{display:flex;justify-content:space-between;align-items:center;margin-bottom:20px}.hd h2{margin:0;font-size:20px}.notice,.st{margin-bottom:20px}</style>
