// Element Plus 图标全局注册
import * as Icons from '@element-plus/icons-vue'
import type { App } from 'vue'

export default function registerIcons(app: App) {
  // 注册所有图标
  for (const [key, component] of Object.entries(Icons)) {
    app.component(key, component)
  }
}

// 导出图标名称常量供模板使用
export const iconNames = Object.keys(Icons)
