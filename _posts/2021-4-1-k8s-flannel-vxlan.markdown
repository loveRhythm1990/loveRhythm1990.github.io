---
layout:     post
title:      "flannel vxlan 工作原理分步骤详解"
date:       2021-4-1 19:54:00
author:     "decent"
header-img-credit: false
tags:
    - 网络
---

**目录**
- [1. 容器发包到 cni0](#1-容器发包到-cni0)
- [2. cni0 路由报文到 flannel.1](#2-cni0-路由报文到-flannel1)
- [3. flannel.1 设备封包](#3-flannel1-设备封包)
  - [3.1 如何已知目标 VTEP 设备的 IP 查其 mac](#31-如何已知目标-vtep-设备的-ip-查其-mac)
  - [3.2 添加 vxlan header](#32-添加-vxlan-header)
  - [3.3 把 vxlan 报文封装到 udp 报文](#33-把-vxlan-报文封装到-udp-报文)
- [4. 主机网络发送 udp 报文](#4-主机网络发送-udp-报文)
- [5. 主机发送报文](#5-主机发送报文)
- [6. 协议栈将报文发给 flannel.1 设备](#6-协议栈将报文发给-flannel1-设备)
- [7. 原始报文发给 cni0](#7-原始报文发给-cni0)

在 flannel 网络插件中，vxlan 的思想在主机网络（underlay 网络）的基础上，通过封包解包，构建一个虚拟的二层网络（overlay 网络），其中 vxlan 的封包解包是在内核态进行的，因为 linux 本身就支持，所以效率还是比较高的。对于 flannel vxlan 来说，集群中所有的节点只工作在一个 vxlan 网络中，因此所有的 VTEP 设备名字都是 `flannel.1`，这名字中的 `1` 就是 VNI（vxlan network identifier），vxlan 网络序号。 

本文根据 flannel vxlan 网络模式中的 pod 发包流程理解下 vxlan 的工作原理。实验环境有三台节点，hostname 和 ip 对应如下（第三台节点 HostGW-Node2 基本用不到，除了偶尔看下路由策略，所以本文称 node 节点时，指的是 HostGW-Node 节点）：

|  节点   | 节点 ip  | pod 网段 |
|  ----  | ----  | ---- |
| HostGW-Master | 192.168.31.201 | 10.244.0.0/24 |
| HostGW-Node  | 192.168.31.202 | 10.244.1.0/24 |
| HostGW-Node2 | 192.168.31.203 | 10.244.2.0/24 |

另外有两个容器，分别在 Master 节点以及 Node 节点上，其 ip 分别为 `10.244.0.97`、`10.244.1.223`，我们就以这两个容器的通信过程，来理解下 vxlan 的工作过程，为了便于理解描述，我们称位于 master 节点的容器为 container-master（10.244.0.97），位于 node 节点的容器为 container-node（10.244.1.223）。
```s
[decent@HostGW-Master ~]$ kubectl get pods -o wide
NAME                                READY   STATUS    RESTARTS   AGE     IP             NODE            NOMINATED NODE   READINESS GATES
nginx-deployment-66b6c48dd5-gbt9z   1/1     Running   2          4d14h   10.244.0.97    hostgw-master   <none>           <none>
nginx-deployment-66b6c48dd5-p5f29   1/1     Running   1          4d14h   10.244.1.223   hostgw-node     <none>           <none>
```
在下图中，我一共画了七步，一步一步分析下中间路由是怎么做的，经历了哪些操作等。
![java-javascript](/pics/vxlan-net-exm.jpg){:height="60%" width="60%"}

### 1. 容器发包到 cni0
这一步是最简单的一步，因为容器中的 eth0 网卡是一个 veth 设备，我们常称为 veth pair，因为 veth 设备总是成对存在的，在 K8s 网络模型中， veth 设备的一端被设置成 eth0，放在了容器的网络 namespace，另一端跟主机的虚拟网桥设备 cni0 相连，所以流量出来之后，就到了 cni0 设备上。
```s
[decent@HostGW-Master ~]$ brctl show cni0
bridge name	bridge id		STP enabled	interfaces
cni0		8000.2a8c9339ed41	no		veth3879c25d
							veth89c2a9b1
```
其中 master-container 容器中的路由路由配置如下，可以看到不管是哪个网段（集群整个 pod 网段，还是默认路由）的数据，都是经 eth0 网卡发出去的，并且网关是 cni0
```s
[decent@HostGW-Master ~]$ sudo nsenter --net=/var/run/docker/netns/e07fc490dc9d
[root@HostGW-Master decent]# ip route
default via 10.244.0.1 dev eth0
10.244.0.0/24 dev eth0 proto kernel scope link src 10.244.0.97
10.244.0.0/16 via 10.244.0.1 dev eth0
```
> 如果无法通过 `kubectl exec` 命令查看容器网络，比如容器内没有 `/bin/sh`命令，可以通过 nsenter 进入到容器的网络 namespace 中查看，容器的网络 namespace 文件在其 pause 容器中的 `NetworkSettings` 字段，属性为 `SandboxKey`，比如：`"SandboxKey": "/var/run/docker/netns/e07fc490dc9d"`

### 2. cni0 路由报文到 flannel.1 
从 container-master 发出的 ip 报文，其目的 ip 为 container-node 的 ip，即 `10.244.1.223`，那这个报文该怎么路由呢？我们看下 master 节点的路由表：
```s
[decent@HostGW-Master ~]$ ip route
default via 192.168.31.1 dev ens33 proto static metric 100
10.244.0.0/24 dev cni0 proto kernel scope link src 10.244.0.1
10.244.1.0/24 via 10.244.1.0 dev flannel.1 onlink
10.244.2.0/24 via 10.244.2.0 dev flannel.1 onlink
172.17.0.0/16 dev docker0 proto kernel scope link src 172.17.0.1
192.168.31.0/24 dev ens33 proto kernel scope link src 192.168.31.201 metric 100
```
我们看到有这样一条路由策略：`10.244.1.0/24 via 10.244.1.0 dev flannel.1 onlink`，其含义是：凡是发往 `10.244.1.0/24`的报文，要经过 flannel.1 设备发出去，并且其 gateway 为 10.244.1.0。而我们从上图可以看到 10.244.1.0 这个地址正好是 node 节点上 VTEP 设备 flannel.1 的 ip 地址。

> 那这个路由策略是什么时候设置的呢？

这个路由策略是 flanneld 在节点 node 加入集群时（watch 节点资源），自动配置的路由，同时我们我们看到关于 `HostGW-Node2` 也有一条路由，*那是不是有多少个节点，就有多少个路由？*

### 3. flannel.1 设备封包
在这一步中，源 VTEP 设备收到原始 ip 报文后，要把原始数据包封装成一个二层数据帧（二层网络），然后发给目的 VTEP 设备。其最终封包的效果如下。[图片来自张磊大佬的极客时间k8s课程]()
![java-javascript](/img/in-post/docker-net/vxlans.png){:height="70%" width="70%"}

#### 3.1 如何已知目标 VTEP 设备的 IP 查其 mac
那这里有个问题，要封装成一个二层的 mac 帧，发给目标的 VTEP 设备，那目标 VTEP 设备的 mac 地址是多少呢?

在步骤 `2.cni0 路由报文到 flannel.1` 中，我们已经知道，通过查主机路由表，可以拿到目的 VTEP 设备的 ip，现在是 **知道了ip 地址，要知道 mac 地址**，在 tcp/ip 协议栈中，这是 arp 协议做的事情，（在以太网中，arp 要做一个广播，拿到对应 ip 的 mac 地址，然后通过这个 mac 地址通信）。在 flannel 框架中，这个 arp 记录是 flanneld 直接写的【flanneld 做的第二件事情，第一件事情是写路由】，我们先在 node 节点上，看下其 mac 地址是多少，我们使用 `ip -d link` 命令看下。
```s
[decent@HostGW-Node ~]$ ip -d link show flannel.1
4: flannel.1: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1450 qdisc noqueue state UNKNOWN mode DEFAULT group default
    link/ether d6:a5:69:c4:e8:f9 brd ff:ff:ff:ff:ff:ff promiscuity 0
    vxlan id 1 local 192.168.31.202 dev ens33 srcport 0 0 dstport 8472 nolearning ageing 300 noudpcsum noudp6zerocsumtx noudp6zerocsumrx addrgenmode eui64 numtxqueues 1 numrxqueues 1 gso_max_size 65536 gso_max_segs 65535
```
通过上面命令，我们看到 node 上的 VTEP 设备的 mac 地址是 `d6:a5:69:c4:e8:f9`。我们再在 master 主机的 arp 记录上有没有对应的记录，我们通过 `ip neigh` 或者 `arp -n` 命令看下，再强调下，arp 记录是根据 ip 查 mac 的，我们已经知道了目标 VTEP 设备的 ip 是 `10.244.1.0`，我们要查 mac 地址。通过下面的输出，我们能看到是能查到对应 mac 地址的。
```s
[decent@HostGW-Master ~]$ ip neigh show dev flannel.1
10.244.1.0 lladdr d6:a5:69:c4:e8:f9 PERMANENT
10.244.2.0 lladdr 92:16:49:ce:1b:f1 PERMANENT
[decent@HostGW-Master ~]$ arp -n
Address                  HWtype  HWaddress           Flags Mask            Iface
10.244.1.0               ether   d6:a5:69:c4:e8:f9   CM                    flannel.1
10.244.2.0               ether   92:16:49:ce:1b:f1   CM                    flannel.1
...
```

#### 3.2 添加 vxlan header
vxlan header 一共有 8 个字节，最主要的标识是 VNI，表示 vxlan 网络编号，一个 VTEP 设备只处理自己 vxlan 网络的报文，对于 flannel.1 则只处理 VNI 为 1 的报文。在到达目的主机后，协议栈会根据这个 vxlan header 以及 VNI 编号，交给对应的 VTEP 设备处理。

#### 3.3 把 vxlan 报文封装到 udp 报文
在 3.1 步封装了一个 mac 帧，在 3.2 步添加了一个 vxlan header，下面要把这些数据发送给对端主机，也就是 node 节点，那怎么发送呢？得走主机网络了，具体是把上面的数据封装到主机网络的一个 udp 报文中。在 vxlan 协议中，内核监听的端口是 `8472`，所以 udp 报文中填的端口是 8472（可以通过 `# netstat -ulnp | grep 8472` 命令查看）。 现在需要通过网络层来传输这个 udp 报文。

> 那问题又来了，在网络层，我们怎么知道 node 的 ip 地址？

其实只要知道目标 pod ip 地址的网段，就能知道其 node ip，因为每个 node 都有一个独立的网段，在 flannel 框架中，这个映射也是 flanneld 做的，其为 flannel.1 维护了一个 fdb 表，我们知道了目标 VTEP 设备的 mac 地址，就能查到其所在宿主机的 ip。【flanneld 做的第三件事：维护 fdb 记录】
```s
[decent@HostGW-Master ~]$ bridge fdb show flannel.1 | grep d6:a5:69:c4:e8:f9
d6:a5:69:c4:e8:f9 dev flannel.1 dst 192.168.31.202 self permanent
```
通过上面记录中，我们看到查到的 ip 是`192.168.31.202`，正好是 node 的 ip 地址。再总结一下，这个 flannel.1 的 fdb 表是根据 mac 查 ip 的。现在报文都封装好了，只需要通过主机网络进行发送就好了。

### 4. 主机网络发送 udp 报文
现在 ip 包封装好了，其目标 ip 为 `192.168.31.202`，我们再回顾下主机的路由，从路由表中，我们看到是发给 ens33 设备，也就是 master 主机的真实网卡。 
```s
[decent@HostGW-Master ~]$ ip route
default via 192.168.31.1 dev ens33 proto static metric 100
10.244.0.0/24 dev cni0 proto kernel scope link src 10.244.0.1
10.244.1.0/24 via 10.244.1.0 dev flannel.1 onlink
10.244.2.0/24 via 10.244.2.0 dev flannel.1 onlink
172.17.0.0/16 dev docker0 proto kernel scope link src 172.17.0.1
192.168.31.0/24 dev ens33 proto kernel scope link src 192.168.31.201 metric 100
```
### 5. 主机发送报文
也是看上面的路由表，可以看到是发给默认网关 `192.168.31.1`，这个是主机网络。

### 6. 协议栈将报文发给 flannel.1 设备
报文到了 node 节点之后，内核协议栈根据 vxlan header 以及 VNI 编号将报文发送给 flannel.1 设备。flannel.1 设备将报文解封，取出最原始的报文，即 ip 地址是 container-node ip `10.244.1.223` 的报文。

### 7. 原始报文发给 cni0
经查路由表，目标 ip 地址为 `10.244.1.223` 的报文，应该发送给 cni0 接口。
```s
[decent@HostGW-Node ~]$ ip route
default via 192.168.31.1 dev ens33 proto static metric 100
10.244.0.0/24 via 10.244.0.0 dev flannel.1 onlink
10.244.1.0/24 dev cni0 proto kernel scope link src 10.244.1.1
10.244.2.0/24 via 10.244.2.0 dev flannel.1 onlink
172.17.0.0/16 dev docker0 proto kernel scope link src 172.17.0.1
192.168.31.0/24 dev ens33 proto kernel scope link src 192.168.31.202 metric 100
```
发给 cni0 之后，cni0 接口，再查查自己的 fdb 表，看对应的 mac 地址要经过哪个 veth 端口发送，这又是二层网络了。下面命令是看 cni0 的 fdb 表。
```s
[decent@HostGW-Node ~]$ bridge fdb show cni0 | grep veth
7a:24:4a:50:fc:98 dev veth3c591b8d vlan 1 master cni0 permanent
7a:24:4a:50:fc:98 dev veth3c591b8d master cni0 permanent
33:33:00:00:00:01 dev veth3c591b8d self permanent
01:00:5e:00:00:01 dev veth3c591b8d self permanent
33:33:ff:50:fc:98 dev veth3c591b8d self permanent
``` 

在本文中，记录了在 flannel vxlan 网络模式下，一个报文从一个容器发往另一个容器的步骤，基本把链路梳理清楚了。后面根据具体问题再丰富这篇文章的内容。

