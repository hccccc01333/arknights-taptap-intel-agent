/**
 * 全合成的 TapTap 增长情报演示数据。
 * 所有话题、数值、时间、摘要和建议均用于展示产品交互，未经采集或验证，
 * 不是实时新闻、真实舆情、用户数据或业务分析结论。未提供伪造的原文链接。
 * 此文件不读取本地数据库、报告、帐号、运行记录或采集内容，可安全用于公开演示。
 */
import type { Idea, Planet, Universe } from "./api";

export type DemoEvidence = { platform: string; title: string; excerpt: string; url?: string };
export type DemoTopicDetail = {
  summary: string;
  evidence: DemoEvidence[];
  recommendation: string;
  what_happened: { summary: string; trigger: string };
  evidence_panel: {
    facts: { tier: string; excerpt: string }[];
    community: { tier: string; excerpt: string }[];
    unknowns: { text: string }[];
  };
};
export type DemoHistoryPoint = {
  date: string;
  label: string;
  discussions: number;
  heat: number;
  sentiment: number;
  platforms: Record<string, number>;
};

type TopicSeed = {
  id: string; title: string; game: string; gameName: string; category: string;
  lifecycle: string; heat: number; velocity: number; contentCount: number;
  platforms: string[]; relevance: number; sentiment: number; change: number;
  summary: string; recommendation: string; evidence: [string, string, string];
  idea: string;
};

