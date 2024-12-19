---
layout:     post
title:      "controllerruntime 中的 cache 与 client 实现"
date:       2023-10-02 10:10:00
author:     "decent"
header-img-credit: false
tags:
    - Operator
---

**目录**
- [cache 问题概述](#cache-问题概述)
- [controllerruntime 的核心接口](#controllerruntime-的核心接口)
	- [Manager 接口](#manager-接口)
	- [Cluster 接口](#cluster-接口)
- [初始化过程](#初始化过程)
	- [cache 初始化](#cache-初始化)
	- [client 初始化](#client-初始化)
	- [informerCache 的 Get 接口](#informercache-的-get-接口)
- [总结](#总结)

### cache 问题概述
近期我们需要将组件部署到客户机房，客户机房对权限要求比较严格，需要我们提供一个 ClusterRole 以及 Role 列表，在梳理 ClusterRole 列表时，发现一个问题，controllerruntime 的缓存机制默认是缓存集群内所有资源的。比如我只想关注 test-ns 命名空间下的 pod 资源，但是缓存默认缓存所有命名空间下的 pod 资源，因此需要 ClusterRole 权限。

问题的解决方式是在初始化 Manager 的时候指定一个命名空间列表，表示只缓存特定命名空间下的资源。本文通过这个问题，研究下 controllerruntime 中的 cache 与client 实现。
```go
mgr, err := ctrl.NewManager(restCfg, ctrl.Options{
	Host:                   "0.0.0.0",
	Port:                   9443,
	MetricsBindAddress:     metricsAddr,
	Cache: cache.Options{
		// 只缓存 test-ns 下面的资源 
		Namespaces: []string{"test-ns"},
	},
})
```

### controllerruntime 的核心接口
在分析 cache 之前，先大概看下 controllerruntime 中的相关结构体，有个感性的认识。
#### Manager 接口
Manager 大概分两部分：Cluster 接口；其他工具接口，其实现者为 controllerManager 结构体。因为内嵌了 cluster.Cluster 接口，因此实际上 Manager 的大多数接口都是 Cluster 接口提供的，比如 cache 和 client 相关接口。

```go
// Manager initializes shared dependencies such as Caches and Clients, and provides them to Runnables.
// A Manager is required to create Controllers.
type Manager interface {
	// Cluster holds a variety of methods to interact with a cluster.
	cluster.Cluster
	Add(Runnable) error
	Elected() <-chan struct{}
	AddHealthzCheck(name string, check healthz.Checker) error
	AddReadyzCheck(name string, check healthz.Checker) error
	Start(ctx context.Context) error
	GetWebhookServer() webhook.Server
	GetLogger() logr.Logger
	GetControllerOptions() config.Controller
}
```

#### Cluster 接口
Cluster 接口是 controllerruntime 实现的主要接口，包括缓存、client 等，其实现者是 cluster 结构体。下面接口中我们先关注两个接口：

1）`GetClient()`：我们最常用的接口（或者说是必用），这个接口默认情况从缓存拿资源，如果资源的缓存不存在，则主动构建一个，这里的缓存是指 informercache。举例来说，如果我们通过下面的语句拿一个 StatefulSet，控制器发现没有缓存 StatefulSet 时，会在后台默认给我们生成一个，即按需生成。
```go
sts := &appsv1.StatefulSet{}
if err := r.Client.Get(context.TODO(), types.NamespacedName{
	Name:      "web",
	Namespace: "default",
}, sts); err != nil {
	fmt.Printf("list statefulset err: %v", err)
}
```

2）`GetAPIReader()`：这个接口生成一个直接跟 apiserver 交互的 reader，只有 get 与 list 接口。

```go
// Cluster provides various methods to interact with a cluster.
type Cluster interface {
	GetHTTPClient() *http.Client
	GetConfig() *rest.Config
	GetCache() cache.Cache
	GetScheme() *runtime.Scheme
	// 获取 client，默认情况下，这个 client 从缓存读数据
	GetClient() client.Client
	GetFieldIndexer() client.FieldIndexer
	GetEventRecorderFor(name string) record.EventRecorder
	GetRESTMapper() meta.RESTMapper
	// 获取一个 Reader
	GetAPIReader() client.Reader
	Start(ctx context.Context) error
}
```
### 初始化过程
在实现一个控制器时，项目的初始化一般分两步：1）初始化一个 Manager；2）用我们的 Manager 初始化一个控制器。本文我们不关注如何实现一个控制器，只关注控制器中的 cache 以及 client 的实现。
```go
// 第一步初始化 Manager
mgr, err := ctrl.NewManager(ctrl.GetConfigOrDie(), ctrl.Options{
	Scheme:                 scheme,
	MetricsBindAddress:     metricsAddr,
	Port:                   9443,
	HealthProbeBindAddress: probeAddr,
	Cache: cache.Options{
		Namespaces: []string{"test-ns"},
	},
})
// 第二步初始化我们的自定义控制器
if err = (&controllers.GuestbookReconciler{
	Client: mgr.GetClient(),
	Reader: mgr.GetAPIReader(),
	Scheme: mgr.GetScheme(),
}).SetupWithManager(mgr); err != nil {
	setupLog.Error(err, "unable to create controller", "controller", "Guestbook")
	os.Exit(1)
}
``` 

#### cache 初始化
因为 client 的初始化是依赖 cache 的，因此我们先关注 cache 的初始化。cache 的初始化是在 Cluster 接口中做的。
```go
// pkg/cluster/cluster.go
func setOptionsDefaults(options Options, config *rest.Config) (Options, error) {
	if options.NewCache == nil {
		options.NewCache = cache.New
	}
	// ...
}
```
上面代码中的 `cache.New` 是默认方法，位于文件 `pkg/cache/cache.go` 中。下面是其代码实现，在下面情况下，只是配置了一个 namespace 的情况，多个命名空间会走 newMultiNamespaceCache 方法。
```go
func New(config *rest.Config, opts Options) (Cache, error) {
	// ...
	return &informerCache{
		scheme: opts.Scheme,
		Informers: internal.NewInformers(config, &internal.InformersOpts{
			HTTPClient:   opts.HTTPClient,
			Scheme:       opts.Scheme,
			Mapper:       opts.Mapper,
			ResyncPeriod: *opts.SyncPeriod,
			Namespace:    opts.Namespaces[0],
			ByGVK:        byGVK,
		}),
	}, nil
}
```
因此，我们知道 controllerruntime 的 cache 实现是 informerCache，后面我们看 cache 相关接口时，可以直接看 informerCache 的相关实现。

#### client 初始化
client 的初始化同样是在初始化 Cluster 接口时候做的，下面的 client.New 是在 `pkg/client/client.go` 方法中实现的。在该方法的注释中，说生成的不带 cache 的 client，这个其实是有误的（看代码的时候给我造成了很大的误解）。因为如果 Options 中带有 cache，则走的是 cache，而且默认情况下是走 cache 的。
```go
// pkg/cluster/cluster.go
func setOptionsDefaults(options Options, config *rest.Config) (Options, error) {
	if options.NewClient == nil {
		options.NewClient = client.New
	}
	// ...
	return options, nil
}

// pkg/client/client.go，注意下面注释有误

// New returns a new Client using the provided config and Options.
// The returned client reads *and* writes directly from the server
// (it doesn't use object caches).
func New(config *rest.Config, options Options) (c Client, err error) {
	c, err = newClient(config, options)
	if err == nil && options.DryRun != nil && *options.DryRun {
		c = NewDryRunClient(c)
	}
	return c, err
}
```

在 Cluster 接口的初始化时，生成两个两个 client：clientWriter 和 clientReader。后者的实现比较明确，是直接跟 K8s apiserver 交互的，不会走 cache（也就是说通过 clientReader 总是从 apiserver 读最新数据）。 我们重点看下 clientWriter 的实现。需要注意的是 clientWriter 最终赋值给了 cluster 的 client 字段，也是我们 Manager 接口 `GetClient()` 方法最终返回的字段。

```go
// New constructs a brand new cluster.
func New(config *rest.Config, opts ...Option) (Cluster, error) {
	options, _ := setOptionsDefaults(options, config)
	cache, _ := options.NewCache(config, cacheOpts)

	// Create the client, and default its options.
	clientOpts := options.Client
	{
		if clientOpts.Cache.Reader == nil {
			clientOpts.Cache.Reader = cache
		}
	}
	// Writer 走 cache
	clientWriter, err := options.NewClient(config, clientOpts)
	// Reader 不走 cache
	clientReader, err := client.New(config, client.Options{
		HTTPClient: options.HTTPClient,
		Scheme:     options.Scheme,
		Mapper:     mapper,
	})
	return &cluster{
		cache:            cache,
		fieldIndexes:     cache,
		client:           clientWriter,
		apiReader:        clientReader,
		// ... 只关注 client 和 cache 字段
	}, nil
}
```
newClient 的实现如下，如果 options 中没有配置 cache，则直接跟 apiserver 交互。否在在调用 Get 方法的时候，从 cache 里拿。
```go
func newClient(config *rest.Config, options Options) (*client, error) {
	c := &client{
		typedClient: typedClient{
			resources:  resources,
			paramCodec: runtime.NewParameterCodec(options.Scheme),
		},
		unstructuredClient: unstructuredClient{
			resources:  resources,
			paramCodec: noConversionParamCodec{},
		},
	}
	if options.Cache == nil || options.Cache.Reader == nil {
		return c, nil
	}
	// client 中的 cache，就是我们上面实现的 informerCache 结构体，但是只用了 Get 和 List
	c.cache = options.Cache.Reader
	return c, nil
}
func (c *client) Get(ctx context.Context, key ObjectKey, obj Object, opts ...GetOption) error {
	if isUncached, err := c.shouldBypassCache(obj); err != nil {
		return err
	} else if !isUncached {
		return c.cache.Get(ctx, key, obj, opts...)
	}
	// ...
}
```

#### informerCache 的 Get 接口
通过上面的分析，我们知道通过 client.Get 接口拿资源时，默认走的是 cache，也就是 informerCache 的接口。其中下面的 `ic.Informers.Get` 方法中，如果没有 gvk 对应的 informer，则会动态创建一个。
```go
// Get implements Reader.
func (ic *informerCache) Get(ctx context.Context, key client.ObjectKey, out client.Object, opts ...client.GetOption) error {
	gvk, err := apiutil.GVKForObject(out, ic.scheme)
	if err != nil {
		return err
	}

	started, cache, err := ic.Informers.Get(ctx, gvk, out)
	if err != nil {
		return err
	}

	if !started {
		return &ErrCacheNotStarted{}
	}
	return cache.Reader.Get(ctx, key, out)
}
```

### 总结
上面我们提到过，在控制器代码中，如果我们使用 client.Get 接口拿资源时，默认是走的 informerCache。还有另一个中情况是，我们在构建一个控制器时，通常会通过 For 或者 Owns 来指定 watch 的资源，这这种情况下，也是共用上面的 informerCache，因此如果我们在初始化 Manager 时指定了命名空间，也会影响到我们的 Controller 可同步到的资源。
```go
// SetupWithManager sets up the controller with the Manager.
func (r *GuestbookReconciler) SetupWithManager(mgr ctrl.Manager) error {
	return ctrl.NewControllerManagedBy(mgr).
		For(&webappv1.Guestbook{}).
		Complete(r)
}
```