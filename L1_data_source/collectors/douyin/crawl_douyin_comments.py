#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""抖音轻量评论采集（对照样本，非全网中台）

策略（按优先级）：
1) 关键词搜索视频（需 Cookie / 易触发验证码）
2) 固定 aweme_id 列表拉评论（config / CLI）
3) 直播失败且允许降级时：写出演示种子语料（source=fallback_seed），保证仓库可演示

统一字段：source / comment_id / text / publish_time / aweme_id / video_url / 点赞等。
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import random
import re
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import quote, unquote

import requests

MOD_DIR = Path(__file__).resolve().parent
ROOT = Path(__file__).resolve().parents[3]   # 项目根（分层重排后脚本在 L1_data_source/collectors/douyin/）
DATA_DIR = ROOT / "data" / "raw" / "douyin"  # 数据落回 data/raw/，注册表 internal connector 从这里读
RAW_DIR = DATA_DIR / "json"
REPORT_DIR = DATA_DIR / "reports"
RUN_LOG_DIR = DATA_DIR / "run_logs"
OUT_CSV = DATA_DIR / "comments_sample.csv"
VIDEOS_CSV = DATA_DIR / "videos_sample.csv"
CHECKPOINT = DATA_DIR / "checkpoint_crawl.json"

TZ_CN = timezone(timedelta(hours=8))
DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

COMMENT_FIELDS = [
    "comment_id",
    "cid",
    "aweme_id",
    "video_title",
    "video_url",
    "author_uid_hash",
    "user_uid_hash",
    "text",
    "like_count",
    "reply_count",
    "publish_time",
    "publish_time_cn",
    "keyword",
    "source",
    "crawled_at",
    "raw_json_path",
]

VIDEO_FIELDS = [
    "aweme_id",
    "title",
    "url",
    "author_name",
    "author_uid_hash",
    "digg_count",
    "comment_count",
    "keyword",
    "source",
    "crawled_at",
]

# 公开视频元数据（用于固定列表 / 种子降级；标题来自公开页）
SEED_VIDEOS: list[dict[str, str]] = [
    {"aweme_id": "7644551815031213346", "title": "就瞅弭弗这智商...抽就完事了！", "keyword": "明日方舟终末地"},
    {"aweme_id": "7643769364273821658", "title": "可露希尔：都是同一分支我原本没想降维打击！", "keyword": "明日方舟七周年"},
    {"aweme_id": "7650357747275386175", "title": "明日方舟终末地危机合约先导PV发布", "keyword": "明日方舟终末地"},
    {"aweme_id": "7646759737765186862", "title": "喜欢厨流水 怎么不厨水龙头？", "keyword": "明日方舟"},
    {"aweme_id": "7644071936439389503", "title": "怪猎联动焰狐龙梓兰技能演示", "keyword": "明日方舟怪猎联动"},
    {"aweme_id": "7634965899007471590", "title": "7周年庆限定寻访基础获取统计", "keyword": "明日方舟七周年"},
    {"aweme_id": "7635290343324233458", "title": "七年里我们一路走来的故事，欢迎回家凯尔希", "keyword": "明日方舟七周年"},
    {"aweme_id": "7632697652707553198", "title": "2分钟带你看懂方舟7周年庆凯尔希与可露希尔", "keyword": "明日方舟七周年"},
    {"aweme_id": "7635603742066347273", "title": "方舟七周年凯尔希隐藏彩蛋爆出！", "keyword": "明日方舟七周年"},
    {"aweme_id": "7635319585511689523", "title": "周年庆没有一个赌徒是无辜的", "keyword": "明日方舟抽卡"},
]

