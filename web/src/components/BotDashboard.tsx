import { useEffect, useState } from "react";
import type { BotApi, BotEvent, LiveEvaluation, LiveEvaluationAsset, LiveEvaluationSide, RollingStats, Settings } from "../api";
import { useEvents, useStatus } from "../hooks";

type Bot2SignalData = {
  asset?: unknown;
  side?: unknown;
  fair_value?: unknown;
  buy_price?: unknown;
  edge?: unknown;
  spread?: unknown;
  seconds_left?: unknown;
  open_price?: unknown;
  current_price?: unknown;
  distance_pct?: unknown;
  vol_regime?: unknown;
  model_regime?: unknown;
  remaining_vol_pct?: unknown;
};

type CheckRowProps = {
  label: string;
  ok: boolean | undefined;
};

function StatusBadge({ status }: { status: string }) {
  const colorMap: Record<string, string> = {
    running: "badge-green",
    stopped: "badge-red",
    starting: "badge-yellow",
    stopping: "badge-yellow",
  };
  return <span className={`badge ${colorMap[status] || "badge-gray"}`}>{status}</span>;
}

function formatUptime(seconds: number): string {
  if (seconds <= 0) return "--";
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = Math.floor(seconds % 60);
  if (h > 0) return `${h}h ${m}m`;
  if (m > 0) return `${m}m ${s}s`;
  return `${s}s`;
}

function pnlClass(val: number): string {
  if (val > 0) return "text-green";
  if (val < 0) return "text-red";
  return "";
}

function EventLevelDot({ level }: { level: string }) {
  const cls = level === "error" ? "dot-red" : level === "warn" ? "dot-yellow" : "dot-blue";
  return <span className={`dot ${cls}`} />;
}

function formatEventTime(iso: string): string {
  try {
    const d = new Date(iso);
    return d.toLocaleTimeString();
  } catch {
    return iso;
  }
}

function asNumber(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function asText(value: unknown): string {
  return typeof value === "string" ? value : String(value ?? "");
}

function formatProbability(value: unknown): string {
  const n = asNumber(value);
  return n === null ? "--" : `${(n * 100).toFixed(1)}%`;
}

function formatPercent(value: unknown, digits = 2): string {
  const n = asNumber(value);
  return n === null ? "--" : `${n.toFixed(digits)}%`;
}

function formatPrice(value: unknown, digits = 2): string {
  const n = asNumber(value);
  return n === null ? "--" : `$${n.toFixed(digits)}`;
}

function formatEdgePoints(value: unknown): string {
  const n = asNumber(value);
  return n === null ? "--" : `${(n * 100).toFixed(1)} pts`;
}

function bot2SignalTone(edge: number | null): "good" | "warn" | "neutral" {
  if (edge === null) return "neutral";
  if (edge >= 0.08) return "good";
  if (edge >= 0.04) return "warn";
  return "neutral";
}

function bot2SignalSummary(data: Bot2SignalData): string {
  const edge = asNumber(data.edge);
  const secondsLeft = asNumber(data.seconds_left);
  const asset = assetLabel(data.asset);
  const side = asText(data.side).toUpperCase();
  const strength =
    edge === null ? "No clear edge" : edge >= 0.08 ? "Strong discount" : edge >= 0.04 ? "Decent discount" : "Thin edge";
  const timing =
    secondsLeft === null ? "" : secondsLeft <= 5 ? " in the final seconds" : secondsLeft <= 15 ? " late in the candle" : "";
  return `${asset} ${side || "signal"} looked underpriced. ${strength}${timing}.`;
}

function liveDecisionSummary(evaluation: LiveEvaluation | null | undefined): string {
  if (!evaluation) return "Waiting for the next live evaluation.";
  if (evaluation.decision === "eligible") {
    return `${asText(evaluation.asset).toUpperCase() || "Asset"} ${asText(evaluation.side).toUpperCase() || "signal"} is ready and passes all entry filters.`;
  }
  return asText(evaluation.reason) || "Watching for a better setup.";
}

function liveDecisionTone(evaluation: LiveEvaluation | null | undefined): "good" | "warn" | "neutral" {
  if (!evaluation) return "neutral";
  if (evaluation.decision === "eligible") return "good";
  if (evaluation.side) return "warn";
  return "neutral";
}

function checkLabel(ok: boolean | undefined): string {
  if (ok === true) return "OK";
  if (ok === false) return "Fail";
  return "--";
}

function CheckRow({ label, ok }: CheckRowProps) {
  const tone = ok === true ? "good" : ok === false ? "danger" : "neutral";
  return (
    <div className="signal-check-row">
      <span>{label}</span>
      <strong className={`signal-check-${tone}`}>{checkLabel(ok)}</strong>
    </div>
  );
}

function assetLabel(asset: unknown): string {
  return asText(asset).toUpperCase() || "--";
}


function SidePanel({ label, side, rolling_key, rolling }: {
  label: string;
  side: LiveEvaluationSide | undefined;
  rolling_key: "up" | "down";
  rolling: RollingStats | undefined;
}) {
  const avgModel = rolling_key === "up" ? rolling?.avg_up_model : rolling?.avg_down_model;
  const avgMarket = rolling_key === "up" ? rolling?.avg_up_market : rolling?.avg_down_market;
  return (
    <div style={{ flex: 1, minWidth: 0 }}>
      <div className="metric-label">{label}</div>
      <div className="signal-summary-line"><span>Model</span><strong>{formatProbability(side?.fair_value)}</strong></div>
      <div className="signal-summary-line"><span>Market</span><strong>{formatProbability(side?.buy_price)}</strong></div>
      <div className="signal-summary-line"><span>Avg(M+Mkt)</span><strong>{formatProbability(side?.avg_prob)}</strong></div>
      <div className="signal-summary-line"><span>Edge</span><strong>{formatEdgePoints(side?.edge)}</strong></div>
      <div className="signal-summary-line">
        <span>Status</span>
        <strong className={side?.eligible ? "signal-side-good" : "signal-side-warn"}>
          {side?.eligible ? "Eligible" : "Blocked"}
        </strong>
      </div>
      <p className="muted" style={{ fontSize: "0.75rem" }}>{asText(side?.reason) || "--"}</p>
      {rolling && (rolling?.samples ?? 0) > 0 && (
        <>
          <div className="signal-summary-line"><span>Avg Model ({rolling.window}s)</span><strong>{formatProbability(avgModel)}</strong></div>
          <div className="signal-summary-line"><span>Avg Market ({rolling.window}s)</span><strong>{formatProbability(avgMarket)}</strong></div>
        </>
      )}
      <div className="signal-check-list">
        {Object.entries(side?.checks || {}).map(([key, ok]) => (
          <CheckRow key={key} label={key.replace(/_/g, " ")} ok={ok} />
        ))}
      </div>
    </div>
  );
}

function AssetEvaluationCard({
  asset,
  data,
}: {
  asset: string;
  data: LiveEvaluationAsset | undefined;
}) {
  return (
    <div className="signal-summary-card">
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 6 }}>
        <div className="metric-label" style={{ marginBottom: 0 }}>{assetLabel(asset)}</div>
        <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
          <span className="muted" style={{ fontSize: "0.75rem" }}>Best: <strong>{asText(data?.side).toUpperCase() || "--"}</strong></span>
          {data?.forced_side && <span className="badge badge-yellow" style={{ fontSize: "0.65rem" }}>Cert: {data.forced_side.toUpperCase()}</span>}
        </div>
      </div>
      <div className="signal-summary-line"><span>Time left</span><strong>{asNumber(data?.seconds_left)?.toFixed(1) ?? "--"}s</strong></div>
      <div className="signal-summary-line"><span>Move</span><strong>{formatPercent(data?.distance_pct, 3)}</strong></div>
      {data?.rolling && (data.rolling.samples ?? 0) > 0 && (
        <div className="signal-summary-line"><span>Samples ({data.rolling.window}s)</span><strong>{data.rolling.samples}</strong></div>
      )}
      <div style={{ display: "flex", gap: 12, marginTop: 8 }}>
        <SidePanel label="UP" side={data?.up} rolling_key="up" rolling={data?.rolling} />
        <SidePanel label="DOWN" side={data?.down} rolling_key="down" rolling={data?.rolling} />
      </div>
      <p className="muted" style={{ marginTop: 6 }}>{asText(data?.reason) || "No evaluation yet"}</p>
    </div>
  );
}

