---
layout:     post
title:      "K8s volume expand 控制器介绍"
subtitle:   " \"由点到线熟悉 K8s Controller Manager\""
date:       2019-08-20 16:36:00
author:     "weak old dog"
header-img-credit: false
tags:
    - K8s
    - Controller
---

> “Somethings Happens”

## 前言

打算开始写一些k8s的文章，已理解并记忆k8s的运行原理与设计模式，基于的k8s版本为1.9.3。
从volume expand controller开始是因为偶然发现这个控制器比较简单，麻雀虽小，五脏俱全，那就从这个开始吧。


## 入口

所有cm中的控制器都是在这里注册的，在后续文章会从宏观上分析整个cm的运行原理。
`kubernetes/cmd/kube-controller-manager/app/controllermanager.go`

```go
	controllers["persistentvolume-expander"] = startVolumeExpandController
```

其中函数的实现如下，首先检查是否启用了featureGate，然后直接调用了expand包里的NewExpandController方法。这个方法的参数列表为：一个与apiserver通信的kubeClient，一个pvcInformer与一个pvInformer用来监听pvc与pv的变化，第四个参数cloudprovider暂时先不考虑，第五个参数为一个plugins的列表，这个列表中就是k8s中所有注册的所有支持Expand的volume Plugin。

其中，一个volume plugin如果支持Expand（即是否实现ExpandableVolumePlugin接口），要在全局函数中ProbeExpandableVolumePlugins注册自己，这个全局函数就返回上述的所有支持Expand的插件。

```go
func startVolumeExpandController(ctx ControllerContext) (bool, error) {
	if utilfeature.DefaultFeatureGate.Enabled(features.ExpandPersistentVolumes) {
		expandController, expandControllerErr := expand.NewExpandController(
			ctx.ClientBuilder.ClientOrDie("expand-controller"),
			ctx.InformerFactory.Core().V1().PersistentVolumeClaims(),
			ctx.InformerFactory.Core().V1().PersistentVolumes(),
			ctx.Cloud,
			ProbeExpandableVolumePlugins(ctx.Options.VolumeConfiguration))

		if expandControllerErr != nil {
			return true, fmt.Errorf("Failed to start volume expand controller : %v", expandControllerErr)
		}
		go expandController.Run(ctx.Stop)
		return true, nil
	}
	return false, nil
}
```
上述代码在创建一个expand代码之后，接着就调用Run跑起来了。


## ExpandController的创建过程

创建过程就是公开函数NewExpandController的实现了，我会尽量少的贴代码，以理解主线为主，避免陷入细节。

```go
func NewExpandController(
	kubeClient clientset.Interface,
	pvcInformer coreinformers.PersistentVolumeClaimInformer,
	pvInformer coreinformers.PersistentVolumeInformer,
	cloud cloudprovider.Interface,
	plugins []volume.VolumePlugin) (ExpandController, error) {

	expc := &expandController{
		kubeClient: kubeClient,
		cloud:      cloud,
		pvcLister:  pvcInformer.Lister(),
		pvcsSynced: pvcInformer.Informer().HasSynced,
		pvLister:   pvInformer.Lister(),
		pvSynced:   pvInformer.Informer().HasSynced,
	}

    //调用所有插件的Init方法，并加入到volumePlugin的map中
	if err := expc.volumePluginMgr.InitPlugins(plugins, nil, expc); err != nil {
		return nil, fmt.Errorf("Could not initialize volume plugins for Expand Controller : %+v", err)
	}

    //创建一个OperationExecutor，OperationExecutor保证对于同一个volume，只有一个动作在执行，并提供了指数退避机制，后面会单独写一篇文章介绍这个
	expc.opExecutor = operationexecutor.NewOperationExecutor(operationexecutor.NewOperationGenerator(
		kubeClient,
		&expc.volumePluginMgr,
		recorder,
		false,
		blkutil))

    // VolumeResizeMap是一个cache，缓存了一个pvc的ID，以及对这个pvc的扩展请求。
    // 这个client参数是用来更新pvc的
	expc.resizeMap = cache.NewVolumeResizeMap(expc.kubeClient)

    //为pvc Informer添加事件注册方法。
	pvcInformer.Informer().AddEventHandler(kcache.ResourceEventHandlerFuncs{
        // 这里的pvcUpdate函数是关键，收到更新事件之后，这里检查pvc Spec字段中的size，如果Spec中的Size比Status中的Size大，则添加到缓存中
        UpdateFunc: expc.pvcUpdate,
        // 这个delete从缓存中删除pvc
		DeleteFunc: expc.deletePVC,
	})

    //Sync是对volume进行扩展的代码，下面会分析
	expc.syncResize = NewSyncVolumeResize(syncLoopPeriod, expc.opExecutor, expc.resizeMap, kubeClient)
	expc.pvcPopulator = NewPVCPopulator(
		populatorLoopPeriod,
		expc.resizeMap,
		expc.pvcLister,
		expc.pvLister,
		kubeClient)
	return expc, nil
}
```

