---
layout:     post
title:      "K8s Service 中的 iptables 规则"
date:       2022-3-26 10:10:00
author:     "decent"
header-img-credit: false
tags:
  - 网络
---

补一下网络基础知识，研究下在 K8s 集群中，不同类型的 Service 生成的 Iptables 规则是什么样的。集群环境如下
```s
[decent@master1 ~]$ kubectl get nodes -o wide
NAME      STATUS   ROLES                      AGE     VERSION   INTERNAL-IP     EXTERNAL-IP   OS-IMAGE                KERNEL-VERSION           CONTAINER-RUNTIME
master1   Ready    controlplane,etcd,worker   5h12m   v1.20.5   192.168.0.201   <none>        CentOS Linux 7 (Core)   3.10.0-1160.el7.x86_64   docker://20.10.13
master2   Ready    controlplane,etcd,worker   5h12m   v1.20.5   192.168.0.202   <none>        CentOS Linux 7 (Core)   3.10.0-1160.el7.x86_64   docker://20.10.13
master3   Ready    controlplane,etcd,worker   5h12m   v1.20.5   192.168.0.203   <none>        CentOS Linux 7 (Core)   3.10.0-1160.el7.x86_64   docker://20.10.13
node1     Ready    worker                     5h12m   v1.20.5   192.168.0.204   <none>        CentOS Linux 7 (Core)   3.10.0-1160.el7.x86_64   docker://20.10.13
node2     Ready    worker                     5h12m   v1.20.5   192.168.0.205   <none>        CentOS Linux 7 (Core)   3.10.0-1160.el7.x86_64   docker://20.10.13
```
为了测试 Service，我们使用后端实例是一个 Hello world 服务，定义如下，curl 一下这个服务，就能返回 hello world。
```yml
kind: ReplicaSet
metadata:
  name: hello-world
  labels:
    app: hello-world
spec:
  selector:
    matchLabels:
      app: hello-world
  replicas: 2
  template:
    metadata:
      labels:
        app: hello-world
    spec:
      containers:
        - name: hello-world
          image: jtblin/node-hello:latest
          imagePullPolicy: IfNotPresent
          ports:
            - containerPort: 8080
              protocol: TCP
```
一共有两个实例，分别部署在 master1、master2 节点上，其 IP 分别为 10.42.0.3、10.42.2.3。
```s
[decent@master1 resources]$ kubectl get pods -o wide
NAME                READY   STATUS    RESTARTS   AGE     IP          NODE      NOMINATED NODE   READINESS GATES
hello-world-c92mq   1/1     Running   0          3h10m   10.42.0.3   master2   <none>           <none>
hello-world-zkgfm   1/1     Running   0          3h10m   10.42.2.3   master1   <none>           <none>
```

### NodePort Service
首先看一下最常见的 Cluster IP Service，定义如下。
```yml
apiVersion: v1
kind: Service
metadata:
  name: hello-world
spec:
  type: NodePort
  selector:
    app: hello-world
  ports:
    - port: 80
      targetPort: 8080
      protocol: TCP
```
测试一下对这个服务的访问，发现都是没问题的，注意最后面的两个 curl 命令，一个是访问 clusterIP，一个是访问 pod IP，都是可以的。
```s
[decent@master1 resources]$ kubectl get service hello-world
NAME          TYPE       CLUSTER-IP     EXTERNAL-IP   PORT(S)        AGE
hello-world   NodePort   10.43.99.121   <none>        80:30610/TCP   15s
[decent@master1 resources]$ kubectl get endpoints hello-world
NAME          ENDPOINTS                       AGE
hello-world   10.42.0.4:8080,10.42.2.4:8080   51s
```
通过 curl 命令测试，在集群中，可以通过下面三种方式访问服务。
```s
# 访问 clusterIP
[decent@master1 resources]$ curl 10.43.99.121:80
Hello World
# 访问 Node IP
[decent@master1 resources]$ curl 192.168.0.204:30610
Hello World
# 访问 pod IP
[decent@master1 resources]$ curl 10.42.0.4:8080    
Hello World
```
对于 NodePort 类型的 Service，kube-proxy 会在每个节点上把对应的端口开启，可以使用 `lsof -i:30610` 命令验证如下。[Kubernetes Services and Iptables](https://msazure.club/kubernetes-services-and-iptables/)
中对此的解释为：占据这个端口，防止其他应用使用这个端口。
```s
[ha@VM-16-29-centos ~]$ sudo lsof -i:30610
COMMAND    PID USER   FD   TYPE    DEVICE SIZE/OFF NODE NAME
kube-prox 3571 root   11u  IPv6 534804567      0t0  TCP *:30610 (LISTEN)
```
另外，kube-proxy 代码中关于这个注释也挺有意思（根据注释的意思，应该是用 `bind()` 调用就够了，但是 `bind()` 调用时，使用 `ss` 以及 `netstat` 命令看不到端口信息）：
> Hold the actual port open, even though we use iptables to redirect it.  This ensures that a) it's safe to take and b) that stays true. NOTE: We should not need to have a real listen()ing socket - bind() should be enough, but I can't figure out a way to e2e test without it.  Tools like 'ss' and 'netstat' do not show sockets that are bind()ed but not listen()ed, and at least the default debian netcat has no way to avoid about 10 seconds of retries.

