---
layout:     post
title:      "Sts控制器UpdateStatefulSet"
subtitle:   " \"关于一个方法的概述\""
date:       2019-09-23 19:16:00
author:     "weak old dog"
header-img-credit: false
tags:
    - k8s
    - statefulset
---


## 前言
在我看来，k8s的statefulset控制器，算是controller-manager中比较重要的一个了（是不是最重要的一个呢？），鉴于也是一块硬骨头，只能一点一点啃，能一次写完整是极好的，但是鉴于精力与能力不足，一点点写也是不错的，而且对我自身来说，也是非常有效的。

本文只会介绍Statefulset控制器中核心算法`UpdateStatefulSet`的实现，基于的k8s代码为1.16。所在的代码路径为`pkg\controller\statefulset\stateful_set_control.go`

这个方法的注释的翻译为:
> UpdateStatefulSet执行sts的核心逻辑，执行可预期的、单调的默认更新策略：扩展的时候是以序号递增的，如果有任何pod处于不健康的状态，新pod是不会创建的；pod终止的时候是降序的。在burst strategy模式下，这些约束有所放松：pod的创建以及删除都非常eagerly，并且无序，使用burst strategy模式的client应该知道有不可预期的可用的pod所带来的影响。


## 算法实现
```golang
// UpdateStatefulSet executes the core logic loop for a stateful set, applying the predictable and
// consistent monotonic update strategy by default - scale up proceeds in ordinal order, no new pod
// is created while any pod is unhealthy, and pods are terminated in descending order. The burst
// strategy allows these constraints to be relaxed - pods will be created and deleted eagerly and
// in no particular order. Clients using the burst strategy should be careful to ensure they
// understand the consistency implications of having unpredictable numbers of pods available.
func (ssc *defaultStatefulSetControl) UpdateStatefulSet(set *apps.StatefulSet, pods []*v1.Pod) error {

	// list all revisions and sort them
	revisions, err := ssc.ListRevisions(set)
	if err != nil {
		return err
	}
	history.SortControllerRevisions(revisions)

	// get the current, and update revisions
	currentRevision, updateRevision, collisionCount, err := ssc.getStatefulSetRevisions(set, revisions)
	if err != nil {
		return err
	}

	// perform the main update function and get the status
	status, err := ssc.updateStatefulSet(set, currentRevision, updateRevision, collisionCount, pods)
	if err != nil {
		return err
	}

	// update the set's status
	err = ssc.updateStatefulSetStatus(set, status)
	if err != nil {
		return err
	}

	klog.V(4).Infof("StatefulSet %s/%s pod status replicas=%d ready=%d current=%d updated=%d",
		set.Namespace,
		set.Name,
		status.Replicas,
		status.ReadyReplicas,
		status.CurrentReplicas,
		status.UpdatedReplicas)

	klog.V(4).Infof("StatefulSet %s/%s revisions current=%s update=%s",
		set.Namespace,
		set.Name,
		status.CurrentRevision,
		status.UpdateRevision)

	// maintain the set's revision history limit
	return ssc.truncateHistory(set, pods, revisions, currentRevision, updateRevision)
}
```