首先实例化一个expandController结构体，并调用expandController的volumePluginMgr的InitPlugins来初始化所有插件。初始化插件就是调用每个插件的Init方法（VolumePlugin中的一个接口，必须实现），并将这个插件放置到volumePluginMgr的一个名叫plugins的map中，map的key是插件的名字，value就是实例。

加下来的步骤是：
* 初始化一个OperationExecutor
* 初始化一个VolumeResizeMap，这应该是一个缓存request的map
* 添加pvc的事件处理器，update以及delete事件
* 初始化一个SyncVolumeResize，这个是同步Resize的地方
* 最后是初始化一个PVCPopulator

## 分析PVCPopulator
pvcPopulator这部分相对独立，先分析这个。这个数据结构只做了一件事：就是每隔2分钟从apiserver把所有的pvc都拿过来，然后把每个pvc对应的pv也拿过来，然后调用调用resizeMap的AddPVCUpdate方法，参数就是一个pvc以及这个pvc对应的pv。
需要注意的是PVCPopulator没有做任何检查，上面操作获取的都是所有的PVC，检查在AddPVCUpdate方法中做。另外获取pv的时候，拿的是一个深拷贝。

## SyncVolumeResize
这个应该是VolumeExpandController的主要处理逻辑。从run函数开始看，这个执行周期是30s，这个数据的sync方法也不麻烦。主要逻辑如下：
```go

	for _, pvcWithResizeRequest := range rc.resizeMap.GetPVCsWithResizeRequest() {

		// 1. 将pvc的状态设置为 Resizing，这个是通过 更新 pvc的status来实现的，一个细节就是调用UpdateStatus之前，做了一个pvc的deepcopy，这里涉及到一些deepcopy，以后要考虑一下为什么。

	    // 2. 调用operationExecutor的ExpandVolume方法，对volume进行调整。
	}

```
SyncVolumeResize看上去是resizeMap的消费者，PVCPopulator是resizeMap的生产者。另外，我们之前pvc update事件处理器`UpdateFunc: expc.pvcUpdate`也是resizeMap的生产者。

## ResizeMap缓存
resizeMap是ExpandController创建并持有的，`	expc.resizeMap = cache.NewVolumeResizeMap(expc.kubeClient)
`，并将这个resizeMap当做参数传递给SyncVolumeResize以及PVCPopoluator，因此，存在三个可以更新缓存的地方：expandController、PVCPopluator、SyncVolumeResize，前两个是生产者，最后一个是消费者。因为有三个goroutine会访问resizemap所以需要加锁。

另外，resizemap的AddPVCUpdate是需要做资源检查的，只有在Spec.Size比Status.Size大的时候才需要进行扩展。

## 总结
ExpandOperator的主要流程是这样的，下一步打算介绍OperationExecutor的设计。