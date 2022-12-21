---
layout:     post
title:      "CNI实现-使用Golang配置网络设备"
date:       2022-12-20 10:10:00
author:     "decent"
header-img-credit: false
tags:
    - 网络
---

### 基础知识 MTU vs MSS
MTU(Maximum Transmission Unit，最大传输单元)，是数据链路层的概念，限制了数据链路层上可以传输的数据包的大小，也因此限制了上层（网络层）的数据包大小，MTU 这个大小包含了IP header 以及 TCP header，MTU一般由硬件规定，如以太网的MTU为1500字节。当IP报文的大小超过MUT时，IP报文会被分片，并在对端被重组。如果配置了不能分片，则超过MTU大小的报文会被丢弃。默认情况下，我们将网卡的MTU设置为1500即可，但是如果是 overlay 网络，则需要考虑 overlay 封装的开销，需要在1500bytes的基础上减去开销，比如vxlan的mtu设置为 1450 bytes。

MSS(maximum segment size，最大报文长度)，mss 是 TCP header option 字段的一个配置项，表示tcp可传输的最大 payload，不包括 tcp header，在 tcp 三次握手过程中，双方会通报自己这边的 MSS 大小，并选定一个最小的使用 [stack-mss](https://networkengineering.stackexchange.com/questions/33574/is-mss-negotiated-or-exchanged-during-the-3-way-handshake)。
![java-javascript](/img/in-post/all-in-one/Mtu.png){:height="50%" width="50%"}
一般来说: `MTU = MSS + 40 (IP header + TCP header)`

### 通过 netlink 创建网卡
Netlink套接字是实现用户进程与内核进程通信的一种特殊的进程间通信机制(IPC)，也是网络应用程序与内核通信的最常用的接口。golang 中 [netlink](https://github.com/vishvananda/netlink)项目，可以完成一系列网络配置操作，如创建网卡、设置IP等。下面介绍一些示例 API 以及参数配置：
* netlink.LinkAdd: 添加一个网桥，其参数为 `netlink.Bridge`，示例如下：
```go
br := &netlink.Bridge{
	LinkAttrs: netlink.LinkAttrs{
		Name: brName, // bridge 名字
		MTU:  mtu,    // mtu
		//TxQLen: -1, // 发送队列长度
	},
}
// 添加以网卡，并忽略 "已存在" 的错误
err := netlink.LinkAdd(br)
if err != nil && err != syscall.EEXIST {
	return nil, fmt.Errorf("could not add %q: %v", brName, err)
}
```
添加网卡操作是可以看出是幂等的，如返回错误`syscall.EEXIST`，忽略即可。
* netlink.LinkByName: 通过名字获取一个网桥，返回的数据结构也是 `netlink.Bridge` 主要是网桥配置的一些参数。

```go
func GetBridgeByName(name string) (*netlink.Bridge, error) {
	l, err := netlink.LinkByName(name)
	if err != nil {
		return nil, fmt.Errorf("could not find %q: %v", name, err)
	}
	br, ok := l.(*netlink.Bridge)
	if !ok {
		return nil, fmt.Errorf("%q illegal type", name)
	}
	return br, nil
}

// 网桥配置参数
// LinkAttrs represents data shared by most link types
type LinkAttrs struct {
	Index        int
	MTU          int
	TxQLen       int               // 传输队列长度，Transmit Queue Length
	Name         string            // 名字
	HardwareAddr net.HardwareAddr  // 硬件地址
	Flags        net.Flags
    // ... ...
}
```

* netlink.LinkSetUp: 启用网桥，作用跟 `ip link set $link up` 一致的。

```go
if err := netlink.LinkSetUp(br); err != nil {
	return nil, err
}
```

其实上面操作，跟下面 shell 一致，用 shell 看起来简单多了。
```s
ip link add name br0 type bridge
ip link set br0 up
```

### 配置 veth 设备
在创建 veth 设备之前，首先需要知道容器的network namespace，这样才能在里面安置一个 veth 设备。在 cni 中，这个 network namespace 是 `libcni.RuntimeConf` 运行时配置的一部分，并且作为环境变量传递到 cni 二进制文件中。
```go
rt := &libcni.RuntimeConf{
	ContainerID: podSandboxID.ID,
	NetNS:       podNetnsPath, // 网络 namespace
	IfName:      network.DefaultInterfaceName, // 容器内部网卡名，默认 eth0
	CacheDir:    plugin.cacheDir,
	Args: [][2]string{
		{"IgnoreUnknown", "1"},
		{"K8S_POD_NAMESPACE", podNs},
		{"K8S_POD_NAME", podName},
		{"K8S_POD_INFRA_CONTAINER_ID", podSandboxID.ID},
	},
}
```
dockershim 在生成 ns 时，首先对 pause 容器调用 docker inspect，在 inspect 的输出的 State 字段拿到容器的进程id。然后通过 `/proc/<pid>/ns/net` 的方式得到容器的网络 namespace。

为了配置网络设备的 net namespace，cni 在 [plugin](github.com/containernetworking/plugins/pkg/ns/ns.go)标准库中定义了一个接口 NetNS，同时其实现者为 `netNS` 结构体，这个结构体的核心字段是一个文件指针，这个文件就是代表进程namespace 的文件，如`/proc/<pid>/ns/net` 等，实现者 `netNS` 代表的就是一个网络 namespace。
```go
type NetNS interface {
	// 在当前 net namespace 中运行一个闭包函数，这个闭包函数也有个参数 NetNS
	// 在实际调用这个 toRun 方法时，这个参数 NetNS 是 host ns，这个参数用于设置一对 veth 之后
	// 将一个 veth 设备置于 hostns。
	Do(toRun func(NetNS) error) error
	// 将当前线程设置到 netNS 所代表的 namespace
	Set() error
	Path() string
	Fd() uintptr
	Close() error
}
// 接口实现者，代表一个 net namespace
type netNS struct {
	file    *os.File
	mounted bool
	closed  bool
}
```
我觉得有必要研究一下这个 `NetNS` 的实现细节，涉及到 golang 操作namespace 的一些细节。
#### NetNS 接口的实现细节
上面大概介绍了 NetNS 的一些情况，下面看下 `netNS` 如何实现这个接口的，以便更好的理解系统的行为，我们首先看 `Set() error` 的实现。
```go
func (ns *netNS) Set() error {
	if err := ns.errorIfClosed(); err != nil {
		return err
	}
	// 调用 setns 系统调用，将当前 goroutine 对应的线程加入到 fd 所表示的 namespace 中。
	// 线程对应的 ns 路径为 /proc/[pid]/task/[threadid]/ns
	if err := unix.Setns(int(ns.Fd()), unix.CLONE_NEWNET); err != nil {
		return fmt.Errorf("Error switching to ns %v: %v", ns.file.Name(), err)
	}
	return nil
}
```
Set 实现比较简单，需要时 setns 系统调用，下面我们重点看一下 Do 方法的实现。

首先在一般情况下，当函数或闭包作为方法参数时，闭包本身可能也需要参数，但是在方法中，一般是作为闭包参数的生产者，闭包参数的消费者是调用该方法时决定的。在本例中，下面的 `Do` 方法是闭包参数中`NetNS`的生产者，调用 Do 的地方是参数 NetNS 的消费者。

Do 方法的本意是`在一个特定的 net namespace 中执行一个闭包`，并且执行完要切回原来的 namespace，也就是只要闭包的代码是需要在特定的 net namespace 中执行的，所以在下面的实现中，在闭包外面由做了一层 warpper，warpper 的作用就是执行闭包前进入 namespace，执行闭包后切换回原来的 namespace。

`GetCurrentNS()` 方法是拿到当前 thread 的 namespace，具体是通过 `/proc/<os.Getpid()>/task/<unix.Gettid()>/ns/net` 拿到的，其中 `/proc/<os.Getpid()>/ns`表示的是进程 master thread 的 namespace，因为golang 中，goroutine 跟 thread 的绑定关系是一直变化的，这里为了得到一个稳定的 namespace，所以用了当前线程的 namespace，那为什么当前线程的 namespace 是稳定的呢？因为在调用这个 warpper 闭包时，在其 goroutine 中调用了 `runtime.LockOSThread()`，将线程和 goroutine 绑定。
```go
func (ns *netNS) Do(toRun func(NetNS) error) error {
	// Do 首先在传入的闭包外面又包了一层 warpper
	containedCall := func(hostNS NetNS) error {
		threadNS, err := GetCurrentNS()
		if err != nil {
			return fmt.Errorf("failed to open current netns: %v", err)
		}
		defer threadNS.Close()

		// 切入到目标 net namespace
		if err = ns.Set(); err != nil {
			return fmt.Errorf("error switching to ns %v: %v", ns.file.Name(), err)
		}
		// 闭包执行完之后，切回thread 原namespace
		defer threadNS.Set() // switch back
		// 执行闭包
		return toRun(hostNS)
	}
	// 生产闭包参数 NetNS，这个参数在闭包内部消费
	// save a handle to current network namespace
	hostNS, err := GetCurrentNS()
	if err != nil {
		return fmt.Errorf("Failed to open current namespace: %v", err)
	}
	defer hostNS.Close()

	// 通过 waitGroup 等待闭包执行完，体贴
	var wg sync.WaitGroup
	wg.Add(1)

	var innerError error
	go func() {
		defer wg.Done()
		runtime.LockOSThread()  // 锁定线程和goroutine 的绑定关系，避免调度器切换
		innerError = containedCall(hostNS)
	}()
	wg.Wait()
	return innerError
}
```
上面是 Do 的实现，分析完还是学到了不少东西，总的来说，这个函数的作用就是在特定的 net namespace 内执行一个闭包，执行完之后再切回原 namespace，

#### 生成 veth 设备，并配置 namespace
上面分析了 `netns.Do`的实现，现在我们看下，怎么通过其生成 veth 设备，并配置 namespace，代码如下，我们看到，也是调用了 cni plugin 项目中的一个`ip`库，这个方法有三个参数：
* ifName：容器中的网卡的 name
* mtu: 一般是 1500 bytes
* hostNS: 注意的network namespace，veth 对中，一个要在容器 namespace，一个要在宿主机 namespace
```go
err := netns.Do(func(hostNS ns.NetNS) error {
	vethInHost, vethInContainer, err := ip.SetupVeth(ifName, mtu, hostNS)
	if err != nil {
		return err
	}
	containerIface.Name = vethInContainer.Name
	containerIface.Mac = vethInContainer.HardwareAddr.String()
	containerIface.Sandbox = netns.Path()
	hostIface.Name = vethInHost.Name
	return nil
})
```
理论上我们只需要调用 `ip.SetupVeth`这个方法就好了，这个方法会返回给我们容器中的 veth 和 宿主机的 veth，不过本着学习的态度，我们再研究一下这个方法的实现。

首先是 `makeVeth` 这个方法是生成 veth 对，此时还没有配置namesapce，只是生成了设备，并返回了名字。netlink 包中有 Veth 对应的结构体，配置一下调用`LinkAdd`就可以了，这个方法是同时生成两个设备。
```go
veth := &netlink.Veth{
	LinkAttrs: netlink.LinkAttrs{
		Name:  name,
		Flags: net.FlagUp,
		MTU:   mtu,
	},
	PeerName: peer,
}
if err := netlink.LinkAdd(veth); err != nil {
	return nil, err
}
```

然后再看一下 `netlink.LinkSetNsFd`这个是配置 veth 的namespace，其作用是 `ip link set $link netns $ns`，这个时候，我们的闭包参数 hostNS 起作用了，后面再就是`netNS.Do()` 其作用就是在宿主机 namespace启用 veth。
```go
func SetupVeth(contVethName string, mtu int, hostNS ns.NetNS) (net.Interface, net.Interface, error) {
	hostVethName, contVeth, err := makeVeth(contVethName, mtu)
	if err != nil {
		return net.Interface{}, net.Interface{}, err
	}
	// ip link set $link up
	if err = netlink.LinkSetUp(contVeth); err != nil {
		return net.Interface{}, net.Interface{}, fmt.Errorf("failed to set %q up: %v", contVethName, err)
	}
	hostVeth, err := netlink.LinkByName(hostVethName)
	if err != nil {
		return net.Interface{}, net.Interface{}, fmt.Errorf("failed to lookup %q: %v", hostVethName, err)
	}
	// ip link set $link netns $ns
	if err = netlink.LinkSetNsFd(hostVeth, int(hostNS.Fd())); err != nil {
		return net.Interface{}, net.Interface{}, fmt.Errorf("failed to move veth to host netns: %v", err)
	}
	err = hostNS.Do(func(_ ns.NetNS) error {
		hostVeth, err = netlink.LinkByName(hostVethName)
		if err != nil {
			return fmt.Errorf("failed to lookup %q in %q: %v", hostVethName, hostNS.Path(), err)
		}
		if err = netlink.LinkSetUp(hostVeth); err != nil {
			return fmt.Errorf("failed to set %q up: %v", hostVethName, err)
		}
		return nil
	})
	if err != nil {
		return net.Interface{}, net.Interface{}, err
	}
	return ifaceFromNetlinkLink(hostVeth), ifaceFromNetlinkLink(contVeth), nil
}
```
生成 veth 对之后，主机 namespace 的 veth 设备还需要做两件事： 
* 挂到 bridge 上，通过 `netlink.LinkSetMaster(hostVeth, br)` 方法就可以了，其作用类似 `ip link set $link master $master`
* 设置 HairPin 模式，通过 `netlink.LinkSetHairpin(hostVeth, hairpinMode)` 方法。

上面整个过程可以用下面脚本表示，总是没有复杂的逻辑，需要了解相关工具的使用。
```s
#1. 创建 veth0 和 veth1 设备，创建之后，通过 ip link list 能看到这两个设备
ip link add veth0 type veth peer name veth1
# 2. 将 veth0 设备设为启用
ip link set dev veth0 up
# 2.5 如果想给 veth0 配置 ip，可以用下面命令
# ifconfig veth0 10.20.30.40/24
# 3. 设置 namespace
ip link set veth1 netns <namespace>
# 4. 将宿主机的 veth1 设备绑定到网卡上
ip link set dev veth1 master br0
```

至此，大概梳理了使用 golang 实现网络配置的一些实现细节，作为实现自定义插件的参考

### 参考

[linux netlink通信机制简介](https://www.cnblogs.com/still-smile/p/11585468.html)

[netlink 是 Go 和内核模块之间优秀的通信兵](https://asphaltt.github.io/post/netlink-and-go/)

[Linux下的网卡速度提升方案](http://kuring.me/post/linux_netcard_promote/)，这篇文章提供了一些网络优化思路，可以细看一下，比如：
* 调整网卡的txqueuelen：txqueuelen的含义为网卡的发送队列长度，可以通过ifconfig命令找到网卡的txqueuelen参数配置，默认为1000，建议将其更改为5000。
```s
[decent@ubuntu ~]$ ifconfig br0
br0: flags=4163<UP,BROADCAST,RUNNING,MULTICAST>  mtu 1500
        inet 10.72.220.77  netmask 255.255.255.0  broadcast 10.72.220.255
        ether 00:22:43:60:ed:dc  txqueuelen 1000  (Ethernet)
        RX packets 752035605  bytes 177528801406 (165.3 GiB)
        RX errors 0  dropped 0  overruns 0  frame 0
        TX packets 642965183  bytes 274365005779 (255.5 GiB)
        TX errors 0  dropped 0 overruns 0  carrier 0  collisions 0
```

[记一次问题排查：为什么在POD无法通过Service访问自己？](https://silenceper.com/blog/202004/bridge-hairpin-mod/)，hairpin-mode 可以在容器内部访问自己，比如在容器内部访问自己的 service，promiscuous 混杂模式也可以。

[浅谈Linux Namespace机制（一）](https://zhuanlan.zhihu.com/p/73248894)提到了linux namespace 机制中三个非常重要的系统调用：fork/setns/unshare，其中 setns 是将当前进程加入到一个新的 namespace 中去，[搞懂容器技术的基石：namespace](https://moelove.info/2021/12/13/%E6%90%9E%E6%87%82%E5%AE%B9%E5%99%A8%E6%8A%80%E6%9C%AF%E7%9A%84%E5%9F%BA%E7%9F%B3-namespace-%E4%B8%8B/#setns2) 中提到，调用 `setns` 系统调用的时候，会导致进程 `/proc/[pid]/ns` 对应的目录中内容发生变化。在我们的常用的linux 工具 `nsenter` 中，通过 strace 工具查看，会发现其也是调用了 setns 系统调用来切换 namespace。
```s
# strace nsenter -t 27242 -i -m -n -p -u /bin/bash
execve("/usr/bin/nsenter", ["nsenter", "-t", "27242", "-i", "-m", "-n", "-p", "-u", "/bin/bash"], [/* 21 vars */]) = 0
…………
…………
pen("/proc/27242/ns/ipc", O_RDONLY)    = 3
open("/proc/27242/ns/uts", O_RDONLY)    = 4
open("/proc/27242/ns/net", O_RDONLY)    = 5
open("/proc/27242/ns/pid", O_RDONLY)    = 6
open("/proc/27242/ns/mnt", O_RDONLY)    = 7
setns(3, CLONE_NEWIPC)                  = 0
close(3)                                = 0
setns(4, CLONE_NEWUTS)                  = 0
close(4)                                = 0
setns(5, CLONE_NEWNET)                  = 0
close(5)                                = 0
setns(6, CLONE_NEWPID)                  = 0
close(6)                                = 0
setns(7, CLONE_NEWNS)                   = 0
close(7)                                = 0
clone(child_stack=0, flags=CLONE_CHILD_CLEARTID|CLONE_CHILD_SETTID|SIGCHLD, child_tidptr=0x7f4deb1faad0) = 4968
```
