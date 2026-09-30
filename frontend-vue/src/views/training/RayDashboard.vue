<template>
  <div class="pg">
    <PageHeader title="Ray Dashboard" desc="分布式集群监控 / 资源管理 / 任务调度" />
    <el-alert v-if="cluster.status === 'offline' || cluster.status === 'unavailable'" title="Ray 集群当前未就绪，分布式训练入口已停用；普通单机训练不受影响。" type="warning" :closable="false" show-icon style="margin-bottom:16px" />
    <el-row :gutter="20" class="stats">
      <el-col :span="6"><el-card shadow="hover"><div class="sv">{{cluster.nodes||1}}</div><div class="sl">节点数</div></el-card></el-col>
      <el-col :span="6"><el-card shadow="hover"><div class="sv">{{cluster.cpus||2}}</div><div class="sl">CPU</div></el-card></el-col>
      <el-col :span="6"><el-card shadow="hover"><div class="sv">{{cluster.status === 'checking' ? '连接中' : cluster.status}}</div><div class="sl">状态</div></el-card></el-col>
      <el-col :span="6"><el-card shadow="hover"><div class="sv">{{jobs.length}}</div><div class="sl">训练任务</div></el-card></el-col>
    </el-row>

    <el-card style="margin-top:16px" header="训练任务">
      <el-table :data="jobs" v-loading="loading" empty-text="暂无任务">
        <el-table-column prop="id" label="ID" width="100" />
        <el-table-column prop="name" label="名称" />
        <el-table-column prop="status" label="状态" width="90">
          <template #default="{row}"><el-tag :type="row.status==='running'?'success':row.status==='completed'?'info':'warning'" size="small">{{row.status}}</el-tag></template>
        </el-table-column>
        <el-table-column prop="created_at" label="创建时间" width="160" />
        <el-table-column label="操作" width="140">
          <template #default="{row}">
            <el-button v-if="row.status==='pending'" size="small" type="success" :disabled="cluster.status!=='ready'" @click="start(row)">启动</el-button>
            <el-button v-if="row.status==='running'" size="small" type="danger" @click="stop(row)">停止</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import {ref,reactive,onMounted} from 'vue';import {ElMessage} from 'element-plus';import {get,post} from '@/api/legacy';import PageHeader from '@/components/PageHeader.vue'
const cluster=reactive({nodes:0,cpus:0,status:'checking'});const jobs=ref<any[]>([]);const loading=ref(false)
async function load(){loading.value=true;cluster.status='checking';try{const s=await get('/distributed-training/status');const info=s?.cluster||s||{};cluster.status=s?.status||info.status||'offline';cluster.nodes=info.nodes||0;cluster.cpus=info.cpus||0;const r=await get('/distributed-training/jobs');jobs.value=r?.jobs||r?.items||[]}catch{cluster.status='offline';cluster.nodes=0;cluster.cpus=0}finally{loading.value=false}}
async function start(row:any){await post('/distributed-training/jobs/'+row.id+'/start');load()}
async function stop(row:any){await post('/distributed-training/jobs/'+row.id+'/stop');load()}
onMounted(load)
</script>
<style scoped>.pg{max-width:1100px}.stats{margin-bottom:16px}.sv{font-size:28px;font-weight:700;color:#67c23a}.sl{font-size:12px;color:#999;margin-top:4px}</style>
