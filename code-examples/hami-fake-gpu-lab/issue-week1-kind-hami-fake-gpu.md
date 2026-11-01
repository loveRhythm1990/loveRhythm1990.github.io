# Issue: 第 1 周 kind + HAMi + 假卡实验一键脚本

> 建议 Labels：`week-1`、`gpu-sharing`、`kind-of-feature`
> 建议 Assignee：lr90
> 前置阅读：本仓库 `_posts/2024-11-20-alicloud-gpu.markdown` 第 7.3 节（HAMi 调研）

## 背景

GPU 共享调度系列实验的第 1 周任务：在没有真实 GPU 硬件的本地环境（macOS + kind）上，跑通 HAMi 的 vGPU 共享调度链路，并把全过程固化为一键脚本。

HAMi 由三个核心组件构成：

1. **hami-webhook**（MutatingWebhook）：拦截申请了 `nvidia.com/gpu`、`nvidia.com/gpumem`、`nvidia.com/gpucores` 资源的 Pod，改写 `schedulerName` 为 hami-scheduler。
2. **hami-scheduler**（Scheduler Extender）：根据各节点上报的设备注册信息做容量记账与打分，完成一卡多 Pod 的共享分配。
3. **hami-device-plugin**（每节点 DaemonSet，节点需打 `gpu=on` 标签才部署）：向 kubelet 上报 `nvidia.com/gpu` 资源，并向节点写入设备注册注解，供 scheduler 感知"每张卡的型号/显存/核数/健康度"。

本机没有 NVIDIA 驱动和真卡，组件 3 无法原样运行，这就是"假卡"要解决的问题。实验又涉及镜像预热、集群重建、注解注入、版本对齐等十几步手工操作，每次重来成本高，因此本 issue 的交付物是**一键脚本**，而不是一份操作手册。

## 目标

一句话：`./lab.sh up && ./lab.sh verify` 之后，能在无 GPU 的 kind 集群里演示"两个 Pod 共享同一张假卡"与"超额申请被 HAMi 拒绝"。

完成后应具备：

- `lab.sh up`：从零拉起实验环境（kind 集群 → 假卡注入 → HAMi 部署 → 验证负载），全程无人工干预、幂等。
- `lab.sh verify`：自动断言共享调度与资源回收行为，全部通过后退出码为 0。
- `lab.sh down`：完全清理（删 kind 集群、无残留镜像/卷）。
- 实验记录 README：版本基线、输出样例、假卡注册信息的来源 commit。

## 范围

**做：**

- 调度与记账层验证：webhook 拦截、hami-scheduler 容量记账、共享分配、超额 Pending、回收归还。
- 假卡实现：能够在 kind 节点上模拟出"N 张具备显存/核数属性的卡"，注册到 HAMi 视野中。
- 一键脚本及其幂等性、错误提示、清理逻辑。

**不做：**

- 不验证真实 CUDA 算力/显存隔离（kind 容器内无 GPU 运行时，`nvidia-smi`、CUDA workload 不在本实验范围）。
- 不验证 HAMi 的动态 MIG、NPU/MLU 等非 NVIDIA 设备类型。
- 不接入监控（HAMi 的 Prometheus 指标留到后续周）。

## 环境与版本基线

运行环境：macOS（arm64）+ Docker Desktop 或 OrbStack。

脚本依赖（启动时必须逐个检查，缺失时给出安装提示而非裸报错）：

| 依赖 | 用途 | 检查方式 |
|---|---|---|
| docker（daemon 运行中） | kind 节点载体 | `docker info` |
| kind | 本地集群 | `kind version` |
| kubectl | 集群操作 | `kubectl version --client` |
| helm v3 | 部署 HAMi | `helm version --short` |

为可复现，以下版本在脚本顶部以变量固化（可用环境变量覆盖，但默认值必须曾实测通过）：

```bash
KIND_NODE_IMAGE="kindest/node:v1.28.13"   # 与 HAMi chart 支持的 K8s 版本区间对齐
HAMI_REPO_URL="https://project-hami.github.io/HAMi/"
HAMI_CHART_VERSION="<helm search repo hami-charts/hami 后填写实测版本>"
FAKE_GPU_COUNT=1        # 假卡数量
FAKE_GPU_MEM_MIB=8192   # 每张假卡显存（MiB），用于 gpumem 记账
FAKE_GPU_CORES=100      # 每张假卡核心百分比总额
LAB_CLUSTER="hami-fake-gpu-lab"
```

