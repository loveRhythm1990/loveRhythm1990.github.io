---
layout:     post
title:      "Knative：Kubernetes 上的 Serverless"
date:       2025-05-21 10:10:00
author:     "decent"
header-img-credit: false
tags:
    - K8s生态
---

## 一、为什么需要 Kubernetes Serverless

Kubernetes 擅长编排容器，但「按请求伸缩、空闲缩到零、修订版流量切分」并不是开箱能力。典型对比：

| 诉求 | 原生 K8s（Deployment + Service + HPA + Ingress） | Knative Serving |
|------|--------------------------------------------------|-----------------|
| 声明工作负载 | Deployment + Service + Ingress 多份 YAML | 一个 `Service` CR |
| 空闲缩到 0 | 默认不行；`minReplicas=0` 也需额外方案且难保请求不丢 | 默认支持 scale-to-zero |
| 扩缩依据 | 多为 CPU / Memory | 默认按并发 / RPS（KPA） |
| 冷启动不丢请求 | 需自建队列或依赖 Ingress 超时 | Activator 缓冲请求并唤醒 Pod |
| 金丝雀 / 蓝绿 | 需 Ingress / Service Mesh 额外配置 | Route 按百分比切 Revision |

**Knative 是什么**：用一组 CRD 与控制面组件，在 Kubernetes 上提供 serverless 运行时。三大块可独立使用：

| 组件 | 职责 | 典型场景 |
|------|------|----------|
| **Serving** | HTTP 驱动的无状态服务：部署、路由、自动扩缩（含缩到零） | API、推理网关、短生命周期 Worker |
| **Eventing** | 基于 CloudEvents 的异步事件路由 | 事件驱动流水线、解耦生产者/消费者 |
| **Functions** | `func` CLI，少写 Dockerfile 的函数开发体验 | 快速原型；底层仍部署为 Serving Service |

### Knative 整体架构

