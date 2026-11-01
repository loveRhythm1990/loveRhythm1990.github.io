# Issue: 第 2 周 HAMi 监控接入与假卡环境下的指标语义核对

> 建议 Labels：`week-2`、`gpu-sharing`、`monitoring`
> 建议 Assignee：lr90
> 前置依赖：第 1 周 issue（`issue-week1-kind-hami-fake-gpu.md`）的 `lab.sh` 已实现并通过其验收标准。当前仓库仅有第 1 周 issue 文档、实现未落地；落地前本 issue 阻塞在 M0。

## 背景

第 1 周在 kind + 假卡环境跑通了 HAMi 的共享调度链路，但验证手段止步于"看 Pod 状态和调度注解"。当时明确把两件事件从范围中移出，本周收回来做其中一件：**监控接入**（另一件——算力隔离的宿主级方案——仍不在本周，见文末钩子）。

需要监控的两个动机：

1. **复核记账正确性**：第 1 周"共享 Running / 超分 Pending / 删除归还"的断言只观察终态，中间的记账过程是黑盒。HAMi 暴露的分配类指标（每张卡的已分配显存/核数）能把这个黑盒打开，让 `verify` 从"看终态"升级为"连续复核记账曲线"。
2. **摸真假卡的指标边界**：假卡环境能验证的指标有一类、不能验证的有另一类，本周要把这条边界画清楚并写成档案，作为第 3 周（真卡对照，候选）的基线。

已知事实边界（本周所有结论在此边界内成立）：

- 假卡 device-plugin 只做资源上报与节点注册，容器内没有 CUDA 上下文。
- 因此"由调度记账驱动"的指标（allocated 显存/核数、设备总量）在假卡下应有真实数值；而"由宿主采集驱动"的指标（SM 利用率、实际显存占用、温度）在假卡下只能验证"指标是否存在/缺席"，数值语义无意义——缺席本身也是预期结论，不是 bug。

## 目标

一句话：在 Week 1 的一键环境上叠加一键监控栈，`./lab.sh monitor on && ./lab.sh metrics-verify` 之后，能证明"分配类指标随调度行为正确变化"并产出"假卡环境指标有效性分级表"。

完成后应具备：

- `lab.sh monitor on`：在既有 kind 集群上部署 Prometheus 栈并打通 HAMi 指标抓取，幂等。
- `lab.sh monitor off`：移除监控栈，不影响 HAMi 与验证负载。
- `lab.sh metrics-verify`：对分配类指标执行 PromQL 断言（含"重放第 1 周调度动作→指标同步变化"的动态验证），全过退出码 0；对采集类指标输出存在性报告（不断言数值）。
- 实验档案：假卡环境下"可用指标 / 仅存在指标 / 缺席指标"三级分类表，附查询样例与指标命名快照。

## 范围

**做：**

- Prometheus 栈接入（含 ServiceMonitor/Relabel 配置，随 HAMi 组件抓取）。
- 分配类指标的动态断言：跑共享/超分/回收三个调度动作，断言指标断面同步变化。
- 指标有效性分级表（假卡环境结论，注明真卡待核对项）。

**不做：**

- 不做告警规则设计/调优（Alertmanager 配置停留在"能跑通一条自擂告警"的演示水位）。
- 不做 Grafana 持久化与生产化（dashboard 以 JSON 随仓库存档，本地导入即用）。
- 不验证采集类指标数值语义（算力/显存利用率）——属真卡环境议题，等宿主级方案议题一并评估。
- 不重做第 1 周的调度断言（`verify` 保持兼容，metrics 断言独立成 `metrics-verify`）。

## 环境与版本基线

运行环境：沿用第 1 周的 macOS（arm64）+ OrbStack/Docker Desktop + kind 假卡集群。

新增依赖与版本（与第 1 周同一策略：顶部变量固化、环境变量可覆盖、默认值必须曾实测通过）：

```bash
# 在第 1 周版本基线之上新增：
KPROM_STACK_CHART_VERSION="<helm search repo prometheus-community/kube-prometheus-stack -l 后填写实测版本>"
KIND_CLUSTER="${LAB_CLUSTER:-hami-fake-gpu-lab}"   # 复用 Week 1 集群变量
PROM_NS="monitoring"
# HAMi 监控开关的 values 键名以 pin 的 chart 版本为准（见风险点）
```

- 版本基线写入 README 的要求与第 1 周一致：执行时 `helm search repo … -l` 实测后再固化。
- 本地断言不引入 jq/yq（沿用第 1 周硬约束）：Prometheus API 返回用 `curl -s … | grep -o` 做粗断言，只允许断言"存在 / 数值阈值比较"，不做结构化解析。

## 监控设计

### 指标有效性分层

| 层级 | 指标类别 | 驱动来源 | 假卡环境归宿 |
|---|---|---|---|
| L0 | 设备总量、已分配显存/核数 | 调度记账 | **可验证数值语义**（本周核心） |
| L1 | 节点设备健康度、注册信息 | 节点注册注解 | 验证与第 1 周注入的假卡参数一致 |
| L2 | SM 利用率、实际显存占用、温度/功耗 | 宿主采集（vGPUmonitor 等） | 仅验证"存在或明确缺席"，数值无意义 |