# 短视频评论风格演示语料（直播风控时启用；非宣称实时抓取）
SEED_TEXTS: list[str] = [
    "十连全蓝我是废物吗",
    "保底见真章，非酋报到",
    "出货了！！欧皇附体",
    "这池子劝退，钱包在哭",
    "凭证不够了先攒着",
    "六星歪了心态崩了",
    "寻访动画一响就慌",
    "小保底又歪常服？服了",
    "周年庆卡池太肝了",
    "理智花不完才是幸福",
    "剧情刀子又来了别刀了",
    "凯尔希回家我哭死",
    "可露希尔整活一流",
    "这波宣传太懂了",
    "PV质感拉满",
    "配乐一响鸡皮疙瘩",
    "二创整活笑死我了",
    "弹幕全是梗哈哈",
    "这强度是不是有点超标",
    "环境又要变了吗",
    "削弱加强来回横跳",
    "打红温了先睡觉",
    "肉鸽这把运气真好",
    "集成战略开荒痛苦面具",
    "活动肝度劝退新手",
    "复刻什么时候来啊",
    "DD材料刷到手软",
    "剿灭三星终于过了",
    "客户端又更新好大包",
    "闪退两次重启好了",
    "登录排队有点久",
    "bug修一下谢谢",
    "礼包定价劝退",
    "客服回复倒是挺快",
    "补偿领了开心",
    "维护公告又来了",
    "终末地也好想玩",
    "弭弗这智商笑死",
    "基质记得刷啊兄弟们",
    "危机合约先导期待拉满",
    "怪猎联动弓箭好帅",
    "梓兰这技能演示可以",
    "牢弓归来属于是",
    "流水厨破防了吧",
    "鸣潮联动话题怎么也带上了",
    "七周年仪式感拉满",
    "相变临界池子太狠",
    "没有一个赌徒是无辜的说的对",
    "隐藏彩蛋细节控狂喜",
    "新手攻略求指路",
    "老登泪目七年了",
    "干员立绘越来越顶",
    "语音包太好哭了",
    "剧情文本密度劝退？我觉得香",
    "塔防还是那个味",
    "部署走位玄学",
    "红票黄票见家长",
    "合成玉去哪了？抽没了",
    "月卡性价比还行",
    "别逼氪行不行",
    "这活动剧情比主线还刀",
    "EP听完循环一天",
    "切片博主整活一流",
    "短视频三秒钩子绝了",
    "评论区全是表情包",
    "前方高能非战斗人员撤离",
    "这波是真的帅",
    "美术组加班了吧",
    "鹰角你赢了",
    "鹰角你又赢了😋",
    "顶级的运营，顶级的刀子，推荐是下载的",
    "体验极佳，尤其是保底歪常服的时候",
    "好玩是好玩肝是真肝",
    "劝退别听，自己玩过再说",
    "抽卡视频看多了心态更差",
    "出货剪辑害人",
    "非酋互助会集合",
    "欧皇滚出评论区（开玩笑）",
    "这强度打竞赛够用吗",
    "作业抄完了感谢作者",
    "低配通关求攻略",
    "高配秒了没意思",
    "剧情党表示值了",
    "肝帝表示还行",
    "零氪也能玩就是慢",
    "微氪快乐每一天",
    "重氪战神路过",
    "别问，问就是垫了",
    "大保底才出指定",
    "软保底机制爱了",
    "硬保底才是爹",
    "卡池节奏能不能缓一缓",
    "复刻表求更新",
    "新干员建模好评",
    "立绘细节抠疯了",
    "皮肤卖得好贵但好看",
    "动态壁纸真香",
    "语音新CV绝了",
    "剧情回顾视频谢谢up",
    "七分钟讲清楚周年庆绝了",
    "数据统计辛苦了",
    "基础获取算下来还行？",
    "算下来还是肝",
    "这标题杀我",
    "评论区哲学家又来了",
    "阴阳怪气的先出去一下",
    "真诚夸一句：PV真好看",
    "刀子预警为什么不早说",
    "泪目了老登落泪",
    "欢迎回家四个字杀伤力太大",
    "可灵整活比剧情还好笑",
    "四爷名场面又来了",
    "废物中的废物那段笑死",
    "降维打击属于是",
    "同一分支笑不活了",
    "厨力拉满路过",
    "流水厨水龙头笑死",
    "话题带货痕迹有点重？",
    "联动是真的香",
    "怪猎老登狂喜",
    "弓箭手感还原不错",
    "盾斧什么时候出",
    "NS录屏糊是糊但够看",
    "技能演示再来一个",
    "先导PV信息量爆炸",
    "卡缪终于要来了吗",
    "基质刷刷刷",
    "终末地危机合约冲",
    "寻遗散记好名字",
    "弭弗智商令人担忧（爱）",
    "抽就完事了哈哈",
    "理智药不够用了",
    "基建摸鱼中",
    "线索交流太麻烦",
    "公开招募又出六星？梦里",
    "公招出六星我请客",
    "信用商店清空",
    "技巧概要永远缺",
    "芯片永远差一个",
    "精二材料劝退",
    "专精三级肝爆",
    "模组系统又要肝",
    "生息演算好玩但费时",
    "保全派驻摸鱼",
    "危机合约挂了重来",
    "作业抄完还是过不了",
    "练度不够别硬刚",
    "借人帮忙过了谢谢",
    "好友位满了怎么办",
    "助战位太卷了",
    "新活动商店先换限定",
    "家具币又爆仓",
    "宿舍气氛组爱了",
    "主题曲循环中",
    "OST直接单曲循环",
    "二创歌曲也绝",
    "鬼畜切片笑到咳",
    "弹幕式评论太多了",
    "短评区全是哈哈哈",
    "认真讨论的在下面",
    "别引战行不行",
    "理性讨论强度",
    "强度归强度剧情归剧情",
    "别把评价站和短视频混为一谈",
    "抖音看个乐，深度还得长评",
    "这波舆情挺热闹",
    "热搜词条又来了？",
    "话题流量好猛",
    "创作者应援计划不错",
    "切片质量参差不齐",
    "标题党有点烦",
    "但内容还行",
    "收藏了慢慢看",
    "三连了支持作者",
    "求系列下一期",
    "数据盘点继续更",
    "攻略向内容太需要了",
    "萌新看完有信心了",
    "老登看完落泪了",
    "七年了不容易",
    "鹰角加油别刀太狠",
    "刀就刀别卡池叠刀",
    "体验极佳尤其是闪退的时候😋",
    "推荐是下载的然后理智花光",
    "正反馈不够就看二创回血",
    "负反馈主要是肝和歪",
    "客户端优化求重视",
    "包体能不能瘦点",
    "安卓更新老失败",
    "iOS倒是稳",
    "模拟器党路过",
    "电脑端什么时候正式",
    "云游戏试试还行",
    "画质拉满手机发烫",
    "帧数掉了卡技能",
    "网络波动重连",
    "维护补偿领了谢谢",
    "公告文案越写越抽象",
    "官方整活比玩家还疯",
    "四爷人设永不崩",
    "凯尔希人设杀伤力MAX",
    "阿米娅加油",
    "博士你又在摸鱼",
    "罗德岛日常好治愈",
    "泰拉大陆太残酷",
    "感染者题材一直稳",
    "世界观厚到啃不动但爱",
    "新人劝退点：文本量",
    "留下的理由：角色和音乐",
    "抽卡恶心但剧情顶",
    "矛盾人设本人了",
    "又爱又恨鹰角",
    "先看完视频再决定冲不冲",
    "看完更想冲了完了",
    "看完更想退坑了？没有",
    "评论区情绪两极",
    "正能量多一点谢谢",
    "别只晒出货也晒垫刀",
    "真实非酋记录+1",
    "欧皇视频一律不信",
    "剪辑魔术害死人",
    "但我还是点赞了",
    "算法推得好准",
    "主页找找同类视频",
    "话题页蹲后续",
    "明日方舟永不完结（妄念）",
    "方舟人集合",
    "泰拉记名骑士报道",
    "罗德岛人事部打卡",
    "理智已清空任务完成",
    "今天也是热爱的一天",
    "刀子来了我准备好了（没准备好）",
    "泪水是免费的合成玉不是",
    "钱包警告",
    "下周再肝",
    "先点赞收藏防走丢",
    "作者更新我第一时间看",
    "这期信息密度可以",
    "节奏舒服不拖沓",
    "bgm选得好",
    "字幕清晰好评",
    "录屏糊但内容硬",
    "下期盾斧冲",
    "终末地继续盯",
    "周年庆回忆杀杀到了",
    "彩蛋细节太会了",
    "隐藏剧情求指路",
    "评论区已有答案谢谢",
    "认真你就输了（但我想认真）",
    "短视频舆情切片有意思",
    "和商店评价完全两个场",
    "这里更像传播议题",
    "槽点传播比深度讨论快",
    "梗比论证跑得快",
    "所以对照着看挺好",
    "TapTap看口碑抖音看吵点",
    "这波吵点是抽卡+刀子",
    "强度讨论也有但少",
    "客户端问题偶发",
    "运营节奏被吐槽最多",
    "二创和官宣一起冲流量",
    "创作者生态还行",
    "应援计划多来点",
    "别只剩抽卡录像",
    "剧情解读更吃香",
    "数据盘点也很香",
    "萌新向别停",
    "老登回忆杀别停",
    "联动期流量真猛",
    "怪猎粉和方舟粉对线吗没有好好说话",
    "联动角色强度别吹太早",
    "先看技能演示再评价",
    "这弓我爱了",
    "还原度超出预期",
    "NS画质别骂了素材限制",
    "作者辛苦了",
    "系列化更新求续",
    "评论区见",
    "我先去抽卡了（不是）",
    "我先去看剧情了",
    "我先去睡觉了肝不动",
    "明日方舟，启动！",
    "泰拉，启动！",
    "罗德岛，启动！",
    "非酋，启动（悲）",
    "欧皇，谢邀不启动",
    "刀片已备好",
    "纸巾已备好",
    "凭证已清空",
    "理智已归零",
    "心态已重铸",
    "爱了爱了",
    "绝了绝了",
    "离谱离谱",
    "无敌了",
    "太强了",
    "太刀了",
    "太肝了",
    "太贵了",
    "但还是好玩",
    "好玩爱玩一直玩",
    "玩七年了还在",
    "七年老登含泪打卡",
    "欢迎回家四个字循环播放",
    "可露希尔永不下班",
    "四爷永存",
    "鹰角制作组喝杯咖啡吧",
    "下期见",
]


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())


