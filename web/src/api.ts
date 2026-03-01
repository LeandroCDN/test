const BASE = import.meta.env.VITE_API_URL || "http://localhost:8000";

async function get<T>(path: string): Promise<T> {
  const res = await fetch(`${BASE}${path}`);
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  return res.json();
}

async function post<T>(path: string, body?: unknown): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || `${res.status} ${res.statusText}`);
  }
  return res.json();
}

export interface StatusSnapshot {
  worker_status: string;
  entry_paused: boolean;
  uptime_seconds: number;
  stats: Stats;
  current_round: CurrentRound | null;
}

export interface Stats {
  total_rounds: number;
  total_entries: number;
  total_btc_entries: number;
  total_eth_entries: number;
  total_wins: number;
  total_losses: number;
  total_unsettled: number;
  total_stop_exits: number;
  total_stop_wins: number;
  total_stop_losses: number;
  total_skipped: number;
  total_pnl: number;
  start_balance: number;
  current_balance: number;
}

export interface Settings {
  enabled_assets: string[];
  entry_start_seconds: number;
  entry_check_interval_seconds: number;
  entry_check_interval_fast_seconds: number;
  entry_check_interval_fast_threshold_seconds: number;
  entry_balance_refresh_seconds: number;
  entry_profile_points: number[][];
  min_bet_usdc: number;
  max_odds: number;
  fill_slippage_warn_pct: number;
  poll_interval_seconds: number;
  post_resolution_buffer_seconds: number;
  wait_log_interval_seconds: number;
  auto_redeem_enabled: boolean;
  auto_redeem_max_conditions_per_cycle: number;
  auto_redeem_attempt_interval_seconds: number;
  auto_redeem_probe_interval_seconds: number;
  auto_redeem_rate_limit_buffer_seconds: number;
  stop_loss_enabled: boolean;
  stop_loss_pct: number;
  stop_loss_poll_seconds: number;
  stop_loss_confirm_ticks: number;
  stop_loss_retry_seconds: number;
}

export interface SettingsResponse {
  safe_mode: boolean;
  settings: Settings;
  active_settings: Settings | null;
  requires_restart: boolean;
  note?: string;
}

export interface CurrentRound {
  round: number;
  assets: string[];
  seconds_left: number;
}

export interface BotEvent {
  id: string;
  ts: string;
  kind: string;
  level: string;
  data: Record<string, unknown>;
}

export const api = {
  health: () => get<{ ok: boolean }>("/health"),
  status: () => get<StatusSnapshot>("/status"),
  metrics: () => get<Stats>("/metrics"),
  events: (afterId?: string) =>
    get<BotEvent[]>(`/events${afterId ? `?after=${afterId}` : ""}`),
  startWorker: (dryRun = false) =>
    post<{ status: string; message: string }>("/worker/start", { dry_run: dryRun }),
  stopWorker: () =>
    post<{ status: string; message: string }>("/worker/stop"),
  pauseEntry: () =>
    post<{ entry_paused: boolean; message: string }>("/worker/pause-entry"),
  resumeEntry: () =>
    post<{ entry_paused: boolean; message: string }>("/worker/resume-entry"),
  forceRedeem: () =>
    post<{ message: string }>("/worker/force-redeem"),
  getSettings: () => get<SettingsResponse>("/settings"),
  updateSettings: (settings: Settings) =>
    fetch(`${BASE}/settings`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ settings }),
    }).then(async (res) => {
      const body = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(body.detail || `${res.status} ${res.statusText}`);
      return body as { safe_mode: boolean; settings: Settings; requires_restart: boolean; message: string };
    }),
};