具体指标名不预填：以 pin 的 HAMI_CHART_VERSION 下的实际暴露为准，`monitor on` 后跑一次指标名录快照（`curl -sG localhost:9090/api/v1/series --data-urlencode 'match[]={job=~".*hami.*"}'` 提取 `__name__` 排序去重；`/api/v1/status/tsdb` 只返回基数统计、不接受 `match[]`，不能用作名录来源）存进 `snapshots/metrics-inventory.txt`，作为命名漂移的回归基线。

### 一键脚本扩展

```text
hami-fake-gpu-lab/
├── lab.sh                        # 复用：up/verify/down/status 不变，新增子命令
├── manifests/monitoring/         # ServiceMonitor、grafana dashboard JSON、自擂告警规则
├── snapshots/                    # 指标命名快照（作为回归基线入库）
└── README.md                     # 增补：监控章节与指标分级表
```

新增 CLI：

```text
lab.sh monitor on        # 部署 kube-prometheus-stack + HAMi 抓取配置
lab.sh monitor off       # 删除 monitoring 命名空间，保留集群与负载
lab.sh metrics-verify    # 动态断言（见下节）
lab.sh inventory snapshot|diff   # 采集指标快照 / 与快照对比
```

内部阶段：

1. **M0 前置检查**：Week 1 环境健壮（`lab.sh status` 全绿）；`HAMI_CHART_VERSION` 已 pin。
2. **M1 部署 Prometheus**：`helm upgrade --install` 到 `monitoring` 命名空间，等待 rollout。
3. **M2 打通抓取**：按 pin chart 版本的 values 开启 HAMi 侧监控开关（若无开关则补充 ServiceMonitor），`kubectl port-forward svc/…prometheus 9090` 背景化，自凭证 token/listen 口径以实际版本为准。
4. **M3 名录快照**：`inventory snapshot` 入库。
5. **M4 动态断言**：`metrics-verify` 驱动"申请→共享→回收"动作并断言指标断面。

## metrics-verify 断言清单

1. **静态断面**：指标中能查到节点假卡的总显存/核数 == `FAKE_GPU_COUNT/MEM/CORES`（L0/L1）。
2. **共享分配**：部署 `gpu-share-demo` 后 60s 内，该节点"已分配显存"指标增量 == 2×3072MiB（L0）。
3. **超分不变**：`gpu-oversub-demo` Pending 期间，allocated 指标**不**变化（负向断言：记账没有被 Pending 请求污染）。
4. **回收归还**：删除一个 share Pod 后，allocated 指标在抓取周期×3 内回落 3072MiB（L0）。
5. **采集类分级**：L2 类指标输出存在性报告（存在/缺席名单），仅打印不断言失败，归档进指标分级表。
6. **自擂告警**：一条"节点已分配显存 > 0"的告警规则能触发（推演示水位，no tuning）。

## 验收标准

- [ ] 在干净 Week 1 环境上：`lab.sh monitor on && lab.sh metrics-verify` 无人工干预、退出码 0。
- [ ] `monitor off` 后 HAMi 与验证负载不受影响，`monitor on` 重入幂等。
- [ ] 断言 6 条全部自动覆盖；断言 3 的"负向不变"有显式实现（不允许仅靠超时静默通过）。
- [ ] 指标名录快照入库，`inventory diff` 能报出命名差异（造一个改名 case 自证）。
- [ ] README 指标分级表补齐 L0/L1/L2 三档，标注"真卡待核对"的 L2 项。
- [ ] 预期失败路径有可读报错：Week 1 环境缺失 / chart 版本未 pin / Prometheus 端点不可达。

## 风险点

- **监控 values 键名与指标命名随 HAMi chart 版本漂移**：键名/指标名一律从 pin 版本实测后固化，快照入库做回归；未实测前不引用"最新版"的任何名字。
- **L2 指标"缺席" vs "错误值"的区分**：分级表只记录观察到的事实并注明环境（假卡 kind），不推断真卡行为；真卡语义待宿主级方案议题统一核对。
- **抓取间隔与断言时序**：指标是周期抓取的，断言窗口必须 ≥ 抓取间隔×3，否则偶发超时；脚本中将 scrape interval 显式配置并读取该值计算等待窗口，不写死等待时长。
- **arm64 镜像可得性**：kube-prometheus-stack 组件镜像需在 M0 阶段实际拉取确认。
- **假卡里不存在的指标不要"造"**：为让 L2 断言好看而伪造利用率数据属反目标，.issue 明确禁止；需要数值语义的 verdict 等真卡环境。

## 参考

- HAMi 监控/指标：安装文档与 chart values（以 pin 版本为准）
- kube-prometheus-stack chart：https://github.com/prometheus-community/helm-charts/tree/main/charts/kube-prometheus-stack
- 第 1 周 issue：`code-examples/hami-fake-gpu-lab/issue-week1-kind-hami-fake-gpu.md`
- 后续周候选钩子：①宿主级 vGPU 算力隔离方案评估（Week 1 显式遗留）；②真卡环境（云 GPU 节点）对照实验，复核本 issue 的 L2 空缺项
