---
layout:     post
title:      "Kubelet 概述"
date:       2019-12-14 7:54:00
author:     "weak old dog"
header-img-credit: false
tags:
    - Kubelet
---

> kubelet 比较庞大，涉及的东西很多，相互之间关联性也不强，目前还没有了解每个模块的能力，因此只能做一个概述。以后在这个提纲的基础上再深入研究某一模块。kubelet的启动流程以及参数初始化不会涉及，主要是每个管理器的初始化过程，以及kubelet的同步过程。基于的代码是k8s 1.9。

### 初始化
初始化的代码在`pkg/kubelet/kubelet.go`文件，函数为`NewMainKubelet`，这个函数返回一个`Kubelet`结构体，这个结构体是kubelet的基本配置，包括了基本的参数配置（比如hostname, 众多client）；以及众多manager，很多组件都是以manager的形式存在的（比如podManager, evictionManager等）。

`NewMainKubelet`主要是初始化`Kubelet`结构体，配置其中的众多Manager，主要方式是调用package中的NewXXXmanager函数。下面挑几个介绍。

```go
runtimeService, imageService, err := getRuntimeAndImageServices(remoteRuntimeEndpoint, remoteImageEndpoint, kubeCfg.RuntimeRequestTimeout)
if err != nil {
    return nil, err
}
klet.runtimeService = runtimeService
```
运行时服务与镜像服务初始化，这里的`runtimeService`的定义如下，其实是对cri定义的全部接口的分类，包括返回版本，容器管理，Sandbox管理等。全部的cri的定义可以参考[CRI-API](https://github.com/kubernetes/cri-api)。
```go
type RuntimeService interface {
	RuntimeVersioner
	ContainerManager
	PodSandboxManager
	ContainerStatsManager

	// UpdateRuntimeConfig updates runtime configuration if specified
	UpdateRuntimeConfig(runtimeConfig *runtimeapi.RuntimeConfig) error
	// Status returns the status of the runtime.
	Status() (*runtimeapi.RuntimeStatus, error)
}
```
除了`runtimeService`，kubelet还维护了一个RuntimeManager，具体参数为kubeGenericRuntimeManager，这个RuntimeManager持有runtimeService的引用，kubeGenericRuntimeManager的主要作用是syncPod，输入为pod，为这个pod创建（调用runtimeService）init container / SandBoxContainer / commonContainer等。

```go
plug, err := network.InitNetworkPlugin(kubeDeps.NetworkPlugins, crOptions.NetworkPluginName, 
&criNetworkHost{&networkHost{klet}, &network.NoopPortMappingGetter{}}, hairpinMode, nonMasqueradeCIDR, int(crOptions.NetworkPluginMTU))
```
初始化网络插件，目前还不是很清楚这里的逻辑，以后研究下。

```go
klet.setNodeStatusFuncs = klet.defaultNodeStatusFuncs()
```
这个是一些获取node状态的函数，这些函数更新node的状态，并更新到apiserver。如果想添加一些自定义的node状态，可以添加自定义的NodeStatusFunc。

volumeManager也是在这个函数中初始化的。

### 运行
Kubelet数据结构初始化之后，是调用Run方法运行的，函数签名如下：

`func (kl *Kubelet) Run(updates <-chan kubetypes.PodUpdate)`

Run函数，运行在初始化阶段创建的各种Manager，一般是调用Manager的Run方法，或者直接使用
`go wait.Until(kl.syncNetworkStatus, 30*time.Second, wait.NeverStop)`循环调用某个函数。除了运行各种Manager，Run方法还开启pod的Sync，即：`kl.syncLoop(updates, kl)`。第二个参数为SyncHandler类型的接口，也为Kubelet结构体本身。SyncHandler接口定义的简介直白，如下
```go
type SyncHandler interface {
	HandlePodAdditions(pods []*v1.Pod)
	HandlePodUpdates(pods []*v1.Pod)
	HandlePodRemoves(pods []*v1.Pod)
	HandlePodReconcile(pods []*v1.Pod)
	HandlePodSyncs(pods []*v1.Pod)
	HandlePodCleanups() error
}
```
`syncLoop`方法进一步调用了`(kl *Kubelet) syncLoopIteration`方法，这个方法就是监听不同的channel，这些channel就是pod的事件来源，并根据事件类型来调用SyncHandler的不同方法，就是上面接口中的方法。进一步，在这些SyncHandler方法的实现中，都调用了dispatchWork方法，SyncType也是根据事件类型定义为`Create`/`Update`等。

`func (kl *Kubelet) dispatchWork(pod *v1.Pod, syncType kubetypes.SyncPodType, mirrorPod *v1.Pod,
 start time.Time) `
 
 在`dispatchWork`中，最终调用的是podWork的UpdatePod方法，这个UpdatePod方法又调用了managerPodLoop方法，后者又调用了podWorker的`syncPodFn`方法，我们重点说下这个方法吧。`syncPodFn`是由kubelet的`syncPod`方法初始化的，这个方法值得细说。这个方法应该是kubelet最终对pod进行同步的地方，总结一下其工作流程（comments中说的清楚）：

1. 如果是create pod，记录pod worker的启动时延。
2. 调用kl.generateAPIPodStatu为pod生成status
3. 如果pod第一次调用syncPodFn，记录pod的启动时延。
4. 在status manager中更新pod的状态。
5. 如果pod不应该Running，kill pod。
6. create mirror pod，如果pod是个static pod，而且没有mirror pod。
7. 为pod创建数据目录，即/var/lib/kubelet/pods中的目录
8. 等待所有的pod的volume都mount和attach
9. 为pod准备secrets
10. 调用kubeGenericRuntimeManager的syncPod方法，为pod创建container
11. Update the traffic shaping for the pod's ingress and egress limits

上面的工作有一个出错了，就返回error，等待下一次syncPod。