> 版本基线以执行时 `helm search repo hami-charts/hami -l` 的实际结果为准写入 README；HAMi 的节点注册注解格式随版本可能变化，不允许在脚本里裸用未经核实的最新版。

## 假卡设计

分两个档位，A 档是 B 档的对照组，脚本中均保留、通过 `--tier=a|b` 选择（默认 b）。

### A 档：仅调度记账（对照组）

对 kind 节点直接 patch 扩展资源容量：

```bash
kubectl patch node "${node}" --subresource=status --type=merge \
  -p '{"status":{"capacity":{"nvidia.com/gpu":"4"},"allocatable":{"nvidia.com/gpu":"4"}}}'
```

- 用途：验证 K8s 扩展资源记账本身（默认调度器下，申请 `nvidia.com/gpu` 的第 5 个 Pod 应 Pending）。
- 局限：**不经过 HAMi 设备注册**，hami-scheduler 看不到设备，不能算"跑通 HAMi"，只作为后续排查问题时的剥离对照。

### B 档：fake device-plugin（核心交付）

实现一个最小 fake device-plugin（Go，单人日量级），DaemonSet 部署到打了 `gpu=on` 的实验节点，模仿 HAMi 设备插件的两个行为：

1. 通过 K8s Device Plugin API（`/var/lib/kubelet/device-plugins/` unix socket）向 kubelet 注册 `nvidia.com/gpu` 资源，数量为 `FAKE_GPU_COUNT`，Allocate 阶段仅返回空环境变量即可（不做设备挂载——本实验不验证容器内可见性）。
2. 向节点写入 HAMi 设备注册注解 `hami.io/node-nvidia-register`（含每张卡的 id、数量、显存、核数、型号、健康度等字段），使 hami-scheduler 把该节点纳入可调度设备池。

**注解字段格式以所 pin 的 HAMi 版本源码为准**：从 HAMi 仓库 `pkg/device-plugin/nvidiadevice/nvinternal/plugin/` 下的节点注册逻辑提取字段顺序与分隔符，把该源码 commit 记录在 README。fake plugin 中把这套格式收敛为单一的 `const`/模板，BM 版本升级时只允许从这里改。

镜像构建：`docker build` 出 `fake-gpu-device-plugin:lab` 后 `kind load` 进集群，避免依赖远端镜像仓库。

## 一键脚本规格

目录布局：

```text
hami-fake-gpu-lab/
├── lab.sh                        # 一键入口
├── fake-device-plugin/           # B 档假卡插件（Go）
│   ├── Dockerfile
│   ├── main.go
│   └── daemonset.yaml
├── manifests/
│   ├── kind-cluster.yaml         # 1 control-plane + 1 worker
│   └── workloads/                # 验证负载（见下）
└── README.md                     # 实验记录（本 issue 验收项之一）
```

### CLI 接口

```text
lab.sh up [--tier a|b] [--reuse]     # 拉起环境；--reuse 复用已存在集群
lab.sh verify                        # 执行验收断言，全过为 0
lab.sh status                        # 打印节点假卡容量、HAMi 组件、验证负载状态
lab.sh down                          # 删除 kind 集群并确认无残留
```

### 硬性要求

- `set -euo pipefail` + `trap`：任何一步失败输出当前阶段名与建议排查命令（如 `kubectl -n kube-system logs deploy/hami-scheduler`）。
- 幂等：`up` 重入不报错（已存在的集群/命名空间/负载走"已就绪"路径）。
- 不引入额外解析依赖：输出解析统一用 `kubectl -o jsonpath`，不要求 jq/yq。
- 等待一律带超时（如 `kubectl wait --for=condition=Ready --timeout=120s`），超时报错而非死等。

### 内部阶段

