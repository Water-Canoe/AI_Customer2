import { createRouter, createWebHistory } from 'vue-router'

import AutoAgentPage from './pages/AutoAgentPage'
import AiPage from './pages/AiPage'
import LogsPage from './pages/LogsPage'
import MessageWorkbenchPage from './pages/MessageWorkbenchPage'
import OverviewPage from './pages/OverviewPage'
import SettingsPage from './pages/SettingsPage'
import TablesPage from './pages/TablesPage'
import TaskPage from './pages/TaskPage'
import TrafficWorkbenchPage from './pages/TrafficWorkbenchPage'

export const routes = [
  {
    path: '/',
    redirect: '/auto-leads',
  },
  {
    path: '/auto-leads',
    name: 'auto-leads',
    component: AutoAgentPage,
    meta: { title: 'AI自动拓客', subtitle: '输入目标后自动完成找竞品、筛客户和私信。', hint: '非技术用户的拓客入口' },
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
    path: '/traffic-auto',
    name: 'traffic-auto',
    component: AutoAgentPage,
    meta: { title: 'AI自动引流', subtitle: '输入目标后自动创建计划并启动抖音引流批次。', hint: '非技术用户的引流入口' },
  },
  {
    path: '/traffic-plans',
    name: 'traffic-plans',
    component: TrafficWorkbenchPage,
    meta: { title: '计划工作台', subtitle: '创建抖音引流计划，选择来源和动作组合。', hint: '先配置计划再启动批次' },
  },
  {
    path: '/traffic-monitor',
    name: 'traffic-monitor',
    component: TrafficWorkbenchPage,
    meta: { title: '执行监控', subtitle: '查看批次状态、用户可读日志和已处理视频。', hint: '失败原因会写清楚' },
  },
  {
    path: '/traffic-records',
    name: 'traffic-records',
    component: TrafficWorkbenchPage,
    meta: { title: '操作记录', subtitle: '查看每个视频的浏览和互动结果。', hint: '复盘每条视频' },
  },
  {
    path: '/traffic-settings',
    name: 'traffic-settings',
    component: TrafficWorkbenchPage,
    meta: { title: '引流设置', subtitle: '授权、登录态、文案图片和限额统一配置。', hint: '启动前先检查授权' },
  },
]

export const router = createRouter({
  history: createWebHistory(),
  routes,
})
