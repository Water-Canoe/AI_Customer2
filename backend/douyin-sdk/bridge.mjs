/** 每个业务账号一个进程；标准输出只承载 JSON Lines，不输出 Cookie 或协议日志。 */
import { createInterface } from 'node:readline';
import { readdir, readFile } from 'node:fs/promises';
import { join } from 'node:path';

const args = Object.fromEntries(process.argv.slice(2).map(value => {
  const index = value.indexOf('=');
  return [value.slice(0, index), value.slice(index + 1)];
}));
const dataDir = args['--data-dir'];
const businessAccountId = args['--account-id'];
// 测试可注入离线假 SDK；生产调用始终使用锁定的 npm 依赖。
const sdk = await import(args['--sdk-module'] || 'douyin-im');
sdk.configureLogger?.({ level: 'silent' });
const client = sdk.createClient({ dataDir, autoLoad: false });
let account;
let loginTask;
let expectedUid = '';
let allowQr = false;
let closing = false;
let state = 'disconnected';

// 固定 douyin-im@1.1.0 的实例内部方法：在正式凭据写盘前拒绝与原绑定不同的扫码 UID。
const originalPromote = client.store.promote;
client.store.promote = function (...values) {
  if (expectedUid && String(values[1]) !== expectedUid) throw fail('扫码账号与原绑定不一致，请使用原抖音账号重新扫码');
  return originalPromote.apply(this, values);
};

/** 只序列化显式构造的业务数据，不能直接序列化 SDK 账号实例。 */
function output(value) { process.stdout.write(`${JSON.stringify(value)}\n`); }
function event(type, data = {}) { output({ event: type, data, account_id: businessAccountId }); }
function fail(message, code = 'operation_failed', result) {
  return Object.assign(new Error(message), { bridgeCode: code, result });
}
function setState(value, error = '') { state = value; event('state', { state: value, error }); }
function numericUid(value) {
  const uid = String(value ?? '');
  if (!/^[0-9]+$/.test(uid) || BigInt(uid) <= 0n) throw fail('请输入有效的抖音数字用户 ID');
  return uid;
}

/** 从服务器会话参与者确定客户，拒绝把其它账号或群聊绑定到当前账号。 */
function peerForThread(threadId) {
  const parts = String(threadId).split(':');
  if (parts.length !== 4 || parts[0] !== '0' || parts[1] !== '1') return null;
  const own = new Set([account?.uid, account?.imUid].filter(Boolean));
  const left = numericUid(parts[2]), right = numericUid(parts[3]);
  if (left === right || (!own.has(left) && !own.has(right))) throw fail('会话参与者与当前账号不一致');
  return own.has(left) ? right : left;
}

/** 保留平台时间原值；无法获取时间时明确标为接收时间。 */
function normalizeMessage(message, history = false, contact) {
  const thread = String(message.threadId || '');
  const peer = peerForThread(thread);
  if (!peer || Number(message.messageType ?? message.msgType) >= 50000) return null;
  const id = String(message.serverMessageId || message.msgId || '');
  if (!id || id === '0') return null;
  const sender = numericUid(message.senderUid);
  const own = new Set([account.uid, account.imUid].filter(Boolean));
  if (sender !== peer && !own.has(sender)) throw fail('消息发送者与当前会话不一致');
  const rawText = typeof message.rawContent === 'string' ? message.rawContent : String(message.content || '');
  let raw;
  try { raw = JSON.parse(rawText); } catch { raw = { unparsed_content: rawText }; }
  if (!raw || Array.isArray(raw) || typeof raw !== 'object') raw = { unparsed_content: rawText };
  const type = Number(message.messageType ?? message.msgType);
  // 固定 SDK 同时支持 JSON text/content/display_name 和原始纯文本正文。
  const bodyText = raw.text ?? raw.content ?? raw.display_name ?? raw.unparsed_content ?? '';
  const text = type === 7 ? (typeof message.text === 'string' ? message.text : String(bodyText)) : '';
  const labels = { 5: '[表情]', 17: '[语音]', 27: '[图片]', 8: '[分享视频]' };
  const rawTime = message.createTime;
  const parsed = Number(rawTime);
  // 与 SDK BaseEvent.normalizeEventTime 使用相同的秒/毫秒界限，微秒异常值不能变成遥远未来历史。
  const platformTime = parsed > 10_000_000_000 ? parsed : parsed * 1000;
  const validTime = Number.isFinite(parsed) && platformTime >= Date.UTC(2016, 0, 1) && platformTime <= Date.now() + 86_400_000;
  if (history && !validTime) throw fail('历史消息缺少平台时间，未保存不完整记录');
  const timestamp = validTime ? platformTime : Date.now();
  const time = new Date(timestamp).toISOString();
  return {
    platform_message_id: id, conversation_id: thread,
    sdk_uid: String(account.uid), own_user_id: String(account.imUid || account.uid),
    conversation_short_id: String(message.conversationShortId || contact?.conversationShortId || ''),
    peer_user_id: peer, sender_user_id: sender, message_type: String(type),
    content: typeof text === 'string' && text ? text : (type === 7 ? '[文本消息暂无法解析，请在抖音查看]' : labels[type] || `[消息类型 ${type}]`),
    raw_content: { ...raw, sdk: 'douyin-im', client_message_id: String(message.clientMessageId || ''),
      platform_create_time: rawTime == null ? '' : String(rawTime), time_source: validTime ? 'platform' : 'received' },
    direction: own.has(sender) ? 'outgoing' : 'incoming',
    sent_at: time, created_at: time, time_source: validTime ? 'platform' : 'received', history,
    nickname: String(contact?.nickname || ''), avatar: String(contact?.avatar || ''),
  };
}

