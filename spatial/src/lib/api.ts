/**
 * API —— 与后端 webapp 同一套端点与权限。
 */

const KEY = "spatial_token";

export type Planet = {
  id: string; title: string; lifecycle: string | null;
  heat: number | null; velocity: number | null; confidence: number | null;
  contentCount: number; platformCount: number; platforms: string[];
  entities: string[];
  relevance: number | null; relevanceLevel: "L1" | "L2" | "L3"; relevanceNote: string;
  nIdeas: number; firstSeen: string | null;
  radius: number; glowRank: number; density: number;
  /** 话题所属游戏（从 game_* 实体标签解析），用于取封面 */
  game?: string | null;
  gameName?: string | null;
  cover?: string | null;
  appId?: string | null;
};

export type Idea = { id: string; name: string; type: string; score: number; passed: boolean };

export type Universe = {
  planets: Planet[]; core: { name: string; games: { key: string; name: string }[];
    topicCount: number; multiPlatformCount: number };
  pipes: { platform: string; count: number }[];
  relevanceCensus: { real: number; proxy: number; unknown: number };
  degradeNotes: string[]; ideas: Record<string, Idea[]>;
  /** 游戏档案（app_id → 封面），来自 scripts/fetch_game_covers.py 实抓 */
  games?: Record<string, { name: string; file: string; app_id: string; bytes: number }>;
};

export const auth = {
  get: () => localStorage.getItem(KEY) || "",
  set: (t: string) => localStorage.setItem(KEY, t),
  clear: () => localStorage.removeItem(KEY),
};

export async function login(actor: string, password: string) {
  const r = await fetch("/api/login", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ actor, password }),
  });
  const d = await r.json();
  if (!r.ok) throw new Error(d.detail || "登录失败");
  auth.set(d.token);
  return d as { token: string; actor: string; role: string };
}

async function get<T>(path: string): Promise<T> {
  const r = await fetch(path, { headers: { Authorization: `Bearer ${auth.get()}` } });
  if (r.status === 401) { auth.clear(); location.reload(); }
  const d = await r.json();
  if (!r.ok) throw new Error(d.detail || r.statusText);
  return d as T;
}

export const api = {
  me: () => get<{ actor: string; role: string }>("/api/me"),
  universe: (limit = 240) => get<Universe>(`/api/universe?limit=${limit}`),
  topic: (id: string) => get<any>(`/api/events/${encodeURIComponent(id)}/workspace`),
  studio: (id: string) => get<any>(`/api/events/${encodeURIComponent(id)}/studio`),
  run: (input: string, selected?: Record<string, string>) =>
    fetch("/api/command", {
      method: "POST",
      headers: { "Content-Type": "application/json", Authorization: `Bearer ${auth.get()}` },
      body: JSON.stringify({ input, selected }),
    }).then(async (r) => {
      const d = await r.json();
      if (!r.ok) throw new Error(d.detail || "命令失败");
      return d;
    }),
};
