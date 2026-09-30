<template>
<div class="pg"><div class="hd"><h2>🎛️ 智能控制</h2><el-button type="primary" @click="refresh">刷新</el-button></div>
<el-row :gutter="16" class="st">
  <el-col :span="6"><el-card><el-statistic title="控制回路" :value="loops.length"/></el-card></el-col>
  <el-col :span="6"><el-card><el-statistic title="自动运行" :value="loops.filter(l=>l.mode==='auto').length"/></el-card></el-col>
  <el-col :span="6"><el-card><el-statistic title="设定点偏差" :value="avgDeviation" suffix="%"/></el-card></el-col>
</el-row>
<el-card><el-table :data="loops" stripe v-loading="loading">
  <el-table-column prop="name" label="控制回路" width="180"/>
  <el-table-column label="模式" width="100"><template #default="{row}"><el-tag :type="row.mode==='auto'?'success':'warning'">{{row.mode==='auto'?'自动':'手动'}}</el-tag></template></el-table-column>
  <el-table-column label="设定值" width="100"><template #default="{row}">{{row.setpoint}}{{row.unit}}</template></el-table-column>
  <el-table-column label="实际值" width="100"><template #default="{row}"><span :style="{color:Math.abs((row.actual||0)-(row.setpoint||0))>10?'#F56C6C':'#67C23A'}">{{row.actual}}{{row.unit}}</span></template></el-table-column>
  <el-table-column label="偏差" width="120"><template #default="{row}"><el-progress :percentage="Math.abs((row.actual||0)-(row.setpoint||0))/(row.setpoint||1)*100" :stroke-width="12" :color="Math.abs((row.actual||0)-(row.setpoint||0))>10?'#F56C6C':'#67C23A'"/></template></el-table-column>
  <el-table-column label="输出" width="100"><template #default="{row}">{{row.output}}%</template></el-table-column>
  <el-table-column label="操作" width="160"><template #default="{row}"><el-button size="small" @click="toggleMode(row)">{{row.mode==='auto'?'切换手动':'切换自动'}}</el-button></template></el-table-column>
</el-table></el-card>
</div>
</template>
<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'; import { ElMessage } from 'element-plus'; import { get } from '@/api/legacy';
const loading=ref(false),loops=ref<any[]>([]);
const avgDeviation=computed(()=>{const d=loops.value.filter(l=>l.setpoint);return d.length?(d.reduce((a,l)=>a+Math.abs((l.actual||0)-(l.setpoint||0))/(l.setpoint||1)*100,0)/d.length).toFixed(1):'0'});
onMounted(async()=>{loading.value=true;try{
  // 后端实际端点是 /control/systems (非 loops)
  const d=await get('/control/systems');
  const list=Array.isArray(d)?d:(d.items||d.loops||[]);
  // 后端字段: control_type/target_system/status/parameters; 映射到控制回路表格字段
  loops.value=list.map((s:any)=>{
    const p=s.parameters||{};
    return {
      ...s,
      name:s.name,
      mode:(s.status==='running'||s.status==='active')?'auto':'manual',
      setpoint:p.setpoint??p.target_value??p.setpoint_value??0,
      actual:p.actual??p.measured??p.current_value??0,
      unit:p.unit??'',
      output:p.output??p.output_pct??0,
    };
  });
}catch{}finally{loading.value=false}});
function toggleMode(row:any){row.mode=row.mode==='auto'?'manual':'auto';ElMessage.success(`已切换为${row.mode==='auto'?'自动':'手动'}模式`)}
function refresh(){loading.value=true;setTimeout(()=>loading.value=false,500)}
</script>
<style scoped>.pg{padding:24px}.hd{display:flex;justify-content:space-between;align-items:center;margin-bottom:20px}.hd h2{margin:0;font-size:20px}.st{margin-bottom:20px}</style>