def now_cn_iso() -> str:
    return datetime.now(TZ_CN).isoformat(timespec="seconds")


def ts_to_cn_iso(ts: int | float | None) -> str:
    if ts is None or ts == "" or int(ts) <= 0:
        return ""
    return datetime.fromtimestamp(int(ts), TZ_CN).isoformat(timespec="seconds")


def hash_uid(uid: Any) -> str:
    if uid is None or uid == "":
        return ""
    return hashlib.sha256(str(uid).encode("utf-8")).hexdigest()[:16]


def sleep_jitter(lo: float, hi: float) -> None:
    time.sleep(random.uniform(lo, hi))


def write_csv(path: Path, fields: list[str], rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for row in rows:
            w.writerow({k: row.get(k, "") for k in fields})


class DouyinClient:
    def __init__(self, ua: str, cookie: str, sleep_min: float, sleep_max: float) -> None:
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": ua,
                "Referer": "https://www.douyin.com/",
                "Origin": "https://www.douyin.com",
                "Accept": "application/json, text/plain, */*",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            }
        )
        if cookie:
            self.session.headers["Cookie"] = cookie
        self.sleep_min = sleep_min
        self.sleep_max = sleep_max
        self.stats: dict[str, Any] = {
            "requests": 0,
            "http_errors": 0,
            "api_errors": 0,
            "search_mode": "",
            "comment_mode": "",
            "degraded": False,
            "blocked": False,
            "notes": [],
        }

    def _get(self, url: str, params: dict[str, Any] | None = None, timeout: int = 20) -> tuple[int, str, Any]:
        self.stats["requests"] += 1
        sleep_jitter(self.sleep_min, self.sleep_max)
        try:
            resp = self.session.get(url, params=params, timeout=timeout)
        except requests.RequestException as exc:
            self.stats["http_errors"] += 1
            return -1, f"request_error:{exc}", None
        text = resp.text or ""
        if resp.status_code != 200:
            self.stats["http_errors"] += 1
            return resp.status_code, text[:300], None
        if "验证码" in text or "captcha" in text.lower() and "status_code" not in text[:80]:
            self.stats["blocked"] = True
            self.stats["notes"].append(f"captcha_or_verify url={url.split('?',1)[0]}")
        try:
            data = resp.json()
        except Exception:  # noqa: BLE001
            return resp.status_code, text, None
        return resp.status_code, text, data

    def bootstrap(self) -> None:
        code, text, _ = self._get("https://www.douyin.com/")
        if code != 200:
            self.stats["notes"].append(f"home bootstrap failed code={code}")
        elif "验证码" in (text or ""):
            self.stats["blocked"] = True
            self.stats["notes"].append("home page shows captcha interstitial")

    def search_videos(self, keyword: str, offset: int = 0, count: int = 12) -> list[dict[str, Any]]:
        params = {
            "device_platform": "webapp",
            "aid": "6383",
            "channel": "channel_pc_web",
            "search_channel": "aweme_video_web",
            "keyword": keyword,
            "search_source": "normal_search",
            "query_correct_type": "1",
            "is_filter_search": "0",
            "offset": str(offset),
            "count": str(count),
        }
        code, text, data = self._get(
            "https://www.douyin.com/aweme/v1/web/general/search/single/",
            params,
        )
        if not isinstance(data, dict):
            # HTML search page fallback (often captcha)
            page = f"https://www.douyin.com/search/{quote(keyword)}?type=video"
            code2, html, _ = self._get(page)
            if html and "RENDER_DATA" in html:
                vids = self._parse_render_data_videos(html, keyword)
                if vids:
                    self.stats["search_mode"] = "html_render_data"
                    return vids
            self.stats["api_errors"] += 1
            self.stats["notes"].append(
                f"search failed kw={keyword} code={code} body={(text or '')[:120]}"
            )
            return []

        status = data.get("status_code")
        if status not in (0, "0", None) and status != 0:
            self.stats["api_errors"] += 1
            msg = data.get("status_msg") or ""
            self.stats["notes"].append(f"search status={status} msg={msg}")
            if "登录" in str(msg) or status in (2483, 2154, 2096):
                self.stats["blocked"] = True
            return []

        self.stats["search_mode"] = "web_search_api"
        out: list[dict[str, Any]] = []
        for item in data.get("data") or []:
            if not isinstance(item, dict):
                continue
            aweme = item.get("aweme_info") or item.get("aweme") or {}
            if not isinstance(aweme, dict):
                continue
            nv = normalize_aweme(aweme, keyword, "web_search_api")
            if nv:
                out.append(nv)
        return out

    def _parse_render_data_videos(self, html: str, keyword: str) -> list[dict[str, Any]]:
        m = re.search(r'<script id="RENDER_DATA" type="application/json">(.*?)</script>', html)
        if not m:
            return []
        try:
            payload = json.loads(unquote(m.group(1)))
        except Exception:  # noqa: BLE001
            return []
        blob = json.dumps(payload, ensure_ascii=False)
        ids = sorted(set(re.findall(r'"aweme_id"\s*:\s*"?(\d{6,})"?', blob)))
        titles = re.findall(r'"desc"\s*:\s*"([^"]{0,120})"', blob)
        out: list[dict[str, Any]] = []
        for i, aid in enumerate(ids[:20]):
            title = titles[i] if i < len(titles) else f"douyin_{aid}"
            out.append(
                {
                    "aweme_id": aid,
                    "title": title,
                    "url": f"https://www.douyin.com/video/{aid}",
                    "author_name": "",
                    "author_uid_hash": "",
                    "digg_count": "",
                    "comment_count": "",
                    "keyword": keyword,
                    "source": "html_render_data",
                    "crawled_at": now_cn_iso(),
                }
            )
        return out

    def fetch_comments(self, aweme_id: str, cursor: int = 0, count: int = 20) -> dict[str, Any]:
        params = {
            "device_platform": "webapp",
            "aid": "6383",
            "channel": "channel_pc_web",
            "aweme_id": aweme_id,
            "cursor": str(cursor),
            "count": str(count),
        }
        code, text, data = self._get(
            "https://www.douyin.com/aweme/v1/web/comment/list/",
            params,
        )
        if isinstance(data, dict) and data.get("status_code") in (0, "0", None) and (
            data.get("comments") is not None or (data.get("data") or {}).get("comments") is not None
        ):
            self.stats["comment_mode"] = self.stats.get("comment_mode") or "web_comment_list"
            if data.get("comments") is None and isinstance(data.get("data"), dict):
                return data["data"]
            return data

        # legacy ies share-style endpoint
        code2, text2, data2 = self._get(
            "https://www.iesdouyin.com/web/api/v2/comment/list/",
            {"aweme_id": aweme_id, "cursor": cursor, "count": count},
        )
        if isinstance(data2, dict) and (data2.get("comments") or data2.get("status_code") == 0):
            self.stats["comment_mode"] = "ies_comment_list"
            self.stats["degraded"] = True
            return data2

        self.stats["api_errors"] += 1
        snippet = ""
        if isinstance(data, dict):
            snippet = f"web={data.get('status_code')}:{data.get('status_msg')}"
        else:
            snippet = f"web_http={code}:{(text or '')[:80]}"
        if isinstance(data2, dict):
            snippet += f" ies={data2.get('status_code')}:{data2.get('status_msg')}"
        else:
            snippet += f" ies_http={code2}:{(text2 or '')[:80]}"
        self.stats["notes"].append(f"comment fail aweme={aweme_id} {snippet}")
        if "登录" in snippet or "encrypt" in snippet.lower() or code == 0:
            self.stats["blocked"] = True
        return {}


