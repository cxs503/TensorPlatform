<template>
  <div class="training-guide">
    <div class="page-header">
      <h1 class="page-title"><el-icon><Reading /></el-icon> 训练流程说明</h1>
      <p class="page-desc">按图中箭头完成准备与运行；点击任一节点可直接进入对应模块。</p>
    </div>

    <el-alert title="预测模型与控制仿真的产出不同" type="info" :closable="false" show-icon class="intro">
      <template #default>预测、分类和时序预测产出可评估、部署的模型；FOPDT、MPC 等控制算法产出过程参数、仿真结果和图表，不会进入模型部署。</template>
    </el-alert>

    <el-card shadow="never" class="flow-card">
      <template #header><div class="flow-card-title">平台训练主流程</div></template>
      <div class="flow-scroll">
        <div class="common-flow">
          <template v-for="(node, index) in commonNodes" :key="node.title">
            <button type="button" class="flow-node common-node" @click="router.push(node.path)">
              <span class="node-number">{{ index + 1 }}</span>
              <span class="node-content"><b>{{ node.title }}</b><small>{{ node.module }}</small><em>{{ node.detail }}</em></span>
            </button>
            <span v-if="index < commonNodes.length - 1" class="flow-arrow">→</span>
          </template>
        </div>

        <div class="split-line"><span>选择任务类型</span></div>

        <div class="branch-flow">
          <section class="flow-branch prediction-branch">
            <header><span class="branch-icon">①</span><div><b>预测模型训练</b><small>回归 · 分类 · 时序预测</small></div></header>
            <div class="vertical-flow">
              <template v-for="(node, index) in predictionNodes" :key="node.title">
                <button type="button" class="flow-node branch-node" @click="router.push(node.path)">
                  <span class="node-content"><b>{{ node.title }}</b><small>{{ node.module }}</small><em>{{ node.detail }}</em></span>
                </button>
                <span v-if="index < predictionNodes.length - 1" class="vertical-arrow">↓</span>
              </template>
            </div>
          </section>

          <section class="flow-branch control-branch">
            <header><span class="branch-icon">②</span><div><b>控制仿真任务</b><small>FOPDT · MPC · 控制优化</small></div></header>
            <div class="vertical-flow">
              <template v-for="(node, index) in controlNodes" :key="node.title">
                <button type="button" class="flow-node branch-node" @click="router.push(node.path)">
                  <span class="node-content"><b>{{ node.title }}</b><small>{{ node.module }}</small><em>{{ node.detail }}</em></span>
                </button>
                <span v-if="index < controlNodes.length - 1" class="vertical-arrow">↓</span>
              </template>
            </div>
          </section>
        </div>
      </div>
    </el-card>

    <el-row :gutter="16" class="shortcut-row">
      <el-col :xs="24" :sm="12" :lg="8" v-for="item in shortcuts" :key="item.path">
        <el-card shadow="hover" class="shortcut-card" @click="router.push(item.path)"><h3>{{ item.title }}</h3><p>{{ item.description }}</p><el-button link type="primary">打开页面 →</el-button></el-card>
      </el-col>
    </el-row>
  </div>
</template>

<script setup lang="ts">
import { Reading } from '@element-plus/icons-vue'
import { useRouter } from 'vue-router'

const router = useRouter()
const commonNodes = [
  { title: '准备数据', module: '数据中台 → 数据集管理 / 数据接入', detail: '上传或接入数据，时序数据保留时间列。', path: '/data/datasets' },
  { title: '清洗与分析', module: '数据中台 → 数据集管理 / 数据治理', detail: '按需清洗、检查、特征工程或标注，得到可用数据版本。', path: '/data/datasets' },
  { title: '配置环境与依赖', module: '模型工厂 → 算法商店 → 依赖配置', detail: '设置 Python、CUDA 和依赖包，构建可用运行环境。', path: '/model/algorithm-store' },
  { title: '上传或编辑算法', module: '模型工厂 → 算法商店 / 代码编辑器', detail: '上传 .py 或 ZIP 项目，选择入口和运行环境。', path: '/model/algorithm-store' },
]
const predictionNodes = [
  { title: '配置训练', module: '算法商店 → 训练', detail: '选择数据集、目标列、特征列、超参数和模型分类。', path: '/model/algorithm-store' },
  { title: '查看训练结果', module: '训练引擎 → 训练任务', detail: '查看进度、验证指标和模型产出。', path: '/model/training-tasks' },
  { title: '模型评估', module: '评估测试 → 模型评估', detail: '使用独立测试集，确认模型在新数据上的实际指标。', path: '/model/model-evaluation' },
  { title: '模型管理', module: '模型资产 → 模型管理', detail: '查看版本、参数、指标和 MLflow 追踪，整理模型分类。', path: '/model/versions' },
  { title: '部署与推理', module: '推理部署 → 部署管理', detail: '确认后部署模型，或进行在线推理、批量推理和制品导出。', path: '/deploy/deployment' },
]
const controlNodes = [
  { title: '配置并运行仿真', module: '算法商店 → 运行控制仿真', detail: '选择完整过程数据及时间、被控、控制、扰动变量。', path: '/model/algorithm-store' },
  { title: '查看仿真结果', module: '训练引擎 → 控制仿真任务', detail: '查看辨识参数、JSON、PNG 图表和运行日志。', path: '/model/control-simulations' },
]
const shortcuts = [
  { title: '数据集管理', description: '上传、查看、清洗分析与分割数据集。', path: '/data/datasets' },
  { title: '算法商店', description: '上传算法项目并发起训练或控制仿真。', path: '/model/algorithm-store' },
  { title: '训练任务', description: '查看普通预测、分类和时序模型训练进度。', path: '/model/training-tasks' },
  { title: '控制仿真任务', description: '查看 FOPDT/MPC 辨识、仿真与结果文件。', path: '/model/control-simulations' },
  { title: '模型评估', description: '使用完整测试集复核已训练模型。', path: '/model/model-evaluation' },
  { title: '模型管理', description: '整理已训练模型并进入推理部署。', path: '/model/versions' },
]
</script>

