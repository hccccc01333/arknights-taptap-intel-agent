/**
 * 3D 卡片布局 —— 沿用 TapTap 首页：分类轨道 + 卡片网格。
 *
 * 关键约束（实测算出来的，不是拍脑袋）：
 *   211 张卡片若铺成 8 列 × 27 行，纵向跨度 698 单位，相机要退到 Z=910 才能看全 ——
 *   那样一张卡只剩几个像素，等于没渲染。
 *   所以改成**固定 3 行的横向长廊**：横向延伸（滚轮/拖动走位），纵向恒定。
 *
 * 相关性 → 横向位置（引力轴）：越靠左越该看。卡片墙同时是一张可读的散点图。
 */

import type { Planet } from "./api";

function hash01(s: string): number {
  let h = 2166136261;
  for (let i = 0; i < s.length; i++) { h ^= s.charCodeAt(i); h = Math.imul(h, 16777619); }
  return ((h >>> 0) % 100000) / 100000;
}

export type Placed = {
  planet: Planet;
  pos: [number, number, number];
  col: number; row: number;
  band: number;
};

export const CARD = { w: 16, h: 21.3 };        // TapTap 竖版卡 3:4
const GAP_X = 18.5;                             // 略大于卡宽 → 留缝不重叠
const GAP_Y = 24;
const ROWS = 3;

export function layout(planets: Planet[]): Placed[] {
  const sorted = [...planets].sort((a, b) => {
    const ra = a.relevance == null ? 1.05 : a.relevance;
    const rb = b.relevance == null ? 1.05 : b.relevance;
    if (Math.abs(rb - ra) > 0.12) return rb - ra;      // 先分带
    return (b.heat ?? 0) - (a.heat ?? 0);              // 带内按热度
  });
  return sorted.map((p, i) => {
    const rel = p.relevance;
    const band = rel == null ? 4 : rel >= 0.65 ? 0 : rel >= 0.45 ? 1 : rel >= 0.3 ? 2 : 3;
    const col = Math.floor(i / ROWS);
    const row = i % ROWS;
    return {
      planet: p,
      pos: [col * GAP_X, (ROWS / 2 - row) * GAP_Y - GAP_Y / 2, (hash01(p.id + "z") - 0.5) * 6],
      col, row, band,
    };
  });
}

/** 长廊总长（相机/滚动范围用） */
export function corridorLength(placed: Placed[]): number {
  if (!placed.length) return 0;
  return Math.max(...placed.map((p) => p.pos[0])) + CARD.w;
}
