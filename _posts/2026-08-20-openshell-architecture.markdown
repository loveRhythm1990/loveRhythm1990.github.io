---
layout:     post
title:      "General architecture of OpenShell"
date:       2026-08-20 10:00:00
author:     "lr90"
header-img-credit: false
tags:
    - Sandbox
---

OpenShell 是给自主 Agent（Claude Code、Codex、OpenCode 这类可以执行命令的 agent binary）提供的**带策略的执行环境**。这类 agent binary 一旦能跑命令，理论上就能读本机任意文件、任意出网。OpenShell 的作用是把它们放进 Sandbox，用声明式 YAML 限制文件系统、进程权限和出站目标，把 Agent 的活动范围收在用户划定的边界内，防止它读到敏感数据、或者对宿主系统造成破坏。

用法是 `sandbox create -- <命令>`，这条命令就成了 Supervisor 的受限子进程。运行时是三层：**CLI/SDK/TUI** 只和 **Gateway** 通信，每个 Sandbox 里再跑一份 **Supervisor**，做本地隔离和出站策略。这套控制面（CLI ↔ Gateway ↔ Supervisor）跟 Sandbox 具体怎么起来是分开的：本机用 Docker，远程用 Kubernetes，还有 Podman、VM，对 Gateway 来说都只是背后一个可插拔的 **Compute Driver**，只管底层怎么起容器/Pod，不参与控制面逻辑。

## 1. 总览

OpenShell 的角色和连接关系：

```text
CLI / SDK / TUI
      │  gRPC / HTTP
      v
   Gateway ──provision──► Driver ──► Docker / K8s / VM
      :                                    │
      :  outbound session                  │
      :                                    v
      :                                 Sandbox
      └····················► Supervisor ──► Agent ──出网──► 外部 API
```

| 名字 | 在哪 | 是什么 |
|------|------|--------|
| CLI / SDK / TUI | 本机 | 唯一用户入口。创建 Sandbox、改策略、`connect` / `exec`。不知道底下是 Docker 还是 K8s |
| Gateway | 本机进程或集群 Service | 控制面。鉴权、状态、生命周期、配置下发、把终端字节转进 Sandbox |
| Sandbox | 一个容器 / Pod / VM | 一次数据面 workload |
| Supervisor | Sandbox 里面的入口进程 | 本地安全边界。隔离、出站代理、回连 Gateway、拉起 Agent |
| Agent | Supervisor 的子进程 | Claude Code / Codex / OpenCode 等 |
| Driver | Gateway 旁边 | 把「起停一个 Sandbox」翻译成 Docker/K8s/VM，并回报 backend 状态与平台事件；不负责策略 enforcement |

图中 Gateway 和 Supervisor 之间的虚线是**outbound session**：Supervisor 启动后主动 `ConnectSupervisor` 连上 Gateway，一直挂着，走配置、日志，以及"再开一条终端管道"这类信令。不是 Agent 出网的流量，也不是命令的 stdout 本身。

图上有两条互不相干的流量：**人操作 Sandbox**（`connect`、`exec -- ls`、创建）：CLI → Gateway → Supervisor。默认不直接暴露 Pod/SSH，集群管理员仍可能通过 `kubectl exec` 等平台权限访问。**Agent 自己出网**（调模型、访问 GitHub）：Agent → Sandbox 内 proxy → 目标站点。不经 Gateway，也不经 CLI。

职责划分：Gateway 拥有对象和授权（谁能做什么），Supervisor 拥有进程/文件/网络层面的实际 enforcement，Driver 只负责把「起停一个 Sandbox」翻译成具体平台的 API。静态隔离（Landlock、降权、seccomp、netns）在 Sandbox 创建时钉死，要改只能重建 Sandbox；网络策略、凭证、推理路由可以在已有会话上热更新。会话断了，Agent 还能继续跑，只是 `connect` 和热更新会失败。

## 2. Gateway：控制面

