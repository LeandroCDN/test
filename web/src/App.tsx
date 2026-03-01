import { useEffect, useState } from "react";
import { api } from "./api";
import type { BotEvent, Settings } from "./api";
import { useStatus, useEvents } from "./hooks";
import "./index.css";

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
  const cls =
    level === "error" ? "dot-red" : level === "warn" ? "dot-yellow" : "dot-blue";
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

export default function App() {
  const { status, error } = useStatus(1000);
  const { events, clearEvents } = useEvents(2000);

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

  useEffect(() => {
    let active = true;
    api.getSettings()
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
  }, []);

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
    if (search && !e.kind.toLowerCase().includes(search.toLowerCase()) &&
        !JSON.stringify(e.data).toLowerCase().includes(search.toLowerCase())) return false;
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
        <h1>BTC/ETH 5-Min Bot</h1>
        {error && <span className="text-red" style={{ fontSize: "0.8rem" }}>API: {error}</span>}
      </header>

      <section className="controls">
        <StatusBadge status={workerStatus} />

        {status?.entry_paused && <span className="badge badge-yellow">Entry Paused</span>}
        {requiresRestart && <span className="badge badge-yellow">Settings Pending Restart</span>}

        <span className="uptime">Uptime: {formatUptime(status?.uptime_seconds || 0)}</span>

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
          Round {round.round} — {round.assets.map((a) => a.toUpperCase()).join(" / ")} — {round.seconds_left}s left
        </section>
      )}

      <section className="metrics">
        <MetricCard label="Start Balance" value={stats ? `$${stats.start_balance.toFixed(2)}` : "--"} />
        <MetricCard label="Balance" value={stats ? `$${stats.current_balance.toFixed(2)}` : "--"} />
        <MetricCard
          label="Session Profit"
          value={stats ? `${(stats.current_balance - stats.start_balance) >= 0 ? "+" : ""}$${(stats.current_balance - stats.start_balance).toFixed(2)}` : "--"}
          className={pnlClass(stats ? stats.current_balance - stats.start_balance : 0)}
        />
        <MetricCard label="Win Rate" value={stats && stats.total_entries > 0 ? `${((stats.total_wins / stats.total_entries) * 100).toFixed(1)}%` : "--"} />
        <MetricCard label="Entries" value={stats?.total_entries ?? "--"} />
        <MetricCard label="BTC" value={stats?.total_btc_entries ?? "--"} />
        <MetricCard label="ETH" value={stats?.total_eth_entries ?? "--"} />
        <MetricCard label="Wins" value={stats?.total_wins ?? "--"} className="text-green" />
        <MetricCard label="Losses" value={stats?.total_losses ?? "--"} className="text-red" />
        <MetricCard label="Unsettled" value={stats?.total_unsettled ?? "--"} className="text-yellow" />
        <MetricCard label="Stop Exits" value={stats?.total_stop_exits ?? "--"} />
        <MetricCard label="Skipped" value={stats?.total_skipped ?? "--"} />
      </section>

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
            <input
              type="text"
              placeholder="Search events..."
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
            <button className="btn-outline btn-small" onClick={clearEvents}>Clear</button>
          </div>
        </div>
        <div className="events-list">
          {filteredEvents.length === 0 && <p className="muted">No events yet</p>}
          {[...filteredEvents].reverse().map((e) => (
            <div key={e.id} className="event-row">
              <EventLevelDot level={e.level} />
              <span className="event-time">{formatEventTime(e.ts)}</span>
              <span className="event-message">{formatEventMessage(e.kind, e.data)}</span>
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
        {settingsMsg && <p className="muted" style={{ marginBottom: "10px" }}>{settingsMsg}</p>}
        {settings && (
          <div className="settings-grid">
            <div className="setting-item">
              <label>Enabled Markets</label>
              <div className="setting-row">
                <label><input type="checkbox" checked={settings.enabled_assets.includes("btc")} onChange={(e) => toggleAsset("btc", e.target.checked)} /> BTC</label>
                <label><input type="checkbox" checked={settings.enabled_assets.includes("eth")} onChange={(e) => toggleAsset("eth", e.target.checked)} /> ETH</label>
              </div>
            </div>

            <NumberField label="Min Bet USDC" value={settings.min_bet_usdc} onChange={(v) => updateSetting("min_bet_usdc", v)} step={0.01} />
            <NumberField label="Max Odds" value={settings.max_odds} onChange={(v) => updateSetting("max_odds", v)} step={0.001} />
            <NumberField label="Entry Start Seconds" value={settings.entry_start_seconds} onChange={(v) => updateSetting("entry_start_seconds", Math.round(v))} />
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

            <NumberField label="Poll Interval Seconds" value={settings.poll_interval_seconds} onChange={(v) => updateSetting("poll_interval_seconds", Math.round(v))} />
            <NumberField label="Post Resolution Buffer Seconds" value={settings.post_resolution_buffer_seconds} onChange={(v) => updateSetting("post_resolution_buffer_seconds", Math.round(v))} />
            <NumberField label="Wait Log Interval Seconds" value={settings.wait_log_interval_seconds} onChange={(v) => updateSetting("wait_log_interval_seconds", Math.round(v))} />

            <div className="setting-item setting-item-full">
              <label>Entry Profile Points (seconds,min_odds,capital_pct per line)</label>
              <textarea
                className="settings-textarea"
                value={profileText}
                onChange={(e) => setProfileText(e.target.value)}
              />
            </div>
          </div>
        )}
      </section>
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
  value: number;
  onChange: (v: number) => void;
  step?: number;
}) {
  return (
    <div className="setting-item">
      <label>{label}</label>
      <input
        type="number"
        step={step}
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
      />
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
  const $ = (v: unknown) => typeof v === "number" ? `$${Number(v).toFixed(2)}` : String(v ?? "");
  const pct = (v: unknown) => typeof v === "number" ? `${Number(v).toFixed(1)}%` : String(v ?? "");
  const n = (v: unknown, d = 4) => typeof v === "number" ? Number(v).toFixed(d) : String(v ?? "");
  const assets = (v: unknown) => Array.isArray(v) ? v.map((a: string) => a.toUpperCase()).join("/") : String(v ?? "");

  switch (kind) {
    case "worker_started":
      return `Bot started with ${$(data.balance)}${data.dry_run ? " (dry run)" : ""}`;
    case "worker_stopped":
      return `Bot stopped — balance: ${$(data.balance)}`;
    case "round_started":
      return `Round ${data.round} — ${assets(data.assets)} — ${n(data.seconds_left, 0)}s left`;
    case "round_skipped":
      return `Skipped: ${data.reason}`;
    case "round_result": {
      const outcome = String(data.outcome ?? "").toUpperCase();
      if (data.outcome === "unsettled")
        return `Round ${data.round} — UNSETTLED (redeem pending) — balance: ${$(data.balance)}`;
      const pnlVal = data.pnl as number;
      const pnlStr = pnlVal >= 0 ? `+${$(pnlVal)}` : `-${$(Math.abs(pnlVal))}`;
      const stop = data.stop_triggered ? " (stop-loss)" : "";
      return `Round ${data.round} — ${outcome}${stop} ${pnlStr} — balance: ${$(data.balance)}`;
    }
    case "entry_sent":
      return `Entry: ${String(data.asset ?? "").toUpperCase()} ${String(data.side ?? "").toUpperCase()} @ ${n(data.price)} — ${$(data.amount)} with ${n(data.seconds_left, 0)}s left`;
    case "fill_received":
      return `Filled: ${String(data.asset ?? "").toUpperCase()} ${n(data.shares)} shares @ ${n(data.entry_price)} (${data.source}) — SL @ ${n(data.stop_price)}`;
    case "slippage_warning":
      return `Slippage: quoted ${n(data.quoted)} → filled ${n(data.fill)} (${pct(data.pct)})`;
    case "stop_triggered":
      return `Stop-loss triggered: bid ${n(data.bid)} ≤ stop ${n(data.stop_price)} — selling ${n(data.shares)} shares`;
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
