---
layout:     post
title:      "K8s Statefulset 控制器设计"
date:       2019-09-23 19:16:00
author:     "weak old dog"
header-img-credit: false
tags:
    - Controller
---

- [前言](#前言)
- [控制器初始化](#控制器初始化)
- [Sync 入口: UpdateStatefulset 方法](#sync-入口-updatestatefulset-方法)
	- [revision 版本控制](#revision-版本控制)
- [控制器的私有 updateStatefulSet 方法](#控制器的私有-updatestatefulset-方法)

### 前言
K8s 的 Statefulset 控制器，是 controller-manager 中比较重要的一个，了解其实现有助于我们开发自己的控制器。[k8s文档](https://kubernetes.io/docs/concepts/workloads/controllers/statefulset/)中对于 Statefulset 的描述为：
* 稳定的、唯一的网络标识符
* 稳定的、持久的存储
* 有序的、优雅的部署和缩放
* 有序的、自动的滚动升级

Sts 控制器的入口为: `pkg/controller/statefulset/stateful_set.go`，函数为 `NewStatefulSetController`
代码主要分三部分：
* stateful_set，对应文件 `pkg/controller/statefulset/stateful_set.go`
* stateful_set_control，对应文件`pkg/controller/statefulset/stateful_set_control.go`
* stateful_pod_control，对应文件`pkg/controller/statefulset/stateful_pod_control.go`

### 控制器初始化
初始化是在 `NewStatefulSetController` 方法中进行的。首先是注册 Pod、Sts 资源的事件处理函数，事件处理器都是 stateful_set 要干的活，代码在其对应的文件中。

Pod 的事件处理函数基本没有业务逻辑，基本是取出 Pod 对应的 Sts，然后调用 `ssc.enqueueStatefulSet` 将 Sts 放入待处理队列。

Sts 的事件处理函数就比较简单了，就是直接调用 `ssc.enqueueStatefulSet` 将 Sts 加入到待循环队列。

这个函数最终返回一个 `StatefulSetController` 结构体，如下：
```go
type StatefulSetController struct {
	kubeClient clientset.Interface
	// 实现 Sts 控制器的核心逻辑，用来更新 Sts 以及其所包含的 Pod，抽象出来为了方便测试
	// Sts 控制器的 sync 方法中，主要逻辑就是调用了这个接口的 UpdateStatefulSet 方法
	control StatefulSetControlInterface
	// 用来操作 Pod 的接口, Pod 的 CRUD 操作
	podControl controller.PodControlInterface

	podLister corelisters.PodLister
	podListerSynced cache.InformerSynced
	setLister appslisters.StatefulSetLister
	setListerSynced cache.InformerSynced
	pvcListerSynced cache.InformerSynced
	revListerSynced cache.InformerSynced
	// 用来存放待更新sts的队列，一般pod或者sts有变动，直接往这个队列里扔就对了
	queue workqueue.RateLimitingInterface
}
```

`StatefulSetController` 结构体的 `Run` 方法: `go wait.Until(ssc.worker, time.Second, stopCh)`，就是启动一个工作线程不停执行。worker 线程的调用链为：

worker() --> processNextWorkItem() --> sync() --> syncStatefulSet(set, pods) --> control.UpdateStatefulset(set, pods)

过程就是取出队列中的所有 Sts，并针对每个 Sts，调用用 sync 方法，最终调用了 `StatefulSetControlInterface` 接口的`UpdateStatefulset` 方法，所以，后者就是整个过程的核心了。

### Sync 入口: UpdateStatefulset 方法
注意这个首字母大写的方法，还有一个首字符小写的方法。首先看一下这个方法的注释：
> UpdateStatefulset 是 Sts 的核心循环逻辑，执行可预测的、默认单调的更新策略：scale up 时是升序的，当前一个 Pod 是 unhealthy 时，后一个 Pod 是不会创建的（在控制器中，当遇到一个 Pod unhealthy 时，立即退出整个控制器的 sync 逻辑，下次 reconcile 还是卡在这个 unhealthy 的 Pod）。Pod 终止时是降序的。在 burst 策略下，Pod 的创建以及删除是无序的。

这个方法有一个参数 pods，是在 sync 方法中拿到的属于该 Sts 的 pod，这个方法的方法体如下：
```go
func (ssc *defaultStatefulSetControl) UpdateStatefulSet(set *apps.StatefulSet, pods []*v1.Pod) error {
	// 拿到这个 Sts 所有 revision
	revisions, err := ssc.ListRevisions(set)
	if err != nil {
		return err
	}
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
	// ... ... 
}
```
这个方法的主要逻辑是：1）拿到这个 Sts 的所有 revision，并且按照版本升序排列（最后后面的是最新的）；2）根据当前 Sts 的 spec 拿到 `currentRevision` 以及 `updateRevision`；3）根据 `currentRevison` 以及 `updateRevision` 调用 `updateStatefulSet` 方法，此方法同步 Sts 中的 Pod。

可以看到还是涉及到了很多概念：revision(CurrentRevision/UpdateRevision)，Replicas，ReadyReplicas，CurrentReplicas，UpdatedReplicas。稍微解释一下

#### revision 版本控制
revision 具体是指 `controllerrevisions` 资源，该资源存放了一个 Sts 资源的 pod template 字段，具体 path 是：spec.template。该字段作为 patch 存放在 controllerrevisions 中，必要的时候可以通过 patch 来回滚 Sts 资源，即用这个 patch 来更新 Statefulset 资源。在 Sts 控制器，可以通过 revision 来实现版本控制，即判断一个 Pod 是不是最新版本，决定要不要升级 Pod。其中：
* updateRevision：通过当前 Sts 实时计算出来的，这个计算出来的 controllerrevision 现在只存在于当前进程的内存中，还没有提交到 Apiserver，如果这个计算出来的 revision 与已经存在的某一个`等价`，就没有必要创建了。
* currentRevision：这个是 `set.Status.CurrentRevision`，如果其为 nil 则跟 updateRevision 是一致的。
  
> 在实际运维中，我们也可以通过 Revision 实现对 Statefulset 的回滚，具体命令为 `kubectl rollout undo statefulset web --to-revision=1`，另外回滚 Sts 时需要手动删除出故障的 Pod，这个可以参考 K8s 文档。

### 控制器的私有 updateStatefulSet 方法
这个是在 `UpdateStatefulSet` 中调用的方法，是一个私有方法，执行了主要的更新逻辑。我们只分析核心的几行代码，从
宏观上了解设计思路。

**注意：** 这个方法在 2024 年 2 月 11 日根据 K8s 1.21 进行了更新。

```go
func (ssc *defaultStatefulSetControl) updateStatefulSet(
	ctx context.Context,
	set *apps.StatefulSet,
	currentRevision *apps.ControllerRevision,
	updateRevision *apps.ControllerRevision,
	collisionCount int32,
	pods []*v1.Pod) (*apps.StatefulSetStatus, error) {

	replicaCount := int(*set.Spec.Replicas)
	replicas := make([]*v1.Pod, replicaCount)

	// 发现 Pod slice 中有 nil 的，就创建一个 
	for ord := getStartOrdinal(set); ord <= getEndOrdinal(set); ord++ {
		replicaIdx := ord - getStartOrdinal(set)
		if replicas[replicaIdx] == nil {
			replicas[replicaIdx] = newVersionedStatefulSetPod(
				currentSet,
				updateSet,
				currentRevision.Name,
				updateRevision.Name, ord)
		}
	}

	// 对每个 pod 执行 processReplica 方法
	processReplicaFn := func(i int) (bool, error) {
		return ssc.processReplica(ctx, set,
			currentRevision, updateRevision, currentSet, updateSet, monotonic, replicas, i)
	}
	if shouldExit, err := runForAll(replicas, processReplicaFn, monotonic); shouldExit || err != nil {
		updateStatus(&status, set.Spec.MinReadySeconds, currentRevision, updateRevision, replicas, condemned)
		return &status, err
	}

        // 这里要对 Pod 动手了
	for target := len(replicas) - 1; target >= updateMin; target-- {
		if getPodRevision(replicas[target]) != updateRevision.Name && !isTerminating(replicas[target]) {
			logger.V(2).Info("Pod of StatefulSet is terminating for update",
				"statefulSet", klog.KObj(set), "pod", klog.KObj(replicas[target]))
			if err := ssc.podControl.DeleteStatefulPod(set, replicas[target]); err != nil {
				if !errors.IsNotFound(err) {
					return &status, err
				}
			}
			status.CurrentReplicas--
			return &status, err
		}
	}
	return &status, nil
}
```
这个方法，可以只关注两个要点：1）对每个 Pod 执行 processReplica 方法。2）检查 Pod 的 revision 是否符合预期。如果不符合预期，就删除重建。

这里重点关注第二个问题。如何判断一个 Pod 的 revision 是否符合预期？判断一个 Pod 的 revision 是否符合预期，也就是判断其 spec 是否符合预期，但是 Sts 控制器并没有检查 pod 的 spec，而是查看其 revision hash。这个是合理的，因为 revision 代表的是 Sts 的 spec.template 字段，所有 pod 是从这个字段生产出来的。在 Sts pod 的 label 中，会有 controllerrevision 的名字，这个名字带有 template 的hash。在检查 pod spec 的 hash 是否符合预期时，只需要检查这个 label 就可以了。
```yaml
    labels:
      app: nginx
      controller-revision-hash: web-744cf45f5d
      statefulset.kubernetes.io/pod-name: web-0
```
> 这里有个问题，只检查 label 中够吗？万一 pod 的 spec 被修改了咋办，答案是够的，此时就算是 pod spec 被修改了，只要 label hash 没有变化，Sts 控制器是不管的。一个简单的例子是，如果我们 `kubectl edit pod` 然后修改 pod 的镜像，Sts 控制器是不会帮我们改过来的。另外其实 pod spec 可被修改的字段是非常有限的。

处理每个 Pod 的 processReplica 方法，这个方法一看就懂，主要是处理 pod 的异常状态，以及不一致行为等。因为 Sts 的更新是单调的，在处理一个 pod 失败时，往往不会处理其他 pod，而是卡在这个失败的 pod 上，代码中有很多这种处理行为。
