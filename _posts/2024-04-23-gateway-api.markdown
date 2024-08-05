---
layout:     post
title:      "使用 gateway api 作为 K8s 外部流量入口"
date:       2024-04-23 10:10:00
author:     "decent"
header-img-credit: false
tags:
    - K8s生态
    - Istio
---

**目录**
- [gateway api 概述](#gateway-api-概述)
- [快速入门](#快速入门)
  - [部署 metallb](#部署-metallb)
  - [安装 crd 以及 istio](#安装-crd-以及-istio)
  - [部署测试服务](#部署测试服务)
  - [配置入口 gateway](#配置入口-gateway)
  - [添加业务路由到 gateway](#添加业务路由到-gateway)
  - [基于权重的路由](#基于权重的路由)
- [总结](#总结)


### gateway api 概述
gateway api 是 K8s 发起的下一代 ingress，关注四层和七层转发，其官方文档为：[gateway api introduction](https://gateway-api.sigs.k8s.io/)。与 ingress 不同，gateway 还计划通过 [GAMMA](https://gateway-api.sigs.k8s.io/mesh/gamma/) 项目来支持东西向流量（集群内部微服务之间的流量）。

gateway api 的设计目标为：更加通用，表现力强，权限分工明确。前两者主要是通过更多的配置来实现，现有的 ingress 一般是添加更多的 annotation 来实现更多的功能，而且不同 ingress 控制器有不同的 annotation 配置；权限分工明确是指资源分层，由不同的人员来管理，比如 GatewayClass 资源由集群基础设施提供者维护（类比与 K8s 存储中的 StorageClass），Gateway 资源由运维人员提供，而具体的 TLSRoute 规则则由应用开发者提供。

与 ingress 中的 IngressController 一样，gateway api 也有许多不同的实现，本文以 istio 为例介绍下 gateway api 的使用，主要参考[Kubernetes Gateway API 入门](https://istio.io/latest/zh/blog/2022/getting-started-gtwapi/)，本文运行环境为 kind 启动的集群、gateway api 文档、istio 文档等。

### 快速入门

#### 部署 metallb 
部署 istio 需要一个 loadbalancer 类型的 service，此 service 即公网入口。我们通过 [metallb](https://metallb.universe.tf/installation/) 为其分配 externalIP。安装完成之后，组件部署在 metallb-system 命名空间。
```s
kubectl apply -f https://raw.githubusercontent.com/metallb/metallb/v0.14.8/config/manifests/metallb-native.yaml
```
安装完成之后，还要配置 ip 池，loadbalancer 类型的 service 将从这个 ip 池里分配 ip，这里注意 ip 池里面的网段不要跟集群中的已知网段重合，包括：节点池网段、service 网段、pod ip 网段等。下面是配置使用 `192.168.10.0/24` 网段，kind 不会使用这个网段。
```yaml
apiVersion: metallb.io/v1beta1
kind: IPAddressPool
metadata:
  name: first-pool
  namespace: metallb-system
spec:
  addresses:
  - 192.168.10.0/24
```

#### 安装 crd 以及 istio
首先安装 gateway api 所需要的 crd，通过下面命令安装，这些 crd 是 K8s 定义的，跟供应商无关的。
```s
lr90@sj ingress % kubectl get crd gateways.gateway.networking.k8s.io &> /dev/null || \
  { kubectl kustomize "github.com/kubernetes-sigs/gateway-api/config/crd?ref=v1.1.0" | kubectl apply -f -; }

customresourcedefinition.apiextensions.k8s.io/gatewayclasses.gateway.networking.k8s.io created
customresourcedefinition.apiextensions.k8s.io/gateways.gateway.networking.k8s.io created
customresourcedefinition.apiextensions.k8s.io/grpcroutes.gateway.networking.k8s.io created
customresourcedefinition.apiextensions.k8s.io/httproutes.gateway.networking.k8s.io created
customresourcedefinition.apiextensions.k8s.io/referencegrants.gateway.networking.k8s.io created
```
部署 gateway api 的控制器实现，本文部署 istio，通过下面命令部署，部署完成后，组件安装在 istio-system 目录。
```s
curl -L https://istio.io/downloadIstio | sh -
cd istio-1.22.3
./bin/istioctl install --set profile=minimal -y
```

#### 部署测试服务
部署 istio 项目中的 [helloworld 示例程序](https://github.com/istio/istio/tree/release-1.22/samples/helloworld)，需要先把项目拉下来，并 cd 到项目的根目录。
```s
kubectl create ns sample
kubectl apply -f samples/helloworld/helloworld.yaml -n sample
```
部署完成后我们先看一下项目部署结构。sample 项目有两个不同的 deployment，代表服务有两个不同的版本，每个 deployment 除了带有 `app: helloworld` selector 外还有 version selector。但是 sample 只有一个 service，并且这个 service 的 selector 只有 app label，所以这个 service 会同时选中两个 pod。
```s
lr90@sj istio % kubectl get deploy -n sample
NAME            READY   UP-TO-DATE   AVAILABLE   AGE
helloworld-v1   1/1     1            1           2m4s
helloworld-v2   1/1     1            1           2m4s
lr90@sj istio % kubectl get pods -n sample --show-labels
NAME                            READY   STATUS    RESTARTS   AGE     LABELS
helloworld-v1-c5ffc9b8-thslw    1/1     Running   0          2m21s   app=helloworld,pod-template-hash=c5ffc9b8,version=v1
helloworld-v2-86b5f6484-mqz29   1/1     Running   0          2m21s   app=helloworld,pod-template-hash=86b5f6484,version=v2
lr90@sj istio % kubectl get service -n sample
NAME         TYPE        CLUSTER-IP    EXTERNAL-IP   PORT(S)    AGE
helloworld   ClusterIP   10.96.66.82   <none>        5000/TCP   2m45s
lr90@sj istio % kubectl get endpoints -n sample
NAME         ENDPOINTS                         AGE
helloworld   10.244.0.6:5000,10.244.0.7:5000   2m57s
```

#### 配置入口 gateway
在 gateway api 中 [gateway](https://gateway-api.sigs.k8s.io/concepts/api-overview/#gateway) 定义为集群外部流量进入 K8s 集群的入口，如：loadbalancer 类型的 service，外部硬件负载均衡等。

除了 gateway 资源，gateway api 中还有两个核心资源：gatewayclass 以及 route，他们关系图如下。其中不同厂商实现自己的 gateway api 并抽象为 gatewayclass 提供出来；gateway 从属于一种 gatewayclass，并且跟 route 是 m:n 的关系，但是每个 gateway 都必须提供公网接入的方式。
![java-javascript](/pics/gateway-api01.svg){:height="60%" width="60%"}

我们通过下面配置创建 gateway，并指定 gatewayclass 为 istio；该配置转发的虚拟主机为 `*.sample.com`；允许**所有命名空间**的 route 附加到该 gateway。
```s
kubectl create namespace sample-ingress
kubectl apply -f - <<EOF
apiVersion: gateway.networking.k8s.io/v1beta1
kind: Gateway
metadata:
  name: sample-gateway
  namespace: sample-ingress
spec:
  gatewayClassName: istio
  listeners:
  - name: http
    hostname: "*.sample.com"
    port: 80
    protocol: HTTP
    allowedRoutes:
      namespaces:
        from: All
EOF
```
上面配置跟 ingress 配置类似，包括虚拟主机配置、协议等。不同的是，gateway 配置没有路由信息，路由信息是单独配置在 route 资源中的，并附加到该 gateway。路由信息应该由业务开发人员配置，以此来实现分工明确。

另外上述 gateway 配置完之后，istio 会自动创建一个 loadbalancer 类型的 service 作为流量入口。该 service 也是对 gateway 协议的实现。并且我们看到 metallb 已经给我们分配好了 ip 地址：192.168.1.240。

```s
lr90@sj ingress % kubectl get service -n sample-ingress
NAME                   TYPE           CLUSTER-IP    EXTERNAL-IP     PORT(S)                        AGE
sample-gateway-istio   LoadBalancer   10.96.24.46   192.168.1.240   15021:32342/TCP,80:32117/TCP   105m
```

#### 添加业务路由到 gateway
我们通过下面配置创建路由，该路由需要跟业务部署在同一个命名空间，不然使用 backendRefs 指定 service 时找不到。该路由配置通过 parentRefs 绑定上面的 sample-gateway；指定虚拟主机为 `helloworld.sample.com`；匹配前缀 `/hello`；转发到后端的 helloworld service。
```yaml
kubectl apply -n sample -f - <<EOF
apiVersion: gateway.networking.k8s.io/v1beta1
kind: HTTPRoute
metadata:
  name: helloworld
spec:
  parentRefs:
  - name: sample-gateway
    namespace: sample-ingress
  hostnames: ["helloworld.sample.com"]
  rules:
  - matches:
    - path:
        type: Exact
        value: /hello
    backendRefs:
    - name: helloworld
      port: 5000
EOF
```
ok，路由信息配置完之后，终于可以测试了，我们首先将 kind 环境中的 loadbalancer service port-forward 出来，因为 kind K8s 网络跟我们的个人电脑网络不一致，我们通过将服务 forward 出来模拟暴露公网。
```s
lr90@sj ingress % kubectl port-forward service/sample-gateway-istio -n sample-ingress 8080:80
Forwarding from 127.0.0.1:8080 -> 80
Forwarding from [::1]:8080 -> 80
```
通过下面命令测试，可以看到 v1 跟 v2 五五开，流量各占一半。
```s
for run in {1..10}; do curl -HHost:helloworld.sample.com http://127.0.0.1:8080/hello; done
lr90@sj ~ % for run in {1..10}; do curl -HHost:helloworld.sample.com http://127.0.0.1:8080/hello; done
Hello version: v2, instance: helloworld-v2-86b5f6484-mqz29
Hello version: v1, instance: helloworld-v1-c5ffc9b8-thslw
Hello version: v2, instance: helloworld-v2-86b5f6484-mqz29
Hello version: v1, instance: helloworld-v1-c5ffc9b8-thslw
Hello version: v1, instance: helloworld-v1-c5ffc9b8-thslw
Hello version: v1, instance: helloworld-v1-c5ffc9b8-thslw
Hello version: v1, instance: helloworld-v1-c5ffc9b8-thslw
Hello version: v1, instance: helloworld-v1-c5ffc9b8-thslw
Hello version: v2, instance: helloworld-v2-86b5f6484-mqz29
Hello version: v1, instance: helloworld-v1-c5ffc9b8-thslw
```

#### 基于权重的路由
整体路由配置跟上面差不多，不同之处在于 backendRefs 的不同。针对 `/hello`这个前缀，这里配置了两个 service（这两个 service 需要提前创建，示例代码的目录里有），并且为两个不同的 service 配置了权重，转发到 `helloworld-1` 服务的权重为 90%， 转发到 `helloworld-2` 服务的权重为 10%。具体操作流程跟上述一致，这里不在叙述了。
```yaml
$ kubectl apply -n sample -f - <<EOF
apiVersion: gateway.networking.k8s.io/v1beta1
kind: HTTPRoute
metadata:
  name: helloworld
spec:
  parentRefs:
  - name: sample-gateway
    namespace: sample-ingress
  hostnames: ["helloworld.sample.com"]
  rules:
  - matches:
    - path:
        type: Exact
        value: /hello
    backendRefs:
    - name: helloworld-v1
      port: 5000
      weight: 90
    - name: helloworld-v2
      port: 5000
      weight: 10
EOF
```

### 总结
本文通过一个具体的例子来了解下 K8s gateway api 的基本配置部署流程，基本有了大概的认识。istio 有自己的流量入口实现（virtualservice/DestinationRule），本文介绍的是其对 K8s gateway api 的实现，两者的区别可以参考 [istio K8s gateway api](https://istio.io/latest/docs/tasks/traffic-management/ingress/gateway-api/)。

总体来说，我感觉跟 ingress 差不多，而且我更习惯 ingress，可能是因为我日常的工作已经涵盖了从部署 K8s 集群到写控制器并暴露给外网。