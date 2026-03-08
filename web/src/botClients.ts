import { createBotApi } from "./api";

const bot1Base = import.meta.env.VITE_BOT1_API_URL || import.meta.env.VITE_API_URL || "http://localhost:8000";
const bot2Base = import.meta.env.VITE_BOT2_API_URL || "http://localhost:8001";

export const bot1Api = createBotApi(bot1Base);
export const bot2Api = createBotApi(bot2Base);
