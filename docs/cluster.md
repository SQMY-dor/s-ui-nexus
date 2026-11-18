# 多节点集群（后端）

> 集群功能移植自 [shenaba/2s-ui](https://github.com/shenaba/2s-ui)（GPL-3.0），并按 s-ui-nexus 的代码做了适配。本文说明后端行为；管理页面（Nodes 页）在后续前端 PR 中提供。

## 概念

- **主面板（master）**：你日常管理用户的那台 s-ui-nexus。它保存节点列表、用户（clients）和订阅。
- **节点（node）**：另一台运行 **s-ui-nexus** 的面板。主面板通过节点的 `apiv2`（Token 鉴权）读取状态、拉取入站、下发用户、回收流量。
- **副本入站（replica）**：主面板“采纳”节点上的某个入站后，在本地数据库保存的一份只读副本（`inbounds.node_id` 非空）。用户在主面板勾选副本入站，即表示允许该用户使用这个节点上的入站。

> 节点必须同样运行 s-ui-nexus。原版 s-ui / s-ui-next 缺少 `clients?full=1`、`save` 的 `sync` 参数和 `status.sbd.version` 等字段，只能部分工作，不受支持；2s-ui 节点使用 sing-box 1.14，同样不受支持。

## 添加节点

1. 在**节点**面板的 *Admins* 页为某个管理员生成一个 API Token（建议单独建一个，并设置过期策略）。OIDC-only 的用户同样可以生成 Token。
2. 在**主面板**添加节点（API `save`，`object=nodes`，`action=new`），字段：
   - `name`：节点名，唯一，不能包含 `[` 或 `]`（它会作为订阅链接备注前缀 `[节点名] `）。
   - `baseUrl`：`http(s)://主机[:端口]`，不带路径。
     **如果节点面板设置了面板域名（webDomain），这里必须填该域名而不是 IP**：节点的 `middleware/DomainValidator` 会对 Host 不匹配的请求直接返回 403（主面板会提示 “HTTP 403 from node panel …”）。
   - `webPath`：节点面板的 Web 路径，默认 `/app/`（会自动补齐首尾 `/`）。
   - `token`：上一步生成的 Token。编辑时留空表示保留原 Token；读取接口永远不会返回 Token，只返回 `tokenSet`。
   - `insecure`：HTTPS 时跳过证书校验（自签证书时使用）。
   - `certPin`：节点叶子证书的 SHA-256（十六进制，可带冒号），设置后取代链校验，优先于 `insecure`。
   - `enable`、`desc`。
3. 可先用 `testNode` 测试连接（按表单内容探测，不必先保存）。
4. 用 `nodeInbounds` 查看节点入站，用 `adoptInbounds` 采纳需要的入站。采纳后主面板会立即对该节点做一次 reconcile。
   - 标签（tag）在主面板全局唯一：如果主面板已有同名入站，采纳会失败，请先在节点上改名。

## 心跳与状态

- 每 **5 秒**探测一次所有启用的节点（`GET apiv2/status?r=cpu,mem,sys,sbd`，核心运行时再取 `apiv2/onlines`），状态只保存在内存快照中：
  - `online`：面板可达且 sing-box 正在运行；
  - `core-stopped`：面板可达但 sing-box 未运行；
  - `offline`：不可达、Token 被拒、返回非 200 或格式不对（`error` 字段给出原因）。
- 只有在 online → 非 online 的转变时才把 `lastSeen` 写入数据库。
- 每次心跳后，对“在线且有待同步改动（dirty）”的节点做 reconcile。

## 用户下发（reconcile）

- 主面板中引用了某节点副本入站的用户，会被下发到该节点，形状为节点本地用户：
  - `group` 固定为 **`@cluster`**（reconcile 只管理这个分组，不会动节点自己的本地用户）；
  - `volume` 为 0（流量配额只由主面板判定），`expiry` 原样复制（节点自行到期）；
  - `inbounds` 映射为节点本地入站 id；`links` 为空，由节点自行生成；
  - 不包含 s-ui-nexus 没有的字段（如 2s-ui 的 `limitIp`、`tgId`、`resetDayOfMonth`）。
- 主面板侧的新增 / 修改 / 禁用 / 删除都会通过 reconcile 生效；编辑时会带回节点当前的 `up/down`，避免把节点计数清零。
- 触发时机：
  - Web 面板（api v1）和移动端（api v3）保存 clients / inbounds 后立即触发；
  - `apiv2/save` 仅在带 `sync=true` 时立即触发（值按 `strconv.ParseBool` 解析，非法值直接报错），否则等待下一次心跳 / 每小时兜底；
  - 到期 / 超额禁用、周期重置、延迟启用（depleteJob）后立即触发；
  - 每 **1 小时**对所有在线节点做一次全量 reconcile（兜底，修复节点侧被手工改动的漂移）；
  - 手动 `reconcileNode`。
- 每个节点单飞执行，后台触发有 30 秒退避；手动触发跳过退避，正在执行时返回错误。

## 副本入站只读

- 副本入站**不会进入本机 sing-box 配置**（`node_id IS NULL` 过滤），也不会参与本机路由引用、out_json 重写、本地链接生成。
- 入站列表（`apiv2 GET inbounds`、api v3 `GET resources/inbounds`）和完整形状（`?id=`）中，副本带 `node_id`（节点 id），本地入站不含该键；副本不返回 `volume/up/down`（流量在节点上统计）。
- 通过普通入站保存接口**编辑**或**新建**副本会被拒绝（请到节点面板上修改，reconcile 会把节点上的变化同步回来）。
- 在入站列表**删除**副本 = 取消采纳：只删数据库记录，节点上的入站继续运行，下一次 reconcile 会删除推送过去的 `@cluster` 用户；用户上的 `[节点名] 标签` 链接会被立即移除。
- **删除节点**会同时删除它的所有副本入站以及对应的订阅链接（节点上已推送的 `@cluster` 用户不会被远程删除，如需清理请在节点上处理）。

## 订阅合并

- reconcile 时，主面板根据副本入站的 `out_json`/`addrs` 快照和用户凭据，在本地为每个用户生成节点链接，以 `external` 类型、备注 `[节点名] 入站标签` 合并进 `client.links`。服务器地址取节点快照里的地址（与链接式订阅一致）。
- 凭据取自主面板用户的 `config`：socks/http/mixed 副本按节点实际鉴权使用的条目生成（mixed 入站用 `config.mixed`，缺失时回退 `socks`/`http`，反之亦然），不会再生成 `socks5://:@…` 这种空凭据链接。已有的空凭据快照会在下一次 reconcile（保存该用户或节点、手动 `reconcileNode`，或每小时兜底）时被重写。
- 三种订阅格式包含同一组节点：
  - 普通（base64 链接）：直接输出本地链接 + `external` 链接；
  - `?format=json`（sing-box）：本地入站按 `out_json` 生成 outbound（仍只取 `node_id IS NULL`，避免重复），节点副本通过上述 `external` 链接转换为 outbound，标签为链接备注 `[节点名] 入站标签`（多条时加序号前缀）；
  - `?format=clash`：同 JSON，再转换为 Clash 代理。socks5:// / http(s):// 链接现在可以被转换（此前转换器不支持这两种协议，节点的 mixed/socks/http 副本因此被丢弃）。
- 修改用户时，节点链接从数据库中保留（只要该副本仍绑定在用户上），不会被旧页面的表单覆盖。
- 节点改名会把所有 `[旧名] ` 前缀改写为 `[新名] `。

## 流量回收

- 每 **1 分钟**从在线节点拉取 `@cluster` 用户的累计 `up/down`，按“节点 × 用户”基线（`nodes.baselines`）计算增量后累加到主面板用户的 `up/down`（节点计数回退时视为重置，重新取基线）。
- 开启了流量历史（trafficAge > 0）时，同时写入 `stats` 表（`resource=user`），用户流量图包含节点流量。
- 配额判定、延迟启用、周期重置都在主面板完成，结果再通过 reconcile 下发。

## 节点端 apiv2 协议要点（s-ui-nexus 已支持）

| 请求 | 说明 |
| --- | --- |
| `GET status?r=cpu,mem,sys,sbd` | `cpu`、`mem.current/total`、`sys.appVersion`、`sbd.running`、`sbd.version` |
| `GET onlines` | `{inbound,user,outbound}` 在线名单 |
| `GET inbounds` / `GET inbounds?id=1,2` | 列表（id/type/tag…）/ 完整形状（含 `out_json`、`addrs`） |
| `GET clients?full=1` | 列表投影 + `config`（含 `group`、`up`、`down`、`expiry`、`enable`、`inbounds`） |
| `POST save`（form：`object`、`action`、`data`，可选 `sync`） | 主面板下发用户使用 `object=clients` |

所有请求通过 `Token`、`Authorization: Bearer` 或 `X-API-Token` 头鉴权。

## 主面板 API（api v1，会话鉴权）

| 请求 | 参数 | 返回 |
| --- | --- | --- |
| `GET api/nodes` | — | `{nodes:[{id,enable,name,baseUrl,webPath,insecure,certPin,desc,lastSeen,tokenSet,dirty,lastSync,status?}]}` |
| `GET api/load` | `lu` | 原有字段 + 每次都有的 `nodesStatus`（`{节点id: NodeStatus}`），有变更时另含 `nodes` |
| `POST api/save` | `object=nodes`，`action=new/edit/del`，`data` | 同其它对象；`del` 的 data 为节点 id |
| `POST api/testNode` | `data`=节点 JSON | `NodeStatus` |
| `GET api/nodeInbounds` | `id` | `[{id,type,tag,adopted}]` |
| `POST api/adoptInbounds` | `id`，`tags`=JSON 字符串数组 | 成功/失败消息 |
| `POST api/reconcileNode` | `id` | 成功/失败消息 |

`NodeStatus`：`{state,latency,cpu,mem:{current,total},appVersion,coreVersion,error?,checkedAt,lastOnline}`。

api v3 的 `GET resources/nodes` 返回同样的节点列表，`POST resources/nodes` 走同一保存逻辑。
