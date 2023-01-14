---
layout:     post
title:      "Ingress-nginx 原理、应用及灰度发布"
date:       2022-3-6 10:10:00
author:     "decent"
header-img-credit: false
tags:
  - K8s
---

这几天一直在排查一个 websocket 连接不断断开的问题，场景大概是：K8s 集群外面的浏览器访问集群内部的服务，集群外 client 访问集群内内部服务是通过 ingress-nginx 来实现的，其部署方式是 hostnetwork，也就是直接以宿主机服务的方式部署在 master 节点的，监听的是 80 以及 443 端口。我们的现象是我们的前端 websocket 连接不断断开，排查的原因是因为我们配置的 ingress-nginx 没有配置网络超时时间，默认是 60s，也就是说，如果 websocket 60s 不发送数据或者 60s 不接收数据，连接就有可能断开了。因为之前没怎么关注过 ingress 这个资源，本文总结下其相关使用。文末还有几个问题，有时间继续补充下。

### K8s 中的 ingress 资源
K8s 中介绍 ingress 的文档是 [https://kubernetes.io/docs/concepts/services-networking/ingress/](https://kubernetes.io/docs/concepts/services-networking/ingress/)，根据文档，ingress 的主要功能就是将集群外的流量转发到集群内部的 service，具体怎么转发是根据 ingress 中定义的转发规则来实现的。

![java-javascript](/img/in-post/all-in-one/2022-03-06-11-08-06.png){:height="70%" width="70%"}

ingress 最核心的功能是将集群外的流量转发到集群内部，除此之外，还有如下功能（特点）：
* give Services externally-reachable URLs：给 K8s 集群内部的 service 一个外部可以访问的 URL.
* load balance traffic：流量的负载均衡
* terminate SSL / TLS：TLS 流量解码
* offer name-based virtual hosting：基于虚拟主机名的路由。

ingress 资源，只是定义规则，流量转发都是由 ingress controller 来实现的，比如 [ingress-nginx](https://kubernetes.github.io/ingress-nginx/)，所以创建 ingress 的时候，一定要有 ingress controller，这点跟 storageclass 有点类似，storageclass 后面要有存储插件的支持。

另外有个问题是，外部流量怎么访问 ingress controller 呢？其实 ingress controller 并没有就服务访问做额外的工作，还是依赖 K8s 提供的机制，比如暴露 ingress controller 的时候用 NodePort Service，或者 host network（目前这种方式用的多一点），或者在公有云环境下用 external-ip。客户端的流量其实访问的是 ignress controller，后者根据 K8s ingress 资源的配置来转发流量。其也可以监听 ingress 资源的变化，来动态调整配置。 

一个典型的 ingress 资源如下：
```yaml
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: ingress-test
spec:
  rules:
  - host: "foo.bar.com"
    http:
      paths:
      - pathType: Prefix
        path: "/bar"
        backend:
          service:
            name: service1
            port:
              number: 80
```
在上面的 ingress 中，定义了一个虚拟主机域名 `foo.bar.com`，所有以 `foo.bar.com/bar` 为前缀的请求都会转发到后端的 service `service1`，这个 `service1` 要跟这个 ingress 在同一个 namespace下面。上面定义的 `pathType` 为 `Prefix`，此外还有 `Exact` 类型。

因为是虚拟主机域名，所以我们可以定义多个虚拟主机域名，这里一种实现方式是：配置 ingress controller 为 hostnetwork 方式，假设部署在 192.168.0.1 这个物理机上。在 client 端给这个地址（即192.168.0.1）配资多个域名，即修改 /etc/hosts 文件，同一个 ip 地址对应多个域名，这样，客户端在使用不同的域名访问时就能根据域名（也就是虚拟主机）来进行流量转发了。

### K8s 维护的 controller: ingress-nginx
K8s 自己维护的 ingress controller 是 ingress-nginx，其文档为[ingress-nginx](https://kubernetes.github.io/ingress-nginx/)，在其 github 中有很多文档可以读一下：[ingress-nginx/docs/user-guide/](https://github.com/kubernetes/ingress-nginx/tree/main/docs/user-guide)，其部署文档为：[Installation Guide](https://github.com/kubernetes/ingress-nginx/blob/main/docs/deploy/index.md)。


### Nginx 代理 websocket。
根据 K8s 的文档，[miscellaneous/#websockets](https://kubernetes.github.io/ingress-nginx/user-guide/miscellaneous/#websockets)，ingress nginx 对 websocket 的支持是开箱即用的，不需要做额外配置，只需要配置两个参数就可以了，防止连接断开，这两个参数的默认时间都是 60s。
* proxy-read-timeout：连续没读到数据的超时时间，单位为秒，如果在指定的时间内没读到数据，则断开连接。
* proxy-send-timeout：连续没写入数据的超时时间，同上。

另外这里[Using websockets with the Nginx Kubernetes ingress controller](https://www.civo.com/learn/using-websockets-with-ingress-controller) 配置一个 nginx 代理 websocket 的典型配置，可以参考一下。
```yaml
apiVersion: extensions/v1beta1
kind: Ingress
metadata:
 name: tornado-socket
 annotations:
  kubernetes.io/ingress.class: nginx
  nginx.ingress.kubernetes.io/proxy-read-timeout: "3600"
  nginx.ingress.kubernetes.io/proxy-send-timeout: "3600"
  nginx.ingress.kubernetes.io/server-snippets: |
   location / {
    proxy_set_header Upgrade $http_upgrade;
    proxy_http_version 1.1;
    proxy_set_header X-Forwarded-Host $http_host;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header X-Forwarded-For $remote_addr;
    proxy_set_header Host $host;
    proxy_set_header Connection "upgrade";
    proxy_cache_bypass $http_upgrade;
    }
spec:
 rules:
  - host: tornado-ws.example.com
   http:
    paths:
     - backend:
       serviceName: tornado-socket
       servicePort: 8000
```

如果直接使用 nginx 来代理 websocket，可以参考[nginx反向代理WebSocket](https://www.xncoding.com/2018/03/12/fullstack/nginx-websocket.html)，最主要的是让 http 连接升级为 websocket 连接
```s
Upgrade: websocket
Connection: Upgrade
```
完整的 nginx 代理 websocket 配置如下 `websocket.conf`：
```s
map $http_upgrade $connection_upgrade {
    default upgrade;
    '' close;
}

upstream websocket {
    server localhost:8282; # appserver_ip:ws_port
}

server {
     server_name test.enzhico.net;
     listen 443 ssl;
     location / {
         proxy_pass http://websocket;
         proxy_read_timeout 300s;
         proxy_send_timeout 300s;
         
         proxy_set_header Host $host;
         proxy_set_header X-Real-IP $remote_addr;
         proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
         
         proxy_http_version 1.1;
         proxy_set_header Upgrade $http_upgrade;
         proxy_set_header Connection $connection_upgrade;
     }
    ssl_certificate /etc/letsencrypt/live/test.enzhico.net/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/test.enzhico.net/privkey.pem;
}
```
上面配置中包含 ssl 证书，client 跟 nginx 是通过 `wss://` 通信的（443 端口），nginx 跟 backend server 是通过 `ws://` 通信的（localhost:8282）。

### 金丝雀发布
金丝雀发布也叫 `canary` 发布，[从概念、部署到优化，Kubernetes Ingress 网关的落地实践](https://mp.weixin.qq.com/s/SzKrpsKiL60_TIjtuy7BIQ) 这篇阿里的文章中介绍了通过 Ingress 来实现恢复发布的功能；[示例十二 - 使用 Ingress-Nginx 进行灰度发布](https://v2-1.docs.kubesphere.io/docs/zh-CN/quick-start/ingress-canary/) KubeSphere 在这篇文章中进行了示例。

总体原理如下图，控制灰度的那个 Ingress 需要加上额外的 annotation，**同时发过来的请求，比如带有特定的 header**。比如在 KubeSphere 的示例中，通过 curl 添加了 header:
```s
$ for i in $(seq 1 10); do curl -s -H "canary: other-value" --resolve kubesphere.io:30205:192.168.0.88 kubesphere.io:30205 | grep "Hostname"; done
```
![](/img/in-post/all-in-one/2022-04-30-15-16-25.png){:height="60%" width="60%"}

Ingress-nginx 支持的灰度有两种策略，一种是根据 header，一种是按比例进行灰度（这种按比例将请求分到不同版本的行为也称为蓝绿发布，即即存在"蓝"版本，也存在"绿"版本），分别由 annotation `nginx.ingress.kubernetes.io/canary-by-header` 以及 `nginx.ingress.kubernetes.io/canary-weight` 控制。关于 annotation 的详细叙述，可以查看 ingress-nginx 的文档：[https://kubernetes.github.io/ingress-nginx/user-guide/nginx-configuration/annotations/#canary](https://kubernetes.github.io/ingress-nginx/user-guide/nginx-configuration/annotations/#canary)

### 遗留问题
1. ingress 中的负载均衡是怎么做的？
	
	在看一个 ingress 资源详细信息的时候，在 status 中如下信息，这三个 ip 是 ingress-nginx 三个 pod 的 IP，这个负载均衡是怎么做的？
	```yaml
    status:
      loadBalancer:
        ingress:
        - ip: 10.72.220.155
        - ip: 10.73.245.148
        - ip: 10.73.246.123
	```

2. 在支持 `LoadBalancer` service 的 K8s 集群中，需要给 ingress controller 分配一个 external IP 或者一个 FQDN，参考 [ingress-nginx-online testing](https://github.com/kubernetes/ingress-nginx/blob/main/docs/deploy/index.md#online-testing)，这个具体是怎么工作的？

### 参考
[k8s ingress原理及ingress-nginx部署测试](https://segmentfault.com/a/1190000019908991)