const seeds: TopicSeed[] = [
  {
    id: "demo-version-preview", title: "原神 · 版本前瞻讨论升温", game: "genshin", gameName: "原神",
    category: "版本动态", lifecycle: "GROWING", heat: 0.94, velocity: 0.87, contentCount: 18420,
    platforms: ["bilibili", "taptap", "weibo", "baidu"], relevance: 0.97, sentiment: 0.76, change: 32.8,
    summary: "演示情境：版本前瞻引发角色机制、探索玩法与资源规划的交叉讨论。视频社区承担第一波传播，玩家社区逐步出现更具体的问答需求。",
    recommendation: "组织一篇前瞻信息索引与版本准备清单，以官方信息为准；在社区设置问答入口，观察讨论是否持续转化为关注与预约。",
    evidence: ["演示样本 · 前瞻亮点盘点引发集中讨论", "演示样本 · 角色培养与版本准备问答增长", "演示样本 · 前瞻相关搜索意图上升"],
    idea: "版本准备清单与社区问答",
  },
  {
    id: "demo-collaboration", title: "崩坏：星穹铁道 · 联动创作热议", game: "star-rail", gameName: "崩坏：星穹铁道",
    category: "跨圈传播", lifecycle: "PEAKING", heat: 0.88, velocity: 0.71, contentCount: 15280,
    platforms: ["weibo", "bilibili", "taptap"], relevance: 0.93, sentiment: 0.83, change: 24.6,
    summary: "演示情境：联动话题在游戏玩家与泛兴趣用户之间传播，二次创作成为主要讨论载体。参与方式、联动内容与角色表达构成不同需求。",
    recommendation: "围绕二次创作建立作品合集，并拆分游戏内参与攻略与泛兴趣介绍。先核实联动素材使用规则，再安排创作活动。",
    evidence: ["演示样本 · 联动话题形成跨圈转发", "演示样本 · 同人视频与剪辑集中出现", "演示样本 · 社区用户询问参与方式"],
    idea: "联动创作精选与参与指南",
  },
  {
    id: "demo-indie-discovery", title: "独立游戏 · 新品发现与试玩口碑", game: "indie", gameName: "独立游戏",
    category: "新品发现", lifecycle: "EMERGING", heat: 0.79, velocity: 0.94, contentCount: 8920,
    platforms: ["taptap", "bilibili", "baidu"], relevance: 0.96, sentiment: 0.88, change: 48.2,
    summary: "演示情境：几款独立游戏的试玩体验开始被玩家分享，美术风格、上手门槛与单局节奏成为核心话题。整体规模较小，但增长斜率较高。",
    recommendation: "建设可试玩新品的编辑精选，标明适合人群与游玩时长；邀请体验者补充优缺点，用真实反馈帮助用户完成发现。",
    evidence: ["演示样本 · 试玩体验帖获得社区关注", "演示样本 · 小体量游戏推荐视频增多", "演示样本 · 新品名称与试玩入口搜索增长"],
    idea: "本周独立游戏试玩精选",
  },
  {
    id: "demo-exhibition", title: "游戏展 · 玩家现场体验与新作线索", game: "game-expo", gameName: "游戏展",
    category: "行业事件", lifecycle: "GROWING", heat: 0.83, velocity: 0.78, contentCount: 11630,
    platforms: ["weibo", "bilibili", "baidu", "taptap"], relevance: 0.84, sentiment: 0.79, change: 21.4,
    summary: "演示情境：游戏展相关内容分为现场体验、试玩评价与新作信息三类。现场视觉内容传播较快，后续的游戏体验细节更适合社区沉淀。",
    recommendation: "建立按游戏组织的体验索引，汇总玩家现场观察；将现场感受与已确认产品信息分开呈现，并补充来源核验。",
    evidence: ["演示样本 · 展会现场见闻形成传播波峰", "演示样本 · 实机试玩视频受到关注", "演示样本 · 社区讨论哪些新作值得预约"],
    idea: "新作体验索引与玩家笔记",
  },
  {
    id: "demo-steam-festival", title: "Steam 新品节 · 试玩推荐扩散", game: "steam-festival", gameName: "Steam 新品节",
    category: "新品发现", lifecycle: "GROWING", heat: 0.81, velocity: 0.86, contentCount: 9740,
    platforms: ["bilibili", "taptap", "baidu"], relevance: 0.89, sentiment: 0.86, change: 35.7,
    summary: "演示情境：新品节试玩推荐形成长尾发现链条。类型偏好、设备需求和多人玩法成为筛选标准，榜单之外的作品也有讨论机会。",
    recommendation: "按玩法而非单一热度策划试玩路线，给出体验时间和设备需求。邀请玩家补充遗漏作品，形成可持续更新的收藏清单。",
    evidence: ["演示样本 · 多款试玩推荐进入视频讨论", "演示样本 · 玩家寻找适合自己的游戏类型", "演示样本 · 试玩配置与多人玩法搜索增多"],
    idea: "按玩法整理的试玩路线",
  },
  {
    id: "demo-wuthering-update", title: "鸣潮 · 新版本探索攻略需求", game: "wuthering-waves", gameName: "鸣潮",
    category: "版本动态", lifecycle: "GROWING", heat: 0.85, velocity: 0.83, contentCount: 12380,
    platforms: ["taptap", "bilibili", "weibo"], relevance: 0.95, sentiment: 0.74, change: 29.1,
    summary: "演示情境：新版本讨论集中于地图探索与新机制理解。短视频快速解答单个问题，社区用户希望把分散内容整理成可检索的完整路线。",
    recommendation: "策划探索攻略专题，将剧透内容折叠；为机制、路线与资源收集提供分类入口，观察收藏、提问与内容补充情况。",
    evidence: ["演示样本 · 社区出现地图探索求助", "演示样本 · 新机制解释视频播放增长", "演示样本 · 玩家分享探索截图与路线"],
    idea: "探索路线专题与机制百科",
  },
  {
    id: "demo-domestic-games", title: "国产游戏 · 美术与叙事讨论", game: "domestic-games", gameName: "国产游戏",
    category: "跨圈传播", lifecycle: "PEAKING", heat: 0.77, velocity: 0.56, contentCount: 8430,
    platforms: ["weibo", "bilibili", "baidu"], relevance: 0.75, sentiment: 0.71, change: 12.4,
    summary: "演示情境：国产游戏的视觉表达与叙事风格吸引泛兴趣讨论，玩家评价存在不同关注点。作品片段的传播效果与实际游玩反馈需要分开理解。",
    recommendation: "制作美术与叙事主题讨论，保留不同观点及其理由；补充体验条件与版本信息，避免用少量片段概括完整产品体验。",
    evidence: ["演示样本 · 美术片段引发泛兴趣转发", "演示样本 · 玩家分析叙事与玩法关系", "演示样本 · 作品背景与体验评价搜索增长"],
    idea: "玩家视角的美术与叙事专题",
  },
  {
    id: "demo-guide-creators", title: "社区攻略 · 创作者共建知识库", game: "community", gameName: "玩家社区",
    category: "社区创作", lifecycle: "EMERGING", heat: 0.72, velocity: 0.89, contentCount: 6870,
    platforms: ["taptap", "bilibili"], relevance: 0.99, sentiment: 0.91, change: 41.8,
    summary: "演示情境：玩家开始整理跨版本攻略，创作者关注内容更新与查找效率。高质量回答能够形成长尾访问，但需要注明适用版本。",
    recommendation: "邀请创作者共建带版本标签的攻略索引，标注审核与更新日期；优先整理高频问题，设置纠错入口并认可贡献者。",
    evidence: ["演示样本 · 高频问答被玩家收藏", "演示样本 · 攻略创作者讨论内容维护", "演示样本 · 视频评论区请求补充文字索引"],
    idea: "带版本标签的攻略共建计划",
  },
  {
    id: "demo-multiplayer", title: "多人游戏 · 组队体验与好友邀请", game: "multiplayer", gameName: "多人游戏",
    category: "社区创作", lifecycle: "GROWING", heat: 0.69, velocity: 0.73, contentCount: 5940,
    platforms: ["taptap", "weibo", "bilibili"], relevance: 0.86, sentiment: 0.82, change: 18.3,
    summary: "演示情境：多人游戏讨论围绕匹配体验、组队需求与朋友邀请展开。轻量玩法介绍更容易被转发，社区组队信息需要有效期。",
    recommendation: "设计玩法标签与有效期明确的组队专区，配合新手协作指南；用成功组队与后续参与情况评估活动效果。",
    evidence: ["演示样本 · 玩家发布好友组队需求", "演示样本 · 轻量协作玩法被分享", "演示样本 · 新手询问组队门槛"],
    idea: "新手协作指南与组队专区",
  },
  {
    id: "demo-puzzle", title: "解谜游戏 · 短内容发现机会", game: "puzzle", gameName: "解谜游戏",
    category: "新品发现", lifecycle: "EMERGING", heat: 0.64, velocity: 0.81, contentCount: 4260,
    platforms: ["bilibili", "taptap"], relevance: 0.82, sentiment: 0.89, change: 37.2,
    summary: "演示情境：解谜游戏的单个巧妙机制开始通过短内容传播。用户关心是否有剧透、难度与提示设计，发现后的完整体验仍需进一步验证。",
    recommendation: "发布无剧透机制介绍与难度说明，为试玩设置明确入口；观察用户是否从观看转向讨论和试玩体验反馈。",
    evidence: ["演示样本 · 解谜机制演示引发兴趣", "演示样本 · 社区讨论提示与难度", "演示样本 · 玩家请求无剧透介绍"],
    idea: "无剧透解谜发现清单",
  },
  {
    id: "demo-retro", title: "经典游戏 · 重玩体验重新活跃", game: "retro", gameName: "经典游戏",
    category: "社区创作", lifecycle: "REACTIVATED", heat: 0.59, velocity: 0.62, contentCount: 3510,
    platforms: ["taptap", "bilibili", "weibo"], relevance: 0.78, sentiment: 0.85, change: 16.9,
    summary: "演示情境：经典作品的重玩分享重新获得关注，老玩家故事与新玩家体验交汇。推荐理由和当前可玩方式是讨论的主要信息需求。",
    recommendation: "组织经典作品重玩日志，鼓励分享具体玩法体验；核实当前平台可用性和版本差异，为新玩家补充入门说明。",
    evidence: ["演示样本 · 重玩日志重新进入社区讨论", "演示样本 · 老玩家回忆视频获得互动", "演示样本 · 新玩家询问当前可玩方式"],
    idea: "经典作品重玩日志征集",
  },
  {
    id: "demo-hardware", title: "移动游戏 · 设备体验与性能反馈", game: "mobile-experience", gameName: "移动游戏",
    category: "体验反馈", lifecycle: "DECLINING", heat: 0.52, velocity: 0.31, contentCount: 2980,
    platforms: ["taptap", "baidu", "bilibili"], relevance: 0.73, sentiment: 0.58, change: -8.6,
    summary: "演示情境：设备体验讨论由传播高峰进入持续反馈阶段。帧率、画质和发热相关体验依赖设备与设置，需要更明确的记录条件。",
    recommendation: "整理包含设备、版本与画质设置的体验反馈模板，分开记录主观感受与可复现问题；将高频问题交由运营进一步核验。",
    evidence: ["演示样本 · 社区分享设备体验", "演示样本 · 画质与性能设置搜索增加", "演示样本 · 玩家比较不同设置的体验"],
    idea: "标准化设备体验反馈征集",
  },
];

