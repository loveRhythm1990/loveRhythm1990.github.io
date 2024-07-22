---
layout:     post
title:      "使用 kruise cloneset lifecycle 管理应用生命周期"
date:       2024-05-01 10:10:00
author:     "decent"
header-img-credit: false
tags:
    - K8s生态
---

**目录**
- [kruise 部署安装](#kruise-部署安装)
- [cloneset lifecycle 概述](#cloneset-lifecycle-概述)
- [lifecycle 实践](#lifecycle-实践)
  - [preNormal](#prenormal)
  - [inPlaceUpdate](#inplaceupdate)
  - [preDelete](#predelete)

## kruise 部署安装
根据 [kruise 文档](https://openkruise.io/zh/docs/installation) 通过 helm 安装。
```s
# Firstly add openkruise charts repository if you haven't do this.
$ helm repo add openkruise https://openkruise.github.io/charts/

# [Optional]
$ helm repo update

# Install the latest version.
$ helm install kruise openkruise/kruise --version 1.6.3
```

## cloneset lifecycle 概述
[openkruise cloneset](https://openkruise.io/zh/docs/user-manuals/cloneset/#%E7%94%9F%E5%91%BD%E5%91%A8%E6%9C%9F%E9%92%A9%E5%AD%90) 管理的 pod 在每个阶段都有明确的状态，该状态使用 label `lifecycle.apps.kruise.io/state` 表明，一共有下面几个状态
* Normal: 正常状态
* PreparingUpdate: 准备原地升级
* Updating: 原地升级中
* Updated: 原地升级完成
* PreparingDelete: 准备删除

官方文档介绍 lifecycle 介绍的不清楚，使用的时候或者排查问题的时候会踩坑，本文梳理下 lifecycle 的使用。kruise controller-manager 会通过 label 显示当前 pod 所处的状态，在我们的业务控制器中，需要感知这个状态，并做相应的处理（比如开关流量、告警等），本文也会介绍在这些状态转换的过程中，业务控制器应该在何时进行介入，并做相应的处理。

## lifecycle 实践
计划通过下面配置来做测试，我们定义了 preNormal、preDelete、inPlaceUpdate 三个 lifecycle，并且通过 finalizer 来实现 hook。需要注意的是这里的 finalizer 只是作为一个配置信息，并不是阻止 K8s apiserver 删除资源用的，因此通过 label 也可以达到同样的效果。使用 lifecycle 时需要配置 KruisePodReady readinessGates。

我们将通过下面 yaml 配置来演示三种 hook 的工作方式。另外，这三种 hook 是需要跟用户的业务控制器相互协作的，在我们的场景中，我们可以将 `人` 视作控制器，在特定的事件发生时，做添加 finalizer 或者移除 finalizer 的操作，使整个流程流转下去。

```yaml
apiVersion: apps.kruise.io/v1alpha1
kind: CloneSet
metadata:
  name: test-lifehook
spec:
  lifecycle:
    preNormal:
      finalizersHandler:
      - example.io/unready-blocker
    preDelete:
      markPodNotReady: true
      finalizersHandler:
      - example.io/unready-blocker
    inPlaceUpdate:
      markPodNotReady: true
      finalizersHandler:
      - example.io/unready-blocker
  minReadySeconds: 30
  replicas: 3
  revisionHistoryLimit: 10
  selector:
    matchLabels:
      app: life-hook-nginx
  template:
    metadata:
      labels:
        app: life-hook-nginx
    spec:
      containers:
      - name: main
        image: nginx:alpine
      readinessGates:
      - conditionType: KruisePodReady
      - conditionType: InPlaceUpdateReady
  updateStrategy:
    inPlaceUpdateStrategy:
      gracePeriodSeconds: 10
    maxSurge: 0
    maxUnavailable: 1
    partition: 0
    type: InPlaceIfPossible
```

### preNormal
我们通过上面 yaml 直接创建 cloneset 的时候（未加任何干预），创建出来的 pod 都是 ready 的，并且不带有任何 finalizer。但此时查看 pod 的 `lifecycle.apps.kruise.io/state` 标签时，均处于 `PreparingNormal` 状态。这是因为我们没有给 pod 加对应的 finalier。
```s
lr90@sj ~ % kubectl get cloneset
NAME            DESIRED   UPDATED   UPDATED_READY   READY   TOTAL   AGE
test-lifehook   3         3         3               3       3       69s
lr90@sj ~ % kubectl get pods -o wide
NAME                  READY   STATUS    RESTARTS   AGE   IP            NODE                 NOMINATED NODE   READINESS GATES
test-lifehook-2f7jt   1/1     Running   0          73s   10.244.0.23   kind-control-plane   <none>           2/2
test-lifehook-szps2   1/1     Running   0          73s   10.244.0.24   kind-control-plane   <none>           2/2
test-lifehook-vlkkx   1/1     Running   0          73s   10.244.0.25   kind-control-plane   <none>           2/2
lr90@sj ~ % kubectl get pods -o yaml | grep lifecycle.apps.kruise.io/state
      lifecycle.apps.kruise.io/state: PreparingNormal
      lifecycle.apps.kruise.io/state: PreparingNormal
      lifecycle.apps.kruise.io/state: PreparingNormal
```

当业务控制器在检测到 pod 处于 PreparingNormal 状态时，可以做一些**接入流量前的初始化操作**，但需要注意的是，此时 pod 都是 ready 状态，因此都已经注册到了 K8s service 的 endpoints 上，这里的接入流量是指非 K8s service 类的服务注册中心。注意这里在 preNormal 中配置 `markPodNotReady: true` 是不起作用的，markPodNotReady 只在 preDelete 和 inPlaceUpdate 时有用，官方文档没有提到这一点。

如果控制器不给 pod 添加 finalizer，pod 将使用处于 PreparingNormal 状态。因此我们这里模拟业务控制器，给三个 pod 加上 finalier: example.io/unready-blocker。当三个 pod 都加上 finalizer 之后，pod 的状态变为 Normal。

**因此对于 preNormal hook 来说，其从 PreparingNormal 变成 Normal 的条件是需要 finalizer 存在，这个 finalizer 是需要业务控制器添加的。**

```s
lr90@sj ~ % kubectl get pods -o yaml | grep lifecycle.apps.kruise.io/state
      lifecycle.apps.kruise.io/state: Normal
      lifecycle.apps.kruise.io/state: Normal
      lifecycle.apps.kruise.io/state: Normal
```

### inPlaceUpdate
经过上面业务控制器添加 finalizer 之后，所有的 state 都变成了 Normal，这个时候我们需要对 pod 进行原地升级，我们当前使用的镜像为 `nginx:alpine`，假设需要升级为 `nginx:stable`，同时我们在 cloneset 的升级策略中指定了 `maxUnavailable: 1`，因此升级过期期望是一个一个升级的。当我们 edit cloneset 替换镜像之后，pod 的状态变成如下：
```s
lr90@sj ~ % kubectl get pods -o wide
NAME                  READY   STATUS    RESTARTS   AGE   IP            NODE                 NOMINATED NODE   READINESS GATES
test-lifehook-2f7jt   1/1     Running   0          11h   10.244.0.23   kind-control-plane   <none>           2/2
test-lifehook-szps2   1/1     Running   0          11h   10.244.0.24   kind-control-plane   <none>           2/2
   1/1     Running   0          11h   10.244.0.25   kind-control-plane   <none>           1/2
lr90@sj ~ % kubectl get pods -o yaml | grep lifecycle.apps.kruise.io/state
      lifecycle.apps.kruise.io/state: Normal
      lifecycle.apps.kruise.io/state: Normal
      lifecycle.apps.kruise.io/state: PreparingUpdate
```
由上面可见，其中一个 pod 的状态变成了 `PreparingUpdate`，并且其已经变成了不健康，其 READINESS GATES 只有一个是 READY 的，需要注意的时，此时还没有开始升级，其镜像仍然是旧版本。这时我们将不健康的 pod 的 finalizer 去掉。此时待升级的 pod 状态变成 Updating，并且升级完成之后变成 Updated。但是在 cloneset 的状态定义中，只有 Normal 是正常的状态，因此变成 Updated 时，这个 pod 的升级过程并没有完成，整个 cloneset 的升级也会卡住。接下来要做的是用户控制器需要再给这个 pod 添加对应的 finalizer，使整个升级过程继续下去。
```s
lr90@sj ~ % kubectl get pod test-lifehook-vlkkx -o yaml | grep lifecycle.apps.kruise.io/state
      lifecycle.apps.kruise.io/state: Updating

# 隔一段时间之后，变成 Updated
lr90@sj ~ % kubectl get pod test-lifehook-vlkkx -o yaml | grep lifecycle.apps.kruise.io/state
      lifecycle.apps.kruise.io/state: Updated

# 用户控制器添加 finalizer 之后，升级继续下去
lr90@sj ~ % kubectl get pods -o wide
NAME                  READY   STATUS    RESTARTS      AGE   IP            NODE                 NOMINATED NODE   READINESS GATES
test-lifehook-2f7jt   1/1     Running   0             11h   10.244.0.23   kind-control-plane   <none>           2/2
test-lifehook-szps2   1/1     Running   0             11h   10.244.0.24   kind-control-plane   <none>           1/2
test-lifehook-vlkkx   1/1     Running   1 (11m ago)   11h   10.244.0.25   kind-control-plane   <none>           2/2
lr90@sj ~ % kubectl get pods -o yaml | grep lifecycle.apps.kruise.io/state
      lifecycle.apps.kruise.io/state: Normal
      lifecycle.apps.kruise.io/state: PreparingUpdate
      lifecycle.apps.kruise.io/state: Normal
```
后续的过程跟上述一致，不再赘述。对三个 pod 都执行上述操作之后，所有的升级过程完成。

**总结来说，对于 inPlaceUpdate hook，用户控制器需要干预两次：1）在升级前去掉 finalizer，并开始替换镜像升级；2）在升级完成之后，添加 finalizer，完成一个 pod 的升级，并开始升级下一个 pod**

### preDelete
这里删除的场景是正常缩容，或者重建升级。我们首先以缩容来看下效果。还是继续上面的测试，假设我们要将三副本改为二副本，修改之后，发现一个 pod 卡在了 PreparingDelete 状态。此时有两点需要注意：1）此时该 pod 已经 NotReady，因为我们设置了 markPodNotReady: true；2）此时 pod 的 deletionTimestamp 为 nil，也就是说还没有触发 K8s 层面的删除。当业务控制器删除 finalizer 之后，pod 被删除。

```s
lr90@sj ~ % kubectl get pods -o wide
NAME                  READY   STATUS    RESTARTS      AGE   IP            NODE                 NOMINATED NODE   READINESS GATES
test-lifehook-2f7jt   1/1     Running   1 (15m ago)   11h   10.244.0.23   kind-control-plane   <none>           1/2
test-lifehook-szps2   1/1     Running   1 (18m ago)   11h   10.244.0.24   kind-control-plane   <none>           2/2
test-lifehook-vlkkx   1/1     Running   1 (32m ago)   11h   10.244.0.25   kind-control-plane   <none>           2/2
lr90@sj ~ % kubectl get pods -o yaml | grep lifecycle.apps.kruise.io/state
      lifecycle.apps.kruise.io/state: PreparingDelete
      lifecycle.apps.kruise.io/state: Normal
      lifecycle.apps.kruise.io/state: Normal

# 当业务控制器删除 finalizer 之后，pod 被删除
lr90@sj ~ % kubectl get pod test-lifehook-2f7jt
Error from server (NotFound): pods "test-lifehook-2f7jt" not found
```
因此这里的 finalizer 并不是卡住 K8s apiserver 删除资源的，而是业务控制器的一个配置信息，因此这个配置信息通过 label 也能做到。
