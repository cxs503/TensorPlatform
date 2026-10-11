<template>
<div class="pg"><div class="hd"><h2>⚠️ 故障预测</h2><el-button type="primary" @click="refresh">刷新</el-button></div>
<el-row :gutter="16" class="st">
  <el-col :span="6"><el-card><el-statistic title="监控设备" :value="equipment.length"/></el-card></el-col>
  <el-col :span="6"><el-card><el-statistic title="健康" :value="healthyCount"/></el-card></el-col>
  <el-col :span="6"><el-card><el-statistic title="预警" :value="warningCount"/></el-card></el-col>
  <el-col :span="6"><el-card><el-statistic title="故障率" :value="faultRate" suffix="%"/></el-card></el-col>
</el-row>
<el-card style="margin-bottom:16px"><template #header>📊 健康度分布</template><div ref="healthChart" style="height:250px"/></el-card>
<el-card><el-table :data="equipment" stripe v-loading="loading">
  <el-table-column prop="name" label="设备名称" width="180"/><el-table-column prop="type" label="类型" width="120"/>
  <el-table-column label="健康度" width="180"><template #default="{row}"><el-progress :percentage="row.health_score||0" :stroke-width="16" :color="(row.health_score||0)>80?'#67c23a':(row.health_score||0)>50?'#e6a23c':'#f56c6c'"/></template></el-table-column>
  <el-table-column label="状态" width="100"><template #default="{row}"><el-tag :type="row.status==='normal'?'success':row.status==='warning'?'warning':'danger'" size="small">{{row.status}}</el-tag></template></el-table-column>
  <el-table-column prop="remaining_life_days" label="剩余寿命(天)" width="120"/>
  <el-table-column prop="last_check" label="最后检测" width="170"/>
  <el-table-column label="操作" width="120"><template #default="{row}"><el-button size="small" @click="diagnose(row)">诊断</el-button></template></el-table-column>
</el-table></el-card>
</div>
</template>
<script setup lang="ts">
import { ref, computed, onMounted, nextTick } from 'vue'; import { ElMessage } from 'element-plus'; import { get } from '@/api/legacy';
import * as echarts from 'echarts';
const loading=ref(false),equipment=ref<any[]>([]),healthChart=ref();
const healthyCount=computed(()=>equipment.value.filter(e=>e.status==='normal').length);
const warningCount=computed(()=>equipment.value.filter(e=>e.status==='warning').length);
const faultRate=computed(()=>{const t=equipment.value.length;return t?((t-healthyCount.value-warningCount.value)/t*100).toFixed(1):'0'});
onMounted(async()=>{loading.value=true;try{const d=await get('/fault-prediction/equipment');equipment.value=Array.isArray(d)?d:(d.items||d.equipment||d.devices||[])}catch{}finally{loading.value=false};nextTick(drawChart)});
function drawChart(){if(!healthChart.value)return;const opt=echarts.init(healthChart.value);opt.setOption({tooltip:{},series:[{type:'gauge',detail:{formatter:'{value}%'},data:[{value:healthyCount.value/equipment.value.length*100,name:'健康率'}],axisLine:{lineStyle:{color:[[0.3,'#F56C6C'],[0.6,'#E6A23C'],[1,'#67C23A']]}}}]})}
function diagnose(row:any){ElMessage.info(`诊断 ${row.name}: 健康度${row.health_score||0}%, 剩余${row.remaining_life_days||'?'}天`)}
function refresh(){loading.value=true;setTimeout(()=>loading.value=false,500)}
</script>
<style scoped>.pg{padding:24px}.hd{display:flex;justify-content:space-between;align-items:center;margin-bottom:20px}.hd h2{margin:0;font-size:20px}.st{margin-bottom:20px}</style>
