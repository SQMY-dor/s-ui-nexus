# 自动 HTTPS（域名与证书）

> Ported from shenaba/2s-ui (GPL-3.0), adapted for s-ui-nexus.

面板可以用 [acme.sh](https://github.com/acmesh-official/acme.sh) 为面板 / 订阅域名自动申请、安装、续期 Let's Encrypt 证书，也可以登记你自己的证书，并可选地自动生成 nginx 反向代理配置。证书更换后**无需重启面板**即可生效（热加载）。

本文只描述后端行为；前端页面在 P3 阶段提供。

## 1. 前提条件

| 条件 | 说明 |
| --- | --- |
| Linux | 仅支持 Linux（其他系统上相关接口直接返回错误，`acmeStatus.supported=false`）。 |
| root 运行 | 默认把证书装到 `/root/cert/<域名>/`，standalone 验证要绑定 80 端口，安装 acme.sh / socat 需要包管理器，均需要 root。 |
| 80 端口可从公网访问 | HTTP-01 验证由 Let's Encrypt 从公网访问 `http://<域名>/.well-known/acme-challenge/...`。云厂商安全组 / 防火墙要放行 TCP 80。 |
| DNS 解析 | 域名的 A（以及 AAAA，如有）记录必须指向本机公网 IP。有 AAAA 记录时 Let's Encrypt 会优先走 IPv6，务必确保 IPv6 也可达或删掉 AAAA。若使用 Cloudflare，申请期间请关闭橙色云（仅 DNS）。 |
| 网络 | 首次申请时需要从 `get.acme.sh` 下载安装 acme.sh。 |

## 2. 验证方式（method）

| 取值 | 行为 |
| --- | --- |
| `auto`（默认，空值同） | 80 端口空闲 → `standalone`；80 被占用且 nginx 正在运行 → `nginx`；否则报错并说明原因。 |
| `standalone` | acme.sh 临时监听 80 端口完成验证（需要 `socat`，缺失时自动尝试 apt/yum/dnf/pacman 安装）。80 被占用时直接报错，提示改用 nginx 或停止占用者。纯 IPv6 主机自动加 `--listen-v6`。 |
| `nginx` | 借用正在运行的 nginx（`acme.sh --nginx`），不中断 80 端口上的服务。若 nginx 中没有该域名的 `:80` server 块，会生成 `s-ui-acme-<域名>.conf` 验证块，`nginx -t` 失败、server_name 冲突或未被 include 时自动删除并回滚。 |

80 端口占用检测在准备前和发起申请前各做一次。面板若**不是** root 运行，绑定 80 会被内核拒绝，错误信息里会提示这一点。

同一时刻只允许一个申请/续期在进行，重复点击会被拒绝（避免撞 Let's Encrypt 限速）。

## 3. 文件位置

| 内容 | 默认路径 | 覆盖用环境变量 |
| --- | --- | --- |
| 安装出来的证书 | `/root/cert/<域名>/fullchain.pem`、`privkey.pem` | `SUI_CERT_DIR` |
| acme.sh 家目录 | 依次查找 `$SUI_ACME_HOME/acme.sh`、`$HOME/.acme.sh`、`/root/.acme.sh`、`/.acme.sh` | `SUI_ACME_HOME`（只用于定位；自动安装始终装到 `/root/.acme.sh`） |
| 生成的 nginx 配置 | `/etc/nginx/conf.d/s-ui-proxy-<域名>.conf`（反代）、`s-ui-acme-<域名>.conf`（验证块） | `SUI_NGINX_CONF_DIR`（Alpine 用 `/etc/nginx/http.d`） |
| 手动登记的证书 | 数据库表 `certs`（只存路径，备份 / 导入会包含它） | — |

acme.sh 申请的证书不入库，列表直接扫描 acme.sh 家目录（到期时间、下次续期时间、CA、密钥类型）。

## 4. 应用到面板 / 订阅

`applyCert` 把证书路径写入现有设置 `webCertFile` / `webKeyFile`（面板）或 `subCertFile` / `subKeyFile`（订阅）：

- 服务**已经在跑 HTTPS**：立即热替换，返回 `hotReloaded: ["web"]`，无需重启。
- 服务当前是 **HTTP**：需要重启面板（监听器类型要变），返回 `restartRequired: ["web"]`，前端可随后调用 `restartApp`。
- `setDomain=true` 时同时把 `webDomain` / `subDomain` 设为该域名，这同样需要重启。

### 与 webDomain（DomainValidator）的关系

`webDomain` / `subDomain` 非空时，`middleware.DomainValidator` 会拒绝 Host 不等于该域名的请求（用 IP 访问返回 403）。因此：

- `applyCert` **默认不改** `webDomain`，避免把正在用 IP 访问的你锁在门外；需要时显式传 `setDomain=true`。
- 若域名设置非空（已有或经 setDomain 设置），证书必须覆盖它，否则拒绝（可用 `force=true` 强制）。
- 域名设置为空时会返回警告：服务接受任意 Host，用 IP 访问时浏览器会提示证书名不匹配。
- TLS 层不按 SNI 拒绝握手（与 s-ui-next 原行为一致；2s-ui 会拒绝），Host 校验仍由 DomainValidator 负责。

删除证书时，若面板或订阅的域名设置或证书路径正指向它，会拒绝删除。

## 5. 续期

- 申请成功后执行 `acme.sh --install-cronjob`，由 acme.sh 自己的 crontab 续期。
- 面板另有每日任务（03:17，面板时区）执行 `acme.sh --cron`，不依赖系统 cron；完成后强制热加载面板 / 订阅证书。
- 证书热加载器在每次 TLS 握手时比较文件的 mtime 和大小，续期覆盖文件后自动生效；新文件读取失败（例如只写了一半）时继续使用旧证书。
- 若 nginx 是证书的使用方（nginx 验证方式，或开启了反代），安装证书时会配置 `--reloadcmd "systemctl try-reload-or-restart nginx"`（仅当 systemd 认识 nginx unit 时；否则留空，需要你自己配置 reloadcmd）。
- sing-box 入站 TLS 若通过 `certificate_path` 引用同一份文件，sing-box 会自己监测文件变化并重新加载，**不会重启内核**。
- 手动续期：`renewCert`（`force=true` 强制续期，会消耗 Let's Encrypt 限速额度）。acme.sh 回复“未到续期时间”时返回成功且 `renewed=false`。

## 6. nginx 反向代理（可选）

设置 `webNginx=true` / `subNginx=true` 表示 “由 nginx 终结 TLS”：面板 / 订阅自身改为明文 HTTP，nginx 在 443 上用证书对外提供 HTTPS 并反代到本机端口。

流程（与 2s-ui 一致）：

1. 前端先调用 `syncNginxProxy`（带表单值）。后端为每个启用的域名生成 `s-ui-proxy-<域名>.conf`（面板与订阅同域名时合并为一个 server 块的两个 location），写入前备份旧文件，执行 `nginx -t`；失败、出现 443 上的 server_name 冲突、reload 失败或文件未进入生效配置（`nginx -T` 复验）时，**恢复旧文件 / 删除新文件并再次 reload**，返回 nginx 原始输出。
2. 成功后再保存 `webNginx=true` 等设置并重启面板。
3. 关闭反代：保存 `webNginx=false` 并重启，启动时的对账会删除不再需要的 vhost（只删带本实例标记的文件，不碰同机其他面板或用户自己写的配置）。
4. `checkNginxProxy` 只检查不写入，返回 `{drift: true|false}`。

reload 优先 `systemctl reload nginx`（systemd 管理时），否则 `nginx -s reload`。

建议开启反代后把 `webListen` / `subListen` 设为 `127.0.0.1`，避免明文端口暴露在公网。订阅链接（未手填 `subURI` 时）在 `subNginx=true` 下自动变为 `https://<subDomain><subPath>`。

## 7. 使用自己的证书

`saveManualCert`：填写域名、证书路径、私钥路径（服务器上的绝对路径）。后端会检查：文件可读、证书与私钥配对、证书覆盖该域名（支持 `*.example.com`）。登记后可以像 acme.sh 证书一样 `applyCert`，但续期由你自己负责，更新文件后热加载会自动生效。删除登记不会删除你的文件。

升级前在设置里手填的 `webCertFile` / `subCertFile`（且有对应域名）会在启动时自动登记为手动证书。

## 8. 安全

- 域名逐段严格校验（只允许字母、数字、`-`，不能以 `-` 开头或结尾，不能有空段，至少两段，不接受 IP）；邮箱同样校验。
- 所有外部命令以参数数组方式执行（`exec.Command`），不会经过 shell 拼接用户输入；唯一的 `sh -c` 是固定的 acme.sh 安装脚本。
- 全部接口需要登录（session）。

## 9. 排错

| 现象 | 处理 |
| --- | --- |
| `80 端口被占用,无法用 standalone 申请` | `ss -ltnp 'sport = :80'` 查占用者；是 nginx 就改用 `nginx` / `auto`，否则先停掉。 |
| `80 端口不可用且未检测到运行中的 nginx` | 同上；非 root 运行时也会出现，请以 root 运行。 |
| acme.sh 报 `Timeout` / `Connection refused` / `Invalid response` | 检查 DNS A/AAAA 记录、云安全组和防火墙是否放行 80、CDN 是否关闭代理。 |
| `Can not find conf file for domain` / 生成的验证块未生效 | 你的 `nginx.conf` 可能没有 `include /etc/nginx/conf.d/*.conf;`；加上，或改用 standalone，或设置 `SUI_NGINX_CONF_DIR`。 |
| `nginx -t` 失败已回滚 | 返回信息里带 nginx 原文；通常是已有配置本身有错误，或 443 被其他程序占用。 |
| `rateLimited` | Let's Encrypt 限速：同域名每周 5 张重复证书。不要频繁“强制续期”，等限速窗口过去。 |
| 申请成功但面板仍是旧证书 | 面板是 HTTP 时需要重启；HTTPS 时检查 `webCertFile` 是否指向 `/root/cert/<域名>/fullchain.pem`。 |
| 自动安装 acme.sh 失败 | 手动执行 `curl https://get.acme.sh | sh`（以 root），然后重试。 |
