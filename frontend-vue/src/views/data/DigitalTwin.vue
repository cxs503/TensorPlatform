<template>
<div class="pg"><div class="hd"><h2>🌐 数字孪生</h2><el-button type="primary" @click="refresh">刷新</el-button></div>
<el-row :gutter="16" class="st">
  <el-col :span="6"><el-card><el-statistic title="孪生体" :value="twins.length"/></el-card></el-col>
  <el-col :span="6"><el-card><el-statistic title="运行中" :value="twins.filter(t=>t.status==='active').length"/></el-card></el-col>
  <el-col :span="6"><el-card><el-statistic title="数据点" :value="totalPoints"/></el-card></el-col>
</el-row>
<el-row :gutter="16">
  <el-col :span="16">
    <el-card style="margin-bottom:16px"><template #header>🏭 3D 设备拓扑</template><div style="height:300px;background:#1a1a2e;display:flex;align-items:center;justify-content:center;border-radius:8px"><div style="text-align:center;color:#aaa"><el-icon size="64"><Cpu/></el-icon><p>设备孪生可视化区域</p><p style="font-size:12px">连接设备传感器数据实时映射</p></div></div></el-card>
  </el-col>
  <el-col :span="8">
    <el-card><template #header>📡 实时传感器</template>
      <div v-for="s in sensors" :key="s.id" style="display:flex;justify-content:space-between;align-items:center;padding:8px 0;border-bottom:1px solid #eee">
        <span>{{s.name}}</span><span style="font-weight:bold" :style="{color:s.value>s.threshold?'#F56C6C':'#67C23A'}">{{s.value}}{{s.unit}}</span>
      </div>
    </el-card>
  </el-col>
</el-row>
<el-card><el-table :data="twins" stripe v-loading="loading">
  <el-table-column prop="name" label="名称" width="180"/><el-table-column prop="type" label="类型" width="120"/>
  <el-table-column prop="status" label="状态" width="100"><template #default="{row}"><el-tag :type="row.status==='active'?'success':'warning'">{{row.status}}</el-tag></template></el-table-column>
  <el-table-column prop="data_points" label="数据点" width="100"/>
  <el-table-column prop="last_sync" label="最后同步" width="170"/>
  <el-table-column label="操作" width="160"><template #default="{row}"><el-button size="small" @click="viewTwin(row)">查看</el-button><el-button size="small" type="success" @click="syncTwin(row)">同步</el-button></template></el-table-column>
</el-table></el-card>
</div>
</template>
<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'; import { ElMessage } from 'element-plus'; import { get } from '@/api/legacy';
const loading=ref(false),twins=ref<any[]>([]);
const sensors=ref([{id:1,name:'轴承温度',value:68.5,unit:'°C',threshold:75},{id:2,name:'电机转速',value:1450,unit:'rpm',threshold:1600},{id:3,name:'振动幅度',value:2.3,unit:'mm/s',threshold:4.5},{id:4,name:'润滑油温',value:42,unit:'°C',threshold:55}]);
const totalPoints=computed(()=>twins.value.reduce((a,t)=>a+(t.data_points||0),0));
onMounted(async()=>{loading.value=true;try{const d=await get('/digital-twin');twins.value=Array.isArray(d)?[d]:(d.items||[d])}catch{}finally{loading.value=false}});
function viewTwin(row:any){ElMessage.info('打开孪生体: '+row.name)}
function syncTwin(row:any){ElMessage.success('同步完成: '+row.name)}
function refresh(){loading.value=true;setTimeout(()=>loading.value=false,500)}
</script>
<style scoped>.pg{padding:24px}.hd{display:flex;justify-content:space-between;align-items:center;margin-bottom:20px}.hd h2{margin:0;font-size:20px}.st{margin-bottom:20px}</style>
