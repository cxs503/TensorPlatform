<template>
<div class="pg"><div class="hd"><h2>📂 数据资产目录</h2><el-button type="primary" @click="showForm=true">+ 注册资产</el-button></div>
<el-card><div style="margin-bottom:12px"><el-input v-model="keyword" placeholder="搜索名称、描述或负责人" clearable style="width:320px" @keyup.enter="reload" @clear="reload"/><el-button style="margin-left:8px" @click="reload">搜索</el-button></div><el-table :data="assets" stripe v-loading="loading"><el-table-column prop="id" label="ID" width="90"/><el-table-column prop="name" label="资产名称"/><el-table-column prop="type" label="类型"><template #default="{row}"><el-tag size="small">{{row.type}}</el-tag></template></el-table-column><el-table-column prop="owner" label="负责人"/><el-table-column prop="domain" label="所属域"/><el-table-column prop="status" label="状态"><template #default="{row}"><el-tag :type="row.status==='published'?'success':'info'" size="small">{{row.status}}</el-tag></template></el-table-column><el-table-column prop="updated_at" label="更新时间"/></el-table><div style="margin-top:12px;text-align:right"><el-pagination v-model:current-page="page" v-model:page-size="pageSize" :total="total" :page-sizes="[10,20,50,100]" layout="total, sizes, prev, pager, next" @current-change="load" @size-change="reload"/></div></el-card></div>
</template>
<script setup lang="ts">
import { ref, onMounted } from 'vue'; import { get } from '@/api/legacy';
const loading=ref(false), showForm=ref(false), assets=ref<any[]>([]), total=ref(0), page=ref(1), pageSize=ref(20), keyword=ref('');
async function load(){loading.value=true;try{const params:any={page:page.value,page_size:pageSize.value};if(keyword.value.trim())params.q=keyword.value.trim();const d:any=await get('/catalog/assets',params);assets.value=d.items||[];total.value=d.total||0}catch(e){}finally{loading.value=false}}
function reload(){page.value=1;load()}
onMounted(load);
</script>
<style scoped>.pg{padding:24px}.hd{display:flex;justify-content:space-between;align-items:center;margin-bottom:20px}.hd h2{margin:0;font-size:20px}</style>
