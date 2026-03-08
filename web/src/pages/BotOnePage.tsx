import { bot1Api } from "../botClients";
import { BotDashboard } from "../components/BotDashboard";

export default function BotOnePage() {
  return (
    <BotDashboard
      title="BTC/ETH 5-Min Bot"
      subtitle="Dashboard original del bot actual."
      api={bot1Api}
      supportedAssets={["btc", "eth"]}
      profileLabel="Entry Profile Points (seconds,min_odds,capital_pct per line)"
    />
  );
}
