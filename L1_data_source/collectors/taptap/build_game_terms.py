#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""L1_data_source/collectors/taptap/build_game_terms.py — 游戏词表（多来源合并）。

★ 这张词表唯一的职责：判断一条外部热点「是不是游戏话题」。
  它必须**全**——漏一个游戏名，那条热点的游戏属性就没了，
  后面所有环节（forming 判定 / LLM 相关性分级 / 创意生成）都无从谈起。

★ 为什么不能只靠 TapTap 索引（2026-10-05 用户指出"词表要做全"）：
  TapTap 索引有三重缺口，实测漏掉 41/66 个国内主流游戏：
    ① 枚举未覆盖完整 —— app_id 枚举只扫到 2101（目标 20000），
       和平精英 / 英雄联盟 / 恋与深空 这些都在更高的 ID 段，索引里根本没有
    ② 标点差异 —— 索引里是「光·遇」，热点里写「光遇」；「崩坏：星穹铁道」vs「崩坏星穹铁道」
    ③ 根本不上架 —— 塞尔达 / 宝可梦 / 动物森友会 这类主机&Switch 游戏 TapTap 没有社区，
       但它们在百度/微博/贴吧的热度不比任何手游低
  所以词表 = 索引 ∪ 贴吧吧名 ∪ B站游戏区 ∪ 媒体标题 ∪ 手写主流盘。

★ 五个来源各自的作用：
    S1 索引   —— 覆盖面最广（2695 → 枚举跑完后更多），带社区规模信息
    贴吧吧名  —— 人工维护的"当下真的有人在玩"的名单，质量最高
    B站游戏区 —— UP 主名常常就是游戏官方号（崩坏星穹铁道/原神…），高置信度
    媒体标题  —— 机核/3DM 的标题里带大量中小游戏名，补长尾
    手写盘    —— 前四源都覆盖不到的头部游戏（主机/Steam/海外）

★ 清洗规则（各来源共用）：
    - 状态/占位名（「该游戏已下架」「暂无」…）→ 丢
    - 纯英文无中文 → 丢（对中文舆情无价值；但 CS/GO 这类会走"手写盘"补回）
    - 过短（<2 字符）→ 丢
    - 括号内的版本号/英文别名 → 剥掉主名
    - 商标符号 → 剥掉
    - **标点归一**：中文冒号/中点/间隔号/全角空格统一剥掉
      （「崩坏：星穹铁道」和「崩坏星穹铁道」在中文热点里是同一个词）
