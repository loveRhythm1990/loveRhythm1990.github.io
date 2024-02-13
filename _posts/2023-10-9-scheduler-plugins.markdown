---
layout:     post
title:      "通过 Scheduler Plugins 扩展 K8s 调度器"
date:       2023-09-20 10:10:00
author:     "decent"
header-img-credit: false
tags:
    - Scheduler
---

**目录**
- [概述](#概述)
- [部署安装](#部署安装)
  - [集群第二个调度器](#集群第二个调度器)
  - [集群唯一调度器](#集群唯一调度器)
- [一些插件](#一些插件)
  - [Capacity Scheduler](#capacity-scheduler)
  - [Coschedulering](#coschedulering)
  - [Node Resources](#node-resources)
  - [Network-Aware Scheduling](#network-aware-scheduling)


## 概述
项目 [scheduler-plugins](https://github.com/kubernetes-sigs/scheduler-plugins) 整合了一些第三方调度插件，部分插件已经经历过了线上开发环境的实践，其他插件也可以为我们扩展调度器提供一些思路。

部署使用 scheduler-plugins 时需要其与现有 Kubernetes 的兼容性，需要选择对应版本的镜像（兼容列表可在 github 主页看到）。同时，scheduler-plugins 是基于默认 default-scheduler 编译的，default-scheduler 所具有的调度插件 scheduler-plugins 也都是有的。

## 部署安装
scheduler-plugins 的工作方式有两种：1）作为集群的第二个调度器（也就是集群中存在两个调度器）；2）作为集群中唯一的调度器。

这两种方式部署方式不一致，其中方式2）复杂一点，但是也是比较合理的方式。部分方式参考文档 [Install Scheduler-plugins](https://github.com/kubernetes-sigs/scheduler-plugins/blob/master/doc/install.md).

另外，我们除了部署一个 scheduler deployment 之外，还要部署一个 scheduler controller，因为许多调度插件都引入了一些 CRD，那就需要控制器来同步这些 CRD，比如 coscheduling 的 PodGroup。

### 集群第二个调度器
这种方式下，可以直接通过 [helm chart](https://github.com/kubernetes-sigs/scheduler-plugins/tree/master/manifests/install/charts) 安装。

通过 helm chart 安装时，调度器的名字为 `scheduler-plugins-scheduler`，如果要使用这个调度器，需要在资源的 spec 中显式指定这个调度器。以 deployment 为例，指定方式如下：
```yaml
# deploy.yaml
...
spec:
  ...
  template:
    spec:
      schedulerName: scheduler-plugins-scheduler
```
但是在生产环境下一般不建议使用多个调度器，而且集群 node 资源是全局的，两个调度器会发生冲突，比如两个调度器同时把 pod 调度到了 node1 上（他们看到这个 node 的 cache 资源时，同时认为这个 node 资源是足够的），那么后面那个调度过去的 pod 有可能会被 evict。

### 集群唯一调度器
作为集群唯一调度器时，需要替换原 K8s 集群的调度器，因为原 K8s 集群部署方式多种多样，所以替换方式也视情况而定。

文档 [Install Scheduler-plugins](https://github.com/kubernetes-sigs/scheduler-plugins/blob/master/doc/install.md) 介绍了以 kind 方式安装的 K8s 集群的替换方式：替换 manifest 文件，重新生成 static pod。

## 一些插件
[Github 主页](https://github.com/kubernetes-sigs/scheduler-plugins/tree/master?tab=readme-ov-file#plugins) 也介绍了一些第三方插件。这些插件比较细节，要是深入了解也比较花时间，这里简单了解下其作用和应用场景，对实现方式不做深入研究。

### Capacity Scheduler
Capacity Scheduler 主要是参考 Yarn 中的资源调度器，用来解决 ResourceQuota 实现资源配额的不足，比如：某一个 namespace 闲置的资源，另一个 namespace 用不到。Capacity Scheduler 引入了资源下线 Min 以及资源上限 Max 的概念，具有一定弹性。

[阿里云文档](https://help.aliyun.com/zh/ack/ack-managed-and-ack-dedicated/user-guide/use-capacity-scheduling)对 Capacity Scheduelr 做了详细描述，其模型如下图。

![java-javascript](/pics/capacity-scheduler.png){:height="60%" width="60%"}

其中 Min 与 Max 有如下规则：
* 同一个弹性配额中的 Min ≤ Max。
* 子级弹性配额的 Min 之和 ≤ 父级的 Min。
* Root 节点的 Min = Max ≤ 集群总资源。
* Namespace 只与弹性配额的叶子节点有一对多的对应关系，且同一个Namespace 只能归属于一个叶子节点。

### Coschedulering
批调度器，一次调度一批 pod，[Github 也用例子](https://github.com/kubernetes-sigs/scheduler-plugins/blob/master/pkg/coscheduling/README.md)，也比较容易理解。这里一个例子如下：
```yaml
apiVersion: scheduling.x-k8s.io/v1alpha1
kind: PodGroup
metadata:
  name: nginx
spec:
  scheduleTimeoutSeconds: 10
  minMember: 3
---
apiVersion: apps/v1
kind: ReplicaSet
metadata:
  name: nginx
  labels:
    app: nginx
spec:
  replicas: 6
  selector:
    matchLabels:
      app: nginx
  template:
    metadata:
      name: nginx
      labels:
        app: nginx
        scheduling.x-k8s.io/pod-group: nginx
    spec:
      containers:
      - name: nginx
        image: nginx
        resources:
          requests:
            cpu: 3000m
            memory: 500Mi
```
上面配置了一个 PodGroup，其**最少同时调度实例数**是 3，那么至少将有 3 个 pod 被同时调度。并且 ReplicaSet 是通过 label `scheduling.x-k8s.io/pod-group` 与 PodGroup 关联的。

### Node Resources
Node Resources 的文档在[这里](https://github.com/kubernetes-sigs/scheduler-plugins/blob/master/pkg/noderesources/README.md)，看上去支持：
1. 调度到可用资源最少的节点上。
2. 调度到可用资源最多的节点上。
3. 支持给资源配置权重。


### Network-Aware Scheduling
网络感知的调度，文档在[这里](https://github.com/kubernetes-sigs/scheduler-plugins/tree/master/pkg/networkaware/networkoverhead)。

这个插件提供了 Filter 插件和 Score 插件。Filter 插件过滤掉网络代价超过 `maxNetworkCost` 的节点。  