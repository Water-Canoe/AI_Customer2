import { createRouter, createWebHistory } from 'vue-router'

import AiPage from './pages/AiPage'
import LogsPage from './pages/LogsPage'
import MessageWorkbenchPage from './pages/MessageWorkbenchPage'
import OverviewPage from './pages/OverviewPage'
import SettingsPage from './pages/SettingsPage'
import TablesPage from './pages/TablesPage'
import TaskPage from './pages/TaskPage'
import TrafficPage from './pages/TrafficPage'
import TrafficSettingsPage from './pages/TrafficSettingsPage'

export const routes = [
  {
    path: '/',
    redirect: '/tasks',
  },
  {
    path: '/tasks',
    name: 'tasks',
    component: TaskPage,
    meta: { title: '任务管理', subtitle: '选择拓客模式，生成 MediaCrawler 参数，并自动导入项目库。', hint: '选择模式后直接开始采集' },
  },
  {
    path: '/overview',
    name: 'overview',
    component: OverviewPage,
    meta: { title: '总览树', subtitle: '按平台、关键词、账号、内容和客户查看证据链。', hint: '从业务关系理解数据' },
  },
  {
    path: '/ai',
    name: 'ai',
    component: AiPage,
    meta: { title: 'AI分析', subtitle: '集中处理竞品筛选、目标客户筛选、失败重试和私信话术。', hint: '把线索变成可跟进客户' },
  },
  {
    path: '/message-workbench',
    name: 'message-workbench',
    component: MessageWorkbenchPage,
    meta: { title: '私信工作台', subtitle: '按关键词推进客户私信、回访和成交状态。', hint: '把客户变成可执行跟进队列' },
  },
  {
    path: '/logs',
    name: 'logs',
    component: LogsPage,
    meta: { title: '任务与日志', subtitle: '查看任务运行状态、控制台输出、归档和硬删除。', hint: '先确认任务是否正确完成' },
  },
  {
    path: '/tables',
    name: 'tables',
    component: TablesPage,
    meta: { title: '数据表', subtitle: '维护内容、评论、竞品、线索和目标客户。', hint: '直接处理具体数据' },
  },
  {
    path: '/settings',
    name: 'settings',
    component: SettingsPage,
    meta: { title: '设置', subtitle: '配置 AI 模型、MediaCrawler 路径、自动化和 ICP 画像。', hint: '开始前先把基础环境配好' },
  },
  {
    path: '/traffic-targeted',
    name: 'traffic-targeted',
    component: TrafficPage,
    meta: { title: '定向引流', subtitle: '从已采集视频生成队列，或按关键词搜索抖音视频后自动引流。', hint: '选择来源后执行' },
  },
  {
    path: '/traffic-random',
    name: 'traffic-random',
    component: TrafficPage,
    meta: { title: '随机引流', subtitle: '打开抖音推荐流后，按配置自动处理刷到的视频。', hint: '手动启动，系统自动跑完批次' },
  },
  {
    path: '/traffic-settings',
    name: 'traffic-settings',
    component: TrafficSettingsPage,
    meta: { title: '引流设置', subtitle: '配置独立授权、执行参数、文案和图片素材。', hint: '引流业务单独授权' },
  },
]

export const router = createRouter({
  history: createWebHistory(),
  routes,
})
