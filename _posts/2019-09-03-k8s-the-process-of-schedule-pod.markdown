---
layout:     post
title:      "Scheduler 调度 Pod 过程"
subtitle:   " \"Scheduler 调度 Pod 的主线流程，及队列管理\""
date:       2019-08-20 16:36:00
author:     "weak old dog"
header-img-credit: false
tags:
    - K8s
    - Scheduler
---

> “Somethings Happens”

## 前言

打算分析一下调度器，将调度器拆成若干部分：调度主干，pod队列管理，缓存管理，调度算法等。本文分析下主干及pod调度队列管理。基于的k8s版本为1.9.3。

## 入口

代码文件：`kubernetes/plugin/pkg/scheduler/scheduler.go`
方法名字：`func (sched *Scheduler) scheduleOne()`

## 流程

1. 取一个pod，从configFactory的podQueue里取一个pod进行调度，这个pod会从待调度队列删除。

    `pod := sched.config.NextPod()`

2. 调度pod，这个包括了predicate过程，以及priority过程。如果调度失败（即找不到合适的node）err不为nil。

   `suggestedHost, err := sched.schedule(pod)`

   调度失败的处理逻辑如下，其中`sched.config.Error(pod, err)`会重新将pod加入到队列，下面的update方法也会触发一个unschedule pod的update事件。
   ```go
   pod = pod.DeepCopy()
   sched.config.Error(pod, err)
   sched.config.Recorder.Eventf(pod, v1.EventTypeWarning, "FailedScheduling", "%v", err)
   sched.config.PodConditionUpdater.Update(pod, &v1.PodCondition{
       Type:    v1.PodScheduled,
       Status:  v1.ConditionFalse,
       Reason:  v1.PodReasonUnschedulable,
       Message: err.Error(),
   })
   ```
   另外如果调度失败，还会尝试抢占，抢占成功，则继续下面的assume操作。这个有个疑问，上面的Error方法将pod加入到了队列中，如果抢占成功（得到了suggestedHost），那队列中还有一个pod，这不是重新调度吗？（等研究下k8s 1.16再看这个问题）

3. 调度成功，准备加入到scheduler的缓存中，即assume操作。首先做一个pod的深拷贝，其次调用assume加入到缓存
   ```golang
   assumedPod := pod.DeepCopy()
   err = sched.assume(assumedPod, suggestedHost)
   ```
   在调用assume的时候，会设置pod.Spec.NodeName，（乐观地认为下面的bind操作会成功），assume的后续操作会根据这个nodeName找到schedulerCache中的对应的NodeInfo(就是缓存的node的信息，包含一个调度到此node上的pod的列表)，并把当前pod加入到这个NodeInfo中。

   另外，assume pod的时候，必须保证cache中不能存在当前pod，以namespace和name为key，如果已经存在会报
   `pod %v state wasn't initial but get assumed`错误。可以参考[issue 61069](https://github.com/kubernetes/kubernetes/pull/61069)，如果对一个pod重复调度也会遇到这个问题，个人不幸遇到了。

   assume失败：如果assume pod失败，会更新pod的condition。

4. bind pod，即更新pod.Spec.NodeName，这个过程是异步的，即一个单独的goroutine去做这个事情，起一个goroutine之后，scheduler又去调度其他pod了。


## 队列管理

这里的队列是指scheduler中待调度pod队列，在文件`kubernetes/plugin/pkg/scheduler/factory/factory.go`中，
```go
// configFactory is the default implementation of the scheduler.Configurator interface.
type configFactory struct {
	// queue for pods that need scheduling
    podQueue core.SchedulingQueue

    ...
}
```
scheduler有两种队列，一种是FIFO，另一种是优先级队列，后者就是实现了golang标准库中的heap接口，参考[golang heap](https://golang.org/pkg/container/heap/)。
队列的接口如下，FIFO队列只实现了其中一部分接口，另一部分接口并没有实现，相对比较简单。
```golang
// SchedulingQueue is an interface for a queue to store pods waiting to be scheduled.
// The interface follows a pattern similar to cache.FIFO and cache.Heap and
// makes it easy to use those data structures as a SchedulingQueue.
type SchedulingQueue interface {
	Add(pod *v1.Pod) error
	AddIfNotPresent(pod *v1.Pod) error
	AddUnschedulableIfNotPresent(pod *v1.Pod) error
	Pop() (*v1.Pod, error)
	Update(oldPod, newPod *v1.Pod) error
	Delete(pod *v1.Pod) error
	MoveAllToActiveQueue()
	AssignedPodAdded(pod *v1.Pod)
	AssignedPodUpdated(pod *v1.Pod)
	WaitingPodsForNode(nodeName string) []*v1.Pod
}
```
队列的生产者是scheduler的pod informer事件处理器，scheduler注册了两个pod事件处理器，一个用于处理unscheduled pod，一个用于处理scheduled pod，两个事件处理器是通过添加Filter函数实现的，其中作为队列生产者的处理器是unscheduled pod事件处理器。schedulerd pod处理器主要用于更新scheduler缓存。
下面是代码，`unassignedNonTerminatedPod`用来过滤没有分配的且未结束的pod，`AddFunc`直接添加到队列里（添加到队列之前会检查队列中是否已经存在），`UpdateFunc`最终也调用的add方法，不过update可以有选择的忽略某些事件。此外，前面提到过，一个pod调度失败，也会被重新添加到scheduler的未调度队列。
```golang
// unscheduled pod queue
podInformer.Informer().AddEventHandler(
	cache.FilteringResourceEventHandler{
		FilterFunc: func(obj interface{}) bool {
			switch t := obj.(type) {
			case *v1.Pod:
				return unassignedNonTerminatedPod(t)
			default:
				runtime.HandleError(fmt.Errorf("unable to handle object in %T: %T", c, obj))
				return false
			}
		},
		Handler: cache.ResourceEventHandlerFuncs{
			AddFunc: func(obj interface{}) {
				if err := c.podQueue.Add(obj.(*v1.Pod)); err != nil {
					runtime.HandleError(fmt.Errorf("unable to queue %T: %v", obj, err))
				}
			},
			UpdateFunc: func(oldObj, newObj interface{}) {
				pod := newObj.(*v1.Pod)
				if c.skipPodUpdate(pod) {
					return
				}
				if err := c.podQueue.Update(oldObj.(*v1.Pod), pod); err != nil {
					runtime.HandleError(fmt.Errorf("unable to update %T: %v", newObj, err))
				}
			},
			DeleteFunc: func(obj interface{}) {
				pod := obj.(*v1.Pod)
				if err := c.podQueue.Delete(pod); err != nil {
					runtime.HandleError(fmt.Errorf("unable to dequeue %T: %v", obj, err))
				}
				if c.volumeBinder != nil {
					// Volume binder only wants to keep unassigned pods
					c.volumeBinder.DeletePodBindings(pod)
				}
			},
		},
	},
)
```
队列的生产者就是scheduler的调度逻辑。即`pod := sched.config.NextPod()`，每次从队列取出一个pod进行调度。