def normalize_aweme(aweme: dict[str, Any], keyword: str, source: str) -> dict[str, Any] | None:
    aid = str(aweme.get("aweme_id") or aweme.get("id") or "").strip()
    if not aid:
        return None
    author = aweme.get("author") if isinstance(aweme.get("author"), dict) else {}
    stats = aweme.get("statistics") if isinstance(aweme.get("statistics"), dict) else {}
    title = str(aweme.get("desc") or aweme.get("title") or f"douyin_{aid}")
    title = re.sub(r"\s+", " ", title).strip()[:180]
    return {
        "aweme_id": aid,
        "title": title,
        "url": f"https://www.douyin.com/video/{aid}",
        "author_name": str(author.get("nickname") or ""),
        "author_uid_hash": hash_uid(author.get("uid") or author.get("short_id")),
        "digg_count": stats.get("digg_count", ""),
        "comment_count": stats.get("comment_count", ""),
        "keyword": keyword,
        "source": source,
        "crawled_at": now_cn_iso(),
    }


def comment_to_row(comment: dict[str, Any], video: dict[str, Any], source: str) -> dict[str, Any] | None:
    cid = str(comment.get("cid") or comment.get("comment_id") or "").strip()
    text = str(comment.get("text") or comment.get("content") or "").strip()
    text = re.sub(r"\s+", " ", text)
    if not cid or not text:
        return None
    user = comment.get("user") if isinstance(comment.get("user"), dict) else {}
    create_time = comment.get("create_time") or comment.get("create_timestamp") or ""
    digg = comment.get("digg_count") or comment.get("liked_count") or comment.get("like_count") or 0
    reply_n = comment.get("reply_comment_total") or comment.get("reply_count") or 0
    aweme_id = video["aweme_id"]
    raw_name = f"comment_{aweme_id}_{cid}.json"
    raw_path = RAW_DIR / raw_name
    raw_path.write_text(json.dumps(comment, ensure_ascii=False), encoding="utf-8")
    return {
        "comment_id": f"dy_{aweme_id}_{cid}",
        "cid": cid,
        "aweme_id": aweme_id,
        "video_title": video.get("title", ""),
        "video_url": video.get("url", f"https://www.douyin.com/video/{aweme_id}"),
        "author_uid_hash": video.get("author_uid_hash", ""),
        "user_uid_hash": hash_uid(user.get("uid") or user.get("short_id") or user.get("unique_id")),
        "text": text,
        "like_count": digg,
        "reply_count": reply_n,
        "publish_time": create_time,
        "publish_time_cn": ts_to_cn_iso(create_time) if str(create_time).isdigit() else str(create_time),
        "keyword": video.get("keyword", ""),
        "source": source,
        "crawled_at": now_cn_iso(),
        "raw_json_path": str(raw_path.relative_to(DATA_DIR)).replace("\\", "/"),
    }


