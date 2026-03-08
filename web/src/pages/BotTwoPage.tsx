import { bot2Api } from "../botClients";
import { BotDashboard } from "../components/BotDashboard";

export default function BotTwoPage() {
  return (
    <BotDashboard
      title="Crypto 5-Min Bot 2"
      subtitle="Bot aislado con estrategia de fair value intravela para BTC, ETH y SOL con prioridad BTC."
      api={bot2Api}
      supportedAssets={["btc", "eth", "sol"]}
      profileLabel="Signal Profile (seconds,min_edge,capital_pct per line)"
      profileHint="En este bot, el segundo valor del perfil representa edge minimo requerido, no odds minima."
      showFairValueSettings
    />
  );
}