/** 凭据目录只能绑定一个已保存账号；不读取或返回 Cookie。 */
async function savedUid() {
  let entries;
  try { entries = await readdir(join(dataDir, 'accounts'), { withFileTypes: true }); }
  catch (error) { if (error.code === 'ENOENT') return ''; throw error; }
  const ids = [];
  for (const entry of entries) {
    if (!entry.isDirectory() || entry.name === '_pending') continue;
    try {
      const saved = JSON.parse(await readFile(join(dataDir, 'accounts', entry.name, 'account.json'), 'utf8'));
      if (String(saved.platformUid) !== entry.name) throw fail('SDK 保存的账号身份不一致');
      ids.push(numericUid(entry.name));
    } catch (error) { if (error.code !== 'ENOENT') throw error; }
  }
  if (ids.length > 1) throw fail('当前业务账号目录包含多个抖音身份，请检查账号隔离');
  return ids[0] || '';
}

client.on('system.login.qrcode', ({ account: current, qrcodeBase64, expireTime }) => {
  if (!allowQr) { void current.logout(); return; }
  event('qrcode', { image_base64: qrcodeBase64, expire_time: expireTime });
  void current.continueLogin().catch(() => setState('error', '扫码登录未完成，请重试'));
});
client.on('system.login.verification', async ({ verification }) => {
  try {
    const url = await verification.openUrl({ openBrowser: false });
    event('verification', { url, methods: verification.methods, operation: verification.operation });
    void verification.open({ openBrowser: false }).catch(() => setState('error', '抖音安全验证未完成'));
  } catch { setState('error', '无法打开抖音安全验证页面'); }
});
client.on('system.online', ({ account: current }) => {
  if (expectedUid && String(current.uid) !== expectedUid) {
    void current.logout();
    setState('error', '登录身份与当前业务账号保存的身份不一致');
    return;
  }
  setState('connected');
});
client.on('system.reconnecting', () => setState('reconnecting', '私信连接中断，正在自动重连'));
client.on('system.offline', () => setState('disconnected'));
client.on('system.login.error', () => setState('error', '抖音登录失败，请重新扫码或完成安全验证'));
client.on('system.handler.error', () => setState('error', '抖音消息处理失败，请重新连接'));
client.on('message', message => {
  const value = normalizeMessage(message, false, message.stranger || message.friend);
  if (value) event('message', value);
});
client.on('notice.message.batch-update', notification => {
  for (const update of notification.updates) {
    for (const message of update.messages) {
      const value = normalizeMessage(message, true);
      if (value) event('message', value);
    }
  }
});

