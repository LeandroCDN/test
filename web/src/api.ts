function normalizeBaseUrl(baseUrl: string): string {
  return baseUrl.replace(/\/+$/, "");
}

export interface StatusSnapshot {
  worker_status: string;
  entry_paused: boolean;
  uptime_seconds: number;
  stats: Stats;
  current_round: CurrentRound | null;
  latest_evaluation: LiveEvaluation | null;
}

export interface Stats {
  total_rounds: number;
  total_entries: number;
  total_btc_entries: number;
  total_eth_entries: number;
  total_sol_entries?: number;
  total_pnl: number;
  start_balance: number;
  current_balance: number;
}

export interface Settings {
  enabled_assets: string[];
  entry_start_seconds: number;
  live_monitor_start_seconds?: number;
  entry_check_interval_seconds: number;
  entry_check_interval_fast_seconds: number;
  entry_check_interval_fast_threshold_seconds: number;
  entry_balance_refresh_seconds: number;
  entry_profile_points: number[][];
  min_bet_usdc: number;
  bet_sizing_mode?: string;
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
  volatility_filter_enabled: boolean;
  volatility_refresh_seconds: number;
  volatility_interval: string;
  volatility_lookback_candles: number;
  volatility_low_threshold: number;
  volatility_high_threshold: number;
  volatility_extreme_threshold: number;
  volatility_min_odds_bump_high: number;
  volatility_min_odds_bump_extreme: number;
  volatility_capital_mult_low: number;
  volatility_capital_mult_high: number;
  volatility_capital_mult_extreme: number;
  fair_value_sigma_floor_pct?: number;
  fair_value_no_trade_band_pct?: number;
  fair_value_max_spread?: number;
  fair_value_requote_threshold?: number;
  fair_value_aggressive_edge?: number;
  fair_value_min_model_probability?: number;
  fair_value_min_market_probability?: number;
  ignore_edge_filter?: boolean;
  certainty_seconds_threshold?: number;
  certainty_avg_threshold?: number;
  rolling_window_seconds?: number;
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

export interface LiveEvaluationSide {
  fair_value?: number | null;
  buy_price?: number | null;
  edge?: number | null;
  spread?: number | null;
  avg_prob?: number | null;
  eligible?: boolean;
  reason?: string;
  checks?: Record<string, boolean>;
}

export interface RollingStats {
  samples?: number;
  window?: number;
  avg_up_model?: number;
  avg_down_model?: number;
  avg_up_market?: number;
  avg_down_market?: number;
}

export interface LiveEvaluationAsset {
  asset?: string;
  decision?: string;
  reason?: string;
  forced_side?: string | null;
  side?: string | null;
  seconds_left?: number;
  open_price?: number;
  current_price?: number;
  distance_pct?: number;
  vol_regime?: string;
  model_regime?: string;
  remaining_vol_pct?: number;
  min_edge?: number;
  rolling?: RollingStats;
  up?: LiveEvaluationSide;
  down?: LiveEvaluationSide;
}

export interface LiveEvaluation {
  decision?: string;
  reason?: string;
  asset?: string | null;
  side?: string | null;
  min_model_probability?: number;
  min_market_probability?: number;
  ignore_edge_filter?: boolean;
  live_monitor_start_seconds?: number;
  entry_start_seconds?: number;
  certainty_avg_threshold?: number;
  certainty_seconds_threshold?: number;
  rolling_window_seconds?: number;
  bet_sizing_mode?: string;
  fixed_bet_usdc?: number;
  assets?: Record<string, LiveEvaluationAsset>;
}

export interface BotEvent {
  id: string;
  ts: string;
  kind: string;
  level: string;
  data: Record<string, unknown>;
}

export interface BotApi {
  readonly baseUrl: string;
  health: () => Promise<{ ok: boolean }>;
  status: () => Promise<StatusSnapshot>;
  metrics: () => Promise<Stats>;
  events: (afterId?: string) => Promise<BotEvent[]>;
  startWorker: (dryRun?: boolean) => Promise<{ status: string; message: string }>;
  stopWorker: () => Promise<{ status: string; message: string }>;
  pauseEntry: () => Promise<{ entry_paused: boolean; message: string }>;
  resumeEntry: () => Promise<{ entry_paused: boolean; message: string }>;
  forceRedeem: () => Promise<{ message: string }>;
  getSettings: () => Promise<SettingsResponse>;
  updateSettings: (
    settings: Settings,
  ) => Promise<{ safe_mode: boolean; settings: Settings; requires_restart: boolean; message: string }>;
}

export function createBotApi(baseUrl: string): BotApi {
  const base = normalizeBaseUrl(baseUrl);

  async function get<T>(path: string): Promise<T> {
    const res = await fetch(`${base}${path}`);
    if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
    return res.json();
  }

  async function post<T>(path: string, body?: unknown): Promise<T> {
    const res = await fetch(`${base}${path}`, {
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

  return {
    baseUrl: base,
    health: () => get<{ ok: boolean }>("/health"),
    status: () => get<StatusSnapshot>("/status"),
    metrics: () => get<Stats>("/metrics"),
    events: (afterId?: string) => get<BotEvent[]>(`/events${afterId ? `?after=${afterId}` : ""}`),
    startWorker: (dryRun = false) =>
      post<{ status: string; message: string }>("/worker/start", { dry_run: dryRun }),
    stopWorker: () => post<{ status: string; message: string }>("/worker/stop"),
    pauseEntry: () => post<{ entry_paused: boolean; message: string }>("/worker/pause-entry"),
    resumeEntry: () => post<{ entry_paused: boolean; message: string }>("/worker/resume-entry"),
    forceRedeem: () => post<{ message: string }>("/worker/force-redeem"),
    getSettings: () => get<SettingsResponse>("/settings"),
    updateSettings: (settings: Settings) =>
      fetch(`${base}/settings`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ settings }),
      }).then(async (res) => {
        const body = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(body.detail || `${res.status} ${res.statusText}`);
        return body as {
          safe_mode: boolean;
          settings: Settings;
          requires_restart: boolean;
          message: string;
        };
      }),
  };
}

const defaultBase = import.meta.env.VITE_API_URL || "http://localhost:8000";
export const api = createBotApi(defaultBase);
