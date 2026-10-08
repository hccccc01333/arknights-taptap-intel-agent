/** API 与公开演示模式；静态部署默认使用明确标注的全合成数据。 */
import { demoTopic, demoUniverse } from "./demo";
import {isPublicResults,loadPublicResults,publicItem} from './publicResults';

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
  /** 卡片主文案：代表内容的真实标题（forming 信号则是回帖变化语境） */
  description?: string | null;
  representativePlatform?: string | null;
  isForming?: boolean;
  /** 演示页面使用的附加维度；后端缺省时不作推断。 */
  category?: string;
  sentiment?: number;
  change?: number;
};

export type Idea = { id: string; name: string; type: string; score: number; passed: boolean; scoreAvailable?: boolean; passedAvailable?: boolean };

export type Universe = {
  planets: Planet[]; core: { name: string; games: { key: string; name: string }[];
    topicCount: number; multiPlatformCount: number };
  pipes: { platform: string; count: number }[];
  relevanceCensus: { real: number; proxy: number; unknown: number };
  degradeNotes: string[]; ideas: Record<string, Idea[]>;
  /** Existing input/output watermarks, independent of this request's time. */
  pipelineHealth?: {
    checked_at: string; status: "ok" | "degraded"; warnings: string[]; note: string;
  };
  /** 游戏档案（app_id → 封面），来自 scripts/fetch_game_covers.py 实抓 */
  games?: Record<string, { name: string; file: string; app_id: string; bytes: number }>;
};

export const auth = {
  get: () => localStorage.getItem(KEY) || "",
  set: (t: string, actor?: string) => { localStorage.setItem(KEY, t); if(actor)localStorage.setItem('spatial_actor',actor);else localStorage.removeItem('spatial_actor');window.dispatchEvent(new Event('pulse-auth-change')); },
  clear: () => { localStorage.removeItem(KEY);localStorage.removeItem('spatial_actor');window.dispatchEvent(new Event('pulse-auth-change')); },
};

/** 根地址可为 https://service.example 或 https://service.example/api。 */
export const apiBaseUrl = (import.meta.env.VITE_API_BASE_URL || "").trim().replace(/\/+$/, "");
const mode = (import.meta.env.VITE_DEMO_MODE || "").trim().toLowerCase();
/** 显式 true/false 优先；没有设置时，有后端地址为 live，否则为 demo。 */
export const isDemoMode = mode === "true" || (mode !== "false" && !apiBaseUrl);

function endpoint(path: string) {
  // 配置已经包含 /api 时，避免得到 /api/api；保留反向代理的前缀路径。
  const suffix = /\/api$/i.test(apiBaseUrl) ? path.replace(/^\/api(?=\/|\?|$)/, "") : path;
  return `${apiBaseUrl}${suffix}`;
}

function serverMessage(data: unknown, fallback: string): string {
  if (!data || typeof data !== "object") return fallback;
  const record = data as Record<string, unknown>;
  const detail = record.detail ?? record.error ?? record.message;
  if (typeof detail === "string" && detail.trim()) return detail;
  if (Array.isArray(detail)) {
    const messages = detail.map((item) => {
      if (typeof item === "string") return item;
      if (item && typeof item === "object" && "msg" in item) return String(item.msg);
      return "请求参数无效";
    });
    if (messages.length) return messages.join("；");
  }
  return fallback;
}

