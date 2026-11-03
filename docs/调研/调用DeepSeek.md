# 调研报告：主程序怎么调用 DeepSeek

> 调研日期：2026-10-31。DeepSeek 文档是当天下载的页面原文（`https://api-docs.deepseek.com/<路径>.md`），AWF 和各项目是当天检出的源码，位置写在每条后面。标"推断"的是从原文推出来的，原文没有直说；标"待验证"的是没查到或要实测的。

## 一、为什么查

agent 的主循环每一步都要把对话和工具定义发给模型，再把模型的回答读回来：回答里有思考内容、要说的话和要调用的工具。这一块要单独写成代码，主循环只管"发出去、拿回来"。写之前要定：用什么发请求、要不要流式、等多久算卡住、哪些错误重试、模型和思考强度怎么设、地址和密钥从哪里来。

已定、和这一块相关的规则：模型暂定 DeepSeek（[agent 怎么干活](../agent怎么干活.md) 第一节）；整个 agent 程序跑在 AWF 容器里，主程序拿不到真密钥，模型的 key 只交给 AWF 的密钥代理（[安全设计](../安全设计.md)"DeepSeek 密钥怎么藏"）；一次运行限时 60 分钟；轨迹文件里每次调模型记一条 `model_call`，字段有开始、结束、可选的开始出字时间、回答、用量、结束原因、重试次数（[src/repoaegis/langfuse_upload.py](../../src/repoaegis/langfuse_upload.py) 开头的说明）。

## 二、方法和出处

- **DeepSeek 文档**：接口参考 `api/create-chat-completion`，指南 `guides/thinking_mode`、`guides/tool_calls`、`guides/multi_round_chat`、`guides/kv_cache`，`quick_start/error_codes`、`quick_start/rate_limit`、`quick_start/pricing`，变更日志 `updates`，以及 2026-09-10、2026-10-17 两篇新闻。没打开：FAQ、服务状态页。
- **AWF**：`github/gh-aw-firewall` 提交 b8eaea2（即 v0.29.0，我们工作流里用的版本），文档 `docs/api-proxy-sidecar.md`，代理代码在 `containers/api-proxy/`（下文简写 `ap/`），容器环境变量在 `src/services/credentials/`，Squid 配置在 `src/squid/config-generator.ts`。
- **参考项目**：mini-swe-agent v2.4.6、SWE-agent v1.1.0、OpenHands software-agent-sdk v1.59.0、Codex CLI rust-v0.166.0，外加 openai Python 库 v3.42.0。
- 两路子 agent 分别读 DeepSeek 文档和 AWF、各项目源码，关键几条我又对着原文抽查过：排队保活和 10 分钟断开（`quick_start/rate_limit` 第 83–92 行）、思考内容回传（`guides/thinking_mode` 第 45–48、136 行）、AWF 注入的变量（`openai-credential-env.ts` 第 20–32 行）、自动补 `stream_options`（`ap/body-transform.js` 第 164–201 行）、Squid 超时（`config-generator.ts` 第 200–219 行）。

## 三、DeepSeek 接口的事实

**模型和地址。** 地址 `https://api.deepseek.com`，端点 `POST /chat/completions`，请求头 `Authorization: Bearer <key>`。当前模型名只有 `deepseek-flash`（背后是 DeepSeek-V4.1-Flash-1017）和 `deepseek-v4-pro`（DeepSeek-V4-Pro-0813）。`deepseek-chat`、`deepseek-reasoner` 已在 2026-07-24 停用；旧名 `deepseek-v4-flash` 暂时转给新 Flash【一手】（`updates`、`quick_start/pricing`）。2026-09-10 的新闻说 9 月 14 日起 v4-pro 的请求都转给 Flash，同一天的变更日志和价目页却还单独列着 Pro，Pro 现在实际是哪个模型【待验证】。

**思考模式。** 默认开，默认强度 high。开关是请求体里的 `thinking: {"type": "enabled" | "disabled"}`，强度是 `reasoning_effort`，取 `none`、`low`、`high`、`max`【一手】（接口参考、`guides/thinking_mode`）。思考模式下温度不起作用，传了不报错；`max_tokens` 不设时，思考模式默认 64K、max 档 128K，上限 393216【一手】（接口参考）。

