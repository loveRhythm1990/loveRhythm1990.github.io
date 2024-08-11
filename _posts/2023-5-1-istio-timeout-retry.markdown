---
layout:     post
title:      "通过 Istio 配置应用超时和重试"
date:       2023-5-1 10:10:00
author:     "decent"
header-img-credit: false
tags:
    - Istio
---

**目录**
- [部署安装](#部署安装)
  - [部署 metallb](#部署-metallb)
  - [部署 istio 和 bookinfo 示例应用](#部署-istio-和-bookinfo-示例应用)
    - [部署 bookinfo 服务](#部署-bookinfo-服务)
    - [通过 gateway 暴露 bookinfo 服务](#通过-gateway-暴露-bookinfo-服务)
  - [部署 Kiali 仪表盘](#部署-kiali-仪表盘)
  - [查看 bookinfo 服务流量](#查看-bookinfo-服务流量)
- [配置超时与重试](#配置超时与重试)
  - [超时](#超时)
  - [重试](#重试)
- [总结](#总结)


### 部署安装
先把 istio 安装一下，个人感觉 istio 门槛比较高的原因是需要配置部署一整套东西，而我又没有稳定的 K8s 环境，我之前有一个台式机，但是搬家的时候被搬家公司给顺走了（我现在想起来还伤心），然后也就一直没有再买。大多数情况下，在 mac 电脑上起一个 kind 环境能应付。
#### 部署 metallb
用于在内网环境下，给 loadbalancer 类型的 service 分配 ip。使用下面命令部署。
```s
kubectl apply -f https://raw.githubusercontent.com/metallb/metallb/v0.14.8/config/manifests/metallb-native.yaml
```
部署完之后，需要配置一个 ip 池，用于分配 ip，注意池子里的网段不能与 K8s 的现有网段重合。如果是用家庭局域网做测试，则可以将网段配置为家庭局域网的后 10 位，防止 ip 冲突。
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
> 注意，如果是通过 kind 部署的 K8s，仍然需要通过 port-forward 的方式来暴露服务，因为网段 192.168.10.0/24 跟我们的 mac 笔记本是不互通的。

#### 部署 istio 和 bookinfo 示例应用
安装 istio 参考文档 [istio 入门](https://istio.io/latest/zh/docs/setup/getting-started/)，使用 istioctl 二进制进行安装。
```s
curl -L https://istio.io/downloadIstio | sh -
cd istio-1.22.3
export PATH=$PWD/bin:$PATH
istioctl install --set profile=demo -y
```
##### 部署 bookinfo 服务
下面部署 bookinfo 示例应用，作为本次配置超时和重试的例子，示例应用也在下载下来的安装包中。首先给 default 命名空间打上 label，允许 istio 给我们的 pod 注入 sidecar 容器。
```s
kubectl label namespace default istio-injection=enabled
kubectl apply -f samples/bookinfo/platform/kube/bookinfo.yaml
```

##### 通过 gateway 暴露 bookinfo 服务
部署完成之后，需要将 bookinfo 示例暴露出去，并能在浏览器里访问。这里需要部署 istio 中的 gateway，通过 gateway 暴露服务原理跟通过 K8s ingress 暴露服务类似，在 ingress 中是需要将 ingress-controller 提供外放访问方式，在 istio 中，则需要将 istio controller 提供外网访问方式；具体来说，是 istio-system 命名空间下面有个 loadbalancer 类型的 service `istio-ingressgateway`，需要给这个 service 分配 external ip。因为 gateway 不是本文的重点，所以这里不过多解释，部署完能访问就可以。
```
kubectl apply -f samples/bookinfo/networking/bookinfo-gateway.yaml
```
因为我们是通过 kind 部署的 K8s 集群，所以仍然需要将 istio-controller 的 service port-forward 出去作为整个 K8s 集群的入口，因为下面这个 service 的 external ip `192.168.10.0` 是无法访问的。
```s
lr90@sj istio % kubectl get service -n istio-system istio-ingressgateway
NAME                   TYPE           CLUSTER-IP     EXTERNAL-IP    PORT(S)                                                                      AGE
istio-ingressgateway   LoadBalancer   10.96.143.83   192.168.10.0   15021:30107/TCP,80:32203/TCP,443:30801/TCP,31400:31759/TCP,15443:30890/TCP   127m

lr90@sj istio % kubectl port-forward service/istio-ingressgateway -n istio-system 8080:80
Forwarding from 127.0.0.1:8080 -> 8080
Forwarding from [::1]:8080 -> 8080
```

配置好之后，通过访问 [http://127.0.0.1:8080/productpage](http://127.0.0.1:8080/productpage) 就可以访问 bookinfo 示例了。
![java-javascript](/pics/bookinfo-sample.png)

#### 部署 Kiali 仪表盘
通过下面命令安装 Kiali、Prometheus、Grafana，所有的组件均部署在 istio-system 命名空间。
```s
$ kubectl apply -f samples/addons
$ kubectl rollout status deployment/kiali -n istio-system
```
启动仪表板，通过 istioctl 命令可以启动仪表板。
```s
lr90@sj istio-1.22.3 % istioctl dashboard kiali

http://localhost:20001/kiali
```

#### 查看 bookinfo 服务流量
我们可以通过 kiali 查看 bookinfo 服务的构成，从下图中可以看出，流量从 istio-system 下面的 ingressgateway 进入，流向 productpage 服务，productpage 服务有两个上游，并且 reviews 服务有三个版本 v1/v2/v3，而 reviews 服务的上游为 ratings。 
![java-javascript](/pics/kiali-sample.png)

### 配置超时与重试
我们已经部署 bookinfo 服务，下面的例子中，通过 bookinfo 服务的 review 服务来验证学习超时与重试的配置。在测试之前，我们先看一下经过上述步骤创建的 virtualservice。

下面的 virtualservice 中，通过 `gateways: [bookinfo-gateway]` 引用了网关服务，如果进入网关的流量能匹配下面的 match 则通过此 virtualservice 配置的转发规则进行转发。match 是通过 uri 进行匹配的，也可以通过 header 等匹配。

同时，将流量都转发到了后端服务 productpage，这里配置的就是 K8s 的 service。此时集群还没有定义 destinationrule，流量是通过 K8s service 转发的。

```yaml
apiVersion: networking.istio.io/v1
kind: VirtualService
metadata:
  name: bookinfo
  namespace: default
spec:
  gateways:
  - bookinfo-gateway
  hosts:
  - '*'
  http:
  - match:
    - uri:
        exact: /productpage
    - uri:
        prefix: /static
    - uri:
        exact: /login
    - uri:
        exact: /logout
    - uri:
        prefix: /api/v1/products
    route:
    - destination:
        host: productpage # K8s 中的 service
        port:
          number: 9080
```

#### 超时
[超时](https://istio.io/latest/zh/docs/concepts/traffic-management/#timeouts)是 envoy 代理等待来自特定服务 response 的时间阈值，以确保服务调用者不会因为等待答复而无限期的挂起，并在可预测的时间范围内调用成功或失败。

http 请求超时可以通过路由规则中的 timeout 字段来指定。默认情况下，请求之间是没有配置超时的。为了方便观察，首先配置只把流量打到 review 的 v2 版本，这个是通过在 destination 中指定 subset 做到的，其中 subset 是在 reviews destinationrule 中定义的，不同版本的 review 各作为一个 subset。
```yaml
apiVersion: networking.istio.io/v1alpha3
kind: VirtualService
metadata:
  name: reviews
spec:
  hosts:
    - reviews
  http:
  - route:
    - destination:
        host: reviews
        subset: v2
```
创建完上面配置之后，我们刷新 bookinfo 页面，则只会显示 v2 版本的 review（黑色的星星）。接下来，我们通过 istio 提供的故障注入能力给 ratings 服务增加两秒的延时（其他服务在访问 ratings 服务时，都有两秒的延迟）。配置完成后，再刷新界面，明显会有两秒的延时。
```yaml
apiVersion: networking.istio.io/v1alpha3
kind: VirtualService
metadata:
  name: ratings
spec:
  hosts:
  - ratings
  http:
  - fault:
      delay:
        percentage:
          value: 100
        fixedDelay: 2s
    route:
    - destination:
        host: ratings
        subset: v1
```
上面配置了调用 ratings 服务时，会有固定两秒的延时。下面再配置访问 reviews 服务的超时时间为 0.5s，配置完成后，再刷新页面时将很快返回，但是 reviews 服务是不可用。因为 reviews 服务要调用 ratings 服务，并且后者要多于 2s 才能返回。页面上显示的错误为：**Sorry, product reviews are currently unavailable for this book.**。
```yaml
apiVersion: networking.istio.io/v1alpha3
kind: VirtualService
metadata:
  name: reviews
spec:
  hosts:
  - reviews
  http:
  - route:
    - destination:
        host: reviews
        subset: v2
    timeout: 0.5s
```

#### 重试
在上面超时的配置中，在故障注入是，我们配置了 100% 的 ratings 请求都有两秒的延迟。然后界面上总是失败的，提示我们 reviews 服务不可用。下面我们配置有 50% 的请求有 2s 延迟，然后配置 reviews 进行重试，并且配置重试次数为 2 次，这样界面基本是可用的，（但是仍存在不可用的情况）。首先将 ratings virtualservice 的故障比例为 50%。
```yaml
apiVersion: networking.istio.io/v1alpha3
kind: VirtualService
metadata:
  name: ratings
spec:
  hosts:
  - ratings
  http:
  - fault:
      delay:
        percentage:
          value: 50
        fixedDelay: 2s
    route:
    - destination:
        host: ratings
        subset: v1
```
如果仅仅配置故障比例为 50%，则 reviews 大概有一半概率是可用的，我们再配置重试 2 次， 那么此时 review 服务不可用的概率为 50% * 50%，为 0.25。那么就是基本可用了。注意下面配置中，我们配置了 preTryTimeout 为 1s，如果 ratings 服务超过了 2s，仍然是失败的。
```yaml
apiVersion: networking.istio.io/v1
kind: VirtualService
metadata:
  name: reviews
  namespace: default
spec:
  hosts:
  - reviews
  http:
  - retries:
      attempts: 2
      perTryTimeout: 1s
    route:
    - destination:
        host: reviews
        subset: v2
```
配置完成后，我们发现失败的概率果然小了很多，但是仍然存在失败的情况。

### 总结
本文学习了 virtualserivce 的超时和重试配置，但也涉及到 istio 中的其他概念，比如 gateway、故障注入、destinationrule 等。我期望通过多实践的方式来学习 istio，如果只是看文档或者书籍，则很容易被一堆概念和配置绕晕，而写写博客也是我学习的动力。
