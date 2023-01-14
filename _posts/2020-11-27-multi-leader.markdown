---
layout:     post
title:      "多副本选主 LeaderElection 的实现"
date:       2020-11-30 00:21:00
author:     "decent"
header-img-credit: false
tags:
    - K8s
---

一些K8s控制组件有多个副本，如Kube-Scheduler，Kube-ControllerManager等。但同一个时刻，只有一个副本处于工作状态，其他组件处于Standby状态。这里以[sig-storage-lib-external-provisioner](https://github.com/kubernetes-sigs/sig-storage-lib-external-provisioner)为例研究一下选主的实现与使用。

程序的框架如下：
```go
	// "k8s.io/client-go/tools/leaderelection"
	// "k8s.io/client-go/tools/leaderelection/resourcelock"
// Run starts all of this controller's control loops
func (ctrl *ProvisionController) Run(ctx context.Context) {
	run := func(ctx context.Context) {
		// run方法是控制器的主要方法，在这个方法中使用go关键字启动一些控制线程
		// 这个方法是阻塞式的，使用select{}对方法进行阻塞，永远不会返回
		for i := 0; i < ctrl.threadiness; i++ {
			go wait.Until(func() { ctrl.runClaimWorker(ctx) }, time.Second, ctx.Done())
			go wait.Until(func() { ctrl.runVolumeWorker(ctx) }, time.Second, ctx.Done())
		}
		select {}
	}

	// 下面是选主组件工作的地方，如果设置了选主则走第一个分支。
	if ctrl.leaderElection {
		rl, err := resourcelock.New("endpoints",
			ctrl.leaderElectionNamespace,
			strings.Replace(ctrl.provisionerName, "/", "-", -1),
			ctrl.client.CoreV1(),
			nil,
			resourcelock.ResourceLockConfig{
				Identity:      ctrl.id,
				EventRecorder: ctrl.eventRecorder,
			})
		if err != nil {
			klog.Fatalf("Error creating lock: %v", err)
		}

		leaderelection.RunOrDie(ctx, leaderelection.LeaderElectionConfig{
			Lock:          rl,
			LeaseDuration: ctrl.leaseDuration,
			RenewDeadline: ctrl.renewDeadline,
			RetryPeriod:   ctrl.retryPeriod,
			Callbacks: leaderelection.LeaderCallbacks{
				OnStartedLeading: run,
				OnStoppedLeading: func() {
					klog.Fatalf("leaderelection lost")
				},
			},
		})
		panic("unreachable")
	} else {
		// 没有设置选主，则直接运行run方法
		run(ctx)
	}
}
```
在上面代码中，如果设置了选主，则下通过`resourcelock.New`方法创建一个资源锁。然后使用`leaderelection.RunOrDie`运行run方法，后者引用New方法创建的资源锁。

先看一下上面两个方法的参数，以及涉及到的数据结构，然后看一下工作原理。

##### 相关数据结构

`resourcelock.New`方法声明如下：k8s.io/client-go/tools/leaderelection/resourcelock/interface.go
```go
func New(lockType string, ns string, name string, coreClient corev1.CoreV1Interface, coordinationClient coordinationv1.CoordinationV1Interface, rlc ResourceLockConfig) (Interface, error) {
```
参数列表为：
* lockType: 锁的类型，有`endpoint`，`configmap`，`lease`三种。
* ns: namespace
* name: 资源锁的名字，这个参数每个副本都是一致的
* ResourceLockConfig: 资源锁配置，这里需要注意的是`Identity`字段，这里每个副本都是不一致的。

`leaderelection.RunOrDie`的函数声明如下：
```go
func RunOrDie(ctx context.Context, lec LeaderElectionConfig) {
```
主要是`LeaderElectionConfig`结构体。定义了一些时间设置以及回调函数。
```go
leaderelection.LeaderElectionConfig{
	Lock:          rl, // 上面定义的锁，包括资源锁名字、类型，每个副本的id等。
	LeaseDuration: ctrl.leaseDuration,
	RenewDeadline: ctrl.renewDeadline,
	RetryPeriod:   ctrl.retryPeriod,
	Callbacks: leaderelection.LeaderCallbacks{
		OnStartedLeading: run, 
		OnStoppedLeading: func() {
			klog.Fatalf("leaderelection lost")
		},
},
```
字段如下：
* LeaseDuration: 租约时长，默认15s，非leader节点想要更新资源锁时，必须等待这些时间
* RenewDeadline: leader更新资源锁的超时时长，默认10s。leader在获得锁之后，还要每隔每隔`RetryPeriod`时间去renew资源锁，如果到了`RenewDeadline`还没更新成功，则放弃锁。
* RetryPeriod: 客户端（包括leader和其他standby）检查资源锁的周期，默认2s，也就是调用`tryAcquireOrRenew`的周期。
* OnStartedLeading: 获得资源锁时运行的函数，也就是各个controller控制器的主函数。

##### 运行原理
主要是看`leaderelection.RunOrDie`方法。其调用过程为：

`RunOrDie` -> `NewLeaderElector` -> `LeaderElector.Run`

看下`LeaderElector.Run`方法：
```go
// Run starts the leader election loop
func (le *LeaderElector) Run(ctx context.Context) {
	defer runtime.HandleCrash()
	defer func() {
		le.config.Callbacks.OnStoppedLeading()
	}()

	if !le.acquire(ctx) {
		return // ctx signalled done
	}
	ctx, cancel := context.WithCancel(ctx)
	defer cancel()
	go le.config.Callbacks.OnStartedLeading(ctx)
	le.renew(ctx)
}
```
每个选主组件的工作方法就如上所示，对于leader节点来说，通过`acquire`拿到锁之后，就在一个单独的goroutine里运行`OnStartedLeading`回调方法，只有就在`renew`方法里，每隔两秒（`RetryPeriod`）就去更新下资源锁的时间戳；对于standby节点，会在`acquire`里一直循环。
![java-javascript](/img/in-post/leaderelection/leader.jpeg)

其实资源锁就是一个`endpoint`资源，leader节点的信息被放到了这个`endpoint`的annotation里，annotation的value就是`LeaderElectionRecord`结构体的序列化数据。其结构如下，leader组件在`renew`方法中，每隔2s就去修改下面的两个字段`AcquireTime`以及`RenewTime`，修改为当前值。standby组件每隔2s拿到这个`endpoint`资源，对比json序列化数据有变化，就修改一个观察时间，如果观察时间加上`LeaseDuration`这个时间没有超时，就认为leader还在正常工作，不进行选主。
```go
type LeaderElectionRecord struct {
	// HolderIdentity is the ID that owns the lease. If empty, no one owns this lease and
	// all callers may acquire. Versions of this library prior to Kubernetes 1.14 will not
	// attempt to acquire leases with empty identities and will wait for the full lease
	// interval to expire before attempting to reacquire. This value is set to empty when
	// a client voluntarily steps down.
	HolderIdentity       string      `json:"holderIdentity"`
	LeaseDurationSeconds int         `json:"leaseDurationSeconds"`
	AcquireTime          metav1.Time `json:"acquireTime"`
	RenewTime            metav1.Time `json:"renewTime"`
	LeaderTransitions    int         `json:"leaderTransitions"`
}
```

tryAcquireOrRenew方法如下。
```go
func (le *LeaderElector) tryAcquireOrRenew(ctx context.Context) bool {
	now := metav1.Now()
	leaderElectionRecord := rl.LeaderElectionRecord{
		HolderIdentity:       le.config.Lock.Identity(),
		LeaseDurationSeconds: int(le.config.LeaseDuration / time.Second),
		RenewTime:            now, // leader每次都更新时间，standby每次感知到有变化，就修改observedTime时间
		AcquireTime:          now,
	}

	// 1. obtain or create the ElectionRecord
	oldLeaderElectionRecord, oldLeaderElectionRawRecord, err := le.config.Lock.Get(ctx)
	if err != nil {
		if !errors.IsNotFound(err) {
			klog.Errorf("error retrieving resource lock %v: %v", le.config.Lock.Describe(), err)
			return false
		}
		if err = le.config.Lock.Create(ctx, leaderElectionRecord); err != nil {
			klog.Errorf("error initially creating leader election record: %v", err)
			return false
		}
		le.observedRecord = leaderElectionRecord
		le.observedTime = le.clock.Now()
		return true
	}

	// 2. Record obtained, check the Identity & Time
	// 对比json序列化数据
	if !bytes.Equal(le.observedRawRecord, oldLeaderElectionRawRecord) {
		le.observedRecord = *oldLeaderElectionRecord
		le.observedRawRecord = oldLeaderElectionRawRecord
		le.observedTime = le.clock.Now()
	}
	if len(oldLeaderElectionRecord.HolderIdentity) > 0 &&
		le.observedTime.Add(le.config.LeaseDuration).After(now.Time) &&
		!le.IsLeader() {
		// 这里是看到没有超时
		klog.V(4).Infof("lock is held by %v and has not yet expired", oldLeaderElectionRecord.HolderIdentity)
		return false
	}

	// 3. We're going to try to update. The leaderElectionRecord is set to it's default
	// here. Let's correct it before updating.
	if le.IsLeader() {
		leaderElectionRecord.AcquireTime = oldLeaderElectionRecord.AcquireTime
		leaderElectionRecord.LeaderTransitions = oldLeaderElectionRecord.LeaderTransitions
	} else {
		leaderElectionRecord.LeaderTransitions = oldLeaderElectionRecord.LeaderTransitions + 1
	}

	// update the lock itself
	if err = le.config.Lock.Update(ctx, leaderElectionRecord); err != nil {
		klog.Errorf("Failed to update lock: %v", err)
		return false
	}

	le.observedRecord = leaderElectionRecord
	le.observedTime = le.clock.Now()
	return true
}
```

另外还有个问题，选主是如何防止多个standby同时变成leader的？

思考一下，这个应该是依赖etcd的版本管理，如果有多个standby同时更新`endpoint`资源，那么除了第一个更新的，剩下的都会更新失败（版本冲突），需要先拿到最新的资源，再进行update操作，拿到最新的endpoint就看到最新的leader组件了。