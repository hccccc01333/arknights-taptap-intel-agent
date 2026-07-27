# v1.4 两段式规则

## 字段

| 字段 | 取值 | 说明 |
|------|------|------|
| `topic_primary` | `gacha\|balance\|gameplay\|story\|event\|client\|ops\|other` | 主诉求单选 |
| `literal_sentiment` | `正\|中\|负` | 字面褒贬 |
| `intended_sentiment` | `正\|中\|负` | 整体真实态度 |
| `sentiment` | `正\|中\|负` | **对外主情绪 = intended** |
| `actionable` | `是\|否` | 有具体可改点可为是，即使整体=正 |
| `rhetoric` | `none\|sarcasm\|gaoji_hei\|fanchuan\|template_praise` | 修辞类型 |
| `rhetoric_confidence` | `0~1` | 修辞置信 |
| `incongruity_cues` | 代码串或 `none` | 裂隙线索 |
| `confidence` | `0~1` | 主题+意图整体置信 |
| `reason` | ≤30 字 | 给人看，不进主指标 |

## incongruity_cues 代码

| 代码 | 含义 |
|------|------|
| `praise_shell_neg_fact` | 褒义外壳 + 负面事实 |
| `template_parallel` | 机械排比好评、信息稀薄 |
| `paren_leak` | 括号/限定泄真 |
| `community_irony` | 圈梗 + 反讽收束 |
| `overstatement` | 夸张到违和 |
| `star_text_conflict` | 星级与文本强冲突（弱线索） |
| `fake_recommend` | 推荐假动作 |

## rhetoric 口径

- `none`：无裂隙；真诚轻槽；亲昵吐槽；建设性提问  
- `sarcasm`：阴阳/反话  
- `gaoji_hei`：整段像夸，细节全是骂  
- `fanchuan`：强证据假人设/引战（不足用 sarcasm）  
- `template_praise`：机械排比敷衍好评  

## 整体态度要点

1. `gaoji_hei` / `fanchuan` 或明确踩核的 `sarcasm` → sentiment 跟真实意图（常负）  
2. `template_praise` → 多为中  
3. 辱骂/劝退/退坑/卸载 → 负（即使高星）  
4. 高星 + 单点机制槽 / 亲昵玩笑 → 多为正或中，`rhetoric=none`  
5. 禁止仅因 `star_text_conflict` 或单个槽点把真诚高星打成负  
6. 禁止忽略裂隙把高级黑当真心好评
