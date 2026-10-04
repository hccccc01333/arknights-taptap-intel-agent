/**
 * Design Tokens —— 锚点 Industrial + TapTap 真实品牌色
 *
 * 为什么是 Industrial 而不是 Retro-Futuristic：
 *   Retro-Futuristic（霓虹 + CRT 扫描线）会直接违背本项目设计稿 §30
 *   「禁止：赛博朋克泛滥 / 满屏 neon / 廉价玻璃拟态」。它是陷阱。
 *   Industrial 是情报终端的本来样子：pitch black、等宽字、平面、1px 硬线、无圆角无阴影。
 *   空间感由真实的 3D 星图提供，不由发光和特效提供。
 *
 * 品牌色来自 taptap.cn 实测（2026-10-03 抓取，非猜测）：
 *   #00B8A7 青绿（主色，出现 318 次）· #FF6F38 橙（次色，220 次）
 *   官方 Logo 已存 public/taptap-logo.svg（viewBox 0 0 91 24，真实 path）
 *
 * 色彩纪律：青绿 = 信号（数据状态）；橙 = 行动（要人做决定）。两者语义不混用。
 * 锚点硬约束（skill §3.2 "Breaks if"）：
 *   serif / 比例字体 / 暖色纸 / 颗粒 / 装饰阴影 / 圆角 —— 出现即锚点失效
 */

export const T = {
  color: {
    // Surface：pitch black / warm-black
    bg: "#0B0C0A",
    surface: "#101210",
    surfaceHi: "#161A16",
    // 1px 边框，不是阴影
    line: "#242A24",
    lineHi: "#38423A",
    // 文字
    tx: "#E4E8E2",
    tx2: "#9AA39A",
    tx3: "#69716A",
    // ★ TapTap 品牌色（实测）
    tap: "#00B8A7",            // 主色：信号、链接、Core、轴线
    tapDim: "#0A7A70",
    tapGlow: "rgba(0,184,167,0.13)",
    act: "#FF6F38",            // 次色：只用于"需要人行动"（跟进按钮、行动提示）
    danger: "#E0655B",
  },
  font: {
    // ★ 全局等宽（Industrial 硬约束）
    mono: '"IBM Plex Mono","JetBrains Mono",ui-monospace,SFMono-Regular,Consolas,monospace',
  },
  space: {
    depth: { UNIVERSE: 0, CLUSTER: 1, TOPIC: 2, INSIDE: 3, IDEA: 4 },
    radius: 0,                          // ★ 0 圆角
  },
  scale: {
    coreRadius: 8,
    planetMin: 1.1,
    planetMax: 5.2,
    /** 相关性引力轴：X 轴 -span(最相关) → +span(最不相关) */
    axisSpan: 96,
    scatter: 62,
  },
  motion: { hover: 0.14, panel: 0.28, focus: 0.7, fly: 1.05 },
} as const;