Gateway 是二进制 `openshell-gateway`（对应 crate `openshell-server`）。CLI 和所有 Supervisor 都只连它，不会直连底层容器或 Pod。

做：鉴权、持久化、让 Driver 起停 workload、下发策略/凭证/推理路由、把 CLI 的终端字节转发到对应 session。不做：拦截 Agent 出网、跑 Landlock，那是 Supervisor 的工作；Gateway 本身看不见 Sandbox 里的进程身份和 socket。

### 2.1 内部模块

一个端口先做 multiplex，拆出 gRPC API 和 HTTP 隧道；鉴权层区分用户身份（mTLS/OIDC）和 Sandbox 身份（JWT），二者不能互换；再往后分别落到持久化、compute+driver、session registry、policy/inference 几块，最终经拦截器交给 handler。下图是这条用户请求链路：

```text
CLI / SDK
    │  一个 TCP 端口
    v
multiplex
    ├── gRPC
    └── HTTP（明文）
          │
          v
        auth
          │
          ├── persistence
          ├── compute/driver
          ├── session registry
          └── policy/inference
```

Supervisor 不在这条链路上：它通过 `ConnectSupervisor` / `RelayStream` 单独接入 session registry，没有画在图里。

`multiplex` 是"多路复用"：Gateway 对外只开一个 TCP 端口，但要同时服务两种协议，gRPC（跑在 HTTP/2 上，走 protobuf 二进制帧）和普通 HTTP（health 检查、WebSocket 隧道用的明文 HTTP/1.1）。要在同一个端口上分流，得在协议栈真正接管这条连接之前，先"偷看"一眼开头几个字节，判断这是 HTTP/2 的 connection preface 还是普通 HTTP 请求，再把连接转给对应的协议栈。这一步做完之后，两条协议路径才汇合到 `auth`，后面的鉴权、路由逻辑是共用的。

- `multiplex` / `gateway_listener`：一端口拆 gRPC 与 HTTP；Docker/Podman 可再开仅 sandbox RPC 的 callback 口。
- `auth`：用户 vs `Principal::Sandbox`；`rpc_auth` 决定哪些 RPC 能出现在 callback 口。
- `persistence`：protobuf 对象库。
- `compute`：生命周期；合成公开 `SandboxPhase`。
- `credentials` / `provider_*`：逻辑 Provider → Secret 后端。
- `policy_store` / `inference`：策略 revision、模型路由 bundle。
- `supervisor_session`：进程内 session 表 + pending relay。
- `grpc/*`：sandbox / provider / policy / workspace RPC。
- `ws_tunnel` / `ssh_sessions`：隧道与 SSH session 元数据。
- interceptors：认证后、handler 前的 unary 拦截（allowlist；secret 字段从拦截载荷剥掉）。

`persistence` 这个名字对应的是行为，不是随手起的：Gateway 里不是所有状态都会落盘。sandbox、provider、policy revision 这些核心对象走 `persistence` 模块，最终写进一个 protobuf 对象库（SQLite/Postgres），Gateway 重启也不会丢。同一张表里的 `supervisor_session` 是反例：它只存在 Gateway 进程内存里，不落库，Gateway 一重启，这些 in-flight 的 session 记录就没了（2.4 节讲的 Ready 状态为什么要求单副本，根源也在这里）。`persistence` 这个名字标的正是"这块状态是持久化的"，用来跟这些不持久化的模块区分开。

### 2.2 两种身份

两种身份指用户身份（CLI/SDK/TUI，走 mTLS/OIDC）和 Sandbox 身份（Supervisor，走 JWT），2.1 节鉴权层区分的正是这两种。