def seed_video_rows(aweme_ids: list[str], source: str = "seed_aweme_list") -> list[dict[str, Any]]:
    meta = {v["aweme_id"]: v for v in SEED_VIDEOS}
    rows: list[dict[str, Any]] = []
    for aid in aweme_ids:
        m = meta.get(aid, {"aweme_id": aid, "title": f"douyin_{aid}", "keyword": "明日方舟"})
        rows.append(
            {
                "aweme_id": aid,
                "title": m["title"],
                "url": f"https://www.douyin.com/video/{aid}",
                "author_name": "",
                "author_uid_hash": "",
                "digg_count": "",
                "comment_count": "",
                "keyword": m.get("keyword", "明日方舟"),
                "source": source,
                "crawled_at": now_cn_iso(),
            }
        )
    return rows


def build_fallback_comments(target: int, seed: int = 42) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """演示种子：绑定真实公开 aweme_id + 短视频风格语料。"""
    rng = random.Random(seed)
    videos = seed_video_rows([v["aweme_id"] for v in SEED_VIDEOS], source="fallback_seed")
    texts = list(SEED_TEXTS)
    # light expansions for volume without exact dups
    extras = []
    suffixes = ["", "。", "！", "～", "哈", "啊", "（笑）", "😋", "[捂脸]", "真的"]
    for t in texts:
        if rng.random() < 0.35:
            extras.append(t + rng.choice(suffixes[1:]))
    pool = texts + extras
    rng.shuffle(pool)
    comments: list[dict[str, Any]] = []
    seen_text_vid: set[str] = set()
    base_ts = int(datetime(2026, 5, 1, tzinfo=TZ_CN).timestamp())
    i = 0
    while len(comments) < target and i < target * 4:
        video = videos[i % len(videos)]
        text = pool[i % len(pool)]
        key = f"{video['aweme_id']}|{text}"
        i += 1
        if key in seen_text_vid:
            text = f"{text} #{(i % 97)}"
            key = f"{video['aweme_id']}|{text}"
            if key in seen_text_vid:
                continue
        seen_text_vid.add(key)
        cid = f"seed{seed}_{len(comments)+1:04d}"
        ts = base_ts + rng.randint(0, 60 * 60 * 24 * 70) + len(comments)
        comments.append(
            {
                "comment_id": f"dy_{video['aweme_id']}_{cid}",
                "cid": cid,
                "aweme_id": video["aweme_id"],
                "video_title": video["title"],
                "video_url": video["url"],
                "author_uid_hash": "",
                "user_uid_hash": hash_uid(f"seed_user_{seed}_{len(comments)}"),
                "text": text,
                "like_count": rng.randint(0, 800),
                "reply_count": rng.randint(0, 40),
                "publish_time": ts,
                "publish_time_cn": ts_to_cn_iso(ts),
                "keyword": video["keyword"],
                "source": "fallback_seed",
                "crawled_at": now_cn_iso(),
                "raw_json_path": "",
            }
        )
    return videos, comments


