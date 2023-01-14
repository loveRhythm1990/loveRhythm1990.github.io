---
layout:     post
title:      "K8s 中的 ReplicaSet 源码解析"
date:       2020-3-2 16:56:00
author:     "weak old dog"
header-img-credit: false
tags:
    - K8s
    - Controller
---

学习一下ReplicaSet Controlelr代码，基于的k8s版本为1.9.6
### 基本要点
ReplicaSet的目的是运行一组固定数量的pod，一般只用来运行固定数量的pod，这些pod是用同一个template生成的，如果pod被意外删除了，RS控制器帮你自动创建等。
也可以使用

`kubectl scale rs/frontend --replicas=2`

来扩展或者缩小rs中的副本数。

ReplicaSet使用Label selector来选择pod，pod有`ownerReference`来说明其所属的控制器，虽然如此，如果一个pod没有ownerReference或者ownerReference的值不等于ReplicaSet的name，但是其label被rs选中了，那么rs也会认为这个pod是它的，那么就会导致使用rs的template启动的pod的个数少于`replicas`的数值，也会导致rs中包含不同模板的pod。

### 源码解析
ReplicaSet Controller的代码应该比较简单，也是基本的k8s constroller的编程规范。开局一个struct，总结一下这个struct的基本组成：
* kubeClient，跟apiserver打交道，必不可少，每个controller都有
* 一些Lister，以及判断缓存是否同步的方法，每个controller都有，当然每个controller关注的资源不同
* 同步方法，输入为资源的key，就是`namespace/name`这样的字符串，这个同步方法是从queue里拿出资源来，然后同步，不包含从queue的pop逻辑
* queue，这就是放所有资源的队列了，监听到的资源一般直接放入这个队列

其他controller结构大概如此，具体业务逻辑可能不同。

```go
// 同步ReplicaSet的Controller
type ReplicaSetController struct {
	schema.GroupVersionKind

    //客户端client
	kubeClient clientset.Interface
	podControl controller.PodControlInterface

	// 当一下子创建/删除这些pod的时候，controller就会停下来等一等。观察到这些pod的watch event的时候，会恢复行动
	burstReplicas int

	// 真正同步RS的方法，允许为了测试注入其他实现
	syncHandler func(rsKey string) error

	// A TTLCache of pod creates/deletes each rc expects to see.
	expectations *controller.UIDTrackingControllerExpectations

    // RS以及pod的lister接口
	rsLister extensionslisters.ReplicaSetLister
	rsListerSynced cache.InformerSynced
	podLister corelisters.PodLister
	podListerSynced cache.InformerSynced

	// 需要同步的队列，这个队列的实现是client-go中workqueue，实现还挺复杂的，有机会可以研究一下
	queue workqueue.RateLimitingInterface
}
```

##### 初始化控制器
New Controller方法，最终调用了下面这个，是为了同时作为`NewReplicationController`这个控制器的实现，为了代码复用。

初始化时，传入的参数首先是一些informer，这些informer都是一些引用，通过这个informer，当前这个controller可以根据自己的业务逻辑添加自己的`EventHandler`，并且生成一些Lister，这些Lister负责从cache中拿对应数据（应该视为只读的）。目前看，controller的初始化任务就是这些。

