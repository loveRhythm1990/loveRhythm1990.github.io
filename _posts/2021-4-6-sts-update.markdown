---
layout:     post
title:      "Statefulset 滚动升级实现"
date:       2021-4-6 16:49:00
author:     "decent"
header-img-credit: false
tags:
    - K8s
---

[Sts控制器概述](https://loverhythm1990.github.io/2019/09/23/k8s-updateStatefulset/)大概描述了sts控制器的工作原理，这里重点关注下滚动升级的实现。

#### controllerrevision 概述
记录了某一时刻podSpec template的模板，其`Data`字段是不可修改的，`Revision`字段是可以修改的。其定义如下。daemonset以及statefulset会通过ControllerRevision来实现滚动升级。
```go
type ControllerRevision struct {
	metav1.TypeMeta `json:",inline"`
	metav1.ObjectMeta `json:"metadata,omitempty" protobuf:"bytes,1,opt,name=metadata"`
	// pod spec template的序列化
	Data runtime.RawExtension `json:"data,omitempty" protobuf:"bytes,2,opt,name=data"`
    // Data字段所代表的template的版本号，这个是递增的字段，如果我们修改了sts的pod template，会产生一个新的controllerrevision，
    // 该字段也会递增1
	Revision int64 `json:"revision" protobuf:"varint,3,opt,name=revision"`
}
```
在集群中我们可以通过如下命令看我们关心的sts的controllerrevision，其中`app=nginx`是sts的seletor字段，用来选择pod。我们可以看到有三个controllerrevision，并且其`REVISION`是递增的（该字段也对应ControllerRevision struct中的`Revision`字段），并且时间越近，`REVISION`值越大。
```s
decent@ubuntu:~$ kubectl get controllerrevision -l app=nginx
NAME             CONTROLLER             REVISION   AGE
web-578cfc4b46   statefulset.apps/web   1          85m
web-699f9c769    statefulset.apps/web   2          61m
web-6b44fbc5dd   statefulset.apps/web   3          55m
```
在statefulset的status字段中，有两个需要额外注意一下，一个是`currentRevision`，一个是`updateRevision`，前者表示当前statefulset升级之前所对应的controllerRevision，如果当前statefulset正在执行滚动升级，那么`updateRevision`表示最新的pod template所对应的controllerRevision。如果statefulset当前没有在升级（或者已经升级完毕），那么这两个字段是一致的。

statefulset控制器通过方法`(ssc *defaultStatefulSetControl) getStatefulSetRevisions`来拿到`currentRevision`以及`updateRevision`，其实现如下，该方法输入为当前statefulset，以及该statefulset对应的所有revision集合。
```go
func (ssc *defaultStatefulSetControl) getStatefulSetRevisions(
	set *apps.StatefulSet,
	revisions []*apps.ControllerRevision) (*apps.ControllerRevision, *apps.ControllerRevision, int32, error) {
	var currentRevision, updateRevision *apps.ControllerRevision

    // 将所有的ControllerRevision按照Revision字段升序排列
	revisionCount := len(revisions)
	history.SortControllerRevisions(revisions)
    // CollisionCount这个字段还不是很清楚
	var collisionCount int32
	if set.Status.CollisionCount != nil {
		collisionCount = *set.Status.CollisionCount
	}

    // 根据当前sts的pod template生成一个新的Revision。其中nextRevision是根据当前最大的revision id来
    // 生成下一个id，生成Revision的过程比较细节，可以先简单理解为对原来template的json.Marshal
	updateRevision, err := newRevision(set, nextRevision(revisions), &collisionCount)
	if err != nil {
		return nil, nil, collisionCount, err
	}

	// 寻找相等的Revision，如果对template进行了更新，是不存在相等的Revision
	equalRevisions := history.FindEqualRevisions(revisions, updateRevision)
	equalCount := len(equalRevisions)

	if equalCount > 0 && history.EqualRevision(revisions[revisionCount-1], equalRevisions[equalCount-1]) {
        // if the equivalent revision is immediately prior the update revision has not changed
        // 什么都没有更新
		updateRevision = revisions[revisionCount-1]
	} else if equalCount > 0 {
		// if the equivalent revision is not immediately prior we will roll back by incrementing the
        // Revision of the equivalent revision
        // 执行的是回滚操作，把当前template修改成跟之前某一个一样，把之前那个ControllerRevision的Revision字段设置成最新
        // 也就是值updateRevision.Revision，执行的是update字段
		updateRevision, err = ssc.controllerHistory.UpdateControllerRevision(
			equalRevisions[equalCount-1],
			updateRevision.Revision)
		if err != nil {
			return nil, nil, collisionCount, err
		}
	} else {
        //if there is no equivalent revision we create a new one
        // 进行了update操作，创建一个新的ControllerRevision
		updateRevision, err = ssc.controllerHistory.CreateControllerRevision(set, updateRevision, &collisionCount)
		if err != nil {
			return nil, nil, collisionCount, err
		}
	}

    // attempt to find the revision that corresponds to the current revision
    // 遍历CurrentRevision，寻找等于set的Status字段中的CurrentRevision
	for i := range revisions {
		if revisions[i].Name == set.Status.CurrentRevision {
			currentRevision = revisions[i]
			break
		}
	}

	// 如果没有currentRevision，设置为updateRevision
	if currentRevision == nil {
		currentRevision = updateRevision
	}
	return currentRevision, updateRevision, collisionCount, nil
}
```
在生成`updateRevision`的时候，会对ControllerRevision的Data字段进行hash，并生成一个`int32`字段的数，并将这个hash值作为lable`controller.kubernetes.io/hash`的值，这个用来快速判断两个Revision是否相等。

通过上面方法，我们拿到了当前statefulset的`updateRevision`以及`currentRevision`，后面`updateStatefulset`方法会执行滚动升级。

#### updateStatefulSet
这个方法比较长，之前也贴过，这里不贴了，只列出关键动作。其方法前面如下：
```go
func (ssc *defaultStatefulSetControl) updateStatefulSet(
	set *apps.StatefulSet,
	currentRevision *apps.ControllerRevision,
	updateRevision *apps.ControllerRevision,
	collisionCount int32,
	pods []*v1.Pod) (*apps.StatefulSetStatus, error) {
```

1. 通过`ApplyRevision`还原出statefulset模板，将controllerRevision的Data字段作为patch添加到当前sts中。
```go
	currentSet, _ := ApplyRevision(set, currentRevision)
	updateSet, _ := ApplyRevision(set, updateRevision)
```
2. 初始化一个pod列表，列表的长度就是`set.Spec.Replicas`，然后通过索引到的所有Pod，来填充这个列表。如果有些下标没有被填充，则初始化一个新的pod，并最终调用`podControl`的`CreateStatefulPod`创建pod，
```go
	replicaCount := int(*set.Spec.Replicas)
	// slice that will contain all Pods such that 0 <= getOrdinal(pod) < set.Spec.Replicas
	replicas := make([]*v1.Pod, replicaCount)
	// slice that will contain all Pods such that set.Spec.Replicas <= getOrdinal(pod)
	condemned := make([]*v1.Pod, 0, len(pods))
```
此外还有一些case:
* 如果Pod Failed，删除Pod
* 如果Pod Terminating，打印日志，并return，return是因为statefulset默认是顺序的，要等这个running之后，才会处理后面的pod，（statefulset pod强删会导致集群中有两个同名的pod）
* 如果Pod 不是RunningAndReady同样return
* 删除`condemned`的pod.

**滚动升级**

先找到`Partition`的大小，序号大于等于`Partition`值的pod需要升级，从后面序号开始遍历，如果当前pod的`Revision`不等于`updateRevision`并且不是`Terminating`状态，那么将pod删除。
```go
// we terminate the Pod with the largest ordinal that does not match the update revision.
for target := len(replicas) - 1; target >= updateMin; target-- {

	// delete the Pod if it is not already terminating and does not match the update revision.
	if getPodRevision(replicas[target]) != updateRevision.Name && !isTerminating(replicas[target]) {
		klog.V(2).Infof("StatefulSet %s/%s terminating Pod %s for update",
			set.Namespace,
			set.Name,
			replicas[target].Name)
		err := ssc.podControl.DeleteStatefulPod(set, replicas[target])
		status.CurrentReplicas--
		return &status, err
	}

	// wait for unhealthy Pods on update
	if !isHealthy(replicas[target]) {
		klog.V(4).Infof(
			"StatefulSet %s/%s is waiting for Pod %s to update",
			set.Namespace,
			set.Name,
			replicas[target].Name)
		return &status, nil
	}

}
```
并且在删除pod后直接返回。