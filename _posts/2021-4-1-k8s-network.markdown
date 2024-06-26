---
layout:     post
title:      "容器网络基础及 CNI 概述"
date:       2021-4-1 19:54:00
author:     "decent"
header-img-credit: false
tags:
    - 网络
---

**目录**
- [基本概念](#基本概念)
- [单机容器网络](#单机容器网络)
- [CNI](#cni)
  - [网络配置格式](#网络配置格式)
  - [Execution Protocol （调用协议）](#execution-protocol-调用协议)
  - [Execution of Network Configurations](#execution-of-network-configurations)
  - [Plugin Delegation](#plugin-delegation)
  - [Result Types](#result-types)
- [CNI实现](#cni实现)
- [参考：](#参考)

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

#### CNI
CNI的项目地址为：[CNI - the Container Network Interface](https://github.com/containernetworking/cni)，spec的地址为：[https://github.com/containernetworking/cni](https://github.com/containernetworking/cni/blob/master/SPEC.md)，根据spec，CNI主要定义了5个标准：
1. A format for administrators to define network configuration.  定义网络配置的格式
2. A protocol for container runtimes to make requests to network plugins.  容器运行时请求网络插件的协议。
3. A procedure for executing plugins based on a supplied configuration.  依据提供的配置文件执行插件的步骤。
4. A procedure for plugins to delegate functionality to other plugins.  网络插件将功能委派给其他插件的步骤。
5. Data types for plugins to return their results to the runtime. 插件返回数据给容器运行时的类型。

cni 的官方文档地址为[https://www.cni.dev/plugins/v0.8/meta/](https://www.cni.dev/plugins/v0.8/meta/)，上面列举了各种规范，以及各个插件的作用。

简述下各个标准。
##### 网络配置格式
下面给了一个例子，kubelet如果要使用cni配置，有三个参数需要注意下，首先是`--network-plugin=cni`，指定使用cni创建，其次是`--cni-conf-dir`，指定网路配置的路径，默认是`/etc/cni/net.d`，这里的配置就是指符合CNI规范的配置，kubelet要读取这个配置文件来发现cni插件；还有就是`--cni-bin-dir`，这个是指定cni二进制文件的目录，默认是`/opt/cni/bin`。

在下面的配置文件中，`type`字段就是cni二进制文件的名字，需要放在`--cni-bin-dir`目录下，其中`bridge`插件是用来建立虚拟网桥设备的，`ipam`插件是用来分配和回收IP的（其中有dhcp和host-local两种类型），`bandwidth`插件是对容器进行流量控制的，后者的实现可以通过[cni标准的代码](https://github.com/containernetworking/plugins/tree/e27c48b391539f5536918b9c379c59ef2793cb0d/plugins/meta/bandwidth)中查看。

对于使用 flannel 插件来说的 cni 配置格式如下，该配置文件是由 kube-system namespace 下的 `kube-flannel-cfg` 配置文件生成。
```json
{
  "name": "cbr0",
  "cniVersion":"0.3.1",
  "plugins": [
    {
      "type": "flannel",
      "delegate": {
        "hairpinMode": true,
        "isDefaultGateway": true
      }
    },
    {
      "type": "portmap",
      "capabilities": {
        "portMappings": true
      }
    }
  ]
}
```
这个 `flannel` 插件的项目源代码地址为：[https://github.com/flannel-io/cni-plugin](https://github.com/flannel-io/cni-plugin)，需要注意这个项目跟 flannel 项目是独立的，这个只是个二进制文件，用来跟 cni 打交道，主要用来配置容器网络，我们称这个二进制或者项目为 `flannel-cni`，`flannel-cni`在配置网络时，基本上是 delegate 给其他默认插件来实现的，比如，网桥创建默认 delegate 给 `bridge`，ipam 地址分配默认 delegate 给 `host-local`。在进行地址分配时，需要读一个文件 `/run/flannel/subnet.env`，这个文件长下面样子：
```s
FLANNEL_NETWORK=10.42.0.0/16
FLANNEL_SUBNET=10.42.0.1/24
FLANNEL_MTU=1450
FLANNEL_IPMASQ=true
```
很明显，这个文件说明了当前节点的 CIDR 情况，跟 node.spec.podCIDR 定义的基本一致，这个文件是由 flannel 项目来维护并修改的。flannel-cni 读这个文件来分配 pod ip。目前看来这个文件将 `flannel-cni` 以及 `flannel` 项目联系在了一起，后者的一个任务就是管理每个节点的子网，关于每个节点的子网，其实是由 kube-controller-manager 组件来管理的，并填到了 `node.spec.podCIDR` 字段中，`flannel` 项目监听 node 资源并配置上述文件。

##### Execution Protocol （调用协议）
这里是指容器运行时调用CNI插件的协议，一方面是传递参数，另一方面是调用规约。传递参数通过执行一些环境变量（针对特定cmd的环境变量，不是操作系统全局的），并将网络配置的json形式作为标准输入。环境变量有：
* CNI_COMMAND: 指定需要执行的动作：ADD, DEL, CHECK, or VERSION.
* CNI_CONTAINERID: Container ID. 
* CNI_NETNS: 容器net namespace。 a path to the network namespace (e.g. /run/netns/\[nsname])
* CNI_IFNAME: 容器内部要创建的虚拟机网络设备名字.
* CNI_ARGS: Extra arguments passed in by the user at invocation time. Alphanumeric key-value pairs separated by semicolons; for example, "FOO=BAR;ABC=123"
* CNI_PATH: List of paths to search for CNI plugin executables. Paths are separated by an OS-specific list separator; for example ':' on Linux and ';' on Windows

以docker为例，dockershim会通过SetupPod方法传入上述环境变量，并进行CNI插件的调用。

从 `flannel-cni` 的实现来看（其实是 cni 的实现，flannel-cni 调用了 cni 的库），其上述六个参数是通过环境变量[https://github.com/containernetworking/cni/blob/main/pkg/skel/skel.go#L57](https://github.com/containernetworking/cni/blob/main/pkg/skel/skel.go#L57)来读取的。

##### Execution of Network Configurations
容器运行时如何识别网络配置，以及根据配置执行插件。

##### Plugin Delegation
创建委托机制，调用另一个插件来做事情，比如flannel插件靠bridge设备来建立虚拟网桥。

这里 `flannel-cni` 的实现可以参考[https://github.com/flannel-io/cni-plugin/blob/main/flannel_linux.go#L75](https://github.com/flannel-io/cni-plugin/blob/main/flannel_linux.go#L75)
```go
	if !hasKey(n.Delegate, "type") {
		n.Delegate["type"] = "bridge"
	}

	if !hasKey(n.Delegate, "ipMasq") {
		// if flannel is not doing ipmasq, we should
		ipmasq := !*fenv.ipmasq
		n.Delegate["ipMasq"] = ipmasq
	}
```
其委托调用的代码为[https://github.com/flannel-io/cni-plugin/blob/6e8bb11373c7743a00571a52d4f27ce7c07256a1/flannel.go#L198](https://github.com/flannel-io/cni-plugin/blob/6e8bb11373c7743a00571a52d4f27ce7c07256a1/flannel.go#L198)

##### Result Types
插件返回结果的形式 

#### CNI实现
首先要实现网络方案本身。以Flannel为例，要维护VTEP设备的MAC地址，目的主机的ip地址等，具体有创建flannel.1设备、配置宿主机路由、配置ARP和FDB表里的信息等。

然后实现该网络方案对应的CNI插件，这一部分要做的就是配置Infra容器里的网络栈，并把它连在CNI网桥上。


#### 参考：

[Kubernetes CNI具体流程和Flannel原理探究](https://blog.csdn.net/weixin_40864891/article/details/106172717)

[Linux虚拟网络设备之tun/tap](https://segmentfault.com/a/1190000009249039)

[云计算底层技术-虚拟网络设备(tun/tap,veth)](https://opengers.github.io/openstack/openstack-base-virtual-network-devices-tuntap-veth/)

[Linux虚拟网络设备之veth](https://segmentfault.com/a/1190000009251098)

[配置网络插件flannel](https://jkzhao.github.io/2019/09/16/%E9%85%8D%E7%BD%AE%E7%BD%91%E7%BB%9C%E6%8F%92%E4%BB%B6flannel/)

[vxlan 协议原理简介](https://cizixs.com/2017/09/25/vxlan-protocol-introduction/)

[vxlan文档](https://tools.ietf.org/html/rfc7348)