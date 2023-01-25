---
layout:     post
title:      "扩展 IPAM host-local 插件实现 IP 预留"
subtitle:   "\"Openkruise 中实现 IP 预留的方式\""
date:       2022-8-20 10:10:00
author:     "decent"
header-img-credit: false
tags:
    - 网络
---

OpenKruise 在其文档 [OpenKruise v1.2：新增 PersistentPodState 实现有状态 Pod 拓扑固定与 IP 复用](https://openkruise.io/zh/blog/openkruise-1.2/) 中说实现了 IP 预留的功能，这里看下是怎么实现的。其实实现这个功能不麻烦，很早以前我们团队也自己实现了这个功能，当时也是把 ip 放在了磁盘上，不过我那个时候对网络不了解，现在通过这些细小的知识点一点一点积累网路知识。

[host-local](https://www.cni.dev/plugins/current/ipam/host-local/) 是一种 ipam(IP Address Management)插件，用于从一个 ip range 中分配一个 ip，这个是 flannel 网络模式默认使用的 ipam 插件，另一个与之对应的插件是 dhcp，这个局域网用户应该都比较清楚了。 host-local 分配的 ip 会以文件的方式放在磁盘上。文件名为分配的 ip 地址，文件内容是容器的Id以及容器内接口的名字。例子如下，有两个 pod，ip 分别为 `10.244.0.93` `10.244.0.92`，host-local 为其保存的文件及文件内容如下：
```s
[decent@HostGW-Master networks]$ kubectl get pods -o wide
NAME                                READY   STATUS    RESTARTS   AGE    IP             NODE            NOMINATED NODE   READINESS GATES
nginx-deployment-66b6c48dd5-8qprm   1/1     Running   0          138m   10.244.0.93    hostgw-master   <none>           <none>
nginx-deployment-66b6c48dd5-gbt9z   1/1     Running   0          138m   10.244.0.92    hostgw-master   <none>           <none>
[decent@HostGW-Master cbr0]$ pwd
/var/lib/cni/networks/cbr0
[decent@HostGW-Master cbr0]$ cat 10.244.0.93
63a6abeb5090423f78ba2fbda9fa094bfec9dcf2270510d91f8f11b4e70d4e81
eth0
[decent@HostGW-Master cbr0]$ cat 10.244.0.92
6a054a83a2be15cc648e9eb15a9998cabfaeacea1e971f620c28337f13c13660
eth0
[decent@HostGW-Master cbr0]$
```
host-local 插件保存已分配 ip 的默认目录为 `/var/lib/cni/networks/`，这个对应插件配置中的 IPAMConfig.DataDir 字段，是个可配置选项，不过这个配置一般不会变动。除了上面那个目录，还有一个 `/var/lib/cni/flannel` 目录也保存了一些网络数据，这个目录是 flannel 用来存放 delegate 给 bridge 插件的数据，这个目录的数据如下(文件名为容器 ID，文件内容为 bridge 插件配置)：
```s
[root@HostGW-Master flannel]# pwd
/var/lib/cni/flannel
[root@HostGW-Master flannel]# ls
63a6abeb5090423f78ba2fbda9fa094bfec9dcf2270510d91f8f11b4e70d4e81  6a054a83a2be15cc648e9eb15a9998cabfaeacea1e971f620c28337f13c13660
[root@HostGW-Master flannel]# cat 63a6abeb5090423f78ba2fbda9fa094bfec9dcf2270510d91f8f11b4e70d4e81
{"cniVersion":"0.3.1","hairpinMode":true,"ipMasq":false,"ipam":{"ranges":[[{"subnet":"10.244.0.0/24"}]],"routes":[{"dst":"10.244.0.0/16"}],"type":"host-local"},"isDefaultGateway":true,"isGateway":true,"mtu":1450,"name":"cbr0","type":"bridge"}
```

这里再补充点 flannel 插件工作方式，flannel cni 插件其实没做什么实质性的工作（这里指 cni binary，区别于每个节点上运行的 flanneld daemonset），其虚拟网络设备的配置都 delegate 给 bridge 插件来做了，ipam 作为 bridge 配置的一部分（需要获取 ip 并配置 ip），默认是由 host-local 插件做的，大体工作模式可以用下图表示。
![java-javascript](/pics/host-local-ipam.png){:height="65%" width="65%"}


### IP 预留中的几个关键问题
这里根据 Openkruise 给出的[例子程序](https://github.com/openkruise/samples)，尝试分析下 IP 预留重点几个关键问题，但对细节不会过多分析。具体问题有：1）怎么实现 ip 预留的；2）怎么知道要不要预留；3）预留的 ip 什么时候回收。

OpenKruise 实现 ip 预留有个前提条件，pod 必须要调度到之前调度过的节点，如果 pod 带有本地 pv（静态 pv），这个条件是默认满足的，但是没有 pv 就不一定能调度了，关于怎么保证调度到之前已经调度过的节点，这个不在本文考虑的范畴（OpenKruise 是通过crd 做的）。

#### 实现 ip 预留
跟文章一开始介绍的方式类似， Openkruise 是将分配的 ip 写到了磁盘上，目录为 `/var/lib/cni/networks/cbr0`，跟上面目录一致，文件的目录格式为：`path.Join(dir, "reserved", podNamespace, podName)`，每个 namespace 有单独的目录，以 podName 为文件名。存放的数据为下面结构体的json 序列化：
```go
type ReservedInfo struct {
	ContainerId string `json:"containerId"`
	IfName string `json:"ifName"`
	IPConf *current.IPConfig `json:"IPConf"`
	//reserved time duration
	Duration time.Duration `json:"duration"`
	ReleaseTime *time.Time `json:"releaseTime,omitempty"`
}

type IPConfig struct {
	// Index into Result structs Interfaces list
	Interface *int
	Address   net.IPNet
	Gateway   net.IP
}
```
注意上面的 `ContainerId` 是有可能会变化的，比如 pod 重启，但是只要 pod 的 namespace 和 name 没有变化就行。每次要给 pod 分配 ip 时，首先根据 namespace 和 name 查找有无已经预留的 ip，有则返回，没有则继续分配。

#### 怎么知道要不要预留
pod 有 annotation 标识要不要预留 ip，但是要将这个 annotation 传递到插件中，Openkruise 没说怎么做（从代码中看，是从 ipamConfig 中取的 pod annotation，由此可见可能扩展了 bridge 插件），但是这个可以通过在 Kubelet 中扩展 `RuntimeConf` 的 Args 来做（这个需要扩展 Kubelet，还是有一点代价的），在调用插件时，这些 args 会通过 `CNI_ARGS` 这个环境变量传递给插件。例子如下，代码文件为 `kubernetes/pkg/kubelet/dockershim/network/cni/cni.go` (以 dockershim 为例)
```go
func (plugin *cniNetworkPlugin) addToNetwork(ctx context.Context, network *cniNetwork, podName string, podNamespace string, podSandboxID kubecontainer.ContainerID, podNetnsPath string, annotations, options map[string]string) (cnitypes.Result, error) {
	rt, _ := plugin.buildCNIRuntimeConf(podName, podNamespace, podSandboxID, podNetnsPath, annotations, options)

	// 添加自定义参数给 cni
	if val, ok := annotations["自定义annotation"]; ok {
		rt.Args = append(rt.Args, [2]string{"自定义参数", val})
	}

	pdesc := podDesc(podNamespace, podName, podSandboxID)
	netConf, cniNet := network.NetworkConfig, network.CNIConfig
	res, _ := cniNet.AddNetworkList(ctx, netConf, rt)
	return res, nil
}
```

#### 预留的 ip 什么时候回收
在 annotation 中配置超时时间，这个超时时间是指从 ip 释放后的超时时间，pod 正在运行，那当然不能回收 ip 了。比如配置 1天。一般一个节点的 pod 上限是 110 个，一个 ip range 有 255 个，可以配置几小时，这个看业务需要。每次有申请新的 ip 时，首先看看有没有旧的 ip 可以释放，有的话就释放。

总结，大概了解了下预留 ip 需要做哪些事情，知道思路即可，在面对具体问题时再具体分析。

### 参考

host-local 官方文档为 [host-local IP address management plugin](https://www.cni.dev/plugins/current/ipam/host-local/)，

host-local 代码仓库： [https://github.com/containernetworking/plugins/tree/main/plugins/ipam/host-local](https://github.com/containernetworking/plugins/tree/main/plugins/ipam/host-local)

[OpenKruise 例子程序](https://github.com/openkruise/samples)