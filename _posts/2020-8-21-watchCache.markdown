---
layout:     post
title:      "Apiserver 中缓存层 Cacher 的实现"
date:       2020-08-21 15:22:00
author:     "decent"
header-img-credit: false
tags:
    - K8s
---

因为前段时间集群遇到了 `Too large resource version` 的问题，感觉有必要理解下 Apiserver 中的缓存层是怎么工作的，以及其事件窗口是怎么设计的。本文参考的 K8s 版本为 1.16。缓存层主要设计到三个组件 Cacher、watchCache、cacherWatcher，本文不会面面俱到的介绍，重在理解整个过程。

分析过程以 Deployment 资源为例，在 Apiserver 中，所有资源都提供了 RESTful 风格的 API 接口，client-go 也是通过 RESTful 接口跟 Apiserver 进行通信的，Deployment 的 RESTful 接口定义在 `pkg/registry/apps/deployment/storage/storage.go` 文件中，先从 Deployment Storage 接口的初始说起，然后看下 watchcache 是怎么实例化以及工作的。

### Storage 通用接口层初始化
下面是 Deployment rest 接口的初始化，包括 `Deployment` 资源相关、`Status` 子资源、`Scale` 以及 `Rollback` 请求等。所有 api 都是通过 NewREST 方法返回的，这个方法有个参数 `optsGetter`，我们首先需要搞清楚这个参数的初始化。
```go
func NewStorage(optsGetter generic.RESTOptionsGetter) (DeploymentStorage, error) {
	deploymentRest, deploymentStatusRest, deploymentRollbackRest, err := NewREST(optsGetter)
	if err != nil {
		return DeploymentStorage{}, err
	}
	return DeploymentStorage{
		Deployment: deploymentRest,
		Status:     deploymentStatusRest,
		Scale:      &ScaleREST{store: deploymentRest.Store},
		Rollback:   deploymentRollbackRest,
	}, nil
}
```
这个 NewStorage 的参数 optsGetter 是一个接口，接口定义了一个 `GetRESTOptions` 方法，该方法为指定的资源生成 RESTOptions 选项。
```go
type RESTOptions struct {
	StorageConfig *storagebackend.Config
	Decorator     StorageDecorator

	EnableGarbageCollection bool
	DeleteCollectionWorkers int
	ResourcePrefix          string
	CountMetricPollPeriod   time.Duration
}
type RESTOptionsGetter interface {
	GetRESTOptions(resource schema.GroupResource) (RESTOptions, error)
}
```

经过一系列反向查找，最终找到 optsGetter 的定义为 `SimpleRestOptionsFactory`，其实现了 `GetRESTOptions` 接口，具体实现如下，在 `generic.RESTOption` 的定义中，有个字段 `Decorator` 是我们需要注意的，这个字段的定义是一个方法，即生成底层存储的方法，在 `SimpleRestOptionsFactory` 的实现中，首先生成的是 rawstorage，即对底层的 etcd 的封装，如果开起了 watchCache，则生成的是带 cache 的storage。

下面的代码涉及到 Apiserver 的两个参数：
* --watch-cache (Default: true): 是否开启 watchcache，一般都是 true，这部分暂未了解到需要设置为 false 的场景。
* --watch-cache-sizes (strings): watchcache 的大小，这个需要注意一下配置方式，这里是为需要配置的资源独立设置的，不是针对所有资源的，配置的方式为：`resource[.group]#size,resource[.group]#size,...,`，通过 `resource.group#size` 的方式声明某个资源的 watchCache 大小，多个资源的配置用逗号隔开。如果没有配置这个选项，默认的 cachesize 为 100。

