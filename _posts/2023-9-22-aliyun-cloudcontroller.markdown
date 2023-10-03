---
layout:     post
title:      "阿里云的 cluster provider controller 是怎么设计的"
date:       2023-9-20 10:10:00
author:     "decent"
header-img-credit: false
tags:
    - K8s
---

**目录**
- [前言](#前言)
- [整体架构概述](#整体架构概述)
- [详细设计](#详细设计)
  - [provider 层设计](#provider-层设计)
  - [资源模型抽象 model](#资源模型抽象-model)
  - [controller 实现](#controller-实现)
  - [context 上下文](#context-上下文)
- [总结](#总结)

### 前言
最近在做公有云相关工作，也会写一些控制器，公有云配置较多，比较琐碎，写的时候发现有很多冗余，设计上也有一些欠缺，知道阿里云有一个 [Cloud provider controller](https://github.com/kubernetes/cloud-provider-alibaba-cloud)，想看一下是怎么实现的，借鉴一下设计思路。cloud controller 的介绍可以参考文档 [Cloud Controller Manager Administration](https://kubernetes.io/docs/tasks/administer-cluster/running-cloud-controller/)，总结来说，就是公有云厂商在 K8s 集群中运行的控制器，实现集群和公有云基础设施的衔接，一个比较直观的例子是 LoadBalancer 类型的 Service，cluster controller 在监听到 Service 的时候，会使用公有云的 sdk 去创建一个 LB 实例，并配置公有 IP，配置好之后，将这个公有 IP 更新到 Service 上，这样我们使用 kubectl get service 命令查看 service 的时候就能看到这个 IP.

本文就以 LoadBalancer 类型的 service 为例，看下 cloud provider controller 是怎么工作的。

### 整体架构概述
从代码大概上看，其架构自底向上分为 4 层，除去末尾的 context，有点类似于 MVC 设计模式。这四层分别为：
* provider: 通过公有云的 sdk 跟公有云通信，创建配置相关基础设施，如 LB、虚拟机、vpc私有网络等。
* model: 这个是对公有云基础设施的抽象，每个基础设施都对应一个 model，有点类似于 K8s 中资源的概念，其中 model的字段就是基础设施的属性。
* controller: 这里的 controller 也就是 K8s 中的控制器，同时也是 cloud provider 实现的核心业务逻辑。
* context: 执行控制器需要的上下文。

![java-javascript](/pics/alibaba-cloud-provider-arch.jpg){:height="50%" width="50%"}
下面依次看下各个层的设计。

### 详细设计
#### provider 层设计
provider 的设计可以说是跟业务无关的，主要是做两件事：
1. sdk 初始化，初始化各个资源的 client，也就是有一个 client 集合。在我代码实际实现中，是通过 RRSA 认证的，这部分工作不难抽象。
2. 对各种资源执行 CRUD 操作，具体操作是由接口定义的，接口的实现有具体的 Provider，以及 mockProvider。

我们主要关注 2 中的实现，以 SLB 资源为例，其 provider 接口定义如下，另外下面接口中的参数`model.LoadBalancer`就是对 LoadBalancer 资源的抽象，后面再讲；接口中第一个参数 context 在实现中基本没有用到（在 Goland 编辑器里，参数 ctx 都是灰色的），阿里云供应商的 api 不支持使用 context。 
```go
type ILoadBalancer interface {
	// LoadBalancer
	FindLoadBalancer(ctx context.Context, mdl *model.LoadBalancer) error
	CreateLoadBalancer(ctx context.Context, mdl *model.LoadBalancer) error
	DescribeLoadBalancer(ctx context.Context, mdl *model.LoadBalancer) error
	DeleteLoadBalancer(ctx context.Context, mdl *model.LoadBalancer) error
	ModifyLoadBalancerInstanceSpec(ctx context.Context, lbId string, spec string) error
	ModifyLoadBalancerInstanceChargeType(ctx context.Context, lbId string, instanceChargeType string, spec string) error
	SetLoadBalancerDeleteProtection(ctx context.Context, lbId string, flag string) error
	SetLoadBalancerName(ctx context.Context, lbId string, name string) error
	ModifyLoadBalancerInternetSpec(ctx context.Context, lbId string, chargeType string, bandwidth int) error
	SetLoadBalancerModificationProtection(ctx context.Context, lbId string, flag string) error

    // listener
    // vServer
}
```
SLB 的配置是有很多属性的，对于特性属性的修改，供应商提供了特定的接口，比如修改删除保护选项用`SetLoadBalancerDeleteProtection`接口，修改是私网类型还是公网类型用`ModifyLoadBalancerInternetSpec`，所以我们在同步 SLB 配置的时候，可能要跟供应商通信好几次，每次调用 api 做特定的修改，看来官方也没很好的办法来通过一次调用完成。

#### 资源模型抽象 model
每个资源都有自己的模型，slb 的模型如下，可以看到模型基本上就是配置项的集合，每个配置项一个字段。在控制器的实现中，每个资源的 model 分为 localModel 以及 remoteModel，其中 localModel 是 K8s 集群的配置，对于 LB 来说，就是 LoadBalancer service 的配置，localModel 是一种 spec；remoteModel 是供应商那边的配置，甚至可能没有配置，如果还没有创建的话，是一种 status。

这里其实也是借鉴了 K8s 中 spec 以及 status 的思想，来将 spec apply 到供应商那里。构建 localModel 和 remoteModel 都是在控制器实现的，localModel 是通过 service 资源以及 service的

```go
// LoadBalancer represents a AlibabaCloud LoadBalancer.
type LoadBalancer struct {
	NamespacedName        types.NamespacedName
	LoadBalancerAttribute LoadBalancerAttribute
	Listeners             []ListenerAttribute
	VServerGroups         []VServerGroup
}

type LoadBalancerAttribute struct {
	IsUserManaged bool

	// parameters can be modified by annotation
	// values of these parameters can not be set to the default value, no need to use ptr type
	LoadBalancerName             string
	AddressType                  AddressType
	VSwitchId                    string
	NetworkType                  string
	Bandwidth                    int
	InternetChargeType           InternetChargeType
	InstanceChargeType           InstanceChargeType
	DeleteProtection             FlagType
	ModificationProtectionStatus ModificationProtectionType
	ResourceGroupId              string
	LoadBalancerSpec             LoadBalancerSpecType
	MasterZoneId                 string
	SlaveZoneId                  string
	AddressIPVersion             AddressIPVersionType
	Tags                         []tag.Tag

	// parameters are immutable
	RegionId                     string
	LoadBalancerId               string
	LoadBalancerStatus           string
	Address                      string
	VpcId                        string
	CreateTime                   string
	ModificationProtectionReason string
}
```

#### controller 实现
controller 是要实现具体的业务，整个项目的核心在这，好在 cloud provider controller 业务也不复杂。对于 LB 来说，就是 reconcile service 资源，在具体实现中，是 ReconcileService 实现了 controller-manager 中的 reconciler 方法，来同步 service。

下面结构体中的 helper.FinalizerManager 是对 ApiObject 进行 get 或者 set Finalizer的工具方法。ApiObject的定义为
```go
type Object interface {
	metav1.Object
	runtime.Object
}
```
metav1.Object 包含对资源所有元数据进行get和set的方法，包括时间戳、label、annotation等。runtime.Object 包含获取 gvk以及deepcopy的方法。controller-manager 中的 client 也是通过这个对象获取和配置资源的。builder 是构建 Model 的数据结构，通过 service 构建本地 model，通过 api 构建 remote model，applier 类似 `kubectl apply` 命令，是将本地 model 应用到远端 model。其中 applier 是控制器实现的关键。

```go
type ReconcileService struct {
	scheme  *runtime.Scheme
	builder *ModelBuilder
	applier *ModelApplier

	// client
	cloud      prvd.Provider // 所有资源的 provider 集合
	kubeClient client.Client

	logger logr.Logger

	//record event recorder
	record           record.EventRecorder
	finalizerManager helper.FinalizerManager
}
```
我们在观察控制器实现时，关注两件事：1）如何实现 diff; 2）如何将本地配置 apply 到远端。

a. 如何实现 diff。这里 cloud controller 的做法是先判断 service 的 hash 有没有变化，如果有变化，重新计算 hash 值，并执行 apply 操作。在计算 service 的 hash 时，取了 service 的三个字段：1）deletionTimestamp，看看是不是要删除; 2）annotations，阿里云中对LB的特殊配置都是通过 annotation 来管理的; 3）spec。计算 hash 的方式比较 trick，主要注意的地方也有两点：如果把 meta hash 也放到 annotation 中去了，在计算 hash 时要把这个 hash 删除，否则可能出现 hash 值永远不相等的情况；第二点是，要把零值删除。（其实我个人感觉这两点可以不做，hash放到 label中去即可）

> 后面再去拉最新代码的时候，发现已经不是把全部的 spec都计算hash了，而是只选取了下面几个字段：`Ports`,`Type`,`ExternalTrafficPolicy`,`LoadBalancerClass`，这样来说，不影响 LB 属性的 service变化是不会触发 apply 操作的，算是代码上的一个优化。

下面代码先将这些属性放到一个 []interface{}中，然后序列化，然后又反序列化到 interface{} 中，从 json 二进制字符串 反序列化到 interface{}时，结构体被反序列化为了 `map[string]interface{}`  类型，然后通过 `remove` 方法移除 hash label 以及类型的零值，然后就是通过 computeHash 计算hash值。

```go
func GetServiceHash(svc *v1.Service) string {
	var op []interface{}
	// ServiceSpec
	op = append(op, svc.Spec.Ports, svc.Spec.Type, svc.Spec.ExternalTrafficPolicy, svc.Spec.LoadBalancerClass)
	op = append(op, svc.Annotations, svc.DeletionTimestamp)
	return hash.HashObject(op)
}

func HashObject(o interface{}) string {
	data, _ := json.Marshal(o)
	var a interface{}
	err := json.Unmarshal(data, &a)
	if err != nil {
		klog.Errorf("unmarshal: %s", err.Error())
	}
	remove(&a)
	return computeHash(PrettyYaml(a))
}
```

b. 将修改 apply到远端。如果 hash 值发生变化，则通过下面 Update 方法来更新远端的 LB，从下面代码中可以看出 apply 操作就是对比配置，如果配置不同，就发送请求去修改，并且不同配置的失败不会影响其他配置，只不过会将错误追加到 `errs := []error{}`中。这些代码略显复杂。

```go 
func (mgr *LoadBalancerManager) Update(reqCtx *svcCtx.RequestContext, local, remote *model.LoadBalancer) error {
	// 添加一个额外的标志符 tag
	if err := mgr.addTagIfNotExist(reqCtx, *remote, lbTag); err != nil {
		errs = append(errs, fmt.Errorf("AddTags: %s", err.Error()))
	}
	// 对 model 执行大量校验
	// 首先更新收费类型
    mgr.cloud.ModifyLoadBalancerInstanceChargeType(reqCtx.Ctx, lbId,
			string(local.LoadBalancerAttribute.InstanceChargeType), spec)
	// 配置成内网或者公网
	mgr.cloud.ModifyLoadBalancerInternetSpec(reqCtx.Ctx, lbId, string(charge), bandwidth)
	// 更新 LB 的规格
	if local.LoadBalancerAttribute.LoadBalancerSpec != "" &&
		local.LoadBalancerAttribute.LoadBalancerSpec != remote.LoadBalancerAttribute.LoadBalancerSpec {
		reqCtx.Log.Info(fmt.Sprintf("update lb: loadbalancerSpec changed ([%s] - [%s])",
			remote.LoadBalancerAttribute.LoadBalancerSpec, local.LoadBalancerAttribute.LoadBalancerSpec),
			"lbId", lbId)
		if err := mgr.cloud.ModifyLoadBalancerInstanceSpec(reqCtx.Ctx, lbId,
			string(local.LoadBalancerAttribute.LoadBalancerSpec)); err != nil {
			errs = append(errs, fmt.Errorf("ModifyLoadBalancerInstanceSpec: %s", err.Error()))
		}
	}
	// 更新删除保护
	if local.LoadBalancerAttribute.DeleteProtection != "" &&
		local.LoadBalancerAttribute.DeleteProtection != remote.LoadBalancerAttribute.DeleteProtection {
		reqCtx.Log.Info(fmt.Sprintf("update lb: delete protection changed([%s] - [%s])",
			remote.LoadBalancerAttribute.DeleteProtection, local.LoadBalancerAttribute.DeleteProtection),
			"lbId", lbId)
		if err := mgr.cloud.SetLoadBalancerDeleteProtection(reqCtx.Ctx, lbId,
			string(local.LoadBalancerAttribute.DeleteProtection)); err != nil {
			errs = append(errs, fmt.Errorf("SetLoadBalancerDeleteProtection: %s", err.Error()))
		}
	}
    // 等等
	return utilerrors.NewAggregate(errs)
}
```

#### context 上下文
初始化各个资源控制器需要一个 context，这个 context 是一个 sync.Map，并且只含有 provider一个key value, provider是各个资源的 sdk api集合。

```go
type Context struct{ Ctx *sync.Map }

func (c *Context) Value(key string) (interface{}, bool) {
	if c.Ctx == nil {
		return nil, false
	}
	return c.Ctx.Load(key)
}

func (c *Context) SetKV(key string, v interface{}) {
	if c.Ctx == nil {
		c.Ctx = &sync.Map{}
	}
	c.Ctx.Store(key, v)
}
func (c *Context) Range(f func(key, value interface{}) bool) { c.Ctx.Range(f) }
```

另外在 reconcile 各个资源时，也会构造一个 reqContext，这个 reqContext配置如下:
```go
// new context for each request
ctx := context.Background()
ctx = context.WithValue(ctx, dryrun.ContextService, svc)// 将当前 service 塞到 context 中

reqContext := &svcCtx.RequestContext{
    Ctx:      ctx,
    Service:  svc, // 当前 service
    Anno:     anno, // 一个简单的 annotation管理器，跟finalizer一样，配置多了就要抽象成管理器
    Log:      util.ServiceLog.WithValues("service", util.Key(svc)),
    Recorder: m.record,
}
```
上面配置中的 Log 也稍微注意下，这个是 klog的结构化版，import 路径为 `k8s.io/klog/v2/klogr`，这样在打印日志时，就知道是哪个控制器，以及当前 reconcile 的资源对象是什么了。

### 总结
大概看了下阿里云控制器的实现，总体上思路跟我们实现的思路差不多，当然我们的实现上还是要丑一些，后面可以多翻翻阿里云这部分代码，学习借鉴一下。