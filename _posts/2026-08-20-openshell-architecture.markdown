---
layout:     post
title:      "AI Agent 安全沙箱 OpenShell"
date:       2026-08-20 10:00:00
author:     "lr90"
header-img-credit: false
tags:
    - Sandbox
---

随着大模型的发展，AI 应用正从对话问答延伸到任务执行。Claude Code、OpenCode 等编码助手，以及 OpenClaw 这类通用 AI 助手，在执行任务时可能都需要运行代码、读写文件和访问外部服务。这些操作会访问本地数据、服务凭证和外部系统，如果缺少必要的权限约束，就可能带来密钥泄露、越权读写文件或未经授权的网络访问。

要控制这些风险，运行环境需要根据任务类型授予必要的权限，并在程序非法访问时及时阻止。沙箱（sandbox）通过隔离执行环境、限制资源访问，为这类控制提供基础设施。

OpenShell 是 NVIDIA 面向这类场景开源的沙箱运行时。开发者指定镜像、启动命令和访问策略，OpenShell 负责创建环境，并在执行过程中限制资源访问。下文以 Linux 沙箱为例，说明这些权限控制如何落实到进程启动、文件操作和网络请求中。

## 1. 整体架构：Gateway 与 Supervisor

OpenShell 通过控制面组件 **Gateway** 管理沙箱。Gateway 向 CLI、SDK 提供接口，保存沙箱状态、安全策略和凭证（secret）配置，并通过 Compute Driver 对接 Docker、Kubernetes 等基础设施。

沙箱内部的 **Supervisor** 负责管理用户进程、执行权限限制，并运行本地网络代理。它与 Gateway 同步策略配置和状态，具体的文件访问限制与网络放行在沙箱侧执行。

Supervisor 启动后会主动连接 Gateway，并保持一条经过认证的控制会话。Gateway 通过这条会话下发配置，CLI 发起的连接、命令执行和文件传输也由它转发。创建沙箱时，底层容器或 Pod 启动后， Supervisor 完成初始化并建立会话后，沙箱才进入 `Ready` 状态。

不同运行环境由 Compute Driver 对接适配。具体可以使用 Docker、Podman 或 VM，集群环境可以使用 Kubernetes。无论底层环境如何，沙箱的创建接口和策略执行模型保持一致。

![OpenShell 的管理路径与业务网络路径](/pics/01-openshell-architecture.png)

*创建接口与策略配置经过 Gateway；用户进程访问外部服务时，由沙箱内的代理检查并转发。*

## 2. 本地安装与创建沙箱

以下以 macOS 和 Docker Desktop 环境为例，在本地部署 OpenShell，并创建一个通用沙箱来演示一下使用和配置过程。OpenShell CLI 和 Gateway 直接运行在 macOS 上，Docker Desktop 为沙箱提供容器运行环境。开始前请确认 Docker Desktop 已经启动。

### 2.1 安装 OpenShell

运行下面的官方脚本来安装 OpenShell CLI 和启动 Gateway 服务。

```shell
curl -LsSf https://raw.githubusercontent.com/NVIDIA/OpenShell/main/install.sh | sh
```

安装脚本会通过 Homebrew 安装 OpenShell CLI 和 Gateway，并将 Gateway 注册为 Homebrew 后台服务。该服务由 macOS 的 `launchd` 管理并直接运行在宿主机上。如果命令成功退出，本地 Gateway 就已经启动了，可以通过下面命令检查连接状态：

```console
$ openshell status

Server Status

  Gateway: openshell
  Server: https://localhost:17670
  Status: Connected
  Authentication: Authenticated (mTLS transport)
  Version: 0.0.116
```

Gateway 启动时会检测 Docker Desktop 并启用 Docker Driver。执行 `openshell sandbox create` 后，Gateway 通过 Docker API 拉取默认镜像、创建沙箱容器，并在其中启动 Supervisor。Supervisor 连接 Gateway 后，沙箱进入就绪状态。

### 2.2 创建一个交互式沙箱

安装完成并确认 Gateway 可以连接后，创建名为 `shell-demo` 的沙箱并进入其登录 Shell：

```shell
openshell sandbox create --name shell-demo
```

Gateway 创建沙箱容器后，Supervisor 加载策略并启动登录 Shell，CLI 随即将当前终端接入沙箱。进入沙箱后，可以像使用普通 Linux 环境一样执行命令。例如，创建并运行一个简单的 Python 脚本：

```console
$ echo 'print("Hello from OpenShell")' > hello.py
$ python hello.py
Hello from OpenShell
```

这两条命令在沙箱工作目录中创建并运行一个 Python 脚本。脚本执行、文件读写以及后续启动的其他程序都会受到沙箱策略约束。除了 Bash、Python 等常用工具，OpenShell 也可以把 Agent 作为沙箱的启动命令。例如：

```shell
openshell sandbox create --name codex-demo -- codex
```

`-- codex` 表示沙箱就绪后启动 Codex，并将它作为沙箱的主进程。OpenShell 根据主进程的运行状态管理沙箱生命周期；Codex 发起的命令执行、文件读写和网络访问同样受沙箱策略约束。这些限制通过安全策略统一配置。

## 3. 配置 OpenShell 中的安全策略

OpenShell 使用 YAML 文件定义沙箱安全策略，再由 Supervisor 负责在沙箱内执行这些安全策略。

### 3.1 基础策略配置示例

下面是一份基础安全策略，该策略配置了沙箱所需的文件访问范围，并允许 `curl` 连接 NVIDIA 官方文档站点：