const planets: Planet[] = seeds.map((seed, index) => ({
  id: seed.id, title: seed.title, game: seed.game, gameName: seed.gameName,
  lifecycle: seed.lifecycle, heat: seed.heat, velocity: seed.velocity,
  confidence: 0.92 - index * 0.01,
  contentCount: seed.contentCount, platformCount: seed.platforms.length,
  platforms: seed.platforms, entities: [`game_${seed.game}`, `category_${seed.category}`],
  relevance: seed.relevance, relevanceLevel: "L1", relevanceNote: "合成示例相关性，未经真实分析",
  nIdeas: 2, firstSeen: `2026-10-0${Math.min(5, 1 + Math.floor(index / 3))}T08:00:00+08:00`,
  radius: 2.1 + seed.heat * 3, glowRank: seed.velocity, density: seed.contentCount / 18420,
  category: seed.category, sentiment: seed.sentiment, change: seed.change,
}));

const ideas: Record<string, Idea[]> = Object.fromEntries(seeds.map((seed) => [seed.id, [
  { id: `${seed.id}-curation`, name: seed.idea, type: "内容策划", score: seed.relevance, passed: false },
  { id: `${seed.id}-conversation`, name: "高频问题收集与玩家反馈", type: "社区运营", score: 0.81, passed: false },
]]));