/** 恢复与扫码共享生命周期；非交互恢复遇到过期登录会拒绝，不隐式生成二维码。 */
async function login(params = {}) {
  if (closing) throw fail('当前账号连接正在停止');
  if (params.expected_uid) expectedUid = numericUid(params.expected_uid);
  if (account?.online) {
    if (expectedUid && String(account.uid) !== expectedUid) throw fail('账号身份不一致');
    return identity();
  }
  if (loginTask) return loginTask;
  allowQr = params.interactive === true;
  const uid = await savedUid();
  if (expectedUid && uid && expectedUid !== uid) throw fail('SDK 保存的账号与当前业务账号不一致');
  if (!uid && !allowQr) throw fail('请先点击“扫码登录私信”，完成首次登录', 'login_required');
  account ??= client.createAccount(uid ? { accountId: uid } : { login: { method: 'qr' } });
  setState('connecting');
  const task = account.login().then(() => {
    if (closing || !account.online || (expectedUid && String(account.uid) !== expectedUid)) throw fail('登录已取消或账号身份不一致');
    return identity();
  });
  loginTask = task;
  try { return await task; } finally { if (loginTask === task) loginTask = undefined; }
}
function identity() {
  return { account_id: businessAccountId, sdk: 'douyin-im', sdk_uid: String(account?.uid || ''),
    nickname: String(account?.nickname || ''), state };
}

/** 直接复用 SDK 创建私聊，允许尚未出现在 SDK 缓存中的数字 UID 客户。 */
async function contactForUid(value, create = true) {
  const uid = numericUid(value);
  if (uid === account.uid || uid === account.imUid) throw fail('不能向当前账号自己发送私信');
  let contact = account.pickStranger(uid) || account.pickFriend(uid);
  if (contact || !create) return contact;
  const response = await account.im.createPrivateConversation([account.imUid || account.uid, uid]);
  if (response.statusCode !== 0) throw fail('平台未允许创建当前客户的私聊会话');
  const info = response.conversation;
  if (!info?.conversationShortId || peerForThread(info.conversationId) !== uid) throw fail('平台创建的私聊会话与目标客户不一致');
  account.applyConversationInfo(info);
  contact = account.pickStranger(uid) || account.pickFriend(uid);
  if (!contact) throw fail('平台会话无法映射到当前客户');
  return contact;
}

/** 完整翻页普通会话；保留 SDK 返回的会话地址，不手工拼协议参数。 */
async function listContacts() {
  const contacts = new Map();
  let cursor = '0';
  const seen = new Set();
  for (;;) {
    const page = await account.im.listThreads({ count: 50, cursor });
    if (page.statusCode !== 0) throw fail('抖音会话列表读取失败');
    for (const info of page.conversations) {
      const peer = peerForThread(info.conversationId);
      if (!peer) continue;
      account.applyConversationInfo(info);
      const contact = await contactForUid(peer, false);
      if (contact) contacts.set(info.conversationId, contact);
    }
    if (!page.hasMore) break;
    if (!page.cursor || seen.has(page.cursor) || page.cursor === cursor) throw fail('抖音会话分页游标未前进');
    seen.add(cursor); cursor = page.cursor;
  }
  // StrangerSync 的公开返回值没有 hasMore；直接复用 SDK 已实现的陌生人分页接口。
  let version = '9223372036854775807';
  for (;;) {
    const page = await account.im.getRecentStrangerMessages({ latestStrangerVersion: version, earliestStrangerVersion: '0', inboxType: 1 });
    if (page.statusCode !== 0) throw fail('抖音陌生人会话读取失败');
    for (const row of page.messages) {
      const peer = peerForThread(row.conversationId);
      if (!peer) continue;
      const cached = account.pickStranger(peer);
      const contact = cached?.threadId === row.conversationId ? cached
        : sdk.Stranger.bind(peer, row.conversationId, row.conversationShortId, account, { inboxType: 1 });
      account.rememberStranger(contact);
      contacts.set(row.conversationId, contact);
    }
    if (!page.hasMore) break;
    if (BigInt(page.nextStrangerVersion) >= BigInt(version) || BigInt(page.nextStrangerVersion) <= 0n) throw fail('抖音陌生人分页游标未前进');
    version = page.nextStrangerVersion;
  }
  return contacts;
}

