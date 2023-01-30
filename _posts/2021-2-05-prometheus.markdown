---
layout:     post
title:      "Prometheus监控基础"
date:       2020-11-27 10:10:00
author:     "weak old dog"
header-img-credit: false
tags:
    - 监控
---

基于 kubelet 分析 `prometheus` 监控的使用以及实现，这里主要介绍 exporter 的使用，其官方文档为：[https://prometheus.io/docs/introduction/overview/](https://prometheus.io/docs/introduction/overview/)

#### 指标类型及使用
这里要参考官方文档[METRIC TYPES](https://prometheus.io/docs/concepts/metric_types/)，官方文档里，同时给出了每种指标类型的代码example，使用的时候可以参考。
* Gauge：表示可以任意增加或者减小的指标，比如内存使用量，气温等。
* Counter：表示一个一直增加的指标，比如总请求数等。
* Histogram：调用`Observe(float64)`对指标进行采样，同时将采集到的指标统计到预先配置的bucket中，在采集到的指标中，显示每个bucket的区间，以及每个区间中指标的个数。Histogram可以用来采集请求时延，以及返回数据大小等。比如
```s
# HELP prometheus_tsdb_compaction_chunk_range Final time range of chunks on their first compaction
# TYPE prometheus_tsdb_compaction_chunk_range histogram
prometheus_tsdb_compaction_chunk_range_bucket{le="100"} 0
prometheus_tsdb_compaction_chunk_range_bucket{le="400"} 0
prometheus_tsdb_compaction_chunk_range_bucket{le="1600"} 0
prometheus_tsdb_compaction_chunk_range_bucket{le="6400"} 0
prometheus_tsdb_compaction_chunk_range_bucket{le="25600"} 0
prometheus_tsdb_compaction_chunk_range_bucket{le="102400"} 0
prometheus_tsdb_compaction_chunk_range_bucket{le="409600"} 0
prometheus_tsdb_compaction_chunk_range_bucket{le="1.6384e+06"} 260
prometheus_tsdb_compaction_chunk_range_bucket{le="6.5536e+06"} 780
prometheus_tsdb_compaction_chunk_range_bucket{le="2.62144e+07"} 780
prometheus_tsdb_compaction_chunk_range_bucket{le="+Inf"} 780
prometheus_tsdb_compaction_chunk_range_sum 1.1540798e+09
prometheus_tsdb_compaction_chunk_range_count 780
```
Histogram除了提供了每个bucket中的指标总数，还提供了一个`sum`值，以及一个`count`值，前者是采集到的指标的数值的所有的和，后者是采集的次数。下面的Summary同样提供了sum以及count。

* Summary：Summary跟Histogram基本一致，应用场景也基本一致。不同的是，Summary显示的分位点的数值，Exporter这边在采样时进行了统计，以下面三个分位点为例，`{quantile="0.5"} 0.012352463`表示，50%的fsync时延都在0.012352463秒以内。
```s
# HELP prometheus_tsdb_wal_fsync_duration_seconds Duration of WAL fsync.
# TYPE prometheus_tsdb_wal_fsync_duration_seconds summary
prometheus_tsdb_wal_fsync_duration_seconds{quantile="0.5"} 0.012352463
prometheus_tsdb_wal_fsync_duration_seconds{quantile="0.9"} 0.014458005
prometheus_tsdb_wal_fsync_duration_seconds{quantile="0.99"} 0.017316173
prometheus_tsdb_wal_fsync_duration_seconds_sum 2.888716127000002
prometheus_tsdb_wal_fsync_duration_seconds_count 216
```
关于数据采集的一些疑问：

gauge类型的指标，不管什么时候设置，只要设置了一次，每次都能采集到，并且如果没有更改，采集到的数据是一致的，这个可以参考kubelet的监控：
```go
	// NodeName is a Gauge that tracks the ode's name. The count is always 1.
	NodeName = metrics.NewGaugeVec(
		&metrics.GaugeOpts{
			Subsystem:      KubeletSubsystem,
			Name:           NodeNameKey,
			Help:           "The node's name. The count is always 1.",
			StabilityLevel: metrics.ALPHA,
		},
		[]string{NodeLabelKey},
	)
```
`NodeName`这个指标是监控主机名字的，只在启动kubelet的时候设置一次，后面就不更新了。

#### 查询结果数据类型
根据查询数据的方式，查询结果可分为四种类型：
* Instant vector：查出来的数据都是瞬时的结果，每个采样指标只有一个值，有很多不同的采样指标，这些采样指标的时间戳都是一样的。
* Range vector：查出来的是一段时间范围内的数据，这段时间是需要通过中括号在查询语句中给出来的，比如：
```s
http_requests_total{job="prometheus"}[5m]
```
采集最近5分钟内，`job`标签为`prometheus`的所有http_requests_total指标。
* Scalar：一个浮点数。
* String：一个字符串。（没有用到）

关于prometheus的时序数列指标以及数据类型，[Understanding the Prometheus rate() function](https://www.metricfire.com/blog/understanding-the-prometheus-rate-function/)有两张图值的看一下：
![java-javascript](/img/in-post/monitor/prometheus_range.png){:height="60%" width="60%"}

上面是三个采样指标，指标名称都一样，但是标签不一样，也是通过三个不同的时序序列来表示的。横轴表示时间，上图显示了最近60秒的采样数据，每个指标都是一个序列。上面是一个`Range vector`，有一个时间范围。(**顺便有个思考，因为缺少实战，如果对上面foo求rate，那么每个指标序列应该都有一个平均值**)

![java-javascript](/img/in-post/monitor/prometheus_instant.png){:height="70%" width="70%"}

这个是一个`Instant vector`，只显示了指标`foo`在某一个时刻的值，但是有三个采样，表示不同的采样指标。这两个图很有代表性。

#### 聚合运算
Prometheus提供了一些聚合操作，关于聚合操作可以参考官方文档[Aggregation operators](https://prometheus.io/docs/prometheus/latest/querying/operators/#aggregation-operators)，目前有以下聚合操作：
* sum (calculate sum over dimensions)
* min (select minimum over dimensions)
* max (select maximum over dimensions)
* avg (calculate the average over dimensions)
* group (all values in the resulting vector are 1)
* stddev (calculate population standard deviation over dimensions)
* stdvar (calculate population standard variance over dimensions)
* count (count number of elements in the vector)
* count_values (count number of elements with the same value)
* bottomk (smallest k elements by sample value)
* topk (largest k elements by sample value)
* quantile (calculate φ-quantile (0 ≤ φ ≤ 1) over dimensions)

这些运算符可以聚合所有的指标，或者根据label聚合，根据label聚合时，有两个关键字`by`和`without`可选。使用方式如下：

`<aggr-op> [without|by (<label list>)] ([parameter,] <vector expression>)`

或者将`by`等放在后面。

`<aggr-op>([parameter,] <vector expression>) [without|by (<label list>)]`

`label list`是一组标签列表，不需要加引号。by我理解是按照label分组的意思（**后面使用的话，这个需要确认一下**），比如

```s
 sum by (application, group) (http_requests_total)
```
是按照label`application`以及`group`分组，相同的为一组。

我们在监控 Apiserver 时，有时候想要监控请求的 QPS，这个时候我们可以先用 rate 来计算每秒的 qps，然后按照资源（resource）以及方法（verb）来分组，来查看特定资源类型以及请求的 QPS。参考[A Deep Dive into Kubernetes Metrics — Part 4: The Kubernetes API Server](https://blog.freshtracks.io/a-deep-dive-into-kubernetes-metrics-part-4-the-kubernetes-api-server-72f1e1210770)，计算公式为：
```s
sum(rate(apiserver_request_count[5m])) by (resource,subresource,verb)
```
上面先通过 rate 对 5m 内的请求求平均值，这个平均值也就是一个 QPS，rate 的结果是一个 instant vector，每个 metrics 的 label 组合都是不一样的，然后 sum 的作用就是将这个 instant vector 按照 label 求和，根据哪些 label 由 by 语句指定，所以 sum 的结果也是一个 QPS，但是是根据label组合之后的 QPS。

#### 查询函数
看一个几个常用查询函数的使用
##### rate
`rate(v range-vector)`只作用于`range vector`，并且只适用于`counter`指标类型，用来计算每秒增长率，也就是每秒增长的个数，以下面为例：
```s
rate(http_requests_total{job="api-server"}[5m])
```
上面函数就5分钟内，平均每秒的增长数，计算方式就是这五分钟内增长的总数（当前总数-5分钟之前的总数），除以5分钟内的所有秒数。rate有个好处是能自动检测到指标重置（也就是组件重启），因为是counter类型，指标采集的数据是只增不减的，如果遇到下降的指标，表示组件重启了，rate在计算的时候，会将下降的指标累加之前的最大值，参考[Understanding the Prometheus rate() function](https://www.metricfire.com/blog/understanding-the-prometheus-rate-function/)。

##### irate
irate在计算时，只会考虑最近的两个采样点，并除以这里两个点的interval。假设有下面6个采样点，每个采样点的间隔是1分钟：
```s
0
60
120
600
720
780
```
那么rate的计算方式为：
```s
(780-0)/5/60 = 2.6/sec
```
irate的计算方式为：
```s
(780-720)/1/60 = 1/sec
```

##### histogram_quantile
`histogram_quantile(φ scalar, b instant-vector)` calculates the `φ-quantile (0 ≤ φ ≤ 1)` from the buckets b of a histogram. 

通过`rate()`函数来指定分位点计算的窗口，比如有个histogram指标称为`http_request_duration_seconds`，计算最近10分钟90分位点的表达式为：
```s
histogram_quantile(0.9, rate(http_request_duration_seconds_bucket[10m]))
```
另外可以通过by进行聚合，具体参考文档[histogram_quantile()](https://prometheus.io/docs/prometheus/latest/querying/functions/#histogram_quantile)


初始化`Histogram`类型的监控的时候，要给出bucket数组，其实就是一个`[]float64`，然后每采集到一个数据，就在对应的bucket中加1，`Histogram`相对于`Summary`能减轻exporter的压力，kubelet中都是`Histogram`没有使用`Summary`。然后就是**没有看到关于计算时间段的设置，那么应该计算的是从系统启动时的直方图**，如果我只想看最近五分钟内的采集数据的直方图，有没有办法呢？

参考：

[官方文档](https://prometheus.io/docs/introduction/overview/)

[How does a Prometheus Histogram work?](https://www.robustperception.io/how-does-a-prometheus-histogram-work)

[How to visualize Prometheus histograms in Grafana](https://grafana.com/blog/2020/06/23/how-to-visualize-prometheus-histograms-in-grafana/)

[Understanding the Prometheus rate() function](https://www.metricfire.com/blog/understanding-the-prometheus-rate-function/)

[irate() Vs rate() Functions in Prometheus](https://www.reddit.com/r/PrometheusMonitoring/comments/eyvsyl/irate_vs_rate_functions_in_prometheus/)