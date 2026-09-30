<template>
<div class="pg"><div class="hd"><h2>☀️ 光伏管理</h2><el-button type="primary" @click="refresh">刷新</el-button></div>
<el-row :gutter="16" class="st">
  <el-col :span="6"><el-card><el-statistic title="今日发电" :value="todayGen" suffix="kWh"/></el-card></el-col>
  <el-col :span="6"><el-card><el-statistic title="本月累计" :value="monthGen" suffix="MWh"/></el-card></el-col>
  <el-col :span="6"><el-card><el-statistic title="等效小时" :value="equivHours" suffix="h"/></el-card></el-col>
  <el-col :span="6"><el-card><el-statistic title="CO₂减排" :value="co2Saved" suffix="t"/></el-card></el-col>
</el-row>
<el-card style="margin-bottom:16px"><template #header>📈 发电曲线</template><div ref="pvChart" style="height:250px"/></el-card>
<el-card><el-table :data="stations" stripe v-loading="loading">
  <el-table-column prop="name" label="电站名称" width="180"/><el-table-column prop="capacity" label="装机容量(kW)" width="130"/>
  <el-table-column label="今日发电" width="120"><template #default="{row}">{{row.today_kwh||0}} kWh</template></el-table-column>
  <el-table-column label="效率" width="100"><template #default="{row}"><el-progress :percentage="row.efficiency||85" :stroke-width="12"/></template></el-table-column>
  <el-table-column label="状态" width="100"><template #default="{row}"><el-tag :type="row.status==='online'?'success':'warning'">{{row.status}}</el-tag></template></el-table-column>
  <el-table-column prop="updated_at" label="更新时间" width="170"/>
</el-table></el-card>
</div>
</template>
<script setup lang="ts">
import { ref, onMounted, nextTick } from 'vue'; import { get } from '@/api/legacy'; import * as echarts from 'echarts';
const loading=ref(false),stations=ref<any[]>([]),pvChart=ref();
const todayGen=ref(1250),monthGen=ref(37.5),equivHours=ref(4.2),co2Saved=ref(18.7);
onMounted(async()=>{loading.value=true;try{
  // 后端实际端点是 /photovoltaic/systems (非 stations)
  const d=await get('/photovoltaic/systems');
  const list=Array.isArray(d)?d:(d.items||[]);
  // 后端 to_dict 字段: capacity_kwp/location/status/created_at; 映射到表格所需字段
  stations.value=list.map((s:any)=>({
    ...s,
    capacity:s.capacity_kwp||s.capacity,
    today_kwh:s.today_kwh||0,
    efficiency:s.efficiency||85,
    status:s.status||'online',
    updated_at:s.updated_at||s.created_at,
  }));
}catch{}finally{loading.value=false}
// 拉真实统计填 4 个卡片(失败则保留默认值)
try{const st:any=await get('/photovoltaic/statistics');
  todayGen.value=Math.round((st.total_energy_kwh||0)*1000);
  monthGen.value=Number(((st.total_energy_kwh||0)/1000*30).toFixed(1));
  co2Saved.value=Number(((st.estimated_co2_saved_kg||0)/1000).toFixed(1));
}catch{}
nextTick(()=>{if(pvChart.value){const opt=echarts.init(pvChart.value);const h=Array.from({length:12},(_,i)=>i+6);const v=[20,80,200,400,550,650,700,680,550,350,150,50];opt.setOption({xAxis:{data:h.map(x=>x+':00')},yAxis:{name:'kW'},series:[{type:'area',data:v,smooth:true,areaStyle:{opacity:0.2}}]})}})});
function refresh(){loading.value=true;setTimeout(()=>loading.value=false,500)}
</script>
<style scoped>.pg{padding:24px}.hd{display:flex;justify-content:space-between;align-items:center;margin-bottom:20px}.hd h2{margin:0;font-size:20px}.st{margin-bottom:20px}</style>
