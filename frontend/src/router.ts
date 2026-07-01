import { createRouter, createWebHistory } from 'vue-router'

import AiPage from './pages/AiPage'
import LogsPage from './pages/LogsPage'
import MessageWorkbenchPage from './pages/MessageWorkbenchPage'
import OverviewPage from './pages/OverviewPage'
import SettingsPage from './pages/SettingsPage'
import TablesPage from './pages/TablesPage'
import TaskPage from './pages/TaskPage'

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
]

export const router = createRouter({
  history: createWebHistory(),
  routes,
})

export const workflowSteps = [
  { index: '01', title: '配置画像', note: 'AI模型、ICP、默认采集参数', route: '/settings', view: 'settings' },
  { index: '02', title: '创建任务', note: '四种模式，一次选择', route: '/tasks', view: 'tasks' },
  { index: '03', title: '观察入库', note: '日志、数量、失败原因', route: '/logs', view: 'logs' },
  { index: '04', title: '理解来源', note: '数据表与总览树交叉查看', route: '/tables', view: 'tables' },
  { index: '05', title: 'AI筛选跟进', note: '批量分析、私信话术、状态流转', route: '/ai', view: 'ai' },
  { index: '06', title: '私信跟进', note: '按关键词推进客户回访', route: '/message-workbench', view: 'message-workbench' },
]