<style scoped lang="scss">
.training-guide{padding:20px}.page-header{margin-bottom:20px}.page-title{display:flex;align-items:center;gap:8px;margin:0;font-size:24px}.page-desc{margin:8px 0 0;color:#64748b}.intro{margin-bottom:16px}.flow-card{margin-bottom:16px}.flow-card-title{font-weight:600}.flow-scroll{overflow-x:auto;padding:6px 2px 10px}.common-flow{display:flex;align-items:stretch;justify-content:center;gap:10px;min-width:980px}.flow-node{border:1px solid #cbd5e1;background:#fff;border-radius:9px;text-align:left;cursor:pointer;transition:.16s;box-shadow:0 1px 2px rgb(15 23 42 / .04)}.flow-node:hover{border-color:#409eff;box-shadow:0 4px 12px rgb(64 158 255 / .14);transform:translateY(-1px)}.common-node{display:flex;align-items:flex-start;gap:10px;width:215px;padding:13px}.node-number{display:grid;place-items:center;flex:0 0 25px;height:25px;border-radius:50%;background:#2563eb;color:#fff;font-size:13px;font-weight:700}.node-content{display:flex;flex-direction:column;gap:4px}.node-content b{font-size:14px;color:#1e293b}.node-content small{font-size:12px;color:#2563eb}.node-content em{font-style:normal;font-size:12px;color:#64748b;line-height:1.45}.flow-arrow{align-self:center;color:#60a5fa;font-size:26px;font-weight:600}.split-line{display:flex;align-items:center;gap:12px;margin:26px auto 18px;max-width:1010px;color:#475569;font-size:13px}.split-line::before,.split-line::after{content:'';height:1px;flex:1;background:#cbd5e1}.split-line span{padding:5px 12px;border-radius:14px;background:#f1f5f9}.branch-flow{display:grid;grid-template-columns:1fr 1fr;gap:22px;max-width:850px;margin:0 auto}.flow-branch{border:1px solid #dbeafe;border-radius:10px;padding:16px;background:#f8fbff}.control-branch{border-color:#bbf7d0;background:#f7fdf9}.flow-branch header{display:flex;align-items:center;gap:9px;margin-bottom:14px}.branch-icon{display:grid;place-items:center;width:28px;height:28px;border-radius:7px;background:#dbeafe;color:#1d4ed8;font-weight:700}.control-branch .branch-icon{background:#dcfce7;color:#15803d}.flow-branch header div{display:flex;flex-direction:column;gap:2px}.flow-branch header b{color:#1e3a5f}.flow-branch header small{color:#64748b}.vertical-flow{display:flex;flex-direction:column;align-items:stretch}.branch-node{padding:12px;width:100%}.vertical-arrow{text-align:center;color:#60a5fa;font-size:21px;line-height:25px}.control-branch .vertical-arrow{color:#22c55e}.shortcut-row{row-gap:16px}.shortcut-card{min-height:145px;cursor:pointer;transition:transform .15s}.shortcut-card:hover{transform:translateY(-2px)}.shortcut-card h3{margin:0 0 8px;color:#1e293b}.shortcut-card p{min-height:42px;margin:0;color:#64748b;line-height:1.6}@media(max-width:900px){.branch-flow{grid-template-columns:1fr}.common-flow{justify-content:flex-start}.flow-scroll{padding-bottom:14px}}
</style>