1. **S0 依赖检查**：见"环境与版本基线"，逐项失败即退出。
2. **S1 kind 集群**：`lab.sh` 用 `manifests/kind-cluster.yaml` 建集群（已存在且 `--reuse` 时跳过）；镜像预热 `kind load` 避免在线拉取抖动。
3. **S2 假卡注入**：A 档 patch capacity；B 档构建/加载/部署 fake device-plugin，并给 worker 节点打 `gpu=on` 标签。
4. **S3 部署 HAMi**：`helm repo add hami-charts ${HAMI_REPO_URL}`，`helm install hami hami-charts/hami -n kube-system --version ${HAMI_CHART_VERSION}`，等待 webhook/scheduler/device-plugin rollout。
5. **S4 验证负载**：
   - `gpu-share-demo`：两个 Pod 各申请 `nvidia.com/gpu: 1` + `nvidia.com/gpumem: 3072`，期望共享同一张假卡并都 Running。
   - `gpu-oversub-demo`：第三个 Pod 申请超出剩余容量的显存，期望 Pending。
6. **S5 verify**：断言见下节。
7. **S6 down**：`kind delete cluster --name ${LAB_CLUSTER}`，并确认 `docker ps` 无残留。

## verify 断言清单

1. `kubectl get node -o jsonpath` 读到 `nvidia.com/gpu` capacity == `FAKE_GPU_COUNT`。
2. 节点上存在 HAMi 设备注册注解（B 档），且内容包含配置的显存/核数。
3. `gpu-share-demo` 两个 Pod 均 Running，且调度注解显示落在同一 `deviceidx`（共享一卡）。
4. `gpu-oversub-demo` Pod 为 Pending，事件中能找到 hami-scheduler 报出的容量不足原因。
5. 资源回收：删除 share-demo 其中一个 Pod 后，oversub Pod 在超时时间内转为 Running（验证记账归还）。
6. `down` 后 `kind get clusters` 不含 `${LAB_CLUSTER}`，`docker ps -a` 无对应容器。

## 验收标准

- [ ] 在干净机器上 `./lab.sh up && ./lab.sh verify` 全程无人工干预、退出码 0。
- [ ] 重复执行 `./lab.sh up --reuse` 幂等通过。
- [ ] 断言清单 6 条全部自动覆盖，无"人工看一眼"项。
- [ ] 预期失败路径有可读报错：docker 未启动 / 端口冲突 / helm repo 不可达。
- [ ] README 记录：版本基线（kind/node 镜像/chart）、假卡注册注解来源的 HAMi commit、verify 输出样例。

## 风险点

- **HAMi 注册注解格式版本相关**：脚本与 chart 版本必须 pin，升级时 fake plugin 的注册模板需要回归验证；未核实前不得在脚本里引用"最新版"。
- **kind 节点内 kubelet 路径假设**：device-plugin 默认使用 `/var/lib/kubelet/device-plugins`，kind 节点内路径与常规节点一致，但属首个 `up` 时要实际确认的点，若不符需在 kind 配置中加 `extraMounts`。
- **arm64 镜像可得性**：HAMi 各组件与 kindest/node 的 arm64 镜像需在 S0 阶段实际拉取确认，缺失时提前报错。
- **假卡止步于记账/调度层**：容器内看不到"卡"属预期，后续周若要演示算力隔离，需要另开 issue 评估 vGPU 宿主级方案，本 issue 不扩展范围。

## 参考

- HAMi 中文 README（架构与共享策略）：https://github.com/Project-HAMi/HAMi/blob/master/README_cn.md
- HAMi helm chart：https://github.com/Project-HAMi/HAMi/tree/master/charts/hami
- 假卡注册注解来源（按 pin 版本取）：HAMi 仓库 `pkg/device-plugin/nvidiadevice/nvinternal/plugin/` 注册逻辑
- kind 文档：https://kind.sigs.k8s.io/
- 本仓库调研文章：`_posts/2024-11-20-alicloud-gpu.markdown` 第 7.3 节

## 附录：lab.sh 框架草案

