<template>
<div class="pg"><div class="hd"><h2>🔌 工业协议</h2><el-button type="primary" @click="refresh">刷新</el-button></div>
<el-row :gutter="16" class="st">
  <el-col :span="6"><el-card><el-statistic title="协议连接" :value="connections.length"/></el-card></el-col>
  <el-col :span="6"><el-card><el-statistic title="在线" :value="connections.filter(c=>c.status==='online').length"/></el-card></el-col>
  <el-col :span="6"><el-card><el-statistic title="数据吞吐" :value="totalThroughput" suffix="msg/s"/></el-card></el-col>
</el-row>
<el-card><el-table :data="connections" stripe v-loading="loading">
  <el-table-column prop="name" label="连接名称" width="180"/>
  <el-table-column label="协议" width="120"><template #default="{row}"><el-tag>{{row.protocol||'Modbus TCP'}}</el-tag></template></el-table-column>
  <el-table-column prop="host" label="地址" width="180"/>
  <el-table-column label="状态" width="100"><template #default="{row}"><el-tag :type="row.status==='online'?'success':'danger'">{{row.status}}</el-tag></template></el-table-column>
  <el-table-column label="数据点" width="80"><template #default="{row}">{{row.points||0}}</template></el-table-column>
  <el-table-column prop="throughput" label="吞吐(msg/s)" width="120"/>
  <el-table-column prop="last_data" label="最后数据" width="170"/>
  <el-table-column label="操作" width="140"><template #default="{row}"><el-button size="small" @click="reconnect(row)">重连</el-button></template></el-table-column>
</el-table></el-card>
</div>
</template>
<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'; import { ElMessage } from 'element-plus'; import http from '@/api/request';
const loading=ref(false),connections=ref<any[]>([]);
const totalThroughput=computed(()=>connections.value.reduce((a,c)=>a+(c.throughput||0),0));
async function loadDevices(){loading.value=true;try{const d=await http.get('/industrial/devices');connections.value=Array.isArray(d)?d:(d.items||[])}catch{connections.value=[]}finally{loading.value=false}}
onMounted(loadDevices);
function reconnect(row:any){ElMessage.success('重连中: '+row.name)}
function refresh(){loadDevices()}
</script>
<style scoped>.pg{padding:24px}.hd{display:flex;justify-content:space-between;align-items:center;margin-bottom:20px}.hd h2{margin:0;font-size:20px}.st{margin-bottom:20px}</style>
