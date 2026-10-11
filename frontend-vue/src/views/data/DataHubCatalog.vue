<template>
<div class="pg"><div class="hd"><h2>📋 数据资产目录</h2><div style="display:flex;gap:10px"><el-input v-model="search" placeholder="搜索数据集、表、字段..." style="width:300px" clearable @input="page=1"/><el-button @click="router.push('/data/lineage')">🔗 数据血缘</el-button></div></div>
<el-row :gutter="16" class="st">
  <el-col :span="6"><el-card><el-statistic title="数据集" :value="stats.datasets"/></el-card></el-col>
  <el-col :span="6"><el-card><el-statistic title="特征列" :value="stats.columns"/></el-card></el-col>
  <el-col :span="6"><el-card><el-statistic title="数据源" :value="stats.sources"/></el-card></el-col>
  <el-col :span="6"><el-card><el-statistic title="标签" :value="stats.tags"/></el-card></el-col>
</el-row>
<el-card><el-table :data="pagedCatalog" stripe v-loading="loading" @row-click="showDetail">
  <el-table-column label="ID" width="100"><template #default="{row}">{{ row.id ? '#'+row.id : '-' }}</template></el-table-column>
  <el-table-column prop="name" label="名称" width="200"/>
  <el-table-column label="类型" width="100"><template #default="{row}"><el-tag :type="row.type==='dataset'?'success':row.type==='table'?'primary':'info'" size="small">{{row.type}}</el-tag></template></el-table-column>
  <el-table-column prop="description" label="描述" min-width="200" show-overflow-tooltip/>
  <el-table-column label="标签" width="200"><template #default="{row}"><el-tag v-for="t in (row.tags||[])" :key="t" size="small" style="margin:2px">{{t}}</el-tag></template></el-table-column>
  <el-table-column prop="owner" label="负责人" width="100"/>
  <el-table-column prop="updated_at" label="更新时间" width="160"/>
</el-table><div style="display:flex;justify-content:flex-end;margin-top:16px"><el-pagination v-model:current-page="page" v-model:page-size="pageSize" :page-sizes="[10,20,50]" layout="total, sizes, prev, pager, next" :total="filteredCatalog.length"/></div></el-card>

<el-dialog v-model="showDetailDlg" :title="detailItem?.name||'资产详情'" width="700px">
  <el-descriptions v-if="detailItem" :column="2" border>
    <el-descriptions-item label="名称">{{detailItem.name}}</el-descriptions-item>
    <el-descriptions-item label="类型">{{detailItem.type}}</el-descriptions-item>
    <el-descriptions-item label="负责人">{{detailItem.owner||'-'}}</el-descriptions-item>
    <el-descriptions-item label="行数">{{detailItem.rows||'-'}}</el-descriptions-item>
    <el-descriptions-item label="大小">{{detailItem.size||'-'}}</el-descriptions-item>
    <el-descriptions-item label="格式">{{detailItem.format||'CSV'}}</el-descriptions-item>
    <el-descriptions-item label="描述" :span="2">{{detailItem.description||'暂无描述'}}</el-descriptions-item>
    <el-descriptions-item label="列信息" :span="2">
      <el-table :data="detailItem.columns||[]" size="small" stripe>
        <el-table-column prop="name" label="列名"/><el-table-column prop="dtype" label="类型" width="80"/>
        <el-table-column prop="nullable" label="可空" width="60"><template #default="{row}"><el-tag size="small" :type="row.nullable?'warning':'success'">{{row.nullable?'是':'否'}}</el-tag></template></el-table-column>
      </el-table>
    </el-descriptions-item>
  </el-descriptions>
  <template #footer><el-button @click="showDetailDlg=false">关闭</el-button></template>
</el-dialog>
</div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'; import { useRouter } from 'vue-router'; import { get } from '@/api/legacy';
const router=useRouter();
const loading=ref(false),search=ref(''),showDetailDlg=ref(false),detailItem=ref<any>(null),page=ref(1),pageSize=ref(20);
const catalog=ref<any[]>([]);
const stats=computed(()=>({
  datasets:catalog.value.filter(c=>c.type==='dataset').length,
  columns:catalog.value.reduce((a,c)=>a+(c.columns?.length||c.column_count||0),0),
  sources:catalog.value.filter(c=>c.type==='source').length,
  tags:new Set(catalog.value.flatMap(c=>c.tags||[])).size
}));
const filteredCatalog=computed(()=>{
  if(!search.value)return catalog.value;
  const q=search.value.toLowerCase();
  return catalog.value.filter(c=>c.name?.toLowerCase().includes(q)||c.description?.toLowerCase().includes(q)||(c.tags||[]).some((t:string)=>t.toLowerCase().includes(q)));
});
const pagedCatalog=computed(()=>filteredCatalog.value.slice((page.value-1)*pageSize.value,page.value*pageSize.value));
onMounted(async()=>{loading.value=true;try{
  const [dsRes,catRes,srcRes,curatedRes]=await Promise.all([
    get('/datasets').catch(()=>({items:[]})),
    get('/catalog/entries').catch(()=>({items:[]})),
    get('/datasources').catch(()=>({items:[]})),
    get('/curated-datasets').catch(()=>({items:[]}))
  ]);
  const datasets=(Array.isArray(dsRes)?dsRes:(dsRes.items||[])).map((d:any)=>({...d,type:'dataset'}));
  const entries=(Array.isArray(catRes)?catRes:(catRes.items||catRes.entries||[])).map((d:any)=>({...d,type:'table'}));
  const sources=(Array.isArray(srcRes)?srcRes:(srcRes.items||srcRes.sources||[])).map((d:any)=>({...d,type:'source'}));
  const curated=(Array.isArray(curatedRes)?curatedRes:(curatedRes.items||[])).map((d:any)=>({
    ...d,type:'dataset',owner:d.source,rows:d.row_count,format:d.format||'CSV',
    tags:[...(d.tags||[]),'验证数据资产'],updated_at:d.updated_at
  }));
  catalog.value=[...curated,...datasets,...entries,...sources];
}catch{}finally{loading.value=false}});
function showDetail(row:any){detailItem.value=row;showDetailDlg.value=true}
</script>
<style scoped>.pg{padding:24px}.hd{display:flex;justify-content:space-between;align-items:center;margin-bottom:20px}.hd h2{margin:0;font-size:20px}.st{margin-bottom:20px}</style>
