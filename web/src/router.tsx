import { HashRouter, Navigate, Route, Routes } from "react-router-dom";

import App from "./App";
import BotOnePage from "./pages/BotOnePage";
import BotTwoPage from "./pages/BotTwoPage";

export default function AppRouter() {
  return (
    <HashRouter>
      <Routes>
        <Route path="/" element={<App />}>
          <Route index element={<BotOnePage />} />
          <Route path="bot-2" element={<BotTwoPage />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Route>
      </Routes>
    </HashRouter>
  );
}