```go
func NewBaseController(rsInformer extensionsinformers.ReplicaSetInformer, podInformer coreinformers.PodInformer, kubeClient clientset.Interface, burstReplicas int,
	gvk schema.GroupVersionKind, metricOwnerName, queueName string, podControl controller.PodControlInterface) *ReplicaSetController {

    // 新建一个结构体
	rsc := &ReplicaSetController{
		GroupVersionKind: gvk,
		kubeClient:       kubeClient,
		podControl:       podControl,
		burstReplicas:    burstReplicas,
		expectations:     controller.NewUIDTrackingControllerExpectations(controller.NewControllerExpectations()),
		queue:            workqueue.NewNamedRateLimitingQueue(workqueue.DefaultControllerRateLimiter(), queueName),
	}

	rsInformer.Informer().AddEventHandler(cache.ResourceEventHandlerFuncs{
		// 收到rs的任何事件，都直接放到同步队列，updateRS那个事件处理函数仅仅是为了打印日志
		AddFunc:    rsc.enqueueReplicaSet,
		UpdateFunc: rsc.updateRS,
		// This will enter the sync loop and no-op, because the replica set has been deleted from the store.
		// Note that deleting a replica set immediately after scaling it to 0 will not work. The recommended
		// way of achieving this is by performing a `stop` operation on the replica set.
		// 不清楚这段注释说的啥，如何stop一个RS ?
		DeleteFunc: rsc.enqueueReplicaSet,
	})
	rsc.rsLister = rsInformer.Lister()
	rsc.rsListerSynced = rsInformer.Informer().HasSynced

	podInformer.Informer().AddEventHandler(cache.ResourceEventHandlerFuncs{
		AddFunc: rsc.addPod,
		// This invokes the ReplicaSet for every pod change, eg: host assignment. Though this might seem like
		// overkill the most frequent pod update is status, and the associated ReplicaSet will only list from
		// local storage, so it should be ok.
		UpdateFunc: rsc.updatePod,
		DeleteFunc: rsc.deletePod,
	})
	rsc.podLister = podInformer.Lister()
	rsc.podListerSynced = podInformer.Informer().HasSynced

	rsc.syncHandler = rsc.syncReplicaSet
	return rsc
}
```

先看上面的`enqueueReplicaSet`是怎么实现的，就是取出object的key加入到队列。

```go
// 参数obj要么是*extensions.ReplicaSet, 要么是DeletionFinalStateUnknown，后者是添加到DeltaFIFO中的一个占位元素
// 用来表示元素已经删除的，防止watch event事件丢失
func (rsc *ReplicaSetController) enqueueReplicaSet(obj interface{}) {
	// 这个keyFunc默认就是返回 “namespace/name”的字符串
	key, err := controller.KeyFunc(obj)
	if err != nil {
		utilruntime.HandleError(fmt.Errorf("couldn't get key for object %+v: %v", obj, err))
		return
	}
	// 将“namespace/name”添加到队列中
	rsc.queue.Add(key)
}
```

##### pod事件处理
然后是pod的处理事件，也看一下，这部分代码还是比较多，总的来说，pod的事件处理函数有下面几点：
* 找出pod的controller，忽略controller不是rs的pod，对于没有controller的orphan pod，列举出所有的rs，看看这个pod能否匹配到其中的rs
* 将pod对应的rs加入到同步队列
* 一些具体业务逻辑的处理，比如删除事件中的DeletedFinalStateUnknown，label变化，DeletionTimestamp不为nil等。

所以主要逻辑还是将rs加入到同步队列

