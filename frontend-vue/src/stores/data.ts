import { defineStore } from 'pinia'
import { ref } from 'vue'
import { api } from '@/api'
import type { Project, Dataset, Model, TrainingTask, Deployment } from '@/types'

export const useDataStore = defineStore('data', () => {
  // 项目
  const projects = ref<Project[]>([])
  const currentProject = ref<Project | null>(null)

  // 数据集
  const datasets = ref<Dataset[]>([])

  // 模型
  const models = ref<Model[]>([])

  // 训练任务
  const trainingTasks = ref<TrainingTask[]>([])

  // 部署
  const deployments = ref<Deployment[]>([])

  // 仪表盘统计
  const dashboardStats = ref({
    projects: { total: 0, active: 0 },
    datasets: { total: 0, size: 0 },
    models: { total: 0, deployed: 0 },
    inference: { total_calls: 0, today_calls: 0 }
  })

  // 最近活动
  const recentActivities = ref<any[]>([])

  // 加载仪表盘数据
  async function loadDashboardStats() {
    try {
      const res = await api.dashboard.getStatistics()
      dashboardStats.value = res
    } catch (error) {
      console.error('加载仪表盘统计失败:', error)
    }
  }

  // 加载最近活动
  async function loadRecentActivities() {
    try {
      const res = await api.dashboard.getRecentActivities(5)
      recentActivities.value = res.activities || []
    } catch (error) {
      console.error('加载最近活动失败:', error)
    }
  }

  // 加载项目
  async function loadProjects() {
    try {
      const res = await api.project.getList()
      projects.value = res.projects || []
    } catch (error) {
      console.error('加载项目失败:', error)
    }
  }

  // 加载数据集
  async function loadDatasets() {
    try {
      const res = await api.dataset.getList()
      datasets.value = res.datasets || []
    } catch (error) {
      console.error('加载数据集失败:', error)
    }
  }

  // 加载模型
  async function loadModels() {
    try {
      const res = await api.model.getList()
      models.value = res.models || []
    } catch (error) {
      console.error('加载模型失败:', error)
    }
  }

  // 加载训练任务
  async function loadTrainingTasks() {
    try {
      const res = await api.training.getList()
      trainingTasks.value = res.tasks || []
    } catch (error) {
      console.error('加载训练任务失败:', error)
    }
  }

  // 加载部署
  async function loadDeployments() {
    try {
      const res = await api.deployment.getList()
      deployments.value = res.deployments || []
    } catch (error) {
      console.error('加载部署失败:', error)
    }
  }

  return {
    projects,
    currentProject,
    datasets,
    models,
    trainingTasks,
    deployments,
    dashboardStats,
    recentActivities,
    loadDashboardStats,
    loadRecentActivities,
    loadProjects,
    loadDatasets,
    loadModels,
    loadTrainingTasks,
    loadDeployments
  }
})
