---
layout:     post
title:      "影响k8s调度器的一些因素"
subtitle:   " \"Assigning Pods to Nodes\""
date:       2019-09-29 12:04:00
author:     "weak old dog"
header-img-credit: false
tags:
    - k8s
    - scheduler
---

## 前言
影响k8s调度pod的因素很多，比如：nodeSelector、nodeName、Affinity及anti-affinity、Inter-pod affinity及anti-affinity、Taints and Tolerations
等。之前这是对这些有大致了解，没有深入了解，这次总结，目的是解决为什么有这么多因素的需求问题，以及各个因素的应用场景。基于的k8s文档为1.16。
k8s文档如下：

[Assigning Pods to Nodes](https://kubernetes.io/docs/concepts/configuration/assign-pod-node/) 

[Taints and Tolerations](https://kubernetes.io/docs/concepts/configuration/taint-and-toleration/)

## NodeName
我们可以直接在controller的pod template中设置pod spec的NodeName字段，这是控制pod调度到node最简单暴力的方式，需要注意的是，如果直接设置了NodeName，那么pod就不走调度器了，调度器的调度队列只监听`len(NodeName)==0`的pod。这个pod会直接被kubelet监听，并开始初始化并启动的操作。设置NodeName是不推荐使用的。

## nodeSelector
nodeSelector是推荐使用的较为简单的影响pod调度到node的一种方法。`nodeSelector`也是pod spec中的字段，即`NodeSelector map[string]string`，是一些`key-value`对，如果一个pod能调度到某个node，这个node必须要拥有这个map中的所有label，（当前也可能有额外的label）。

使用nodeSelector的步骤为：

### step 1: 给node打标签
通过`kubectl get nodes`可以看到集群中所有的node，通过命令：

`kubectl label nodes <node-name> <label-key>=<label-value>`

可以给node打上特定类型的标签，比如你想给node01打上`disktype=ssd`的标签，需要执行的命令为：
`kubectl label nodes node01 disktype=ssd` 打上标签之后，可以通过命令`kubectl get nodes --show-labels`以及命令`kubectl describe node node01`来确认node的标签。

### step 2: 在pod的spec中设置nodeSelector
我们很少单独定义一个pod的yaml文件，一般是通过控制器来定义pod的template，总之方式都是一样的，假如我有一个pod的yaml，设置nodeSelector的方式如下：
```yaml
apiVersion: v1
kind: Pod
metadata:
  name: nginx
  labels:
    env: test
spec:
  containers:
  - name: nginx
    image: nginx
    imagePullPolicy: IfNotPresent
  nodeSelector:
    disktype: ssd
```
当我们使用`kubectl apply -f`创建这个pod的时候，这个pod就会被调度到含有标签`disktype=ssd`的node上。

> 内置的node标签：
> 除了我们主动添加的lable，node还存在一些内置的标签，这些内置的标签是由云供应商提供的，是不可控的，比如标签`kubernetes.io/hostname`的值在一些集群中会是node的名字，但在另一些集群中则不是。

在node和pod上打标签，允许一部分pod只能调度到特定的node上，这可以被用来做隔离、安全、或者特定的管理策略，当我们出于这种目的给node打标签时，**必须要保证我们的标签不能被kubelet进程修改**。`NodeRestriction`准入插件禁止kubelet修改node的以`node-restriction.kubernetes.io`为前缀的标签，为保证此配置生效，我们需要：

  1. 确保k8s在v1.11以上，比此版本高的k8s NodeRestriction才可用。
  2. 配置`--authorization-mode=Node,...`，以及添加`NodeRestriction`准入控制插件。
  3. 给node打标签的时候，以`node-restriction.kubernetes.io/`为前缀，比如:`example.com.noderestriction.kubernetes.io/fips=true `

### Affinity and anti-affinity
nodeSelector提供了一种简单的将pod调度到node上的方式，`affinity/anti-affinity`提供了一种更宽泛的约束方式，重点如下：

  1. 约束更广泛，并不只是And以及严格匹配。
  2. `affinity/anti-affinity`可以提供非硬性约束，调度器不能满足这些条件时，pod仍然可以被调度。
  3. 可以控制pod之间的行为，一些pod希望个另一些pod在同一个节点上，或者跟另一些pod不在一个节点上。

这些affinity特性分为两类：`node affinity`以及`inter-pod affinity/anti-affinity`，`node affinity`跟nodeSelector类似，但是有上面的1、2点好处；`inter-pod affinity/anti-affinity`提供pod的label之间的约束行为。
关于第一条是说提供了多种运算符，并不只是严格的等于，如下：
```golang
	NodeSelectorOpIn           NodeSelectorOperator = "In"
	NodeSelectorOpNotIn        NodeSelectorOperator = "NotIn"
	NodeSelectorOpExists       NodeSelectorOperator = "Exists"
	NodeSelectorOpDoesNotExist NodeSelectorOperator = "DoesNotExist"
	NodeSelectorOpGt           NodeSelectorOperator = "Gt"
	NodeSelectorOpLt           NodeSelectorOperator = "Lt"
```

##### Node affinity
Node affinity在概念上跟nodeSelector上一致，它同样根据node的标签，来约束哪些pod可以调度到该node上。目前有两类node affinity，分别为：

`requiredDuringSchedulingIgnoredDuringExecution`  
`preferredDuringSchedulingIgnoredDuringExecution`

你可以将他们分别看做`硬性`以及`非硬性`约束，第一个约束指定pod调度到node的硬性条件（类似nodeSelector，但是语法上更宽泛一些），第二个约束提供一个`preference`，并不是强制规定。`IgnoredDuringExecution`部分跟nodeSelector类似，当一个pod已经在node上运行了，但是node标签变了，已经不满足nodeSelector约束条件了，pod仍然继续运行。

pod的spec中有个`affinity`字段，但是具体来说这个字段还是有点麻烦的，嵌套了好几层，下面是给pod设置NodeAffinity的示例：
```yaml
apiVersion: v1
kind: Pod
metadata:
  name: with-node-affinity
spec:
  affinity:
    nodeAffinity:
      requiredDuringSchedulingIgnoredDuringExecution:
        nodeSelectorTerms:
        - matchExpressions:
          - key: kubernetes.io/e2e-az-name
            operator: In
            values:
            - e2e-az1
            - e2e-az2
      preferredDuringSchedulingIgnoredDuringExecution:
        - weight: 1
        preference:
          matchExpressions:
          - key: another-node-label-key
            operator: In
            values:
            - another-node-label-value
  containers:
   - name: with-node-affinity
  image: k8s.gcr.io/pause:2.0
```
这个pod的NodeAffinity对node的要求是：node必须含有`kubernetes.io/e2e-az-name`标签，并且标签的值为`e2e-az1`**或者**`e2e-az2`，如果有多个这样的node，优先使用含有标签`another-node-label-key=another-node-label-value`的node。

另外，还有一些规则如下：

 1. 如果同时指定了nodeSelector以及nodeAffinity，要调度到一个node上，这些条件必须都满足。
 2. 如果指定了多个`nodeSelectorTerms`（这是一个slice），node只需要满足一个就可以了，代码中也说了，这是`或`的关系。
 3. `nodeSelectorTerms`下面的`matchExpressions`也是一个数组，但是在同一个`nodeSelectorTerms`的`matchExpressions`是一个`且`的关系。
 4. 在pod已经调度成功后，你删除或修改node的label，这个pod是不会被删除的，affinity只在pod调度的时候有影响。

##### Inter-pod affinity and anti-affinity
这些affinity是根据node上已经运行的pod的标签，来决定当前pod是否能调度到Node上。这些规则可以简述为：pod能调度到节点X上，只有X上已经运行了至少一个pod，并且这个pod满足条件Y。条件`Y`就是一些LabelSelector，这些labelSelector是跟namespace相关的（pod是namespace相关的，因此pod的label也是）。
> `Inter-pod affinity and anti-affinity`需要大量逻辑处理，可能会拖慢调度过程，在有几百个节点以上的集群中，不建议使用。

> Pod `anti-affinity`需要所有的node都打上标签`topologyKey`，没有这个标签会产生不可预期的行为。

`topologyKey`的概念一直没讲清楚，文档说这个用来表示一种拓扑结构，比如：节点、机架、云供应商的zone或者region。
跟node affinity一致，pod affinity以及anti-affinity也分为`hard`, `soft`两类，（各分两类）：
`requiredDuringSchedulingIgnoredDuringExecution`  
`preferredDuringSchedulingIgnoredDuringExecution`

下面是一个使用pod affinity的例子，注意缩进表示的golang数据结构组合关系，`-` 符号表示数组的一项。
```yaml
apiVersion: v1
kind: Pod
metadata:
  name: with-pod-affinity
spec:
  affinity:
    podAffinity:
      requiredDuringSchedulingIgnoredDuringExecution:
      - labelSelector:
         matchExpressions:
         - key: security
           operator: In
           values:
           - S1
        topologyKey: failuredomain.beta.kubernetes.io/zone
    podAntiAffinity:
      preferredDuringSchedulingIgnoredDuringExecution:
      - weight: 100
        podAffinityTerm:
          labelSelector:
            matchExpressions:
            - key: security
              operator: In
              values:
              - S2
          topologyKey: failuredomain.beta.kubernetes.io/zone
containers:
- name: with-pod-affinity
image: k8s.gcr.io/pause:2.0
```
这个例子定义了一个pod affinity规则，以及一个pod anti-affinity规则，第一个规则定义为：pod对node的要求是，node上面至少有个正在运行的其他pod，并且这个pod含有标签`security=S1`，并且node含有标签`failure-domain.beta.kubernetes.io/zone`（关于这点，我理解的也不清楚，只能等以后结合具体实例看一下了）

### 关于topology key
关于Affinity以及AntiAffinity中的`PodAffinityTerm`代码中有这样一段注释，
>  Defines a set of pods (namely those matching the labelSelector relative to the given namespace(s)) that this pod should be co-located (affinity) or not co-located (anti-affinity) with, where co-located is defined as running on a node whose value of the label with key topologyKey matches that of any node on which a pod of the set of pods is running

这段注释中解释了topologyKey的含义，首先，在`PodAffinityTerm`中定义的topologyKey指的是node的一个label，这个label以及label的value，指定了这个node的拓扑结构，如果可以指定在同一个机架上，所有node的含有这个topologyKey label，并且这个label的value是相同的。AntiAffinity以及Affinity的`co-located`，即共存或者不共存，指的是在这个**topology**中共存不共存，默认情况下，这个key是`kubernetes.io/hostname`，volue是hostname，在这种情况下比较简单，就是指pod能不能共存在一个node上。

## 实际用例
Interpod Affinity 以及 AntiAffinity在ReplicaSets、StatefulSets、以及Deployment中用处更大一些。比如，假如我们起了三个redis作为内存缓存，分布在三个节点；同时我们希望起三个web-server，每个web-server都跟一个redis在同一个节点。这里有两个需求：1）三个pod分布在三个节点；2）另外三个pod实例web-server希望依附于之前三个redis实例。

关于第一个需求：使用pod anti-affinity实现，首先template中先给pod打一个`app=store`的标签，其次利用podAntiAffinity的requiredDuringSchedulingIgnoredDuringExecution配置拥有app=store标签的pod互斥，这个是强制规定，配置如下：
```yaml
template:
  metadata:
    labels:
      app: store
spec:
  affinity:
    podAntiAffinity:
      requiredDuringSchedulingIgnoredDuringExecution:
      - labelSelector:
        matchExpressions:
        - key: app
          operator: In
          values:
          - store
        topologyKey: "kubernetes.io/hostname"
```
关于第二个需求：首先配置podAntiAffinity的requiredDuringSchedulingIgnoredDuringExecution来保证三个web-server互斥，跟上面的redis配置一致；其次配置podAffinity的requiredDuringSchedulingIgnoredDuringExecution要求节点运行了redis（要求节点含有有标签`app=store`的pod），配置就不贴了。