async function request<T>(path: string, options: RequestInit = {}, timeoutMs = 20000): Promise<T> {
  const controller = new AbortController();
  const external = options.signal;
  const cancel = () => controller.abort();
  if(external?.aborted)controller.abort();else external?.addEventListener('abort',cancel,{once:true});
  const timeout = window.setTimeout(() => controller.abort(), timeoutMs);
  try {
    const headers = new Headers(options.headers);
    const token = auth.get();
    if (token && !headers.has("Authorization")) headers.set("Authorization", `Bearer ${token}`);
    const response = await fetch(endpoint(path), { ...options, headers, signal: controller.signal });
    if (response.status === 401) {
      if (auth.get() === token) auth.clear();
      throw new Error(path === "/api/login" ? "帐号或密码不正确。" : "登录状态已失效，请重新登录。");
    }
    const body = await response.text();
    let data: unknown;
    try { data = body ? JSON.parse(body) : null; }
    catch { throw new Error(`后端返回了无法解析的数据（HTTP ${response.status}）。`); }
    if (!response.ok) {
      throw new Error(serverMessage(data, `请求失败（HTTP ${response.status}），请稍后重试。`));
    }
    if (data && typeof data === "object" && "error" in data && (data as { error?: unknown }).error) {
      throw new Error(serverMessage(data, "后端未能完成请求。"));
    }
    return data as T;
  } catch (error) {
    if (external?.aborted) throw new DOMException('读取已取消','AbortError');
    if (controller.signal.aborted) throw new Error("请求超时，请检查后端服务后重试。");
    if (error instanceof TypeError) throw new Error("无法连接后端，请检查 API 地址、服务状态与跨域配置。");
    throw error;
  } finally {
    window.clearTimeout(timeout);
    external?.removeEventListener('abort',cancel);
  }
}

export async function login(actor: string, password: string) {
  if (isDemoMode) return { token: "", actor: "演示访客", role: "viewer" };
  const d = await request<{ token: string; actor: string; role: string }>("/api/login", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ actor, password }),
  });
  if (!d || typeof d.token !== "string" || !d.token) throw new Error("登录响应缺少凭证，请检查后端配置。");
  auth.set(d.token,d.actor);
  return d;
}

function normalizeUniverse(value: unknown): Universe {
  if (!value || typeof value !== "object") throw new Error("后端没有返回可用的情报数据。");
  const data = value as Partial<Universe>;
  const planets = Array.isArray(data.planets) ? data.planets : [];
  const core = data.core || {} as Universe["core"];
  return {
    planets,
    core: {
      name: typeof core.name === "string" && core.name ? core.name : "TapTap 全网增长情报",
      games: Array.isArray(core.games) ? core.games : [],
      topicCount: Number.isFinite(core.topicCount) ? core.topicCount : planets.length,
      multiPlatformCount: Number.isFinite(core.multiPlatformCount)
        ? core.multiPlatformCount : planets.filter((planet) => planet.platformCount > 1).length,
    },
    pipes: Array.isArray(data.pipes) ? data.pipes : [],
    relevanceCensus: {
      real: data.relevanceCensus?.real ?? planets.filter((planet) => planet.relevanceLevel === "L1").length,
      proxy: data.relevanceCensus?.proxy ?? planets.filter((planet) => planet.relevanceLevel === "L2").length,
      unknown: data.relevanceCensus?.unknown ?? planets.filter((planet) => planet.relevanceLevel === "L3").length,
    },
    degradeNotes: Array.isArray(data.degradeNotes) ? data.degradeNotes : [],
    pipelineHealth: data.pipelineHealth && Array.isArray(data.pipelineHealth.warnings)
      ? data.pipelineHealth : undefined,
    ideas: data.ideas && typeof data.ideas === "object" && !Array.isArray(data.ideas) ? data.ideas : {},
    games: data.games,
  };
}

export const api = {
  me: async () => isDemoMode ? { actor: "演示访客", role: "viewer" }
    : request<{ actor: string; role: string }>("/api/me"),
  universe: async (limit = 240, signal?: AbortSignal) => {
    if (isDemoMode) return structuredClone(demoUniverse);
    const safeLimit = Number.isFinite(limit) ? Math.max(1, Math.min(1000, Math.floor(limit))) : 240;
    return normalizeUniverse(await request<unknown>(`/api/universe?limit=${safeLimit}`, {signal}));
  },
  topic: async (id: string, signal?: AbortSignal): Promise<any> => isDemoMode ? demoTopic(id)
    : request<any>(`/api/events/${encodeURIComponent(id)}/workspace`, {signal}),
  studio: async (id: string): Promise<any> => isDemoMode
    ? { event_id: id, mode: "demo", readOnly: true, ideas: demoUniverse.ideas[id] || [],
        note: "演示策划示例，未经评审，不支持后端执行。" }
    : request<any>(`/api/events/${encodeURIComponent(id)}/studio`),
  run: async (input: string, selected?: Record<string, string>) => {
    if (isDemoMode) throw new Error("演示模式为只读，不支持后端命令执行。请连接后端后再运行。");
    return request<any>("/api/command", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ input, selected }),
    }, 60000);
  },
};

