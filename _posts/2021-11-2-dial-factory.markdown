---
layout:     post
title:      "Rke DialerFactory 实现"
date:       2020-11-27 10:10:00
author:     "weak old dog"
header-img-credit: false
tags:
    - go
---

rke 的代码提供了两个 DialerFactory，其输入为 Host，输出是返回一个 connection，通过这个 DialerFactory，我们可以对不同的服务，建立不同的连接。代码路径在，rke/hosts/dialer.go

```go
type DialerFactory func(h *Host) (func(network, address string) (net.Conn, error), error)

type DialersOptions struct {
	DockerDialerFactory    DialerFactory // docker 的 client
	LocalConnDialerFactory DialerFactory
	K8sWrapTransport       transport.WrapperFunc
}
```

研究下 rancher Dialer Factory 的实现，感觉挺有意思，代码路径为：/Users/decent/go/src/sankuai.com/xy/mcloud-mke/vendor/github.com/rancher/types/config/dialer/dialer.go
```go
package dialer

import "net"

type Dialer func(network, address string) (net.Conn, error)

type Factory interface {
	ClusterDialer(clusterName string) (Dialer, error)
	DockerDialer(clusterName, machineName string) (Dialer, error)
	NodeDialer(clusterName, machineName string) (Dialer, error)
}
```

docker 的 client 提供了 WithDialContext，可以通过一个 Dialer 函数，构造一个 Docker client。参考 [https://pkg.go.dev/github.com/docker/docker/client#Opt](https://pkg.go.dev/github.com/docker/docker/client#Opt)

```go
func WithDialContext(dialContext func(ctx context.Context, network, addr string) (net.Conn, error)) Opt
```

把相关知识点都总结一下

1. ssh tunnel 相关
2. docker golang 相关
3. http Dial 相关
