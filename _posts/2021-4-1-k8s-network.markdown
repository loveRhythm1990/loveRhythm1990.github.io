---
layout:     post
title:      "容器网络基础"
date:       2021-4-1 19:54:00
author:     "decent"
header-img-credit: false
tags:
    - k8s
---

感觉网络涉及的内容挺多的。这里主要参考张磊的《深入剖析Kubernetes》。

#### 基本概念
名词解释都放在这里
* 网络栈：网卡（Network Interface）、回环设备（Loopback Device）、路由表（Routing Table）和 iptables 规则。
* 直联路由：`Gateway`是`0.0.0.0`的路由，即，凡是匹配到这条规则的IP包，应该经过本机的eth0网卡，通过二层网络直接发往目的主机。另外根据维基百科的解释：[ This generally means that no intermediate routing hops are necessary because the system is directly connected to the destination.](https://en.wikipedia.org/wiki/0.0.0.0)，一般认为设备是二层直接连接的，通过mac地址就能通信。
```s
$ route
Kernel IP routing table
Destination     Gateway         Genmask         Flags Metric Ref    Use Iface
default         172.17.0.1      0.0.0.0         UG    0      0        0 eth0
172.17.0.0      0.0.0.0         255.255.0.0     U     0      0        0 eth0
```
* 默认路由：路由表中，经过最长匹配，没有找到匹配规则时走的路由，上面的`default`就是默认路由

#### 单机容器网络
单机容器网络是指没有接入Kubernetes时的网络情况，这里[单机容器网络配置](https://loverhythm1990.github.io/2019/11/07/dockernet/)列了一些知识点，可以再复习一下。单机容器网络一般有三种情况。
* 容器与宿主机通信。在上一小节打印了一个路由表，这个路由表就是一个容器的路由表，从表中可以看出，不管是默认路由还是`172.17.0.0`的网段，都是通过eth0发到宿主机docker0网桥的。报文到了docker0网桥之后，继续该怎么走，这个要看宿主机的路由表了。同时，根据我看文档（纸上谈兵），不同跨主机通信的网络方法，也就从路由表这里开始了不同。对于UDP解决方案来讲，这部分报文是路由到了`flannel0`设备，这是一个TUN设备；对于VXLAN方案来讲，路由到了`flannel.1`，来进行二层数据帧的封装工作。
* 容器与本宿主机的其他容器通信，这个是通过直联路由直接发送的。
* 容器与非本宿主机通信，这个是到了docker0之后，查宿主机路由表，发现得从宿主机的`eth0`出去。

另外补充一点`TUN`设备以及`veth`设备，文末给了一些链接，大概就是`TUN`设备是为了在用户空间和内核空间传输数据，跟`eth0`的区别就是，`eth0`一端是协议栈，而另一端则是物理网络（比如一个交换机），可以通过`/dev/net/tun`设备来读取和发送设备。而`veth`设备最常用的场景就是连接两个不同的net namespace.

#### Flannel UDP解决方案
UDP解析方法是把容器的流量从`docker0`路由到`flannel0`设备，由用户态的flannel进程来处理，用户态的flannel进程知道每个容器子网对应的nodeIP（储存在etcd里面的，也就是每个节点会被分配一个子网，容器从这个子网中获取ip），flannel知道目的主机的IP之后，将容器的报文（一个IP包），封装在UDP报文里，UDP的目的IP就是目标宿主机的IP，UDP报文的源地址是当前宿主机IP，UDP报文的端口是`8285`，这个是flannel监听的端口，到了目标主机再交给flannel解包。

所以Flannel UDP是靠Flannel进程把容器IP报文封装到UDP报文的解决方案。重点关注UDP。

![java-javascript](/img/in-post/docker-net/udp.jpeg)
[图片来自张磊大佬的极客时间k8s课程]

Flannel UDP提供的是三层的Overlay网络：它首先对发出端的IP包进行UDP封装，然后在接收端进行解封装拿到原始IP报文，进而把这个报文转发给目标容器。Flannel UDP性能不好主要是要经过三次用户态与内核态的数据拷贝：
* 业务容器发数据到宿主机网桥，经过veth设备到docker0网桥，即上图中的 container-1 -> docker0
* flannel0设备拷贝数据到用户态的flannel进程，即上图总的 flannel0 -> flanneld:8285，flannel0是一个tun设备，从docker0到flannel0是不经过用户态的。
* 用户态的flannel进程封包之后，向对端host发送udp报文，即上图中的，flanneld:8285 -> eth0



#### Flannel VXLAN解决方案
VXLAN的封包操作放在了内核态去做，把所以容器放在一个二层网络上（所有节点的VTEP设备构成了一个二层网络，就像是在一个局域网，这个二层网络是基于现有三层网络的，最终通信还是要靠现有网络实现传输），二层网络靠mac报文通信，所以它要封装的是mac报文，这个是由`flannel.1`设备去做的，这个`flannel.1`就是一个VTEP设备。VTEP封装的MAC报文，其MAC地址是对端VTEP设备的地址，一般来说，由IP地址（我们通过路由表能拿到对端VTEP设备的IP地址）获得MAC地址，是通过`ARP`地址解析协议来实现的，但是Flannel已经将每个VTEP的MAC地址，记录在节点上了，通过`ip neigh show dev flannel.1`命令可以查看MAC地址。

![java-javascript](/img/in-post/docker-net/vxlan.jpeg)
[图片来自张磊大佬的极客时间k8s课程]

关于VXLAN网络模式，我有个疑问，所有节点都对每个子网配置一个路由（如下），如果集群中节点数量非常多，那路由表项是不是就特别多？**查找起来花时间吗？**
```s
$ route -n
Kernel IP routing table
Destination     Gateway         Genmask         Flags Metric Ref    Use Iface
...
10.1.16.0       10.1.16.0       255.255.255.0   UG    0      0        0 flannel.1
```
上面路由显示凡是发往10.1.16.0/24网段的IP包，都需要经过flannel.1设备发出，并且，它最后被发往的网关地址是：10.1.16.0，10.1.16.0正是Node 2上的VTEP设备（也就是flannel.1 设备）的IP地址。

![java-javascript](/img/in-post/docker-net/vxlans.png)
最终封包效果如上所示，解释一下各部分：
* 目的容器的IP地址：这个是容器发出的原始报文的IP地址。
* 目的VTEP设备的MAC地址：flanneld进程帮我们获取到了，通过`ip neigh`可以看到
* VXLAN header: Linux加的一个特殊的VXLAN头，用来表示这个数据包实际上是一个VXLAN要使用的数据帧。而这个 VXLAN 头里有一个重要的标志叫作 VNI，它是 VTEP 设备识别某个数据帧是不是应该归自己处理的重要标识。而在 Flannel 中，VNI 的默认值是 1，这也是为何，宿主机上的 VTEP 设备都叫作 flannel.1 的原因，这里的"1"，其实就是 VNI 的值
* Linux 内核会把这个数据帧封装进一个 UDP 包里发出去，根据[vxlan 协议原理简介](https://cizixs.com/2017/09/25/vxlan-protocol-introduction/)，这个UDP的端口号是4789，VTEP设备使用的
* 目的主机的IP地址：flannel.1这个VTEP设备还要扮演"网桥"的角色，维护了一个FDB（Forwarding DataBase），用来存储MAC地址和IP的对应关系，这个不太懂。使用方式如下：
```s
# 在Node 1上，使用"目的VTEP设备"的MAC地址进行查询
$ bridge fdb show flannel.1 | grep 5e:f8:4f:00:e3:37
5e:f8:4f:00:e3:37 dev flannel.1 dst 10.168.0.3 self permanent
```
发往目标VTEP设备（MAC 地址是 5e:f8:4f:00:e3:37）的二层数据帧，应该通过 flannel.1 设备，发往 IP 地址为 10.168.0.3 的主机。显然，这台主机正是 Node 2，UDP包要发往的目的地就找到了。


#### CNI
CNI的项目地址为：[CNI - the Container Network Interface](https://github.com/containernetworking/cni)，spec的地址为：[https://github.com/containernetworking/cni](https://github.com/containernetworking/cni/blob/master/SPEC.md)，根据spec，CNI主要定义了5个标准：
1. A format for administrators to define network configuration.  定义网络配置的格式
2. A protocol for container runtimes to make requests to network plugins.  容器运行时请求网络插件的协议。
3. A procedure for executing plugins based on a supplied configuration.  依据提供的配置文件执行插件的步骤。
4. A procedure for plugins to delegate functionality to other plugins.  网络插件将功能委派给其他插件的步骤。
5. Data types for plugins to return their results to the runtime. 插件返回数据给容器运行时的类型。

简述下各个标准
##### 网络配置格式
下面给了一个例子，kubelet如果要使用cni配置，有三个参数需要注意下，首先是`--network-plugin=cni`，指定使用cni创建，其次是`--cni-conf-dir`，指定网路配置的路径，默认是`/etc/cni/net.d`，这里的配置就是指符合CNI规范的配置，kubelet要读取这个配置文件来发现cni插件；还有就是`--cni-bin-dir`，这个是指定cni二进制文件的目录，默认是`/opt/cni/bin`。

在下面的配置文件中，`type`字段就是cni二进制文件的名字，需要放在`--cni-bin-dir`目录下，其中`bridge`插件是用来建立虚拟网桥设备的，`ipam`插件是用来分配和回收IP的（其中有dhcp和host-local两种类型），`bandwidth`插件是对容器进行流量控制的，后者的实现可以通过[cni标准的代码](https://github.com/containernetworking/plugins/tree/e27c48b391539f5536918b9c379c59ef2793cb0d/plugins/meta/bandwidth)中查看。
```json
{
  "cniVersion": "1.0.0",
  "name": "dbnet",
  "plugins": [
    {
      "type": "bridge",
      // plugin specific parameters
      "bridge": "cni0",
      "keyA": ["some more", "plugin specific", "configuration"],
      
      "ipam": {
        "type": "host-local",
        // ipam specific
        "subnet": "10.1.0.0/16",
        "gateway": "10.1.0.1",
        "routes": [
            {"dst": "0.0.0.0/0"}
        ]
      },
      "dns": {
        "nameservers": [ "10.1.0.1" ]
      }
    },
    {
      "type": "tuning",
      "capabilities": {
        "mac": true,
      },
      "sysctl": {
        "net.core.somaxconn": "500"
      }
    },
    {
        "type": "portmap",
        "capabilities": {"portMappings": true}
    }
  ]
}
```
##### Execution Protocol （调用协议）
这里是指容器运行时调用CNI插件的协议，一方面是传递参数，另一方面是调用规约。传递参数通过执行一些环境变量（针对特定cmd的环境变量，不是操作系统全局的），并将网络配置的json形式作为标准输入。环境变量有：
* CNI_COMMAND: 指定需要执行的动作：ADD, DEL, CHECK, or VERSION.
* CNI_CONTAINERID: Container ID. 
* CNI_NETNS: 容器net namespace。 a path to the network namespace (e.g. /run/netns/\[nsname])
* CNI_IFNAME: 容器内部要创建的虚拟机网络设备名字.
* CNI_ARGS: Extra arguments passed in by the user at invocation time. Alphanumeric key-value pairs separated by semicolons; for example, "FOO=BAR;ABC=123"
* CNI_PATH: List of paths to search for CNI plugin executables. Paths are separated by an OS-specific list separator; for example ':' on Linux and ';' on Windows

以docker为例，dockershim会通过SetupPod方法传入上述环境变量，并进行CNI插件的调用。

##### Execution of Network Configurations
容器运行时如何识别网络配置，以及根据配置执行插件。

##### Plugin Delegation
创建委托机制，调用另一个插件来做事情，比如flannel插件靠bridge设备来建立虚拟网桥。

##### Result Types
插件返回结果的形式 

#### CNI实现
首先要实现网络方案本身。以Flannel为例，要维护VTEP设备的MAC地址，目的主机的ip地址等，具体有创建flannel.1设备、配置宿主机路由、配置ARP和FDB表里的信息等。

然后实现该网络方案对应的CNI插件，这一部分要做的就是配置Infra容器里的网络栈，并把它连在CNI网桥上。


#### 参考：
[Linux虚拟网络设备之tun/tap](https://segmentfault.com/a/1190000009249039)

[云计算底层技术-虚拟网络设备(tun/tap,veth)](https://opengers.github.io/openstack/openstack-base-virtual-network-devices-tuntap-veth/)

[Linux虚拟网络设备之veth](https://segmentfault.com/a/1190000009251098)

[配置网络插件flannel](https://jkzhao.github.io/2019/09/16/%E9%85%8D%E7%BD%AE%E7%BD%91%E7%BB%9C%E6%8F%92%E4%BB%B6flannel/)

[vxlan 协议原理简介](https://cizixs.com/2017/09/25/vxlan-protocol-introduction/)

[vxlan文档](https://tools.ietf.org/html/rfc7348)