在启用 loopback listener 的部署中，一个端口同时跑 gRPC 和 HTTP（health、WebSocket 隧道）。loopback 明文 HTTP 只给 Sandbox 服务子域，不承载 Gateway API；这不是所有远程部署的通用入口。两种调用方怎么进、能调什么：**CLI / SDK / TUI**：本地默认 mTLS；K8s 用 OIDC 或接入代理。能调 Sandbox CRUD、策略、Provider、watch。**Supervisor**：Sandbox JWT。仅 allowlist：`ConnectSupervisor`、`RelayStream`、续期、config sync、日志、策略状态。

K8s 上，Supervisor 不用 mTLS 证明身份：用 projected SA token 调 `IssueSandboxToken`，Gateway 做 `TokenReview` 并核对 Pod / Sandbox 的 ownerReference 后签发 JWT。本机 Docker/Podman/VM 由 runtime 把初始 token 注入进程；这个 JWT 默认 `ttl_secs = 0`（不过期），共享集群应设正 TTL。

`Health` 接口不鉴权，CLI 用 `GetGatewayInfo` 来判断用户是否已登录：返回 `Unauthenticated` 说明凭证被拒，返回 `PermissionDenied` 则说明身份验证过了，只是没有 admin 权限。

### 2.3 持久化

Gateway 里的对象统一存成 protobuf payload 加一组索引列（核心字段是 id、type、name、scope、version、status、resource_version、labels；策略相关的对象还可能用到 dedup_key、hit_count）。sandbox、provider、policy revision、SSH session、inference route 共用同一个 Store：SQLite 是单人默认选项，Postgres 用于外置库场景，生产写入统一走 CAS（compare-and-swap）避免并发覆盖。Secret 单独走凭证后端；如果没有配置外部后端，Gateway 会把它加密后存进库里。

### 2.4 Ready 从哪来

Ready 不是某个组件直接吐出来的一个字段，是 Gateway 拿两个信号拼出来的：Driver 报的 backend 事实（容器/Pod 是否在跑，顺带带上生命周期和平台事件），加上 supervisor session 有没有注册上。两个都满足才算 Ready。backend Ready 但 session 没到 → Provisioning（`SupervisorNotConnected`）；session 已经到、backend 快照还没跟上 → 仍然算 Ready。

`Stop` 只关掉 exec/SSH，资源和磁盘都保留；`Start` 要等新 session 建立起来才算完成。`SupervisorSessionRegistry` 只在 Gateway 进程内存里，不落库，所以多副本部署会把 Ready 状态打乱（上游 issue #1868）；可靠的 Ready 状态实际上要求单副本。

未认领 relay 的数量上限、超时时间、心跳间隔是实现细节，会随版本变化，以对应版本的 Gateway 配置和源码为准，不是稳定的 API 契约。

## 3. Supervisor：Sandbox 里的安全边界

`openshell-sandbox` 跑在每一个 Sandbox 里面，是该容器/Pod/VM 的入口进程，不是 Gateway 旁边的服务。

```text
Sandbox
    Supervisor
         │
         v
       Agent
         │
         v
    Policy proxy
         │
         ├──► 外部 API
         └──► inference.local ──► 模型后端
```

Supervisor 以 root 身份跑，做隔离、出站代理、加载配置和凭证、回连 Gateway；它拉起的 Agent（Claude Code / Codex 等）是非特权进程。Linux 上 spawn Agent 时 fail-closed 清空 capability bounding set，清不空就不启动。

启动顺序是固定的：runtime 注入身份与 callback → 加载策略 → 依次挂上 Landlock / 降权 / netns / proxy / 内部 SSH → `ConnectSupervisor` 回连 Gateway → 最后才 exec Agent。Driver 注入的环境变量会覆盖镜像里的同名变量，防止镜像伪造 callback 地址。

- `openshell-sandbox`：编排、OCSF、denial 聚合。
- `openshell-supervisor-process`：降权、seccomp、netns、SSH、session 客户端、日志推送。
- `openshell-supervisor-network`：出站代理、OPA、L7、推理拦截、SigV4。
- `openshell-supervisor-middleware`：L7 通过后、注凭证前的 HTTP 链。

