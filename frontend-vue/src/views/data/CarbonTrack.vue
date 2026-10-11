<template>
<div class="pg"><div class="hd"><h2>🌍 碳排放追踪</h2><el-button type="primary" @click="refresh">刷新数据</el-button></div>
<el-row :gutter="16" class="st">
  <el-col :span="6"><el-card><el-statistic title="今日排放" :value="todayEmission" suffix="kgCO₂"/></el-card></el-col>
  <el-col :span="6"><el-card><el-statistic title="本月累计" :value="monthEmission" suffix="tCO₂"/></el-card></el-col>
  <el-col :span="6"><el-card><el-statistic title="碳配额剩余" :value="quotaLeft" suffix="tCO₂"/></el-card></el-col>
  <el-col :span="6"><el-card><el-statistic title="减排率" :value="reductionRate" suffix="%"/></el-card></el-col>
</el-row>
<el-card style="margin-bottom:16px"><template #header>📈 碳排放趋势</template><div ref="co2Chart" style="height:280px"/></el-card>
<el-card><el-table :data="records" stripe v-loading="loading">
  <el-table-column prop="source" label="排放源" width="160"/>
  <el-table-column label="类型" width="100"><template #default="{row}"><el-tag>{{row.type||'固定源'}}</el-tag></template></el-table-column>
  <el-table-column prop="amount" label="排放量(kg)" width="120"/>
  <el-table-column label="状态" width="100"><template #default="{row}"><el-tag :type="(row.amount||0)<100?'success':'danger'" size="small">{{(row.amount||0)<100?'正常':'超标'}}</el-tag></template></el-table-column>
  <el-table-column prop="location" label="位置" width="120"/>
  <el-table-column prop="recorded_at" label="记录时间" width="170"/>
  <el-table-column label="操作" width="100"><template #default="{row}"><el-button size="small" @click="showDetail(row)">详情</el-button></template></el-table-column>
</el-table></el-card>
</div>
</template>
<script setup lang="ts">
import { ref, onMounted, nextTick } from 'vue'; import { get } from '@/api/legacy'; import * as echarts from 'echarts';
const loading=ref(false),records=ref<any[]>([]),co2Chart=ref();
const todayEmission=ref(0),monthEmission=ref(0),quotaLeft=ref(850),reductionRate=ref(12.5);
onMounted(async()=>{loading.value=true;try{
  const d=await get('/carbon/emissions');
  const list=Array.isArray(d)?d:(d.items||[]);
  // 后端字段: emission_amount/source/scope/entity_type/recorded_at; 映射到表格字段
  records.value=list.map((r:any)=>({
    ...r,
    amount:r.emission_amount??r.amount??0,
    source:r.source||r.entity_id,
    type:r.entity_type||('scope'+(r.scope||1)),
    location:r.location||r.entity_id,
    recorded_at:r.recorded_at,
  }));
  todayEmission.value=records.value.reduce((a:any,r:any)=>a+(Number(r.amount)||0),0);
  monthEmission.value=(Number(todayEmission.value)*30/1000).toFixed(1);
}catch{}finally{loading.value=false};nextTick(drawChart)});
function drawChart(){if(!co2Chart.value)return;const opt=echarts.init(co2Chart.value);const data=[120,135,110,150,130,125,140];opt.setOption({tooltip:{},xAxis:{data:['Mon','Tue','Wed','Thu','Fri','Sat','Sun']},yAxis:{name:'kgCO₂'},series:[{type:'bar',data,itemStyle:{color:'#67C23A'}},{type:'line',data:data.map(v=>v*1.1),smooth:true,lineStyle:{color:'#F56C6C'}}]})}
function showDetail(row:any){alert('排放源: '+row.source+'\n排放量: '+row.amount+'kg')}
function refresh(){loading.value=true;setTimeout(()=>loading.value=false,500)}
</script>
<style scoped>.pg{padding:24px}.hd{display:flex;justify-content:space-between;align-items:center;margin-bottom:20px}.hd h2{margin:0;font-size:20px}.st{margin-bottom:20px}</style>
