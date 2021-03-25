---
layout:     post
title:      "Reflector工作原理"
date:       2021-3-23 17:31:00
author:     "weak old dog"
header-img-credit: false
tags:
    - k8s
---

之前在[关于k8s informer的基本概念与原理](https://loverhythm1990.github.io/2020/02/25/scheduler-informer1/)中介绍过informer的一些东西，但是没怎么讲清楚，这里再梳理一下知识点。还是以项目[sig-storage-lib-external-provisioner](https://github.com/kubernetes-sigs/sig-storage-lib-external-provisioner)为例，这些项目逻辑比较简单，相对容易分析，下面文中所称的`项目`都指该项目。

##### 主体流程
这里主要是说从声明一个informer，到启动这个informer的过程，这个应该都比较熟悉了。这里再复习一下相关的数据结构，以及它们之间的相互关系。

###### 声明
provisioner项目主要对pvc以及pv感兴趣，所以声明了如下pvc以及pv Informer，另外这里把存放pvc以及pv的本地cache也暴露出来了。
```go
	claimInformer  cache.SharedIndexInformer
	claimsIndexer  cache.Indexer
	volumeInformer cache.SharedInformer
	volumes        cache.Store
```
我们先看下`SharedIndexInformer`以及`SharedInformer`的定义，前者嵌入了后者，并添加了`AddIndexers`以及`GetIndexer`方法，需要注意的是，添加indexer需要在informer启动之前添加，否则会返回错误。`SharedInformer`的接口定义主要包括添加EventHandler，启动informer等。
```go
type SharedIndexInformer interface {
	SharedInformer
	// AddIndexers add indexers to the informer before it starts.
	AddIndexers(indexers Indexers) error
	GetIndexer() Indexer
}
type SharedInformer interface {
	AddEventHandler(handler ResourceEventHandler)
	// 带resync的EventHandler
	AddEventHandlerWithResyncPeriod(handler ResourceEventHandler, resyncPeriod time.Duration)
	// GetStore returns the informer's local cache as a Store.
	GetStore() Store
	// 已废弃
	GetController() Controller
	Run(stopCh <-chan struct{})
	// 至少从Etcd进行过一次FULL LIST之后，HasSynced返回true
	HasSynced() bool
	// LastSyncResourceVersion is the resource version observed when last synced with the underlying
	// store. The value returned is not synchronized with access to the underlying store and is not
	// thread-safe.
	LastSyncResourceVersion() string
	SetWatchErrorHandler(handler WatchErrorHandler) error
}
```
`Store`接口定义了对本地cache的CRUD操作。除了通过`GetStore`接口直接拿到本地cache的操作接口，我们还可以定义一个Lister来对本地cache进行读操作，`Lister`可以通过`labels.Selector`进行选取，也可以通过name获取特定的资源，通过lister获取的资源，必须视为read-only的，否则会修改本地cache，导致跟etcd数据不一致，另外由于本地cache会自动同步，做的修改可能会被同步操作覆盖掉。

一般来讲，我们不需要通过`Store`接口来对资源进行get/delete操作，通过`Lister`接口足够了。`Store`接口也不支持通过`labels.Selector`来进行选择。K8s的informer机制会自动更新本地cache，一种case是省去自动同步的时延，手动添加到本地cache之后，后续的操作都直接能看到。

```go
//Informer = informer.Core().V1().PersistentVolumeClaims().Informer()
pvLister := informer.Core().V1().PersistentVolumeClaims().Lister()
```

###### 初始化及启动
初始化的过程是，通过`SharedInformerFactory`生成特定资源的informer，然后调用这个informer的Run方法，或者直接调用`SharedInformerFactory`的`Start(stopCh <-chan struct{})`方法，但是这里的informer可能由其他的informerFactory生成（在用户定制化informer的情况下），所以这里调用了特定资源的`Run`方法。
 ```go
 	informer := informers.NewSharedInformerFactory(client, controller.resyncPeriod)
    controller.claimInformer = informer.Core().V1().PersistentVolumeClaims().Informer()
    
    claimHandler := cache.ResourceEventHandlerFuncs{
		AddFunc:    func(obj interface{}) { controller.enqueueClaim(obj) },
		UpdateFunc: func(oldObj, newObj interface{}) { controller.enqueueClaim(newObj) },
		DeleteFunc: func(obj interface{}) {},
	}
    controller.claimInformer.AddEventHandler(claimHandler)
    
    if !ctrl.customClaimInformer {
		go ctrl.claimInformer.Run(ctx.Done())
    }
 ```
创建InformerFactory时，除了使用使用默认值时，调用`NewSharedInformerFactory`方法（该方法需要指定一个参数`defaultResync`），还有下面两个方法可用，可以对Informer进行定制化，默认情况下`tweakListOptions`参数为nil。
```go
func NewFilteredSharedInformerFactory(client kubernetes.Interface, defaultResync time.Duration, namespace string, tweakListOptions internalinterfaces.TweakListOptionsFunc) SharedInformerFactory {
	return NewSharedInformerFactoryWithOptions(client, defaultResync, WithNamespace(namespace), WithTweakListOptions(tweakListOptions))
}

// NewSharedInformerFactoryWithOptions constructs a new instance of a SharedInformerFactory with additional options.
func NewSharedInformerFactoryWithOptions(client kubernetes.Interface, defaultResync time.Duration, options ...SharedInformerOption) SharedInformerFactory {
	factory := &sharedInformerFactory{
		client:           client,
		namespace:        v1.NamespaceAll,
		defaultResync:    defaultResync,
		informers:        make(map[reflect.Type]cache.SharedIndexInformer),
		startedInformers: make(map[reflect.Type]bool),
		customResync:     make(map[reflect.Type]time.Duration),
	}

	// Apply all options
	for _, opt := range options {
		factory = opt(factory)
	}
	return factory
}
```

###### 等待同步及启动worker goroutine
 `WaitForCacheSync`是每个informerFactory以及informer启动后都要做的事，根据注释，其含义是等待本地cache至少进行一次`FULL LIST`操作。

 ```go
    if !cache.WaitForCacheSync(ctx.Done(), ctrl.claimInformer.HasSynced, ctrl.volumeInformer.HasSynced, ctrl.classInformer.HasSynced) {
        return
    }

    for i := 0; i < ctrl.threadiness; i++ {
		go wait.Until(func() { ctrl.runClaimWorker(ctx) }, time.Second, ctx.Done())
    }
 ```
`WaitForCacheSync`底层的实现查看Reflector的`pupulated`以及`initialPopulationCount`字段，下面有解释，概括来讲，就是将Reflector的List的操作结果都放到DeltaFifo队列里了，并且又被DeltaFIFO的Pop方法消费了（每调用一次pop，`initialPopulationCount`值就减1），Pop消费的方式之一就是添加到本地cache中。具体实现如下：
```go
func (f *DeltaFIFO) HasSynced() bool {
	f.lock.Lock()
	defer f.lock.Unlock()
	return f.populated && f.initialPopulationCount == 0
}
```

##### Reflector工作原理
在上一小节中，启动informer用的是`claimInformer.Run`方法，这个方法创建一个informer机制中的`controller`，并在运行此controller的时候创建一个`reflector`并运行。`Run`方法如下：
```go
func (s *sharedIndexInformer) Run(stopCh <-chan struct{}) {
	defer utilruntime.HandleCrash()

    // 创建一个Delta队列
	fifo := NewDeltaFIFOWithOptions(DeltaFIFOOptions{
		KnownObjects:          s.indexer,
		EmitDeltaTypeReplaced: true,
	})

	cfg := &Config{
		Queue:            fifo,
		ListerWatcher:    s.listerWatcher,
		ObjectType:       s.objectType,
		FullResyncPeriod: s.resyncCheckPeriod,
		RetryOnError:     false,
		ShouldResync:     s.processor.shouldResync,

		Process:           s.HandleDeltas,
		WatchErrorHandler: s.watchErrorHandler,
	}

	func() {
		s.startedLock.Lock()
		defer s.startedLock.Unlock()
        // 创建controller
		s.controller = New(cfg)
		s.controller.(*controller).clock = s.clock
		s.started = true
	}()

	// Separate stop channel because Processor should be stopped strictly after controller
	processorStopCh := make(chan struct{})
	var wg wait.Group
	defer wg.Wait()              // Wait for Processor to stop
	defer close(processorStopCh) // Tell Processor to stop
	wg.StartWithChannel(processorStopCh, s.cacheMutationDetector.Run)
	wg.StartWithChannel(processorStopCh, s.processor.run)

	defer func() {
		s.startedLock.Lock()
		defer s.startedLock.Unlock()
		s.stopped = true // Don't want any new listeners
	}()
	s.controller.Run(stopCh)
}
```
###### DeltaFIFO队列
`DeltaFIFO`首先是一个FIFO队列，其主要字段是`items`和`queue`，`items`存放元素的key以及事件列表，事件列表`Deltas`也是一个一个Slice，也是有序的。比如：

`default/pod1: update/update/delete`

另外需要注意的是，`queue`字段中不包含重复的元素，在append这个Slice的时候，先检查`items`是否有该元素，如果没有再进行append，也就是下面注释中说的，`items`与`queue`是1：1映射的。

关于`populated`字段，只要调用过DeltaFIFO的`Delete`或者`Add`或者`Update`等方法，这个字段就是被设置为true。Reflactor的WatchHander是DeltaFIFO的生产者，会调用上面几个CRUD方法往DeltaFIFO里填数据。

`initialPopulationCount`字段是第一次调用Replace时，往Delta里填充的资源个数。每次调用DeltaFIFO的`Pop`方法消费资源时，这个数值就会减一。

```go
type DeltaFIFO struct {
	// `items` maps keys to Deltas.
	// `queue` maintains FIFO order of keys for consumption in Pop().
	// We maintain the property that keys in the `items` and `queue` are
	// strictly 1:1 mapping, and that all Deltas in `items` should have
	// at least one Delta.
	items map[string]Deltas
	queue []string

	// populated is true if the first batch of items inserted by Replace() has been populated
	// or Delete/Add/Update/AddIfNotPresent was called first.
	populated bool
	// initialPopulationCount is the number of items inserted by the first call of Replace()
	initialPopulationCount int

	// knownObjects list keys that are "known" --- affecting Delete(),
	// Replace(), and Resync()
    knownObjects KeyListerGetter
    // 省略部分字段...
}

type Delta struct {
	Type   DeltaType
	Object interface{}
}

// Deltas is a list of one or more 'Delta's to an individual object.
// The oldest delta is at index 0, the newest delta is the last one.
type Deltas []Delta
```

###### controller
controller不介绍了，比较容易理解，就是启动reflector，并消费DeltaFIFO的资源，一方面触发EventHandler，另一方面放到本地cache中。

###### reflector
在看`reflector`工作原理之前，先看一下`reflector`的一些参数配置。其创建代码在`controller`的Run方法中，
```go
func (c *controller) Run(stopCh <-chan struct{}) {
	defer utilruntime.HandleCrash()
	go func() {
		<-stopCh
		c.config.Queue.Close()
	}()
	r := NewReflector(
		c.config.ListerWatcher,
		c.config.ObjectType,
		c.config.Queue,
		c.config.FullResyncPeriod,
	)
	r.ShouldResync = c.config.ShouldResync
	r.clock = c.clock
	if c.config.WatchErrorHandler != nil {
		r.watchErrorHandler = c.config.WatchErrorHandler
	}

	c.reflectorMutex.Lock()
	c.reflector = r
	c.reflectorMutex.Unlock()

	var wg wait.Group

	wg.StartWithChannel(stopCh, r.Run)

	wait.Until(c.processLoop, time.Second, stopCh)
	wg.Wait()
}
```
关注下面几个字段：

**ListerWatcher**

我们在启动PVC Informer的时候，并没有指定`ListerWatcher`，那我们看一下默认值是怎么构建的。我们创建informer时，使用的方法为：`claimInformer = informer.Core().V1().PersistentVolumeClaims().Informer()`
其最终调用的方法为`NewFilteredPersistentVolumeClaimInformer`，代码路径为：`client-go/informers/core/v1/persistentvolumeclaim.go`，其中`tweakListOptions`在创建factory时没有指定，默认为nil。

```go
func NewFilteredPersistentVolumeClaimInformer(client kubernetes.Interface, namespace string, resyncPeriod time.Duration, indexers cache.Indexers, tweakListOptions internalinterfaces.TweakListOptionsFunc) cache.SharedIndexInformer {
	return cache.NewSharedIndexInformer(
		&cache.ListWatch{
			ListFunc: func(options metav1.ListOptions) (runtime.Object, error) {
				if tweakListOptions != nil {
					tweakListOptions(&options)
				}
				return client.CoreV1().PersistentVolumeClaims(namespace).List(context.TODO(), options)
			},
			WatchFunc: func(options metav1.ListOptions) (watch.Interface, error) {
				if tweakListOptions != nil {
					tweakListOptions(&options)
				}
				return client.CoreV1().PersistentVolumeClaims(namespace).Watch(context.TODO(), options)
			},
		},
		&corev1.PersistentVolumeClaim{},
		resyncPeriod,
		indexers,
	)
}
```
上面的`NewSharedIndexInformer`最终返回一个`sharedIndexInformer`实例，并将`listerWatcher`以及下面的`resyncPeriod`赋值给`resyncCheckPeriod`等。

**resyncPeriod**

这个参数还是得从Factory的创建说起，我们在创建时指定了一个参数`defaultResync time.Duration`，这个参数最终被赋值给了`sharedInformerFactory`的`defaultResync`字段。
```go
informer := informers.NewSharedInformerFactory(client, controller.resyncPeriod)
```
然后当我们使用`sharedInformerFactory`的方法`InformerFor`为特定资源创建Informer的时候，先检查有没有为该种资源定制化同步时间，没有定制化，就使用factory的`defaultResync`方法，定制化是使用装饰器方法，如下：
```go
func WithCustomResyncConfig(resyncConfig map[v1.Object]time.Duration) SharedInformerOption {
	return func(factory *sharedInformerFactory) *sharedInformerFactory {
		for k, v := range resyncConfig {
			factory.customResync[reflect.TypeOf(k)] = v
		}
		return factory
	}
}
```
此方法跟`WithTweakListOptions`方法使用方式一致。上面提到了在创建`sharedInformerFactory`时，将`resyncPeriod`传递给了`resyncCheckPeriod`，这个参数在创建配置`controller`以及`Reflactor`的config时，再次被赋值给了`Config`的`FullResyncPeriod`，这个参数被传递了多次，还每次都重新起个名字，很绕。
```go
	cfg := &Config{
		Queue:            fifo,
		ListerWatcher:    s.listerWatcher,
		ObjectType:       s.objectType,
		FullResyncPeriod: s.resyncCheckPeriod,
		RetryOnError:     false,
		ShouldResync:     s.processor.shouldResync,

		Process:           s.HandleDeltas,
		WatchErrorHandler: s.watchErrorHandler,
	}
```

**store**
这里只`reflector`中的store，这个store是`DeltaFIFO`。reflector将事件同步到`DeltaFIFO`中，再通过`Pop`方法进行消费。

然后就是`Reflecor`的主要方法`ListAndWatch`，这个方法是通过`wait.BackoffUntil`来运行的，后者不考虑运行函数的返回值，会一直运行直到channel关闭，不过如果`ListAndWatch`失败了，可以通过`watchErrorHandler`来处理。另外`ListAndWatch`本来就是无限循环的，只有在出错时才退出。
```go
	wait.BackoffUntil(func() {
		if err := r.ListAndWatch(stopCh); err != nil {
			r.watchErrorHandler(r, err)
		}
	}, r.backoffManager, true, stopCh)
```
`ListAndWatch`首先从特定的`ResourceVersion`开始List，List之后，拿到最新的`ResourceVersion`然后开始开始watch。
```go
func (r *Reflector) ListAndWatch(stopCh <-chan struct{}) error {
	klog.V(3).Infof("Listing and watching %v from %s", r.expectedTypeName, r.name)
	var resourceVersion string

    // relistResourceVersion决定开始list的版本号，不低于已经存在cache中的版本号
    // 对于错误码410 Gone的情况（对应版本的数据etcd不存在了），这个方法返回""，表示从最新的版本号开始list
	options := metav1.ListOptions{ResourceVersion: r.relistResourceVersion()}

    // 这个匿名函数执行List操作
	if err := func() error {
		initTrace := trace.New("Reflector ListAndWatch", trace.Field{"name", r.name})
		defer initTrace.LogIfLong(10 * time.Second)
        // 这个list变量就是List的结果，是一个ListItem类型，需要转换为对应资源的Slice列表
        var list runtime.Object 
		var paginatedResult bool
		var err error
		listCh := make(chan struct{}, 1)
		panicCh := make(chan interface{}, 1)
		go func() {
			defer func() {
				if r := recover(); r != nil {
					panicCh <- r
				}
			}()
			// 尝试使用分块传输，如果ListWatch不支持，则一次返回所有结果
			pager := pager.New(pager.SimplePageFunc(func(opts metav1.ListOptions) (runtime.Object, error) {
                // 使用定制化的listwatcher进行List，可以只list etcd的部分资源
                return r.listerWatcher.List(opts)
			}))
			switch {
			case r.WatchListPageSize != 0:
				pager.PageSize = r.WatchListPageSize
			case r.paginatedResult:
			case options.ResourceVersion != "" && options.ResourceVersion != "0":
				pager.PageSize = 0
			}

			list, paginatedResult, err = pager.List(context.Background(), options)
			if isExpiredError(err) || isTooLargeResourceVersionError(err) {
				r.setIsLastSyncResourceVersionUnavailable(true)
				// Retry immediately if the resource version used to list is unavailable.
				// The pager already falls back to full list if paginated list calls fail due to an "Expired" error on
				// continuation pages, but the pager might not be enabled, the full list might fail because the
				// resource version it is listing at is expired or the cache may not yet be synced to the provided
				// resource version. So we need to fallback to resourceVersion="" in all to recover and ensure
				// the reflector makes forward progress.
				list, paginatedResult, err = pager.List(context.Background(), metav1.ListOptions{ResourceVersion: r.relistResourceVersion()})
			}
			close(listCh)
		}()
		select {
		case <-stopCh:
			return nil
		case r := <-panicCh:
			panic(r)
		case <-listCh:
		}
		if err != nil {
			return fmt.Errorf("failed to list %v: %v", r.expectedTypeName, err)
		}

		// We check if the list was paginated and if so set the paginatedResult based on that.
		// However, we want to do that only for the initial list (which is the only case
		// when we set ResourceVersion="0"). The reasoning behind it is that later, in some
		// situations we may force listing directly from etcd (by setting ResourceVersion="")
		// which will return paginated result, even if watch cache is enabled. However, in
		// that case, we still want to prefer sending requests to watch cache if possible.
		//
		// Paginated result returned for request with ResourceVersion="0" mean that watch
		// cache is disabled and there are a lot of objects of a given type. In such case,
		// there is no need to prefer listing from watch cache.
		if options.ResourceVersion == "0" && paginatedResult {
			r.paginatedResult = true
		}

		r.setIsLastSyncResourceVersionUnavailable(false) // list was successful
		initTrace.Step("Objects listed")
		listMetaInterface, err := meta.ListAccessor(list)
		if err != nil {
			return fmt.Errorf("unable to understand list result %#v: %v", list, err)
		}
		resourceVersion = listMetaInterface.GetResourceVersion()
		initTrace.Step("Resource version extracted")
		items, err := meta.ExtractList(list)
		if err != nil {
			return fmt.Errorf("unable to understand list result %#v (%v)", list, err)
		}
        initTrace.Step("Objects extracted")
        // syncWith调用DeltaFIFO的Replace，用List的结果，替换DeltaFIFO中的内容
		if err := r.syncWith(items, resourceVersion); err != nil {
			return fmt.Errorf("unable to sync list result: %v", err)
		}
		initTrace.Step("SyncWith done")
		r.setLastSyncResourceVersion(resourceVersion)
		initTrace.Step("Resource version updated")
		return nil
	}(); err != nil {
		return err
	}

	resyncerrc := make(chan error, 1)
	cancelCh := make(chan struct{})
    defer close(cancelCh)
    
    // 下面是resync goroutine，就是将DeltaFIFO中的引用的knownObjects，（也就是本地缓存cache或者indexer），全部添加到
    // DeltaFIFO中，并设置事件类型为sync。周期性的。
	go func() {
		resyncCh, cleanup := r.resyncChan()
		defer func() {
			cleanup() // Call the last one written into cleanup
		}()
		for {
			select {
			case <-resyncCh:
			case <-stopCh:
				return
			case <-cancelCh:
				return
			}
			if r.ShouldResync == nil || r.ShouldResync() {
				klog.V(4).Infof("%s: forcing resync", r.name)
				if err := r.store.Resync(); err != nil {
					resyncerrc <- err
					return
				}
			}
			cleanup()
			resyncCh, cleanup = r.resyncChan()
		}
	}()

    // 这个是watch的循环，只有出错了才返回，返回之后listwatch要重新调用。
	for {
		// give the stopCh a chance to stop the loop, even in case of continue statements further down on errors
		select {
		case <-stopCh:
			return nil
		default:
		}

		timeoutSeconds := int64(minWatchTimeout.Seconds() * (rand.Float64() + 1.0))
		options = metav1.ListOptions{
			ResourceVersion: resourceVersion,
			// We want to avoid situations of hanging watchers. Stop any wachers that do not
			// receive any events within the timeout window.
			TimeoutSeconds: &timeoutSeconds,
			// To reduce load on kube-apiserver on watch restarts, you may enable watch bookmarks.
			// Reflector doesn't assume bookmarks are returned at all (if the server do not support
			// watch bookmarks, it will ignore this field).
			AllowWatchBookmarks: true,
		}

		// start the clock before sending the request, since some proxies won't flush headers until after the first watch event is sent
        start := r.clock.Now()
        // 调用listerWatcher的Watch方法
		w, err := r.listerWatcher.Watch(options)
		if err != nil {
			// If this is "connection refused" error, it means that most likely apiserver is not responsive.
			// It doesn't make sense to re-list all objects because most likely we will be able to restart
			// watch where we ended.
			// If that's the case wait and resend watch request.
			if utilnet.IsConnectionRefused(err) {
				time.Sleep(time.Second)
				continue
			}
			return err
		}

        // 这个方法是阻塞式的，从watch接口的watch.Interface结果中，处理返回的事件
		if err := r.watchHandler(start, w, &resourceVersion, resyncerrc, stopCh); err != nil {
			if err != errorStopRequested {
				switch {
				case isExpiredError(err):
					// Don't set LastSyncResourceVersionUnavailable - LIST call with ResourceVersion=RV already
					// has a semantic that it returns data at least as fresh as provided RV.
					// So first try to LIST with setting RV to resource version of last observed object.
					klog.V(4).Infof("%s: watch of %v closed with: %v", r.name, r.expectedTypeName, err)
				default:
					klog.Warningf("%s: watch of %v ended with: %v", r.name, r.expectedTypeName, err)
				}
			}
			return nil
		}
	}
}
```
上面`watchHandler`是DeltaFIFO的生产者，收到事件之后，添加到DeltaFIFO中。

本文概述了Reflector的工作原理，最后从[Kubernetes源码剖析](https://item.jd.com/12665791.html)中盗一张图：

![java-javascript](/img/in-post/reflector/reflector.jpeg)
