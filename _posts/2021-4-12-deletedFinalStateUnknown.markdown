---
layout:     post
title:      "事件处理中的 DeletedFinalStateUnknown 是什么"
date:       2021-4-12 10:10:00
author:     "weak old dog"
header-img-credit: false
tags:
    - K8s
---

我们在 K8s 中处理 delete 事件时，有可能传入的资源并不是我们所关注的资源，而是一个 `DeletedFinalStateUnknown`，以`sig-storage-local-static-provisioner` 项目为例，处理 pv 事件的方法如下，可以看到在处理 delete 事件时，如果类型断言不成功，需要看一下是不是`cache.DeletedFinalStateUnknown`类型。
```go
sharedInformer.Informer().AddEventHandler(cache.ResourceEventHandlerFuncs{
	AddFunc: func(obj interface{}) {
		// handle add 
	},
	UpdateFunc: func(oldObj, newObj interface{}) {
		// handle update		
	},
	DeleteFunc: func(obj interface{}) {
		pv, ok := obj.(*v1.PersistentVolume)
		if !ok {
			klog.Warningf("Deleted object is not a v1.PersistentVolume type")
			// When a delete is dropped, the relist will notice a pv in the local cache but not
			// in the list, leading to the insertion of a tombstone object which contains
			// the deleted pv.
			tombstone, ok := obj.(cache.DeletedFinalStateUnknown)
			if !ok {
				klog.Errorf("Unknown object type in delete event %+v", obj)
				return
			}
			pv, ok = tombstone.Obj.(*v1.PersistentVolume)
			if !ok {
				klog.Errorf("Tombstone contained object is not a v1.PersistentVolume %+v", obj)
				return
			}
		}
		// 仍然要处理删除事件
		p.handlePVDelete(pv)
	},
})
```

根据注释，这个是 Reflector 的 ListWatch 在发送Relist的时候（这里的 relist 其实就是 reflector 在 wait 循环里重新调用了一次 list 方法），发现最新 list的对象没有这个 object，但是本地 cache 里却有这个 object，也就是本地 cache 比 etcd 多出来一些资源。那对于多出来的资源，就会被 Reflector 以`DeletedFinalStateUnknown` 的形式添加到 Reflector 的 DeltaFIFO 队列中，并将事件类型标记为 `Deleted` 表示这个资源从etcd中删除了。Replace 代码如下：
```go
func (f *DeltaFIFO) Replace(list []interface{}, resourceVersion string) error {
	f.lock.Lock()
	defer f.lock.Unlock()
	keys := make(sets.String, len(list))
	
	// 省去其他代码... 

	if f.knownObjects == nil {
		// Do deletion detection against our own list.
		queuedDeletions := 0
		for k, oldItem := range f.items {
			if keys.Has(k) {
				continue
			}
			var deletedObj interface{}
			if n := oldItem.Newest(); n != nil {
				deletedObj = n.Object
			}
			queuedDeletions++
			// 添加DeletedFinalStateUnknown
			if err := f.queueActionLocked(Deleted, DeletedFinalStateUnknown{k, deletedObj}); err != nil {
				return err
			}
		}
		return nil
	}

	// Detect deletions not already in the queue.
	knownKeys := f.knownObjects.ListKeys()
	queuedDeletions := 0
	for _, k := range knownKeys {
		if keys.Has(k) {
			continue
		}

		deletedObj, exists, err := f.knownObjects.GetByKey(k)
		if err != nil {
			deletedObj = nil
			klog.Errorf("Unexpected error %v during lookup of key %v, placing DeleteFinalStateUnknown marker without object", err, k)
		} else if !exists {
			deletedObj = nil
			klog.Infof("Key %v does not exist in known objects store, placing DeleteFinalStateUnknown marker without object", k)
		}
		queuedDeletions++
		// 添加DeletedFinalStateUnknown
		if err := f.queueActionLocked(Deleted, DeletedFinalStateUnknown{k, deletedObj}); err != nil {
			return err
		}
	}
	return nil
}
```

`Replace` 的代码在添加 DeletedFinalStateUnknown 资源时，其实包含两种 case：
1. knownObjects 为 nil 时只从 reflector 的 item 队列里查看 object 存不存在，knownObject 是获取本地 cache 中资源的方法，（即 indexer 的 ListKeys 以及 GetByKey 方法），一般情况下 knowObjects 是不为空的（从 informer 初始化的情况看也是这样），毕竟 reflector 的主要任务就是更新本地 cache，knownObjects 为 nil 的case 可能是为了测试考虑，ut 中会出现这种 case。
2. knownObjects 不为 nil 的情况，正常情况，查看本地 cache 中是否存在 object。

DeletedFinalStateUnknown 这个资源只有在 `Replace` 这个方法中才会被添加，而 `Replace` 被调用的时机就是在进行全量 list 之后。第一次 list 时本地 cache 是空的，是不会有这个资源的，只有在 relist 时才会发生。

这其实是一种事件丢失的补救手段，那为什么会发生事件丢失呢？或者为什么会发生relist呢？关于后者，我们只要看下Reflector的方法`ListAndWatch`什么情况下会退出就好了，只要`ListAndWatch`退出了，下次执行时又要重新list一下。

一种情况是 watch 开始的 resourceVersion 太小了，server端已经不存在对应记录了，etcd只保存最近5分钟的事件，5分钟之前的resourceVersion都没有了。
```s
W0224 15:34:39.762384       1 reflector.go:302] k8s.io/client-go/informers/factory.go:133: watch of *v1.PersistentVolume ended with: too old resource version: 8834140265 (8835125434)
W0224 15:58:56.777447       1 reflector.go:302] k8s.io/client-go/informers/factory.go:133: watch of *v1.PersistentVolume ended with: too old resource version: 8835256883 (8835485735)
W0224 16:19:23.792176       1 reflector.go:302] k8s.io/client-go/informers/factory.go:133: watch of *v1.PersistentVolume ended with: too old resource version: 8835949058 (8836034791)
W0224 17:26:53.822864       1 reflector.go:302] k8s.io/client-go/informers/factory.go:133: watch of *v1.PersistentVolume ended with: too old resource version: 8836528786 (8837627927)
W0224 17:54:32.840258       1 reflector.go:302] k8s.io/client-go/informers/factory.go:133: watch of *v1.PersistentVolume ended with: too old resource version: 8838377648 (8838666047)
```

另外，watch请求也有超时时间，如果watch请求一段时间内没有收到事件，这个watch也会断开。下面的`TimeoutSeconds`默认设置是5到10分钟。
```go
var (
	// We try to spread the load on apiserver by setting timeouts for
	// watch requests - it is random in [minWatchTimeout, 2*minWatchTimeout].
	minWatchTimeout = 5 * time.Minute
)

timeoutSeconds := int64(minWatchTimeout.Seconds() * (rand.Float64() + 1.0))

options = metav1.ListOptions{
    ResourceVersion: resourceVersion,
    // We want to avoid situations of hanging watchers. Stop any wachers that do not
    // receive any events within the timeout window.
    TimeoutSeconds: &timeoutSeconds,
}
```