```go
// When a pod is created, enqueue the replica set that manages it and update its expectations.
// pod创建的时候，将其相关的replica set放到同步队列，并更新expectations，
func (rsc *ReplicaSetController) addPod(obj interface{}) {
	pod := obj.(*v1.Pod)

	if pod.DeletionTimestamp != nil {
		// 当controller manager重启时，可能会收到一个新的pod，但是这个pod其实是在pending deletion状态
		// 直接删除这个pod好了
		rsc.deletePod(pod)
		return
	}

	// 返回这个pod的ownerReference
	if controllerRef := metav1.GetControllerOf(pod); controllerRef != nil {

		// 这里只处理ownerReference是ReplicaSet的，其他Controller不考虑
	    // 符合条件时，返回一个ReplicaSet，否则返回nil
		rs := rsc.resolveControllerRef(pod.Namespace, controllerRef)
		if rs == nil {
			return
		}
		rsKey, err := controller.KeyFunc(rs)
		if err != nil {
			return
		}
		glog.V(4).Infof("Pod %s created: %#v.", pod.Name, pod)
		rsc.expectations.CreationObserved(rsKey)
		// 将此pod对应的RS加入到队列
		rsc.enqueueReplicaSet(rs)
		return
	}

	// 没有拿到这个pod的ownerreference，说明这是一个Orphan Pod，那除了ownerReference，RS还有一层约束关系，
	// 也是主要的约束关系，那就是label，下面的方法列举所有的RS，看看这个pod是否符合这个RS的label，如果符合
	// 就加入到一个数组，将返回的所有的RS加入到同步队列
	rss := rsc.getPodReplicaSets(pod)
	if len(rss) == 0 {
		return
	}
	glog.V(4).Infof("Orphan Pod %s created: %#v.", pod.Name, pod)
	for _, rs := range rss {
		rsc.enqueueReplicaSet(rs)
	}
}

// 当收到pod的update事件的时候，找出对应的rs并放入同步队列（这个叫wake them up很形象，又有点恐怖）
// 可能比较复杂的有点就是，pod的label会变，这个有点变态，为啥要改pod的label，在这种情况下，把涉及到的
// rs都找出来，然后唤醒，
func (rsc *ReplicaSetController) updatePod(old, cur interface{}) {
	curPod := cur.(*v1.Pod)
	oldPod := old.(*v1.Pod)
	if curPod.ResourceVersion == oldPod.ResourceVersion {
		// 周期性的resync会给所有的pod发送update事件，（这句话的意思是，这类update事件的两个pod ResourceVersion是相同的）
		// 除此之外，两个pod的RV总是不同的
		return
	}

    // label有变化
	labelChanged := !reflect.DeepEqual(curPod.Labels, oldPod.Labels)
	if curPod.DeletionTimestamp != nil {
		// 当一个pod执行优雅删除（deleted gracefully）的时候，DeletionTimestamp首先被设置，用来表示这个grace period。
		// 当这个grace period过去的时候，kubelet会从apiserver中删除这个pod。
		// 当我们收到因DeletionTimestamp修改而导致的update事件后，我们期望能快速的重新创建一个副本以保证replicas数量，而不是等待kubelet真正的去
		// 删除它，
	    // This is different from the Phase of a pod changing, because
		// an rs never initiates a phase change, and so is never asleep waiting for the same.
		rsc.deletePod(curPod)
		if labelChanged {
			// we don't need to check the oldPod.DeletionTimestamp because DeletionTimestamp cannot be unset.
			rsc.deletePod(oldPod)
		}
		return
	}

	curControllerRef := metav1.GetControllerOf(curPod)
	oldControllerRef := metav1.GetControllerOf(oldPod)
	controllerRefChanged := !reflect.DeepEqual(curControllerRef, oldControllerRef)
	if controllerRefChanged && oldControllerRef != nil {
		// The ControllerRef was changed. Sync the old controller, if any.
		if rs := rsc.resolveControllerRef(oldPod.Namespace, oldControllerRef); rs != nil {
			rsc.enqueueReplicaSet(rs)
		}
	}

	// If it has a ControllerRef, that's all that matters.
	if curControllerRef != nil {
		rs := rsc.resolveControllerRef(curPod.Namespace, curControllerRef)
		if rs == nil {
			return
		}
		glog.V(4).Infof("Pod %s updated, objectMeta %+v -> %+v.", curPod.Name, oldPod.ObjectMeta, curPod.ObjectMeta)
		rsc.enqueueReplicaSet(rs)
		// TODO: MinReadySeconds in the Pod will generate an Available condition to be added in
		// the Pod status which in turn will trigger a requeue of the owning replica set thus
		// having its status updated with the newly available replica. For now, we can fake the
		// update by resyncing the controller MinReadySeconds after the it is requeued because
		// a Pod transitioned to Ready.
		// Note that this still suffers from #29229, we are just moving the problem one level
		// "closer" to kubelet (from the deployment to the replica set controller).
		if !podutil.IsPodReady(oldPod) && podutil.IsPodReady(curPod) && rs.Spec.MinReadySeconds > 0 {
			glog.V(2).Infof("%v %q will be enqueued after %ds for availability check", rsc.Kind, rs.Name, rs.Spec.MinReadySeconds)
			// Add a second to avoid milliseconds skew in AddAfter.
			// See https://github.com/kubernetes/kubernetes/issues/39785#issuecomment-279959133 for more info.
			rsc.enqueueReplicaSetAfter(rs, (time.Duration(rs.Spec.MinReadySeconds)*time.Second)+time.Second)
		}
		return
	}

	// Otherwise, it's an orphan. If anything changed, sync matching controllers
	// to see if anyone wants to adopt it now.
	if labelChanged || controllerRefChanged {
		rss := rsc.getPodReplicaSets(curPod)
		if len(rss) == 0 {
			return
		}
		glog.V(4).Infof("Orphan Pod %s updated, objectMeta %+v -> %+v.", curPod.Name, oldPod.ObjectMeta, curPod.ObjectMeta)
		for _, rs := range rss {
			rsc.enqueueReplicaSet(rs)
		}
	}
}

// 当收到删除pod事件的时候，对象有可能是pod，也有可能是DeletedFinalStateUnknown，为什么会是后面一个对象，看注释的意思是
// 防止漏掉delete事件，总的来说不是很理解
// When a pod is deleted, enqueue the replica set that manages the pod and update its expectations.
// obj could be an *v1.Pod, or a DeletionFinalStateUnknown marker item.
func (rsc *ReplicaSetController) deletePod(obj interface{}) {
	pod, ok := obj.(*v1.Pod)

	// When a delete is dropped, the relist will notice a pod in the store not
	// in the list, leading to the insertion of a tombstone object which contains
	// the deleted key/value. Note that this value might be stale. If the pod
	// changed labels the new ReplicaSet will not be woken up till the periodic resync.
	// TODO 现在遇到一个问题，DeletedFinalStateUnknown这个object是什么时候加进去的，怎么加进去的
	// 这个对象只有在delete事件的时候才会有
	if !ok {
		tombstone, ok := obj.(cache.DeletedFinalStateUnknown)
		if !ok {
			utilruntime.HandleError(fmt.Errorf("couldn't get object from tombstone %+v", obj))
			return
		}
		pod, ok = tombstone.Obj.(*v1.Pod)
		if !ok {
			utilruntime.HandleError(fmt.Errorf("tombstone contained object that is not a pod %#v", obj))
			return
		}
	}

    // 是个没有controller的orphon pod，直接返回
	controllerRef := metav1.GetControllerOf(pod)
	if controllerRef == nil {
		// No controller should care about orphans being deleted.
		return
	}

	// 拿到对应的rs，然后将这个rs放入到队列，如果controller不是rs，直接返回
	rs := rsc.resolveControllerRef(pod.Namespace, controllerRef)
	if rs == nil {
		return
	}
	rsKey, err := controller.KeyFunc(rs)
	if err != nil {
		return
	}
	glog.V(4).Infof("Pod %s/%s deleted through %v, timestamp %+v: %#v.", pod.Namespace, pod.Name, utilruntime.GetCaller(), pod.DeletionTimestamp, pod)
	rsc.expectations.DeletionObserved(rsKey, controller.PodKey(pod))
	rsc.enqueueReplicaSet(rs)
}
```

