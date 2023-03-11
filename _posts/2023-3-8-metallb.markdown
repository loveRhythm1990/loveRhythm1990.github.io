---
layout:     post
title:      "使用 metallb 为 loadbalance 类型的服务分配 ip"
date:       2023-3-8 10:10:00
author:     "decent"
header-img-credit: false
tags:
    - Istio
---

- [安装](#安装)
- [配置 ip 池](#配置-ip-池)
- [配置服务，并测试](#配置服务并测试)
  - [集群内访问](#集群内访问)
  - [集群外访问](#集群外访问)
- [参考](#参考)

在部署测试 Istio 时，发现依赖 LoadBalance 类型的 Service。LoadBalance 类型的 Service 一般由公有云厂商分配`EXTERNAL-IP`，从而在集群外访问集群内的服务，如果是我们自己的测试集群，又依赖 LoadBalance 类型的服务，那怎么办呢？[metallb](https://github.com/metallb/metallb) 提供了一种解决方案，能够为集群内的服务分配 external-ip，并可以从集群外访问。其工作原理跟 [KeepAlived](https://loverhythm1990.github.io/2023/02/03/keepalived/) 非常类似，（[官方文档](https://metallb.universe.tf/concepts/layer2/#comparison-to-keepalived)中有解释其跟 keepalived 的区别），都是生成一个 vip，并响应这个 vip arp 请求。

本文记录一下 metallb layer2 模式的使用，关于 metallb 的原理性内容，后面再整理下。
> 当我使用最新的 v0.13.9 版本安装 metallb 时，参考[官方文档](https://metallb.universe.tf/installation/)，分配的 externalip 不能够从集群外访问，集群内时可以的，查了半天不知所以然，所以这里参考[在 Kubernetes 集群中使用 MetalLB 作为 LoadBalancer（上）- Layer2](https://atbug.com/load-balancer-service-with-metallb/) 使用 `v0.12.1` 版本进行配置。

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

### 参考
[在 Kubernetes 集群中使用 MetalLB 作为 LoadBalancer（上）- Layer2](https://atbug.com/load-balancer-service-with-metallb/)