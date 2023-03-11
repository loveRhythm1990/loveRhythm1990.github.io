---
layout:     post
title:      "体验 Calico BGP 网络模型"
date:       2023-2-4 10:10:00
author:     "decent"
header-img-credit: false
tags:
    - 网络
---
- [安装 Calico](#安装-calico)
- [安装 calicoctl](#安装-calicoctl)
- [calico 架构概述](#calico-架构概述)
- [报文路径](#报文路径)
  - [在 master 容器内 ping node 节点上的容器](#在-master-容器内-ping-node-节点上的容器)
  - [容器网络配置](#容器网络配置)
  - [主机网络配置](#主机网络配置)
- [总结](#总结)
- [参考](#参考)

本文在一个三个节点的 K8s 集群中体验一下 Calico 网络模型，并对 Calico 的框架和通信模式做一个概述，三个节点的 K8s 集群如下，K8s 使用的版本为 1.20，Calico 使用的版本为 3.20。

|  K8s 节点  | 节点 ip  | 
|  ----  | ----  | 
| Master | 192.168.31.201 |
| Node | 192.168.31.202 | 
| Node2  | 192.168.31.203 | 

### 安装 Calico
安装这部分参考官方文档 [Quickstart for Calico on Kubernetes](https://projectcalico.docs.tigera.io/getting-started/kubernetes/quickstart)，在使用 `kubeadm init` 初始化集群之后，通过两个 yaml 文件分别安装 `Tigera Calico operator` 和 Calico。
```s
# 使用 10.244.0.0/16 网段
kubeadm init --pod-network-cidr=10.244.0.0/16
# install tigera operator
kubectl create -f https://docs.projectcalico.org/archive/v3.20/manifests/tigera-operator.yaml
# install calico
kubectl create -f https://docs.projectcalico.org/archive/v3.20/manifests/custom-resources.yaml
```
在上面最后一步 install calico 中，需要指定 ip pool 的网段，这个网段需要跟 kubeadm 的网段相同。另外为了简化模型，我们不启用 vxlan 封装，在配置文件中将 `encapsulation: VXLANCrossSubnet` 注释掉，本以为注释掉之后，就不启用 overlay 封装了，发现注释掉之后，默认使用了 IPIP 封装。这里我们设置 `encapsulation: None`，表示不使用任何封装。
> encapsulation 支持的选项有：IPIPCrossSubnet、IPIP、VXLAN、VXLANCrossSubnet、None，默认是 IPIP。

```s
apiVersion: operator.tigera.io/v1
kind: Installation
metadata:
  name: default
spec:
  calicoNetwork:
    ipPools:
    - blockSize: 26
      cidr: 10.244.0.0/16
      encapsulation: None
```
安装完成后，观察所有 Calico pod 启动正常。 
```s
[decent@Master calico]$ kubectl get pods -n calico-system
NAME                                       READY   STATUS    RESTARTS   AGE
calico-kube-controllers-7bcf4776f8-nvlwd   1/1     Running   0          4m48s
calico-node-gkrhr                          1/1     Running   0          4m48s
calico-node-v6rk7                          1/1     Running   0          4m48s
calico-node-w795d                          1/1     Running   0          4m48s
calico-typha-64457dc85c-5nkgw              1/1     Running   0          4m48s
calico-typha-64457dc85c-j7dv9              1/1     Running   0          4m40s
calico-typha-64457dc85c-np4lp              1/1     Running   0          4m40s
```

### 安装 calicoctl
calicoctl 是 Calico 的命令行工具，可以通过 calicoctl 来管理配置 Calico 资源和网络，Calico 配置文件都是以 crd 的形式声明的，使用 calicoctl 管理 Calico 的资源和配置跟使用它 kubectl 管理 crd 中的 cr 资源是一致的。 calicoctl 官方文档为[calicoctl user reference](https://projectcalico.docs.tigera.io/reference/calicoctl/overview)。通过下面命令下载安装，版本要跟 Calico 一致。
```s
wget https://github.com/projectcalico/calico/releases/download/v3.20.6/release-v3.20.6.tgz
tar -xvf release-v3.20.6.tgz
```
在 `192.168.31.201` 节点通过 calicoctl 查看 BGP peer 信息，发现另外两个 peer 分别是 node1 和 node2。另外这里是`full-mesh` 模型，即每个节点都充当一个 BGP peer，并且所有的 BGP peer 之间相互通信，并交换路由信息。另一种模式是 `Route Reflectors`，即所有的 peer 只跟一个中心式的 reflactor 通信，在集群规模较大时，应该使用后者。
```s
[decent@Master calico]$ sudo calicoctl node status
Calico process is running.

IPv4 BGP status
+----------------+-------------------+-------+----------+-------------+
|  PEER ADDRESS  |     PEER TYPE     | STATE |  SINCE   |    INFO     |
+----------------+-------------------+-------+----------+-------------+
| 192.168.31.202 | node-to-node mesh | up    | 09:32:50 | Established |
| 192.168.31.203 | node-to-node mesh | up    | 09:32:49 | Established |
+----------------+-------------------+-------+----------+-------------+

IPv6 BGP status
No IPv6 peers found.
```
通过 calicoctl 查看 ippool 的状态。从下面 ippool 的状态也能看出，在此次网路配置中，没有使用 ipip overlay 网络，也没有使用 vxlan overlay 网络。那就是纯 BGP 转发了。
```yml
[decent@Master resources]$ sudo calicoctl get ippools -o yaml
apiVersion: projectcalico.org/v3
items:
- apiVersion: projectcalico.org/v3
  kind: IPPool
  metadata:
    creationTimestamp: "2023-02-04T10:41:06Z"
    name: default-ipv4-ippool
    resourceVersion: "27237"
    uid: d475e113-4181-4d27-9a7b-5e2e91093ed1
  spec:
    blockSize: 26
    cidr: 10.244.0.0/16
    ipipMode: Never
    natOutgoing: true
    nodeSelector: all()
    vxlanMode: Never
kind: IPPoolList
metadata:
  resourceVersion: "71808"
```
上面 calicoctl 的输出感觉跟 K8s 中的资源模型一致，应该是直接转发的 calico crd 的信息，如下，因此 calico 所有配置应该都是以 crd 的形式保存在 etcd 中的。
```s
[decent@Master resources]$ kubectl get crd | grep pool
ippools.crd.projectcalico.org                         2023-02-04T10:40:47Z
[decent@Master resources]$ kubectl get ippools.crd.projectcalico.org default-ipv4-ippool
NAME                  AGE
default-ipv4-ippool   5h9m
```

### calico 架构概述
Calico v3.20.6 版本给出的架构图如下，最新版的架构图在这 [Component architecture](https://projectcalico.docs.tigera.io/master/reference/architecture/overview)，总体架构变化不大。
![java-javascript](/pics/architecture-calico_old.svg)
根据 [官网](https://projectcalico.docs.tigera.io/archive/v3.20/reference/architecture/overview) 解释，主要的组件及功能如下:
* Felix: 是一个守护进程，运行在 daemonset calico-node 中，其主要工作有：1）管理网络接口；2）编写路由规则；3）编写 ACL，实现方式是 iptables 规则。
* BIRD：全称是 BGP internet routing daemon，其工作为 从 Felix 中获取路由规则，并发送到其他 BGP peer 中。
* confd：监听 Calico 配置变化（监听 Calico crd ?），并触发新的 BIRD 配置。
* Dikastes：跟 istio 集成，作为每个应用容器的 sidecar，配置网络策略。

### 报文路径
假设我们有两个业务 pod，分别部署在 master 和 node 节点上，我们以下面的 `10.244.219.66` 访问 `10.244.166.129` 为例，看一下路由是怎么转发的。
```s
[decent@Master resources]$ kubectl get pods -o wide
NAME                               READY   STATUS    RESTARTS   AGE   IP               NODE     NOMINATED NODE   READINESS GATES
echo-deployment-598f49cc44-7r7kv   1/1     Running   0          91m   10.244.166.129   node1    <none>           <none>
echo-deployment-598f49cc44-td2lf   1/1     Running   0          91m   10.244.219.66    master   <none>           <none>
echo-deployment-598f49cc44-wsw2d   1/1     Running   0          91m   10.244.166.130   node1    <none>           <none>
```

#### 在 master 容器内 ping node 节点上的容器
首先测试一下网络链路 `10.244.219.66 -> 10.244.166.129`，通的，没有问题。
```s
/ # ping 10.244.166.129
PING 10.244.166.129 (10.244.166.129): 56 data bytes
64 bytes from 10.244.166.129: seq=0 ttl=62 time=0.479 ms
64 bytes from 10.244.166.129: seq=1 ttl=62 time=0.512 ms
64 bytes from 10.244.166.129: seq=2 ttl=62 time=0.482 ms
^C
--- 10.244.166.129 ping statistics ---
3 packets transmitted, 3 packets received, 0% packet loss
round-trip min/avg/max = 0.479/0.491/0.512 ms
```
#### 容器网络配置
我们首先看一下 master 节点上容器 `10.244.219.66` 的网络配置，首先是路由表，看到路由表时感觉有点奇怪，`169.254.1.1` 这个 ip 不属于我们配置过的任何一个网段。而默认路由就是通过 eth0 网卡发往这个网关。
```s
/ # route -n
Kernel IP routing table
Destination     Gateway         Genmask         Flags Metric Ref    Use Iface
0.0.0.0         169.254.1.1     0.0.0.0         UG    0      0        0 eth0
169.254.1.1     0.0.0.0         255.255.255.255 UH    0      0        0 eth0
```
再看下接口配置，接口配置貌似挺正常的。
```s
/ # ifconfig eth0
eth0      Link encap:Ethernet  HWaddr 2E:3C:C9:8B:C0:0F
          inet addr:10.244.219.66  Bcast:10.244.219.66  Mask:255.255.255.255
          UP BROADCAST RUNNING MULTICAST  MTU:1500  Metric:1
          RX packets:13 errors:0 dropped:0 overruns:0 frame:0
          TX packets:6 errors:0 dropped:0 overruns:0 carrier:0
          collisions:0 txqueuelen:0
          RX bytes:1016 (1016.0 B)  TX bytes:957 (957.0 B)
```

关于上面那个奇怪的地址 `169.254.1.1`，我查了一下，原来 Calico 在 ARP 表中，添加了下面一条记录，即容器在查 `169.254.1.1` ip 所对应的 mac 地址的时候，根据 arp 表就直接返回了 `ee:ee:ee:ee:ee:ee` 地址，不用发送 arp 广播了。官方也有 FAP[Why does my container have a route to 169.254.1.1?](https://projectcalico.docs.tigera.io/reference/faq)

> 回顾一下， ARP 协议是以太网使用的，用来根据 ip 地址来查找 mac 地址的协议。因为以太网是根据 mac 地址通信的。

```s
/ # ip neigh
192.168.31.201 dev eth0 lladdr ee:ee:ee:ee:ee:ee used 0/0/0 probes 0 STALE
169.254.1.1 dev eth0 lladdr ee:ee:ee:ee:ee:ee used 0/0/0 probes 1 STALE
```

当容器中的报文通过 veth pair 设备之后，通过查主机的路由表进行下一步转发。另外这里还有个问题，主机上的 veth pair 另一端通过 `ARP proxy` 机制对容器内的 arp 广播报文进行了应答。
```s
[decent@Master ~]$ sudo cat /proc/sys/net/ipv4/conf/calic88c1a5fc6d/proxy_arp
1
```

> 如何确认主机上跟容器网卡匹配的 veth 设备？
1. 在容器中执行 `ip addr`，看到网卡后面有个后缀 `if6`，这个 `6` 就是主机中 veth 设备的编号。
```s
/ # ip addr
1: lo: <LOOPBACK,UP,LOWER_UP> mtu 65536 qdisc noqueue state UNKNOWN qlen 1000
    link/loopback 00:00:00:00:00:00 brd 00:00:00:00:00:00
    inet 127.0.0.1/8 scope host lo
       valid_lft forever preferred_lft forever
3: eth0@if6: <BROADCAST,MULTICAST,UP,LOWER_UP,M-DOWN> mtu 1500 qdisc noqueue state UP
    link/ether 2e:3c:c9:8b:c0:0f brd ff:ff:ff:ff:ff:ff
    inet 10.244.219.66/32 brd 10.244.219.66 scope global eth0
       valid_lft forever preferred_lft forever
```
2. 在主机上执行 `ip link show | grep 6` 查找对应设备。
```s
[decent@Master ~]$ ip link show | grep 6
1: lo: <LOOPBACK,UP,LOWER_UP> mtu 65536 qdisc noqueue state UNKNOWN mode DEFAULT group default qlen 1000
    link/ether 00:50:56:2c:93:b5 brd ff:ff:ff:ff:ff:ff
6: calic88c1a5fc6d@if3: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1500 qdisc noqueue state UP mode DEFAULT group default
```

#### 主机网络配置
我们首先看一下主机上的网络设备，除去我们不关心的 lo、docker0，发现除了容器的 veth 没有额外的设备了，这跟 flannel 下还有个 cni0 虚拟网桥是不一致的，在 flannel 网络中，容器的默认网关即为 cni0 网桥。这也可能是 Calico 需要 arp proxy 的原因？
```s
[decent@Master ~]$ ip link list
1: lo: <LOOPBACK,UP,LOWER_UP> mtu 65536 qdisc noqueue state UNKNOWN mode DEFAULT group default qlen 1000
    link/loopback 00:00:00:00:00:00 brd 00:00:00:00:00:00
2: ens33: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1500 qdisc pfifo_fast state UP mode DEFAULT group default qlen 1000
    link/ether 00:50:56:2c:93:b5 brd ff:ff:ff:ff:ff:ff
3: docker0: <NO-CARRIER,BROADCAST,MULTICAST,UP> mtu 1500 qdisc noqueue state DOWN mode DEFAULT group default
    link/ether 02:42:3b:4b:fa:9c brd ff:ff:ff:ff:ff:ff
4: cali83494b49003@if3: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1500 qdisc noqueue state UP mode DEFAULT group default
    link/ether ee:ee:ee:ee:ee:ee brd ff:ff:ff:ff:ff:ff link-netnsid 0
5: calia43cac5d3d1@if3: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1500 qdisc noqueue state UP mode DEFAULT group default
    link/ether ee:ee:ee:ee:ee:ee brd ff:ff:ff:ff:ff:ff link-netnsid 1
6: calic88c1a5fc6d@if3: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1500 qdisc noqueue state UP mode DEFAULT group default
    link/ether ee:ee:ee:ee:ee:ee brd ff:ff:ff:ff:ff:ff link-netnsid 2
```

然后再看一下主机的路由表，因为我们的目标 ip 为 `10.244.166.129`，匹配第二条路由，也就是要经网卡 ens33 发往 `192.168.31.202`，这个地址就是 node 的 ip 地址。此外，如果我们想访问 node 上另一个容器，其 ip 为 `10.244.166.130`，匹配的也是这个路由表项。
```s
[decent@Master resources]$ ip route
default via 192.168.31.1 dev ens33 proto static metric 100
10.244.166.128/26 via 192.168.31.202 dev ens33 proto bird
10.244.219.64 dev cali83494b49003 scope link
blackhole 10.244.219.64/26 proto bird
10.244.219.65 dev calia43cac5d3d1 scope link
10.244.219.66 dev calic88c1a5fc6d scope link
172.17.0.0/16 dev docker0 proto kernel scope link src 172.17.0.1
192.168.31.0/24 dev ens33 proto kernel scope link src 192.168.31.201 metric 100
```

目标主机的路由表跟源主机路由表类似，以源主机上的源 pod 容器 `10.244.219.66`，会直接发给其 veth 设备。

### 总结
大概看了一下 Calico 的总体架构和思路，离理解 Calico 还有一些距离，不过入门之后就容易多了。


### 参考
[FIB表与RIB表的区别与联系](https://www.cnblogs.com/geekHao/p/12251527.html)

[calico 配置 BGP Route Reflectors](https://www.cnblogs.com/cheyunhua/p/15206529.html)