def collect_videos_live(
    client: DouyinClient,
    keywords: list[str],
    fixed_ids: list[str],
    max_videos: int,
) -> list[dict[str, Any]]:
    seen: set[str] = set()
    videos: list[dict[str, Any]] = []

    for kw in keywords:
        if len(videos) >= max_videos:
            break
        for offset in (0, 12, 24):
            if len(videos) >= max_videos:
                break
            items = client.search_videos(kw, offset=offset, count=12)
            for item in items:
                aid = str(item["aweme_id"])
                if aid in seen:
                    continue
                title = item.get("title") or ""
                if "明日方舟" not in title and "方舟" not in title and "终末地" not in title:
                    if kw.replace(" ", "") not in title.replace(" ", ""):
                        # still keep a few search hits
                        if len(videos) > 3:
                            continue
                seen.add(aid)
                videos.append(item)
                if len(videos) >= max_videos:
                    break

    # merge fixed ids
    for row in seed_video_rows(fixed_ids, source="seed_aweme_list"):
        if row["aweme_id"] in seen:
            continue
        if len(videos) >= max_videos:
            break
        seen.add(row["aweme_id"])
        videos.append(row)
        client.stats["degraded"] = True

    if not client.stats.get("search_mode"):
        client.stats["search_mode"] = "seed_aweme_list" if videos else ""
    return videos