```bash
#!/usr/bin/env bash
# lab.sh — 第 1 周 kind + HAMi + 假卡实验一键脚本
set -euo pipefail
trap 'echo "✘ 阶段 [${STAGE:-init}] 失败；排查：kubectl -n kube-system get pods" >&2' ERR

KIND_NODE_IMAGE="${KIND_NODE_IMAGE:-kindest/node:v1.28.13}"
HAMI_REPO_URL="${HAMI_REPO_URL:-https://project-hami.github.io/HAMi/}"
HAMI_CHART_VERSION="${HAMI_CHART_VERSION:?需要实测后固化默认 chart 版本}"
LAB_CLUSTER="${LAB_CLUSTER:-hami-fake-gpu-lab}"
FAKE_GPU_COUNT="${FAKE_GPU_COUNT:-1}"
FAKE_GPU_MEM_MIB="${FAKE_GPU_MEM_MIB:-8192}"
TIER="b"; REUSE=""

# ── 阶段 S0：依赖检查 ─────────────────────────────
check_deps() {
  STAGE="S0-依赖检查"
  for c in docker kind kubectl helm; do
    command -v "$c" >/dev/null || { echo "缺少 $c，请先安装"; exit 1; }
  done
  docker info >/dev/null 2>&1 || { echo "docker daemon 未运行"; exit 1; }
}

# ── 阶段 S1：kind 集群 ────────────────────────────
ensure_cluster() {
  STAGE="S1-kind集群"
  if kind get clusters | grep -qx "$LAB_CLUSTER"; then
    [[ -n "$REUSE" ]] || { echo "集群已存在，使用 --reuse 或先 down"; exit 1; }
  else
    kind create cluster --name "$LAB_CLUSTER" \
      --image "$KIND_NODE_IMAGE" --config manifests/kind-cluster.yaml
  fi
  kubectl wait --for=condition=Ready nodes --all --timeout=120s
}

# ── 阶段 S2：假卡注入 ─────────────────────────────
inject_fake_gpu() {
  STAGE="S2-假卡注入"
  local node; node=$(kubectl get nodes -o jsonpath='{.items[?(@.metadata.name!="'"$LAB_CLUSTER"'-control-plane")].metadata.name}')
  kubectl label node "$node" gpu=on --overwrite
  if [[ "$TIER" == "a" ]]; then
    kubectl patch node "$node" --subresource=status --type=merge \
      -p '{"status":{"capacity":{"nvidia.com/gpu":"'"$FAKE_GPU_COUNT"'"},
                       "allocatable":{"nvidia.com/gpu":"'"$FAKE_GPU_COUNT"'"}}}'
  else
    docker build -t fake-gpu-device-plugin:lab fake-device-plugin/
    kind load docker-image fake-gpu-device-plugin:lab --name "$LAB_CLUSTER"
    # daemonset.yaml 中用 FAKE_GPU_MEM_MIB/FAKE_GPU_COUNT 环境变量渲染
    kubectl apply -f fake-device-plugin/daemonset.yaml
    kubectl -n kube-system rollout status ds/fake-gpu-device-plugin --timeout=120s
  fi
}

# ── 阶段 S3：部署 HAMi ────────────────────────────
install_hami() {
  STAGE="S3-部署HAMi"
  helm repo add hami-charts "$HAMI_REPO_URL" >/dev/null
  helm upgrade --install hami hami-charts/hami -n kube-system \
    --version "$HAMI_CHART_VERSION"
  kubectl -n kube-system rollout status deploy/hami-scheduler --timeout=180s
}

# ── 阶段 S4/S5：验证负载与断言 ────────────────────
verify() {
  STAGE="S5-verify"
  kubectl apply -f manifests/workloads/
  # 断言 1/2/3/4：capacity、注册注解、共享 Running、超分 Pending
  # 断言 5：删除一个 share Pod → oversub Pod 转 Running
  : # 具体实现随 fake plugin 落地
}

down() {
  STAGE="S6-down"
  kind delete cluster --name "$LAB_CLUSTER"
  ! docker ps -aq --filter "name=${LAB_CLUSTER}" | grep -q .
}

main() {
  local cmd="${1:-}"; shift || true
  while [[ $# -gt 0 ]]; do
    case "$1" in --tier) TIER="$2"; shift 2;; --reuse) REUSE=1; shift;; *) echo "未知参数 $1"; exit 2;; esac
  done
  case "$cmd" in
    up)     check_deps; ensure_cluster; inject_fake_gpu; install_hami; kubectl apply -f manifests/workloads/ ;;
    verify) verify ;;
    status) kubectl get nodes -o custom-columns=NODE:.metadata.name,GPU:.status.capacity.nvidia\\.com/gpu ;;
    down)   down ;;
    *) echo "usage: $0 {up [--tier a|b] [--reuse]|verify|status|down}"; exit 2 ;;
  esac
  echo "✔ $cmd 完成"
}
main "$@"
```
