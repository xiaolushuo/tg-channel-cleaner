import type {
  AccountsResp, DialogsResp, VersionResp, Device, Folder, HistoryItem,
  ActionResult, LogEntry,
} from "./types";

const BASE = "/api";

async function req<T>(method: string, path: string, body?: unknown, signal?: AbortSignal): Promise<T> {
  const res = await fetch(BASE + path, {
    method,
    headers: { "Content-Type": "application/json" },
    body: body !== undefined ? JSON.stringify(body) : undefined,
    signal,
  });
  let data: any = null;
  const text = await res.text();
  if (text) {
    try { data = JSON.parse(text); } catch { data = text; }
  }
  if (!res.ok) {
    const msg = (data && (data.error || data.detail)) || `请求失败 (${res.status})`;
    throw new Error(msg);
  }
  return data as T;
}

export const api = {
  // 版本
  version: () => req<VersionResp>("GET", "/version"),

  // 账号
  accounts: () => req<AccountsResp>("GET", "/accounts"),
  proxyTest: (proxy: string) => req<{ ok: boolean; note: string }>("POST", "/proxy/test", { proxy }),
  upsertAccount: (a: { api_id: string; api_hash: string; phone: string; proxy: string; nick: string }) =>
    req<{ key: string; ok: boolean }>("POST", "/accounts", a),
  switchAccount: (key: string) => req<{ ok: boolean }>("POST", "/accounts/switch", { key }),
  removeAccount: (key: string, delete_files = false) =>
    req<{ ok: boolean }>("POST", "/accounts/remove", { key, proxy: delete_files ? "DELETE_FILES" : "" }),

  // 旧版登录态
  legacyDiscover: (dir = "") => req<{ items: any[]; scanned: string[] }>(`GET`, `/legacy/discover${dir ? `?dir=${encodeURIComponent(dir)}` : ""}`),
  legacyImport: (dir = "") => req<{ imported: { ok: boolean; key: string; note: string; phone: string; api_id: string; api_hash: string; proxy: string; nick: string }[]; failed: { note: string }[] }>("POST", "/legacy/import", { dir }),

  // 登录
  loginStart: (p: { key?: string; demo?: boolean; api_id?: string; api_hash?: string; phone?: string; proxy?: string }) =>
    req<{ state: string }>("POST", "/login/start", p),
  loginState: () => req<{ state: string; logged_in: boolean; user: any; demo?: boolean; note?: string }>("GET", "/login/state"),
  loginCode: (code: string) => req<{ ok: boolean }>("POST", "/login/code", { key: code }),
  loginPassword: (pwd: string) => req<{ ok: boolean }>("POST", "/login/password", { key: pwd }),

  // 会话
  dialogs: () => req<DialogsResp>("GET", "/dialogs"),
  refreshDialogs: () => req<{ ok: boolean }>("POST", "/dialogs/refresh"),
  stats: (peer_keys: string[], force = false, conc = 1, ttl_hours = 24, use_cache = true, signal?: AbortSignal) =>
    req<{ stats: Record<string, any> }>("POST", "/stats",
      { peer_keys, force, conc, ttl_hours, use_cache }, signal),
  statsCacheInfo: () => req<{ count: number }>("GET", "/stats/cache"),
  // 媒体统计（v3.17.0）：扫描会话最近 N 条消息按类型计数
  media: (peer_keys: string[], limit = 500, force = false, conc = 2, ttl_hours = 24, use_cache = true, signal?: AbortSignal, mode: "scan" | "totals" = "scan") =>
    req<{ media: Record<string, any> }>("POST", "/media",
      { peer_keys, limit, force, conc, ttl_hours, use_cache, mode }, signal),
  mediaAll: () => req<{ media: Record<string, any> }>("GET", "/media"),
  mediaProgress: () => req<{ done: number; total: number; running: boolean }>("GET", "/media/progress"),
  statsProgress: () => req<{ done: number; total: number; running: boolean }>("GET", "/stats/progress"),
  clearStatsCache: () => req<{ cleared: number }>("DELETE", "/stats/cache"),

  // 操作
  leave: (peer_keys: string[]) => req<{ results: ActionResult[] }>("POST", "/actions/leave", { peer_keys }),
  leaveMany: (peer_keys: string[], interval = 0, jitter = 0) =>
    req<{ results: ActionResult[]; remaining: { peer_key: string; title: string }[]; interrupted: boolean; stopped?: boolean }>(
      "POST", "/actions/leave_many", { peer_keys, interval, jitter }),
  resumePending: (interval = 0, jitter = 0) =>
    req<{ results: ActionResult[]; remaining: { peer_key: string; title: string }[]; interrupted: boolean; note?: string }>(
      "POST", "/actions/resume", { interval, jitter }),
  pending: () => req<{ keys: string[]; items: { peer_key: string; title: string }[] }>("GET", "/pending"),
  clearPending: () => req<{ ok: boolean }>("DELETE", "/pending"),
  destroy: (peer_keys: string[], confirm_word: string) =>
    req<{ results: ActionResult[] }>("POST", "/actions/destroy", { peer_keys, confirm_word }),
  rejoin: (kind: string, value: string) => req<{ state: string; note: string }>("POST", "/actions/rejoin", { kind, value }),
  rejoinRaw: (raw: string) => req<{ state: string; note: string }>("POST", "/actions/rejoin", { kind: "username", value: "", raw }),

  // 设备
  devices: () => req<{ devices: Device[] }>("GET", "/devices"),
  killDevices: (hashes: number[]) => req<{ ok: boolean }>("POST", "/devices/kill", { hashes }),

  // 分组
  folders: () => req<{ folders: Folder[] }>("GET", "/folders"),
  applyFolder: (folder_id: number, peer_keys: string[], add: boolean) =>
    req<{ ok: number; skipped: number; note: string }>("POST", "/folders/apply", { folder_id, peer_keys, add }),

  // 邀请链接（无用户名会话跳转客户端）
  exportInvite: (peer_key: string) =>
    req<{ link: string; tg: string }>("POST", "/invite", { peer_key }),

  // 静音 / 归档（逐条如实回报：ok=成功数，failed=失败明细）
  mute: (peer_keys: string[], on: boolean) =>
    req<{ ok: number; failed: { peer_key: string; note: string }[]; total: number }>("POST", "/mute", { peer_keys, on }),
  archive: (peer_keys: string[], on: boolean) =>
    req<{ ok: number; failed: { peer_key: string; note: string }[]; total: number }>("POST", "/archive", { peer_keys, on }),

  // 快照对比（保存当前列表 → 与另一槽位对比，看谁消失了 / 改名了 / 涨跌了）
  snapshotSlots: () => req<{ slots: ({ taken: string; count: number } | null)[] }>("GET", "/snapshot/slots"),
  saveSnapshot: (slot: number) =>
    req<{ ok: boolean; taken: string; count: number }>("POST", "/snapshot/save", { slot }),
  compareSnapshots: (slot_a: number, slot_b: number) =>
    req<{
      diff: {
        added: string[]; removed: string[];
        renamed: [string, string, string][];
        member_changed: [string, number, number][];
        risk_new: [string, string][];
      };
    }>("POST", "/snapshot/compare", { slot_a: slot_a, slot_b: slot_b }),

  // 历史（操作记录，供「🕘 历史」菜单使用）
  history: () => req<{ items: HistoryItem[] }>("GET", "/history"),
  clearHistory: () => req<{ cleared: number }>("DELETE", "/history"),

  // 白名单
  whitelist: (peer_keys: string[], on: boolean) => req<{ ok: boolean }>("POST", "/whitelist", { peer_keys, on }),

  // 导出（POST 返回文件流 -> 触发下载）
  exportFile: async (
    fmt: "csv" | "xlsx",
    peer_keys: string[],
    scope_label = "",
    quota_text = "",
  ) => {
    const res = await fetch(BASE + "/export", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ fmt, peer_keys, scope_label, quota_text }),
    });
    if (!res.ok) throw new Error(`导出失败 (${res.status})`);
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = fmt === "csv" ? "tg_dialogs.csv" : "tg_dialogs.xlsx";
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 2000);
  },

  // 停止批量任务（协作式中止，剩余项留在断点）
  cancelBatch: () => req<{ ok: boolean }>("POST", "/actions/cancel", {}),

  // 日志
  logs: (limit = 500) => req<{ logs: LogEntry[] }>("GET", `/logs?limit=${limit}`),
};