我们在一个节点上执行 `iptables -S -t nat `命令查看该 service 生成的 iptables 规则，其中 `-S` 表示 `--list-rules `，即打印规则。
```s
[decent@master1 resources]$ sudo iptables -S -t nat
-A PREROUTING -m comment --comment "kubernetes service portals" -j KUBE-SERVICES
-A OUTPUT -m comment --comment "kubernetes service portals" -j KUBE-SERVICES
-A POSTROUTING -m comment --comment "kubernetes postrouting rules" -j KUBE-POSTROUTING
-A POSTROUTING -s 172.17.0.0/16 ! -o docker0 -j MASQUERADE
-A POSTROUTING -s 10.42.0.0/16 -d 10.42.0.0/16 -j RETURN
-A POSTROUTING -s 10.42.0.0/16 ! -d 224.0.0.0/4 -j MASQUERADE
-A POSTROUTING ! -s 10.42.0.0/16 -d 10.42.2.0/24 -j RETURN
-A POSTROUTING ! -s 10.42.0.0/16 -d 10.42.0.0/16 -j MASQUERADE
-A KUBE-MARK-DROP -j MARK --set-xmark 0x8000/0x8000
-A KUBE-MARK-MASQ -j MARK --set-xmark 0x4000/0x4000
-A KUBE-NODEPORTS -p tcp -m comment --comment "default/hello-world" -m tcp --dport 30610 -j KUBE-MARK-MASQ
-A KUBE-NODEPORTS -p tcp -m comment --comment "default/hello-world" -m tcp --dport 30610 -j KUBE-SVC-DZ6LTOHRG6HQWHYE
-A KUBE-POSTROUTING -m mark ! --mark 0x4000/0x4000 -j RETURN
-A KUBE-POSTROUTING -j MARK --set-xmark 0x4000/0x0
-A KUBE-POSTROUTING -m comment --comment "kubernetes service traffic requiring SNAT" -j MASQUERADE
-A KUBE-SEP-65EI7GDZIRLR2RCT -s 10.42.2.4/32 -m comment --comment "default/hello-world" -j KUBE-MARK-MASQ
-A KUBE-SEP-65EI7GDZIRLR2RCT -p tcp -m comment --comment "default/hello-world" -m tcp -j DNAT --to-destination 10.42.2.4:8080
-A KUBE-SEP-ZI6BFS3EG32KA2XC -s 10.42.0.4/32 -m comment --comment "default/hello-world" -j KUBE-MARK-MASQ
-A KUBE-SEP-ZI6BFS3EG32KA2XC -p tcp -m comment --comment "default/hello-world" -m tcp -j DNAT --to-destination 10.42.0.4:8080
-A KUBE-SERVICES ! -s 10.42.0.0/16 -d 10.43.99.121/32 -p tcp -m comment --comment "default/hello-world cluster IP" -m tcp --dport 80 -j KUBE-MARK-MASQ
-A KUBE-SERVICES -d 10.43.99.121/32 -p tcp -m comment --comment "default/hello-world cluster IP" -m tcp --dport 80 -j KUBE-SVC-DZ6LTOHRG6HQWHYE
-A KUBE-SERVICES -m comment --comment "kubernetes service nodeports; NOTE: this must be the last rule in this chain" -m addrtype --dst-type LOCAL -j KUBE-NODEPORTS
-A KUBE-SVC-DZ6LTOHRG6HQWHYE -m comment --comment "default/hello-world" -m statistic --mode random --probability 0.50000000000 -j KUBE-SEP-ZI6BFS3EG32KA2XC
-A KUBE-SVC-DZ6LTOHRG6HQWHYE -m comment --comment "default/hello-world" -j KUBE-SEP-65EI7GDZIRLR2RCT
```

