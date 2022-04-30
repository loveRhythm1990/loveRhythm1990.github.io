---
layout:     post
title:      "Etcd 安装、部署、测试"
date:       2021-08-28 10:10:00
author:     "weak old dog"
header-img-credit: false
tags:
    - etcd
---

#### 安装部署

从 [https://github.com/etcd-io/etcd/releases/v3.4.3](https://github.com/etcd-io/etcd/releases/v3.4.3) 下载 etcd 二进制文件，放到 PATH 下面。

下载 [https://github.com/etcd-io/etcd/blob/v3.4.3/Procfile](https://github.com/etcd-io/etcd/blob/v3.4.3/Procfile) 下载 Procfile 文件，这个文件是 goreman 用来启动 etcd 的配置文件。

下载 goreman，命令为：`go get github.com/mattn/goreman`

使用 goreman 启动本地 etcd 集群: `goreman -f Procfile start`

#### 测试

etcdctl put hello world --endpoints http://127.0.0.1:2379

etcdctl get hello --endpoints http://127.0.0.01:2379

K8s 集群中访问 etcd:
etcdctl --endpoints=https://172.x.x.x:2379,https://172.x.x.x:2379,https://172.x.x.x:2379 --cert=kube-etcd-172-x-x-x.pem --key=kube-etcd-172-x-x-x-key.pem --cacert=kube-ca.pem member list

官方测试数据：[https://etcd.io/docs/v3.4/op-guide/performance/](https://etcd.io/docs/v3.4/op-guide/performance/)

#### clientv3 客户端使用
```go
package main

import (
	"context"
	"fmt"
	clientv3 "go.etcd.io/etcd/client/v3"
	"time"
)

func main() {
	var (
		config clientv3.Config
		client *clientv3.Client
		err error
		ctx = context.TODO()
	)
	config = clientv3.Config {
		Endpoints: []string{"localhost:2379", "localhost:22379", "localhost:32379"},
		DialTimeout: 5 * time.Second,
	}

	if client, err = clientv3.New(config); err != nil {
		panic(err)
	}
	defer client.Close()

	//resp, err := client.Cluster.MemberList(ctx)
	//if err != nil {
	//	panic(err)
	//}

	kvClient := clientv3.NewKV(client) 
	kvClient.Put(ctx, "k1", "v1")
	getResp, err := kvClient.Get(ctx, "k1")
	if err != nil {
		panic(err)
	}

	fmt.Printf("header:%v\n", getResp.Header)
	fmt.Printf("len: %d, items: %v\n", len(getResp.Kvs), getResp.Kvs[0])
}
```
关于 clientv3 如何实现负载均衡，可以参考这篇文章[Etcd client v3 学习笔记](https://www.jianshu.com/p/281b80ae619b)，从文章分析以及代码看来，clientv3 是通过 grpc 提供的接口来实现负载均衡的，具体实现是在 `etcd/clientv3/balancer/balancer.go` 文件中，

```go
// Balancer takes input from gRPC, manages SubConns, and collects and aggregates
// the connectivity states.
//
// It also generates and updates the Picker used by gRPC to pick SubConns for RPCs.
//
// HandleSubConnectionStateChange, HandleResolvedAddrs and Close are guaranteed
// to be called synchronously from the same goroutine.
// There's no guarantee on picker.Pick, it may be called anytime.
type Balancer interface {
	// HandleSubConnStateChange is called by gRPC when the connectivity state
	// of sc has changed.
	// Balancer is expected to aggregate all the state of SubConn and report
	// that back to gRPC.
	// Balancer should also generate and update Pickers when its internal state has
	// been changed by the new state.
	//
	// Deprecated: if V2Balancer is implemented by the Balancer,
	// UpdateSubConnState will be called instead.
	HandleSubConnStateChange(sc SubConn, state connectivity.State)
	// HandleResolvedAddrs is called by gRPC to send updated resolved addresses to
	// balancers.
	// Balancer can create new SubConn or remove SubConn with the addresses.
	// An empty address slice and a non-nil error will be passed if the resolver returns
	// non-nil error to gRPC.
	//
	// Deprecated: if V2Balancer is implemented by the Balancer,
	// UpdateClientConnState will be called instead.
	HandleResolvedAddrs([]resolver.Address, error)
	// Close closes the balancer. The balancer is not required to call
	// ClientConn.RemoveSubConn for its existing SubConns.
	Close()
}
```
gRPC 中实现负载均衡的方式还是比较简单的。gPRC 有两个默认的负载均衡算法：`pick_first` 和 `round_robin`，pick_first 会尝试连接第一个地址，如果能够连接成功，就会将该地址用于所有的RPC，如果失败，则会尝试下一个地址。round_robin 会连接所有地址，并会按顺序每次向后端发送一个 RPC，参考《gRPC与云原生应用开发》。
```go
pickfirstConn, err := grpc.Dial(
    "address",
    grpc.WithBalancerName("pick_first"),
    grpc.WithInsecure(),
)

roundrobinConn, err := grpc.Dial(
    "address",
    grpc.WithBalancerName("round_robin"),
    grpc.WithInsecure(),
)
```


#### K8s 中的 etcd 运维操作
[Operating etcd clusters for Kubernetes](https://kubernetes.io/docs/tasks/administer-cluster/configure-upgrade-etcd/)


#### 将 event 分开存储
这个主要参考[大规模场景下 kubernetes 集群的性能优化](https://zhuanlan.zhihu.com/p/111244925)，有时间要分析一下这篇文章。将 object 分开存储的方式为使用Apiserver 的参数 `--etcd-servers-overrides`，配置方式为：
```s
--etcd-servers-overrides=/events#https://xxx:3379;https://xxx:3379;https://xxx:3379
```
关于 `--etcd-servers-overrides` 参数，官方文档的解释为：
> Per-resource etcd servers overrides, comma separated. The individual override format: group/resource#servers, where servers are URLs, semicolon separated. Note that this applies only to resources compiled into this server binary.

不同资源的配置之间用**逗号**分隔，同一个资源的 servers 之间用**分号**分隔。

