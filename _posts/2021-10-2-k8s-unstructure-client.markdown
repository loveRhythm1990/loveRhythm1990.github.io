---
layout:     post
title:      "K8s 里的 Client: Discovery 以及 Unstructured"
date:       2020-11-27 10:10:00
author:     "weak old dog"
header-img-credit: false
tags:
    - Operator
---

**文章目录**
- [概述](#概述)
- [structured client](#structured-client)
	- [处理自定义资源 cr](#处理自定义资源-cr)
- [discovery client](#discovery-client)
- [unstructured client](#unstructured-client)
	- [根据 gvr 获取资源](#根据-gvr-获取资源)
	- [通过 unstructured client 创建资源](#通过-unstructured-client-创建资源)
- [dynamic informer](#dynamic-informer)


### 概述
除了我们常用的 structured client（或typed client），K8s 里还有 discovery client 以及 dynamic client，后者大多数在处理 crd 的情况下比较有用，本文也主要介绍这几种 client 在处理 crd 时的使用方式。

### structured client
这个是我们常用的 client，一般情况下，使用这种 client 的时候，我们对所要处理的资源的 gvk 是比较清楚的，比如 pod，其 group 是空 `""`，version 是 v1, kind 是 pod。在 K8s 中所对应的结构体类型是 corev1.Pod，这个时候，我们可以直接通过下面代码来获取集群中的某个 pod 资源。
```go
pod := &corev1.Pod{}
_ := versionClient.Get(context.TODO, types.NamespacedName{Name:"pod1", Namespace:"default"}, pod)
```
#### 处理自定义资源 cr
在使用 typed client 处理 cr 的时候，首先需要注册 crd 到我们的 scheme 中，然后使用这个 scheme 初始化 client，这个时候就可以像处理 K8s 内置资源一样处理 crd 资源了。我们以 cert-manager 为例说明初始化过程。在下面代码中，我把 import 都列出来了,然后是：

1）初始化自己的一个 scheme，这个 scheme 默认不含有任何内置类型，然后在 init 函数中将 K8s 的默认类型注册到这个 scheme 中；

2）将 cert-manager 的 api 也就是 crd 注册到这个 scheme；

3）初始化一个可用的 K8s client，我们直接初始化了一个 controller-runtime 的 client，一般情况下，我们用这个 client 更多，而不是 K8s 原生的 client。

```go
import (
	cmapi "github.com/cert-manager/cert-manager/pkg/apis/certmanager/v1"
	"k8s.io/apimachinery/pkg/runtime"
	utilruntime "k8s.io/apimachinery/pkg/util/runtime"
	controllerclient "sigs.k8s.io/controller-runtime/pkg/client"
	"k8s.io/client-go/tools/clientcmd"
	clientgoscheme "k8s.io/client-go/kubernetes/scheme" // 这个 package 下有个全局变量 scheme
)
var (
	scheme  = runtime.NewScheme() // our scheme
)
func init() {
	utilruntime.Must(clientgoscheme.AddToScheme(scheme))  // 注册 K8s 默认的 scheme
	utilruntime.Must(cmapi.AddToScheme(scheme))           // 注册 cert-manager api
}

func buildTypedClient(bs []byte) controllerclient.Client {
	clientConfig, _ := clientcmd.Load(bs)
	config, _ := clientcmd.NewDefaultClientConfig(*clientConfig, &clientcmd.ConfigOverrides{}).ClientConfig()
	client, _ := controllerclient.New(config, controllerclient.Options{
		Scheme: scheme,
	})
	return client
}
```

在上面代码中，我们使用 `runtime.NewScheme()` 初始化了一个新的 scheme，另外一种方式是直接使用 clientgoscheme.Scheme，后者是一个全局变量，并且内置了 K8s 里默认 scheme（其实也是在自己 package 的 init 方法中调用了 AddToScheme 方法），这样我们在代码里只需要注册我们自己的 scheme 就可以了，使用方式如下。
```go
import (
	clientgoscheme "k8s.io/client-go/kubernetes/scheme"
)

func init() {
	utilruntime.Must(cmapi.AddToScheme(clientgoscheme.Scheme))  // 注册 cert-manager api
}

//  `buildTypedClient` 的参数为 kubeconfig 文件的 bytes 数组。
func buildTypedClient(bs []byte) controllerclient.Client {
	// ...
	client, _ := controllerclient.New(config, controllerclient.Options{
		Scheme: clientgoscheme.Scheme, // 使用全局变量那个 scheme
	})
	return client
}
```
### discovery client
这个主要用于发现 kube-apiserver 到底都有哪些资源，我们用下面例子说明，我们知道 kubectl 有个命令 `kubectl api-resrouces` 会打印集群中所有资源，其中也包括 crd 资源，其输出如下：
```sh
lr90@sj temp % kubectl api-resources
NAME                              SHORTNAMES   APIVERSION                             NAMESPACED   KIND
bindings                                       v1                                     true         Binding
componentstatuses                 cs           v1                                     false        ComponentStatus
configmaps                        cm           v1                                     true         ConfigMap
endpoints                         ep           v1                                     true         Endpoints
events                            ev           v1                                     true         Event
certificaterequests               cr,crs       cert-manager.io/v1                     true         CertificateRequest
certificates                      cert,certs   cert-manager.io/v1                     true         Certificate
... ...
```
我们也想通过 discovery client 把所有资源都打印出来，可以通过下面代码实现，首先也是初始化一个 client，然后是打印 kube-apiserver 中的所有 group，最后是打印每个 group 中的资源以及 version。
```go
import(
	"k8s.io/client-go/discovery"
)
func buildDiscoverClient(bs []byte) discovery.DiscoveryInterface {
	clientConfig, _ := clientcmd.Load(bs)
	config, _ := clientcmd.NewDefaultClientConfig(*clientConfig,&clientcmd.ConfigOverrides{}).ClientConfig()
	discoveryClient, _ := discovery.NewDiscoveryClientForConfig(config)
	return discoveryClient
}

func main() {
	disClient := buildDiscoverClient([]byte(kubeconfig))
	serverGroups, _, _ := disClient.ServerGroupsAndResources() // 获取所有 group
	
	for _, serverGroup := range serverGroups {
		groupName := serverGroup.Name

		for _, version := range serverGroup.Versions {

			// 获取该 group 及 version 下的所有 resource
			resources, _ := disClient.ServerResourcesForGroupVersion(version.GroupVersion)
			
			for _, resource := range resources.APIResources {
				groupVersion := fmt.Sprintf("%s/%s", groupName, version.Version)
				if groupName == "" {
					groupVersion = version.Version
				}
				// 打印所有的 gvr
				fmt.Printf("%s %s\n", groupVersion, resource.Name)
			}
		}
	}
}
```
代码输出如下，注意所有的资源都是以复数（resources）形式输出的。
```sh
batch/v1 cronjobs/status
batch/v1 jobs
batch/v1 jobs/status
certificates.k8s.io/v1 certificatesigningrequests
... ...
... ...
```

在公司开发中，多个团队之间可能进行协作，有些团队可能并不能知道其他团队 crd 的所有 scheme，在这种情况下，可以通过 discovery client 获取某个 group 下的所有资源种类。不过我感觉将所有的 api 开放出来，供其他团队来注册到 client，是一个更好的实践。

### unstructured client
unstructured client 的作用在于可以不用知道资源的结构，仅仅根据资源的 gvr 就能创建或者获取资源，其中 gvr 的定义为：
```go
gvr := schema.GroupVersionResource{
	Group:    "cert-manager.io",
	Version:  "v1",
	Resource: "certificates",
}
```
在这种情况下，资源的结构是通过 `map[string]interface{}` 来表示的，对应 `pkg/apis/meta/v1/unstructured` package 下的 Unstructured 定义
```go
type Unstructured struct {
	// Object is a JSON compatible map with string, float, int, bool, []interface{}, or
	// map[string]interface{}
	// children.
	Object map[string]interface{}
}
```
Unstructured 虽然是个 `map[string]interface{}` 结构，但是实现了 K8s 里面的 `metav1.Object` 接口，对于一些元信息（meta 部分信息）可以直接通过接口去拿，而不用通过遍历 map 去拿。
```go
type Object interface {
	GetNamespace() string
	SetNamespace(namespace string)
	GetName() string
	SetName(name string)
	GetGenerateName() string
	SetGenerateName(name string)
	GetUID() types.UID
	SetUID(uid types.UID)
	GetResourceVersion() string
	SetResourceVersion(version string)
	GetGeneration() int64
	SetGeneration(generation int64)
	GetSelfLink() string
	SetSelfLink(selfLink string)
	GetCreationTimestamp() Time
	SetCreationTimestamp(timestamp Time)
	GetDeletionTimestamp() *Time
	SetDeletionTimestamp(timestamp *Time)
	GetDeletionGracePeriodSeconds() *int64
	SetDeletionGracePeriodSeconds(*int64)
	GetLabels() map[string]string
	SetLabels(labels map[string]string)
	GetAnnotations() map[string]string
	SetAnnotations(annotations map[string]string)
	GetFinalizers() []string
	SetFinalizers(finalizers []string)
	GetOwnerReferences() []OwnerReference
	SetOwnerReferences([]OwnerReference)
	GetManagedFields() []ManagedFieldsEntry
	SetManagedFields(managedFields []ManagedFieldsEntry)
}
```
下面是使用 unstructured client 的例子。
#### 根据 gvr 获取资源
通过 discovery client 我们可以获取所有的资源种类，unstructued client 可以帮我们把所有资源全部列举出来。我们还是以 cert-manager 为例说明，下面代码使用 unstructued client 列出了所有的 certificate，注意在构建 client 的时候并没有注册 scheme，而是使用了 gvr 来构建对应资源的 client。

```go
import (
	"k8s.io/client-go/dynamic"
)

func buildUnstructuredClient(bs []byte) dynamic.Interface {
	clientConfig, _ := clientcmd.Load(bs)
	config, _ := clientcmd.NewDefaultClientConfig(*clientConfig, &clientcmd.ConfigOverrides{}).ClientConfig()
	dynClient, _ := dynamic.NewForConfig(config)
	return dynClient
}

func main() {
	unstructuredClient := buildUnstructuredClient([]byte(kubeconfig))
	gvr := schema.GroupVersionResource{
		Group:    "cert-manager.io",
		Version:  "v1",
		Resource: "certificates",
	}
	certificates, _ := unstructuredClient.Resource(gvr).List(context.TODO(), metav1.ListOptions{})
	for _, certificate := range certificates.Items {
		fmt.Println(certificate.GetNamespace(), certificate.GetName())
	}
}
```
通过 unstructuredClient 返回的资源类型结构体为 Unstructured。

#### 通过 unstructured client 创建资源
在 controllerruntime client 的接口中，待处理的资源需要实现 `Object` 接口，而 Unstructured 类型实现了这个接口，因此是可以直接通过 Unstructured 来创建资源的，而 Unstructured 类型只有一个 `map[string]interface{}` 字段，所以首先我们需要有资源的 map 描述。有一种 case 是，资源的 scheme 并没有注册到 client 中（所以不能用 typed client），但是我知道资源的结构描述，比如 yaml 结构，我可以将这个 yaml 结构反序列化为 map，然后通过 Unstructured 结构再去创建这个资源。
```go
type Object interface {
	metav1.Object
	runtime.Object
}
// Writer knows how to create, delete, and update Kubernetes objects.
type Writer interface {
	Create(ctx context.Context, obj Object, opts ...CreateOption) error
	Delete(ctx context.Context, obj Object, opts ...DeleteOption) error
	Update(ctx context.Context, obj Object, opts ...UpdateOption) error
	Patch(ctx context.Context, obj Object, patch Patch, opts ...PatchOption) error
	DeleteAllOf(ctx context.Context, obj Object, opts ...DeleteAllOfOption) error
}
```
下面是通过 Unstructured 创建资源的例子。
```go
manifest := `
kind: Certificate
metadata:
  name: a-test-cert
  namespace: default
spec:
  commonName: matrixone
  ...
`
var data map[string]interface{}
if err := yaml.Unmarshal(manifest, &data); err != nil {
	return err
}
obj := &unstructured.Unstructured{Object: data}
client.Create(ctx, obj)
```

### dynamic informer
dynamic 客户端也支持构建 informer，也就是通过 gvr 构建某一种资源的 informer cache。
这个可以参考[The Kubernetes Dynamic Client](https://medium.com/@caiorcferreira/the-kubernetes-dynamic-client-cd14af2047f5)，这里贴一下代码，需要的时候可以参考。

```go
package main
import (
	"fmt"
	utilruntime "k8s.io/apimachinery/pkg/util/runtime"
	"k8s.io/apimachinery/pkg/util/wait"
	"k8s.io/client-go/dynamic"
	"k8s.io/client-go/dynamic/dynamicinformer"
	"k8s.io/client-go/tools/cache"
	"k8s.io/client-go/util/workqueue"
	"time"
)

const maxRetries = 3
var monboDBResource = schema.GroupVersionResource{Group: "mongodbcommunity.mongodb.com", Version: "v1", Resource: "mongodbcommunity"}

type MongoDBController struct {
	informer cache.SharedIndexInformer
	stopper  chan struct{}
	queue    workqueue.RateLimitingInterface
}
func NewMongoDBController(client dynamic.Interface) (*MongoDBController, error) {
	dynInformer := dynamicinformer.NewDynamicSharedInformerFactory(client, 0)
	informer := dynInformer.ForResource(monboDBResource).Informer()
	stopper := make(chan struct{})

	queue := workqueue.NewRateLimitingQueue(workqueue.DefaultControllerRateLimiter())
	informer.AddEventHandler(cache.ResourceEventHandlerFuncs{
		DeleteFunc: func(obj interface{}) {
			key, err := cache.DeletionHandlingMetaNamespaceKeyFunc(obj)
			if err == nil {
				queue.Add(key)
			}
		},
	})

	return &MongoDBController{
		informer: informer,
		queue: queue,
		stopper: stopper,
	}, nil
}

func (m *MongoDBController) Stop() {
	close(m.stopper)
}

func (m *MongoDBController) Run() {
	defer utilruntime.HandleCrash()
	defer m.queue.ShutDown()
	go m.informer.Run(m.stopper)
	// wait for the caches to synchronize before starting the worker
	if !cache.WaitForCacheSync(m.stopper, m.informer.HasSynced) {
		utilruntime.HandleError(fmt.Errorf("timed out waiting for caches to sync"))
		return
	}

	// runWorker will loop until some problem happens. The wait.Until will then restart the worker after one second
	wait.Until(m.runWorker, time.Second, m.stopper)
}

func (m *MongoDBController) runWorker() {
	for {
		key, quit := m.queue.Get()
		if quit {
			return
		}
		err := m.processItem(key.(string))
		if err == nil {
			m.queue.Forget(key)
		} else if m.queue.NumRequeues(key) < maxRetries {
			m.queue.AddRateLimited(key)
		} else {
			m.queue.Forget(key)
			utilruntime.HandleError(err)
		}
		m.queue.Done(key)
	}
}
func (m *MongoDBController) processItem(mongodb string) error {
	// Clean up PVCs
	return nil
}
```