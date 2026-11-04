<template>
  <div class="tf-page">
    <HeroPanel>
      <template #title>创新中心 <span class="tf-hero-mint">· 未实现</span></template>
      <template #sub>
        自主创新实体（SPEC 第 4 章「创新与演化中心」）尚未建设——本页如实标注，不用假数据占位。
      </template>
    </HeroPanel>

    <el-row :gutter="16">
      <el-col :span="12">
        <div class="tf-card">
          <div class="tf-card-title">现状（如实）</div>
          <el-descriptions :column="1" border size="small">
            <el-descriptions-item label="创新提案机制">未实现</el-descriptions-item>
            <el-descriptions-item label="自主探索任务线">未实现（探索触发器在 AOG P2 规划中）</el-descriptions-item>
            <el-descriptions-item label="创新产出账">未实现（能力中心已有相邻面：<router-link class="tf-link" to="/center/capability">能力增量流水</router-link>）</el-descriptions-item>
          </el-descriptions>
          <div class="innov-note">
            依据：AUTONOMOUS_AGENT_ORGANIZATION_GOVERNANCE_SPEC_v1.0 第 4 章；P0 审计（报告 #3554）
            已定谳「创新实体未实现」。建设属于 P2 映射表（M01-M08）后的排期项。
          </div>
        </div>
      </el-col>
      <el-col :span="12">
        <div class="tf-card">
          <div class="tf-card-title">相邻的真实机制（已上线）</div>
          <div class="innov-link" v-for="l in links" :key="l.to">
            <router-link class="tf-link" :to="l.to">{{ l.label }}</router-link>
            <span class="tf-dim">{{ l.desc }}</span>
          </div>
        </div>
      </el-col>
    </el-row>
  </div>
</template>

<script setup lang="ts">
const links = [
  { to: '/center/capability', label: '能力中心', desc: '验收任务 → 能力增量入账（真实账本）' },
  { to: '/missions', label: '开发任务', desc: '任务在册与状态（创新任务未来落此平面）' },
  { to: '/papers', label: '论文生产线', desc: '研究产出线（已运行）' },
]
</script>

<style scoped>
.tf-link { color: var(--tf-navy-700); text-decoration: none; }
.tf-link:hover { text-decoration: underline; }
.tf-dim { color: #a5aeb8; font-size: 12px; margin-left: 8px; }
.innov-note { margin-top: 12px; color: #a5aeb8; font-size: 12px; line-height: 1.7; }
.innov-link { padding: 6px 0; }
</style>
