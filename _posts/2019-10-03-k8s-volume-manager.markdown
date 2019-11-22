---
layout:     post
title:      "Kubelet volume manager"
subtitle:   " \"概述\""
date:       2019-10-04 19:16:00
author:     "weak old dog"
header-img-credit: false
tags:
    - k8s
    - kubelet
---


## 前言
本身是做k8s存储相关工作的，但对kubelet volume-manager怎么工作的，了解的还不够多，这里分析一下吧。基于的k8s版本为1.16。volume-manager的入口函数在`pkg/kubelet/kubelet.go`文件中。程序启动非常简单：
```golang
	// Start volume manager
	go kl.volumeManager.Run(kl.sourcesReady, wait.NeverStop)
```
下面就从这个入口函数开始分析。在贴代码时，会删去代码中的加锁、校验、监控项、错误处理、以及日志等不影响业务逻辑的代码。

## Run方法
volume-manager的主要代码在`pkg/kubelet/volume-manager`这个package中。`Run`方法的实现为：
```golang
func (vm *volumeManager) Run(sourcesReady config.SourcesReady, stopCh <-chan struct{}) {

	go vm.desiredStateOfWorldPopulator.Run(sourcesReady, stopCh)

	go vm.reconciler.Run(stopCh)

	if vm.kubeClient != nil {
		// start informer for CSIDriver
		vm.volumePluginMgr.Run(stopCh)
	}

	<-stopCh
}
```
由此可见，`Run`主要做两件事，启动`desiredStateOfWorldPopulator`以及启动`reconciler`逻辑。下面分别分析。

### 关于desiredStateOfWorldPopulator
`desiredStateOfWorldPopulator`负责维护desired state world缓存的，下面简称dsw。对于每个active的pod，如果这个pod存在volume，dsw populator确保其在dsw缓存中；对于dsw缓存中的pod，如果被删除了，则从dsw缓存删除。dsw的Run方法就是调用`populatorLoop`方法。这个方法如下，省去了部分代码：
```golang
func (dswp *desiredStateOfWorldPopulator) populatorLoop() {
	dswp.findAndAddNewPods()

	// 这个方法需要调用container运行时接口，去查看一个pod的container是否被terminated，
	// 这个操作非常耗时，所以被限制为2秒钟执行一次。
	dswp.findAndRemoveDeletedPods()
}
```
dsw的工作流程如下：
![java-javascript](/img/in-post/volumemanager/dswpopulator.png){:height="70%" width="70%"}

#### dsw缓存中的数据结构
dsw-populator根据pod-manager来更新dsw缓存，后者表示应该要mount的一些volume和pod，其数据结构如下，：

![java-javascript](/img/in-post/volumemanager/dsw.png){:height="50%" width="50%"}

### 关于reconciler
`reconciler`是volume manager的主要循环控制逻辑，就是从当前状态到期望状态靠拢的逻辑，期望状态就是上面说的dsw，我们看下reconcile，主要循环逻辑为：
```go
func (rc *reconciler) reconciliationLoopFunc() func() {
	return func() {
		rc.reconcile()

		// 下面if语句的第一个case表示dsw的所有pod source是否ready，在ready之后，返回值都是true。
		// 第二个case表示是否执行过sync，如果执行过，这个条件就不成立，那么说来，这个sync方法只会执行一次
		if rc.populatorHasAddedPods() && !rc.StatesHasBeenSynced() {
			klog.Infof("Reconciler: start to sync state")
			rc.sync()
		}
	}
}
```
在看reconciler之前，还需要了解一下actual status of world这个数据结构，后面简称asw。

#### 关于actual status of world
这个是kubelet看到的关于volume当前状态，我们主要看这个缓存包括哪些数据结构，是如何构建以及更新的。这个缓存所在文件为`pkg/kubelet/volumemanager/cache/actual_state_of_world.go`，缓存包含的数据结构跟dsw非常像:

![java-javascript](/img/in-post/volumemanager/asw.png){:height="50%" width="50%"}

`mountedPod`表示pod的信息，含有一个volumeSpec，表示pod的这个volume已经成功mount了。代码就不贴了，对asw的更新主要在reconcile的逻辑中，在分析reconcile时，会着重看一下缓存是如何更新的。下面直接看reconciler吧。

#### volume manager的reconcile
`reconcile`是volume manager的核心，这个方法主要是三个for循环，结构如下，分开来分析，这些代码细节较多，只是进行概述，如果有必要，以后在详细查看某一点。另外这三个for循环顺序是固定的，调整哪一个都不行。

![java-javascript](/img/in-post/volumemanager/reconcile.png){:height="30%" width="30%"}

我们先看第一个，对应该执行umount的volume执行umount操作，这个简单一点，`GetMouontedVolumes`这个方法返回成功mount的volume，以及对应的pod，可以理解成是一个pair。

![java-javascript](/img/in-post/volumemanager/ensureumount.png){:height="50%" width="50%"}


我们再看第二个，对volume执行attached/mounted操作，这个代码多了一些，总的来说，就是dws缓存的状态，来对volume进行attach和mount操作，同时
更新asw缓存。

![java-javascript](/img/in-post/volumemanager/ensureattach.png){:height="50%" width="50%"}

下面看第三个for循环，保证应该被detached/unmounted的volume被detached/unmounted了，

![java-javascript](/img/in-post/volumemanager/ensuredetach.png){:height="50%" width="50%"}


### OperationExecutor
用来保证同一时刻只有一个volume（或者volume及对应的pod）在执行操作，对于每个操作，启动一个线程。具体来说：

1. 定义了一些操作用来attach detach mount umount一个volume，保证同一时刻只有一个操作在执行。
2. 操作是幂等的。
3. 如果操作成功，更新asw

其主要数据结构如下，类似于一个线程池：

![java-javascript](/img/in-post/volumemanager/operation.png)

每当reconcile要执行操作时，就丢给operationExecutor来执行，operationExecutor先看看对应的volume是否已经有操作在执行了，如果有返回AlreadyExistError，如果在退避期间，返回退避错误，流程大概如下：

![java-javascript](/img/in-post/volumemanager/executor.png){:height="50%" width="50%"}