/** V2 always reads the real backend; V1 synthetic mode remains a separate entry. */
export const v2Api = {
  login: async (actor: string, password: string) => {
    const result = await request<{token:string;actor:string;role:string}>("/api/login", {
      method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({actor,password}),
    });
    auth.set(result.token,result.actor);
    return result;
  },
  me: () => request<{actor:string;role:string}>("/api/me"),
  overview: () => request<any>("/api/v2/overview"),
  candidates: (query="") => request<any>("/api/v2/candidates?q="+encodeURIComponent(query)),
  ingest: () => request<any>("/api/v2/ingest",{method:"POST"},60000),
  research: (task:string) => request<{run_id:string;status:string}>("/api/v2/runs", {
    method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({task}),
  }),
  run: (id:string) => request<any>("/api/v2/runs/"+encodeURIComponent(id)),
  event: (id:string) => request<any>("/api/v2/events/"+encodeURIComponent(id)),
  collection: () => request<{run_id:string;status:string}>("/api/v2/collection",{method:"POST"}),
  brief: (days:number) => request<any>("/api/v2/brief?days="+days),
  feedback: (body:any) => request<any>("/api/v2/feedback",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)}),
  context: (body:any) => request<any>("/api/v2/context",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)}),
  schedule: (body:any) => request<any>("/api/v2/schedule",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)}),
  model: (model:string) => request<any>("/api/v2/model",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({model})}),
};

export const v3Api = {
  login:v2Api.login, me:v2Api.me,
  overview:()=>isPublicResults?loadPublicResults(true).then(d=>d.overview):request<any>('/api/v3/overview'),
  cycle:(body:any)=>request<any>('/api/v3/cycles',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}),
  run:(id:string)=>request<any>('/api/v3/runs/'+encodeURIComponent(id)),
  topic:(id:string)=>isPublicResults?publicItem('topics',id):request<any>('/api/v3/topics/'+encodeURIComponent(id)),
  topics:(domain:string)=>isPublicResults?loadPublicResults().then(d=>({hotspots:d.overview.hotspots.filter((h:any)=>h.payload.heat_evidence.some((e:any)=>e.domains?.includes(domain)))})):request<any>('/api/v3/topics?domain='+encodeURIComponent(domain)),
  trackedEvent:(id:string)=>request<any>('/api/v3/tracked-events/'+encodeURIComponent(id)),
  relation:(id:string)=>request<any>('/api/v3/event-relations/'+encodeURIComponent(id)),
  decideRelation:(id:string,body:any)=>request<any>('/api/v3/event-relations/'+encodeURIComponent(id)+'/decide',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}),
  withdrawRelation:(id:string,body:any)=>request<any>('/api/v3/event-relations/'+encodeURIComponent(id)+'/withdraw',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}),
  event:(id:string)=>isPublicResults?publicItem('events',id):request<any>('/api/v3/events/'+encodeURIComponent(id)),
  materials:(query:string)=>request<any>('/api/v3/materials?q='+encodeURIComponent(query)),
  sourceMaterials:(query:string)=>request<any>('/api/v3/source-materials?q='+encodeURIComponent(query)),
  brief:(days:number)=>request<any>('/api/v3/brief?days='+days),
  configure:(kind:string,body:any)=>request<any>('/api/v3/settings/'+kind,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}),
  feedback:(body:any)=>request<any>('/api/v3/feedback',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}),
};