首先看前两条，前两条将**所有的报文**都转发到 `KUBE-SERVICES` 链。并且这些 rule 有个注释 `kubernetes service portals`，其中 `-m comment` 表示使用 comment 扩展模块给 rule 添加注释，参考[Linux: Add Comment to IPTables Rule](https://stackpointer.io/unix/linux-add-comment-to-iptables-rule/641/).
```s
-A PREROUTING -m comment --comment "kubernetes service portals" -j KUBE-SERVICES
-A OUTPUT -m comment --comment "kubernetes service portals" -j KUBE-SERVICES
```
其中，PREROUTING 用来处理所有外面进入（inbound）的流量，OUTPUT 用来处理所有出去的流量（outbound）。

继续分析，通过节点的 `30610` 端口访问 NodePort，会进入以下链。【【 这里顺便补充一下 ClusterIP 类型的 Service 匹配机制，后者是在 `KUBE-SERVICES` 链中匹配的，具体规则为：`-A KUBE-SERVICES -d 192.168.0.1/32 -p tcp -m comment --comment "default/kubernetes:https cluster IP" -m tcp --dport 443 -j KUBE-SVC-NPX46M4PTMTKRN6Y`，也就是通过 clusterIP（这里为192.168.0.1），以及 Port 443 匹配的，其他规则跟 NodePort 是一致的】】
```s
-A KUBE-NODEPORTS -p tcp -m comment --comment "default/hello-world" -m tcp --dport 30610 -j KUBE-MARK-MASQ
-A KUBE-NODEPORTS -p tcp -m comment --comment "default/hello-world" -m tcp --dport 30610 -j KUBE-SVC-DZ6LTOHRG6HQWHYE
```
kube-proxy 针对 NodePort 流量入口专门创建了 KUBE-NODEPORTS 链。在我们这个例子中，KUBE-NODEPORTS 进一步跳转到 KUBE-SVC-DZ6LTOHRG6HQWHYE 链。
```s
-A KUBE-SVC-DZ6LTOHRG6HQWHYE -m comment --comment "default/hello-world" -m statistic --mode random --probability 0.50000000000 -j KUBE-SEP-ZI6BFS3EG32KA2XC
-A KUBE-SVC-DZ6LTOHRG6HQWHYE -m comment --comment "default/hello-world" -j KUBE-SEP-65EI7GDZIRLR2RCT
```
这里利用了 iptables 的 random 模块，使连接有 50% 的概率进入 KUBE-SEP-ZI6BFS3EG32KA2XC 链，50% 的概率进入 KUBE-SEP-65EI7GDZIRLR2RCT 链。因此，Kube-proxy 的 iptables 模式采用随机数实现了服务的负载均衡。关于这里的计算模式其实是使用了 `statistic` 扩展模块，具体可以参考 [https://ipset.netfilter.org/iptables-extensions.man.html](https://ipset.netfilter.org/iptables-extensions.man.html)

```s
This module matches packets based on some statistic condition. It supports two distinct modes settable with the --mode option.
Supported options:
--mode mode
Set the matching mode of the matching rule, supported modes are random and nth.
[!] --probability p
Set the probability for a packet to be randomly matched. It only works with the random mode. p must be within 0.0 and 1.0. The supported granularity is in 1/2147483648th increments.
[!] --every n
Match one packet every nth packet. It works only with the nth mode (see also the --packet option).
--packet p
Set the initial counter value (0 <= p <= n-1, default 0) for the nth mode.
```

KUBE-SEP-ZI6BFS3EG32KA2XC 链的具体作用就是将请求通过 DNAT 发送到 10.42.0.4 的 8080 端口，另一条链的作用一致，不再贴出。
```s
-A KUBE-SEP-ZI6BFS3EG32KA2XC -s 10.42.0.4/32 -m comment --comment "default/hello-world" -j KUBE-MARK-MASQ
-A KUBE-SEP-ZI6BFS3EG32KA2XC -p tcp -m comment --comment "default/hello-world" -m tcp -j DNAT --to-destination 10.42.0.4:8080
```

如果目的Pod IP在其他节点，则需要进行容器跨节点通信。注意，这种情形下，本节点相当于网关的角色，在将源数据包转发出去之前，需要进行SNAT，将源数据包的源IP地址，转换为网关（本节点）的IP地址，这样，数据包才可能原路返回，即从目的节点经过本节点返回到实际的k8s集群外部的客户端，参考下面的几条规则，（其中 KUBE-POSTROUTING 链中的 `RETURN` 表示：跳出当前链，该链里后续的规则不再执行），KUBE-POSTROUTING 链的含义就是对打了 `0x4000/0x4000` 标记的 Packet 进行 MASQUERADE。 
```s
-A KUBE-MARK-MASQ -j MARK --set-xmark 0x4000/0x4000
-A KUBE-NODEPORTS -p tcp -m comment --comment "default/hello-world" -m tcp --dport 30610 -j KUBE-MARK-MASQ
# KUBE-POSTROUTING 链中的规则
-A KUBE-POSTROUTING -m mark ! --mark 0x4000/0x4000 -j RETURN
-A KUBE-POSTROUTING -j MARK --set-xmark 0x4000/0x0
-A KUBE-POSTROUTING -m comment --comment "kubernetes service traffic requiring SNAT" -j MASQUERADE
```

### 配置了 sessionAffinity 的 Service
主要参考[Kubernetes Services and Iptables](https://msazure.club/kubernetes-services-and-iptables/)，`sessionAffinity`主要功能是让客户端固定访问特定的后端（Pod），可以参考 Kubernetes 官方文档。
配置 
```yml
apiVersion: v1
kind: Service
metadata:
  name: redis-sa
spec:
  sessionAffinity: ClientIP
  ports:
  - port: 6379
  selector:
    app: redis
```
生成的规则主要使用了 iptables 的 `recent` 模块，可参考[iptables-extensions.man](https://ipset.netfilter.org/iptables-extensions.man.html)。
```s
-A KUBE-SVC-YUZPDSCUOF7FG5LD -m recent --rcheck --seconds 10800 --reap --name KUBE-SEP-6MUUJB4K75LGZXHS --mask 255.255.255.255 --rsource -j KUBE-SEP-6MUUJB4K75LGZXHS
-A KUBE-SVC-YUZPDSCUOF7FG5LD -m recent --rcheck --seconds 10800 --reap --name KUBE-SEP-F5DCISRHJOTG66JA --mask 255.255.255.255 --rsource -j KUBE-SEP-F5DCISRHJOTG66JA
-A KUBE-SVC-YUZPDSCUOF7FG5LD -m statistic --mode random --probability 0.50000000000 -j KUBE-SEP-6MUUJB4K75LGZXHS
-A KUBE-SVC-YUZPDSCUOF7FG5LD -j KUBE-SEP-F5DCISRHJOTG66JA

-A KUBE-SEP-6MUUJB4K75LGZXHS -s 10.244.1.69/32 -j KUBE-MARK-MASQ
-A KUBE-SEP-6MUUJB4K75LGZXHS -p tcp -m recent --set --name KUBE-SEP-6MUUJB4K75LGZXHS --mask 255.255.255.255 --rsource -m tcp -j DNAT --to-destination 10.244.1.69:6379
```

### 假如 Service 没有后端 Pod
没有后端 Pod，访问的时候就报错了，参考[Kubernetes Services and Iptables](https://msazure.club/kubernetes-services-and-iptables/)，报错信息为：`ICMP 10.0.8.126 tcp port 6379 unreachable`
```s
-A KUBE-SERVICES -d 10.0.8.126/32 -p tcp -m comment --comment "default/redis-none: has no endpoints" -m tcp --dport 6379 -j REJECT --reject-with icmp-port-unreachable
```

### Headless Service
Headless Service 是指没有 VIP 的 Service，其定义如下：
```yml
apiVersion: v1
kind: Service
metadata:
  name: redis-headless
spec:
  clusterIP: None
  ports:
  - port: 6379
  selector:
    app: redis
```
通过 coredns 解析 headless Service 域名。
```s
#nslookup redis-headless.default.svc.cluster.local 10.0.0.10
Server:		10.0.0.10
Address:	10.0.0.10#53

Name:	redis-headless.default.svc.cluster.local
Address: 10.244.1.69
Name:	redis-headless.default.svc.cluster.local
Address: 10.244.1.70
```
headless Service 没有 iptables 生成。

### 参考

[kubernetes 官方文档--Using Source IP](https://kubernetes.io/docs/tutorials/services/source-ip/)

[Kubernetes Services and Iptables](https://msazure.club/kubernetes-services-and-iptables/)

[kube-proxy iptables规则分析](http://kuring.me/post/kube-proxy-iptables/)

[kubernetes网络之service](https://cvvz.github.io/post/k8s-network-service/)

[iptables-extensions.man](https://ipset.netfilter.org/iptables-extensions.man.html)