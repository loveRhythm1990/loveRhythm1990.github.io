---
layout:     post
title:      "karpenter 中调度与 consolidate 实现"
date:       2024-12-07 10:10:00
author:     "decent"
header-img-credit: false
tags:
    - 扩缩容
---

**目录**
- [什么时候触发 node 弹出](#什么时候触发-node-弹出)
- [调度大概过程](#调度大概过程)
- [节点 consolidate](#节点-consolidate)
  - [关键因素：consolidatable condition](#关键因素consolidatable-condition)
  - [Empty 节点 consolidate](#empty-节点-consolidate)
  - [单节点重调度](#单节点重调度)
- [单例控制器](#单例控制器)
  - [初始化控制器](#初始化控制器)
  - [singleton source 实现](#singleton-source-实现)
- [总结](#总结)


### 什么时候触发 node 弹出
文档中的描述是 `Watching for pods that the Kubernetes scheduler has marked as unschedulable`，那么会是一个 pod pending 之后马上 nodeclaim 创建吗？答案是否定的，karpenter 具体是通过一个 batch 来聚合需要调度的 pod，batch 有两个参数（后面是默认值）：
```s
BATCH_MAX_DURATION: 10*time.Second
BATCH_IDLE_DURATION: time.Second
```
具体工作过程是：发现一个 pod pending 之后，等一秒钟，如果一秒之内还有 pod pending，则聚合这个 pod，如果等的过程超过了 10s 钟，则结束此次 batch，如果两个 pod 间隔过程超过了 1s，同样会结束 batch。

### 调度大概过程
karpenter 中，会有两种场景触发调度：1.集群中有 pod pending 的时候；2.当有节点需要 consolidate 的时候。每次触发调度的是，都会初始化一个新的 scheduler，类似于  Kubernetes 中给集群状态做一个快照。

调度问题基本上就是装箱问题，将一组 pod 调度到一组节点上，其中`一组节点`是指下面的 existingNodes，待调度的 pod 是以参数传递给 scheduler 的。其余参数一般是对集群状态的缓存。

```go
type Scheduler struct {
	id                 types.UID // Unique UUID attached to this scheduling loop
	newNodeClaims      []*NodeClaim
	existingNodes      []*ExistingNode
	nodeClaimTemplates []*NodeClaimTemplate
	remainingResources map[string]corev1.ResourceList // (NodePool name) -> remaining resources for that NodePool
	daemonOverhead     map[*NodeClaimTemplate]corev1.ResourceList
	cachedPodRequests  map[types.UID]corev1.ResourceList // (Pod Namespace/Name) -> calculated resource requests for the pod
	preferences        *Preferences
	topology           *Topology
	cluster            *state.Cluster
}
```

调度流程如下，跟 Kubernetes 中的调度器流程基本一致。调度过程返回的结果是 Result 对象，包括需要新建的 NodeClaim、当前已经存在的节点、调度 pod 过程中发生的错误。
```go
type Results struct {
	NewNodeClaims []*NodeClaim
	ExistingNodes []*ExistingNode
	PodErrors     map[*corev1.Pod]error
}
func (s *Scheduler) Solve(ctx context.Context, pods []*corev1.Pod) Results {
	// 缓存待调度的 pod 的资源请求
	for _, p := range pods {
		s.cachedPodRequests[p.UID] = resources.RequestsForPods(p)
	}
	// 构建待调度 pod 队列，按照 cpu 和 memory 资源请求降序排列
	q := NewQueue(pods, s.cachedPodRequests)

	for {
		// 从队列依次选择 pod 进行调度
		pod, ok := q.Pop()
		if !ok {
			break
		}
		// 调度单个 pod
		if errors[pod] = s.add(ctx, pod); errors[pod] == nil {
			delete(errors, pod)
			continue
		}
		// 调度失败
		relaxed := s.preferences.Relax(ctx, pod)
		q.Push(pod, relaxed)
		if relaxed {
			if err := s.topology.Update(ctx, pod); err != nil {
				log.FromContext(ctx).Error(err, "failed updating topology")
			}
		}
	}
}
```

下面是具体调度单个 pod，步骤整体有三个：1.看当前已存在节点是否满足 pod 调度约束（包括资源约束、拓扑调度约束等）；2.查看此轮调度过程新建的的 nodeclaim 是否满足调度约束，因为有可能一次调度多个 pod，所以可能之前新建过 nodeclaim 了；3.新建一个 nodeclaim，并确保新建的 nodeclaim 能满足当前 pod。

```go
func (s *Scheduler) add(ctx context.Context, pod *corev1.Pod) error {
	// 1. 调度至已有 node
	for _, node := range s.existingNodes {
		if err := node.Add(ctx, s.kubeClient, pod, s.cachedPodRequests[pod.UID]); err == nil {
			return nil
		}
	}
	sort.Slice(s.newNodeClaims, func(a, b int) bool { return len(s.newNodeClaims[a].Pods) < len(s.newNodeClaims[b].Pods) })

	// 2. 此轮调度中新建的 nodeclaim 是否满足调度约束
	for _, nodeClaim := range s.newNodeClaims {
		if err := nodeClaim.Add(pod, s.cachedPodRequests[pod.UID]); err == nil {
			return nil
		}
	}

	// 3. 创建新的 nodeclaim
	var errs error
	for _, nodeClaimTemplate := range s.nodeClaimTemplates {
		instanceTypes := nodeClaimTemplate.InstanceTypeOptions
		// if limits have been applied to the nodepool, ensure we filter instance types to avoid violating those limits
		if remaining, ok := s.remainingResources[nodeClaimTemplate.NodePoolName]; ok {
			// 确保机型有足够的资源容纳 pod
		}
		nodeClaim := NewNodeClaim(nodeClaimTemplate, s.topology, s.daemonOverhead[nodeClaimTemplate], instanceTypes)
		// 确认是否满足 pod 调度约束
		if err := nodeClaim.Add(pod, s.cachedPodRequests[pod.UID]); err != nil {
			continue
		}
		// we will launch this nodeClaim and need to track its maximum possible resource usage against our remaining resources
		s.newNodeClaims = append(s.newNodeClaims, nodeClaim)
		s.remainingResources[nodeClaimTemplate.NodePoolName] = subtractMax(s.remainingResources[nodeClaimTemplate.NodePoolName], nodeClaim.InstanceTypeOptions)
		return nil
	}
	return errs
}
```

### 节点 consolidate
consolidate 是指 karpenter 将闲置节点或者资源使用率比较少的节点移除，可能会触发 pod 的重新调度，以及新节点的弹出。

不同于通过资源使用率来定义资源使用率比较低的节点（比如阿里云是这么做的），karpenter 的做法是将节点上的 pod 重新调度到已有的节点，或者新建一台价格比较低的节点，不过总体感觉来说，效果如何需要实践证明。

#### 关键因素：consolidatable condition
主要是看 condition `Consolidatable` 是否为 true，这个条件为 true 的主要条件是，当前时间已经过了 nodepool 中配置的 consolidateAfter 时间，节点初始化时间距离当前时间超过了这个时间，则把这个 condition 设置为 true。
```yaml
spec:
  disruption:
    consolidateAfter: 1m | Never 
```
在实现上，karpenter 通过一个单独的控制器 Consolidation 来设置这个 condition，严格来说不是一个控制器或者说是一个子控制器，因为这个 Consolidation 控制器虽然有 Reconcile 方法，但是没有实现 controllerruntime 的 Reconcile 接口，该控制器的 Reconcile 方法是在 nodeclaim disrupt 控制器中调用的。

Consolidation 控制器位于 pkg/controllers/nodeclaim/disruption/consolidation.go，该控制器只做一件事，就是设置 `Consolidatable` condition，该控制器会将 nodepool requeue，requeue 的时间是就是设置的 consolidateAfter 时间，也就是说会及时将条件设置为 true。

nodepool 控制器在 reconcile 的时候会调用这个方法，除了这个方法还有一个 drift 子控制器，实现方式类似。
```go
func (c *Controller) Reconcile(ctx context.Context, nodeClaim *v1.NodeClaim) (reconcile.Result, error) {
	// ...

	reconcilers := []nodeClaimReconciler{
		c.drift,
		c.consolidation,
	}
	for _, reconciler := range reconcilers {
		res, err := reconciler.Reconcile(ctx, nodePool, nodeClaim)
		errs = multierr.Append(errs, err)
		results = append(results, res)
	}
	if !equality.Semantic.DeepEqual(stored, nodeClaim) {
        // patch status
	}
	return result.Min(results...), nil
}
```
#### Empty 节点 consolidate
disruption 控制器主要通过 ShouldDisrupt 方法来判断要不要进行 consolidate，empty 节点的方法如下，有两个条件：1）上一小节的 consolidatable condition 为 true；2）节点上没有可 reschedulable 的 pods，指除 static、daemonset 以外的 pod。 
```go
func (e *Emptiness) ShouldDisrupt(_ context.Context, c *Candidate) bool {
	if c.nodePool.Spec.Disruption.ConsolidateAfter.Duration == nil {
		return false
	}
	// return true if there are no pods and the nodeclaim is consolidatable
	return len(c.reschedulablePods) == 0 && c.NodeClaim.StatusConditions().Get(v1.ConditionTypeConsolidatable).IsTrue()
}
```

#### 单节点重调度
单节点重调度是指 singlenodeconsolidation，在对单个节点进行 consolidation 时，首先要将该节点上的 pod 进行模拟调度 `SimulateScheduling`，看看该节点上的 pod 是否能调度到已有的 node，或者需要新启动一个 nodeclaim。

该调度模型中，待调度的 pod 有：1）candidate 节点上的 pod，2）正在 deleting 的节点上的 pod，3）集群中当前 pending 的 pod。调度的目标机器为不在 condidate 集合中的其他机器。

```go
func SimulateScheduling(ctx context.Context,
	kubeClient client.Client,
	cluster *state.Cluster,
	provisioner *provisioning.Provisioner,
	candidates ...*Candidate) (pscheduling.Results, error) {
    
	// ...
	// 初始化调度器来调度上面的 pods
	scheduler, err := provisioner.NewScheduler(log.IntoContext(ctx, operatorlogging.NopLogger), pods, stateNodes)
	if err != nil {
		return pscheduling.Results{}, fmt.Errorf("creating scheduler, %w", err)
	}

	// 调度 pods
	results := scheduler.Solve(log.IntoContext(ctx, operatorlogging.NopLogger), pods).TruncateInstanceTypes(pscheduling.MaxInstanceTypes)
	
	// ...
	return results, nil
}
```
在模拟调度之后，disrupt 控制器需要确认，有没有新的 nodeclaim 需要确创建，以及考虑新的 nodeclaim 的收费类型（是不是 spot 实例），收费价格等，这些比较细节，咱不展开描述。

karpenter 除了 singlenodeconsolidation 还有 multinodeconsolidation，总体过程类似，condidate 从一个 node 变成了多个 node。目前来看 karpenter 在调度时，基本没考虑资源使用率，而主要考虑是不是过了在 nodepool 中指定的 consolidateAfter 时间，在重调度算法层面，也只是遍历 condidate 进行调度（启发式算法）。

### 单例控制器
单例控制器是 karpenter 中一个经常使用的设计模式，普通控制器是基于事件来 reconcile 对象，比如 pod 控制器会根据增删改事件来触发 reconcile。单例控制器的事件源是自定义事件源，并且只会触发一个事件，那么只有一个事件，那 reconcile 只执行一次吗？不是的，因为这个对象在一直被 requeue，只有两种情况：1.因为发生错误被 requeue；2.主动返回 requeue，并指定 requeue 的时间，在总体实现效果上，像一个带有 sleep 的 for 循环。

#### 初始化控制器
初始化一个单例控制器代码如下，其中 `singleton.Source()` 实现了 controllerruntime 中的 TypedSource，`singleton.AsReconciler(p)` 则实现了 TypedReconciler 接口，即带有泛型的 Reconciler。
```go
// import "github.com/awslabs/operatorpkg/singleton"

func (p *Provisioner) Register(_ context.Context, m manager.Manager) error {
	return controllerruntime.NewControllerManagedBy(m).
		Named("provisioner").
		WatchesRawSource(singleton.Source()).
		Complete(singleton.AsReconciler(p))
}
```

#### singleton source 实现
singleton source 的实现也比较简单，具体是使用 controllerruntime 中提供的 channel source，并向该 source 添加了一个唯一的 GenericEvent。
```go
import (
    "context"
	"time"

	"k8s.io/client-go/util/workqueue"
	"sigs.k8s.io/controller-runtime/pkg/event"
	"sigs.k8s.io/controller-runtime/pkg/handler"
	"sigs.k8s.io/controller-runtime/pkg/reconcile"
	"sigs.k8s.io/controller-runtime/pkg/source"
)

const (
	// RequeueImmediately is a constant that allows for immediate RequeueAfter when you want to run your
	// singleton controller as hot as possible in a fast requeuing loop
	RequeueImmediately = 1 * time.Nanosecond
)

type Reconciler interface {
	Reconcile(ctx context.Context) (reconcile.Result, error)
}

func AsReconciler(reconciler Reconciler) reconcile.Reconciler {
	return reconcile.Func(func(ctx context.Context, r reconcile.Request) (reconcile.Result, error) {
		return reconciler.Reconcile(ctx)
	})
}

func Source() source.Source {
	eventSource := make(chan event.GenericEvent, 1)
	eventSource <- event.GenericEvent{}
	return source.Channel(eventSource, handler.Funcs{
		GenericFunc: func(_ context.Context, _ event.GenericEvent, queue workqueue.TypedRateLimitingInterface[reconcile.Request]) {
			queue.Add(reconcile.Request{})
		},
	})
}
```
### 总结
浏览量一下 karpenter 中的一些实现方式，细节部分没有深入探究，大致对设计这样一个成本系统（提高资源使用率、或者替换为成本较低的机器）中需要注意的问题有所了解。

个人感觉 karpenter 总体代码质量还算挺高的，也有一些比较好的设计模式，后面有机会会继续研究一下。 