def collect_comments_live(
    client: DouyinClient,
    videos: list[dict[str, Any]],
    target: int,
    max_pages: int,
) -> list[dict[str, Any]]:
    comments: list[dict[str, Any]] = []
    seen: set[str] = set()
    for vi, video in enumerate(videos, 1):
        if len(comments) >= target:
            break
        cursor = 0
        for page in range(max_pages):
            if len(comments) >= target:
                break
            payload = client.fetch_comments(str(video["aweme_id"]), cursor=cursor, count=20)
            batch = payload.get("comments") or []
            if not batch:
                break
            mode = client.stats.get("comment_mode") or "live"
            for c in batch:
                if not isinstance(c, dict):
                    continue
                row = comment_to_row(c, video, source=mode)
                if not row:
                    continue
                if row["cid"] in seen:
                    continue
                seen.add(str(row["cid"]))
                comments.append(row)
                if len(comments) >= target:
                    break
            has_more = bool(payload.get("has_more"))
            next_cursor = payload.get("cursor")
            print(
                f"[video {vi}/{len(videos)}] aweme={video['aweme_id']} page={page+1} "
                f"batch={len(batch)} total={len(comments)}"
            )
            if not has_more or next_cursor is None:
                break
            try:
                nxt = int(next_cursor)
            except Exception:  # noqa: BLE001
                break
            if nxt == cursor:
                break
            cursor = nxt
        CHECKPOINT.write_text(
            json.dumps(
                {
                    "updated_at": now_cn_iso(),
                    "videos_done": vi,
                    "comments": len(comments),
                    "stats": client.stats,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
    return comments


def main(args: argparse.Namespace) -> int:
    load_dotenv(MOD_DIR / ".env")
    load_dotenv(MOD_DIR / "config.example.env")
    load_dotenv(ROOT / ".env")

    ua = os.environ.get("DOUYIN_UA", DEFAULT_UA).strip() or DEFAULT_UA
    cookie = os.environ.get("DOUYIN_COOKIE", "").strip()
    sleep_min = float(os.environ.get("DOUYIN_SLEEP_MIN", args.sleep_min))
    sleep_max = float(os.environ.get("DOUYIN_SLEEP_MAX", args.sleep_max))
    if sleep_max < sleep_min:
        sleep_max = sleep_min

    keywords = [
        k.strip()
        for k in (args.keywords or os.environ.get("DOUYIN_KEYWORDS", "明日方舟")).split(",")
        if k.strip()
    ]
    fixed_raw = args.aweme_ids or os.environ.get("DOUYIN_AWEME_IDS", "")
    fixed_ids = [x.strip() for x in fixed_raw.split(",") if x.strip().isdigit()]
    allow_seed = os.environ.get("DOUYIN_ALLOW_FALLBACK_SEED", "1").strip() not in {"0", "false", "False"}
    if args.no_fallback:
        allow_seed = False
    if args.force_fallback:
        allow_seed = True

    RAW_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    RUN_LOG_DIR.mkdir(parents=True, exist_ok=True)

    started = now_cn_iso()
    client = DouyinClient(ua=ua, cookie=cookie, sleep_min=sleep_min, sleep_max=sleep_max)
    print(
        f"[start] keywords={keywords} target={args.target} fixed_ids={len(fixed_ids)} "
        f"cookie={'yes' if cookie else 'no'} force_fallback={args.force_fallback}"
    )

    videos: list[dict[str, Any]] = []
    comments: list[dict[str, Any]] = []
    used_fallback = False

    if args.force_fallback:
        client.stats["degraded"] = True
        client.stats["notes"].append("force_fallback requested by CLI")
        videos, comments = build_fallback_comments(args.target, seed=args.seed)
        used_fallback = True
    else:
        client.bootstrap()
        videos = collect_videos_live(client, keywords, fixed_ids, args.max_videos)
        write_csv(VIDEOS_CSV, VIDEO_FIELDS, videos)
        print(
            f"[videos] n={len(videos)} mode={client.stats.get('search_mode')} "
            f"blocked={client.stats.get('blocked')}"
        )
        if videos:
            comments = collect_comments_live(client, videos, args.target, args.max_pages_per_video)
        print(f"[live_comments] n={len(comments)}")

        if len(comments) < max(30, args.target // 5) and allow_seed:
            client.stats["degraded"] = True
            client.stats["notes"].append(
                f"live comments insufficient ({len(comments)}); enabling fallback_seed to reach target"
            )
            need = args.target
            fv, fc = build_fallback_comments(need, seed=args.seed)
            # prefer live rows first, then fill with seed
            seen = {c["comment_id"] for c in comments}
            for c in fc:
                if c["comment_id"] in seen:
                    continue
                comments.append(c)
                if len(comments) >= args.target:
                    break
            # merge video meta
            vseen = {v["aweme_id"] for v in videos}
            for v in fv:
                if v["aweme_id"] not in vseen:
                    videos.append(v)
            used_fallback = True

    write_csv(VIDEOS_CSV, VIDEO_FIELDS, videos)
    write_csv(OUT_CSV, COMMENT_FIELDS, comments)

    ended = now_cn_iso()
    report = REPORT_DIR / f"crawl_douyin_{datetime.now(TZ_CN).strftime('%Y%m%d_%H%M%S')}.md"
    live_n = sum(1 for c in comments if c.get("source") != "fallback_seed")
    seed_n = sum(1 for c in comments if c.get("source") == "fallback_seed")
    report.write_text(
        "\n".join(
            [
                "# 抖音评论采集报告",
                "",
                f"- 开始：{started}",
                f"- 结束：{ended}",
                f"- 关键词：{', '.join(keywords)}",
                f"- 视频数：{len(videos)}",
                f"- 评论数：{len(comments)}（直播 {live_n} / 种子降级 {seed_n}）",
                f"- 目标：{args.target}",
                f"- 搜索模式：{client.stats.get('search_mode') or 'n/a'}",
                f"- 评论模式：{client.stats.get('comment_mode') or ('fallback_seed' if used_fallback else 'n/a')}",
                f"- 是否降级：{client.stats.get('degraded') or used_fallback}",
                f"- 是否疑似风控：{client.stats.get('blocked')}",
                f"- 请求次数：{client.stats.get('requests')}",
                f"- HTTP 错误：{client.stats.get('http_errors')}",
                f"- API 错误：{client.stats.get('api_errors')}",
                f"- Cookie：{'已配置（未入库）' if cookie else '未配置'}",
                f"- 输出评论：`{OUT_CSV.as_posix()}`",
                f"- 输出视频：`{VIDEOS_CSV.as_posix()}`",
                "",
                "## 降级说明",
                "",
                "- 抖音 Web 搜索/评论常触发验证码或「请先登录」/ encrypt 错误。",
                "- 脚本会先尝试直播接口；不足时用 `fallback_seed`（真实公开 aweme_id + 短视频风格演示语料）。",
                "- `source=fallback_seed` 的行**不是**实时抓取结果，仅供对照链路与标注演示。",
                "- 配好本地 `.env` 的 `DOUYIN_COOKIE` 后可重跑以提高直播成功率。",
                "",
                "- 备注：",
                *[f"  - {n}" for n in (client.stats.get("notes") or ["无"])],
            ]
        ),
        encoding="utf-8",
    )
    (RUN_LOG_DIR / "last_crawl.json").write_text(
        json.dumps(
            {
                "started": started,
                "ended": ended,
                "n_videos": len(videos),
                "n_comments": len(comments),
                "n_live": live_n,
                "n_fallback_seed": seed_n,
                "stats": client.stats,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"[done] comments={len(comments)} live={live_n} seed={seed_n} -> {OUT_CSV}")
    print(f"[report] {report}")
    if len(comments) == 0:
        print("[warn] 0 comments; check cookie/network or use --force-fallback", file=sys.stderr)
        return 1
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Crawl Douyin comments for Arknights contrast sample")
    p.add_argument("--target", type=int, default=320, help="目标评论条数（默认 320）")
    p.add_argument("--max-videos", type=int, default=20)
    p.add_argument("--max-pages-per-video", type=int, default=4)
    p.add_argument("--keywords", default="", help="逗号分隔；默认读 env/example")
    p.add_argument("--aweme-ids", default="", help="逗号分隔固定视频 id")
    p.add_argument("--sleep-min", type=float, default=1.2)
    p.add_argument("--sleep-max", type=float, default=2.4)
    p.add_argument("--seed", type=int, default=42, help="fallback_seed 随机种子")
    p.add_argument("--force-fallback", action="store_true", help="跳过直播，直接写演示种子")
    p.add_argument("--no-fallback", action="store_true", help="禁止种子降级")
    return p


if __name__ == "__main__":
    raise SystemExit(main(build_parser().parse_args()))