**思考内容必须原样回传。** 请求只要带了工具，之前所有轮次的 `reasoning_content` 都必须传回去，原文强调"even for turns where the model did not perform a tool call"，漏了就返回 400【一手】（`guides/thinking_mode` 第 136 行）。指南的做法是把上一次回答的 message 原样追加进对话。但接口参考页对 assistant 消息的结构定义里没列 `tool_calls`，还把 `reasoning_content` 标成只在前缀续写时用，和指南矛盾；带着两者回传能否被接受，要实测【待验证】。

**回答。** message 里有 `content`（可以是空）、`reasoning_content`、`tool_calls`；工具参数是一段 JSON 文字，原文提醒它"does not always generate valid JSON"。结束原因有六种：`stop`、`length`（到了 max_tokens 或上下文上限）、`content_filter`、`tool_calls`、`insufficient_system_resource`（推理资源不足被中断）、`aborted`（被中断）。用量里有命中缓存和未命中缓存的输入、输出、其中思考用的数【一手】（接口参考）。

**流式。** 每块是一段增量；工具调用按 `index` 分开，第一块带编号、类型和函数名，之后的块只带参数片段；最后一块带用量；以 `data: [DONE]` 结束【一手】（接口参考）。文档示例只演示了普通文字，思考内容和工具调用的增量实际长什么样【待验证】。

**排队和超时。** 账号并发上限 Flash 2500、Pro 500，超了返回 429。请求在排队时，服务端会一直发东西保持连接：非流式发空行，流式发 `: keep-alive`；排了 10 分钟还没开始推理就断开【一手】（`quick_start/rate_limit` 第 83–92 行）。开始推理以后有没有总时长上限，文档没写【待验证】。

**错误码。** 400 格式错、401 key 错、402 余额不足、422 参数错、429 太快或超并发、500 服务器出错（"retry your request after a brief wait"）、503 过载（稍等再试）。文档明确建议重试的只有 500 和 503【一手】（`quick_start/error_codes`）。

**缓存。** 自动开，按前缀完整匹配命中，尽力而为，几小时到几天清掉；命中的输入价格约是未命中的五十分之一【一手】（`guides/kv_cache`、`quick_start/pricing`）。我们每一步都是在上一次对话后面追加，前缀天然相同【推断】。

**价格**（美元每百万 token，错峰 / 高峰）：Flash 命中缓存 0.003 / 0.006，未命中 0.15 / 0.30，输出 0.60 / 1.20；Pro 分别是 0.022 / 0.044、0.66 / 1.32、1.98 / 3.96。高峰是 UTC 周一到周五 01:00–04:00、06:00–10:00，中国法定节假日除外【一手】（`quick_start/pricing`，当天页面）。

## 四、AWF 密钥代理的事实

**容器里看到什么。** 主机上有 `OPENAI_API_KEY` 时，容器里得到 `OPENAI_BASE_URL=http://172.30.0.30:10000` 和占位值 `OPENAI_API_KEY=sk-placeholder-for-api-proxy`【一手】（`openai-credential-env.ts` 第 20–32 行）。把上游指到 DeepSeek 用 `--openai-api-target api.deepseek.com`；代理把请求路径原样接在上游地址后面，所以发 `$OPENAI_BASE_URL/chat/completions` 就到 `https://api.deepseek.com/chat/completions`【一手】（`ap/proxy-utils.js` 第 170–184 行，`ap/providers/openai.js` 第 70 行）。真钥匙放在运行 `awf` 那一步的环境变量里，代理去掉客户端带的认证头、换上真的【一手】（文档第 184–188、339–342 行）。

**代理会改请求。** 它先收齐整个请求体（上限 10 MB），流式请求没带 `stream_options` 就自动加上 `include_usage`，还会去掉模型名前的"厂商/"前缀【一手】（`ap/body-handler.js`、`ap/body-transform.js`）。自动加 `stream_options` 曾经弄坏过别的上游（AWF issue #10710）【一手】，DeepSeek 支持这个参数，所以对我们无害；我们自己带上它，代理就不改【推断】。

**流式直通，没有重试。** 回答用管道直接转给容器，不缓冲【一手】（`ap/upstream-response.js` 第 346、426 行）。代理对上游不设超时，链路上起作用的是 Squid 的读超时 30 分钟【一手】（`config-generator.ts` 第 200–219 行）。OpenAI 这一路不重试 5xx 和 429，连不上上游直接回 502【一手】（`ap/upstream-http.js` 第 794–812 行）。所以重试必须我们自己做。

