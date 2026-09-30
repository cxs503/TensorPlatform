<template>
  <div class="page-root"><div class="page-header"><h2>🖥️ GPU 资源调度</h2></div>
  <el-row :gutter="16" class="stats-row">
    <el-col :span="6"><el-card><el-statistic title="GPU总数" :value="stats.total_gpus"/></el-card></el-col>
    <el-col :span="6"><el-card><el-statistic title="可用" :value="stats.available_gpus"/></el-card></el-col>
    <el-col :span="6"><el-card><el-statistic title="占用" :value="stats.occupied_gpus"/></el-card></el-col>
    <el-col :span="6"><el-card><el-statistic title="活跃任务" :value="stats.active_jobs"/></el-card></el-col>
  </el-row>
  <el-alert v-if="!loading && !stats.hardware_detected" :title="stats.unavailable_reason || error || '未检测到GPU设备'" type="warning" :closable="false" show-icon class="gpu-alert" />
  <el-card header="GPU 节点"><el-table :data="gpus" stripe v-loading="loading" empty-text="当前未检测到真实GPU设备">
    <el-table-column prop="name" label="节点" width="160"/><el-table-column label="显存(已用/总)" width="180"><template #default="{row}">{{row.used_memory_mb}} / {{row.total_memory_mb}} MB</template></el-table-column>
    <el-table-column label="利用率" width="180"><template #default="{row}"><el-progress :percentage="Math.round(row.utilization_percent||0)" :color="(row.utilization_percent||0)>80?'#f56c6c':'#67c23a'"/></template></el-table-column>
    <el-table-column label="温度" width="100"><template #default="{row}">{{row.temperature_celsius}}°C</template></el-table-column>
    <el-table-column prop="status" label="状态" width="110"><template #default="{row}"><el-tag :type="row.status==='available'?'success':'warning'" size="small">{{row.status}}</el-tag></template></el-table-column>
    <el-table-column prop="device_index" label="设备号" width="90"/>
  </el-table></el-card>
  </div>
</template>
<script setup lang="ts">
import { ref, onMounted } from 'vue'; import { get } from '@/api/legacy';
const gpus=ref<any[]>([]),stats=ref<any>({total_gpus:0,available_gpus:0,occupied_gpus:0,active_jobs:0,hardware_detected:false,unavailable_reason:''});
const loading=ref(false),error=ref('');
onMounted(async()=>{loading.value=true;error.value='';try{const [g,s]=await Promise.all([get('/gpu/gpus'),get('/gpu/stats')]);gpus.value=Array.isArray(g)?g:(g?.gpus||[]);stats.value={...stats.value,...(s||{})}}catch(e:any){gpus.value=[];error.value=e?.message||'GPU探测接口不可用';stats.value={...stats.value,hardware_detected:false,unavailable_reason:error.value}}finally{loading.value=false}});
</script>
<style scoped>.page-root{padding:24px}.page-header{display:flex;justify-content:space-between;align-items:center;margin-bottom:20px}.page-header h2{margin:0;font-size:20px}.stats-row{margin-bottom:20px}.gpu-alert{margin-bottom:16px}</style>
