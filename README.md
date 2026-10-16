# astrbot_plugin_innerlife

群聊机器人拟人化框架（AstrBot 插件，AGPL-3.0）。它让 QQ 群里的机器人像一个有脾气的群友：有人设和说话方式，有情绪、心情和对每个人的好感，记得群友说过的事，每天写日记、排日程、看天气和新闻，没人 @ 她时自己判断要不要插话，可以把一句话拆成几条连发，也能自己决定画一张图。

框架只管"她是谁、怎么说话、现在什么状态"，不含任何具体业务。知识库答疑、状态卡片、群管这类功能写成独立的功能插件，按 [`docs/design.md`](docs/design.md) 里的钩子约定和它配合。

## 功能

- **插话判断**：没被 @ 时，每批群消息看一眼，按当前心情和兴致决定不说话、闲聊一句，还是等一会儿没人回答再代答（需要配置 `support_scope`）。
- **情绪四层**：具体情绪（会衰减）→ 背景心情 → 兴致 → 对每个人的好感；性格参数可调，默认是易炸毛、不记仇、一示好就软。
- **记忆**：从对话里记下群友的小事，再次提起会加深，久了会忘；私密的事不进提示词。每天深夜整理一次。
- **日记**：每天 8 节固定模板，外加周记、月记；最近几天的日记会影响她今天的状态。
- **生活**：每天早上给自己排日程，看所在城市的天气、节假日和新闻；群里安静时可能自己开个话头。
- **连发**：闲聊时按打字速度分几条发出，答疑和多步说明始终一条。
- **识图、画图**：看群里的图片和表情包；接上 MCP 生图服务后可以自己决定画图，所有工具调用都经过屏蔽词和每日次数检查。
- **安全底线**：高危请求拒绝、密钥脱敏、链接白名单、提示词注入防护、冷却、并发和排队、`/机器人 开启/关闭` 开关。

## 环境要求

- AstrBot `>=4.26.7,<5`
- 平台：`aiocqhttp`（OneBot v11，如 NapCat），只支持 QQ 群
- 一个聊天模型。可选：一个后台模型（插话判断、整理、日记等，`background_provider_id`）、一个提供生图工具的 MCP 服务
- 后台任务需要访问外网：天气 [open-meteo](https://open-meteo.com/)、节假日 [holiday-cn](https://github.com/NateScarlet/holiday-cn)、新闻 RSS（默认是几个中文科技和游戏站，可改 `news_feeds`）

没有额外的 Python 依赖（`httpx`、`mcp` 随 AstrBot 安装）。

## 安装

```sh
cd AstrBot/data/plugins
git clone https://github.com/DawnJson/astrbot_plugin_innerlife.git
```

重启 AstrBot，在 WebUI 的插件配置里改设置。启动时插件会创建（或更新）一个 AstrBot 人格 `astrbot_persona_id`（默认 `innerlife`），内容为安全底线加人设、不带工具。

## 最少要改的配置

| 键 | 说明 |
|---|---|
| `persona_prompt` | 她的人设和说话方式 |
| `bot_names` | 群友对她的称呼，用来判断是不是在叫她 |
| `group_ids` | 生效的群号；空表示所有群 |
| `allowed_link_domains` | 回复里允许出现的 HTTPS 域名；空表示不发任何链接 |
| `support_scope` | 她会主动代答的问题范围（一句话）；空表示只闲聊，不代答 |
| `city_name` / `latitude` / `longitude` / `timezone` | 她"住"的城市，用于天气和作息 |
| `background_provider_id` | 后台调用用的模型提供商 ID；留空用聊天模型 |

全部 96 个配置项见 `_conf_schema.json`：每个功能一个开关（`emotion` `memory` `consolidation` `diary` `schedule` `weather` `news` `proactive` `draw` `vision` `burst` 等），数值都能调。AstrBot 的插件配置没有自由字典类型，`tool_daily_limits` 和 `draw_tool_arguments` 填 JSON 文本，例如 `{"quality": "low"}`。

## 让她自己画图

1. 在 AstrBot 里添加一个提供生图工具的 MCP 服务。注意 AstrBot 只允许 `node`、`npx`、`python`、`uvx` 等启动器，不允许 `sh`/`bash`。
2. 让生图服务把图片存到 `data/plugin_data/astrbot_plugin_innerlife/images/`，工具返回的文本里带 `saved: <图片路径> (`（与 [pro-image-mcp](https://github.com/DawnJson/Pro-Image-Mcp) 的输出格式一致）。不在这个目录下的文件不会被发出。
3. 配置 `draw: true`、`draw_tool_name`（工具名）、`draw_tool_arguments`（固定参数）和 `draw_daily_limit`。

她在回复里决定要画时，回复发出后由框架调用工具，图片发出后删除。屏蔽词（`tool_blocked_terms` 加上内置词表）命中时不会调用。

## 数据和隐私

插件会在 `data/plugin_data/astrbot_plugin_innerlife/` 下保存群友的数据，部署前请告知群成员：

| 内容 | 位置 | 保留多久 |
|---|---|---|
| 对每个群友的昵称、好感、一句话印象 | `innerlife.db` 的 `people` 表 | 一直保留；可用 `person_keep_days` 让长期不说话的人自动删除 |
| 群友说过的事（记忆） | `memories` | 按遗忘曲线衰减，弱到阈值以下删除 |
| 当天的群聊原文 | `chat_log` | 每晚整理完清空 |
| 日记、周记、月记 | `diaries` | `diary_keep_daily_days` / `diary_keep_weekly_days` |
| 情绪、心情、日程、天气、新闻 | `emotions` `mood` `life` `news` | 情绪 3 天，新闻 `news_keep_days` |
| 工具用量、开关状态 | `tool_usage.json`、`reply_switch.json` | — |

- 群友发 `forget_command`（默认 `/忘掉我`）即可删除关于自己的印象、好感、记忆、相关情绪和未整理的聊天记录；已写成的日记不改。留空关闭此功能。
- `innerlife.db` 是普通 SQLite 文件，管理员可以用任何 SQLite 工具查看和修改，改动立即生效。

## 写功能插件

功能插件不 import innerlife，只按钩子优先级配合：只追加 `system_prompt`、不往请求里加工具、固定命令处理完 `stop_event()`。完整约定、公开方法（`note_signal`、`note_bot_message`）、内部标记协议和各模块的设计见 [`docs/design.md`](docs/design.md)。

## 测试

```sh
python -m unittest discover -s tests          # 纯模块测试
python tests/runtime_policy.py                # 需要 AstrBot 环境（在 AstrBot 容器或源码目录里运行）
```

## 许可证

[AGPL-3.0](LICENSE)，与 AstrBot 一致。
