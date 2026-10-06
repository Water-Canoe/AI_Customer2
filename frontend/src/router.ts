import { createRouter, createWebHistory } from 'vue-router'

// 页面按路由异步加载，首次启动只解析应用壳和当前工作台。
const AiPage = () => import('./pages/AiPage')
const GlobalSettingsPage = () => import('./pages/GlobalSettingsPage')
const AutomationPlanPage = () => import('./pages/AutomationPlanPage')
const ContentWorkbenchPage = () => import('./pages/ContentWorkbenchPage')
const PublishCenterPage = () => import('./pages/PublishCenterPage')
const RuntimeCenterPage = () => import('./pages/RuntimeCenterPage')
const AcquisitionWorkbenchPage = () => import('./pages/AcquisitionWorkbenchPage')
const WorkspacePage = () => import('./pages/WorkspacePage')
const MessageWorkbenchPage = () => import('./pages/MessageWorkbenchPage')
const MessageBatchPage = () => import('./pages/MessageBatchPage')
const MessageSettingsPage = () => import('./pages/MessageSettingsPage')
const OverviewPage = () => import('./pages/OverviewPage')
const SettingsPage = () => import('./pages/SettingsPage')
const TablesPage = () => import('./pages/TablesPage')
const TrafficWorkbenchPage = () => import('./pages/TrafficWorkbenchPage')

export const routes = [
  {
    path: '/',
    redirect: '/workspace',
  },
  {
    path: '/workspace',
    name: 'workspace',
    component: WorkspacePage,
    meta: { title: '工作台', subtitle: '继续手头的工作，处理需要关注的任务。', module: '日常工作' },
  },
  {
    path: '/acquisition',
    name: 'acquisition',
    component: AcquisitionWorkbenchPage,
    meta: { title: '采集与结果', subtitle: '从来源到线索，查看每次采集的进度与产出。', module: '获客' },
  },
  {
    path: '/accounts',
    name: 'accounts',
    component: GlobalSettingsPage,
    meta: { title: '账号中心', subtitle: '管理执行账号、用途及平台登录状态。', module: '配置' },
  },
  {
    path: '/automation-plans',
    name: 'automation-plans',
    component: AutomationPlanPage,
    meta: { title: '自动化计划', subtitle: '统一安排获客、私信和引流任务，并按列表顺序共用浏览器队列。', hint: '把跨工作台的重复工作交给固定计划' },
  },
  {
    path: '/runtime',
    name: 'runtime-center',
    component: RuntimeCenterPage,
    meta: { title: '任务中心', subtitle: '查看执行阶段、处理异常并回到对应业务。', module: '执行' },
  },
  {
    path: '/global-settings',
    name: 'global-settings',
    component: GlobalSettingsPage,
    meta: { title: '系统设置', subtitle: '软件授权、环境与数据保护。', module: '配置' },
  },
  {
    path: '/overview',
    name: 'overview',
    component: OverviewPage,
    meta: { title: '来源证据', subtitle: '追溯客户、评论、内容和采集来源。', module: '获客' },
  },
  {
    path: '/ai',
    name: 'ai',
    component: AiPage,
    meta: { title: 'AI筛选', subtitle: '判断竞品账号与客户意向，保留每次分析依据。', module: '获客' },
  },
  {
    path: '/message-workbench',
    name: 'message-workbench',
    component: MessageWorkbenchPage,
    meta: { title: '客户跟进', subtitle: '按关键词推进客户私信、回访和成交状态。', hint: '把客户变成可执行跟进队列' },
  },
  {
    path: '/message-batches',
    name: 'message-batches',
    component: MessageBatchPage,
    meta: { title: '触达记录', subtitle: '查看批次与每位客户的实际执行结果。', module: '客户' },
  },
  {
    path: '/message-settings',
    name: 'message-settings',
    component: MessageSettingsPage,
    meta: { title: '私信设置', subtitle: '统一管理话术来源、人工确认、未回复提醒和发送频率额度。', hint: '所有私信入口共用同一套规则' },
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
    meta: { title: '拓客设置', subtitle: '配置拓客 AI、采集参数、自动筛选规则和 ICP 画像。', hint: '开始拓客前先把基础环境配好' },
  },
  {
    path: '/traffic-plans',
    name: 'traffic-plans',
    component: TrafficWorkbenchPage,
    meta: { title: '引流运营', subtitle: '维护计划，查看执行和互动结果。', module: '引流' },
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
    meta: { title: '引流设置', subtitle: '统一维护互动文案、图片、动作限额和运行环境。', hint: '账号登录与用途统一在全局设置管理' },
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
    meta: { title: '素材与音色', subtitle: '管理可复用的视频、图片、音频及我的音色。', module: '内容' },
  },
  {
    path: '/content-records',
    name: 'content-records',
    component: ContentWorkbenchPage,
    meta: { title: '作品库', subtitle: '继续草稿，查看生成历史并选择具体成品。', module: '内容' },
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
    meta: { title: '内容默认设置', subtitle: '配置文案、配音、视频默认参数和生成环境。', module: '内容' },
  },
]

export const router = createRouter({
  history: createWebHistory(),
  routes,
})
