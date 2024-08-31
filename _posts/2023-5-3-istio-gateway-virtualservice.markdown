---
layout:     post
title:      "通过 Istio gateway 将暴露服务到集群外"
date:       2023-5-3 10:10:00
author:     "decent"
header-img-credit: false
tags:
    - Istio
---

**目录**
- [概述](#概述)
- [环境准备](#环境准备)
- [部署 gateway 资源](#部署-gateway-资源)
- [配置 virtualservice 资源](#配置-virtualservice-资源)
- [测试访问](#测试访问)
- [其他路由转发配置](#其他路由转发配置)
  - [virtualservice 转发流量到其他 namespace](#virtualservice-转发流量到其他-namespace)
  - [定义目标规则 destinationrule](#定义目标规则-destinationrule)


### 概述
Istio gateway 功能跟 K8s ingress 类似，都是将集群内的服务暴露到集群外卖。与 ingress 不同的是，gateway 只提供接入点，路由规则通用 virtualservice 资源配置。

本文通过一个简单的 [go httpserver](https://github.com/loveRhythm1990/simple-go-server) 来做测试，该服务对访问 url 返回 hello world。httpserver 部署在 default 命名空间下面，并通过 go-server service 的 8000 端口暴露服务。
```s
lr90@sj istio % curl http://127.0.0.1:8000/hello
hello world%
```

istio 官方文档的地址为 [https://istio.io/latest/docs/reference/config/networking/gateway/](https://istio.io/latest/docs/reference/config/networking/gateway)

### 环境准备
环境准备工作包括安装 istio，部署 go httpserver 服务，以及给 default namespace 打上 label，允许 istio 注入 sidecar，注意**给命名空间打 label 要先于在命名空间部署应用**。环境部署工作这里不再赘述，可以参考《[通过 Istio 配置应用超时和重试](https://loverhythm1990.github.io/2023/05/01/istio-timeout-retry/)》。
```s
kubectl label namespace default istio-injection=enabled
```

### 部署 gateway 资源
接下来为我们的 httpserver 服务生成 gateway 资源。参考 istio 文档 [入口网关](https://istio.io/latest/zh/docs/tasks/traffic-management/ingress/ingress-control/)，我们将服务注册到虚拟主机 `httpserver.lr90.site` 上。

在编写 gateway 配置之前，我们需要先注意一下，istio 提供公网访问的 istio-ingressgateway service。这个 service 的类型是 LoadBalancer，下面 EXTERNAL-IP 是 pending，不过不要紧我们会通过 port-forward 的方式把这个服务暴露出去；另外有 `80` 端口用于暴露 http 服务，`443` 端口用于暴露 https 服务。我们在 gateway 资源中会用到这个端口。
```s
lr90@sj httpserver % kubectl get service istio-ingressgateway -n istio-system
NAME                   TYPE           CLUSTER-IP     EXTERNAL-IP   PORT(S)                                                                      AGE
istio-ingressgateway   LoadBalancer   10.96.241.23   <pending>     15021:30662/TCP,80:32314/TCP,443:32155/TCP,31400:30283/TCP,15443:32478/TCP   60m
```

在 gateway 配置中 `istio: ingressgateway` 是固定的，表示用 istio 默认的 gateway controller，这个 selector 选中的是 istio-system 命名空间下的 istio-ingressgateway pod。
servers[0] 中 port 的配置是指 istio gateway service 的端口。

```yaml
apiVersion: networking.istio.io/v1
kind: Gateway
metadata:
  name: httpserver-gateway
spec:
  selector:
    istio: ingressgateway # use Istio default gateway implementation
  servers:
  - port:
      number: 80
      name: http
      protocol: HTTP
    hosts:
    - "httpserver.lr90.site"
```

### 配置 virtualservice 资源
通过 gateway 定义流量接入点之后，需要在 [virtualservice](https://istio.io/latest/docs/reference/config/networking/virtual-service/) 中定义转发规则。在 istio 中 virtualservice 主要用来**定义路由规则集合**。

接下来看下其配置，首先我们通过 gateways 字段声明要转发某一个（或多个） gateway 的流量。
spec.hosts 配置该 virtualservice 适用的 K8s service 或者主机名，我们配置为 gateway 中定义的虚拟主机。HTTPRouteDestination 中的 host 定义流量转发的后端 K8s service。

在下面配置中，我们只定义了 uri `/hello`，访问其他 uri 将返回错误。

```yaml
apiVersion: networking.istio.io/v1
kind: VirtualService
metadata:
  name: httpserver
spec:
  hosts:
  - "httpserver.lr90.site"
  gateways:
  - httpserver-gateway
  http:
  - match:
    - uri:
        prefix: /hello
    route:
    - destination:
        port:
          number: 8000
        host: go-server
```

### 测试访问
因为我们用 kind 部署的 K8s 环境，没办法给 loadbalancer 类型的 service 分配集群外可访问的 ip，所以我们先将 istio 的服务通过 port-forward 的方式暴露出来，将 service 的 80 端口映射到本地的 8080 端口。
```s
lr90@sj ~ % kubectl port-forward service/istio-ingressgateway -n istio-system 8080:80
Forwarding from 127.0.0.1:8080 -> 8080
Forwarding from [::1]:8080 -> 8080
```
我们映射的是 80 端口，上面显示的是 `8080->8080` 这个原因应该是 service 的 80 端口实际上代理的是 pod 的 8080 端口。我们通过 curl 命令访问显示服务是正常的。

```s
lr90@sj ~ % curl -s -HHost:httpserver.lr90.site "http://127.0.0.1:8080/hello"
hello world%

# 访问其他主机，显示 Not Found
lr90@sj ~ % curl -s -I -HHost:httpserver.lr90.com "http://127.0.0.1:8080/hello"
HTTP/1.1 404 Not Found
date: Sun, 18 Aug 2024 13:52:00 GMT
server: istio-envoy
transfer-encoding: chunked

# 访问其他 uri，同样显示 Not Found
lr90@sj ~ % curl -s -I -HHost:httpserver.lr90.site "http://127.0.0.1:8080/abc"
HTTP/1.1 404 Not Found
date: Sun, 18 Aug 2024 13:56:05 GMT
server: istio-envoy
transfer-encoding: chunked
```

### 其他路由转发配置
通过上面的配置，我们已经实现了 gateway 转发流量到集群内部的 service。istio 还有其他一些细节这里一起验证测试一下。

#### virtualservice 转发流量到其他 namespace
上面测试中 virtualservice 和 httpserver 都部署到了 default 命名空间下面，在转发到其他命名空间的服务时，需要使用 FQDN（推荐在 istio 中一直用 FQDN）。假设 httpserver 部署到了 test 命名空间，同时需要给 test 命名空间打上 `istio-injection=enabled` 标签，virtualservice 中的 host 需要修改为 `go-server.test.svc.cluster.local`。
```yaml
apiVersion: networking.istio.io/v1
kind: VirtualService
metadata:
  name: httpserver
  namespace: default
spec:
  gateways:
  - httpserver-gateway
  hosts:
  - httpserver.lr90.site
  http:
  - match:
    - uri:
        prefix: /hello
    route:
    - destination:
        host: go-server.test.svc.cluster.local
        port:
          number: 8000
```
测试访问成功：
```s
lr90@sj ~ % curl -s  -HHost:httpserver.lr90.site "http://127.0.0.1:8080/hello"
hello world%
```
#### 定义目标规则 destinationrule
在上面定义中，我们通过 virtualservice 将流量之间转发到了后端 service，如果我们想定义一些**流量策略**或者**对不同服务的版本**做一下细分，则要定义 [destinationrule](https://istio.io/latest/docs/reference/config/networking/destination-rule/)。

参考《[Istio学习笔记——DestinationRule解析](https://juejin.cn/post/7353280369382195226)》中的例子，在集群中部署 httpd 以及 nginx 作为应用的两个版本（其实是两个不同的应用 doge）。并且在 destinationrule 中分别为着两个版本定义不同的子集，并未 httpd 定义权重 90%，nginx 定义权重 10%。

```yaml
# nginx deploymen
apiVersion: apps/v1
kind: Deployment
metadata:
  name: nginx
  namespace: test-istio
spec:
  selector:
    matchLabels:
      app: nginx
      server: web
  replicas: 1
  template:
    metadata:
      labels:
        app: nginx
        server: web
    spec:
      containers:
      - name: nginx
        image: nginx:latest
        ports:
        - containerPort: 80
---
# httpd deployment
apiVersion: apps/v1
kind: Deployment
metadata:
  labels:
    app: httpd
  name: httpd
  namespace: test-istio
spec:
  replicas: 1
  selector:
    matchLabels:
      app: httpd
  strategy:
    rollingUpdate:
      maxSurge: 25%
      maxUnavailable: 25%
    type: RollingUpdate
  template:
    metadata:
      labels:
        app: httpd
        server: web
    spec:
      containers:
      - image: httpd:latest
        name: httpd
---
# service
apiVersion: v1
kind: Service
metadata:
  name: web-svc
  namespace: test-istio
spec:
  ports:
  - name: port
    port: 80
    protocol: TCP
    targetPort: 80
  selector:
    server: web
---
# client 端
apiVersion: apps/v1
kind: Deployment
metadata:
  name: client
  namespace: test-istio
spec:
  selector:
    matchLabels:
      app: client-curl
  replicas: 1
  template:
    metadata:
      labels:
        app: client-curl
    spec:
      containers:
      - name: curl
        image: curlimages/curl:latest
        command:
        - sleep
        - "36000"
```

通过 destinationrule 划分子集与权重，如果只是划分子集与权重，只需要配置 host 以及 subsets 字段就好了，其中 host 配置 service 的名字，因为在同一个命名空间下，配置 web-svc 就可以了。subsets 也只需要配置 name 与 labels。其中 virtualservice 也只需要配置 hosts 与 http 路由规则就好了。
```yaml
# DestinationRule
apiVersion: networking.istio.io/v1alpha3
kind: DestinationRule
metadata:
  name: test-dr
  namespace: test-istio
spec:
  host: web-svc
  subsets:
  - name: httpd   # httpd 子集
    labels:
      app: httpd
  - name: nginx   # nginx 子集
    labels:
      app: nginx
---
# virtualService
apiVersion: networking.istio.io/v1alpha3
kind: VirtualService
metadata:
  name: web-vs
  namespace: test-istio
spec:
  hosts:
  - web-svc
  http:
  - route:
    - destination:
        host: web-svc
        subset: nginx
      weight: 10
    - destination:
        host: web-svc
        subset: httpd
      weight: 90
```

配置完成后，通过在 client pod 访问 service，就可以看到到 httpd 的流量大概是 90%，而到 nginx 的流量大概是 10%。
```s
lr90@sj juejin % kubectl exec -it client-78cc96c576-zvcb2 -n test-istio /bin/
sh
kubectl exec [POD] [COMMAND] is DEPRECATED and will be removed in a future version. Use kubectl exec [POD] -- [COMMAND] instead.
~ $ curl web-svc
<html><body><h1>It works!</h1></body></html>
~ $ curl web-svc
<html><body><h1>It works!</h1></body></html>
~ $ curl web-svc
<html><body><h1>It works!</h1></body></html>
~ $ curl web-svc
<html><body><h1>It works!</h1></body></html>
~ $ curl web-svc
<html><body><h1>It works!</h1></body></html>
~ $ curl web-svc
<html><body><h1>It works!</h1></body></html>
~ $ curl web-svc
<html><body><h1>It works!</h1></body></html>
~ $ curl web-svc
<html><body><h1>It works!</h1></body></html>
~ $ curl web-svc
<html><body><h1>It works!</h1></body></html>
~ $ curl web-svc
<html><body><h1>It works!</h1></body></html>
~ $ curl web-svc
<html><body><h1>It works!</h1></body></html>
~ $ curl web-svc
<!DOCTYPE html>
<html>
<head>
<title>Welcome to nginx!</title>
<style>
html { color-scheme: light dark; }
body { width: 35em; margin: 0 auto;
font-family: Tahoma, Verdana, Arial, sans-serif; }
</style>
</head>
<body>
<h1>Welcome to nginx!</h1>
<p>If you see this page, the nginx web server is successfully installed and
working. Further configuration is required.</p>

<p>For online documentation and support please refer to
<a href="http://nginx.org/">nginx.org</a>.<br/>
Commercial support is available at
<a href="http://nginx.com/">nginx.com</a>.</p>
```