---
layout:     post
title:      "CNI实现：flanneld hostgw backend 相关实现"
date:       2023-2-11 10:10:00
author:     "decent"
header-img-credit: false
tags:
    - 网络
---
- [flanneld 配置参数](#flanneld-配置参数)
- [flanneld 启动流程](#flanneld-启动流程)
- [hostgw backend 的实现](#hostgw-backend-的实现)
- [在 flanneld 中自定义插件](#在-flanneld-中自定义插件)

flanneld 中的 backend 类似网络框架中的控制层面，会根据网络模型在节点配置相应的路由规则和 iptables 规则。常见的 backend 有 udp、vxlan、hostgw 等。本文看下 flanneld 中 hostgw backend 的实现，因为这个比较简单。

### flanneld 配置参数
在使用 [kubeadm 安装完一个 K8s 集群](https://loverhythm1990.github.io/2022/08/26/kubeadm-k8s/) 之后，需要安装一个网络插件，对于 flannel 来说，可以通过 [coreos 提供的 yaml](https://raw.githubusercontent.com/coreos/flannel/master/Documentation/kube-flannel.yml) 来安装。在这个 yaml 文件中主要有下面几部分配置：
* RBAC 配置，用于生成 InClusterConfig，flanneld 要跟 kube-apiserver 通信。
* 网路配置：网络配置放在一个 ConfigMap 中，分两部分：1）一个是 CNI 插件配置，供 kubelet 消费，这个配置是要符合 CNI 规范的，放在主机 `/etc/cni/net.d/` 目录；2）另一个是 flannel 的网络配置，包括集群的网段和 flannel 的后端配置，假设配置后端类型为 `host-gw`，网络配置示例如下。
```s
net-conf.json: |
  {
    "Network": "10.244.0.0/16",
    "Backend": {
      "Type": "host-gw"
    }
  }
```
* flanneld daemonset：每个节点上都运行的 daemon，这个 daemon 有两个 initContainer，分别用于拷贝 flannel 二进制插件和配置文件到 kubelet 指定的目录下面（默认为`/opt/cni/bin` 目录和 `/etc/cni/net.d` 目录）。主 container 的启动参数为：`/opt/bin/flanneld --ip-masq --kube-subnet-mgr`，其中 `kube-subnet-mgr` 为启动一个 subnetmanager，如果不指定这个选项，需要配置 etcd 作为存储后端，subnetmanager 直接消费集群中的 `v1.Node` 资源管理子网，主要看 node.Spec 中的 `podCIDR` 配置。这个是 K8s 中的 ControllerManager 分配的。

### flanneld 启动流程
flanneld 的启动流程相对比较清晰，大概的流程步骤可以参考下面代码。总结一下，主要工作有：
* 初始化 flanneld 相关数据结构，包括 subnetmanager、backendmanager 等，前者主要是要启动一个 Node informer，并在 informer 事件处理中将 Node 资源转换为 lease 资源(flanneld 中定义的一个数据结构)，供 backend 创建的 Network 消费。
* 根据网络配置初始化特定的 backend：调用特定 backend 的 `RegisterNetwork` 方法，并启动其返回的 Network（调用其 Run 方法）。
* 写子网配置到 `/var/run/flannel/subnet.env` 文件中，供 flannel 插件消费。
* 配置 forward iptables 规则，即下面的规则，这两个规则每个节点都一样。
```s
sudo iptables -A FORWARD -s 10.244.0.0/16 -j ACCEPT
sudo iptables -A FORWARD -d 10.244.0.0/16 -j ACCEPT 
```

大体流程可以看下面代码：
```go
import (
	// 每个 backend 都是在 init 函数中注册自己的
	_ "github.com/flannel-io/flannel/backend/hostgw"
	// ...
)
func main() {
	// 初始化一个 subnetManager，这个 subnetManager 会启动一个 node 的 informer，监听所有的 node 事件
	sm, _ := newSubnetManager(ctx)

	// 获取 flanneld 网络配置，就两个：集群网段、backend 类型
	config, _ := getConfig(ctx, sm)

	// 初始化一个 backendManager
	bm := backend.NewManager(ctx, sm, extIface)
	// 获取特定的 backend
	be, _ := bm.GetBackend(config.BackendType)
	// 调用特定 backend 的 RegisterNetwork 方法
	bn, _ := be.RegisterNetwork(ctx, &wg, config)

	// 配置 ip forword rule
	if opts.iptablesForwardRules {
		log.Infof("Changing default FORWARD chain policy to ACCEPT")
		go network.SetupAndEnsureIPTables(network.ForwardRules(config.Network.String()), opts.iptablesResyncSeconds)
	}
	// 写配置文件 /var/run/flannel/subnet.env，供 flannel 插件消费
	if err := WriteSubnetFile(opts.subnetFile, config.Network, opts.ipMasq, bn); err != nil {
    	// handle err
	}
	wg.Add(1)
	go func() {
		// 启动 Backend 返回的 Network。
		bn.Run(ctx)
		wg.Done()
	}()
}
```

### hostgw backend 的实现
参考《[手动配置 K8s hostgw 网络](https://loverhythm1990.github.io/2022/10/01/hostgw-by-hand/)》的描述，hostgw 做的主要是**在一个节点，配置到每一个其他节点的路由**，所以实现相对简单，看下 hostgw 后端的实现。

在 hostgw 后端实现中，返回的是 `RouteNetwork`，该 `RouteNetwork` 消费 lease 事件（Node事件），并配置相应的路由策略。下面 `RegisterNetwork` 是每个 backend 插件都需要实现的方法。

```go
func (be *HostgwBackend) RegisterNetwork(ctx context.Context, wg *sync.WaitGroup, config *subnet.Config) (backend.Network, error) {
	// 返回 RouteNetwork
	n := &backend.RouteNetwork{
		SimpleNetwork: backend.SimpleNetwork{
			ExtIface: be.extIface,
		},
		SM:          be.sm,
		BackendType: "host-gw",
		Mtu:         be.extIface.Iface.MTU,
		LinkIndex:   be.extIface.Iface.Index,
	}
	// 根据 lease 生成一条路由
	n.GetRoute = func(lease *subnet.Lease) *netlink.Route {
		return &netlink.Route{
			Dst:       lease.Subnet.ToIPNet(),
			Gw:        lease.Attrs.PublicIP.ToIP(),
			LinkIndex: n.LinkIndex,
		}
	}
	attrs := subnet.LeaseAttrs{
		PublicIP:    ip.FromIP(be.extIface.ExtAddr),
		BackendType: "host-gw",
	}

	// 配置 node annotation
	l, _ := be.sm.AcquireLease(ctx, &attrs)
	return n, nil
}

// RouteNetwork 在其 Run 方法中处理 lease 事件，并添加相应路由策略。
func (n *RouteNetwork) handleSubnetEvents(batch []subnet.Event) {
	for _, evt := range batch {
		switch evt.Type {
		case subnet.EventAdded:
			route := n.GetRoute(&evt.Lease)
			n.addToRouteList(*route)
			if len(routeList) > 0 && routeEqual(routeList[0], *route) {
				// 添加路由策略
			} else if err := netlink.RouteAdd(route); err != nil {
				continue
			}

		case subnet.EventRemoved:
			route := n.GetRoute(&evt.Lease)
			n.removeFromRouteList(*route)

			// 删除路由策略
			if err := netlink.RouteDel(route); err != nil {
				log.Errorf("Error deleting route to %v: %v", evt.Lease.Subnet, err)
				continue
			}
		default:
			log.Error("Internal error: unknown event type: ", int(evt.Type))
		}
	}
}
```
hostgw 后端实现总体比较简单，就是监听集群中的节点添加删除事件，并进行路由策略的添加和删除。如果把路由配置这个功能看做是 flanneld 提供的默认能力的话，hostgw 插件总体实现代码也就几十行。另外补充一下，flanneld 添加的 Node annotation 长啥样：
![java-javascript](/pics/flanneld_backend.jpg){:height="60%" width="60%"}

### 在 flanneld 中自定义插件
如果我们要在 flanneld 中自定义一个插件的话，也可以遵循 flanneld 的设计模式：1）通过 init 方法注册插件；2）实现 New 方法；3）实现 RegisterNetwork。
```go
func init() {
	backend.Register("my-plugin", New)
}

type MyBackend struct {
	sm       subnet.Manager
	extIface *backend.ExternalInterface
}

func New(sm subnet.Manager, extIface *backend.ExternalInterface) (backend.Backend, error) {
	// 实现 New 方法
}

func (be *HostgwBackend) RegisterNetwork(ctx context.Context, wg *sync.WaitGroup, config *subnet.Config) (backend.Network, error) {
	// 实现 RegisterNetwork 方法
}
```