隔离是好几层叠加在一起，不是单一原语：

- **Landlock**：限制文件路径。
- **进程降权**：权限最小化。
- **seccomp**：堵住 raw socket 等系统调用。
- **netns**：出站只能进本机 proxy，绕不过去。
- **policy proxy**：校验目标、二进制身份、SSRF、L7 规则。

### 3.1 前四层管的是什么

前四层都是 Linux 内核原生机制，policy proxy 是 OpenShell 自己在用户态加的一层（下一段单独展开），这里只说内核那四层。这四层大致对应内核处理一次操作时依次经过的几个检查点：先看进程有没有资格做这件事（capability、Landlock 管的是这一层），再看这个系统调用本身允不允许被调用（seccomp 管这一层），最后对网络而言还要看物理上够不够得着目标（netns 管这一层）。

#### Landlock

Landlock 是 Linux 5.13 引入的 LSM（Linux Security Module，内核里一个通用的安全钩子框架）。LSM 本身不实现具体策略，只是在内核关键操作点（打开文件、执行程序、建立网络连接等）插入一批"钩子"，让接进来的安全模块在这些点上做额外的允许/拒绝判断，跑在传统的属主/属组/rwx 权限检查之后，是叠加的一层，不是替代。SELinux、AppArmor 也是接在这个框架上的实现，但它们要管理员预先给整机写好策略；Landlock 反过来，允许一个非特权进程给自己（以及它 fork 出来的子进程）加限制，不需要 root，也不需要管理员配置，而且规则一旦施加，同一进程内只能收紧、不能放宽。这对沙箱场景很关键：即使 Agent 进程本身被攻破，它也没法把已经收紧的规则再改宽。Supervisor 用 Landlock 给 Agent 的文件系统访问建白名单：哪些路径能读、能写、能执行，其余一律拒绝，不依赖 Agent 进程自己守规矩。新版 Landlock（ABI v4 起）已经能管 TCP 连接、v10 起还能管 UDP，但 OpenShell 这里只拿它管文件路径，网络交给下面的 netns，是有意的职责拆分，不是 Landlock 能力不够。

#### 进程降权

进程降权说的是清空 capability bounding set。传统 Unix 权限模型只有 root 和非 root 两档，root 天下无敌；Linux 后来把 root 的这些超能力拆成了几十个可以单独开关的细粒度权限，叫 capability，比如管网络配置的 `CAP_NET_ADMIN`、管挂载文件系统的 `CAP_SYS_ADMIN`、管绕过文件权限检查的 `CAP_DAC_OVERRIDE`。一个进程实际能拿到的 capability，上限由它的 bounding set 决定：就算进程的 UID 显示是 0（root），只要 bounding set 是空的，它就调用不了任何需要 capability 的特权操作，跟普通用户没有区别。前面提到的 fail-closed 清空动作，就是在 spawn Agent 之前把这个 bounding set 清空，清不空就不启动。

#### seccomp

seccomp（secure computing mode）是在系统调用这一层再加一道过滤。能不能读某个文件、能不能拿到某个 capability，判断的是"有没有资格做这件事"；但一个进程真正能干什么，最终都要落到它调用了哪些系统调用（syscall），seccomp 管的就是这一层：用一段 BPF 程序对进程能调用哪些 syscall（以及带什么参数）做白名单，不在名单里的直接在内核入口被拒绝。典型例子是创建 raw socket，这个调用能绕开常规的 socket API 直接组装网络包，用来嗅探或伪造流量；就算前面的权限检查都没堵住，seccomp 也能单独把这个调用挡在外面。它和 capability 管的是两个维度：capability 决定"有没有权限"，seccomp 决定"这个系统调用本身能不能被调用"，就算权限没被拿干净，seccomp 也能兜底挡住。

#### netns