"""

from __future__ import annotations

import argparse
import csv
import json
import re
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List

ROOT = Path(__file__).resolve().parents[3]
MAP_CSV = ROOT / "data" / "raw" / "taptap" / "community" / "community_map.csv"
TIEBA_DIR = ROOT / "data" / "raw" / "tieba" / "forums"
BILI_CSV = ROOT / "data" / "raw" / "bilibili" / "hot_videos.csv"
MEDIA_CSV = ROOT / "data" / "raw" / "gamemedia" / "news.csv"
OUT_TERM_TABLE = ROOT / "data" / "state" / "game_term_table.json"

# 枚举器会碰到的状态/占位名，不是游戏
JUNK_NAMES = {
    "该游戏已下架", "已下架", "下架", "该应用已下架", "未知", "暂无", "更多",
    "TapTap", "taptap", "首页", "游戏", "app", "话题", "全部", "推荐",
    "关注", "热门", "最新", "综合", "详情", "下载", "评分",
}

# 前四个来源都覆盖不到的**头部游戏**：主机 / Steam / 海外 / 无社区但在中文圈很热。
# 这不是"补漏"，而是承认 TapTap 索引的天花板——它按上架渠道划界，中文舆情不按这个划。
MANUAL_TERMS = [
    # 主机 / Steam
    "塞尔达传说", "宝可梦", "动物森友会", "马里奥", "马力欧赛车", "喷射战士",
    "双人成行", "黑神话悟空", "艾尔登法环", "博德之门", "文明6", "星露谷物语",
    "泰拉瑞亚", "杀戮尖塔", "鬼泣5", "巫师3", "赛博朋克", "空洞骑士",
    "奥伯拉丁的回归", "星之卡比", "纸嫁衣", "太吾绘卷", "了不起的修仙模拟器",
    # 射击 / 竞技
    "无畏契约", "使命召唤", "堡垒之夜", "彩虹六号", "APEX英雄", "守望先锋",
    "火箭联盟", "DOTA2", "CSGO", "求生之路", "泰坦陨落", "彩虹六号围攻",
    "CS2", "cs2", "CSGO", "csgo", "绝地求生", "APEX英雄", "apex英雄",
    # 沙盒 / 联机派对
    "我的世界", "迷你世界", "蛋仔派对", "香肠派对", "元梦之星",
    "罗布乐思", "恐鬼症", "肥鹅健身房", "鹅鸭杀", "光遇", "蛋仔",
    # 二次元 / 抽卡
    "原神", "崩坏3", "崩坏星穹铁道", "绝区零", "鸣潮", "明日方舟", "少女前线",
    "碧蓝航线", "阴阳师", "第五人格", "恋与深空", "光与夜之恋", "世界之外",
    "未定事件簿", "代号鸢", "恋与制作人", "星痕共鸣", "二重螺旋", "异环",
    "白荆回廊", "铃兰之剑", "重返未来", "斯露德", "明日方舟终末地",
    # MMO / 端游手游通吃
    "逆水寒", "剑网3", "天涯明月刀", "梦幻西游", "大话西游", "塞尔达",
    "黑色沙漠", "最终幻想14", "魔兽世界", "炉石传说", "英雄联盟", "王者荣耀",
    "和平精英", "金铲铲之战", "三角洲行动", "永劫无间", "燕云十六声",
    "星球重启", "卡拉彼丘", "七日世界", "归唐", "杖剑传说", "菇勇者传说",
    "英雄没有闪", "咸鱼之王", "寻道大千", "躺平发育", "无限暖暖",
    # 棋牌 / 休闲
    "三国杀", "欢乐三国杀", "欢乐斗地主", "开心消消乐", "羊了个羊",
    # ── 通用简称（2026-10-05 实测：B站视频标题高频用圈内简称，
    #    「【崩铁】遐蝶测评」这种全角括号+简称的写法，原词表完全扫不到）
    "崩铁", "星铁", "崩三", "崩2", "舟游", "方舟",
    "米游", "鹰角游戏", "库洛游戏", "叠纸游戏",
    "王者", "联盟", "吃鸡手游", "端游", "手游游",
    "崩崩", "绝零", "米哈游游戏",
    # ── B站/贴吧高频具体游戏（2026-10-05 抽检漏判样本补录）
    "球球大作战", "phigros", "音游", "影之刃", "元梦之星",
    "迷你世界", "香肠派对", "蛋仔", "炉石传说", "DOTA2",
    "斗罗大陆", "一拳超人", "海贼王", "宝可梦大集结", "马力欧",
    # ── 系列总称（一个词覆盖全系列：暖暖=无限/奇迹/闪耀暖暖）；
    #    叠纸系游戏不在 TapTap，索引里一个都没有，只能手写
    "暖暖", "恋与", "雀魂", "游戏王", "云顶之弈", "大征服者", "金铲铲之战",
    # ── 端游常青树 / 海外经典（百度游戏榜常年在榜，但 TapTap 无社区、贴吧无吧）
    "复古传奇", "传奇", "坦克世界", "荒野大镖客", "荒野大镖客2", "绝地求生",
    "求生之路2", "求生之路", "红色警戒", "星际争霸", "魔兽争霸",
    "刀塔传奇", "地下城与勇士", "DNF", "穿越火线", "英雄联盟手游",
    "金铲铲", "魔兽世界怀旧服", "大话西游2", "植物大战僵尸",
    # ── 单机 / 主机大作（中文圈有讨论量但零社区覆盖）
    "博德之门3", "赛博朋克2077", "2077", "影之刃零", "明末渊虚之羽",
    "匹诺曹的谎言", "哈迪斯2", "星空", "辐射4", "巫师3", "消逝的光芒",
    "地平线5", "漫威蜘蛛侠", "潜水员戴夫", "小丑牌", "动物派对", "星露谷",
    # ── 国产独立 / Steam 新热
    "暖雪", "鬼谷八荒", "太吾绘卷", "了不起的修仙模拟器", "隐形守护者",
    "中国式家长", "笼中窥梦", "文字化化",
]

# 标点归一：中文热点里这些标点经常被省略或换写法
_PUNCT_MAP = str.maketrans({
    "：": "", "·": "", "・": "", "•": "", "．": "", "・": "",
    "－": "", "—": "", "－": "", "~": "", "・": "", "・": "",
    "　": " ", "－": "", "—": "",
})

_CJK = re.compile(r"[\u4e00-\u9fff]")


def clean_title(raw: str) -> str:
    """把任意来源的游戏名清洗成可用的词表条目。返回空串表示无效。"""
    t = (raw or "").strip()
    if not t:
        return ""
    t = t.replace("™", "").replace("®", "").replace("©", "")
    t = re.sub(r"^【[^】]*】|^\[[^\]]*\]", "", t)          # 剥掉【】前缀标签
    t = t.split("(")[0].split("（")[0].split("[")[0].split("【")[0].strip()
    t = t.translate(_PUNCT_MAP)
    t = re.sub(r"\s+", " ", t).strip()
    if not t or t in JUNK_NAMES:
        return ""
    if len(t) < 2:
        return ""
    if t.lower().startswith("app_"):
        return ""
    # 纯英文（无中文）→ 对中文舆情无价值，丢（英文名走 MANUAL_TERMS 补回）
    if not _CJK.search(t):
        return ""
    return t[:30]


def _read_csv(path: Path) -> List[Dict[str, str]]:
    if not path.exists():
        return []
    try:
        with path.open(encoding="utf-8-sig", newline="") as f:
            return list(csv.DictReader(f))
    except (OSError, UnicodeDecodeError):
        return []


def from_taptap_index() -> Counter:
    """来源①：S1 社区索引 —— 覆盖面最广，且带社区规模（count = 出现次数）。"""
    c: Counter = Counter()
    for r in _read_csv(MAP_CSV):
        n = clean_title(r.get("title") or "")
        if n:
            c[n] += 1
    return c


def from_tieba_forums() -> Counter:
    """来源②：贴吧吧目录名 —— 人工维护的"当下真有人在玩"名单，质量最高。

    目录名本身就是吧名，一个吧 = 一个游戏话题。覆盖了 TapTap 索引缺失的
    和平精英/光遇/恋与深空/无限暖暖 等（那些游戏在 TapTap 无社区或枚举未覆盖）。
    """
    c: Counter = Counter()
    if not TIEBA_DIR.is_dir():
        return c
    for d in TIEBA_DIR.iterdir():
        if d.is_dir():
            n = clean_title(d.name)
            if n:
                c[n] += 1
    return c


def from_bilibili() -> Counter:
    """来源③：B站游戏区排行榜 —— UP 主名常常就是游戏官方号（高置信度）。"""
    c: Counter = Counter()
    for r in _read_csv(BILI_CSV):
        # author 字段：官方号名 = 游戏名；个人 UP 名会在这里被 clean_title 过滤掉一部分
        n = clean_title(r.get("author") or "")
        if n and 2 <= len(n) <= 12:      # 官方号名一般短；过长的多半是个人 UP
            c[n] += 1
    return c


def from_gamemedia() -> Counter:
    """来源④：游戏媒体标题 —— 机核/3DM/游民 的标题里带大量中小游戏名，补长尾。

    从标题里剥掉常见的媒体/栏目修饰词，剩下的候选送进 clean_title。
    不做严格的词边界判定——宁可多收，由下游 LLM 三级分级（相关/相邻/无关）兜底。
    """
    c: Counter = Counter()
    noise = re.compile(
        r"(评测|攻略|前瞻|速报|新闻|视频|直播|开测|上线|定档|专访|访谈|盘点|"
        r"合集|教程|抽卡|福利|爆料|解析|杂谈|上手|体验|试玩|前瞻|更新|版本|"
        r"实机|演示|上线|发售|开箱|杂谈|锐评|冷饭|考据|考据)")
    for r in _read_csv(MEDIA_CSV):
        title = r.get("title") or ""
        # 用《》「」『』包住的通常是作品/游戏名 —— 优先取
        for m in re.findall(r"[《【「『]([^》】」』]{2,20})[》】」』]", title):
            n = clean_title(noise.sub("", m))
            if n:
                c[n] += 1
    return c


def build() -> Dict[str, Any]:
    all_terms: Dict[str, int] = {}
    sources: Dict[str, int] = {}

    for label, c in (
        ("taptap_index", from_taptap_index()),
        ("tieba_forums", from_tieba_forums()),
        ("bilibili", from_bilibili()),
        ("gamemedia", from_gamemedia()),
    ):
        sources[label] = len(c)
        for name, n in c.items():
            all_terms[name] = max(all_terms.get(name, 0), n)

    # 手写盘：所有来源都覆盖不到的头部游戏（count=0，标记为"补充"）
    # ★ 手写盘**不过** clean_title 的"纯英文→丢"规则 —— CS2 / DOTA2 / APEX 这类
    #   在中文热点里也常直接用英文名出现（"CS2 新版本"），把它们滤掉等于自己关掉一扇门。
    manual_added = 0
    for t in MANUAL_TERMS:
        n = clean_title(t) or t.strip()
        if n and n not in all_terms:
            all_terms[n] = 0
            manual_added += 1

    # 自有游戏档案（我们重点关注的）：games/*.json
    own: set = set()
    gdir = ROOT / "games"
    if gdir.is_dir():
        for fn in gdir.glob("*.json"):
            try:
                d = json.loads(fn.read_text(encoding="utf-8"))
            except (ValueError, OSError):
                continue
            for k in [d.get("name"), d.get("key")] + list(d.get("aliases") or []):
                if k:
                    own.add(clean_title(str(k)) or str(k))

    index_rows = _read_csv(MAP_CSV)
    return {
        "ok": True,
        "index_total": len(index_rows),
        "valid_terms": len(all_terms),
        "by_source": sources,
        "manual_added": manual_added,
        "own_games": sorted(own),
        "top": Counter(all_terms).most_common(30),
        "terms": dict(all_terms),
    }



def _csv_mtime() -> float:
    try:
        return MAP_CSV.stat().st_mtime
    except OSError:
        return -1.0


def watch(out: str, interval: int) -> int:
    """常驻：S1 枚举器每写一批 community_map.csv，这里就重建一次词表。

    ★ 为什么（2026-10-05）：枚举器要扫 20000 个 app_id（约 5 小时），
      词表若只在枚举结束后重建一次，中间几小时采集进程用的是旧词表。
      采集侧 game_terms.get_matcher() 每 5 分钟按 mtime 热更新，
      这边只要保证词表文件跟住索引就行 —— 两端合起来全链路自动滚动。
    """
    import time
    last_built = -1.0
    while True:
        mtime = _csv_mtime()
        if mtime > last_built:
            res = build()
            if res.get("ok"):
                outp = Path(out)
                outp.parent.mkdir(parents=True, exist_ok=True)
                outp.write_text(json.dumps({
                    "generated_at": datetime.now().isoformat(timespec="seconds"),
                    "index_total": res["index_total"],
                    "valid_terms": res["valid_terms"],
                    "by_source": res["by_source"],
                    "manual_added": res["manual_added"],
                    "own_games": res["own_games"],
                    "terms": res["terms"],
                }, ensure_ascii=False, indent=2), encoding="utf-8")
                print(f"[watch] 词表已重建：{res['valid_terms']} 个游戏名"
                      f"（索引 {res['index_total']}）", flush=True)
                last_built = mtime
            else:
                print(f"[watch] build 失败：{res.get('reason')}", flush=True)
                last_built = mtime     # 失败也推进，避免同一坏状态反复打日志
        time.sleep(max(60, interval))


def main() -> int:
    ap = argparse.ArgumentParser(description="多来源合并生成游戏词表")
    ap.add_argument("--out", default=str(OUT_TERM_TABLE))
    ap.add_argument("--print", type=int, default=30, help="打印前 N 个")
    ap.add_argument("--watch", action="store_true",
                    help="常驻：索引 csv 有变化时自动重建词表（配合 app_id 枚举器）")
    ap.add_argument("--interval", type=int, default=600, help="watch 检查间隔（秒）")
    args = ap.parse_args()

    if args.watch:
        return watch(args.out, args.interval)

    res = build()
    if not res.get("ok"):
        print(f"[error] {res.get('reason')}")
        return 1

    print(f"词表 {res['valid_terms']} 个游戏名（多来源合并）")
    for k, v in res["by_source"].items():
        print(f"    {k:<16} {v:>5}")
    print(f"    {'manual_only':<16} {res['manual_added']:>5}  (前四源都没覆盖，靠手写盘补)")
    print(f"\n自有游戏档案 {len(res['own_games'])} 个: {', '.join(res['own_games'])}")

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "index_total": res["index_total"],
        "valid_terms": res["valid_terms"],
        "by_source": res["by_source"],
        "manual_added": res["manual_added"],
        "own_games": res["own_games"],
        "terms": res["terms"],
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n词表已写 {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())