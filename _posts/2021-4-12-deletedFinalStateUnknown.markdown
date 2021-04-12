---
layout:     post
title:      "关于DeletedFinalStateUnknown"
date:       2020-11-27 10:10:00
author:     "weak old dog"
header-img-credit: false
tags:
    - k8s
---

我们在k8s中处理delete事件时，有可能传入的资源并不是我们期望的资源，而是一个`DeletedFinalStateUnknown`，以`sig-storage-local-static-provisioner`项目为例，处理pv事件的方法如下，可以看到在处理delete事件时，如果类型断言不成功，需要看一下是不是`cache.DeletedFinalStateUnknown`类型。
```go
	sharedInformer.Informer().AddEventHandler(cache.ResourceEventHandlerFuncs{
		AddFunc: func(obj interface{}) {
			pv, ok := obj.(*v1.PersistentVolume)
			if !ok {
				klog.Errorf("Added object is not a v1.PersistentVolume type")
				return
			}
			p.handlePVUpdate(pv)
		},
		UpdateFunc: func(oldObj, newObj interface{}) {
			newPV, ok := newObj.(*v1.PersistentVolume)
			if !ok {
				klog.Errorf("Updated object is not a v1.PersistentVolume type")
				return
			}
			p.handlePVUpdate(newPV)
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
			p.handlePVDelete(pv)
		},
	})
```

根据注释，这个是Reflector的ListWatch在发送Relist的时候（这里的relist其实就是wait循环里重新调用了一次listwatch方法），发现最新list的的对象没有这个object，但是本地cache里却有这个object，也就是本地cache比etcd多出来一些资源。那对于多出来的资源，就会被Reflector以`DeletedFinalStateUnknown`的形式添加到Reflector的DeltaFIFO队列中，并将事件类型标记为`Deleted`表示这个资源从etcd中删除了。如下：
```go
func (f *DeltaFIFO) Replace(list []interface{}, resourceVersion string) error {
	f.lock.Lock()
	defer f.lock.Unlock()
	keys := make(sets.String, len(list))

	for _, item := range list {
		key, err := f.KeyOf(item)
		if err != nil {
			return KeyError{item, err}
		}
		keys.Insert(key)
		if err := f.queueActionLocked(Sync, item); err != nil {
			return fmt.Errorf("couldn't enqueue object: %v", err)
		}
	}

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

		if !f.populated {
			f.populated = true
			// While there shouldn't be any queued deletions in the initial
			// population of the queue, it's better to be on the safe side.
			f.initialPopulationCount = len(list) + queuedDeletions
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

	if !f.populated {
		f.populated = true
		f.initialPopulationCount = len(list) + queuedDeletions
	}

	return nil
}
```

这其实是一种事件丢失的补救手段，那为什么会发生事件丢失呢？或者为什么会发生relist呢？关于后者，我们只要看下Reflector的方法`ListAndWatch`什么情况下会退出就好了，只要`ListAndWatch`退出了，下次执行时又要重新list一下。

一种情况是watch开始的resourceVersion太小了，server端已经不存在对应记录了，etcd只保存最近5分钟的事件，5分钟之前的resourceVersion都没有了。
```s
W0224 15:34:39.762384       1 reflector.go:302] k8s.io/client-go/informers/factory.go:133: watch of *v1.PersistentVolume ended with: too old resource version: 8834140265 (8835125434)
W0224 15:58:56.777447       1 reflector.go:302] k8s.io/client-go/informers/factory.go:133: watch of *v1.PersistentVolume ended with: too old resource version: 8835256883 (8835485735)
W0224 16:19:23.792176       1 reflector.go:302] k8s.io/client-go/informers/factory.go:133: watch of *v1.PersistentVolume ended with: too old resource version: 8835949058 (8836034791)
W0224 17:26:53.822864       1 reflector.go:302] k8s.io/client-go/informers/factory.go:133: watch of *v1.PersistentVolume ended with: too old resource version: 8836528786 (8837627927)
W0224 17:54:32.840258       1 reflector.go:302] k8s.io/client-go/informers/factory.go:133: watch of *v1.PersistentVolume ended with: too old resource version: 8838377648 (8838666047)
W0224 18:30:22.869686       1 reflector.go:302] k8s.io/client-go/informers/factory.go:133: watch of *v1.PersistentVolume ended with: too old resource version: 8839212626 (8839715876)
W0224 19:43:27.892497       1 reflector.go:302] k8s.io/client-go/informers/factory.go:133: watch of *v1.PersistentVolume ended with: too old resource version: 8840261219 (8840346479)
W0224 20:37:03.911906       1 reflector.go:302] k8s.io/client-go/informers/factory.go:133: watch of *v1.PersistentVolume ended with: too old resource version: 8842315974 (8843140207)
W0225 00:50:28.968473       1 reflector.go:302] k8s.io/client-go/informers/factory.go:133: watch of *v1.PersistentVolume ended with: too old resource version: 8843776515 (8845226552)
W0225 06:15:01.029696       1 reflector.go:302] k8s.io/client-go/informers/factory.go:133: watch of *v1.PersistentVolume ended with: too old resource version: 8851020613 (8853640366)
W0225 14:50:39.121279       1 reflector.go:302] k8s.io/client-go/informers/factory.go:133: watch of *v1.PersistentVolume ended with: too old resource version: 8860108671 (8873871992)
W0225 15:20:18.137869       1 reflector.go:302] k8s.io/client-go/informers/factory.go:133: watch of *v1.PersistentVolume ended with: too old resource version: 8874885161 (8875025595)
W0225 15:39:37.158988       1 reflector.go:302] k8s.io/client-go/informers/factory.go:133: watch of *v1.PersistentVolume ended with: too old resource version: 8875623516 (8876042631)
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