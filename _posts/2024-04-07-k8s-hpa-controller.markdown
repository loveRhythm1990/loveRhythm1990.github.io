---
layout:     post
title:      "K8s hpa 控制器中的代码实现（二）"
date:       2024-04-07 10:10:00
author:     "decent"
header-img-credit: false
tags:
    - 扩缩容
---

**目录**
- [概述](#概述)
- [参数配置](#参数配置)
  - [horizontal-pod-autoscaler-sync-period](#horizontal-pod-autoscaler-sync-period)
  - [horizontal-pod-autoscaler-initial-readiness-delay](#horizontal-pod-autoscaler-initial-readiness-delay)
- [计算 desiredReplica](#计算-desiredreplica)
- [stabilizationWindowSeconds 配置](#stabilizationwindowseconds-配置)
- [附录](#附录)
  - [hpa 配置示例](#hpa-配置示例)

### 概述
在 K8s hpa 中，官方文档往往不能将问题解释清楚，需要的时候去研究一下源码实现，本文对 hpa 整体代码进行概览，并只关注核心逻辑（如：不关注控制器初始化）。

### 参数配置

#### horizontal-pod-autoscaler-sync-period
hpa 控制器执行的时间间隔，hpa 控制器是间歇性执行的。默认是 15s。这个参数对应控制器中的 `resyncPeriod` 变量，在初始化 hpa 控制器的时候，用于初始化控制器队列，

```go
hpaController := &HorizontalController{

    queue: workqueue.NewTypedRateLimitingQueueWithConfig(
        NewDefaultHPARateLimiter(resyncPeriod),
        workqueue.TypedRateLimitingQueueConfig[string]{
            Name: "horizontalpodautoscaler",
        },
    ),

}
```
上面的 NewDefaultHPARateLimiter 调用 NewFixedItemIntervalRateLimiter 初始化一个固定延迟的 rateLimiter，这个固定延迟就是 `resyncPeriod`，因此在控制器循环中，每个 hpa 在等待 resyncPeriod 之后才会被重新加入队列。
```go
func NewFixedItemIntervalRateLimiter(interval time.Duration) workqueue.TypedRateLimiter[string] {
	return &FixedItemIntervalRateLimiter{
		interval: interval,
	}
}
```

#### horizontal-pod-autoscaler-initial-readiness-delay
这个参数与参数 `horizontal-pod-autoscaler-cpu-initialization-period` 作用类似，用于判断一个 pod 是不是 Ready，如果一个 pod 正在初始化，则避免使用这个 pod 的监控指标数据，以免带来误差，因为  pod 在启动阶段的负载不具有参考性。

对于 `horizontal-pod-autoscaler-cpu-initialization-period`，其对应代码中的 `cpuInitializationPeriod`，在 pod 刚启动到 cpuInitializationPeriod 的这个时间段内，即使 pod 状态为 Ready，但是如果指标采集的不够，不够一个 metrics.Window，则仍认为 pod 还在启动阶段，是 unready 的。这个参数的默认值是 5 分钟。

对于 `horizontal-pod-autoscaler-initial-readiness-delay`，对应代码中的 `delayOfInitialReadinessStatus`，如果 pod 的 ready condition 是 false，并且转换时间没有超过这个参数，则认为是 unready 的。这个参数的默认值是 30s。

判断一个 pod 是不是 unready 的代码如下，在扩容时（scaleup），如果一个 pod 时 unready 的，则重新调整这个 pod 的资源利用率，设置为 0，这样计算出来的 desiredReplica 比较小，避免扩容过于激进。
```go
if resource == v1.ResourceCPU {
    var unready bool
    _, condition := podutil.GetPodCondition(&pod.Status, v1.PodReady)
    if condition == nil || pod.Status.StartTime == nil {
        unready = true
    } else {
        // Pod still within possible initialisation period.
        if pod.Status.StartTime.Add(cpuInitializationPeriod).After(time.Now()) {
            // Ignore sample if pod is unready or one window of metric wasn't collected since last state transition.
            unready = condition.Status == v1.ConditionFalse || metric.Timestamp.Before(condition.LastTransitionTime.Time.Add(metric.Window))
        } else {
            // Ignore metric if pod is unready and it has never been ready.
            unready = condition.Status == v1.ConditionFalse && pod.Status.StartTime.Add(delayOfInitialReadinessStatus).After(condition.LastTransitionTime.Time)
        }
    }
    if unready {
        unreadyPods.Insert(pod.Name)
        continue
    }
}
```

### 计算 desiredReplica
在 hpa 控制器中，计算副本数是通过 `calcPlainMetricReplicas` 方法进行的，这个方法的主要任务是拉取 pod 的 metrics，并根据下面公式计算新副本数：
```s
desiredReplicas = ceil[currentReplicas * ( currentMetricValue / desiredMetricValue )]
```
正常情况下，会直接返回上面上面公式计算出的副本数。初次之前，可能还存在一些异常情况：
* pod metrics 缺失：通过 pod label selector 能 list 到 pod，但是通过 metricsclient 拿到的 metrics 中，缺少部分 pod 的监控数据。
* unready pod: pod 的 phase 为 Pending，或者 Ready condition 为 false，或者 pod 正在启动阶段，参考上面的参数。
* ignore pod: 需要被忽略的 pod，包括正在被删除的 pod (DeletionTimestamp 不为 nil)，以及 Phase 为 Failed 的 pod。

在计算 desiredReplicas 时，会根据上面的异常情况对监控数据（pod metrics 数据）稍微进行调整，比如：在扩容时，认为 metrics 丢失的 pod 所使用的资源量为 0，这样计算出来的 usageRatio 会偏小，从而实现的效果是扩容会比较比较保守，这部分 corner case 比较多，不太好总结，建议需要的时候直接看代码。

```go
func (c *ReplicaCalculator) calcPlainMetricReplicas(
	metrics metricsclient.PodMetricsInfo, // pod 的指标，key 是 pod 的 name，value 是 pod 的指标
	currentReplicas int32, // 当前的副本数
	targetUsage int64, // 目标利用率
	tolerances Tolerances, // 容忍度，默认为 nil
	namespace string, // 命名空间
	selector labels.Selector, // pod selector
	resource v1.ResourceName, // 资源类型，假设为 cpu
) (replicaCount int32, usage int64, err error) {

	podList, _ := c.podLister.Pods(namespace).List(selector)

	// 根据上面的 podList，在 metrics 中获取 pod 的指标，并分类：
	// 未就绪的 pod，缺失 metrics的 pod，忽略的 pod
	readyPodCount, unreadyPods, missingPods, ignoredPods := groupPods(podList, metrics, resource, c.cpuInitializationPeriod, c.delayOfInitialReadinessStatus)
	removeMetricsForPods(metrics, ignoredPods)
	removeMetricsForPods(metrics, unreadyPods)

	// 计算指标的利用率: float64(currentUsage) / float64(targetUsage)
	// 如果 usageRatio > 1.0，则表示要扩容
	// 如果 usageRatio < 1.0，则表示要缩容
	usageRatio, usage := metricsclient.GetMetricUsageRatio(metrics, targetUsage)

	// 如果存在未就绪的 pod，并且计算出来的比例是需要扩容
	scaleUpWithUnready := len(unreadyPods) > 0 && usageRatio > 1.0
	if !scaleUpWithUnready && len(missingPods) == 0 {
		if tolerances.isWithin(usageRatio) {
			// 容忍度在 [1-tolerances.scaleDown, 1+tolerances.scaleUp] 之间，则认为当前的副本数是合适的
			return currentReplicas, usage, nil
		}

		// 如果没有任何未就绪的 pod 或者缺失 metrics 的 pod，则可以计算新的副本数
		// NOTE：这个是 NORMAL case
		return int32(math.Ceil(usageRatio * float64(readyPodCount))), usage, nil
	}

	if len(missingPods) > 0 {
		if usageRatio < 1.0 {
			// 在缩容时，认为 metrics 丢失的 pod 的资源使用情况，跟设置的 targetUsage
			// 在这种情况下，metrics 丢失的 pod 不会影响缩容比例
			for podName := range missingPods {
				metrics[podName] = metricsclient.PodMetric{Value: targetUsage}
			}
		} else if usageRatio > 1.0 {
			// 在扩容时，认为 metrics 丢失的 pod 所使用的资源量为 0，这样计算出来的 usageRatio 会偏小
			// 因此扩容会相对保守
			for podName := range missingPods {
				metrics[podName] = metricsclient.PodMetric{Value: 0}
			}
		}
	}

	if scaleUpWithUnready {
		// 在扩容时，认为未就绪的 pod 所使用的资源量为 0，这样计算出来的 usageRatio 会偏小
		// 因此扩容会相对保守
		for podName := range unreadyPods {
			metrics[podName] = metricsclient.PodMetric{Value: 0}
		}
	}

	// 根据上面的调整(missing pod 以及 unready pod)，重新计算 usageRatio
	newUsageRatio, _ := metricsclient.GetMetricUsageRatio(metrics, targetUsage)

	if tolerances.isWithin(newUsageRatio) || (usageRatio < 1.0 && newUsageRatio > 1.0) || (usageRatio > 1.0 && newUsageRatio < 1.0) {
		// 若符合一定的容忍度，则不进行扩缩容
		return currentReplicas, usage, nil
	}

	// 重新计算副本数
	newReplicas := int32(math.Ceil(newUsageRatio * float64(len(metrics))))
	if (newUsageRatio < 1.0 && newReplicas > currentReplicas) || (newUsageRatio > 1.0 && newReplicas < currentReplicas) {
		// 若符合一定的容忍度，则不进行扩缩容
		return currentReplicas, usage, nil
	}

	// 返回结果，副本数是根据 metrics 计算出来的副本数
	return newReplicas, usage, nil
}
```

### stabilizationWindowSeconds 配置
扩缩容存在抖动行为，在 hpa 中防止抖动的方式是配置 stabilizationWindowSeconds，配置方式参考 *附录 -> hpa 配置示例*，在代码中，处理此配置的是方法 stabilizeRecommendationWithBehaviors，这个方法首先计算两个时间区间 `[upCutoff, now]` 以及 `[downCufoff, now]`，cutoff 时间是当前时间减去 stabilizationWindowSeconds，然后遍历这个时间窗口内的历史推荐值，扩容时选择窗口内的`最小值`，缩容时选择窗口内的`最大值`，通过这种方式，能最大限度**减少副本数的变化**。
```go
// stabilizeRecommendationWithBehaviors:
// - replaces old recommendation with the newest recommendation,
// - returns {max,min} of recommendations that are not older than constraints.Scale{Up,Down}.DelaySeconds
func (a *HorizontalController) stabilizeRecommendationWithBehaviors(args NormalizationArg) (int32, string, string) {
	now := time.Now()

	foundOldSample := false
	oldSampleIndex := 0

	upRecommendation := args.DesiredReplicas
	upDelaySeconds := *args.ScaleUpBehavior.StabilizationWindowSeconds
	upCutoff := now.Add(-time.Second * time.Duration(upDelaySeconds))

	downRecommendation := args.DesiredReplicas
	downDelaySeconds := *args.ScaleDownBehavior.StabilizationWindowSeconds
	downCutoff := now.Add(-time.Second * time.Duration(downDelaySeconds))

	// Calculate the upper and lower stabilization limits.
	a.recommendationsLock.Lock()
	defer a.recommendationsLock.Unlock()
	for i, rec := range a.recommendations[args.Key] {
		if rec.timestamp.After(upCutoff) {
			upRecommendation = min(rec.recommendation, upRecommendation)
		}
		if rec.timestamp.After(downCutoff) {
			downRecommendation = max(rec.recommendation, downRecommendation)
		}
		if rec.timestamp.Before(upCutoff) && rec.timestamp.Before(downCutoff) {
			foundOldSample = true
			oldSampleIndex = i
		}
	}

	// Bring the recommendation to within the upper and lower limits (stabilize).
	recommendation := args.CurrentReplicas
	if recommendation < upRecommendation {
		recommendation = upRecommendation
	}
	if recommendation > downRecommendation {
		recommendation = downRecommendation
	}

	// Record the unstabilized recommendation.
	if foundOldSample {
		a.recommendations[args.Key][oldSampleIndex] = timestampedRecommendation{args.DesiredReplicas, time.Now()}
	} else {
		a.recommendations[args.Key] = append(a.recommendations[args.Key], timestampedRecommendation{args.DesiredReplicas, time.Now()})
	}

	// Determine a human-friendly message.
	var reason, message string
	if args.DesiredReplicas >= args.CurrentReplicas {
		reason = "ScaleUpStabilized"
		message = "recent recommendations were lower than current one, applying the lowest recent recommendation"
	} else {
		reason = "ScaleDownStabilized"
		message = "recent recommendations were higher than current one, applying the highest recent recommendation"
	}
	return recommendation, reason, message
}
```

### 附录
#### hpa 配置示例
```yaml
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: php-apache
spec:
  behavior:
    scaleDown:
      policies:
      - periodSeconds: 60
        type: Pods
        value: 1
      selectPolicy: Max
      stabilizationWindowSeconds: 300
    scaleUp:
      policies:
      - periodSeconds: 60
        type: Pods
        value: 1
      selectPolicy: Max
      stabilizationWindowSeconds: 180
  maxReplicas: 10
  metrics:
  - type: Resource
    resource:
      name: cpu
      target:
        type: Utilization
        averageUtilization: 50
  minReplicas: 2
  scaleTargetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: php-apache
```


