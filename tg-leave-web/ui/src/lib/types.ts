// 与后端 api 对齐的共享类型定义

export interface RiskItem {
  code: string;
  short: string;
  why: string;
  raw: string;
}

export interface Dialog {
  id: number;
  title: string;
  username: string | null;
  kind: string;
  kind_key: string;
  peer_key: string;
  risk: RiskItem[];
  risk_cell: string;
  members: number | null;
  posts: number | null;
  last_ts: number | null;
  active_days: number | null;
  active_text: string;
  role: string;
  folders: string;
  top_message?: number; // 最后一条消息 ID（私有频道/超级群走 tg://privatepost 唤起用）
  // A 组零成本字段（拉列表时 API 自带）
  unread?: number;
  pinned?: boolean;
  archived?: boolean;
  muted?: boolean;
  verified?: boolean;
  signature?: boolean;
  gigagroup?: boolean;
  has_photo?: boolean;
  created_ts?: number | null;
  status: string;
  whitelisted: boolean;
  is_protected: boolean;
  is_member: boolean;
  can_destroy: boolean;
}

export interface MediaSummary {
  scanned: number;
  limit: number;
  counts: Record<string, number>;
  total?: number;        // 媒体总数 = 非文本桶之和（v3.19.6 后端下发；缺失时前端按 counts 兜底计算）
  ts: number;
  error?: string;
  exhausted?: boolean;   // 会话历史不足 limit，已扫到底（对任意更大深度免重扫）
  mode?: "scan" | "totals";  // totals = 全部历史真实总数（对齐官方客户端媒体面板，v3.20.0）
}

export interface DialogsResp {
  items: Dialog[];
  busy: boolean;
  quota_used: number;
  premium: boolean;
  quota_limit?: number;
  quota_text?: string;
  fetch_error?: string;
}

export interface Account {
  key: string;
  phone: string;
  nick: string;
  proxy: string;
  api_id: string;
}

export interface AccountsResp {
  current: string;
  accounts: Account[];
  logged_in: boolean;
}

export interface VersionNote {
  version: string;
  date: string;
  notes: string[];
}

export interface VersionResp {
  name: string;
  version: string;
  changelog: VersionNote[];
}

export interface Device {
  hash: number;
  current: boolean;
  device: string;
  system: string;
  ip: string;
  country: string;
  created: string;
  active: string;
}

export interface Folder {
  id: number;
  title: string;
  editable: boolean;
  include: string[];
  exclude: string[];
  pinned: string[];
  rules: string;
}

export interface HistoryItem {
  action: string;
  peer_key?: string;
  title?: string;
  value?: string;
  note?: string;
  t: string;
}

export interface ActionResult {
  peer_key: string;
  ok: boolean;
  gone?: boolean;
  note?: string;
}

export interface LogEntry {
  t: string;
  ts: number;
  level: string;
  source: string;
  message: string;
}
