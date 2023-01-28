---
layout:     post
title:      "K8s Sts 控制器概述"
subtitle:   " \"关于一个方法的概述\""
date:       2019-09-23 19:16:00
author:     "weak old dog"
header-img-credit: false
tags:
    - K8s
    - Statefulset
---

### 前言
在我看来，k8s的statefulset控制器，算是controller-manager中比较重要的一个了（是不是最重要的一个呢？），鉴于也是一块硬骨头，只能一点一点啃，能一次写完整是极好的，但是鉴于精力与能力不足，一点点写也是不错的，而且对我自身来说，也是非常有效的。[k8s文档](https://kubernetes.io/docs/concepts/workloads/controllers/statefulset/)中对于statefulset的特定描述为：
* 稳定的、唯一的网络标识符
* 稳定的、持久的存储
* 有序的、优雅的部署和缩放
* 有序的、自动的滚动升级

sts控制器的入口为: `pkg/controller/statefulset/stateful_set.go`，函数为 `NewStatefulSetController`
代码主要分三部分：
* stateful_set，对应文件`pkg/controller/statefulset/stateful_set.go`
* stateful_set_control，对应文件`pkg/controller/statefulset/stateful_set_control.go`
* stateful_pod_control，对应文件`pkg/controller/statefulset/stateful_pod_control.go`

### statefulset controller的初始化函数`NewStatefulSetController`
首先是注册pod、statefulset资源的事件处理函数，事件处理器都是stateful_set要干的活，代码在其对应的文件中。

pod的事件处理函数基本没有业务逻辑，基本是取出pod对应的statefulset，然后调用`ssc.enqueueStatefulSet`将sts放入待处理队列。

statefulset的事件处理函数就比较简单了，就是直接调用`ssc.enqueueStatefulSet`将sts加入到待循环队列。

这个函数最终返回一个`StatefulSetController`结构体，如下：
```go
type StatefulSetController struct {
	// 向apiserver发请求的client
	kubeClient clientset.Interface
	// 实现sts控制器的核心逻辑，用来更新sts以及其所包含的pod，抽象出来为了方便测试
	// sts控制器的sync方法中，主要逻辑就是调用了这个接口的 UpdateStatefulSet方法
	control StatefulSetControlInterface
	// 用来操作pod的接口，包括：CreatePods/CreatePodsOnNode/DeletePod/PatchPod方法等
	podControl controller.PodControlInterface
	// podLister is able to list/get pods from a shared informer's store
	podLister corelisters.PodLister
	// podListerSynced returns true if the pod shared informer has synced at least once
	podListerSynced cache.InformerSynced
	setLister appslisters.StatefulSetLister
	setListerSynced cache.InformerSynced
	pvcListerSynced cache.InformerSynced
	// revListerSynced returns true if the rev shared informer has synced at least once
	revListerSynced cache.InformerSynced
	// 用来存放待更新sts的队列，一般pod或者sts有变动，直接往这个队列里扔就对了
	queue workqueue.RateLimitingInterface
}
```

下面来看`StatefulSetController`结构体的`Run`方法:`go wait.Until(ssc.worker, time.Second, stopCh)`，就是启动一个工作线程不停执行。worker线程的调用链为：

worker() --> processNextWorkItem() --> sync() --> syncStatefulSet(set, pods) --> control.UpdateStatefulset(set, pods)

过程就是取出队列中的所有sts，并针对每个sts，代用sync方法，最终调用了`StatefulSetControlInterface`接口的`UpdateStatefulset`方法，所以，后者就是整个过程的核心了。

### StatefulSetControlInterface的UpdateStatefulset方法
首先看一下这个方法的注释：
> UpdateStatefulset是sts的核心循环逻辑，执行可预测的，默认单调的更新策略：scale up时是升序的，当前一个pod是unhealthy时，后一个pod是不会创建的。pod终止时是降序的。在burst策略下，pod的创建以及删除是无序的。

这个方法有一个参数pods，是在sync方法中拿到的属于该sts的pod，这个方法的方法体如下：
```go
func (ssc *defaultStatefulSetControl) UpdateStatefulSet(set *apps.StatefulSet, pods []*v1.Pod) error {

	// 拿到这个sts包含的所有revision
	revisions, err := ssc.ListRevisions(set)
	if err != nil {
		return err
	}
	// 将这个sts的所有controllerrevision按照其Revision字段排序，升序，也就是版本从旧到新排序
	history.SortControllerRevisions(revisions)

	// 获取当前的，以及update的revision，如果是sts的update事件，可能会创建新的revision
	currentRevision, updateRevision, collisionCount, err := ssc.getStatefulSetRevisions(set, revisions)
	if err != nil {
		return err
	}

	// 执行sts主要的update逻辑，并返回sts的status
	status, err := ssc.updateStatefulSet(set, currentRevision, updateRevision, collisionCount, pods)
	if err != nil {
		return err
	}

	// update the set's status
	err = ssc.updateStatefulSetStatus(set, status)
	if err != nil {
		return err
	}
	// 忽略日志
	// maintain the set's revision history limit
	return ssc.truncateHistory(set, pods, revisions, currentRevision, updateRevision)
}
```
可以看到还是涉及到了很多概念：revision(CurrentRevision/UpdateRevision)，Replicas，ReadyReplicas，CurrentReplicas，UpdatedReplicas。稍微解释一下

#### revision与replicas字段
`revision`翻译过来是`版本`，在k8s里面，也是这个意思，在sts的status中，有下面字段:
```go
	// 副本数
	Replicas int32 `json:"replicas" protobuf:"varint,2,opt,name=replicas"`

	// ready状态的副本数
	ReadyReplicas int32 `json:"readyReplicas,omitempty" protobuf:"varint,3,opt,name=readyReplicas"`

	// 使用currentRevision的副本数
	CurrentReplicas int32 `json:"currentReplicas,omitempty" protobuf:"varint,4,opt,name=currentReplicas"`

	// 使用updateRevision的副本数
	UpdatedReplicas int32 `json:"updatedReplicas,omitempty" protobuf:"varint,5,opt,name=updatedReplicas"`
	// currentRevision, 当前sts用来生成pod的revision，有currentReplicas个，下标从0到currentReplicas-1
	// [0,currentReplicas).
	CurrentRevision string `json:"currentRevision,omitempty" protobuf:"bytes,6,opt,name=currentRevision"`

	// updateRevision, 升级sts用的revision，有updatedReplicas个，下标从replicas-updatedReplicas到replicas-1
	// [replicas-updatedReplicas,replicas)
	UpdateRevision string `json:"updateRevision,omitempty" protobuf:"bytes,7,opt,name=updateRevision"`
```
这个两个字段说明了sts用来生成pod的版本，revision其实也是k8s中的一种资源，在k8s中对应的结构体为v1beta1.ControllerRevision，可以通过`kubectl get controllerrevisions`来查看集群中存在的revision，controllerrevisions看上去像是statefulset或者Daemon set的模板的一个快照，是只读的（不能被修改，只能被删除）。从kubectl get的输出看，controllerrevision是只包括spec，不包含status的。

#### ssc的私有的updateStatefulset方法
这个方法比较长，具体
```go
func (ssc *defaultStatefulSetControl) updateStatefulSet(
	set *apps.StatefulSet,
	currentRevision *apps.ControllerRevision,
	updateRevision *apps.ControllerRevision,
	collisionCount int32,
	pods []*v1.Pod) (*apps.StatefulSetStatus, error) {
	// 这个貌似是从revision中恢复出statefulset
	currentSet, err := ApplyRevision(set, currentRevision)
	if err != nil {
		return nil, err
	}
	updateSet, err := ApplyRevision(set, updateRevision)
	if err != nil {
		return nil, err
	}

	// set the generation, and revisions in the returned status
	status := apps.StatefulSetStatus{}
	status.ObservedGeneration = new(int64)
	*status.ObservedGeneration = set.Generation
	status.CurrentRevision = currentRevision.Name
	status.UpdateRevision = updateRevision.Name
	status.CollisionCount = new(int32)
	*status.CollisionCount = collisionCount

	replicaCount := int(*set.Spec.Replicas)
	// slice that will contain all Pods such that 0 <= getOrdinal(pod) < set.Spec.Replicas
	// sts的合法pod列表，这个列表是已经初始化大小的，在下面的pod for循环中，会根据pod的id填充
	// 这个列表，那么如果pod的数目少于replicas，那么这个slice会有表项为nil，遇到为nil的会重新创建
	// 一个pod的template，并向apiserver发送create请求
	replicas := make([]*v1.Pod, replicaCount)
	// slice that will contain all Pods such that set.Spec.Replicas <= getOrdinal(pod)
	// condemned pod看上去是需要被删除的pod
	condemned := make([]*v1.Pod, 0, len(pods))

	unhealthy := 0
	firstUnhealthyOrdinal := math.MaxInt32
	var firstUnhealthyPod *v1.Pod

	// 将所有的pod分到两个列表中：合法的副本列表，需要被删除的pod的列表
	for i := range pods {
		status.Replicas++

		// 统计running以及ready的pod数量
		// 符合条件的pod为：pod.Status.Phase == v1.PodRunning，并且包含pod的Ready condition，并且ready condition的status为true
		if isRunningAndReady(pods[i]) {
			status.ReadyReplicas++
		}

		// isCreated的条件是pod.Status.Phase != ""
		// isTerminating的条件是 pod.DeletionTimestamp != nil
		if isCreated(pods[i]) && !isTerminating(pods[i]) {
			if getPodRevision(pods[i]) == currentRevision.Name {
				status.CurrentReplicas++
			}
			if getPodRevision(pods[i]) == updateRevision.Name {
				status.UpdatedReplicas++
			}
		}

		if ord := getOrdinal(pods[i]); 0 <= ord && ord < replicaCount {
			// if the ordinal of the pod is within the range of the current number of replicas,
			// insert it at the indirection of its ordinal
			replicas[ord] = pods[i]
		} else if ord >= replicaCount {
			// 如果pod的序号超过了replica，加入到condemned的pod的列表中
			condemned = append(condemned, pods[i])
		}
		// If the ordinal could not be parsed (ord < 0), ignore the Pod.
	}

	// 对于每个合法的序号，使用正确的revision创建pod，（根据升级策略以及当前id来判断使用那个revision）
	// 这个地方只是创建了一个pod的template，并没有向apiserver发送create请求
	for ord := 0; ord < replicaCount; ord++ {
		if replicas[ord] == nil {
			replicas[ord] = newVersionedStatefulSetPod(
				currentSet,
				updateSet,
				currentRevision.Name,
				updateRevision.Name, ord)
		}
	}

	// sort the condemned Pods by their ordinals
	sort.Sort(ascendingOrdinal(condemned))

	// 查找第一个unhealthy的pod，unhealthy的条件是下面两个之一：
	// 1. 不是phase不是running，或不包含ready condition或者condition status不是true
	// 2. delete时间戳不是nil
	for i := range replicas {
		if !isHealthy(replicas[i]) {
			unhealthy++
			if ord := getOrdinal(replicas[i]); ord < firstUnhealthyOrdinal {
				firstUnhealthyOrdinal = ord
				firstUnhealthyPod = replicas[i]
			}
		}
	}

	for i := range condemned {
		if !isHealthy(condemned[i]) {
			unhealthy++
			if ord := getOrdinal(condemned[i]); ord < firstUnhealthyOrdinal {
				firstUnhealthyOrdinal = ord
				firstUnhealthyPod = condemned[i]
			}
		}
	}

	if unhealthy > 0 { 
		glog.V(4).Infof("StatefulSet %s/%s has %d unhealthy Pods starting with %s",
			set.Namespace,
			set.Name,
			unhealthy,
			firstUnhealthyPod.Name)
	}

	// If the StatefulSet is being deleted, don't do anything other than updating
	// status.
	if set.DeletionTimestamp != nil {
		return &status, nil
	}

	monotonic := !allowsBurst(set)

	// 检查每个副本
	for i := range replicas {
		// delete and recreate failed pods
		// failed的条件是：pod.Status.Phase == v1.PodFailed
		if isFailed(replicas[i]) {
			glog.V(4).Infof("StatefulSet %s/%s is recreating failed Pod %s",
				set.Namespace,
				set.Name,
				replicas[i].Name)
				// 只删除pod，并不会删除pvc
			if err := ssc.podControl.DeleteStatefulPod(set, replicas[i]); err != nil {
				return &status, err
			}
			if getPodRevision(replicas[i]) == currentRevision.Name {
				status.CurrentReplicas--
			}
			if getPodRevision(replicas[i]) == updateRevision.Name {
				status.UpdatedReplicas--
			}
			status.Replicas--
			replicas[i] = newVersionedStatefulSetPod(
				currentSet,
				updateSet,
				currentRevision.Name,
				updateRevision.Name,
				i)
		}
		// If we find a Pod that has not been created we create the Pod
		// 发现pod的phase==""，表示只是一个模板，向apiserver发送create请求
		if !isCreated(replicas[i]) {
			// 调用PodControlInterface的方法创建pod，创建pod之前会先创建pvc，创建pvc之前又
			// 会先检查pvc在不在，如果pvc存在就不创建pvc了
			if err := ssc.podControl.CreateStatefulPod(set, replicas[i]); err != nil {
				return &status, err
			}
			status.Replicas++
			if getPodRevision(replicas[i]) == currentRevision.Name {
				status.CurrentReplicas++
			}
			if getPodRevision(replicas[i]) == updateRevision.Name {
				status.UpdatedReplicas++
			}

			// if the set does not allow bursting, return immediately
			if monotonic {
				return &status, nil
			}
			// pod created, no more work possible for this round
			continue
		}
		// If we find a Pod that is currently terminating, we must wait until graceful deletion
		// completes before we continue to make progress.
		if isTerminating(replicas[i]) && monotonic {
			glog.V(4).Infof(
				"StatefulSet %s/%s is waiting for Pod %s to Terminate",
				set.Namespace,
				set.Name,
				replicas[i].Name)
			return &status, nil
		}
		// If we have a Pod that has been created but is not running and ready we can not make progress.
		// We must ensure that all for each Pod, when we create it, all of its predecessors, with respect to its
		// ordinal, are Running and Ready.
		if !isRunningAndReady(replicas[i]) && monotonic {
			glog.V(4).Infof(
				"StatefulSet %s/%s is waiting for Pod %s to be Running and Ready",
				set.Namespace,
				set.Name,
				replicas[i].Name)
			return &status, nil
		}
		// Enforce the StatefulSet invariants
		if identityMatches(set, replicas[i]) && storageMatches(set, replicas[i]) {
			continue
		}
		// Make a deep copy so we don't mutate the shared cache
		replica := replicas[i].DeepCopy()
		if err := ssc.podControl.UpdateStatefulPod(updateSet, replica); err != nil {
			return &status, err
		}
	}

	// At this point, all of the current Replicas are Running and Ready, we can consider termination.
	// We will wait for all predecessors to be Running and Ready prior to attempting a deletion.
	// We will terminate Pods in a monotonically decreasing order over [len(pods),set.Spec.Replicas).
	// Note that we do not resurrect（复活） Pods in this interval. Also note that scaling and offlining
	// will take precedence over updates.
	for target := len(condemned) - 1; target >= 0; target-- {
		// wait for terminating pods to expire
		if isTerminating(condemned[target]) {
			glog.V(4).Infof(
				"StatefulSet %s/%s is waiting for Pod %s to Terminate prior to scale down",
				set.Namespace,
				set.Name,
				condemned[target].Name)
			// block if we are in monotonic mode
			if monotonic {
				return &status, nil
			}
			continue
		}
		// if we are in monotonic mode and the condemned target is not the first unhealthy Pod block
		if !isRunningAndReady(condemned[target]) && monotonic && condemned[target] != firstUnhealthyPod {
			glog.V(4).Infof(
				"StatefulSet %s/%s is waiting for Pod %s to be Running and Ready prior to scale down",
				set.Namespace,
				set.Name,
				firstUnhealthyPod.Name)
			return &status, nil
		}
		glog.V(4).Infof("StatefulSet %s/%s terminating Pod %s for scale dowm",
			set.Namespace,
			set.Name,
			condemned[target].Name)

		if err := ssc.podControl.DeleteStatefulPod(set, condemned[target]); err != nil {
			return &status, err
		}
		if getPodRevision(condemned[target]) == currentRevision.Name {
			status.CurrentReplicas--
		}
		if getPodRevision(condemned[target]) == updateRevision.Name {
			status.UpdatedReplicas--
		}
		if monotonic {
			return &status, nil
		}
	}

	// for the OnDelete strategy we short circuit. Pods will be updated when they are manually deleted.
	if set.Spec.UpdateStrategy.Type == apps.OnDeleteStatefulSetStrategyType {
		return &status, nil
	}

	// we compute the minimum ordinal of the target sequence for a destructive update based on the strategy.
	updateMin := 0
	if set.Spec.UpdateStrategy.RollingUpdate != nil {
		updateMin = int(*set.Spec.UpdateStrategy.RollingUpdate.Partition)
	}
	// we terminate the Pod with the largest ordinal that does not match the update revision.
	for target := len(replicas) - 1; target >= updateMin; target-- {
		// delete the Pod if it is not already terminating and does not match the update revision.
		if getPodRevision(replicas[target]) != updateRevision.Name && !isTerminating(replicas[target]) {
			glog.V(4).Infof("StatefulSet %s/%s terminating Pod %s for update",
				set.Namespace,
				set.Name,
				replicas[target].Name)
			err := ssc.podControl.DeleteStatefulPod(set, replicas[target])
			status.CurrentReplicas--
			return &status, err
		}

		// wait for unhealthy Pods on update
		if !isHealthy(replicas[target]) {
			glog.V(4).Infof(
				"StatefulSet %s/%s is waiting for Pod %s to update",
				set.Namespace,
				set.Name,
				replicas[target].Name)
			return &status, nil
		}

	}
	return &status, nil
}
```

总的来说，就是对sts所有的pod进行遍历，同时对所有的id进行遍历，根据pod状态做出相应动作。