```go
genericConfig.RESTOptionsGetter = &genericoptions.SimpleRestOptionsFactory{Options: etcdOptions}

func (f *SimpleRestOptionsFactory) GetRESTOptions(resource schema.GroupResource) (generic.RESTOptions, error) {
	ret := generic.RESTOptions{
		StorageConfig:           &f.Options.StorageConfig,
		// 默认生成的底层存储是 UndecoratedStorage，这里即是 rawstorage，是直接对 etcd 的封装，透传到 etcd 的
		Decorator:               generic.UndecoratedStorage,
		// ... ... 
	}
	if f.Options.EnableWatchCache {
		// 解析配置的 watchcache size
		sizes, err := ParseWatchCacheSizes(f.Options.WatchCacheSizes)
		if err != nil {
			return generic.RESTOptions{}, err
		}
		cacheSize, ok := sizes[resource]
		if !ok {
			cacheSize = f.Options.DefaultWatchCacheSize
		}
		// 在 Apiserver 的参数中，每个资源都可以单独定义 cacheSize，默认是 100，
		ret.Decorator = genericregistry.StorageWithCacher(cacheSize)
	}
	return ret, nil
}

// Decorator 的定义是 StorageDecorator，该方法返回一个 storage.Interface
type StorageDecorator func(
	config *storagebackend.Config,
	resourcePrefix string,
	keyFunc func(obj runtime.Object) (string, error),
	newFunc func() runtime.Object,
	newListFunc func() runtime.Object,
	getAttrsFunc storage.AttrFunc,
	trigger storage.IndexerFuncs) (storage.Interface, factory.DestroyFunc, error)

```
上面 `Docorator` 装饰器返回的是一个 `storage.Interface` 接口，该接口即为 Apiserver 中存储层的接口，其实现者也为两个，分别是 rawStorage 和 cacheStorage。所以通过上面分析我们可以看出，对于每个资源，都有独立的 `storage.Interface` 实例，该实例最外层是带缓存的，然后这个带缓存的又封装了 rawStorage，在之前的文章中我们分析过，对于写请求，一般是直接委派给 rawStorage 的。


### cache storage 初始化
在上面的通用存储层中，是通过 `genericregistry.StorageWithCacher(cacheSize)` 初始化的的存储实例，其具体实现如下，该方法接收一个 capacity 参数，然后返回生成 cachestorage 的闭包，在下面闭包中，`cacherstorage.NewCacherFromConfig(cacherConfig)` 返回一个 `Cacher` 结构体，该结构体定义为文件 `staging/src/k8s.io/apiserver/pkg/storage/cacher/cacher.go` 中，即是我们带缓存的存储的最终实现了。
```go
// 这个是存储装饰者的定义，这个方法返回一个闭包方法，返回的闭包方法即是生产存储层的实现
func StorageWithCacher(capacity int) generic.StorageDecorator {
	return func(
		storageConfig *storagebackend.Config,
		resourcePrefix string,
		keyFunc func(obj runtime.Object) (string, error),
		newFunc func() runtime.Object,
		newListFunc func() runtime.Object,
		getAttrsFunc storage.AttrFunc,
		triggerFuncs storage.IndexerFuncs) (storage.Interface, factory.DestroyFunc, error) {

		// 首先生成一个 rawStorage，带缓存的 Storage 是在这个基础之上进行封装的
		s, d, err := generic.NewRawStorage(storageConfig)
		if err != nil {
			return s, d, err
		}
		cacherConfig := cacherstorage.Config{
			CacheCapacity:  capacity,
			// 这个 rawStorage 也被封装在了 cachestorage 中
			Storage:        s,
			Versioner:      etcd3.APIObjectVersioner{},
			ResourcePrefix: resourcePrefix,
			KeyFunc:        keyFunc,
			NewFunc:        newFunc,
			NewListFunc:    newListFunc,
			GetAttrsFunc:   getAttrsFunc,
			IndexerFuncs:   triggerFuncs,
			Codec:          storageConfig.Codec,
		}
		// 基于 rawstorage 和一些配置生成 cachestorage，这个就是我们这篇文章的主角了。
		cacher, err := cacherstorage.NewCacherFromConfig(cacherConfig)
		if err != nil {
			return nil, func() {}, err
		}
		// 省略了一些代码...
		return cacher, destroyFunc, nil
	}
}
```
结构体 `Cacher` 比较庞大，但还有有必要贴出来分析一下，首先看结构体注释，注释说这个 Cacher 主要是为 WATCH 以及 LIST 服务的，并且其他大部分操作都委派给了底层的 storage，另外这个结构体实现了 `storage.Interface` 是一个存储层实现。
```go
type Cacher struct {
	incomingHWM storage.HighWaterMark
	// 这个 incoming channel 很重要，用来接收事件，并分配给 watcher，一个 watcher 就是一个 watch 请求
	incoming chan watchCacheEvent

	sync.RWMutex
	ready *ready
	// 底层的 rawStorage
	storage storage.Interface
	// 对底层的 underlying cache 的期望的 object 类型
	objectType reflect.Type
	// 这个 watchCache 就是保存事件的滑动窗口了，这个 watchCache 是有大小的，大小即我们在 apiserver 参数中定义的 watchCache capacity
	// watchCache 中包含两部分数据：包含历史数据的滑动窗口；当前最新缓存。
	watchCache *watchCache
	// 这个就是 client-go 中，我们经常分析的 reflector，这个 reflector 通过对 rawStorage 进行 list-watch 来生产缓存
	reflector  *cache.Reflector
	versioner storage.Versioner
	// 这个 newFunc 用来产生一个（当前资源的）空的对象，这个空的对象保存查询结果
	newFunc func() runtime.Object
	indexedTrigger *indexedTriggerFunc
	// watchers is mapping from the value of trigger function that a
	// watcher is interested into the watchers
	watcherIdx int
	watchers   indexedWatchers

	dispatchTimeoutBudget *timeBudget

	stopLock sync.RWMutex
	stopped  bool
	stopCh   chan struct{}
	stopWg   sync.WaitGroup
	clock clock.Clock
	timer *time.Timer

	dispatching bool
	// watchersBuffer is a list of watchers potentially interested in currently
	// dispatched event.
	watchersBuffer []*cacheWatcher
	// blockedWatchers is a list of watchers whose buffer is currently full.
	blockedWatchers []*cacheWatcher
	// watchersToStop is a list of watchers that were supposed to be stopped
	// during current dispatching, but stopping was deferred to the end of
	// dispatching that event to avoid race with closing channels in watchers.
	watchersToStop []*cacheWatcher
	// Maintain a timeout queue to send the bookmark event before the watcher times out.
	bookmarkWatchers *watcherBookmarkTimeBuckets
	// watchBookmark feature-gate
	watchBookmarkEnabled bool
}
```
在 `Cacher` 的字段中，`watchCache` 也是一个很重要的结构体，我们也贴一下其实现，首先看注释，注释说这个 watchCache 实现了 client-go 的 Store 接口（这个接口就是我们在使用 informer 时的底层的接口），并且是一个具有特定大小的 `sliding window`。