netns（网络命名空间）让 Agent 进程拥有自己独立的网络栈：网卡、路由表、iptables 规则都是独立的一份，不共享宿主机的。Linux 的命名空间机制能把同一台机器上的资源"分身"成互相看不见的多份，网络命名空间分的就是网络设备和路由表这一份。Supervisor 把 Agent 放进一个只有一条内部虚拟网卡（veth pair）通向本机 policy proxy 的 netns 里，这个 netns 里压根没有配置到公网的路由。不是靠防火墙规则去"允许 / 拒绝"某个目标地址（这类规则理论上还可能有漏洞被绕过）：这里是路由表这一层就没有路径可走，Agent 想绕开 proxy 直连外网，在网络层面根本走不通。

四层各管一个维度：Landlock 管文件路径，降权和 seccomp 管进程能拿到什么权限、能调用什么系统调用，netns 管网络物理可达性。单独绕过其中一层，另外几层还是拦得住。

L7 这一层可以对 REST 的 method/path、WebSocket 文本消息、GraphQL 操作，以及 MCP/JSON-RPC 的请求方法（含部分请求体字段）执行策略；但目前 JSON-RPC 的 response 和 MCP server-to-client 的 SSE 消息还不会被完整解析。`https://inference.local` 这个地址会绕过普通的 OPA 网络规则，但仍然要经过本地 TLS、请求识别、凭证剥离和 router；如果 Agent 直连外部模型 URL 而不走 `inference.local`，走的还是普通出站策略。

`inference.local` 不是一个真的能被 DNS 解析出来的域名，而是本机 proxy 代码里写死的一个匹配目标：proxy 处理 CONNECT 请求时专门检查目标是不是 `inference.local:443`，命中就直接回一个 `200 Connection Established`，把这条连接交给专门的拦截路径处理，不走普通出站策略。接下来它用本机的 sandbox CA 就地终止 TLS，识别出常见的 OpenAI/Anthropic 兼容请求格式，剥掉 Agent 自己带的凭证和不该出现的 Header，再经 `router` 转发到真正的模型后端，用的是 Gateway 下发的那份模型路由 bundle（也就是 2.1 节 `policy_store` / `inference` 模块管的那份配置）。Agent 代码里只要把 base URL 填成 `https://inference.local`，不需要知道背后接的是哪家模型、密钥是什么，这些都由 proxy 按策略路由过去。

Agent 调用被策略允许的 API 时，明文密钥不需要进入子进程：凭证存在 Gateway，Supervisor 在运行时拉取，HTTP 请求上先用 `openshell:resolve:env:…` 这样的占位符代替真实值，等 proxy 确认目标和 L7 规则都通过之后，才把占位符换成真实的 Header 或 Query 参数。这只是调用路径上的一层附带控制，不是 Sandbox 存在的理由。Agent 的进程环境里只会出现策略和 provider 配置明确允许的变量，用于回连 Gateway 的 bootstrap JWT 对子进程不可见。静态凭证如果刷新失败，会直接吊销上一份，不会留下半新半旧的一组凭证；动态凭证刷新失败则按对应 provider 自己的快照策略处理。

同一条 outbound session 上还承载着运行期间的持续通信：Supervisor 一侧推日志、策略加载结果（`LOADED` / `FAILED`）、L4 denial 摘要；Gateway 一侧下发配置、凭证、`RelayOpen`。配置轮询失败时会保留 last-known-good 的旧配置，而不是清空；一次不合法的热更新会被整包拒绝，不会部分生效。企业环境的正向代理配置写在 Supervisor 的命令行上：如果 TLS/CONNECT 代理配置无效，会直接 fail-closed；至于明文 HTTP 是否也经过企业代理，取决于当前协议适配器的实现，不要笼统地认为所有出站流量都会经过代理。

## 4. 三条工作流

### 4.1 创建 Sandbox

```text
CLI
  │  CreateSandbox
  v
Gateway
  │  provision
  v
Driver
  │  启动 Sandbox（注入 callback）
  v
Supervisor
  │  ConnectSupervisor
  v
Gateway
  │  Ready
  v
CLI
```

