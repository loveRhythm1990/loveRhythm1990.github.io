---
layout:     post
title:      "使用 metallb 为 loadbalance 类型的服务分配 ip"
date:       2023-3-8 10:10:00
author:     "decent"
header-img-credit: false
tags:
    - Istio
    - 运维
---

**文章目录**
- [metallb 概述](#metallb-概述)
  - [对比 keepalived](#对比-keepalived)
- [安装](#安装)
- [配置 ip 池](#配置-ip-池)
- [配置服务，并测试](#配置服务并测试)
  - [集群内访问](#集群内访问)
  - [集群外访问](#集群外访问)
- [高级特性](#高级特性)
  - [多 service 共享 vip](#多-service-共享-vip)
  - [指定 vip](#指定-vip)
  - [指定 ip 池](#指定-ip-池)
- [局限性](#局限性)

### metallb 概述
在部署测试 Istio 时，发现依赖 LoadBalance 类型的 Service。LoadBalance 类型的 Service 一般由公有云厂商分配 external-ip，从而在集群外访问集群内的服务，如果是我们自己的测试集群，又依赖 LoadBalance 类型的服务，那怎么办呢？[metallb](https://github.com/metallb/metallb) 提供了一种解决方案，能够为集群内的服务分配 external-ip，并可以从集群外访问。

#### 对比 keepalived 
在 layer2 工作模式下，其与 [KeepAlived](https://metallb.universe.tf/concepts/layer2/#comparison-to-keepalived)类似，不过 KeepAlived 通过 VRRP(Virtual Router Redundancy Protocol) 交换路由协议，而 metallb layer2 通过 gossip 协议（具体实现为 [memberlist](https://github.com/hashicorp/memberlist)）交换成员信息，并进行选举，其中成员是指 metallb 中用于响应 arp 请求的 speaker 节点，只有一个节点在工作，其余 standby。

另外有一个使用场景上的区别是，metallb 依赖 K8s 集群，是为 K8s 量身定做的的。keepalived 则没有此限制。

### 安装
通过下面命令安装 metallb，并验证所有 pod 是否正常 Running。
```s
kubectl apply -f https://raw.githubusercontent.com/metallb/metallb/v0.12.1/manifests/namespace.yaml
kubectl apply -f https://raw.githubusercontent.com/metallb/metallb/v0.12.1/manifests/metallb.yaml

[decent@Master resources]$ kubectl get pods -n metallb-system
NAME                          READY   STATUS    RESTARTS   AGE
controller-6554b76d68-kpp8x   1/1     Running   0          46m
speaker-swgml                 1/1     Running   0          46m
speaker-w9lvj                 1/1     Running   0          46m
speaker-x5tbp                 1/1     Running   0          46m
```

### 配置 ip 池
external ip 池通过 configmap 的形式进行配置，分配的 ip 段位 `192.168.31.210-192.168.31.220`，这个 ip 段是我家里的路由器的 ip 段的一部分，我家里的路由器 ip 子网位 `192.168.31.1/24`，要保证这个 ip 段不会被分配到其他设备，这个可以登录路由器修改 dhcp 的ip分配范围，其实大可不必，家里没有这么多设备需要 ip。

> 最新版的 metallb 好像不是通过 cm 配置了，后面再研究下怎么使用。另外使用过程中发现一个问题，metallb 会记录 service 的 ip 分配历史，如果之前分配过一个 ip，但是又想重新分配一个，这个时候好像得重启 metallb 的 controller，不然总是分配之前分配过的 ip.

```yml
apiVersion: v1
kind: ConfigMap
metadata:
  namespace: metallb-system
  name: config
data:
  config: |
    address-pools:
    - name: default
      protocol: layer2
      addresses:
      - 192.168.31.210-192.168.31.220
```
通过 kubectl apply -f 创建上面的 cm。

在高版本 metallb 中，需要使用下面命令创建 ip 池。在 kind 环境下首先需要确定 node 网段的 ip 范围，然后划出一个小网段，给 metallb 用。
```s
subnets="$(docker network inspect -f '{{json .IPAM.Config}}' kind)"
ipv4Subnets=$(echo "${subnets}" | use_grep -oP '"Subnet":"\K[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+/[0-9]+' )
subnetPrefix=$(echo "${ipv4Subnets}" | awk -F'.' '{print $1"."$2}')

    cat <<EOF | kubectl apply -f -
apiVersion: metallb.io/v1beta1
kind: IPAddressPool
metadata:
  name: example
  namespace: metallb-system
spec:
  addresses:
    - ${subnetPrefix}.255.200-${subnetPrefix}.255.250
---
apiVersion: metallb.io/v1beta1
kind: L2Advertisement
metadata:
  name: empty
  namespace: metallb-system
EOF
```

### 配置服务，并测试
使用的测试服务为 nginx，配置如下，Service 要配置成 `LoadBalancer` 类型，
```yml
apiVersion: v1
kind: Pod
metadata:
  name: nginx-example
  labels:
    app.kubernetes.io/name: proxy
spec:
  containers:
  - name: nginx
    image: nginx:stable
    ports:
      - containerPort: 80
        name: http-web-svc
---
apiVersion: v1
kind: Service
metadata:
  name: nginx-service
spec:
  type: LoadBalancer
  selector:
    app.kubernetes.io/name: proxy
  ports:
  - name: name-of-service-port
    protocol: TCP
    port: 80
    targetPort: http-web-svc
```
查看 service 是否分配 ip 成功：
```s
[decent@Master resources]$ kubectl get service nginx-service
NAME            TYPE           CLUSTER-IP       EXTERNAL-IP      PORT(S)        AGE
nginx-service   LoadBalancer   10.102.106.196   192.168.31.211   80:32234/TCP   50m
```

#### 集群内访问
在 K8s 集群内访问这个 Service，是成功的。
```s
[decent@Master resources]$ curl 192.168.31.211
<!DOCTYPE html>
<html>
<head>
<title>Welcome to nginx!</title>
```

#### 集群外访问
在自己的 mac 电脑上访问这个 Service，也是成功的。
```s
decent@Mac ansible % curl 192.168.31.211
<!DOCTYPE html>
<html>
<head>
<title>Welcome to nginx!</title>
```

### 高级特性
metallb 支持通过给 service 配置一些 annotation 来支持一些高级特性。
#### 多 service 共享 vip
多个 LoadBalance 类型的 service 可以共享一个 vip。这时 service 需要配置一个 metallb 的 annotation，并且共享 service 的 ipkey 配置必须是一样的。同时共享 vip 的时候，service 的 externalTrafficPolicy 必须为 Cluster。
```yaml
metallb.universe.tf/allow-shared-ip="${ipkey}"
```
参考[ip-address-sharing](https://metallb.universe.tf/usage/#ip-address-sharing)

#### 指定 vip
通过下面 annotation 指定 vip，一般情况下，vip 是需要提前申请和划分的，指定 vip 是合理的。
> 注意，这里的 vip 不一定是公网 ip，一般情况下，多个 vip 可以对应同一个公网 ip，并且通过端口来区分。
```yaml
metallb.universe.tf/loadBalancerIPs="${ip}"
```
参考[requesting-specific-ips](https://metallb.universe.tf/usage/#requesting-specific-ips)

#### 指定 ip 池
通过下面 annotation 指定 ip 池，当集群中存在多个 ip 池的时候，需要指定 ip 池。
```yaml
metallb.universe.tf/address-pool="${pool}"
```

### 局限性
在 layer2 模式下，只有一个 speaker leader 在工作，其余节点都是 standby，因此这是一个单点（是指 speaker 的能力局限于 leader 所在的节点，比如带宽，当 leader 失败时，standby 应该在 10s 以内接管 leader）。

更大的问题在于，某些场景下，我们需要保留客户端原 ip，比如我们允许客户设置白名单，这个时候我们需要拿到客户的原 ip，为此我们需要将 service 的 `spec.externalTrafficPolicy` 设置为 `Local`，因为通过 vip 的流量会打到 speaker leader，而 leader 节点只会将流量打到与其同一个 K8s 节点的业务 pod，这样即使有多个业务 pod，其余也是空闲的。

> 默认情况下，externalTrafficPolicy 被配置为 Cluster，这样外部负载均衡（如供应商阿里云的负载均衡）会将流量打到所有的集群节点(每个节点对 LoadBalancer 类型的 service 都会有一个 NodePort 监听），这样集群的节点会具有负载均衡的能力。当为 Local 时，流量只会打到服务 pod 所在的节点。

metallb 支持 BGP 模式，在 BGP 模式下，每个节点都会与路由器交换路由信息，这个需要路由器的支持，目前看上去使用的局限性较大。

**参考**

[在 Kubernetes 集群中使用 MetalLB 作为 LoadBalancer（上）- Layer2](https://atbug.com/load-balancer-service-with-metallb/)