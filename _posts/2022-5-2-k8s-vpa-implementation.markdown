---
layout:     post
title:      "K8s vertical-pod-autoscaler 资源推荐算法"
date:       2022-5-2 10:10:00
author:     "decent"
header-img-credit: false
tags:
    - 扩缩容
---

**文章目录**

- [概述](#概述)
- [直方图与半衰指数直方图](#直方图与半衰指数直方图)
	- [直方图](#直方图)
	- [半衰指数直方图](#半衰指数直方图)
- [资源评估算法](#资源评估算法)
	- [cpu](#cpu)
	- [memory](#memory)
- [其他](#其他)


### 概述
本文研究下 [vertical-pod-autoscaler](https://github.com/kubernetes/design-proposals-archive/blob/main/autoscaling/vertical-pod-autoscaler.md) 的资源推荐算法是如何实现的，主要关注算法本身，有些业务和配置细节暂不涉及。

### 直方图与半衰指数直方图

#### 直方图

直方图是统计数学中的概念，在实现直方图时关注的也是其数学特性，比如：在某个 bucket 添加一个点，求分位点等。在 vpa 中，一个直方图的行为由下面接口定义。主要接口有：

**1.求分位点**：求分位点的语义为**给定一个分位点，比如 0.5，求这个分位点的采样值**，以 cpu 资源为例，如果 0.5 分位点的 cpu 使用为 2 核，意味着在采集周期内，50% 的时间 cpu 使用核数都超过了 2 核。 

那么如何计算分位点呢？

从第一个 bucket 开始累加概率，指导概率和大于等于分位点，那此时 bucket 的起始节点即预期的采样值。

**2.添加/删除样例数据**：对于直方图来说，有一个 bucket 列表，每个 bucket 是一个区间，按照采样值从小到大排列，**每个 bucket 的取值为落在 bucket 区间内的采样值的个数**，所以 bucket 的取值就是是一个 count，每有一个采样值落在这个区间，count 就 +1，在实际接口中，还有一个 weight 值，实际加的是 `1 * weight`，也就是 weight，这个是用于实现半衰指数直方图。

那么如何计算采样值应该落在哪个 bucket 呢？

对于线性直方图来说，假设每个 bucket 的大小为 0.5c，当前用了 3c，那么 bucket 为 `3/0.5==6`，也就是第 6 个 bucket。对于指数直方图，每个 bucket 的大小是按照一个比率的指数递增的，计算稍微复杂。

**3.保存/Load Checkpoint**:  Checkpoint 是直方图的一个快照，可以理解为将整个 bucket 数组都保存起来，并可以通过 Checkpoint 来恢复直方图。


具体来说，在 vpa 中，直方图定义的接口如下。
```go
type Histogram interface {
	Percentile(percentile float64) float64

	AddSample(value float64, weight float64, time time.Time)
	SubtractSample(value float64, weight float64, time time.Time)

	Merge(other Histogram)
	IsEmpty() bool
	Equals(other Histogram) bool
	String() string

	SaveToChekpoint() (*vpa_types.HistogramCheckpoint, error)
	LoadFromCheckpoint(*vpa_types.HistogramCheckpoint) error
}
```

#### 半衰指数直方图

半衰指数直方图是直方图的 pro 版本，有两个主要特性：
1）bucket 大小按照 ratio 指数增加

假设第一个 bucket 的大小为 `firstBucketSize`，bucket 增长比例为 ratio，那么第 n 个 bucket 的大小为 `firstBucketSize * ratio^n`。与线性直方图类似，计算分位点以及找出采样点的 bucket 操作也是通过累加 bucket 进行的，不过这里涉及到一些数学公式，课本上学的数学终于用上了！
  
2）bucket 权重含有半衰期

这个是指不同时间段内的采样指标权重不一样，离越近的指标权重越大，那这个是如何实现的呢？

在初始化直方图时，首先选定一个参考时间 `referenceTimestamp`，默认时 time.Now()，因此在添加指标的时候，假设指标的采样时间是 sampleTime，这个 referenceTimestamp 是早于 sampleTime 的，也就是 `sampleTime>referenceTimestamp`，计算指标权重是根据公式：

```s
weight * 2^((sampleTime - referenceTimestamp) / halfLife)
```
其中 halfLife 的默认值是 1 天，因为 referenceTimestamp 是过去固定的一个时间点，所以离当前时间越近，其 weight 越大。例如，在下面时间线中，T1 时间点的权重是小于 T2 时间点的权重的。其中基础 weight 固定为 0.1 或者 1。当 referenceTimestamp 离现在时间比较远的时候，比如超过了 100 天，则重新将当前时间设置为 referenceTimestamp。
```s
|referenceTimestamp------------T1-------T2--------Time.Now()|
```

### 资源评估算法
在理解了直方图的时间之后，资源评估算法就比容容易理解了，一般来说，就是根据直方图来求分位点。
#### cpu
对于 cpu 资源，分位点是由参数 targetCPUPercentile 控制，默认是 0.9。另外，在求了分位点之后，还需要加上一个 margin 的 buff，默认这个 margin 是 0.15。具体是这么计算的。
```s
0.9percentile * (1 + 0.15)
```
计算过程对应下面代码，比较容易理解。
```go
// GetCPUEstimation returns the CPU estimation for the given AggregateContainerState.
func (e *cpuMarginEstimator) GetCPUEstimation(
	s *model.AggregateContainerState
) model.ResourceAmount {
	base := e.baseEstimator.GetCPUEstimation(s)
	margin := model.ScaleResource(base, e.marginFraction)
	return base + margin
}
```

#### memory
对于 memory 资源，分位点是由参数 targetMemoryPercentile 控制，默认也是 0.9。其计算过程也跟 cpu 一致，不同的时，内存资源推荐还要考虑 oom 事件，当 oom 事件发生的时候，需要将 pod 所使用的内存作为采样加入到直方图中。

计算 memoryNeeded 的公式为：
```s

#oomBumpUpRatio   = flag.Float64("oom-bump-up-ratio", model.DefaultOOMBumpUpRatio, 
# `The memory bump up ratio when OOM occurred, default is 1.2.`)

#oomMinBumpUp  = flag.Float64("oom-min-bump-up-bytes", model.DefaultOOMMinBumpUp, 
#`The minimal increase of memory when OOM occurred in bytes, default is 100 * 1024 * 1024`)

max(memoryUsed + OOMMinBumpUp, memoryUsed * oomBumpUpRatio)
```
下面是具体的代码实现。

```go
// RecordOOM adds info regarding OOM event in the model as an artificial memory sample.
func (container *ContainerState) RecordOOM(timestamp time.Time, requestedMemory ResourceAmount) error {
	// Discard old OOM
	if timestamp.Before(container.WindowEnd.Add(-1 * GetAggregationsConfig().MemoryAggregationInterval)) {
		return fmt.Errorf("OOM event will be discarded - it is too old (%v)", timestamp)
	}
	// Get max of the request and the recent usage-based memory peak.
	// Omitting oomPeak here to protect against recommendation running too high on subsequent OOMs.
	memoryUsed := ResourceAmountMax(requestedMemory, container.memoryPeak)
	memoryNeeded := ResourceAmountMax(memoryUsed+MemoryAmountFromBytes(GetAggregationsConfig().OOMMinBumpUp),
		ScaleResource(memoryUsed, GetAggregationsConfig().OOMBumpUpRatio))

	oomMemorySample := ContainerUsageSample{
		MeasureStart: timestamp,
		Usage:        memoryNeeded,
		Resource:     ResourceMemory,
	}
	if !container.addMemorySample(&oomMemorySample, true) {
		return fmt.Errorf("adding OOM sample failed")
	}
	return nil
}
```

### 其他

与 hpa 的结合：最好不要作用于同一个资源 https://github.com/kubernetes/design-proposals-archive/blob/main/autoscaling/vertical-pod-autoscaler.md#combining-vertical-and-horizontal-scaling。
vpa 适用于有状态一个能用，hpa 适用于无状态应用。