export const demoUniverse: Universe = {
  planets,
  core: {
    name: "TapTap 全网增长情报",
    games: seeds.map(({ game, gameName }) => ({ key: game, name: gameName })),
    topicCount: planets.length,
    multiPlatformCount: planets.filter((planet) => planet.platformCount > 1).length,
  },
  pipes: [
    { platform: "bilibili", count: 34270 }, { platform: "taptap", count: 30950 },
    { platform: "weibo", count: 26780 }, { platform: "baidu", count: 16360 },
  ],
  relevanceCensus: { real: planets.length, proxy: 0, unknown: 0 },
  degradeNotes: [
    "演示模式：所有数值、话题情境、摘要与建议均为合成示例，不代表真实采集或分析结果。",
    "示例中的 L1 标签仅用于展示相关性界面；没有经过 AI 实测或人工核验。",
    "示例证据没有原文链接，不能作为实际业务判断依据。",
  ],
  ideas,
};

export function demoTopic(id: string): DemoTopicDetail {
  const seed = seeds.find((topic) => topic.id === id);
  if (!seed) throw new Error("未找到这个演示话题，请重新选择。");
  const evidence = seed.evidence.map((title, index) => ({
    platform: seed.platforms[index % seed.platforms.length], title,
    excerpt: `${title}。这是一条合成示例，不是实际采集记录。`,
  }));
  return {
    summary: seed.summary, evidence, recommendation: seed.recommendation,
    what_happened: { summary: seed.summary, trigger: seed.summary },
    evidence_panel: {
      facts: [], community: evidence.map(({ excerpt }) => ({ tier: "DEMO", excerpt })),
      unknowns: [{ text: "该情境尚未采集真实证据；规模、传播趋势与增长机会均待核验。" }],
    },
  };
}

function historyPoint(date: string, label: string, discussions: number, heat: number): DemoHistoryPoint {
  const bilibili = Math.round(discussions * 0.34);
  const taptap = Math.round(discussions * 0.31);
  const weibo = Math.round(discussions * 0.24);
  return {
    date, label, discussions, heat, sentiment: 0.78,
    platforms: { bilibili, taptap, weibo, baidu: discussions - bilibili - taptap - weibo },
  };
}

/** 固定日期的合成日/周聚合，只用于切换与图表演示，不随系统时间伪装为实时。 */
export const demoHistory: { daily: DemoHistoryPoint[]; weekly: DemoHistoryPoint[] } = {
  daily: [
    historyPoint("2026-09-29", "09.29", 56240, 0.54),
    historyPoint("2026-09-30", "09.30", 64980, 0.59),
    historyPoint("2026-10-01", "10.01", 62510, 0.57),
    historyPoint("2026-10-02", "10.02", 81340, 0.66),
    historyPoint("2026-10-03", "10.03", 76480, 0.63),
    historyPoint("2026-10-04", "10.04", 98520, 0.75),
    historyPoint("2026-10-05", "10.05", 108360, 0.82),
  ],
  weekly: [
    historyPoint("2026-08-24", "08.24", 267840, 0.42),
    historyPoint("2026-08-31", "08.31", 312560, 0.48),
    historyPoint("2026-09-07", "09.07", 296430, 0.45),
    historyPoint("2026-09-14", "09.14", 384970, 0.58),
    historyPoint("2026-09-21", "09.21", 421080, 0.66),
    historyPoint("2026-09-28", "09.28", 517350, 0.76),
    historyPoint("2026-10-05", "10.05", 608240, 0.82),
  ],
};
