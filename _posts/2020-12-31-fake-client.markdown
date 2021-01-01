---
layout:     post
title:      "K8s编写可测试代码"
date:       2021-01-01 13:53:00
author:     "weak old dog"
header-img-credit: false
tags:
    - k8s
---

研究一下K8s测试代码是怎么写的，以[https://github.com/kubernetes-sigs/sig-storage-local-static-provisioner](https://github.com/kubernetes-sigs/sig-storage-local-static-provisioner)为例。

我们有时候需要mock一个apiserver，或者mock一个clientset，这时候该怎么做呢？本文罗里吧嗦研究一下

#### Mock clientset
Discovery的测试代码里有下面的代码片段，由此看出，使用runtime.Object的Slice就可以构造。
```go
import ""k8s.io/client-go/kubernetes/fake""

// 初始化一个runtime.Object类型的slice
objects := make([]runtime.Object, 0)
for _, o := range testStorageClasses {
    objects = append(objects, runtime.Object(o))
}
// 使用上述slice初始化一个clientset
test.client = fake.NewSimpleClientset(objects...)
```
首先看下`runtime.Object`其定义如下，`runtime.Object`是Kubernetes类型系统的基石，K8s中所有的资源都是`runtime.Object`类型，也就是实现了这个接口定义的方法。
```go
// Object interface must be supported by all API types registered with Scheme. Since objects in a scheme are
// expected to be serialized to the wire, the interface an Object must provide to the Scheme allows
// serializers to set the kind, version, and group the object is represented as. An Object may choose
// to return a no-op ObjectKindAccessor in cases where it is not expected to be serialized.
type Object interface {
	GetObjectKind() schema.ObjectKind
	DeepCopyObject() Object
}
```
`schema.ObjectKind`也是一个interface，定义如下，允许通过接口拿到资源的`group/version/kind`，比如pod的group为core（group为core在yaml文件中都可以不写），version为v1，kind为Pod，
```go
// All objects that are serialized from a Scheme encode their type information. This interface is used
// by serialization to set type information from the Scheme onto the serialized version of an object.
// For objects that cannot be serialized or have unique requirements, this interface may be a no-op.
type ObjectKind interface {
	// SetGroupVersionKind sets or clears the intended serialized kind of an object. Passing kind nil
	// should clear the current setting.
	SetGroupVersionKind(kind GroupVersionKind)
	// GroupVersionKind returns the stored group, version, and kind of an object, or nil if the object does
	// not expose or provide these fields.
	GroupVersionKind() GroupVersionKind
}
```
言归正传，我们看下初始化clientset的`NewSimpleClientset`方法：
```go
func NewSimpleClientset(objects ...runtime.Object) *Clientset {
    // 新建一个object tracker，将所有Object添加到tracker中
	o := testing.NewObjectTracker(scheme, codecs.UniversalDecoder())
	for _, obj := range objects {
		if err := o.Add(obj); err != nil {
			panic(err)
		}
	}

	cs := &Clientset{tracker: o}
    cs.discovery = &fakediscovery.FakeDiscovery{Fake: &cs.Fake}
    // 添加默认的reactor，这里的reactor实现就是，调用tracker接口提供的增删改查接口
	cs.AddReactor("*", "*", testing.ObjectReaction(o))
    // 添加watch reactor，这个reactor的实现略负责，就是满足fake client对infromer的实现
    cs.AddWatchReactor("*", func(action testing.Action) (handled bool, ret watch.Interface, err error) {
		gvr := action.GetResource()
		ns := action.GetNamespace()
		watch, err := o.Watch(gvr, ns)
		if err != nil {
			return false, nil, err
		}
		return true, watch, nil
	})
	return cs
}
```
得先看一下`ObjectTracker`这个接口，这个接口持有一个Object的集合，像是一个假的Server，支持对Object的增删改查操作，对Object的操作是根据`GroupVersionResource`的。接口定义如下，tracker的实现中，有个map，用来保存每个`GroupVersionResource`到该资源下的资源列表，`objects map[schema.GroupVersionResource][]runtime.Object`。其Add操作就是添加到这个map中。
```go
type ObjectTracker interface {
	// Add adds an object to the tracker. If object being added is a list, its items are added separately.
	Add(obj runtime.Object) error
	Get(gvr schema.GroupVersionResource, ns, name string) (runtime.Object, error)
	Create(gvr schema.GroupVersionResource, obj runtime.Object, ns string) error
	Update(gvr schema.GroupVersionResource, obj runtime.Object, ns string) error
	List(gvr schema.GroupVersionResource, gvk schema.GroupVersionKind, ns string) (runtime.Object, error)
	Delete(gvr schema.GroupVersionResource, ns, name string) error
	Watch(gvr schema.GroupVersionResource, ns string) (watch.Interface, error)
}
```
以daemonset为例，其gvr以及gvk定义分别为：
```go
// Resource都是小写，并且是复数
var daemonsetsResource = schema.GroupVersionResource{Group: "apps", Version: "v1", Resource: "daemonsets"}
// Kind首字母大写，并且是单数
var daemonsetsKind = schema.GroupVersionKind{Group: "apps", Version: "v1", Kind: "DaemonSet"}
```
mock一个clientset看上去简单一些，把一些前置资源添加到tracker中去即可。返回的数据结构是fake.Clientset
```go
// Clientset implements clientset.Interface. Meant to be embedded into a
// struct to get a default implementation. This makes faking out just the method
// you want to test easier.
type Clientset struct {
    // 内嵌了Fake数据结构，包含执行的Action列表，reactor列表，以及watchReactor列表
	testing.Fake
    discovery *fakediscovery.FakeDiscovery
    // 添加前置资源的tracker
	tracker   testing.ObjectTracker
}
// Fake implements client.Interface. Meant to be embedded into a struct to get
// a default implementation. This makes faking out just the method you want to
// test easier.
type Fake struct {
	sync.RWMutex
	actions []Action // 执行过的action的列表，invoke方法调用时，会将当前action 追加到此列表中
    // reactor列表，每当有事件发生时，遍历此Reactor，如果匹配则执行
	ReactionChain []Reactor
	WatchReactionChain []WatchReactor
	ProxyReactionChain []ProxyReactor
	Resources []*metav1.APIResourceList
}
```

#### 添加Reactor
没有明确的定义说明Reactor是什么，从代码中看，Reactor像是一个事件处理列表，当事件的动作（create/update/delete等）以及resource（persistentvolumes/pods等）匹配时，就会触发Reactor。Discovery代码中的添加的两个Reactor是：
```go
    // 当创建pv时，添加到本地pv cache中
	test.client.PrependReactor("create", "persistentvolumes", func(action core.Action) (bool, runtime.Object, error) {
		if test.apiShouldFail {
			return true, nil, fmt.Errorf("API failed")
		}

		obj := action.(core.CreateAction).GetObject()
		pv := obj.(*v1.PersistentVolume)
		test.cache.AddPV(pv)
		return false, nil, nil
	})
    // 就是追加一个item到Fake的列表中
    func (c *Fake) PrependReactor(verb, resource string, reaction ReactionFunc) {
	    c.ReactionChain = append([]Reactor{&SimpleReactor{verb, resource, reaction}}, c.ReactionChain...)
    }
```
这些reactor是在`Fake`的`Invokes`方法中调用的，Invokes方法在各个action中都会被调用，代码如下：
```go
// Invokes records the provided Action and then invokes the ReactionFunc that
// handles the action if one exists. defaultReturnObj is expected to be of the
// same type a normal call would return.
func (c *Fake) Invokes(action Action, defaultReturnObj runtime.Object) (runtime.Object, error) {
	c.Lock()
	defer c.Unlock()

    // 深拷贝一份，添加到Fake的action列表中
	actionCopy := action.DeepCopy()
	c.actions = append(c.actions, action.DeepCopy())
	for _, reactor := range c.ReactionChain {
		if !reactor.Handles(actionCopy) {
			continue
        }
        // 执行React
		handled, ret, err := reactor.React(actionCopy)
		if !handled {
			continue
		}
		return ret, err
	}
	return defaultReturnObj, nil
}

// 调用create pv时，执行Invokes
func (c *FakePersistentVolumes) Create(persistentVolume *corev1.PersistentVolume) (result *corev1.PersistentVolume, err error) {
	obj, err := c.Fake.
	// 创建一个create pv的action
		Invokes(testing.NewRootCreateAction(persistentvolumesResource, persistentVolume), &corev1.PersistentVolume{})
	if obj == nil {
		return nil, err
	}
	return obj.(*corev1.PersistentVolume), err
}
```
上面代码是讲怎么初始化一个mock client，以及处理事件，那么怎么消费这些资源呢？Discovery测试中方法是遍历Fake的action列表，选出需要的资源，（感觉跟调用apiserver的get方法差不多）
```go
func getAndResetCreatedPVs(cli *fake.Clientset, cache *cache.VolumeCache) map[string]*v1.PersistentVolume {
    pvs := make(map[string]*v1.PersistentVolume)
    // fake client提供了Actions方法，获取所有的action列表，K8s client是没有的，所以参数不能是kubernetes.ClientSset
	for _, action := range cli.Actions() {
        // 看看是不是pv create事件
		if action.Matches("create", "persistentvolumes") {
			obj := action.(core.CreateAction).GetObject()
            pv := obj.(*v1.PersistentVolume)
            // 检查本地缓存中有没有
			if _, exists := cache.GetPV(pv.Name); exists {
				pvs[pv.Name] = pv
			}
		}
	}
	cli.ClearActions()
	return pvs
}
```

日常对K8s client, informer的测试，用fake client就可以了，参考：[如何给k8s服务做单元测试](http://www.14en.com/?p=181)