Gateway 先写库、解析策略，再把 spec 交给 Driver；Driver 注入 callback 之后起容器或 Pod；Supervisor 回连并注册 session 之后，Gateway 才对外报告 Ready。本地 Docker 与 K8s 走的是同一套契约：K8s 的 Driver 通过 Agent Sandbox CR 等 Kubernetes 对象，交给 Controller 去创建 Pod。

### 4.2 人看终端：`connect` / `exec`

outbound session 只让 Gateway 知道这个 Sandbox 还在；终端字节走的是另一条按次建立的 relay。

```text
（平时：Supervisor 已通过 ConnectSupervisor 常驻连接 Gateway）

键盘/命令
    │  connect / exec
    v
   CLI
    │  请求
    v
 Gateway
    │  RelayOpen
    v
Supervisor ──拨内部 SSH──┐
    │                    │
    │◄───────────────────┘
    │  RelayStream
    v
 Gateway
    │  PTY / stdout
    v
   CLI
    │  终端输出
    v
键盘/命令
```

交互式场景走的是 PTY，`exec` 则只是命令的 stdout/stderr；Gateway 不解释画面内容，只做转发。`sandbox logs` 是另外一条单独推送的日志流，跟这条 relay 无关。Sandbox 内的 HTTP 服务通过 CLI 支持的 relay/forwarding 能力对外暴露，不需要直接开 NodePort。

### 4.3 Agent 出网

这条路径完全不经过 Gateway，也不经过 CLI，人的终端不在这条路上。

```text
Agent ──► Policy proxy ──► OPA 评估
                              │
                              ├── 允许 ──────────────► 外部 API
                              └── inference.local ──► 模型后端
```

允许通过的请求，凭证在 proxy 里注入；走 `inference.local` 的请求路由到对应的模型后端。

## 5. 其余模块与部署形态

用户面由 `openshell-cli`、`sdk`、`tui`、`bootstrap` 几个组件组成；`openshell-core`、`policy`、`providers`、`router`、`ocsf`、`otel` 是 Gateway 和 Supervisor 共用的库。Compute driver 支持 `docker` / `podman` / `kubernetes` / `vm` 四种；凭证 driver 支持 `vault` / `kubernetes-secrets` / `db-credstore` 三种后端。两种部署：**本机**：Gateway 是工作站进程，Compute 用 Docker / Podman / VM，Supervisor 连本机 Gateway。**Kubernetes**：Gateway 是集群 Service，Compute 走 Agent Sandbox CR → Pod，Supervisor 连 `server.grpcEndpoint`（须 **Pod 内可达**，通常配置为集群内 Service/DNS）。

两种部署下，CLI 操作流程不变：`gateway add` → `sandbox create` → `connect`。Kubernetes 上的 Helm 部署目前仍处于实验性阶段，建议固定住 OpenShell 的 release/tag 或 commit 之后再对照本文阅读。

## 参考与延伸阅读

- [architecture/gateway.md](https://github.com/NVIDIA/OpenShell/blob/main/architecture/gateway.md)：Gateway 鉴权、持久化、session
- [architecture/sandbox.md](https://github.com/NVIDIA/OpenShell/blob/main/architecture/sandbox.md)：Supervisor 隔离、代理、凭证
- [architecture/compute-runtimes.md](https://github.com/NVIDIA/OpenShell/blob/main/architecture/compute-runtimes.md)：Driver 契约与 Ready 状态
- [Issue #1633](https://github.com/NVIDIA/OpenShell/issues/1633)：`inference.local` 拦截机制的实现细节，以及把这个模式推广到任意 host-local 服务的提案
- [How OpenShell Works](https://docs.nvidia.com/openshell/latest/about/how-it-works)：官方概念页
- [NVIDIA/OpenShell](https://github.com/NVIDIA/OpenShell)：源码