function isBot2SignalEvent(event: BotEvent): event is BotEvent & { data: Bot2SignalData } {
  return event.kind === "bot2_signal";
}

function EventContent({ event }: { event: BotEvent }) {
  if (isBot2SignalEvent(event)) {
    const edge = asNumber(event.data.edge);
    const tone = bot2SignalTone(edge);
    return (
      <div className="event-signal-card">
        <div className="event-signal-header">
          <span className={`signal-chip signal-chip-${tone}`}>{asText(event.data.side).toUpperCase() || "SIGNAL"}</span>
          <span className="event-signal-title">{bot2SignalSummary(event.data)}</span>
        </div>
        <div className="event-signal-metrics">
          <span>Model: {formatProbability(event.data.fair_value)}</span>
          <span>Market: {formatProbability(event.data.buy_price)}</span>
          <span>Edge: {formatEdgePoints(event.data.edge)}</span>
          <span>Time left: {asNumber(event.data.seconds_left)?.toFixed(1) ?? "--"}s</span>
        </div>
        <div className="event-signal-details">
          <span>Open {formatPrice(event.data.open_price)}</span>
          <span>Now {formatPrice(event.data.current_price)}</span>
          <span>Move {formatPercent(event.data.distance_pct, 3)}</span>
          <span>Vol {asText(event.data.vol_regime) || "--"}</span>
        </div>
      </div>
    );
  }

  return <span className="event-message">{formatEventMessage(event.kind, event.data)}</span>;
}

function SelectField({
  label,
  value,
  options,
  onChange,
}: {
  label: string;
  value: string;
  options: string[];
  onChange: (v: string) => void;
}) {
  return (
    <div className="setting-item">
      <label>{label}</label>
      <select value={value} onChange={(e) => onChange(e.target.value)}>
        {options.map((opt) => (
          <option key={opt} value={opt}>
            {opt}
          </option>
        ))}
      </select>
    </div>
  );
}

function NumberField({
  label,
  value,
  onChange,
  step = 1,
}: {
  label: string;
  value: number | undefined;
  onChange: (v: number) => void;
  step?: number;
}) {
  return (
    <div className="setting-item">
      <label>{label}</label>
      <input type="number" step={step} value={value ?? ""} onChange={(e) => onChange(Number(e.target.value))} />
    </div>
  );
}

