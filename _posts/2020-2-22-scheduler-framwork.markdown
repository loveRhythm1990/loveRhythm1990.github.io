---
layout:     post
title:      "K8s Scheduler framework 调度流程概述"
date:       2020-02-22 16:36:00
author:     "weak old dog"
header-img-credit: false
tags:
    - K8s
    - Scheduler
---

**目录**
- [概述](#概述)
- [扩展点](#扩展点)
	- [队列排序（Queue sort）](#队列排序queue-sort)
	- [预过滤（Pre-filter）](#预过滤pre-filter)
	- [过滤 （Filter）](#过滤-filter)
	- [Post-filter](#post-filter)
	- [计分（Scoring）](#计分scoring)
	- [Normalize scoring](#normalize-scoring)
	- [预留(Reserve)](#预留reserve)
	- [Permit](#permit)
	- [Approving a Pod binding](#approving-a-pod-binding)
	- [Pre-bind](#pre-bind)
	- [Bind](#bind)
	- [Post-bind](#post-bind)
	- [Unreserve](#unreserve)
- [Plugin API](#plugin-api)
- [Plugin Configuration](#plugin-configuration)
- [scheduler方法](#scheduler方法)


### 概述
Scheduling Framework定义了一些扩展点，让scheduler更容易定制化。k8s最新版本1.18 release中，已经使用了Scheduler Framework的形式，本文对着文档翻一下K8s的源代码。

Pod在调度框架中的调度过程被分为两个阶段：`Scheduling Cycle`以及`Binding Cycle`，Scheduling cycles是串行执行的，bind cycles是并行执行的。

下图显示了pod在调度时的scheduing context以及scheduling framework暴露的扩展点。在下图中，`Filter`等价于`Predicate`，`Scoring`等价于`Priority function`，一个插件可以注册多个扩展点来执行更复杂以及有状态的任务。

![java-javascript](/img/in-post/scheduler-framwork/scheduling-framework-extensions.png){:height="70%" width="70%"}

### 扩展点
扩展点就是一些可以定制化插件的地方，下面列举了每一个扩展点，其实在代码，对应Plugins结构体，代码路径在`pkg/scheduler/apis/config/types.go`，k8s的这个文档，也算是对这个结构体的说明。
```go
type Plugins struct {
	// QueueSort is a list of plugins that should be invoked when sorting pods in the scheduling queue.
	QueueSort *PluginSet
	// PreFilter is a list of plugins that should be invoked at "PreFilter" extension point of the scheduling framework.
	PreFilter *PluginSet
	// Filter is a list of plugins that should be invoked when filtering out nodes that cannot run the Pod.
	Filter *PluginSet
	// PreScore is a list of plugins that are invoked before scoring.
	PreScore *PluginSet
	// Score is a list of plugins that should be invoked when ranking nodes that have passed the filtering phase.
	Score *PluginSet
	// Reserve is a list of plugins invoked when reserving a node to run the pod.
	Reserve *PluginSet
	// Permit is a list of plugins that control binding of a Pod. These plugins can prevent or delay binding of a Pod.
	Permit *PluginSet
	// PreBind is a list of plugins that should be invoked before a pod is bound.
	PreBind *PluginSet
	// Bind is a list of plugins that should be invoked at "Bind" extension point of the scheduling framework.
	// The scheduler call these plugins in order. Scheduler skips the rest of these plugins as soon as one returns success.
	Bind *PluginSet
	// PostBind is a list of plugins that should be invoked after a pod is successfully bound.
	PostBind *PluginSet
	// Unreserve is a list of plugins invoked when a pod that was previously reserved is rejected in a later phase.
	Unreserve *PluginSet
}
```
#### 队列排序（Queue sort）
这些插件用来对调度队列中的pod进行排序，队列排序插件主要提供一个`Less(pod1, pod2)`函数，比较好理解，就是比较一下两个pod，并给出个顺序，只有调度时，只能有一个队列排序插件可以启用。

K8s默认的队列排序为`const Name = "PrioritySort"`，其是根据pod优先级对Pod排序的
```go
func (pl *PrioritySort) Less(pInfo1, pInfo2 *framework.QueuedPodInfo) bool {
	p1 := corev1helpers.PodPriority(pInfo1.Pod)
	p2 := corev1helpers.PodPriority(pInfo2.Pod)
	return (p1 > p2) || (p1 == p2 && pInfo1.Timestamp.Before(pInfo2.Timestamp))
}
```
K8s队列的实现为`PriorityQueue`，代码路径`pkg/scheduler/internal/queue`，是一个`堆`数据结构的实现，堆结构的比较函数Less，就是使用的上面的排序插件的Less函数。

在scheduler的`scheduleOne`方法中，每次调用`sched.NextPod`方法取一个Pod进行调度时，就是从`PriorityQueue`的ActiveQ中取一个Pod，此外PriorityQueue还有多个SubQueue，会专门研究一下。

#### 预过滤（Pre-filter）
预过滤插件用来预先处理pod的一些信息，或者检查集群或者pod是否满足特定的条件，如果预过滤插件返回错误，那么调度过程会终止。

以noderesources `Fit`插件为例，这个在`PreFilter`阶段会计算每个Pod的资源请求，计算好之后，写到`CycleState`缓存中。计算算好的资源请求，会在`Filter`阶段消费。
```go
func (f *Fit) PreFilter(ctx context.Context, cycleState *framework.CycleState, pod *v1.Pod) *framework.Status {
	cycleState.Write(preFilterStateKey, computePodResourceRequest(pod))
	return nil
}
```
写到这里，想到一个问题，对于没有设置Request以及Limit的Pod，也就是BestEffort的Pod，总是能调度成功，是不是在调度时，就认为这些Pod的资源请求为0？看了一下，果然是的，`computePodResourceRequest`就是累加了`container.Resources.Requests`，对于BestEffort类型的Pod，这些值都是0，可以通过kubectl get pod验证一下

#### 过滤 （Filter）
Filter插件用来过滤不能运行此pod的node，对于每个node，调度器会按照预先设置的循序调用每个filter plugins，如果任何一个filter插件返回这个节点不可用，那么剩下的插件就不会被执行了。在这个阶段**node的评估过程可以是并行的**。这个过程跟1.9版本的`Predicate`一致，不多说。

#### Post-filter
这是一个信息扩展点（information extension point），插件会在一系列通过filter插件的node上调用，插件可以用此扩展点来更新内部状态或者打印日志或者一些metric信息。

另外，希望执行`pre-scoring`工作的插件也可以在此执行，毕竟这个扩展点在`Scoring`之前。

#### 计分（Scoring）
用来给通过了filter插件的node打分，调度器会为每个node依次调用scoring插件。每个插件会给出一个介于最大值和最小值之间的分数。经过下面的`normalize scoring`阶段之后，调度器会根据每个插件的权重，以及打出的分数来和平所有scoring插件的分数。

#### Normalize scoring
归一化。

#### 预留(Reserve)
用来预留一些资源，这个是在Scheduler调用`Assume`之后调用的，`Assume`是将Pod加入到SchedulerCache中，这里是执行一些其他资源的预留，其实`Assume`将Pod加入SchedulerCache也是一种资源预留，以`volume_binding`插件为例，这个插件要在Assume Pod之后，进行Assume Volume，调用的方法为`AssumePodVolumes`，该方法做两件事：
* 将延迟binding的pvc，将这Filter阶段找到的静态pv加入到pv cache中，到了这一步，已经找好pre-binding的pvc了（否则Filter阶段就过不了）
* 对于需要动态provision的pvc，设置pvc的annotation `volume.kubernetes.io/selected-node`，表示调度到的目标节点。

#### Permit
Permit插件用来禁止或者延迟bind某个pod，Permit插件可以做这些事：
1. approve，如果所有的permit插件approve一个pod，那么这个pod开始执行binding
2. deny，如果任何一个permit否定了一个pod，那么这个pod会被重新放入到调度队列，这回导致执行下面的`Unreserve`插件。
3. wait(带超时时间)，如果一个permit插件返回了`wait`，那么这个pod会被停留在permit阶段，直到插件返回了`approve`，如果超时了，会被重新放回调度队列，并触发`Unreserve`插件。

Permit貌似主要实现一个co-scheduling的功能，要等待其他pod一起调度。

framework有一个waitpod缓存，应该是记录了需要wait的pod，`waitingPods *waitingPodsMap`，其中framework是general_scheduler的一个field，包含了很多plugin的集合，完整的framework代码如下：
```go
// framework is the component responsible for initializing and running scheduler
// plugins.
type framework struct {
	registry              Registry
	snapshotSharedLister  schedulerlisters.SharedLister
	waitingPods           *waitingPodsMap
	pluginNameToWeightMap map[string]int
	queueSortPlugins      []QueueSortPlugin
	preFilterPlugins      []PreFilterPlugin
	filterPlugins         []FilterPlugin
	preScorePlugins       []PreScorePlugin
	scorePlugins          []ScorePlugin
	reservePlugins        []ReservePlugin
	preBindPlugins        []PreBindPlugin
	bindPlugins           []BindPlugin
	postBindPlugins       []PostBindPlugin
	unreservePlugins      []UnreservePlugin
	permitPlugins         []PermitPlugin

	clientSet       clientset.Interface
	informerFactory informers.SharedInformerFactory
	volumeBinder    *volumebinder.VolumeBinder

	metricsRecorder *metricsRecorder

	// Indicates that RunFilterPlugins should accumulate all failed statuses and not return
	// after the first failure.
	runAllFilters bool
}
```

#### Approving a Pod binding
尽管所有的插件都可以访问cache中的`waiting`状态的pod，并且approve他们，我们期望只有permit 插件能做这件事情，一旦pod被approved，他会被送到pre-bind phase。

#### Pre-bind
pre-bind插件用来执行一些需要在bind之前做的操作。比如，一个pre-bind插件可以provision一个网络存储，并将其mount到目标节点，完成之后，才允许pod在那个节点执行。Pre-bind失败同样会返回调度队列。

#### Bind
用来将pod bind到node。在所有pre-bind执行完之前，bind插件是不会执行的，每个bind插件会按照预先设置的顺序执行，一个bind插件会选择是否处理给定的pod，如果一个bind插件选择执行一个pod，那么剩下的插件将会忽略，**也就是只有一个bind插件可以执行**

#### Post-bind
信息扩展点，Post-bind插件在pod成功bind之后执行（bind不是异步的吗？怎么知道成功不成功），这个是bind cycle的末尾，可以用来清理一些资源。

#### Unreserve
如果Pod被reserve了，并且后来被reject了，那么会触发unreserve插件，Unreserve插件会清除reserve pod相关的一些状态。**用此扩展点的插件一般也使用reserve插件，一般成对出现**

### Plugin API
Plugin API一般有两步，首先，plugin必须被注册以及配置，其次实现扩展点的接口，扩展点接口形式如下：
```go
type Plugin interface {
   Name() string
}

type QueueSortPlugin interface {
   Plugin
   Less(*v1.pod, *v1.pod) bool
}

type PreFilterPlugin interface {
   Plugin
   PreFilter(PluginContext, *v1.pod) error
}
```

### Plugin Configuration
可以在scheduler的配置中启用（或者禁用）plugins，在1.15中，没有默认的插件。调度器的配置也可以插件的配置，调度器在初始化插件的时候，会将配置传递给它们，配置是任意值，需要插件自己解析。

下面例子显示了一个调度器的配置，在配置中启用了`reserve`以及`preBind`扩展点，并禁用了一个reserve插件，并且提供了一个`foo`插件的配置。
```yaml
apiVersion: kubescheduler.config.k8s.io/v1alpha1
kind: KubeSchedulerConfiguration

...

plugins:
  reserve:
    enabled:
    - name: foo
    - name: bar
    disabled:
    - name: baz
  preBind:
    enabled:
    - name: foo
    disabled:
    - name: baz

pluginConfig:
- name: foo
  args: >
    Arbitrary set of args to plugin foo
```
当一个扩展点从配置中忽略时，那个扩展点的默认的插件会使用。配置了的时候，默认的和配置的都会使用，默认的会先执行，如果要调整顺序，需要把默认的也配置上。

k8s有一个获取默认配置的方法`getDefaultConfig`，通过这个方法返回默认的插件配置（不包括定制的插件），并生成scheduler，代码路径在`pkg/scheduler/algorithmprovider`，本来是放predicate以及priorities的地方。
```go
func getDefaultConfig() *schedulerapi.Plugins {
	return &schedulerapi.Plugins{
		QueueSort: &schedulerapi.PluginSet{
			Enabled: []schedulerapi.Plugin{
				{Name: queuesort.Name},
			},
		},
		PreFilter: &schedulerapi.PluginSet{
			Enabled: []schedulerapi.Plugin{
				{Name: noderesources.FitName},
				{Name: nodeports.Name},
				{Name: interpodaffinity.Name},
			},
		},
		Filter: &schedulerapi.PluginSet{
			Enabled: []schedulerapi.Plugin{
				{Name: nodeunschedulable.Name},
				{Name: noderesources.FitName},
				{Name: nodename.Name},
				{Name: nodeports.Name},
				{Name: nodeaffinity.Name},
				{Name: volumerestrictions.Name},
				{Name: tainttoleration.Name},
				{Name: nodevolumelimits.EBSName},
				{Name: nodevolumelimits.GCEPDName},
				{Name: nodevolumelimits.CSIName},
				{Name: nodevolumelimits.AzureDiskName},
				{Name: volumebinding.Name},
				{Name: volumezone.Name},
				{Name: interpodaffinity.Name},
			},
		},
		PreScore: &schedulerapi.PluginSet{
			Enabled: []schedulerapi.Plugin{
				{Name: interpodaffinity.Name},
				{Name: defaultpodtopologyspread.Name},
				{Name: tainttoleration.Name},
			},
		},
		Score: &schedulerapi.PluginSet{
			Enabled: []schedulerapi.Plugin{
				{Name: noderesources.BalancedAllocationName, Weight: 1},
				{Name: imagelocality.Name, Weight: 1},
				{Name: interpodaffinity.Name, Weight: 1},
				{Name: noderesources.LeastAllocatedName, Weight: 1},
				{Name: nodeaffinity.Name, Weight: 1},
				{Name: nodepreferavoidpods.Name, Weight: 10000},
				{Name: defaultpodtopologyspread.Name, Weight: 1},
				{Name: tainttoleration.Name, Weight: 1},
			},
		},
		Bind: &schedulerapi.PluginSet{
			Enabled: []schedulerapi.Plugin{
				{Name: defaultbinder.Name},
			},
		},
	}
}
```

### scheduler方法
就是调度pod的方法。
```go
// Schedule tries to schedule the given pod to one of the nodes in the node list.
// If it succeeds, it will return the name of the node.
// If it fails, it will return a FitError error with reasons.
func (g *genericScheduler) Schedule(ctx context.Context, state *framework.CycleState, pod *v1.Pod) (result ScheduleResult, err error) {
	trace := utiltrace.New("Scheduling", utiltrace.Field{Key: "namespace", Value: pod.Namespace}, utiltrace.Field{Key: "name", Value: pod.Name})
	defer trace.LogIfLong(100 * time.Millisecond)

	//这个basic检查pod所使用的pvc是否存在（从apiserver的get请求没有错），以及是否被删除
	if err := podPassesBasicChecks(pod, g.pvcLister); err != nil {
		return result, err
	}
	trace.Step("Basic checks done")

    	// 创建一个schedulercache以及nodeinfo的快照，在调度过程中使用这个快照的信息
	if err := g.Snapshot(); err != nil {
		return result, err
	}
	trace.Step("Snapshotting scheduler cache and node infos done")

	if g.nodeInfoSnapshot.NumNodes() == 0 {
		return result, ErrNoNodesAvailable
	}

	// Run "prefilter" plugins.
	// 执行prefilter插件，framework定义了运行插件的方法，其实现了FrameWork接口，
	// 变包含各个插件的列表
	preFilterStatus := g.framework.RunPreFilterPlugins(ctx, state, pod)
	if !preFilterStatus.IsSuccess() {
		return result, preFilterStatus.AsError()
	}
	trace.Step("Running prefilter plugins done")

	startPredicateEvalTime := time.Now()
	// findNodesThatFitPod包含两部分，一部分是RunFilterPlugins，一部分是运行scheduler extender
	// 的filter方法，看来extender虽然走网络很耗时，但是k8s 1.18还是保留了extender的设计
	filteredNodes, filteredNodesStatuses, err := g.findNodesThatFitPod(ctx, state, pod)
	if err != nil {
		return result, err
	}
	trace.Step("Computing predicates done")

	if len(filteredNodes) == 0 {
		return result, &FitError{
			Pod:                   pod,
			NumAllNodes:           g.nodeInfoSnapshot.NumNodes(),
			FilteredNodesStatuses: filteredNodesStatuses,
		}
	}

	// Run "prescore" plugins.
	// 运行PreScore插件
	prescoreStatus := g.framework.RunPreScorePlugins(ctx, state, pod, filteredNodes)
	if !prescoreStatus.IsSuccess() {
		return result, prescoreStatus.AsError()
	}
	trace.Step("Running prescore plugins done")

	metrics.DeprecatedSchedulingAlgorithmPredicateEvaluationSecondsDuration.Observe(metrics.SinceInSeconds(startPredicateEvalTime))
	metrics.DeprecatedSchedulingDuration.WithLabelValues(metrics.PredicateEvaluation).Observe(metrics.SinceInSeconds(startPredicateEvalTime))

	startPriorityEvalTime := time.Now()
	// When only one node after predicate, just use it.
	if len(filteredNodes) == 1 {
		metrics.DeprecatedSchedulingAlgorithmPriorityEvaluationSecondsDuration.Observe(metrics.SinceInSeconds(startPriorityEvalTime))
		return ScheduleResult{
			SuggestedHost:  filteredNodes[0].Name,
			EvaluatedNodes: 1 + len(filteredNodesStatuses),
			FeasibleNodes:  1,
		}, nil
	}

   	// 运行ScorePlugin，在这部分代码中没有发现Normalize插件，framework中也没有类似的方法
	priorityList, err := g.prioritizeNodes(ctx, state, pod, filteredNodes)
	if err != nil {
		return result, err
	}

	metrics.DeprecatedSchedulingAlgorithmPriorityEvaluationSecondsDuration.Observe(metrics.SinceInSeconds(startPriorityEvalTime))
	metrics.DeprecatedSchedulingDuration.WithLabelValues(metrics.PriorityEvaluation).Observe(metrics.SinceInSeconds(startPriorityEvalTime))

	host, err := g.selectHost(priorityList)
	trace.Step("Prioritizing done")

	return ScheduleResult{
		SuggestedHost:  host,
		EvaluatedNodes: len(filteredNodes) + len(filteredNodesStatuses),
		FeasibleNodes:  len(filteredNodes),
	}, err
}
```
