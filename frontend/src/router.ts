import { createRouter, createWebHistory } from 'vue-router'

import AiPage from './pages/AiPage'
import AutomationPlanPage from './pages/AutomationPlanPage'
import ContentWorkbenchPage from './pages/ContentWorkbenchPage'
import PublishCenterPage from './pages/PublishCenterPage'
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
    redirect: '/tasks',
  },
  {
    path: '/tasks',
    name: 'tasks',
    component: TaskPage,
    meta: { title: '任务管理', subtitle: '选择拓客模式，生成采集任务，并自动导入项目库。', hint: '选择模式后直接开始采集' },
  },
  {
    path: '/automation-plans',
    name: 'automation-plans',
    component: AutomationPlanPage,
    meta: { title: '自动化计划', subtitle: '按星期和时间自动执行获客或私信任务。', hint: '把重复工作交给固定计划' },
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
    meta: { title: '设置', subtitle: '配置 AI 模型、自动化规则和 ICP 画像。', hint: '开始前先把基础环境配好' },
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
  {
    path: '/content-create',
    name: 'content-create',
    component: ContentWorkbenchPage,
    meta: { title: '视频创作', subtitle: '组合本地内容资产、AI文案、配音和字幕，生成可发布视频。', hint: '选择素材后创建视频任务' },
  },
  {
    path: '/content-assets',
    name: 'content-assets',
    component: ContentWorkbenchPage,
    meta: { title: '内容资产', subtitle: '统一管理客户自己的视频、图片和音频素材。', hint: '导入一次即可在多个视频中复用' },
  },
  {
    path: '/content-records',
    name: 'content-records',
    component: ContentWorkbenchPage,
    meta: { title: '生成记录', subtitle: '查看视频生成进度、结果、重试和发布状态。', hint: '生成和发布分别记录结果' },
  },
  {
    path: '/content-publish',
    name: 'content-publish',
    component: PublishCenterPage,
    meta: { title: '发布中心', subtitle: '管理平台账号，把视频和图文一键发布到抖音、快手和小红书。', hint: '先扫码登录并设置默认账号' },
  },
  {
    path: '/content-settings',
    name: 'content-settings',
    component: ContentWorkbenchPage,
    meta: { title: '内容设置', subtitle: '独立配置视频AI、语音、素材源、Whisper和自动发布。', hint: '不会读取拓客AI设置' },
  },
]

export const router = createRouter({
  history: createWebHistory(),
  routes,
})