##### 队列同步
下面看ReplicaSet是如何同步队列的。下面是`processNextWorkItem`，就是不断从队列取出元素，然后交给`syncHandler`处理，
```go
func (rsc *ReplicaSetController) processNextWorkItem() bool {
	key, quit := rsc.queue.Get()
	if quit {
		return false
	}
	defer rsc.queue.Done(key)

	err := rsc.syncHandler(key.(string))
	if err == nil {
		rsc.queue.Forget(key)
		return true
	}

	utilruntime.HandleError(fmt.Errorf("Sync %q failed with %v", key, err))
	rsc.queue.AddRateLimited(key)

	return true
}
```
`syncHandler`是下面的函数，按照上文描述的，一般收到rs的事件或者pod的事件的时候，我们都是要把对应的rs入队，所以在队列这边，我们就只知道了关于rs的信息，
其他信息就不了解了，那我们看一下ReplicaSetController是如何处理同步的。

```go
// syncReplicaSet will sync the ReplicaSet with the given key if it has had its expectations fulfilled,
// meaning it did not expect to see any more of its pods created or deleted. This function is not meant to be
// invoked concurrently with the same key.
func (rsc *ReplicaSetController) syncReplicaSet(key string) error {

	// 分离namespace以及name
	namespace, name, err := cache.SplitMetaNamespaceKey(key)
	if err != nil {
		return err
	}
	// 在cache中没有发现，认为直接删除了，返回nil，认为是正常的
	rs, err := rsc.rsLister.ReplicaSets(namespace).Get(name)
	if errors.IsNotFound(err) {
		glog.V(4).Infof("%v %v has been deleted", rsc.Kind, key)
		rsc.expectations.DeleteExpectations(key)
		return nil
	}
	if err != nil {
		return err
	}

	rsNeedsSync := rsc.expectations.SatisfiedExpectations(key)
	selector, err := metav1.LabelSelectorAsSelector(rs.Spec.Selector)
	if err != nil {
		utilruntime.HandleError(fmt.Errorf("Error converting pod selector to selector: %v", err))
		return nil
	}

	//　列出缓存中的所有的pod，包括selector已经不匹配但是仍然含有一个旧的controller ref
	allPods, err := rsc.podLister.Pods(rs.Namespace).List(labels.Everything())
	if err != nil {
		return err
	}
	// Ignore inactive pods.
	var filteredPods []*v1.Pod
	for _, pod := range allPods {
		if controller.IsPodActive(pod) {
			filteredPods = append(filteredPods, pod)
		}
	}

	// 注意：filteredPods是一些指向内存的指针，如果要修改他们，需要先做一个深拷贝
	filteredPods, err = rsc.claimPods(rs, selector, filteredPods)
	if err != nil {
		return err
	}

	var manageReplicasErr error
	if rsNeedsSync && rs.DeletionTimestamp == nil {
		// 这里是pod create以及delete的入口
		manageReplicasErr = rsc.manageReplicas(filteredPods, rs)
	}

	// 这里做了一个深拷贝
	rs = rs.DeepCopy()
	newStatus := calculateStatus(rs, filteredPods, manageReplicasErr)

	// 没当pod启动或者die的时候，都要更新rs status
	updatedRS, err := updateReplicaSetStatus(rsc.kubeClient.ExtensionsV1beta1().ReplicaSets(rs.Namespace), rs, newStatus)
	if err != nil {
		// Multiple things could lead to this update failing. Requeuing the replica set ensures
		// Returning an error causes a requeue without forcing a hotloop
		return err
	}
	// Resync the ReplicaSet after MinReadySeconds as a last line of defense to guard against clock-skew.
	if manageReplicasErr == nil && updatedRS.Spec.MinReadySeconds > 0 &&
		updatedRS.Status.ReadyReplicas == *(updatedRS.Spec.Replicas) &&
		updatedRS.Status.AvailableReplicas != *(updatedRS.Spec.Replicas) {
		rsc.enqueueReplicaSetAfter(updatedRS, time.Duration(updatedRS.Spec.MinReadySeconds)*time.Second)
	}
	return manageReplicasErr
}
```
在上面代码中，`manageReplicas`是主要的删除和添加pod的入口，稍微分析一下
```go
func (rsc *ReplicaSetController) manageReplicas(filteredPods []*v1.Pod, rs *extensions.ReplicaSet) error {
	diff := len(filteredPods) - int(*(rs.Spec.Replicas))
	rsKey, err := controller.KeyFunc(rs)
	if err != nil {
		utilruntime.HandleError(fmt.Errorf("Couldn't get key for %v %#v: %v", rsc.Kind, rs, err))
		return nil
	}
	// 目前副本数太少了，需要create
	if diff < 0 {
		diff *= -1
		if diff > rsc.burstReplicas {
			diff = rsc.burstReplicas
		}
		// TODO: Track UIDs of creates just like deletes. The problem currently
		// is we'd need to wait on the result of a create to record the pod's
		// UID, which would require locking *across* the create, which will turn
		// into a performance bottleneck. We should generate a UID for the pod
		// beforehand and store it via ExpectCreations.
		rsc.expectations.ExpectCreations(rsKey, diff)
		glog.V(2).Infof("Too few replicas for %v %s/%s, need %d, creating %d", rsc.Kind, rs.Namespace, rs.Name, *(rs.Spec.Replicas), diff)
	
		// 采用慢启动方式create pod，SlowStartInitialBatchSize的值是1，因此create pod的数量依次是：
		// 1, 2, 4, 8, ...
		successfulCreations, err := slowStartBatch(diff, controller.SlowStartInitialBatchSize, func() error {
			boolPtr := func(b bool) *bool { return &b }
			controllerRef := &metav1.OwnerReference{
				APIVersion:         rsc.GroupVersion().String(),
				Kind:               rsc.Kind,
				Name:               rs.Name,
				UID:                rs.UID,
				BlockOwnerDeletion: boolPtr(true),
				Controller:         boolPtr(true),
			}
			err := rsc.podControl.CreatePodsWithControllerRef(rs.Namespace, &rs.Spec.Template, rs, controllerRef)
			if err != nil && errors.IsTimeout(err) {
				// Pod is created but its initialization has timed out.
				// If the initialization is successful eventually, the
				// controller will observe the creation via the informer.
				// If the initialization fails, or if the pod keeps
				// uninitialized for a long time, the informer will not
				// receive any update, and the controller will create a new
				// pod when the expectation expires.
				return nil
			}
			return err
		})

		// Any skipped pods that we never attempted to start shouldn't be expected.
		// The skipped pods will be retried later. The next controller resync will
		// retry the slow start process.
		if skippedPods := diff - successfulCreations; skippedPods > 0 {
			glog.V(2).Infof("Slow-start failure. Skipping creation of %d pods, decrementing expectations for %v %v/%v", skippedPods, rsc.Kind, rs.Namespace, rs.Name)
			for i := 0; i < skippedPods; i++ {
				// Decrement the expected number of creates because the informer won't observe this pod
				rsc.expectations.CreationObserved(rsKey)
			}
		}
		return err
	// 需要删除pod
	} else if diff > 0 {
		if diff > rsc.burstReplicas {
			diff = rsc.burstReplicas
		}
		glog.V(2).Infof("Too many replicas for %v %s/%s, need %d, deleting %d", rsc.Kind, rs.Namespace, rs.Name, *(rs.Spec.Replicas), diff)

		// Choose which Pods to delete, preferring those in earlier phases of startup.
		podsToDelete := getPodsToDelete(filteredPods, diff)

		// Snapshot the UIDs (ns/name) of the pods we're expecting to see
		// deleted, so we know to record their expectations exactly once either
		// when we see it as an update of the deletion timestamp, or as a delete.
		// Note that if the labels on a pod/rs change in a way that the pod gets
		// orphaned, the rs will only wake up after the expectations have
		// expired even if other pods are deleted.
		rsc.expectations.ExpectDeletions(rsKey, getPodKeys(podsToDelete))

		errCh := make(chan error, diff)
		var wg sync.WaitGroup
		wg.Add(diff)
		for _, pod := range podsToDelete {
			go func(targetPod *v1.Pod) {
				defer wg.Done()
				if err := rsc.podControl.DeletePod(rs.Namespace, targetPod.Name, rs); err != nil {
					// Decrement the expected number of deletes because the informer won't observe this deletion
					podKey := controller.PodKey(targetPod)
					glog.V(2).Infof("Failed to delete %v, decrementing expectations for %v %s/%s", podKey, rsc.Kind, rs.Namespace, rs.Name)
					rsc.expectations.DeletionObserved(rsKey, podKey)
					// 如果删除发生错误，向这个channel中写入错入
					errCh <- err
				}
			}(pod)
		}
		// 等待所有删除完成
		wg.Wait()

		select {
		case err := <-errCh:
			// 至少有一个delete操作发生了错误，返回该错误
			if err != nil {
				return err
			}
		default:
		}
	}

	return nil
}
```