控制面组件跑在 `knative-serving` / `knative-eventing` 等命名空间，多服务共享；数据面则是每个 Revision 的 Pod（用户容器 + `queue-proxy` sidecar）。网络层可插拔（本文实验用 **Kourier**）。图来自 [Serving Architecture](https://knative.dev/docs/serving/architecture/)。

![Knative Serving 架构](/img/in-post/knative/serving-architecture.png)

粗箭头是流量，细箭头是控制：

1. 提交/变更 ksvc → **Webhook** 校验并填默认值 → etcd。**Controller** watch CR，创建/更新 Configuration、Route、Revision 及其底层 Deployment / PA / SKS。
2. 请求进 **Ingress Gateway**。无容量（含 0 副本）走 **Proxy**：Gateway → **Activator** 缓冲，并 poke **Autoscaler** 从 0 拉起，Pod Ready 后再转发。有余量走 **Serve**：Gateway 直打 Pod 里的 **Queue-Proxy**，不再经过 Activator。
3. **Autoscaler** 按 queue-proxy（及 Proxy 模式下 Activator）的 in-flight / RPS 改 `Deployment.replicas`。
4. **Queue-Proxy** 始终在数据面：计量、强制 `containerConcurrency`、把请求转到 **User-Container**。

第三节逐步展开这条数据面。`DomainMapping` 给已有 Route 额外绑域名，实验可以忽略。

网络层不绑死某一家 Ingress。Route reconcile 出内部 **Knative Ingress（KIngress）**；`net-kourier` / `net-istio` 等 watch 它，去配置真正的网关。集群外 DNS 指到网关前面的 Kubernetes Service：

![Serving 与 Ingress 网络层](/img/in-post/knative/serving-architecture-ingress.png)

下文以 **Serving** 为主（serverless 的核心），Eventing 在文末简要串联。

---

## 二、Serving 核心对象

创建顶层资源 `Service`（`serving.knative.dev/v1`，常简称 **ksvc**）后，控制面会自动维护其余 Serving CR：

```text
Service (ksvc)
├── Configuration ──每次变更──► Revision（不可变快照）
└── Route ────────按百分比导流──► Revision
```

| 对象 | 含义 |
|------|------|
| **Service** | 生命周期入口；保证有 Route + Configuration，并随更新产生新 Revision |
| **Configuration** | 期望状态（镜像、环境变量、资源等）；变更 → 新 Revision |
| **Revision** | 代码与配置的不可变快照；Autoscaler 扩缩的是它对应的 Pod。默认名 `{Configuration 名}-{generation，五位补零}`（如 `hello-00001`）；也可在 `spec.template.metadata.name` 自定 |
| **Route** | 对外 URL 与流量策略（可把流量按比例分给多个 Revision） |

日常只提交 **Service** YAML；Configuration、Revision、Route 由控制面 reconcile，调试时再 `kubectl get configuration,revision,route`。

最小 YAML 示例：

```yaml
apiVersion: serving.knative.dev/v1
kind: Service
metadata:
  name: hello
spec:
  template:
    spec:
      containers:
        - image: ghcr.io/knative/helloworld-go:latest
          ports:
            - containerPort: 8080
          env:
            - name: TARGET
              value: "World"
```

### Service 关键配置

日常只改 **Service**。写错层会踩坑：

| 写在哪 | 改了会怎样 |
|--------|------------|
| `spec.template.spec` / `spec.template.metadata.annotations` | 出**新 Revision** |
| `spec.traffic` | 只改 Route 切流，**不**换镜像 |
| Service 顶层 `metadata.annotations` | 一般只影响暴露，不必然出新 Revision |

扩缩 annotation **必须写在 template 上**，写在 Service 顶层不会随新 Revision 生效。正文后面只用得到 `metric` / `target` / `min-scale` / `max-scale`；`timeoutSeconds`、`containerConcurrency`、全部 annotation 见 [文末附录](#十附录service-配置速查)。

容器按普通 Pod 写（`image` / `ports` / `env` / `resources`）；queue-proxy 由控制器注入，YAML 里不要写。切流见第五节。

### Service CR 与 Kubernetes 资源对应

一份 ksvc **不是**只变成一个 Deployment。用户 API 是下面这张对象模型（[Serving Overview](https://knative.dev/docs/serving/)）；控制面 / 数据面怎么跑见第一节官方架构图。

#### 对象模型：你提交的 YAML 变成哪几个 CR

![Serving 资源如何互相协作](/img/in-post/knative/object_model.png)

`Service` 是编排器：创建并一直看着同名的 `Route` + `Configuration`。`Configuration` 保存期望状态（镜像、env、并发等）；每次改它就多一个不可变 `Revision`（图里叠着的黄块）。`Route` 把 URL 指到一个或多个 Revision：默认 100% latest ready，也可以按百分比切流（第五节）。

这四件套是 `serving.knative.dev` 用户 API。Revision 就绪之后，**controller 才会**再派生 Kubernetes 对象：每个 Revision 一份 `Deployment`（Pod = queue-proxy + user-container）、`PodAutoscaler`、`ServerlessService`（再拆出 public/private `Service`）。这些内部对象不出现在对象模型图里，但 `kubectl get` 能看到。

按「创建一个 ksvc、一个 Revision 就绪」粗算，用户命名空间里常见对象量级如下（名称随 Revision 变化，可用 `kubectl get` 核对）：

| 层级 | 资源 | 典型数量（单 Revision） | 说明 |
|------|------|-------------------------|------|
| Serving CR | Service / Configuration / Route | 各 1 | 与 ksvc 同名 |
| Serving CR | Revision | ≥1 | 每次变更 +1，旧的可保留 |
| 扩缩内部 CR | PodAutoscaler、Metric、ServerlessService | 各 1 / Revision | `*.internal.knative.dev` |
| 核心 K8s | Deployment | 1 / Revision | 由 Revision 创建 |
| 核心 K8s | Pod | 0…N | 含 2 容器：queue-proxy + 业务 |
| 核心 K8s | Service | 2 / Revision | SKS 的 public + private |
| 核心 K8s | Endpoints / EndpointSlice | 随 Service | proxy 模式时 public 可指到 Activator |
| 网络 | Knative Ingress + net-* 产物 | ≥1 套 / Route | 集群级 Activator/Autoscaler **不**按服务复制 |

集群级（安装 Serving 时已有，非每个 ksvc 新建）：`activator`、`autoscaler`、`controller`、`webhook`，以及所选网络层的网关 Pod。

---

## 三、请求路径与 Scale-to-Zero（端到端原理）

理解 Knative 的关键，不是「会不会缩到零」，而是**零副本时第一个请求如何不丢、高并发时如何去掉多余跳数**。

共享控制面组件（集群级，多服务共用）：

- **Ingress / HTTP Router**：Kourier、Istio、Contour 等可插拔网络层
- **Activator**：零流量或低流量时的请求缓冲与唤醒入口
- **Autoscaler（KPA）**：按并发 / RPS 调整副本数

每个应用 Pod 内还有 **queue-proxy** sidecar（始终在请求路径上）。

### 3.1 从零唤醒（cold start）

```text
Client
  |
  v
Ingress（Kourier / Istio / Contour）
  |  该 Revision 无可用容量（含 0 副本）
  v
Activator  ----信号---->  Autoscaler(KPA)  ----调副本---->  Kubernetes Deployment
  |                         |
  | 缓冲 HTTP 请求            | 拉起 Pod
  |                         v
  |                    Pod Ready
  |                         |
  +--------转发------------->+
                            |
                            v
                   queue-proxy sidecar
                            |
                            v
                   用户容器（业务进程）
```

步骤：

1. 请求进入 Ingress；Route 已选定某个 Revision。
2. 低流量 / 零副本时，Ingress 把流量指到 **Activator**（Revision 对应的 Endpoints 可写成 Activator，而非用户 Pod）。
3. Activator **排队**请求，并通知 Autoscaler「需要容量」。
4. Autoscaler 提高该 Revision 的期望副本；新 Pod 就绪后，Activator 把排队请求转发出去。
5. 客户端侧通常只表现为**延迟升高（冷启动）**，而不是连接被直接拒绝。

冷启动耗时 ≈ 调度 + 拉镜像 + 容器启动 + readiness。镜像越大、启动越重，体感越明显；生产上常用 `min-scale`、预热、更小镜像缓解。

### 3.2 高流量路径（Activator 旁路）

当空闲容量足够（与 `target-burst-capacity`、并发目标等相关）时，Ingress 被编程为**直接指向用户 Pod**，Activator 退出数据面，降低延迟：

```text
Client --> Ingress --> queue-proxy --> 用户容器
                 （不再经过 Activator）
```

流量回落、容量不足时，Ingress 会再次把流量切回 Activator，以便缓冲突发并触发扩容。

### 3.3 Queue-Proxy 做什么

| 能力 | 说明 |
|------|------|
| 并发 / RPS 度量 | 上报给 Autoscaler（Activator 旁路后仍依赖它） |
| 硬并发上限 | `containerConcurrency`：超出则在 sidecar 排队 |
| 优雅退出 | 拒绝新请求、继续完成 in-flight 请求 |
| 探测加速 | 比 kubelet 更积极地探测用户容器就绪，缩短冷启动可服务时间 |

---

## 四、自动扩缩：KPA 要点

默认使用 **Knative Pod Autoscaler（KPA）**，可缩到零；也可选装 Kubernetes HPA 类，但 **HPA 路径不支持 scale-to-zero**。

| 维度 | KPA | HPA（Serving 可选扩展） |
|------|-----|-------------------------|
| 缩到零 | 支持 | 不支持 |
| 指标 | concurrency / rps | CPU / Memory 等 |
| Panic 模式 | 有（短窗口快速扩） | 无同等语义 |

常用修订 annotation（写在 `spec.template.metadata.annotations`，才会随新 Revision 生效）：

```yaml
apiVersion: serving.knative.dev/v1
kind: Service
metadata:
  name: my-api
spec:
  template:
    metadata:
      annotations:
        autoscaling.knative.dev/class: "kpa.autoscaling.knative.dev"
        autoscaling.knative.dev/metric: "concurrency"
        autoscaling.knative.dev/target: "10"          # 软目标：每 Pod 目标并发
        autoscaling.knative.dev/min-scale: "0"
        autoscaling.knative.dev/max-scale: "20"
        autoscaling.knative.dev/window: "60s"         # stable 窗口
    spec:
      containerConcurrency: 0   # 0 表示不设硬上限；设正整数则由 queue-proxy 强制
      containers:
        - image: ghcr.io/knative/helloworld-go:latest
          ports:
            - containerPort: 8080
```

- **Soft target**（`autoscaling.knative.dev/target`）：希望每 Pod 长期平均接近的并发，可短暂超过。
- **Hard limit**（`containerConcurrency`）：queue-proxy 强制上限；`0` 表示不限制。过低会排队、放大冷启动。
- **Stable / Panic**：稳定窗口默认约 60s；流量远超容量时进入约 6s 窗口快速扩容，期间通常不缩容。
- **scale-to-zero**：稳定窗口内无流量后副本可到 0，再来请求走第三节路径。

完整表见 [文末附录](#十附录service-配置速查)。

对「同步等 LLM / 外部 API」类 Agent 网关，CPU 往往闲着而连接占着——按**并发**扩缩通常比按 CPU 更合理。这也是 KPA 相对经典 HPA 的常见选型理由。

### 4.1 queue-proxy 在扩缩链路中的位置

KPA **不**读 Pod 的 CPU；它读的是「每个 Revision 当前有多少 in-flight 请求」。这些数字几乎全部由 **queue-proxy** 产生——`queue-proxy` 是 Knative **自动注入** 的 sidecar 容器（固定名 `queue-proxy`），与业务容器共享网络命名空间，跑在同一个 Pod 里。**所有进入该 Pod 的 HTTP 流量必须先经过 queue-proxy**，再转发到用户容器。

```text
Client
  |
  v
queue-proxy  <---- autoscaler scrape :9090
  |                    |
  v                    +---- 改 replicas ----> Deployment
user-container

activator  ----WebSocket 推送 Stat---->  autoscaler
（仅 Proxy 模式）
```

| 角色 | 谁在做 |
|------|--------|
| **计量** | queue-proxy 统计 **本 Pod** in-flight 请求数、请求计数；Activator 在 Proxy 模式下统计经其转发的 in-flight 请求 |
| **暴露 / 上报** | queue-proxy **不主动找 autoscaler**；在 `:9090/metrics` 暴露 Protobuf 指标，由 autoscaler **定时拉取（scrape）**。Activator 在 Proxy 模式下通过 **WebSocket 主动推送** Stat 给 autoscaler |
| **聚合** | autoscaler 对每个 Revision 采样多个 Pod 的 queue-proxy 指标并**加总/外推**，得到 Revision 级 in-flight 并发或 RPS；与 Activator 指标合并 |
| **决策** | KPA 在 stable / panic 窗口内平滑上述 Revision 级指标，计算期望副本数 |
| **执行** | KPA 更新 `PodAutoscaler`，并改对应 `Deployment` 的 `replicas` |
| **硬限流** | 若设置了 `containerConcurrency`，超出部分在 queue-proxy **本地排队**，不会压垮用户容器 |

因此：**客户端发请求 → 请求在 queue-proxy 里算 in-flight → autoscaler 看见压力 → 加副本**。请求结束（连接关闭、handler 返回）后，in-flight 数下降，稳定窗口过后副本才会缩回去。

#### 业务 Pod 示例：你写的 vs 集群里实际的

ksvc 里只声明业务容器（同第二节）；Revision 控制器生成 Pod 时追加 `queue-proxy`，该 sidecar **不会**出现在你提交的 YAML 里。

**reconcile 后单个 Pod 的结构（概念示意）**

Pod 名类似 `hello-00001-deployment-7d4f8b9c6-xk2lm`，由 Deployment `hello-00001-deployment` 创建：

```yaml
apiVersion: v1
kind: Pod
metadata:
  name: hello-00001-deployment-7d4f8b9c6-xk2lm
  labels:
    serving.knative.dev/service: hello
    serving.knative.dev/revision: hello-00001
spec:
  containers:
    # ① Knative 注入的 sidecar（扩缩计量 + 转发 + 限流）
    - name: queue-proxy
      image: gcr.io/knative-releases/knative.dev/serving/cmd/queue@sha256:…
      ports:
        - name: queue-port          # 对外接收入站 HTTP，默认 8012
          containerPort: 8012
        - name: http-autometric     # 给 autoscaler 抓指标的端口
          containerPort: 9090
        - name: http-usermetric
          containerPort: 9091
      env:
        - name: SERVING_NAMESPACE
          value: default
        - name: SERVING_SERVICE
          value: hello
        - name: SERVING_REVISION
          value: hello-00001
        - name: CONTAINER_CONCURRENCY
          value: "0"
        - name: USER_PORT
          value: "8080"             # 转发目标：业务容器端口
      # readiness 由 queue-proxy 代理，以便更快进入 Ready

    # ② 你的业务容器（未写 name 时默认 user-container）
    - name: user-container
      image: ghcr.io/knative/helloworld-go:latest
      ports:
        - name: user-port
          containerPort: 8080       # 通常不对外暴露 Service，仅 loopback 给 queue-proxy
      env:
        - name: TARGET
          value: World
      # 用户容器不直接接集群 Service 流量
```

**请求路径（Serve 模式，Activator 已旁路）**

```
Client → Ingress/Kourier → Service hello-00001 → Pod:8012 (queue-proxy)
                                              → 127.0.0.1:8080 (user-container)
```

| 容器 | 谁声明 | 对外接流量？ | 主要职责 |
|------|--------|--------------|----------|
| `queue-proxy` | Knative 自动注入 | **是**（Pod 的 Service 指向它） | 转发、in-flight 计数、上报 Stat、硬并发排队 |
| `user-container` | 你在 ksvc 里写 | 否（本机回环访问） | 运行业务逻辑 |

若你在 `spec.template.spec.containers` 里**再添加自己的 sidecar**（如日志、mesh），Pod 会变成 `queue-proxy` + `user-container` + 你的 sidecar；**queue-proxy 仍会注入且仍是 HTTP 入口**，扩缩指标仍来自 queue-proxy 对 in-flight 请求的统计。

**本地查看真实 Pod**

```bash
# 容器列表（常见为 2/2 Ready：queue-proxy + user-container）
kubectl get pods -l serving.knative.dev/service=hello \
  -o custom-columns=NAME:.metadata.name,READY:.status.containerStatuses[*].ready

kubectl get pod -l serving.knative.dev/service=hello \
  -o jsonpath='{range .items[0].status.containerStatuses[*]}{.name}{"\t"}{.ready}{"\n"}{end}'
# queue-proxy    true
# user-container true

# 看完整 spec（镜像版本、端口、env 以集群为准）
kubectl get pod -l serving.knative.dev/service=hello -o yaml | less
```

### 4.2 期望副本（concurrency）

默认指标是 **concurrency**（in-flight 请求）。软目标为 `autoscaling.knative.dev/target`（示例常用 `10`；集群默认约 `100`，见 `config-autoscaler` 的 `container-concurrency-target-default`）。

稳定态、非 Panic：

```
期望副本 ≈ ceil( Revision 级平均 in-flight 并发 / 每 Pod 目标并发 )
```

50 个 in-flight、`target=10` → 约 **5** 个 Pod。`autoscaling.knative.dev/metric: rps` 时按每秒请求数决策，适合短请求、高 QPS。

### 4.3 指标如何到 autoscaler，如何合成副本数

queue-proxy **不主动上报**。它在本 Pod 统计 in-flight 并发与 RPS，在 `:9090`（`http-autometric`）暴露 `GET /metrics`（Protobuf），约每 2s 刷新快照。env 里没有 autoscaler 地址，只有 `SERVING_REVISION`、`USER_PORT` 等身份与转发配置。

autoscaler 是拉取方：Revision 会生成 `Metric/<revision-name>`，scraper 经 Kubernetes API 拿到 Pod IP，约每 **1s** 请求：

```http
GET http://<pod-ip>:9090/metrics
Accept: application/protobuf
```

低流量 / 零副本时请求走 Activator（Proxy 模式），Activator 用 **WebSocket 推送** Stat；这是 Activator 的路径，不是 queue-proxy 推送。

```text
activator  ----WebSocket 推送---->  autoscaler
                                         |
                      HTTP scrape :9090  |
                             /           |
                            v            v
                    queue-proxy Pod1   queue-proxy Pod2
                                         |
                                         v
                                    KPA 算法
                                         |
                                         v
                               Deployment replicas
```

每个 `Stat` 带 `PodName`，含 `AverageConcurrentRequests`（in-flight 均值）和 `RequestCount`（RPS）。scraper **随机采样**部分 Pod，相加后再按总数外推，得到 Revision 级 `observedStableValue` / `observedPanicValue`（约 60s / 6s 窗口）。

稳定态期望副本：

```
dspc ≈ ceil( Revision 级观测并发 / 每 Pod 目标并发 )
```

再乘 target-utilization（默认约 70%），并受 `min-scale` / `max-scale`、max-scale-up-rate 约束。若 `观测并发 / Ready Pod 数 ≥ panic 阈值`（默认 200% target），进入 panic：用短窗口算 `dppc`，取与 stable 中更大者，且通常不缩容。

最终 `desiredPodCount` 写入 `PodAutoscaler.status`，reconciler 改 `Deployment/<revision>-deployment` 的 `spec.replicas`。

| 客户端 | target | 聚合后 in-flight 并发 | 期望副本 |
|--------|--------|----------------|----------|
| `hey -c 50`，每请求 `sleep=500ms` | 10 | ≈ 50 | `ceil(50/10)=5` |

压测停止后，in-flight 数下降，stable 窗口内均值归零，副本回落，最终可到 0。观察：

```bash
kubectl logs -n knative-serving deploy/autoscaler --tail=30 | rg -i 'PANIC|Scale|PodCount|scrape'

kubectl get podautoscaler -l serving.knative.dev/service=autoscale-go \
  -o custom-columns=NAME:.metadata.name,DESIRED:.status.desiredScale,ACTUAL:.status.actualScale

kubectl get deploy -l serving.knative.dev/service=autoscale-go
```

---

## 五、流量切分与 Revision

Route 可把同一 URL 的流量按百分比分到多个 Revision（金丝雀 / 蓝绿）：

```yaml
apiVersion: serving.knative.dev/v1
kind: Service
metadata:
  name: hello
spec:
  template:
    metadata:
      name: hello-v2   # 可选：作为即将创建的 Revision.metadata.name
    spec:
      containers:
        - image: ghcr.io/knative/helloworld-go:latest
          env:
            - name: TARGET
              value: "Knative"
  traffic:
    - revisionName: hello-00001   # 已有 Revision 的 metadata.name
      percent: 90
    - revisionName: hello-v2      # 与 template.metadata.name 对应；有 revisionName 时勿再设 latestRevision: true
      percent: 10
```

指向「当前 latest」时用 `latestRevision: true`，且该条目不能同时写 `revisionName`。

`hello-00001`、`hello-00002` 这类名字是 **Knative 控制面起的**，不是 Kubernetes 对普通资源的默认规则。未写 `spec.template.metadata.name` 时：

```text
Revision.metadata.name = {Configuration 名}-{generation，五位补零}
```

Service `hello` 对应同名 Configuration，第一次模板是 `hello-00001`，再变一次是 `hello-00002`。`kubectl get revision` 里的 `GENERATION` 就是这个序号。若像上面那样指定 `template.metadata.name: hello-v2`，即将创建的那一版会叫 `hello-v2`（须在命名空间内唯一），而不是继续 `hello-00003`。

---

## 六、本地端到端实验（Mac M2 / Apple Silicon）

在 kind 上用官方 YAML 安装 Serving + Kourier，再 `kubectl apply` 一个 Service：观察缩到零、冷启动唤醒、Revision 与按并发扩缩。

前置：Docker 已运行；建议给集群约 **3 CPU / 4 GB RAM**（官方单节点建议更高）。`brew install kind kubectl`。YAML 清单从 GitHub Releases 拉取，**Serving 与 net-kourier 用同一个 `knative-vX.Y.Z` tag**（见 [Serving Releases](https://github.com/knative/serving/releases)、[net-kourier Releases](https://github.com/knative-extensions/net-kourier/releases)，或直接抄 [官方 YAML 安装页](https://knative.dev/docs/install/yaml-install/serving/install-serving-with-yaml/) 里的 tag）。

### 6.1 建集群并安装 Serving

kind 节点跑在 Docker 里，本机默认打不到集群 Service。创建时把**本机 8080** 映射到节点 **31080**（稍后作为 Kourier 的 NodePort）：

```bash
kind create cluster --config /dev/stdin <<EOF
kind: Cluster
apiVersion: kind.x-k8s.io/v1alpha4
name: knative
nodes:
  - role: control-plane
    extraPortMappings:
      - containerPort: 31080
        hostPort: 8080
        protocol: TCP
EOF
```

安装顺序：CRD → Serving 核心 → 网络层 → 指定 ingress class → DNS 后缀。

```bash
# 与官方 YAML 安装页 / GitHub Releases 上的稳定 tag 对齐，例如 knative-v1.x.y
export KNATIVE_RELEASE=knative-vX.Y.Z

# serving-crds.yaml：注册 Serving CRD（只定义 API，不跑进程）
#   用户侧 serving.knative.dev：
#     Service / Configuration / Revision / Route / DomainMapping
#   内部 autoscaling.internal.knative.dev：
#     PodAutoscaler / Metric
#   内部 networking.internal.knative.dev：
#     Ingress / ServerlessService / Certificate / ClusterDomainClaim
#   内部 caching.internal.knative.dev：
#     Image
kubectl apply -f https://github.com/knative/serving/releases/download/${KNATIVE_RELEASE}/serving-crds.yaml

# serving-core.yaml：控制面工作负载 + 默认配置（命名空间 knative-serving）
#   Namespace: knative-serving
#   Deployment/Service：
#     controller  — reconcile ksvc → Configuration/Revision/Route
#     activator   — 零副本时缓冲请求并唤醒
#     autoscaler  — KPA，按并发/RPS 改 replicas
#     webhook     — 校验/默认值（Mutating + Validating）
#   ConfigMap（常用）：config-network、config-domain、config-autoscaler、
#     config-defaults、config-deployment、config-features、config-gc 等
#   另含 RBAC、HPA（activator/webhook）、webhook 证书 Secret
# webhook 未就绪时可能报错，等几秒再执行同一条
kubectl apply -f https://github.com/knative/serving/releases/download/${KNATIVE_RELEASE}/serving-core.yaml

kubectl wait --for=condition=Available deploy --all -n knative-serving --timeout=180s

# kourier.yaml：可插拔网络层（Envoy 网关 + Knative Ingress 控制器）
#   Namespace: kourier-system
#   Deployment knative-serving/net-kourier-controller  — 把 Knative Ingress 编成 Envoy 配置
#   Deployment kourier-system/3scale-kourier-gateway   — Envoy 数据面
#   Service kourier-system/kourier           — 集群外入口（清单里默认 LoadBalancer）
#   Service kourier-system/kourier-internal  — 集群内入口
kubectl apply -f https://github.com/knative-extensions/net-kourier/releases/download/${KNATIVE_RELEASE}/kourier.yaml
kubectl wait --for=condition=Available deploy --all -n knative-serving --timeout=180s
kubectl wait --for=condition=Available deploy --all -n kourier-system --timeout=180s

# kind 没有云 LoadBalancer。改成 NodePort，且必须等于 extraPortMappings.containerPort
kubectl patch svc kourier -n kourier-system --type merge -p '{"spec":{"type":"NodePort"}}'
kubectl patch svc kourier -n kourier-system --type json -p '[
  {"op":"replace","path":"/spec/ports/0/nodePort","value":31080}
]'

# 写入 config-network：默认 ingress class 设为 Kourier（否则 Route 不知道流量从哪进）
kubectl patch configmap/config-network \
  --namespace knative-serving \
  --type merge \
  --patch '{"data":{"ingress-class":"kourier.ingress.networking.knative.dev"}}'

# 写入 config-domain：ksvc URL 后缀。kind 上无可用云 LoadBalancer，不用 serving-default-domain Job
kubectl patch configmap/config-domain \
  --namespace knative-serving \
  --type merge \
  --patch '{"data":{"127.0.0.1.sslip.io":""}}'
```

```bash
kubectl get pods -n knative-serving
kubectl get pods -n kourier-system


lr90@xbook ~ % kubectl get pods -n knative-serving
kubectl get pods -n kourier-system
NAME                                      READY   STATUS    RESTARTS   AGE
activator-67cbcccdfb-k4zgj                1/1     Running   0          71s
autoscaler-847d45f498-cvclr               1/1     Running   0          71s
controller-c9767f9c-hwbzp                 1/1     Running   0          71s
net-kourier-controller-769cd4d84c-pk6l6   1/1     Running   0          62s
webhook-7d56465b69-9gdh2                  1/1     Running   0          71s
NAME                                     READY   STATUS    RESTARTS   AGE
3scale-kourier-gateway-d95dfffb6-kwqcf   1/1     Running   0          62s
```

这些是集群级共享组件，不是每个 ksvc 一份。

### 6.2 部署第一个 Service

```bash
cat <<EOF | kubectl apply -f -
apiVersion: serving.knative.dev/v1
kind: Service
metadata:
  name: hello
spec:
  template:
    spec:
      containers:
        - image: ghcr.io/knative/helloworld-go:latest
          ports:
            - containerPort: 8080
          env:
            - name: TARGET
              value: "World"
EOF

kubectl get ksvc hello -w

NAME    URL                                       LATESTCREATED   LATESTREADY   READY     REASON
hello   http://hello.default.127.0.0.1.sslip.io   hello-00001                   Unknown   RevisionMissing
hello   http://hello.default.127.0.0.1.sslip.io   hello-00001     hello-00001   Unknown   RevisionMissing
hello   http://hello.default.127.0.0.1.sslip.io   hello-00001     hello-00001   Unknown   IngressNotConfigured
hello   http://hello.default.127.0.0.1.sslip.io   hello-00001     hello-00001   Unknown   Uninitialized
hello   http://hello.default.127.0.0.1.sslip.io   hello-00001     hello-00001   True
```

ksvc URL 按 80 写出；本机入口是映射后的 **8080**。不要用会走 HTTP 代理的主机名直连：`hello.default.127.0.0.1.sslip.io` **不会**命中 `no_proxy=127.0.0.1`，本机 `http_proxy` 会把请求拦掉（常见表现：无输出、403、或连不上）。直打 loopback 并带 Host：

```bash
curl -sS --max-time 30 \
  -H "Host: hello.default.127.0.0.1.sslip.io" \
  http://127.0.0.1:8080
# Hello World!
```

### 6.3 观察端到端生命周期

```bash
# Serving CR 关系
kubectl get ksvc,configuration,revision,route

lr90@xbook ~ % kubectl get ksvc,configuration,revision,route
NAME                                URL                                       LATESTCREATED   LATESTREADY   READY   REASON
service.serving.knative.dev/hello   http://hello.default.127.0.0.1.sslip.io   hello-00001     hello-00001   True

NAME                                      LATESTCREATED   LATESTREADY   READY   REASON
configuration.serving.knative.dev/hello   hello-00001     hello-00001   True

NAME                                       CONFIG NAME   GENERATION   READY   REASON   ACTUAL REPLICAS   DESIRED REPLICAS
revision.serving.knative.dev/hello-00001   hello         1            True             1                 1

NAME                              URL                                       READY   REASON
route.serving.knative.dev/hello   http://hello.default.127.0.0.1.sslip.io   True
```

```bash
# 底层 Deployment / Pod（Revision 名含后缀如 hello-00001）
# 等空闲一段时间后，副本应变为 0（默认稳定窗口约 60s 量级，可多等一会）
kubectl get deploy,pods -l serving.knative.dev/service=hello
lr90@xbook ~ % kubectl get deploy,pods -l serving.knative.dev/service=hello
NAME                                     READY   UP-TO-DATE   AVAILABLE   AGE
deployment.apps/hello-00001-deployment   0/0     0            0           6m23s

NAME                                          READY   STATUS        RESTARTS   AGE
pod/hello-00001-deployment-59777955f8-w7t2c   1/2     Terminating   0          96s
lr90@xbook ~ % kubectl get deploy,pods -l serving.knative.dev/service=hello
NAME                                     READY   UP-TO-DATE   AVAILABLE   AGE
deployment.apps/hello-00001-deployment   0/0     0            0           6m32s

```

缩到 0 后发请求，同时另开终端观察 Pod：

```bash
# 终端 A：watch
kubectl get pods -l serving.knative.dev/service=hello -w

lr90@xbook ~ % kubectl get pods -l serving.knative.dev/service=hello -w
NAME                                      READY   STATUS    RESTARTS   AGE
hello-00001-deployment-59777955f8-7pzj4   0/2     Pending   0          0s
hello-00001-deployment-59777955f8-7pzj4   0/2     Pending   0          0s
hello-00001-deployment-59777955f8-7pzj4   0/2     ContainerCreating   0          0s
hello-00001-deployment-59777955f8-7pzj4   0/2     ContainerCreating   0          1s
hello-00001-deployment-59777955f8-7pzj4   1/2     Running             0          1s
hello-00001-deployment-59777955f8-7pzj4   2/2     Running             0          2s
hello-00001-deployment-59777955f8-7pzj4   2/2     Running             0          3s

# 终端 B：触发冷启动
time curl -sS --max-time 30 \
  -H "Host: hello.default.127.0.0.1.sslip.io" \
  http://127.0.0.1:8080
```

你会看到：短暂无 Pod → Pod 创建 Running → curl 返回；`time` 里多出来的就是冷启动。

再看 Serving 侧状态：

```bash
kubectl describe ksvc hello

kubectl get revision -l serving.knative.dev/service=hello
lr90@xbook ~ % kubectl get revision -l serving.knative.dev/service=hello
NAME          CONFIG NAME   GENERATION   READY   REASON   ACTUAL REPLICAS   DESIRED REPLICAS
hello-00001   hello         1            True             1                 1

kubectl get ksvc hello -o jsonpath='{.status.url}{"\n"}{.status.latestReadyRevisionName}{"\n"}'

http://hello.default.127.0.0.1.sslip.io
hello-00001
```

### 6.4 更新与流量切分

再次 apply 同一 Service（改 env / 镜像）会创建新 Revision；默认 Route 将 100% 切到 latest。

把 `TARGET` 改为 `Knative` 后再 apply 一次：

`kubectl apply` 在这里和 Kubernetes 里一样，是 **update 名为 `hello` 的那一个 Service**（同一 `metadata.name`，原地改 spec）。被更新的是 ksvc / Configuration 的**期望模板**，不是 Revision。

Revision 是控制面看到模板变了之后**另外创建**的对象（`hello-00002`），类似 Deployment 更新时不会改掉旧 ReplicaSet、而是再挂一个新 RS。旧 `hello-00001` 仍是独立 CR，所以不会被这次 apply 删掉。若 apply 的是 ConfigMap 这类没有「版本快照」的对象，才会表现为字段被直接覆盖。

```bash
cat <<EOF | kubectl apply -f -
apiVersion: serving.knative.dev/v1
kind: Service
metadata:
  name: hello
spec:
  template:
    spec:
      containers:
        - image: ghcr.io/knative/helloworld-go:latest
          ports:
            - containerPort: 8080
          env:
            - name: TARGET
              value: "Knative"
EOF

kubectl get revision -l serving.knative.dev/service=hello
lr90@xbook ~ % kubectl get revision -l serving.knative.dev/service=hello
NAME          CONFIG NAME   GENERATION   READY   REASON   ACTUAL REPLICAS   DESIRED REPLICAS
hello-00001   hello         1            True             0                 0
hello-00002   hello         2            True             1
```

表里的 `hello-00001` / `hello-00002` 是 Knative 按 `{Configuration 名}-{generation}` 自动起的（见第五节），`GENERATION` 列即序号。这次 apply 没有写 `template.metadata.name`，所以不会变成 `hello-v2` 这种自定义名。

`hello-00001` **没有被删**。再次 apply 只是新建 `hello-00002`，并把 Route 默认切到 latest；旧快照仍作为 Revision 对象留着。`ACTUAL/DESIRED REPLICAS = 0` 表示当前没有流量打到它，KPA 把它缩到零，不是 `kubectl delete revision`。下面 `traffic` 里写 `revisionName: hello-00001`，就是把流量再分给这个还在的旧版本，副本会重新拉起。

按百分比切流时写 `spec.traffic`（语义见第五节）。记下旧 Revision 名：

```bash
cat <<EOF | kubectl apply -f -
apiVersion: serving.knative.dev/v1
kind: Service
metadata:
  name: hello
spec:
  template:
    spec:
      containers:
        - image: ghcr.io/knative/helloworld-go:latest
          ports:
            - containerPort: 8080
          env:
            - name: TARGET
              value: "Knative"
  traffic:
    # 当前 latest = 刚创建的 hello-00002（TARGET=Knative），占 20%
    - latestRevision: true
      percent: 20
    # 仍存在的旧快照 hello-00001（TARGET=World）；当前副本为 0，分到流量后会再拉起
    - revisionName: hello-00001
      percent: 80
EOF

kubectl get route hello -o yaml
```

`Revision` 是某一次 `template` 的**不可变快照**。上面第二次 apply 改了 `TARGET`，Configuration 的 generation 从 1 变成 2，于是多出 `hello-00002`；`hello-00001` 仍在，只是默认不再吃流量。`spec.traffic` 按 **Revision 名字**切百分比：`latestRevision: true` 始终指向当前 latest（这里是 `hello-00002`），`revisionName: hello-00001` 则固定打到旧版。同一条目不能同时写 `latestRevision: true` 和 `revisionName`。未写 `traffic` 时，Route 把 100% 给 latest。

看 Configuration（与 ksvc 同名 `hello`）：

```bash
kubectl get configuration hello
# NAME    LATESTCREATED   LATESTREADY   READY
# hello   hello-00002     hello-00002   True

# generation：metadata.generation 是该 Configuration 被改了几次
kubectl get configuration hello -o jsonpath='{.metadata.generation}{" latestCreated="}{.status.latestCreatedRevisionName}{" latestReady="}{.status.latestReadyRevisionName}{"\n"}'
# 2 latestCreated=hello-00002 latestReady=hello-00002
```

`LATESTCREATED` / `LATESTREADY` **只指向当前最新那一版**，所以上面看不到 `hello-00001`（名字是五位补零 `hello-00001`，不是 `hello-0001` / `hello-001`）。旧快照在 Revision 列表里：

```bash
lr90@xbook ~ % kubectl get revision -l serving.knative.dev/configuration=hello
NAME          CONFIG NAME   GENERATION   READY   REASON   ACTUAL REPLICAS   DESIRED REPLICAS
hello-00001   hello         1            True             0                 0
hello-00002   hello         2            True             0                 0
```

### 6.5 用流量驱动 queue-proxy 扩缩

控制 in-flight 请求数或 QPS，观察 queue-proxy → autoscaler → Deployment。需已完成 6.1。压测用 [hey](https://github.com/rakyll/hey)：

```bash
go install github.com/rakyll/hey@latest
```

官方 `autoscale-go` 可用 query `sleep=` 拉长单次请求，抬高 in-flight 并发（类似等待外部 API 的 handler）：

```bash
cat <<EOF | kubectl apply -f -
apiVersion: serving.knative.dev/v1
kind: Service
metadata:
  name: autoscale-go
spec:
  template:
    metadata:
      annotations:
        autoscaling.knative.dev/target: "10"
        autoscaling.knative.dev/min-scale: "0"
        autoscaling.knative.dev/max-scale: "10"
    spec:
      containers:
        - image: ghcr.io/knative/autoscale-go:latest
          ports:
            - containerPort: 8080
EOF

export URL="http://127.0.0.1:8080"
# 不要用 HOST：zsh 里 $HOST 是本机主机名，覆盖后 prompt 会变成 `user@Host: autoscale-go ...`
export KSVC_HOST="autoscale-go.default.127.0.0.1.sslip.io"
```

#### 实验 A：50 in-flight → 约 5 个 Pod

```bash
# 终端 1
hey -z 60s -c 50 -host "$KSVC_HOST" "${URL}?sleep=500"

# 终端 2：每 2s 刷新当前快照（macOS 默认没有 watch，先 brew install watch）
watch -n 2 'kubectl get deploy,pods,podautoscaler -l serving.knative.dev/service=autoscale-go'


Every 2.0s: kubectl get deploy,pods,podautoscaler -l serving.knative.dev/service=autoscale-go                                       xbook: 四  8月/20 02:05:29 2026
                                                                                                                                                      in 0.356s (0)
NAME                                            READY   UP-TO-DATE   AVAILABLE   AGE
deployment.apps/autoscale-go-00001-deployment   7/8     8            7           6m58s

NAME                                                 READY   STATUS              RESTARTS   AGE
pod/autoscale-go-00001-deployment-69f9769f67-5zm96   2/2     Running             0          3s
pod/autoscale-go-00001-deployment-69f9769f67-7sbdg   2/2     Running             0          8s
pod/autoscale-go-00001-deployment-69f9769f67-drld6   0/2     ContainerCreating   0          9s
pod/autoscale-go-00001-deployment-69f9769f67-lw8v5   2/2     Running             0          9s
pod/autoscale-go-00001-deployment-69f9769f67-mckcm   2/2     Running             0          12s
pod/autoscale-go-00001-deployment-69f9769f67-mvvwp   2/2     Running             0          6s
pod/autoscale-go-00001-deployment-69f9769f67-ndmtl   2/2     Running             0          9s
pod/autoscale-go-00001-deployment-69f9769f67-zbjhb   2/2     Running             0          9s

NAME                                                                DESIREDSCALE   ACTUALSCALE   READY   REASON
podautoscaler.autoscaling.internal.knative.dev/autoscale-go-00001   8              7             True
```

这条 `hey` 在造 **稳定的 in-flight 并发**（concurrency），不是冲 QPS：

| 参数 | 作用 |
| --- | --- |
| `-z 60s` | 压 60 秒后停（按时长，不是发满 N 个请求） |
| `-c 50` | 50 个并发 worker；一个返回立刻发下一个，in-flight 数大致钉在 50 |
| `-host "$KSVC_HOST"` | 设置 HTTP `Host`（必须用 `-host`，见下）。请求打到 `127.0.0.1:8080`，Kourier 靠 Host 路由到 `autoscale-go` |
| `?sleep=500` | `autoscale-go` 的 query，单位毫秒：handler 睡 500ms 再返回 |

`sleep` 必须够长：每个请求占着一个 worker 约 0.5s，50 个 worker 才能把 concurrency 撑满。`sleep=0` 时请求瞬间结束，in-flight 数远低于 50，扩容看不出来。

**`hey` 必须用 `-host`，不能用 `-H "Host: ..."`。** Go 的 `net/http` 实际发出的 Host 来自 `Request.Host`（默认取 URL 主机名 `127.0.0.1`），`-H` 写进 Header map 的 `Host` 会被丢掉。Kourier 按 Host 选 ksvc，对不上就 **404**，handler 根本不跑，`sleep=500` 也不会生效。典型误跑汇总：`[404] 几十万 responses`、Average 几毫秒、`Requests/sec` 上万、watch 仍是 `NoTraffic`。`curl -H "Host: ..."` 没有这个问题。

先确认一发能打中服务（应卡住约 0.5s，且不是 404）：

```bash
curl -sS -w '\nHTTP %{http_code}  time=%{time_total}s\n' \
  -H "Host: $KSVC_HOST" "${URL}?sleep=500"
```

没有 `watch` 时用 `kubectl -w`（事件流，不是整表刷新）：

```bash
kubectl get deploy,pods,podautoscaler -l serving.knative.dev/service=autoscale-go -w
```

**先开 watch、还没跑 hey** 时就是缩到 0，没有 Pod 行，PA 报 `NoTraffic`——这是正常空闲态，不是没装好：

```text
NAME                                            READY   UP-TO-DATE   AVAILABLE   AGE
deployment.apps/autoscale-go-00001-deployment   0/0     0            0           4m27s

NAME                                                                DESIREDSCALE   ACTUALSCALE   READY   REASON
podautoscaler.autoscaling.internal.knative.dev/autoscale-go-00001   0              0             False   NoTraffic
```

另开终端跑 `hey` 后，`DESIREDSCALE` / `ACTUALSCALE` 会涨，并出现 `pod/autoscale-go-00001-deployment-...`。`READY False` 在有流量时应变 `True`。若 hey 已经在跑仍停在 `NoTraffic`：看 hey 汇总是不是全 `404`（Host 没用上），以及该终端里 `URL` / `KSVC_HOST` 是否还在。

稳定后副本约 **`ceil(50/10)=5`**（Panic 可能短暂更高）。压测结束后约 60s+ 回落，最终可到 0。

#### 实验 B：硬并发上限

`containerConcurrency` 是 **每个副本** 允许同时进入 **user-container** 的 in-flight 请求上限，写在 `spec.template.spec` 上（不是 annotation）。由 **queue-proxy** 强制：已有 N 个请求正在处理时，第 N+1 个进 sidecar 队列，**不会**再转给应用。这和 K8s Deployment 无关——kubelet / Service 都不按「in-flight HTTP 数」限流。

| | 软目标 `autoscaling.knative.dev/target` | 硬上限 `containerConcurrency` |
|---|---|---|
| 作用 | 告诉 KPA「希望每 Pod 平均接近这个并发」 | 告诉 queue-proxy「应用最多同时处理这么多个」 |
| 能否超过 | 能（突发、窗口均值） | 不能；多出来的在 sidecar 等 |
| 默认 | 集群约 `100`（`container-concurrency-target-default`） | `0` = **不限制** |
| 两者都写 | 扩缩用的目标取 **两者较小值**（避免目标 > 硬上限） | 同上 |

默认 `0` 时，一个 Pod 可以同时处理很多请求，KPA 只按软目标加副本。设成正整数是为了保护**扛不住高并发**的应用（单线程、连接池很小、一次只能跑一个推理等）。官方建议：没有明确理由就不要设硬上限；设太小会排队变长、冷启动变多。

`target-utilization-percentage`（默认约 70%）会让 KPA **在顶到硬上限之前**就开始加 Pod。例如上限 10、利用率 70%，平均并发到 7 就扩，8–10 仍由现有副本接着打。

本实验 `target: "10"` 且 `containerConcurrency: 5`，有效扩缩目标是 **5**（取小），不是 10。`hey -c 20` + `sleep=2000` 维持约 20 in-flight：每 Pod 最多 5 个真正进应用，其余在 queue-proxy 排队；KPA 看到排队 + in-flight 偏高就会加副本（约 `ceil(20/5)` 量级，再乘 70% 利用率会略多）。

请求路径：

```text
Client --> queue-proxy --≤5 in-flight--> user-container
                 |
                 +-- 超出则在 sidecar 排队
```

```bash
cat <<EOF | kubectl apply -f -
apiVersion: serving.knative.dev/v1
kind: Service
metadata:
  name: autoscale-go
spec:
  template:
    metadata:
      annotations:
        autoscaling.knative.dev/metric: "concurrency"
        autoscaling.knative.dev/target: "10"
        autoscaling.knative.dev/min-scale: "0"
        autoscaling.knative.dev/max-scale: "10"
    spec:
      containerConcurrency: 5   # 硬上限：每副本最多 5 个请求同时进应用
      containers:
        - image: ghcr.io/knative/autoscale-go:latest
          ports:
            - containerPort: 8080
EOF

hey -z 20s -c 20 -host "$KSVC_HOST" "${URL}?sleep=2000"
```

#### 观察决策

```bash
kubectl get podautoscaler -l serving.knative.dev/service=autoscale-go \
  -o custom-columns=NAME:.metadata.name,DESIRED:.status.desiredScale,ACTUAL:.status.actualScale
kubectl get serverlessservice -l serving.knative.dev/service=autoscale-go
kubectl logs -n knative-serving deploy/autoscaler --tail=50 | rg -i 'panic|scale|PodCount'
```

官方说明见 [Autoscale Sample App - Go](https://knative.dev/docs/serving/autoscaling/autoscale-go/)。

### 6.6 清理

```bash
kubectl delete ksvc hello autoscale-go --ignore-not-found
kind delete cluster --name knative
```

---

## 七、Eventing 如何接上（概念串联）

Serving 解决「HTTP 来了如何跑容器」；Eventing 解决「事件如何可靠地送到消费者」。

```
事件源（Kafka / PingSource / ApiServerSource ...）
        |
        v
     Broker  ----Trigger（过滤）---->  订阅者（常是 Knative Service）
        |
     Channel（可选，点对点/扇出等）
```

约定载荷为 **CloudEvents**。本地可同样用 YAML 安装：`eventing-crds.yaml` + `eventing-core.yaml`，再选 Broker 实现。内存后端仅演示；生产用 Kafka / RabbitMQ 等。

最小心智模型：Source 产生事件 → Broker 接收 → Trigger 按属性过滤 → 投递到 Serving Service 的 HTTP 端点。消费者仍是无状态 HTTP 服务，可继续享受 scale-to-zero。

---

## 八、Knative vs KEDA

二者都能「闲时缩副本、忙时拉起来」，但**不是同一层抽象**，常被误当成二选一替代品。KEDA 是 CNCF Graduated 的**事件驱动自动扩缩器**；Knative Serving 是**请求驱动的应用运行时**（部署 + 路由 + 扩缩一体）。

### 8.1 设计哲学

| 维度 | Knative Serving | KEDA |
|------|-----------------|------|
| 核心问题 | 如何把无状态容器变成可缩到零的 **HTTP 服务** | 如何按**外部信号**改已有工作负载的副本数 |
| 抽象层级 | 应用平台：自有 CR（ksvc / Revision / Route）+ 数据面（Activator、queue-proxy） | 扩缩插件：`ScaledObject` / `ScaledJob` 挂在 Deployment、Job 等之上 |
| 扩缩信号 | in-flight 请求并发、RPS（活流量） | Kafka lag、SQS 深度、Prometheus、Cron、SQL 等 **60+ scaler** |
| 流量路径 | 拥有入口与冷启动缓冲；零副本时请求先进 Activator | **默认不接管 HTTP 路径**；缩到 0 后新请求会失败，除非另加 KEDA HTTP add-on 等 |
| 与 HPA 关系 | 自研 KPA（也可接 HPA class，但无缩到零） | 通常**创建/驱动 HPA**，把外部指标变成 HPA 能用的 metric |
| 部署模型 | 换一套 Service 模型（Revision、流量百分比） | **保留**原有 Deployment/Service/Ingress，侵入小 |
| 附加能力 | 金丝雀、Revision、可选 Eventing/Functions | 专注扩缩；路由与发布策略仍用原生 K8s / Mesh |

一句话：Knative 问的是「服务怎么被请求驱动地跑起来」；KEDA 问的是「副本数听谁的」。

### 8.2 使用场景

| 更适合 Knative | 更适合 KEDA |
|----------------|-------------|
| 对外 / 对内 **HTTP(S) API**，要按并发扩缩 | **队列 / 流消费者**（Kafka、RabbitMQ、SQS…）按积压扩 |
| 需要 **scale-to-zero 且冷启动不丢请求**（Activator） | 已有 Deployment，只想加扩缩，不想换网络与 CR 体系 |
| Revision 流量切分、金丝雀发布 | Cron 定时拉起批处理；按 Prometheus 自定义指标扩 |
| 与 Knative Eventing 一体的 CloudEvents 流水线 | GPU 推理批任务、异步 pipeline；常与 Karpenter 等节点扩缩组合 |
| 想要 Cloud Run 类体验、自建集群 | 多工作负载类型（Deployment / Job / StatefulSet）统一用 scaler |

**不宜互相硬替**

- 用 KEDA「假装 Knative」：没有 Activator 时，缩到 0 的 HTTP 服务会直接连不上；KEDA-HTTP 能补一截，但不会带来 Revision/Route 那套发布模型。
- 用 Knative「假装 KEDA」：可以挂 Eventing 吃 CloudEvents，但按 Kafka lag 调副本不是 Serving 的主战场；队列深度类指标更自然落在 KEDA。

**可组合**：同一集群里 HTTP 入口走 Knative，后台 worker 用 Deployment + KEDA；注意**同一负载不要两个扩缩器抢 replicas**。

### 8.3 其他边界

| 方案 | 关系 / 差异 |
|------|-------------|
| **Deployment + HPA** | 更通用；无原生缩到零与 Activator 语义；运维面更碎 |
| **Cloud Run** | 体验接近 Knative Serving；托管、省运维，绑定 GCP |
| **AWS Lambda** | 函数粒度与事件生态强；不是「任意容器 + 任意集群」模型 |
| **Agent Sandbox / 有状态会话** | Knative 适合**无状态**请求；缩到零再拉起是**新实例**，无「同一沙箱身份 + PVC + 快照」连续性。本仓库另有 Agent Sandbox 对比（有状态会话，不在本篇范围） |

适合 Knative 的：突发 HTTP API、间歇流量、需要金丝雀的无状态服务。  
不适合硬套的：强会话粘滞、本地磁盘状态、长生命周期 IDE/沙箱、无法接受冷启动的超低延迟路径（除非 `min-scale≥1` 并接受成本）。

---

## 九、生产落地时多想一步

1. **网络层**：开发常用 Kourier；已有 Istio/Contour 可复用，减少再引入一套数据面。
2. **DNS / TLS**：本地 `config-domain` + sslip.io / Host 头仅供实验；生产需真实通配符域名与证书。
3. **冷启动预算**：镜像体积、启动探测、`min-scale` / `scale-to-zero-grace-period`、初始并发。
4. **并发模型**：同步阻塞型 handler 用 concurrency；吞吐型可看 rps；硬上限与软目标分开调。
5. **可观测性**：关注 Activator 是否在路径上、queue-proxy 指标、Revision 与 Deployment 事件。
6. **安装**：YAML 清单透明、适合对照控制面；Operator 便于升级与配置。清单 tag 以当时官方 YAML 安装页为准。

---

## 十、附录：Service 配置速查

默认值以集群里 `knative-serving` 的 `config-defaults` / `config-autoscaler` 为准。扩缩 annotation 必须写在 `spec.template.metadata.annotations`。

### Revision spec（`spec.template.spec`）

容器按普通 Pod 写：`image` / `ports` / `env` / `resources` / probes；queue-proxy 由控制器注入。其余 `serviceAccountName` 等照 Kubernetes。

| 字段 | 默认 | 做什么 |
|------|------|--------|
| `containerConcurrency` | `0`（不限制） | 每副本进 user-container 的 in-flight **硬上限**，queue-proxy 强制；与 `target` 同时写时扩缩取较小值。见 6.5 实验 B |
| `timeoutSeconds` | `300`（上限常见 `600`） | 整次请求最长多久。到期：若还没写出响应，queue-proxy 回 **504** 并 cancel 上游；若已经在流式输出，则停掉 copy，客户端连接会在中途断掉。同步等 LLM 往往要加大 |
| `responseStartTimeoutSeconds` | `300` | 交给容器后，**首字节**必须在此时限内出现。到期且还没开始写响应 → 同样 **504**；已经开始吐数据则不再用这条杀请求 |
| `idleTimeoutSeconds` | `0`（不限制） | 已经有流量之后，**后续空闲**（应用不再吐字节）允许多久。流式/SSE 才需要盯 |

超时三件套：`timeoutSeconds` 管「从头到尾」；`responseStartTimeoutSeconds` 管「有没有开始说话」；`idleTimeoutSeconds` 管「说话中途停太久」。到期时 queue-proxy 对客户端写 **504 Gateway Timeout**（响应已开始则只能掐流），并 cancel 打到 user-container 的上游请求。流式接口把 idle 留 `0`、把总超时加大。

### 扩缩 annotations

| annotation | 默认 | 做什么 |
|------|------|--------|
| `autoscaling.knative.dev/class` | `kpa.autoscaling.knative.dev` | KPA 可缩到 0；`hpa.autoscaling.knative.dev` **不能**缩到 0 |
| `autoscaling.knative.dev/metric` | `concurrency` | `concurrency`：按 in-flight；`rps`：按每秒请求数（短请求、高 QPS） |
| `autoscaling.knative.dev/target` | concurrency 约 `100`，rps 约 `200` | **软目标**：希望每 Pod 平均接近的值，突发可超过 |
| `autoscaling.knative.dev/target-utilization-percentage` | `70` | 实际瞄准 `target × 70%`，在顶满硬上限**之前**加副本 |
| `autoscaling.knative.dev/min-scale` | KPA 且允许缩到 0 时为 `0` | 下限。`≥1` 则永不缩到 0，换延迟、付常驻成本 |
| `autoscaling.knative.dev/max-scale` | `0` = 不限制 | 上限，防止被打爆集群 |
| `autoscaling.knative.dev/initial-scale` | `1` | **新建** Revision 先拉到的副本数，Ready 一次后作废，随后仍可按流量缩 |
| `autoscaling.knative.dev/activation-scale` | `1` | 从 0 **唤醒**时至少拉起几个，避免第一波请求挤在单 Pod |
| `autoscaling.knative.dev/window` | `60s` | stable 窗口。缩到 0 前，最后一副本要等窗口内**一直无流量** |
| `autoscaling.knative.dev/panic-window-percentage` | `10`（即 6s） | panic 用更短窗口，只快扩、期间通常不缩 |
| `autoscaling.knative.dev/panic-threshold-percentage` | `200` | 观测负载 ≥ 当前副本能力的 200% 进 panic |
| `autoscaling.knative.dev/scale-down-delay` | `0s` | 负载已低，再等这么久才真缩。与 `min-scale` 不同：到期仍可到 0 |
| `autoscaling.knative.dev/target-burst-capacity` | `200` | 决定 Activator 是否留在数据面。`0`：仅从 0 拉起时经过 Activator；`-1`：始终经过 |

缩容时间线：流量没了 → 等 **stable window**（以及可选的 **scale-down-delay**）→ 副本向 `min-scale` 靠。`min-scale: 0` 时才会真正没 Pod。`scale-down-delay` 是「先留着防抖」，不是下限。

`target-burst-capacity`（TBC）：KPA 估算「现有副本还能吃多少突发」。余量不够就把 Ingress 指回 **Activator**（缓冲 + 唤醒）；余量够则旁路，请求直打 queue-proxy。这就是第三节 Proxy / Serve 切换的旋钮。

### 流量与可见性

| 字段 / annotation | 默认 | 做什么 |
|-------------|------|--------|
| `spec.traffic` | 100% → latest ready | `percent` 之和必须 100。`latestRevision: true` 跟 `revisionName` **不能写在同一条** |
| `spec.template.metadata.name` | `{ksvc}-{generation}` 五位补零 | 即将创建的 Revision 名，如 `hello-v2`；命名空间内唯一 |
| `networking.knative.dev/visibility: cluster-local` | 对外暴露 | 只给集群内 DNS（`*.svc.cluster.local`），不进公网 Ingress |

`traffic` 改的是网关后面的权重，旧 Revision 对象还在；`0` 副本只表示没流量，不是被删掉。切流 YAML 见第五节。

---

## 参考与延伸阅读

| 链接 | 说明 |
|------|------|
| [Knative 文档首页](https://knative.dev/docs/) | 官方总入口：概念、安装、Serving / Eventing |
| [Install Serving with YAML](https://knative.dev/docs/install/yaml-install/serving/install-serving-with-yaml/) | Serving YAML 安装（第六节） |
| [Deploying a Knative Service](https://knative.dev/docs/getting-started/first-service/) | helloworld 官方入门 |
| [Knative Serving Overview](https://knative.dev/docs/serving/) | Service / Route / Configuration / Revision 对象模型（本文 `object_model.png`） |
| [Knative Serving Architecture](https://knative.dev/docs/serving/architecture/) | 控制面组件、Proxy/Serve、KIngress（本文 `serving-architecture*.png`） |
| [HTTP Request Flows](https://knative.dev/docs/serving/request-flow/) | Activator / 高流量旁路 / queue-proxy 权威说明 |
| [Demystifying Activator on the data path](https://knative.dev/blog/articles/demystifying-activator-on-path/) | Service→PA→SKS→K8s Service 派生链与 proxy/serve 模式 |
| [Serving API](https://knative.dev/docs/serving/reference/serving-api/) | Service / Revision 字段：`timeoutSeconds`、`containerConcurrency`、`traffic` |
| [config-defaults](https://knative.dev/docs/serving/configuration/config-defaults/) | 超时、资源 request、`containerConcurrency` 全局默认 |
| [Configuring concurrency](https://knative.dev/docs/serving/autoscaling/concurrency/) | 软目标 vs 硬上限、target-utilization |
| [Scale bounds](https://knative.dev/docs/serving/autoscaling/scale-bounds/) | min/max/initial/activation-scale、scale-down-delay |
| [KPA-specific settings](https://knative.dev/docs/serving/autoscaling/kpa-specific/) | stable/panic 窗口与阈值 |
| [Target burst capacity](https://knative.dev/docs/serving/load-balancing/target-burst-capacity/) | TBC 如何把 Activator 切进/切出数据面 |
| [Autoscale Sample App - Go](https://knative.dev/docs/serving/autoscaling/autoscale-go/) | 官方 queue-proxy/KPA 压测实验（本文 6.5 节来源） |
| [Install Eventing with YAML](https://knative.dev/docs/install/yaml-install/eventing/install-eventing-with-yaml/) | Eventing CRD / core / Broker 的 YAML 安装 |
| [Knative Operator](https://knative.dev/docs/install/operator/knative-with-operators/) | 用 Operator 安装与升级 Serving/Eventing |
| [SREKubeCraft: Knative guide](https://srekubecraft.io/posts/knative/) | 面向平台工程的 Serving/Eventing/Functions 综述 |
| [KEDA 官网](https://keda.sh/) | 事件驱动扩缩：ScaledObject、scaler 列表 |
| [KEDA vs Knative vs HPA](https://thinhdanggroup.github.io/keda-knative-kubenetes/) | 三种扩缩策略的场景划分（工程向） |