**日志。** 文档说不记请求和回答的内容，但上游返回非 2xx 时，代码会记下最多 64 KB 的错误正文、请求里的工具名和模型名，认证类字段会打码，结束后作为产物保存【一手】（`ap/upstream-log.js`）。真钥匙和提示词不会进日志；DeepSeek 的 400 正文如果回显了输入，会被记下【推断】。

**白名单。** 代码会自动把上游主机加进 Squid 白名单，文档却说要手动加【一手，两处不一致】。容器里的命令也能经 Squid 直接访问 `api.deepseek.com`，但只有占位钥匙，会被拒【推断】。

## 五、各项目怎么做

**mini-swe-agent。** 用 litellm 库，不流式，不设超时；几种明确不该重试的错误（认证、找不到、上下文超长等）以外全部重试，最多 10 次，间隔 4 秒起指数增长到 60 秒；每个任务默认费用上限 3 美元。回答被截断时提示模型"说短一点"，没有工具调用就当格式错误打回。它把整条 message 存进历史，思考内容会跟着回传【推断】。（`models/litellm_model.py` 第 64–71 行，`utils/retry.py` 第 19–57 行，`agents/default.py` 第 100–114 行）

**SWE-agent。** litellm，不流式，最多重试 20 次、间隔 10 到 120 秒随机；发送前先数 token，超了就当上下文超长退出。它只保留 content 和 tool_calls，会丢掉思考内容【一手】（`sweagent/agent/models.py` 第 715–729 行），这在 DeepSeek 思考模式加工具时就是 400。

**OpenHands SDK。** litellm，默认不流式；重试连接错误、超时、408、409、429、5xx 和"回答为空"，默认 5 次，间隔 8 秒起翻倍到 64 秒，并且不短于服务端给的 Retry-After；每次尝试 300 秒硬超时，流式时两块数据之间最多等 300 秒。对名单里的 DeepSeek 模型专门回传思考内容，来由正是有人报了 400（issue #4249）【一手】（`llm/llm.py` 第 392–409、1149–1168 行，`utils/retry_mixin.py` 第 25–132 行，`utils/model_features.py` 第 300–310 行）。

**Codex CLI。** 自己写的 HTTP 客户端，只支持 Responses 接口，接不了 DeepSeek 的 chat completions。流式；两块数据之间最多等 300 秒，流断了、超时、5xx、429 都重试，截断、上下文超长、额度用完不重试；连接失败的间隔 5 秒起到 60 秒【一手】（`codex-api/src/sse/responses.rs` 第 584–619 行，`protocol/src/error.rs` 第 421–473 行）。

**openai Python 库。** 默认总超时 600 秒，重试 2 次，重试 408、409、429 和 500 以上，间隔 0.5 秒起最多 8 秒【一手】（`_constants.py`、`_base_client.py` 第 898–964 行）。

## 六、借鉴什么，不借鉴什么

- **借鉴 OpenHands 的重试分类和间隔**：网络错误、超时、429、5xx 重试，格式错、认证错、余额不足不重试；间隔 8 秒起翻倍到 64 秒，尊重 Retry-After。它是几家里唯一明确处理 DeepSeek 思考内容的，分类也和 DeepSeek 错误码页一致。不照搬 mini 的"除了几种全重试"：余额不足（402）重试 10 次没有意义。
- **借鉴 Codex 和 OpenHands 的"两块数据之间的最长间隔"**，而不是给整次调用定一个总时长。思考模式一次可能输出几万 token，总时长定短了会切掉正常回答，定长了又发现不了卡死的连接；DeepSeek 排队时会持续发保活，流式下只要连接活着就有东西来【推断】。
- **借鉴 Codex 把"流在结束前断开"当作可重试的错误**。不借鉴 Codex 的整体做法：它只支持 Responses 接口。
- **不借鉴 SWE-agent 丢掉思考内容的做法**，在 DeepSeek 上会直接 400。
- **不用 litellm**：三家 Python 项目都用它，是为了支持上百种模型；我们只接一个兼容 OpenAI 的接口，它带来的大量依赖和它自己的重试逻辑都用不上。
- **不用 openai 库**【推断，属取舍】：它的重试规则（2 次、最多等 8 秒）和总超时都要改掉，流式拼工具调用也还得自己做；而直接发请求、自己解析流，用 Python 自带的库一百来行就够，和 [langfuse_upload.py](../../src/repoaegis/langfuse_upload.py) 的写法一致，容器里也不用多装包。

