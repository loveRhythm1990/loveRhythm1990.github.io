---
layout:     post
title:      "K8s1.9 版本 Scheduler 调度流程概述"
date:       2020-02-28 14:46:00
author:     "weak old dog"
header-img-credit: false
tags:
    - K8s
    - Scheduler
---
**目录**
- [初始化 Scheduler](#初始化-scheduler)
- [scheduleOne](#scheduleone)
- [schedule](#schedule)
- [priority 过程](#priority-过程)


k8s 1.9 scheduler概述，因为之前工作中是基于1.9版本的，还没有引入scheduler framework。

### 初始化 Scheduler
初始化scheduler主要涉及两个结构体: `SchedulerServer`与`Scheduler`，也就是构造这个两个结构体，然后调用`run`方法。初始化SchedulerServer的代码在`plugin/cmd/kube-scheduler/app/server.go`

初始化的SchedulerServer默认有两个参数，即结构体的SchedulerName被设置为`default-scheduler`，AlgorithmSource被设置为`Provider:DefaultProvider`，policy被设置为null，

在SchedulerServer的run方法中，会生成一个Config配置，并用这个配置初始化一个Scheduler，其实Scheduler结构体只有Config这一个field，这个Config就是一个Scheduler的全部配置，其实现如下：
```go
// Scheduler watches for new unscheduled pods. It attempts to find
// nodes that they fit on and writes bindings back to the api server.
type Scheduler struct {
	config *Config
}
```
```go
// Config is an implementation of the Scheduler's configured input data.
// TODO over time we should make this struct a hidden implementation detail of the scheduler.
type Config struct {
	// It is expected that changes made via SchedulerCache will be observed
	// by NodeLister and Algorithm.
	SchedulerCache schedulercache.Cache
	// Ecache is used for optimistically invalid affected cache items after
	// successfully binding a pod
	Ecache     *core.EquivalenceCache
    NodeLister algorithm.NodeLister
    //就是general scheduler
	Algorithm  algorithm.ScheduleAlgorithm
	Binder     Binder
	// PodConditionUpdater is used only in case of scheduling errors. If we succeed
	// with scheduling, PodScheduled condition will be updated in apiserver in /bind
	// handler so that binding and setting PodCondition it is atomic.
	PodConditionUpdater PodConditionUpdater

	// PodPreemptor is used to evict pods and update pod annotations.
	PodPreemptor PodPreemptor

	// NextPod should be a function that blocks until the next pod
	// is available. We don't use a channel for this, because scheduling
	// a pod may take some amount of time and we don't want pods to get
	// stale while they sit in a channel.
	NextPod func() *v1.Pod

	// WaitForCacheSync waits for scheduler cache to populate.
	// It returns true if it was successful, false if the controller should shutdown.
	WaitForCacheSync func() bool

	// Error is called if there is an error. It is passed the pod in
	// question, and the error
	Error func(*v1.Pod, error)

	// Recorder is the EventRecorder to use
	Recorder record.EventRecorder

	// Close this to shut down the scheduler.
	StopEverything chan struct{}

	// VolumeBinder handles PVC/PV binding for the pod.
	VolumeBinder *volumebinder.VolumeBinder
}
```

生成Config的方法是`SchedulerServer SchedulerConfig()`，其主要代码如下（我们忽略了Policy的逻辑，因为默认调度器是使用defaultprovider初始化的）：

```go
    // 生成一个配置器，这个配置器从config中生成一个scheduler
    // 基本上，我们看到生成配置器的一大堆参数是一些Informer，在生成配置器的时候，首要任务就是给这些event添加EventHandler，
    // 资源比较多，涉及到的业务也听过的，以pod为例，先过滤未调度的、已经Terminated的pod，以及调度器不是本调度的pod，然后将pod加入到调度队列中。（pod还有一个已经调度的pod的event handler，这是用来更新缓存的，稍后再分析）
	configurator := factory.NewConfigFactory(
		s.SchedulerName,
		s.Client,
		s.InformerFactory.Core().V1().Nodes(),
		s.PodInformer,
		s.InformerFactory.Core().V1().PersistentVolumes(),
		s.InformerFactory.Core().V1().PersistentVolumeClaims(),
		s.InformerFactory.Core().V1().ReplicationControllers(),
		s.InformerFactory.Extensions().V1beta1().ReplicaSets(),
		s.InformerFactory.Apps().V1beta1().StatefulSets(),
		s.InformerFactory.Core().V1().Services(),
		s.InformerFactory.Policy().V1beta1().PodDisruptionBudgets(),
		storageClassInformer,
		s.HardPodAffinitySymmetricWeight,
		utilfeature.DefaultFeatureGate.Enabled(features.EnableEquivalenceClassCache),
	)
    // 基本上，上面初始化时，都是在给informer添加eventHandler。
	source := s.AlgorithmSource
	var config *scheduler.Config
	switch {
	case source.Provider != nil:
        // Create the config from a named algorithm provider.
        // 这里就是根据provider生成scheduler的全部了，这个地方涉及到很多全局变量
        // 这里的default provider也是在init()方法中生成的，并注册到全局变量algorithmProviderMap中，这是一个map
        // 目前 predicate priority也是在init方法中注册到全局变量map中的

        // 在CreateFromProvider的最后，这个configurator调用CreateFromKeys创建scheduler，基本上就是利用
        // provider的predicate, priority生成了配置
		sc, err := configurator.CreateFromProvider(*source.Provider)
		if err != nil {
			return nil, fmt.Errorf("couldn't create scheduler using provider %q: %v", *source.Provider, err)
		}
        config = sc
        // ..忽略source.Policy逻辑
    }
    
    // Additional tweaks to the config produced by the configurator.
	config.Recorder = s.Recorder
	return config, nil
```


### scheduleOne
运行scheduler的代码在`plugin/pkg/scheduler/scheduler.go`，如下
```go
// Run begins watching and scheduling. It waits for cache to be synced, then starts a goroutine and returns immediately.
func (sched *Scheduler) Run() {
	if !sched.config.WaitForCacheSync() {
		return
	}

	if utilfeature.DefaultFeatureGate.Enabled(features.VolumeScheduling) {
		go sched.config.VolumeBinder.Run(sched.bindVolumesWorker, sched.config.StopEverything)
	}

    // 第二个参数period为0，说明scheduleOne方法是一停不停运行的，也就是调度完一个pod，接着调度下一个pod
	go wait.Until(sched.scheduleOne, 0, sched.config.StopEverything)
}
```

调度pod入口，schedulerOne代码如下，只贴了重要部分的代码：
```go
// scheduleOne does the entire scheduling workflow for a single pod.  It is serialized on the scheduling algorithm's host fitting.
// scheduleOne方法是串行的
func (sched *Scheduler) scheduleOne() {

    // 从队列中取一个pod
	pod := sched.config.NextPod()

	// Synchronously attempt to find a fit for the pod.
	var suggestedHost string

    // 调度pod，成功返回节点，不成功返回错误，出错会进行错误处理，包括日志，以及event，这里忽略
    // 有一点需要注意的是，schedule pod之前会将所有的nodeInfo（scheduler cache的一部分信息）做一个深拷贝，供调度pod时的
    // predicate以及priority使用，所以在调度pod过程中，使用的是nodeInfo的一个镜像
	suggestedHost, scheduleErr := sched.schedule(pod)

    // 在bind之前，将pod加入到schedulerCache中，这样下一个pod在调度时，就能看到此pod的调度信息，
    // 下一个pod调度时，不用等待从apiserver同步pod信息
    // 另一方面，加入到cache，实际上是加入到schedulerCache的nodeinfo信息中，加入后，也会累加这个node上
    // 已经请求的资源，
	assumedPod := pod.DeepCopy()

	err = sched.assumeAndBindVolumes(assumedPod, suggestedHost)
	if err != nil {
		return
	}

	// 加入scheduleCache缓存
	err = sched.assume(assumedPod, suggestedHost)
	if err != nil {
		return
	}

	// update location annotation of PVs in this pod, indicate these PVs bind to this node
	if err = sched.updateVolumeLocation(assumedPod, suggestedHost, isPreemptHost); err != nil {
		glog.Errorf("scheduler update pv annotation err:%v", err)
		return
	}

    // 异步bind，可以异步bind的原因是，在上面的assume pod中已经将pod加入到scheduleCache缓存，
    // 同样，bind可能出错，出错时，会将pod从schedulerCache中删除，调用的是SchedulerCache.ForgetPod方法
	go func() {
		err := sched.bind(assumedPod, &v1.Binding{
			ObjectMeta: metav1.ObjectMeta{Namespace: assumedPod.Namespace, Name: assumedPod.Name, UID: assumedPod.UID},
			Target: v1.ObjectReference{
				Kind: "Node",
				Name: suggestedHost,
			},
		})
		if err != nil {
			glog.Errorf("Internal error binding pod: (%v)", err)
		}
	}()
}
```

### schedule
同样是只看重要的方法，忽略部分代码
```go
func (g *genericScheduler) Schedule(pod *v1.Pod, nodeLister algorithm.NodeLister) (string, error) {
	trace := utiltrace.New(fmt.Sprintf("Scheduling %s/%s", pod.Namespace, pod.Name))
	defer trace.LogIfLong(100 * time.Millisecond)

    // nodeInformer的list方法，即从缓存中拿的
    // 这个nodeInformer同时有Add/Update/Delete事件处理函数，用于更新scheduleCache缓存
    // 那么可以认为这里list到的数据，跟scheduleCache中的数据是一致的
	nodes, err := nodeLister.List()
	if err != nil {
		return "", err
	}
	if len(nodes) == 0 {
		return "", ErrNoNodesAvailable
	}

    // 每次调度pod前，都会将schedulerCache更新到generalScheduler的cachedNodeInfoMap字段
    // 是一个深拷贝
	err = g.cache.UpdateNodeNameToInfoMap(g.cachedNodeInfoMap)
	if err != nil {
		return "", err
	}

	filteredNodes, failedPredicateMap, err := findNodesThatFit(pod, g.cachedNodeInfoMap, nodes, g.predicates, g.extenders, g.predicateMetaProducer, g.equivalenceCache, g.schedulingQueue, g.alwaysCheckAllPredicates)
	if err != nil {
		return "", err
	}

    // 如果filteredNodes长度为0，返回错误，这里忽略
    // 如果filteredNodes长度为1，不进行priority

    metaPrioritiesInterface := g.priorityMetaProducer(pod, g.cachedNodeInfoMap)
    
    // 返回的结果是一个slice，slice的元素是每个node的名字，以及这个node的分数
	priorityList, err := PrioritizeNodes(pod, g.cachedNodeInfoMap, metaPrioritiesInterface, g.prioritizers, filteredNodes, g.extenders)
	if err != nil {
		return "", err
	}

    // 从这个slice中选择一个node，分数相同的可能有好多个节点，这好多个节点是以轮询的方式选择的
	return g.selectHost(priorityList)
}
```
### priority 过程
这个过程**并行**的运行priority函数，每个priority返回0-10之间的数，并且每个priority是有权重的。
要想看一下这个Priority过程，我们得看一下Priority的构造过程，这个函数的参数为`[]algorithm.PriorityConfig`，即一个PriorityConfig的Slice，其定义如下：
```go
type PriorityConfig struct {
	Name   string
    Map    PriorityMapFunction
	Reduce PriorityReduceFunction
	// TODO: Remove it after migrating all functions to Map-Reduce pattern.
	Function PriorityFunction
	Weight   int
}

type HostPriority struct {
	// Name of the host
	Host string
	// Score associated with the host
	Score int
}

// 输入为一个pod以及一个nodeInfo，输出为这个node的名字自己评分，普通的priority就是这种格式
type PriorityMapFunction func(pod *v1.Pod, meta interface{}, nodeInfo *schedulercache.NodeInfo) (schedulerapi.HostPriority, error)

// Reduce的作用是原地修改HostPriorityList，这是一个[]HostPriority类型，类似于做归一化，只不过
// 这里做的是`归十化`，目前在代码中没有使用到这个函数
type PriorityReduceFunction func(pod *v1.Pod, meta interface{}, nodeNameToInfo map[string]*schedulercache.NodeInfo, result schedulerapi.HostPriorityList) error

// 输入是pod，所有node列表，输出是所有node针对这个priority评分，目前用的也不多
type PriorityFunction func(pod *v1.Pod, nodeNameToInfo map[string]*schedulercache.NodeInfo, nodes []*v1.Node) (schedulerapi.HostPriorityList, error)
```
下面是PrioritizeNodes方法，最终返回所有node的分数
```go
func PrioritizeNodes(
	pod *v1.Pod,
	nodeNameToInfo map[string]*schedulercache.NodeInfo,
	meta interface{},
	priorityConfigs []algorithm.PriorityConfig,
	nodes []*v1.Node,
	extenders []algorithm.SchedulerExtender,
) (schedulerapi.HostPriorityList, error) {

	var (
		mu   = sync.Mutex{}
		wg   = sync.WaitGroup{}
		errs []error
	)
	appendError := func(err error) {
		mu.Lock()
		defer mu.Unlock()
		errs = append(errs, err)
	}

    // 初始化一个二维的Slice，第一维表示priority的编号，第二维表示所有node针对这个priority的评分
	results := make([]schedulerapi.HostPriorityList, len(priorityConfigs), len(priorityConfigs))

	for i, priorityConfig := range priorityConfigs {
		if priorityConfig.Function != nil {
            // DEPRECATED
            // 这个priority的Function不为nil，根据Function函数的定义，这个函数的输入为
            // 一个pod，及所有的node，然后返回这些所有node针对这个priority的评分
			wg.Add(1)
			go func(index int, config algorithm.PriorityConfig) {
				defer wg.Done()
                var err error
                // 返回所有node针对这个Function的评分，是所有的Config.Function是并发执行的
				results[index], err = config.Function(pod, nodeNameToInfo, nodes)
				if err != nil {
					appendError(err)
				}
			}(i, priorityConfig)
		} else {
            // 初始化所有的node的评分为0
			results[i] = make(schedulerapi.HostPriorityList, len(nodes))
			for index, node := range nodes {
				results[i][index] = schedulerapi.HostPriority{
					Host:  node.Name,
					Score: 0,
				}
			}
		}
    }
    
    // 执行所有map函数
	processNode := func(index int) {
		nodeInfo := nodeNameToInfo[nodes[index].Name]
        var err error
        // 根据这个for循环来看，对于一个node，所有的map是顺序执行的，
        // 但是所有的node是并行执行的
		for i := range priorityConfigs {
			if priorityConfigs[i].Function != nil {
				continue
            }
            // map是针对单个node的，只是返回这个node的分数
			results[i][index], err = priorityConfigs[i].Map(pod, meta, nodeInfo)
			if err != nil {
				appendError(err)
				return
			}
		}
    }
    
    // 对于所有的node，并行执行所有的map
    workqueue.Parallelize(16, len(nodes), processNode)
    
    //这里是执行reduce
	for i, priorityConfig := range priorityConfigs {
		if priorityConfig.Reduce == nil {
			continue
		}
        wg.Add(1)
        // 并行执行所有的reduce
		go func(index int, config algorithm.PriorityConfig) {
			defer wg.Done()
			if err := config.Reduce(pod, meta, nodeNameToInfo, results[index]); err != nil {
				appendError(err)
			}
		}(i, priorityConfig)
	}
    
    // Wait for all computations to be finished.
	wg.Wait()
	if len(errs) != 0 {
		return schedulerapi.HostPriorityList{}, errors.NewAggregate(errs)
	}

    // 对于每个node，累加所有priority的分数，这样一个node只有一个分数
	result := make(schedulerapi.HostPriorityList, 0, len(nodes))
	for i := range nodes {
        result = append(result, schedulerapi.HostPriority{Host: nodes[i].Name, Score: 0})
        // 累加分数，有权重的乘以权重
		for j := range priorityConfigs {
			result[i].Score += results[j][i].Score * priorityConfigs[j].Weight
		}
	}

    // 调用extender的priority方法
	if len(extenders) != 0 && nodes != nil {
		combinedScores := make(map[string]int, len(nodeNameToInfo))
		for _, extender := range extenders {
			wg.Add(1)
			go func(ext algorithm.SchedulerExtender) {
				defer wg.Done()
				prioritizedList, weight, err := ext.Prioritize(pod, nodes)
				if err != nil {
					// Prioritization errors from extender can be ignored, let k8s/other extenders determine the priorities
					return
				}
				mu.Lock()
				for i := range *prioritizedList {
					host, score := (*prioritizedList)[i].Host, (*prioritizedList)[i].Score
					combinedScores[host] += score * weight
				}
				mu.Unlock()
			}(extender)
		}
		// wait for all go routines to finish
		wg.Wait()
		for i := range result {
			result[i].Score += combinedScores[result[i].Host]
		}
	}
	return result, nil
}
```
在Priority这块看来，Config.Function用的不多，Reduce用的好像也不多，平常扩展的话，只要添加一个map函数就可以了，然后注册到defaultPriority，default AlgorithmProvider再根据defaultPriority生成scheduler。

关于调度，这里需要注意的是，scheduler顺序调度pod，调度pod时，使用的nodeInfo是scheduleCache中的一个快照，所有node是并行执行predicate的，但是对于每个node，predicate是顺序执行的，这样有一个predicate失败，剩下的就不执行了（短路参数设置为true）。在Priority中，每个priority Function是并行执行的。对于map，每个node是并行执行的，但是map 函数是顺序执行的。下一篇文章会看一下scheduler中的ScheduleCache是如何维护的。