function MetricCard({ label, value, className }: { label: string; value: string | number; className?: string }) {
  return (
    <div className="metric-card">
      <div className="metric-label">{label}</div>
      <div className={`metric-value ${className || ""}`}>{value}</div>
    </div>
  );
}

function formatEventMessage(kind: string, data: Record<string, unknown>): string {
  const $ = (v: unknown) => (typeof v === "number" ? `$${Number(v).toFixed(2)}` : String(v ?? ""));
  const pct = (v: unknown) => (typeof v === "number" ? `${Number(v).toFixed(1)}%` : String(v ?? ""));
  const n = (v: unknown, d = 4) => (typeof v === "number" ? Number(v).toFixed(d) : String(v ?? ""));
  const assets = (v: unknown) => (Array.isArray(v) ? v.map((a: string) => a.toUpperCase()).join("/") : String(v ?? ""));

  switch (kind) {
    case "worker_started":
      return `Bot started with ${$(data.balance)}${data.dry_run ? " (dry run)" : ""}`;
    case "worker_stopped":
      return `Bot stopped - balance: ${$(data.balance)}`;
    case "round_started":
      return `Round ${data.round} - ${assets(data.assets)} - ${n(data.seconds_left, 0)}s left`;
    case "round_skipped":
      return `Skipped: ${data.reason}`;
    case "round_result": {
      const outcome = String(data.outcome ?? "").toUpperCase();
      if (data.outcome === "unsettled") {
        return `Round ${data.round} - UNSETTLED (redeem pending) - balance: ${$(data.balance)}`;
      }
      const pnlVal = data.pnl as number;
      const pnlStr = pnlVal >= 0 ? `+${$(pnlVal)}` : `-${$(Math.abs(pnlVal))}`;
      const stop = data.stop_triggered ? " (stop-loss)" : "";
      return `Round ${data.round} - ${outcome}${stop} ${pnlStr} - balance: ${$(data.balance)}`;
    }
    case "entry_sent":
      return `Entry: ${String(data.asset ?? "").toUpperCase()} ${String(data.side ?? "").toUpperCase()} @ ${n(data.price)} - ${$(data.amount)} with ${n(data.seconds_left, 0)}s left`;
    case "fill_received":
      return `Filled: ${String(data.asset ?? "").toUpperCase()} ${n(data.shares)} shares @ ${n(data.entry_price)} (${data.source})${data.stop_price != null ? ` - SL @ ${n(data.stop_price)}` : ""}`;
    case "take_profit_order_placed":
      return `Take profit placed: ${String(data.asset ?? "").toUpperCase()} ${n(data.shares)} shares @ ${n(data.price)}${data.order_type ? ` (${data.order_type})` : ""}`;
    case "take_profit_order_failed":
      return `Take profit failed: ${String(data.asset ?? "").toUpperCase()} ${n(data.shares)} shares @ ${n(data.price)}`;
    case "slippage_warning":
      return `Slippage: quoted ${n(data.quoted)} -> filled ${n(data.fill)} (${pct(data.pct)})`;
    case "stop_triggered":
      return `Stop-loss triggered: bid ${n(data.bid)} <= stop ${n(data.stop_price)} - selling ${n(data.shares)} shares`;
    case "redeem_claimed":
      return `Redeemed ${data.claimed} position${Number(data.claimed) !== 1 ? "s" : ""}${Number(data.pending) > 0 ? ` (${data.pending} pending)` : ""}`;
    case "redeem_attempt":
      return `Redeem attempt #${data.attempt} (${data.reason}) max_conditions=${data.max_conditions}${data.force ? " [force]" : ""}`;
    case "redeem_pending_detected":
      return `Redeem pending detected (${data.count}) from ${data.reason}`;
    case "redeem_rate_limited":
      return `Redeem rate-limited: retry in ${data.retry_in_seconds}s (attempt #${data.attempt})`;
    case "redeem_error":
      return `Redeem error: ${Array.isArray(data.errors) ? data.errors.join(", ") : data.errors}`;
    case "entry_paused":
      return "Entry paused by user";
    case "entry_resumed":
      return "Entry resumed";
    case "force_redeem_requested":
      return "Force redeem requested";
    case "round_error":
      return `Error: ${data.message}`;
    case "error":
      return `Error: ${data.message}`;
    default: {
      const entries = Object.entries(data);
      if (entries.length === 0) return "";
      return entries.map(([k, v]) => `${k}: ${typeof v === "number" ? n(v) : v}`).join(" · ");
    }
  }
}

function profileToText(points: number[][]): string {
  return points.map((p) => `${p[0]},${p[1]},${p[2]}`).join("\n");
}

function textToProfile(text: string): number[][] {
  const rows = text
    .split("\n")
    .map((x) => x.trim())
    .filter(Boolean);
  if (!rows.length) throw new Error("Entry profile cannot be empty");
  return rows.map((row) => {
    const parts = row.split(",").map((x) => x.trim());
    if (parts.length !== 3) throw new Error(`Invalid profile row: "${row}"`);
    const sec = Number(parts[0]);
    const odds = Number(parts[1]);
    const cap = Number(parts[2]);
    if (!Number.isFinite(sec) || !Number.isFinite(odds) || !Number.isFinite(cap)) {
      throw new Error(`Invalid numeric values in row: "${row}"`);
    }
    return [sec, odds, cap];
  });
}