```yaml
version: 1

filesystem_policy:
  include_workdir: true
  read_only: [/usr, /lib, /proc, /dev/urandom, /app, /etc, /var/log]
  read_write: [/tmp, /dev/null]

landlock:
  compatibility: best_effort

network_policies:
  nvidia_docs:
    name: nvidia-docs
    endpoints:
      - host: docs.nvidia.com
        port: 443
    binaries:
      - path: /usr/bin/curl
```

`filesystem_policy` 配置项将常用系统目录设为只读，允许工作目录和 `/tmp` 写入，未列出的路径不可访问。Supervisor 在启动用户程序前通过 Linux Landlock 施加这些限制。`include_workdir: true` 表示将沙箱工作目录自动加入可写范围。

`network_policies` 将目标地址与获准联网的程序绑定起来：这项配置仅允许 `/usr/bin/curl` 连接 `docs.nvidia.com:443`。该规则只控制网络连接，不检查具体的 HTTP 方法和路径。

### 3.2 网络策略如何生效

当沙箱中的程序发起外部请求时，请求会被强制转发到本地代理（沙箱内运行的 proxy 进程）。Supervisor 识别发起连接的 binary，再结合目标地址和请求规则判断是否放行；没有匹配规则的请求默认拒绝。

![网络请求从进程识别到获准转发](/pics/02-openshell-enforcement.png)

要按 HTTP 方法和路径执行 `request` 规则，代理还需要读取 HTTP 请求。对于启用了 `request` 检查的 HTTPS 端点，OpenShell 使用沙箱的临时 CA 建立信任，由本地代理终止客户端 TLS，读取请求，再通过 TLS 连接上游服务。

这样，网络策略既可以限制哪些程序能够访问哪些地址，也可以进一步限制具体的 HTTP 请求。

## 4. 网络策略示例：只读访问 GitHub API

下面以访问 GitHub API 为例，在运行中的沙箱更新网络规则，并验证更新前后的访问结果。将上一章节中的基础配置保存为 `basic.yaml`文件，并使用该策略创建 `demo` 沙箱：

```shell
openshell sandbox create --name demo --policy basic.yaml --detach
```

`--detach` 让沙箱在后台运行，后续命令通过 `sandbox exec` 从宿主机执行，以便连续验证策略更新前后的结果。文件系统和进程配置会在创建沙箱时固定下来，网络规则可以在运行过程中更新。此时策略只允许 `curl` 访问 NVIDIA 官方文档站点，请求 GitHub API 会被拒绝：

```console
$ openshell sandbox exec -n demo -- curl https://api.github.com/zen
curl: (56) CONNECT tunnel failed, response 403
```

这条错误说明代理拒绝了连接，请求没有发送到 GitHub。将下面的 `github_api` 规则加入 `basic.yaml` 现有的 `network_policies` 中：

```yaml
  github_api:
    name: github-api-readonly
    endpoints:
      - host: api.github.com
        port: 443
        protocol: rest
        enforcement: enforce
        access: read-only
    binaries:
      - path: /usr/bin/curl
```

`endpoints` 限定目标，`binaries` 限定发起请求的程序；`protocol: rest` 开启 HTTP 请求检查，`enforcement: enforce` 表示违规时阻断，`access: read-only` 只允许 GET、HEAD 和 OPTIONS。`policy set` 会替换整份策略，因此更新文件时需要保留 `basic.yaml` 中原有的文件系统、Landlock 和网络配置。完成编辑后，应用更新后的策略：

```console
$ openshell policy set demo --policy basic.yaml --wait

✓ Policy version 2 submitted (hash: 3251220cf714)
✓ Policy version 2 loaded (active version: 2)
```

`--wait` 会等待 Supervisor 确认新策略已经加载，更新网络规则不需要重新创建沙箱。再次执行 GET 请求，可以正常获得响应：

```console
$ openshell sandbox exec -n demo -- curl https://api.github.com/zen
Practicality beats purity.
```

该规则中的“只读”按 HTTP 方法判断，会放行 GET、HEAD 和 OPTIONS 请求，不分析远端接口的业务语义。OpenShell 的网络策略会同时检查调用程序、目标地址和具体请求。

## 5. 总结

OpenShell 将沙箱管理与运行时控制分开：Gateway 负责创建环境、保存配置和管理状态，Supervisor 在沙箱内部启动用户程序并执行安全策略。不同 Compute Driver 可以对接 Docker、Kubernetes 等基础设施，而上层使用方式保持一致。

对 AI Agent 而言，这套机制提供了隔离、受控的执行环境，同时将文件访问和外部网络请求限制在明确的策略范围内。在此基础上，平台还可以进一步优化沙箱的交付方式、启动效率和生命周期管理。

## 6. 参考资料

- [NVIDIA/OpenShell GitHub 仓库](https://github.com/NVIDIA/OpenShell)
- [OpenShell 官方文档：产品概览](https://docs.nvidia.com/openshell/about/overview)
- [OpenShell 官方文档：工作原理](https://docs.nvidia.com/openshell/about/how-it-works)
- [OpenShell 官方文档：安装指南](https://docs.nvidia.com/openshell/latest/about/installation)
- [OpenShell 官方教程：配置第一个沙箱网络策略](https://docs.nvidia.com/openshell/get-started/tutorials/first-network-policy)
- [OpenShell 官方文档：Policy Schema Reference](https://docs.nvidia.com/openshell/reference/policy-schema)