/** 每页合并真实服务器 ID；历史只返回给宿主入库，不产生自动回复。 */
async function history(params) {
  const contacts = await listContacts();
  const peer = params.peer_user_id ? numericUid(params.peer_user_id) : '';
  const messages = new Map();
  for (const [thread, contact] of contacts) {
    if (peer && peerForThread(thread) !== peer) continue;
    let cursor;
    const seen = new Set();
    for (;;) {
      if (closing) throw fail('历史同步已取消');
      const page = await contact.getHistory({ count: 50, direction: 'older', ...(cursor ? { cursor } : {}) });
      if (page.statusCode !== 0) throw fail('抖音历史消息读取失败');
      for (const message of page.messages) {
        const value = normalizeMessage(message, true, contact);
        if (value) messages.set(`${value.conversation_id}:${value.platform_message_id}`, value);
      }
      if (!page.hasMore) break;
      if (!page.cursor || seen.has(page.cursor) || page.cursor === cursor) throw fail('抖音历史消息分页游标未前进');
      seen.add(page.cursor); cursor = page.cursor;
    }
  }
  return [...messages.values()].sort((left, right) => left.sent_at.localeCompare(right.sent_at));
}

/** RPC 允许并发接收 close，使扫码、网络等待和历史同步都能及时取消。 */
async function dispatch(method, params = {}) {
  if (method === 'status') return { ...identity(), saved_uid: await savedUid() };
  if (method === 'close') {
    closing = true;
    await client.logout();
    return { closed: true };
  }
  if (method === 'login') return login(params);
  await login({ expected_uid: params.expected_uid });
  if (method === 'send') {
    const uid = numericUid(params.peer_user_id);
    if (typeof params.text !== 'string' || !params.text.trim() || params.text.length > 2000) throw fail('私信内容不能为空且不能超过2000字');
    if (params.conversation_id && peerForThread(params.conversation_id) !== uid) throw fail('私信会话与目标客户不一致');
    const contact = await contactForUid(uid);
    const result = { conversation_id: contact.threadId, conversation_short_id: String(contact.conversationShortId), ticket: '',
      sdk_uid: String(account.uid), own_user_id: String(account.imUid || account.uid),
      client_message_id: '', platform_message_id: '' };
    try {
      const response = await contact.sendMsg(params.text);
      return { ...result, client_message_id: response.clientMessageId || '', platform_message_id: response.serverMessageId || '', status: 'accepted', error: '' };
    } catch (error) {
      if (error.name === 'SendMessageError') return { ...result, client_message_id: error.response?.clientMessageId || '', status: 'failed', error: '平台拒绝了本次私信发送' };
      if (error.name === 'ImProtoTransportError' && Number(error.cmd) === 100) throw fail('发送结果待确认，请先确认对方是否收到', 'send_uncertain', { ...result, status: 'uncertain' });
      throw fail('私信发送未完成，请核对对方是否收到', 'send_uncertain', { ...result, status: 'uncertain' });
    }
  }
  if (method === 'profile') {
    const uid = numericUid(params.peer_user_id);
    let contact = await contactForUid(uid, false);
    if (!contact) { await listContacts(); contact = await contactForUid(uid, false); }
    if (!contact) throw fail('未找到该客户的会话资料，请先同步历史消息');
    if (!contact.secUid) await contact.refresh();
    const profile = await contact.getProfile();
    if (String(profile.uid) !== uid) throw fail('平台用户资料与当前客户不一致');
    return { peer_user_id: uid, nickname: String(profile.nickname || ''), avatar: String(profile.avatarThumb || '') };
  }
  if (method === 'history') return history(params);
  throw fail('未知 SDK 操作');
}

const input = createInterface({ input: process.stdin });
input.on('line', line => {
  let request;
  try { request = JSON.parse(line); }
  catch { output({ id: null, error: { code: 'invalid_request', message: '无效 JSON 请求' } }); return; }
  if (request.account_id !== businessAccountId) {
    output({ id: request.id, error: { code: 'account_mismatch', message: '请求与 SDK 业务账号不一致' } }); return;
  }
  void dispatch(request.method, request.params).then(
    result => { output({ id: request.id, result }); if (request.method === 'close') input.close(); },
    error => output({ id: request.id, error: { code: error.bridgeCode || 'operation_failed', message: error.bridgeCode ? error.message : '抖音 SDK 操作失败，请检查网络或重新登录', ...(error.result ? { result: error.result } : {}) } }),
  );
});
input.on('close', () => {
  closing = true;
  // 宿主退出后不留下后台账号连接，网络清理最多等待一秒。
  const timer = setTimeout(() => process.exit(0), 1000);
  void client.logout().finally(() => { clearTimeout(timer); process.exit(0); });
});
event('ready', { sdk: 'douyin-im', version: '1.1.0' });