## 七、建议（2026-11-02 用户确认，加两条：日志和 Langfuse 都显示 token，不显示钱；回答收得久时每 30 秒在日志里打一行进度）

1. **用 Python 自带的库直接发 HTTP 请求**，不装 openai、litellm。
2. **用流式。** 能记下开始出字的时间，能靠"多久没收到数据"判断卡死。
3. **等待**：连接 30 秒；两块数据之间最多 5 分钟；主循环把这次运行剩下的时间交进来，到点就停。不给单次调用定总时长。
4. **重试**：连不上、读超时、流中途断开、429、500、502、503、504，以及结束原因是 `insufficient_system_resource`、`aborted` 的，最多再试 5 次，间隔 8 秒起翻倍到 64 秒，有 Retry-After 按它；剩下的时间不够等就不等了。400、401、402、422 不重试，直接按出错交给主循环；`length`、`content_filter` 照常交回主循环处理。
5. **模型和思考强度**：默认 `deepseek-flash`、思考开、强度 high，都写明在请求里，也记进轨迹；不设 `max_tokens`，不传温度。
6. **地址和密钥**：从 `OPENAI_BASE_URL`、`OPENAI_API_KEY` 读，正好是 AWF 注入的两个名字；本机测试时把这两个变量指向 DeepSeek 和 `.env` 里的 key。
7. **回传**：模型的回答整理成可以直接追加进对话的 assistant 消息（content、reasoning_content、tool_calls 都在），主循环原样存、原样发。

## 八、实测（2026-11-02，WSL 本机直连 api.deepseek.com，用 src/repoaegis/model.py）

脚本在 WSL 的 `~/scratch/deepseek-live/`，不进仓库，共调用 `deepseek-flash` 约 7 次。

- **带着 `tool_calls` 和 `reasoning_content` 回传，接口接受**【实测】。文档里两处矛盾按指南的说法成立。
- **去掉思考内容回传，也被接受了，没有返回文档说的 400**【实测，只测了一种情况：一轮工具调用后的第二轮】。代码仍照文档原样回传；保留思考内容的那次，第二轮 4,081 个输入 token 里 3,840 个命中缓存，说明回传的思考内容也进了缓存前缀【实测】。
- **简单任务模型会跳过思考**：请求写了开思考、强度 high，"运行 `echo hello`"这种任务思考 token 是 0，流里只有一个空的 `reasoning_content` 字段【实测】。换成"找出一段代码在哪些输入上出错"，想了 11,130 字（3,521 token），这一步 19.5 秒【实测】。
- **流式碎片的样子和代码的拼法一致**【实测】：第一段 `delta` 带 `role`、空的 `reasoning_content`、`content: null`；工具调用第一段带 `id`、`type` 和函数名，之后每段只带几个字符的参数碎片（`{"`、`command`、`"`、`: "`……）；最后一段 `delta` 为空文字、带 `finish_reason` 和完整用量，然后是 `data: [DONE]`。这几次没有排队，没见到 `: keep-alive`。
- **回应头里有 `x-ds-trace-id`**【实测】，可以当请求编号，现在没有记下来。
- 用量字段和文档一致：`prompt_cache_hit_tokens`、`prompt_cache_miss_tokens`、`prompt_tokens_details.cached_tokens`、`completion_tokens_details.reasoning_tokens` 都有【实测】。

## 九、尚未查清

1. 去掉思考内容在什么情况下会 400（多轮、多次工具调用后？），只测了一种情况。
2. 开始推理以后服务端有没有总时长上限；生成途中两段之间最长会停多久（决定 5 分钟要不要缩短）。对话超长时 400 的错误信息具体怎么写，代码现在按几个关键词认【待验证】。
3. AWF 接 DeepSeek 的完整链路（启动时它会请求一次 `/v1/models`，DeepSeek 是否支持）；要在测试仓库的 Actions 上跑。
4. `deepseek-v4-pro` 现在实际由哪个模型提供。
5. 工具个数、请求体大小的上限，缓存的切分间隔和最小长度。
6. 回应头里的 `x-ds-trace-id` 要不要记进轨迹，方便出问题时找 DeepSeek 查。