export function BotDashboard({
  title,
  subtitle,
  api,
  supportedAssets,
  profileLabel,
  profileHint,
  showFairValueSettings = false,
}: {
  title: string;
  subtitle?: string;
  api: BotApi;
  supportedAssets: string[];
  profileLabel: string;
  profileHint?: string;
  showFairValueSettings?: boolean;
}) {
  const { status, error } = useStatus(api, 1000);
  const { events, clearEvents } = useEvents(api, 2000);

  const [busy, setBusy] = useState(false);
  const [filter, setFilter] = useState<string>("all");
  const [search, setSearch] = useState("");
  const [settingsBusy, setSettingsBusy] = useState(false);
  const [settingsMsg, setSettingsMsg] = useState("");
  const [settings, setSettings] = useState<Settings | null>(null);
  const [requiresRestart, setRequiresRestart] = useState(false);
  const [profileText, setProfileText] = useState("");

  const workerStatus = status?.worker_status || "unknown";
  const stats = status?.stats;
  const round = status?.current_round;
  const latestEvaluation = status?.latest_evaluation;
  const evaluationEntries = Object.entries(latestEvaluation?.assets || {});
  const focusAssetKey =
    asText(latestEvaluation?.asset) ||
    (evaluationEntries.length > 0 ? evaluationEntries[0][0] : "");
  const focusAsset = (latestEvaluation?.assets || {})[focusAssetKey];
  const latestSignal = [...events].reverse().find((event) => event.kind === "bot2_signal") as
    | (BotEvent & { data: Bot2SignalData })
    | undefined;

  useEffect(() => {
    let active = true;
    api
      .getSettings()
      .then((resp) => {
        if (!active) return;
        setSettings(resp.settings);
        setRequiresRestart(resp.requires_restart);
        setProfileText(profileToText(resp.settings.entry_profile_points));
      })
      .catch(() => {
        if (!active) return;
        setSettingsMsg("Could not load settings");
      });
    return () => {
      active = false;
    };
  }, [api]);

  async function action(fn: () => Promise<unknown>) {
    setBusy(true);
    try {
      await fn();
    } catch (e) {
      alert(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  const filteredEvents = events.filter((e: BotEvent) => {
    if (filter !== "all" && e.level !== filter) return false;
    if (
      search &&
      !e.kind.toLowerCase().includes(search.toLowerCase()) &&
      !JSON.stringify(e.data).toLowerCase().includes(search.toLowerCase())
    ) {
      return false;
    }
    return true;
  });

  function updateSetting<K extends keyof Settings>(key: K, value: Settings[K]) {
    if (!settings) return;
    setSettings({ ...settings, [key]: value });
  }

  function toggleAsset(asset: string, enabled: boolean) {
    if (!settings) return;
    const current = new Set(settings.enabled_assets.map((a) => a.toLowerCase()));
    if (enabled) current.add(asset);
    else current.delete(asset);
    updateSetting("enabled_assets", Array.from(current).sort());
  }

  async function saveSettings() {
    if (!settings) return;
    setSettingsBusy(true);
    setSettingsMsg("");
    try {
      const parsed = textToProfile(profileText);
      const payload: Settings = { ...settings, entry_profile_points: parsed };
      const resp = await api.updateSettings(payload);
      setSettings(resp.settings);
      setRequiresRestart(resp.requires_restart);
      setSettingsMsg(resp.message);
    } catch (e) {
      setSettingsMsg(e instanceof Error ? e.message : String(e));
    } finally {
      setSettingsBusy(false);
    }
  }

  return (
    <div className="app">
      <header>
        <div>
          <h1>{title}</h1>
          {subtitle && <p className="muted" style={{ marginTop: "4px" }}>{subtitle}</p>}
        </div>
        {error && <span className="text-red" style={{ fontSize: "0.8rem" }}>API: {error}</span>}
      </header>

      <section className="controls">
        <StatusBadge status={workerStatus} />
        {status?.entry_paused && <span className="badge badge-yellow">Entry Paused</span>}
        {requiresRestart && <span className="badge badge-yellow">Settings Pending Restart</span>}
        <span className="uptime">Uptime: {formatUptime(status?.uptime_seconds || 0)}</span>
        <span className="uptime">API: {api.baseUrl}</span>

        <div className="btn-group">
          {(workerStatus === "stopped" || workerStatus === "unknown") && (
            <button disabled={busy} onClick={() => action(() => api.startWorker(false))}>
              Start
            </button>
          )}
          {(workerStatus === "stopped" || workerStatus === "unknown") && (
            <button disabled={busy} className="btn-outline" onClick={() => action(() => api.startWorker(true))}>
              Dry Run
            </button>
          )}
          {workerStatus === "running" && (
            <button disabled={busy} className="btn-danger" onClick={() => action(() => api.stopWorker())}>
              Stop
            </button>
          )}
          {workerStatus === "running" && !status?.entry_paused && (
            <button disabled={busy} className="btn-outline" onClick={() => action(() => api.pauseEntry())}>
              Pause Entry
            </button>
          )}
          {status?.entry_paused && (
            <button disabled={busy} className="btn-outline" onClick={() => action(() => api.resumeEntry())}>
              Resume Entry
            </button>
          )}
          {workerStatus === "running" && (
            <button disabled={busy} className="btn-outline" onClick={() => action(() => api.forceRedeem())}>
              Force Redeem
            </button>
          )}
        </div>
      </section>

      {round && (
        <section className="round-bar">
          Round {round.round} - {round.assets.map((a) => a.toUpperCase()).join(" / ")} - {round.seconds_left}s left
        </section>
      )}

      <section className="metrics">
        <MetricCard label="Start Balance" value={stats ? `$${stats.start_balance.toFixed(2)}` : "--"} />
        <MetricCard label="Balance" value={stats ? `$${stats.current_balance.toFixed(2)}` : "--"} />
        <MetricCard
          label="Session Profit"
          value={
            stats
              ? `${stats.current_balance - stats.start_balance >= 0 ? "+" : ""}$${(
                  stats.current_balance - stats.start_balance
                ).toFixed(2)}`
              : "--"
          }
          className={pnlClass(stats ? stats.current_balance - stats.start_balance : 0)}
        />
        <MetricCard label="Entries" value={stats?.total_entries ?? "--"} />
        <MetricCard label="BTC" value={stats?.total_btc_entries ?? "--"} />
        <MetricCard label="ETH" value={stats?.total_eth_entries ?? "--"} />
        <MetricCard label="SOL" value={stats?.total_sol_entries ?? "--"} />
      </section>

      {showFairValueSettings && (
        <section className="events-section" style={{ marginBottom: "24px" }}>
          <div className="events-header">
            <h2>Live Model Monitor</h2>
            <span className="muted">Updates every loop, even when no entry qualifies</span>
          </div>
          <div className="signal-summary-grid">
            <div className="signal-summary-card">
              <div className="metric-label">Decision</div>
              <div className={`metric-value signal-side-${liveDecisionTone(latestEvaluation)}`}>
                {latestEvaluation?.decision === "eligible" ? "Entry Ready" : "Watching"}
              </div>
              <p className="muted">{liveDecisionSummary(latestEvaluation)}</p>
              {focusAsset?.forced_side && (
                <p className="muted" style={{ fontWeight: "bold" }}>Certainty active: forcing {focusAsset.forced_side.toUpperCase()}</p>
              )}
            </div>
            {supportedAssets.map((asset) => (
              <AssetEvaluationCard key={asset} asset={asset} data={(latestEvaluation?.assets || {})[asset]} />
            ))}
            <div className="signal-summary-card">
              <div className="metric-label">Context</div>
              <div className="signal-summary-line">
                <span>Focus asset</span>
                <strong>{assetLabel(focusAssetKey)}</strong>
              </div>
              <div className="signal-summary-line">
                <span>Focus side</span>
                <strong>{asText(focusAsset?.side).toUpperCase() || "--"}</strong>
              </div>
              <div className="signal-summary-line">
                <span>Time left</span>
                <strong>{asNumber(focusAsset?.seconds_left)?.toFixed(1) ?? "--"}s</strong>
              </div>
              <div className="signal-summary-line">
                <span>Open</span>
                <strong>{formatPrice(focusAsset?.open_price)}</strong>
              </div>
              <div className="signal-summary-line">
                <span>Now</span>
                <strong>{formatPrice(focusAsset?.current_price)}</strong>
              </div>
              <div className="signal-summary-line">
                <span>Move</span>
                <strong>{formatPercent(focusAsset?.distance_pct, 3)}</strong>
              </div>
              <div className="signal-summary-line">
                <span>Vol regime</span>
                <strong>{asText(focusAsset?.vol_regime) || "--"}</strong>
              </div>
              <div className="signal-summary-line">
                <span>Model regime</span>
                <strong>{asText(focusAsset?.model_regime) || "--"}</strong>
              </div>
              <div className="signal-summary-line">
                <span>Remaining vol</span>
                <strong>{formatPercent(focusAsset?.remaining_vol_pct, 3)}</strong>
              </div>
              <div className="signal-summary-line">
                <span>Bet mode</span>
                <strong>{asText(latestEvaluation?.bet_sizing_mode).toUpperCase() || "--"}</strong>
              </div>
              <div className="signal-summary-line">
                <span>Fixed bet</span>
                <strong>{formatPrice(latestEvaluation?.fixed_bet_usdc)}</strong>
              </div>
              <div className="signal-summary-line">
                <span>Min model</span>
                <strong>{formatProbability(latestEvaluation?.min_model_probability)}</strong>
              </div>
              <div className="signal-summary-line">
                <span>Min market</span>
                <strong>{formatProbability(latestEvaluation?.min_market_probability)}</strong>
              </div>
              <div className="signal-summary-line">
                <span>Ignore edge</span>
                <strong>{latestEvaluation?.ignore_edge_filter ? "ON" : "OFF"}</strong>
              </div>
              <div className="signal-summary-line">
                <span>Certainty Time</span>
                <strong>{asNumber(latestEvaluation?.certainty_seconds_threshold) ?? "--"}s</strong>
              </div>
              <div className="signal-summary-line">
                <span>Certainty Avg</span>
                <strong>{formatProbability(latestEvaluation?.certainty_avg_threshold)}</strong>
              </div>
              <div className="signal-summary-line">
                <span>Rolling Window</span>
                <strong>{asNumber(latestEvaluation?.rolling_window_seconds) ?? "--"}s</strong>
              </div>
              <div className="signal-summary-line">
                <span>Live start</span>
                <strong>{asNumber(latestEvaluation?.live_monitor_start_seconds) ?? "--"}s</strong>
              </div>
              <div className="signal-summary-line">
                <span>Order start</span>
                <strong>{asNumber(latestEvaluation?.entry_start_seconds) ?? "--"}s</strong>
              </div>
              <div className="signal-summary-line">
                <span>Min edge</span>
                <strong>{formatEdgePoints(focusAsset?.min_edge)}</strong>
              </div>
            </div>
          </div>
        </section>
      )}

      {showFairValueSettings && latestSignal && (
        <section className="events-section" style={{ marginBottom: "24px" }}>
          <div className="events-header">
            <h2>Current Signal</h2>
            <span className="muted">Latest model read before entry/skip</span>
          </div>
          <div className="signal-summary-grid">
            <div className="signal-summary-card">
              <div className="metric-label">Direction</div>
              <div className={`metric-value signal-side-${bot2SignalTone(asNumber(latestSignal.data.edge))}`}>
                {asText(latestSignal.data.side).toUpperCase() || "--"}
              </div>
              <p className="muted">{bot2SignalSummary(latestSignal.data)}</p>
            </div>
            <div className="signal-summary-card">
              <div className="metric-label">Model Vs Market</div>
              <div className="signal-summary-line">
                <span>Model</span>
                <strong>{formatProbability(latestSignal.data.fair_value)}</strong>
              </div>
              <div className="signal-summary-line">
                <span>Market</span>
                <strong>{formatProbability(latestSignal.data.buy_price)}</strong>
              </div>
              <div className="signal-summary-line">
                <span>Edge</span>
                <strong>{formatEdgePoints(latestSignal.data.edge)}</strong>
              </div>
            </div>
            <div className="signal-summary-card">
              <div className="metric-label">Candle Context</div>
              <div className="signal-summary-line">
                <span>Time left</span>
                <strong>{asNumber(latestSignal.data.seconds_left)?.toFixed(1) ?? "--"}s</strong>
              </div>
              <div className="signal-summary-line">
                <span>Open</span>
                <strong>{formatPrice(latestSignal.data.open_price)}</strong>
              </div>
              <div className="signal-summary-line">
                <span>Now</span>
                <strong>{formatPrice(latestSignal.data.current_price)}</strong>
              </div>
              <div className="signal-summary-line">
                <span>Move</span>
                <strong>{formatPercent(latestSignal.data.distance_pct, 3)}</strong>
              </div>
            </div>
            <div className="signal-summary-card">
              <div className="metric-label">Risk Context</div>
              <div className="signal-summary-line">
                <span>Vol regime</span>
                <strong>{asText(latestSignal.data.vol_regime) || "--"}</strong>
              </div>
              <div className="signal-summary-line">
                <span>Model regime</span>
                <strong>{asText(latestSignal.data.model_regime) || "--"}</strong>
              </div>
              <div className="signal-summary-line">
                <span>Remaining vol</span>
                <strong>{formatPercent(latestSignal.data.remaining_vol_pct, 3)}</strong>
              </div>
              <div className="signal-summary-line">
                <span>Spread</span>
                <strong>{formatProbability(latestSignal.data.spread)}</strong>
              </div>
            </div>
          </div>
        </section>
      )}

      <section className="events-section">
        <div className="events-header">
          <h2>Events</h2>
          <div className="events-filters">
            <select value={filter} onChange={(e) => setFilter(e.target.value)}>
              <option value="all">All</option>
              <option value="info">Info</option>
              <option value="warn">Warn</option>
              <option value="error">Error</option>
            </select>
            <input type="text" placeholder="Search events..." value={search} onChange={(e) => setSearch(e.target.value)} />
            <button className="btn-outline btn-small" onClick={clearEvents}>Clear</button>
          </div>
        </div>
        <div className="events-list">
          {filteredEvents.length === 0 && <p className="muted">No events yet</p>}
          {[...filteredEvents].reverse().map((e) => (
            <div key={e.id} className={`event-row${e.kind === "bot2_signal" ? " event-row-rich" : ""}`}>
              <EventLevelDot level={e.level} />
              <span className="event-time">{formatEventTime(e.ts)}</span>
              <EventContent event={e} />
            </div>
          ))}
        </div>
      </section>

      <section className="events-section" style={{ marginTop: "16px" }}>
        <div className="events-header">
          <h2>Settings (Safe Mode)</h2>
          <div className="events-filters">
            <button className="btn-outline btn-small" disabled={settingsBusy || !settings} onClick={saveSettings}>
              {settingsBusy ? "Saving..." : "Save Settings"}
            </button>
          </div>
        </div>
        <p className="muted" style={{ marginBottom: "10px" }}>
          Changes are always applied only after worker restart.
        </p>
        {profileHint && (
          <p className="muted" style={{ marginBottom: "10px" }}>
            {profileHint}
          </p>
        )}
        {settingsMsg && <p className="muted" style={{ marginBottom: "10px" }}>{settingsMsg}</p>}
        {settings && (
          <div className="settings-grid">
            <div className="setting-item">
              <label>Enabled Markets</label>
              <div className="setting-row">
                {supportedAssets.includes("btc") && (
                  <label>
                    <input
                      type="checkbox"
                      checked={settings.enabled_assets.includes("btc")}
                      onChange={(e) => toggleAsset("btc", e.target.checked)}
                    />{" "}
                    BTC
                  </label>
                )}
                {supportedAssets.includes("eth") && (
                  <label>
                    <input
                      type="checkbox"
                      checked={settings.enabled_assets.includes("eth")}
                      onChange={(e) => toggleAsset("eth", e.target.checked)}
                    />{" "}
                    ETH
                  </label>
                )}
                {supportedAssets.includes("sol") && (
                  <label>
                    <input
                      type="checkbox"
                      checked={settings.enabled_assets.includes("sol")}
                      onChange={(e) => toggleAsset("sol", e.target.checked)}
                    />{" "}
                    SOL
                  </label>
                )}
              </div>
            </div>

            <NumberField label="Min Bet USDC" value={settings.min_bet_usdc} onChange={(v) => updateSetting("min_bet_usdc", v)} step={0.01} />
            {showFairValueSettings && (
              <SelectField
                label="Bet Sizing Mode"
                value={settings.bet_sizing_mode || "dynamic"}
                options={["dynamic", "fixed"]}
                onChange={(v) => updateSetting("bet_sizing_mode", v)}
              />
            )}
            <NumberField label="Max Odds" value={settings.max_odds} onChange={(v) => updateSetting("max_odds", v)} step={0.001} />
            <NumberField label="Order Start Seconds" value={settings.entry_start_seconds} onChange={(v) => updateSetting("entry_start_seconds", Math.round(v))} />
            <NumberField label="Live Monitor Start Seconds" value={settings.live_monitor_start_seconds} onChange={(v) => updateSetting("live_monitor_start_seconds", Math.round(v))} />
            <NumberField label="Entry Check Interval" value={settings.entry_check_interval_seconds} onChange={(v) => updateSetting("entry_check_interval_seconds", v)} step={0.1} />
            <NumberField label="Fast Check Interval" value={settings.entry_check_interval_fast_seconds} onChange={(v) => updateSetting("entry_check_interval_fast_seconds", v)} step={0.1} />
            <NumberField label="Fast Threshold Seconds" value={settings.entry_check_interval_fast_threshold_seconds} onChange={(v) => updateSetting("entry_check_interval_fast_threshold_seconds", Math.round(v))} />
            <NumberField label="Balance Refresh Seconds" value={settings.entry_balance_refresh_seconds} onChange={(v) => updateSetting("entry_balance_refresh_seconds", Math.round(v))} />
            <NumberField label="Slippage Warn Pct" value={settings.fill_slippage_warn_pct} onChange={(v) => updateSetting("fill_slippage_warn_pct", v)} step={0.001} />

            <div className="setting-item">
              <label>Auto Redeem Enabled</label>
              <div className="setting-row">
                <label>
                  <input type="checkbox" checked={settings.auto_redeem_enabled} onChange={(e) => updateSetting("auto_redeem_enabled", e.target.checked)} /> Enabled
                </label>
              </div>
            </div>
            <NumberField label="Auto Redeem Max Conditions" value={settings.auto_redeem_max_conditions_per_cycle} onChange={(v) => updateSetting("auto_redeem_max_conditions_per_cycle", Math.round(v))} />
            <NumberField label="Redeem Attempt Interval Seconds" value={settings.auto_redeem_attempt_interval_seconds} onChange={(v) => updateSetting("auto_redeem_attempt_interval_seconds", Math.round(v))} />
            <NumberField label="Redeem Probe Interval Seconds" value={settings.auto_redeem_probe_interval_seconds} onChange={(v) => updateSetting("auto_redeem_probe_interval_seconds", Math.round(v))} />
            <NumberField label="Redeem Rate Limit Buffer Seconds" value={settings.auto_redeem_rate_limit_buffer_seconds} onChange={(v) => updateSetting("auto_redeem_rate_limit_buffer_seconds", Math.round(v))} />

            {!showFairValueSettings && (
              <>
                <div className="setting-item">
                  <label>Stop Loss Enabled</label>
                  <div className="setting-row">
                    <label>
                      <input type="checkbox" checked={settings.stop_loss_enabled} onChange={(e) => updateSetting("stop_loss_enabled", e.target.checked)} /> Enabled
                    </label>
                  </div>
                </div>
                <NumberField label="Stop Loss Pct" value={settings.stop_loss_pct} onChange={(v) => updateSetting("stop_loss_pct", v)} step={0.01} />
                <NumberField label="Stop Loss Poll Seconds" value={settings.stop_loss_poll_seconds} onChange={(v) => updateSetting("stop_loss_poll_seconds", Math.round(v))} />
                <NumberField label="Stop Loss Confirm Ticks" value={settings.stop_loss_confirm_ticks} onChange={(v) => updateSetting("stop_loss_confirm_ticks", Math.round(v))} />
                <NumberField label="Stop Loss Retry Seconds" value={settings.stop_loss_retry_seconds} onChange={(v) => updateSetting("stop_loss_retry_seconds", Math.round(v))} />
              </>
            )}

            <div className="setting-item">
              <label>Volatility Filter Enabled</label>
              <div className="setting-row">
                <label>
                  <input
                    type="checkbox"
                    checked={settings.volatility_filter_enabled}
                    onChange={(e) => updateSetting("volatility_filter_enabled", e.target.checked)}
                  />{" "}
                  Enabled
                </label>
              </div>
            </div>
            <SelectField
              label="Volatility Candle Interval"
              value={settings.volatility_interval}
              options={["1m", "3m", "5m", "15m", "1h"]}
              onChange={(v) => updateSetting("volatility_interval", v)}
            />
            <NumberField label="Volatility Refresh Seconds" value={settings.volatility_refresh_seconds} onChange={(v) => updateSetting("volatility_refresh_seconds", Math.round(v))} />
            <NumberField label="Volatility Lookback Candles" value={settings.volatility_lookback_candles} onChange={(v) => updateSetting("volatility_lookback_candles", Math.round(v))} />
            <NumberField label="Volatility Low Threshold" value={settings.volatility_low_threshold} onChange={(v) => updateSetting("volatility_low_threshold", v)} step={0.0001} />
            <NumberField label="Volatility High Threshold" value={settings.volatility_high_threshold} onChange={(v) => updateSetting("volatility_high_threshold", v)} step={0.0001} />
            <NumberField label="Volatility Extreme Threshold" value={settings.volatility_extreme_threshold} onChange={(v) => updateSetting("volatility_extreme_threshold", v)} step={0.0001} />
            <NumberField label="Volatility Min Edge Bump (High)" value={settings.volatility_min_odds_bump_high} onChange={(v) => updateSetting("volatility_min_odds_bump_high", v)} step={0.001} />
            <NumberField label="Volatility Min Edge Bump (Extreme)" value={settings.volatility_min_odds_bump_extreme} onChange={(v) => updateSetting("volatility_min_odds_bump_extreme", v)} step={0.001} />
            <NumberField label="Volatility Capital Mult (Low)" value={settings.volatility_capital_mult_low} onChange={(v) => updateSetting("volatility_capital_mult_low", v)} step={0.01} />
            <NumberField label="Volatility Capital Mult (High)" value={settings.volatility_capital_mult_high} onChange={(v) => updateSetting("volatility_capital_mult_high", v)} step={0.01} />
            <NumberField label="Volatility Capital Mult (Extreme)" value={settings.volatility_capital_mult_extreme} onChange={(v) => updateSetting("volatility_capital_mult_extreme", v)} step={0.01} />

            {showFairValueSettings && (
              <>
                <NumberField label="Fair Value Sigma Floor Pct" value={settings.fair_value_sigma_floor_pct} onChange={(v) => updateSetting("fair_value_sigma_floor_pct", v)} step={0.0001} />
                <NumberField label="Fair Value No Trade Band Pct" value={settings.fair_value_no_trade_band_pct} onChange={(v) => updateSetting("fair_value_no_trade_band_pct", v)} step={0.0001} />
                <NumberField label="Fair Value Max Spread" value={settings.fair_value_max_spread} onChange={(v) => updateSetting("fair_value_max_spread", v)} step={0.001} />
                <NumberField label="Fair Value Requote Threshold" value={settings.fair_value_requote_threshold} onChange={(v) => updateSetting("fair_value_requote_threshold", v)} step={0.001} />
                <NumberField label="Fair Value Aggressive Edge" value={settings.fair_value_aggressive_edge} onChange={(v) => updateSetting("fair_value_aggressive_edge", v)} step={0.001} />
                <NumberField label="Min Model Probability (0-1 or %)" value={settings.fair_value_min_model_probability} onChange={(v) => updateSetting("fair_value_min_model_probability", v)} step={0.01} />
                <NumberField label="Min Market Probability (0-1 or %)" value={settings.fair_value_min_market_probability} onChange={(v) => updateSetting("fair_value_min_market_probability", v)} step={0.01} />
                <div className="setting-item">
                  <label>Ignore Edge Filter</label>
                  <div className="setting-row">
                    <label>
                      <input
                        type="checkbox"
                        checked={Boolean(settings.ignore_edge_filter)}
                        onChange={(e) => updateSetting("ignore_edge_filter", e.target.checked)}
                      />{" "}
                      Enabled
                    </label>
                  </div>
                </div>
                <NumberField label="Certainty Seconds Threshold" value={settings.certainty_seconds_threshold} onChange={(v) => updateSetting("certainty_seconds_threshold", Math.round(v))} step={1} />
                <NumberField label="Certainty Avg Threshold (0-1)" value={settings.certainty_avg_threshold} onChange={(v) => updateSetting("certainty_avg_threshold", v)} step={0.01} />
                <NumberField label="Rolling Window Seconds" value={settings.rolling_window_seconds} onChange={(v) => updateSetting("rolling_window_seconds", Math.round(v))} step={1} />
              </>
            )}

            <NumberField label="Poll Interval Seconds" value={settings.poll_interval_seconds} onChange={(v) => updateSetting("poll_interval_seconds", Math.round(v))} />
            <NumberField label="Post Resolution Buffer Seconds" value={settings.post_resolution_buffer_seconds} onChange={(v) => updateSetting("post_resolution_buffer_seconds", Math.round(v))} />
            <NumberField label="Wait Log Interval Seconds" value={settings.wait_log_interval_seconds} onChange={(v) => updateSetting("wait_log_interval_seconds", Math.round(v))} />

            <div className="setting-item setting-item-full">
              <label>{profileLabel}</label>
              <textarea className="settings-textarea" value={profileText} onChange={(e) => setProfileText(e.target.value)} />
            </div>
          </div>
        )}
      </section>
    </div>
  );
}
