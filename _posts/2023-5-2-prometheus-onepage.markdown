---
layout:     post
title:      "K8s 集群中的 Prometheus 监控系统"
date:       2023-5-2 10:10:00
author:     "decent"
header-img-credit: false
tags:
    - 监控
---

**目录**
- [operator 部署及冒烟测试](#operator-部署及冒烟测试)
	- [部署 prometheus stack](#部署-prometheus-stack)
	- [部署测试组件](#部署测试组件)
- [指标拉取配置](#指标拉取配置)
	- [servicemonitor relabel](#servicemonitor-relabel)
	- [其他 scrape 配置](#其他-scrape-配置)
- [指标数据存储](#指标数据存储)
	- [数据目录结构](#数据目录结构)
	- [磁盘数据量预测](#磁盘数据量预测)
	- [tsdb status](#tsdb-status)
- [附录](#附录)
	- [prometheus 测试应用](#prometheus-测试应用)
	- [tsdb-status 输出示例](#tsdb-status-输出示例)


### operator 部署及冒烟测试
#### 部署 prometheus stack
通过 [charts/kube-prometheus-stack](https://github.com/prometheus-community/helm-charts/tree/main/charts/kube-prometheus-stack) 安装。

```s
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts
helm repo update

helm install prometheus -n monitor prometheus-community/kube-prometheus-stack
```

我们看看 helm chart 都部署了哪些组件，可以看到除了 `prometheus-0` 之外，还部署了 alertmanager、grafana、kube-state-metrics、node-exporter。
```s
lr90@sj ~ % kubectl get pods -n monitor
NAME                                                     READY   STATUS    RESTARTS   AGE
alertmanager-prometheus-kube-prometheus-alertmanager-0   2/2     Running   0          61s
prometheus-grafana-6854b47bf4-ttg6c                      3/3     Running   0          11m
prometheus-kube-prometheus-operator-7f8d744cd7-64nk6     1/1     Running   0          11m
prometheus-kube-state-metrics-f699c577d-tk245            1/1     Running   0          11m
prometheus-prometheus-kube-prometheus-prometheus-0       2/2     Running   0          58s
prometheus-prometheus-node-exporter-9hft2                1/1     Running   0          11m
```
部署完成之后，可以访问 grafana 看一下，其中 grafana 用户 `admin` 的用户名密码在 `prometheus-grafana` secret 中，需要将 grafana 的 3000 端口 port-forward 到本地，port-forward 之后，就可以通过 127.0.0.1:3000 来访问 grafana 了。
```s
lr90@sj ~ % kubectl port-forward pod/prometheus-grafana-6854b47bf4-ttg6c -nmonitor 3000:3000
Forwarding from 127.0.0.1:3000 -> 3000
```
安装部署参考 prometheus 文档 [Installing Prometheus Operator](https://prometheus-operator.dev/docs/getting-started/installation/)

#### 部署测试组件 
这部分参考 prometheus-operator [getting-started](https://github.com/prometheus-operator/prometheus-operator/blob/main/Documentation/developer/getting-started.md)，

使用本文 *附录-> prometheus 测试应用* 中的 yaml 来部署测试应用，部署完成之后，使用 servicemonitor 来暴露应用的指标，在 servicemonitor 资源中，其 selector 选中的是 service 的 label。
```yaml
apiVersion: monitoring.coreos.com/v1
kind: ServiceMonitor
metadata:
  name: example-app
  labels:
    team: frontend
spec:
  selector:
    matchLabels:
      app: example-app
  endpoints:
  - port: web
```
附录中的测试应用暴露了 8080 端口，并且内置了 http_requests_total 指标，我们可以通过访问 8080 端口来增加这个指标的值。


配置 servicemonitor 之后，我们需要配置集群中的 `prometheus` cr，使其能够选择我们的 servicemonitor。可以将 serviceMonitorSelector 设置为空 `{}` 表示选择所有的 servicemonitor，配置完之后，可以在 grafana 的 explore 界面查看 metrics 了。
```yaml
  serviceMonitorSelector:
    matchLabels:
      team: frontend
```
测试用用产生的指标如下，其中 instance="10.244.0.14:8080" 表示的是 pod 的 ip 以及端口。
```html
http_requests_total{
	code="200",
	container="example-app",
	endpoint="web",
	instance="10.244.0.14:8080",
	job="example-app",
	method="get",
	namespace="default",
	pod="example-app-5ffc85cf6d-nb9g6",
	service="example-app"
}
```


### 指标拉取配置
#### servicemonitor relabel
servicemonitor 是最常用定义指标 target 的方式。这里介绍如何通过 servicemonitor 中的 relabel 来实现指标过滤。首先看几个例子：
1）丢弃 ip 为 10.244.0.18 的 pod 的指标；2）将 annotation env 添加到 metric 的 label 中。
```yaml
spec:
  endpoints:
  - interval: 30s
    path: /metrics
    port: web
    relabelings:
    - action: drop
      regex: 10\.244\.0\.18
      sourceLabels:
      - __meta_kubernetes_pod_ip
    - action: replace
      regex: (.+)
      sourceLabels:
      - __meta_kubernetes_pod_annotation_env
      targetLabel: environment      
```
在 prometheus 监控系统中，通过 drop 指标可以避免监控系统基数太高的问题，参考 [cardinality-analysis](https://grafana.com/docs/enterprise-metrics/v2.6.x/tenant-management/cardinality-analysis/)。
导致基数太高的 label 有 user_id、txn_id 等。如果 pod 的 name 作为 label，但是 pod 的生命周期很短，并且会频繁创建，也会导致基数过高。

上面的 __meta_kubernetes_pod_ip 以及 __meta_kubernetes_pod_annotation_env 是 prometheus 默认添加的元数据 label，参考 [kubernetes_sd_config](https://prometheus.io/docs/prometheus/latest/configuration/configuration/#kubernetes_sd_config)，这部分 tag 最终不会添加到 metrics 中。关于 relabel 的配置，可以参考文档 [relabel_config](https://prometheus.io/docs/prometheus/latest/configuration/configuration/#relabel_config)，在 relabel 中有比较常用的两个 action 是 keep(白名单)以及 drop(黑名单)，用来选择要保留的 metrics。

#### 其他 scrape 配置
除了使用 servicemonitor 之外，prometheus 还支持使用 podmonitor、scrapeConfigNamespaceSelector/scrapeConfigSelector、AdditionalScrapeConfigs，这里不再叙述。

### 指标数据存储
#### 数据目录结构
默认情况下，prometheus 的指标数据是存储在本地磁盘中的，数据目录是 /prometheus（或者是 /data），其目录结构如下。每隔两个小时，prometheus 会将采集的指标归档为一个目录，如下的 01JR7YGGGDDT5PZYP689PWCEZD，在这个目录中，有一些文件，其中：meta.json 存储的是元数据，包括该 block的开始时间、结束时间、时序数量、采样次数等；chunks 是指标按 512M 分组的一个的一个 segment；tombstones 是通过 api 被逻辑删除的采样；index 是采样指标的索引，用来查找 chunks 中的数据。
```html
/prometheus $ tree
.
├── 01JR7QMRYQ4MDK4J1QV57JWFC2
├── 01JR7YGFRQ3KZST9XHX457PBTV
├── 01JR7YGGGDDT5PZYP689PWCEZD
│   ├── chunks
│   │   └── 000001
│   ├── index
│   ├── meta.json
│   └── tombstones
├── chunks_head
│   ├── 000005
│   └── 000006
├── lock
├── queries.active
└── wal
    ├── 00000002
    ├── 00000003
    ├── 00000004
    ├── 00000005
    └── checkpoint.00000001
        └── 00000000
```
除了被归档在磁盘上的指标之外，prometheus 还将最近的 2 小时的数据保存在内存中（称为 head block），并通过 wal 来保证内存中数据的可靠性，当 prometheus 重启的时候，可以通过重放 wal 来恢复内存中的数据。wal 即上面目录中的 wal 目录，另外 chunks_head 目录是 head block 的内存 mmap 映射，能有效减少 prometheus 的内存使用。

#### 磁盘数据量预测

官方文档中提到了一个公式用来预测需要的磁盘空间，一般来说，可以认为每个采样使用 2bytes 数据量。
```s
needed_disk_space = retention_time_seconds * ingested_samples_per_second * bytes_per_sample
```
每秒的采样数可以通过下面 promQL 查询，那么假设每秒的采样数为 100000，并且数据量保留 15d，那么需要的磁盘空间为：`15*24*3600 * 100000 * 2` bytes，也就是 241GB 左右。
```s
rate(prometheus_tsdb_head_samples_appended_total[5m])
```

#### tsdb status
通过 prometheus 提供的 http api [TSDB Stats](https://prometheus.io/docs/prometheus/latest/querying/api/#tsdb-stats) 我们查看 tsdb 的 status，其输出内容如 *附录-tsdb status*。
```s
lr90@sj ~ % kubectl port-forward pod/prometheus-prometheus-kube-prometheus-prometheus-0 -n monitor 9090:9090
Forwarding from 127.0.0.1:9090 -> 9090
Forwarding from [::1]:9090 -> 9090

curl http://localhost:9090/api/v1/status/tsdb
```
输出解释如下：
* headStats: TSDB head block 的状态，包括：numSeries:指标时序的数量；chunkCount: chunk 的数量，一个 head block 包含 120 个采样点，大概占用 1.5KB 空间，可以根据此数据大概估计下 headblock 的内存使用； minTime/maxTime: headblock 开始/结束时间（毫秒时间戳）。
* seriesCountByMetricName: 按指标名称统计的序列数（包含所有block），是全局数据，不仅仅是 head block。
* labelValueCountByLabelName: 按指标名统计的时序数量。
* memoryInBytesByLabelName: 标签名称的内存占用。
* seriesCountByLabelPair: 按标签键值对统计的序列数。

tsdb status 能帮我们大概了解 headblock 的状态，以及整个 tsdb 指标统计数据，能够帮我们分析高基数 metric 以及高基数 label。

### 附录

#### prometheus 测试应用
```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: example-app
spec:
  replicas: 3
  selector:
    matchLabels:
      app: example-app
  template:
    metadata:
      labels:
        app: example-app
    spec:
      containers:
      - name: example-app
        image: quay.io/brancz/prometheus-example-app:v0.5.0
        ports:
        - name: web
          containerPort: 8080
---
kind: Service
apiVersion: v1
metadata:
  name: example-app
  labels:
    app: example-app
spec:
  selector:
    app: example-app
  ports:
  - name: web
    port: 8080   
```

#### tsdb-status 输出示例
```json
{
  "status": "success",
  "data": {
    "headStats": {
      "numSeries": 35213,
      "numLabelPairs": 4678,
      "chunkCount": 74340,
      "minTime": 1744077600015,
      "maxTime": 1744083107444
    },
    "seriesCountByMetricName": [
      {
        "name": "apiserver_request_body_size_bytes_bucket",
        "value": 2464
      },
      {
        "name": "apiserver_request_duration_seconds_bucket",
        "value": 2313
      },
      {
        "name": "etcd_request_duration_seconds_bucket",
        "value": 2268
      },
      {
        "name": "apiserver_request_sli_duration_seconds_bucket",
        "value": 1344
      },
      {
        "name": "apiserver_response_sizes_bucket",
        "value": 1280
      },
      {
        "name": "apiserver_watch_list_duration_seconds_bucket",
        "value": 1044
      },
      {
        "name": "apiserver_watch_cache_read_wait_seconds_bucket",
        "value": 812
      },
      {
        "name": "grafana_http_request_duration_seconds_bucket",
        "value": 533
      },
      {
        "name": "apiserver_watch_events_sizes_bucket",
        "value": 522
      },
      {
        "name": "apiserver_admission_controller_admission_duration_seconds_bucket",
        "value": 385
      }
    ],
    "labelValueCountByLabelName": [
      {
        "name": "__name__",
        "value": 1690
      },
      {
        "name": "name",
        "value": 562
      },
      {
        "name": "le",
        "value": 280
      },
      {
        "name": "type",
        "value": 147
      },
      {
        "name": "resource",
        "value": 145
      },
      {
        "name": "method",
        "value": 118
      },
      {
        "name": "handler",
        "value": 89
      },
      {
        "name": "id",
        "value": 64
      },
      {
        "name": "endpoint",
        "value": 58
      },
      {
        "name": "kind",
        "value": 58
      }
    ],
    "memoryInBytesByLabelName": [
      {
        "name": "__name__",
        "value": 1717702
      },
      {
        "name": "service",
        "value": 980816
      },
      {
        "name": "instance",
        "value": 875600
      },
      {
        "name": "id",
        "value": 661414
      },
      {
        "name": "namespace",
        "value": 646711
      },
      {
        "name": "endpoint",
        "value": 603621
      },
      {
        "name": "job",
        "value": 596381
      },
      {
        "name": "pod",
        "value": 565117
      },
      {
        "name": "resource",
        "value": 337527
      },
      {
        "name": "name",
        "value": 241314
      }
    ],
    "seriesCountByLabelValuePair": [
      {
        "name": "namespace=default",
        "value": 21008
      },
      {
        "name": "endpoint=https",
        "value": 20344
      },
      {
        "name": "service=kubernetes",
        "value": 20029
      },
      {
        "name": "job=apiserver",
        "value": 20026
      },
      {
        "name": "instance=172.18.0.2:6443",
        "value": 20025
      },
      {
        "name": "namespace=monitor",
        "value": 9033
      },
      {
        "name": "version=v1",
        "value": 7857
      },
      {
        "name": "component=apiserver",
        "value": 6375
      },
      {
        "name": "node=kind-control-plane",
        "value": 5664
      },
      {
        "name": "service=prometheus-kube-prometheus-kubelet",
        "value": 5526
      }
    ]
  }
}
```