另外需要注意这个 watchCache 是 reflector 通过 listwatch rawstorage 来进行更新的，因为其实现了 client-go 中的 Store 接口，所以是可以当做缓存使用的，我们知道 reflector 对于 cache 更新需要有两个地方：1）全量 list 之后调用 store.Replace；2）在 watchHandler 中当发生事件的时候，调用 store.Add、store.Update、store.Delete 处理事件。在 client-go informer 的实现中，扮演这个 store 角色的是 DeltaFIFO，这个可以参考《[K8s 中的 Informer reflector 实现细节全解析](https://loverhythm1990.github.io/2021/03/23/reflector/)》。既然也是通过 reflector 来更新的，那其 resourceVersion 更新机制应该也是类似。后面我们分析下。

```go
type watchCache struct {
	sync.RWMutex
	// list 接口基于这个 cond 等待最新的 RV，这个 cond 每 3s boradcast 一次
	cond *sync.Cond
	// 滑动窗口的最大值
	capacity int
	keyFunc func(runtime.Object) (string, error)
	getAttrsFunc func(runtime.Object) (labels.Set, fields.Set, error)

	// cache is used a cyclic buffer - its first element (with the smallest
	// resourceVersion) is defined by startIndex, its last element is defined
	// by endIndex (if cache is full it will be startIndex + capacity).
	// Both startIndex and endIndex can be greater than buffer capacity -
	// you should always apply modulo capacity to get an index in cache array.
	// 上面解释很麻烦，我们先记住这个是循环队列就好了
	cache      []*watchCacheEvent
	startIndex int
	endIndex   int
	// 这个就是 client-go 中的 thread-safe store，那个带锁的 map
	// 这个缓存用来保存 etcd 最新的数据
	store cache.Store
	// 当前 watchCache 最新的 resourceVersion
	resourceVersion uint64
	// ResourceVersion of the last list result (populated via Replace() method).
	listResourceVersion uint64
	// This handler is run at the end of every successful Replace() method.
	onReplace func()
	// This handler is run at the end of every Add/Update/Delete method
	// and additionally gets the previous value of the object.
	eventHandler func(*watchCacheEvent)
	clock clock.Clock
	versioner storage.Versioner
}
```
从上面看 watchCache 除了有一个滑动窗口 `cache []*watchCacheEvent`，还有一个本地缓存用来缓存 etcd 的最新数据 `store cache.Store`。

因为这个 watchCache 也是通过 reflector 来更新的，接下来我们看下在 reflector 框架下， watchCacher 是怎么处理事件的，也就是 watchCacher 中 `Replace/Add/Update/Delete` 接口的实现，reflector 在工作过程中会调用这几个接口来更新缓存。

#### watchCache 的 Replace
`Replace` 是 reflector list-watch 中进行全量 list 之后进行的操作，用于替换(更新本地缓存)，这个方法的参数是 list 到的数据，以及 list 返回的 resourceVersion。
```go
func (w *watchCache) Replace(objs []interface{}, resourceVersion string) error {
	version, err := w.versioner.ParseResourceVersion(resourceVersion)

	toReplace := make([]interface{}, 0, len(objs))
	for _, obj := range objs {
		// ... 条件检查
		toReplace = append(toReplace, &storeElement{
			Key:    key,Object: object,Labels: objLabels,Fields: objFields,
		})
	}
	// 将历史数据滑动窗口的两个 index 置为空，清空滑动窗口
	w.startIndex = 0
	w.endIndex = 0
	// 这个是替换底层的缓存数据，threadsafe store
	if err := w.store.Replace(toReplace, resourceVersion); err != nil {
		return err
	}
	// 将 listResourceVersion 和 resourceVersion 都置为 list 得到的 resourceVersion
	w.listResourceVersion = version
	w.resourceVersion = version
	if w.onReplace != nil {
		w.onReplace()
	}
	// 将 cond 进行 Broadcast，如果这个时候，有list 操作正在等待较新的 RV，那它将得到通知
	w.cond.Broadcast()
	klog.V(3).Infof("Replace watchCache (rev: %v) ", resourceVersion)
	return nil
}
```
综上所述，`Replace` 所做的事情有：
* 清空历史数据滑动窗口
* 替换本地缓存
* 更新 listResourceVersion 以及 resourceVersion

#### watchCache 的 Add/Update/Delete
这几个操作都是类似的，我们只以 Add 事件说明，这些回调函数，是 Reflector 在 list-watch 中的 watchHandler 中处理的，也就是处理 watch event 用的，这里的 reflector 是向 rawStorage 发送 list-watch 的，可以理解为向 etcd 发送 list-watch 请求。
```go
// 处理 Add 事件
func (w *watchCache) Add(obj interface{}) error {
	// 得到 Add 事件中，对象的 RV，这个是最新的 RV
	object, resourceVersion, err := w.objectToVersionedRuntimeObject(obj)
	if err != nil {
		return err
	}
	// 生成一个 Add 事件
	event := watch.Event{Type: watch.Added, Object: object}
	// 本地缓存的更新方法，Add 事件是 add 缓存，Delete 事件是 delete 缓存，Update 事件是 update 缓存，这个比较容易理解
	f := func(elem *storeElement) error { return w.store.Add(elem) }
	// 这个 processEvent 就是处理具体的时间了
	return w.processEvent(event, resourceVersion, f)
}
```
我们看到，具体的事件交给了 processEvent 进行处理，整体逻辑也比较简单，快速过一下，
```go
func (w *watchCache) processEvent(event watch.Event, resourceVersion uint64, updateFunc func(*storeElement) error) error {
	key, err := w.keyFunc(event.Object)
	if err != nil {
		return fmt.Errorf("couldn't compute key: %v", err)
	}
	elem := &storeElement{Key: key, Object: event.Object}
	// 获取 labels，fields 等，主要用于过滤，因为客户端在发送 list或watch 请求时，可能设置了 listOption
	elem.Labels, elem.Fields, err = w.getAttrsFunc(event.Object)
	if err != nil {
		return err
	}
	watchCacheEvent := &watchCacheEvent{
		Type:            event.Type,
		Object:          elem.Object,
		ObjLabels:       elem.Labels,
		ObjFields:       elem.Fields,
		Key:             key,
		ResourceVersion: resourceVersion,
	}

	if err := func() error {
		// ... 省去一些代码

		// 这个是更新 watchCache 的滑动窗口
		w.updateCache(watchCacheEvent)
		w.resourceVersion = resourceVersion
		defer w.cond.Broadcast()
		// 调用传入的本地缓存更新方法更新本地缓存，就是 Add/Update/Delete
		return updateFunc(elem)
	}(); err != nil {
		return err
	}
	// 这里的 eventHander 就是 Cacher 结构体的 processEvent 函数，实现比较简单，就是将事件推送给 cacheWatcher。
	if w.eventHandler != nil {
		w.eventHandler(watchCacheEvent)
	}
	return nil
}
```
从代码看出 watchCache 在处理 reflector 的事件时，需要做了三件事：
1. 更新滑动窗口，这里也贴一下更新滑动窗口的代码吧，就是将 endIndex 加 1，另外如果 `w.endIndex == w.startIndex+w.capacity` 条件成立，表明滑动窗口满了，要删除最早进入窗口的事件，即将 startIndex++，另外需要注意，这里的 startIndex 以及 endIndex 都是只增加的，不会减少，额外说明一下，判断是不是空的方法是看看 endIndex是否等于 startIndex，这个跟我们在数据结构中学习的循环队列是一致的。
```go
func (w *watchCache) updateCache(event *watchCacheEvent) {
	if w.endIndex == w.startIndex+w.capacity {
		// Cache is full - remove the oldest element.
		w.startIndex++
	}
	w.cache[w.endIndex%w.capacity] = event
	w.endIndex++
}
```
2. 更新本地缓存，也就是 client-go 中给我们提供的那个 thread-safe store 的实现。当客户端执行 list 操作的时候，会从这个本地缓存取数据，但是有两点需要注意：1）会等到本地缓存的数据比 list 操作指定的 RV 大时再从缓存取，这个是阻塞的（如果等的时间太长了就会有 `Too large resource version`问题）。2）如果 list 操作时，客户端的 RV 为空，即 `""` 则透传 etcd，不使用缓存。 
3. 使用 eventHandler 派发事件，这个是将事件派发给 cacheWatcher，这里 eventHandler 的实现就是 Cacher 的 processEvent 方法。
```go
func (c *Cacher) processEvent(event *watchCacheEvent) {
	if curLen := int64(len(c.incoming)); c.incomingHWM.Update(curLen) {
		klog.V(1).Infof("cacher (%v): %v objects queued in incoming channel.", c.objectType.String(), curLen)
	}
	// 这个 incoming 先发送给 Cacher，再由 Cacher 发给 cacheWatcher.
	c.incoming <- *event
}
```

### cacheWatcher 概述
`Cacher` 在处理 watch 请求的时候，会对每个 watch 请求建立一个 `cacheWatcher`，其目的是将事件发送给客户端，每个 cacheWatcher 对象都会有一个 id，这个 id 是唯一的，并且从 0 开始计数（每当有新的 watch 请求时，该计数就自增1）。cacheWacher 通过 map 进行管理，其中 key 为id，value 为cacheWatcher。
```go
type watchersMap map[int]*cacheWatcher
```

在通过 newCacheWatcher 函数进行实例化时，内部会运行一个 goroutine（watcher.process函数） 来监控 c.input channel 中的数据，当其中没有数据时，处于阻塞状态，当其中有数据时，数据会通过 ResultChan 函数对外暴露，只发送大于 ResourceVersion 的数据。代码如下：
```go
func (c *cacheWatcher) process(ctx context.Context, initEvents []*watchCacheEvent, resourceVersion uint64) {
	// 省去一些代码
	defer close(c.result)
	defer c.Stop()
	for {
		select {
		case event, ok := <-c.input:
			if !ok {
				return
			}
			// 比较 ResourceVersion 大小
			if event.ResourceVersion > resourceVersion {
				c.sendWatchCacheEvent(event)
			}
		case <-ctx.Done():
			return
		}
	}
}
```

下图是描述 kube-apiserve 整个缓存层的设计，参考自《kubernetes源码剖析》，主要工作组件就是 Cacher、watchCache、cacheWatcher，其中 Cacher 全局的缓存层设计，watchCacher 用于保存一个本地缓存以及滑动窗口，cacherWatcher 向每一个 watcher 派送事件。
![java-javascript](/pics/cacherwacher123.jpeg){:height="70%" width="70%"}
