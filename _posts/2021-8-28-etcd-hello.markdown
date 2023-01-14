---
layout:     post
title:      "在本地安装 Etcd 集群进行测试"
date:       2021-08-28 10:10:00
author:     "weak old dog"
header-img-credit: false
tags:
    - Etcd
---

在本地机器上安装一个简单的 etcd 集群，用于测试。

#### 通过 goreman 部署集群
从 [https://github.com/etcd-io/etcd/releases/v3.4.3](https://github.com/etcd-io/etcd/releases/v3.4.3) 下载 etcd 二进制文件，放到 PATH 下面。

下载 [https://github.com/etcd-io/etcd/blob/v3.4.3/Procfile](https://github.com/etcd-io/etcd/blob/v3.4.3/Procfile) 下载 Procfile 文件，这个文件是 goreman 用来启动 etcd 的配置文件。也可以从自己的 etcd 项目中拷贝这个文件。

下载 goreman，命令为(在新版本 Go 中需要使用 `go install github.com/mattn/goreman@latest` 安装)：
```s
go get github.com/mattn/goreman
```
使用 goreman 启动本地 etcd 集群，goreman 默认是以 http 方式启动集群的，不需要安全认证，生产环境是不能这个样子的。
```s
goreman -f Procfile start
```

#### etcdctl 命令行测试
通过 etcdctl 命令进行数据的 put 和 get 操作。
```s
etcdctl put hello world --endpoints http://127.0.0.1:2379
etcdctl get hello --endpoints http://127.0.0.01:2379
```

#### clientv3 测试
通过 clientv3 客户端进行数据的 put 和 get 操作，下面代码是客户端的基本使用。
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
	// 客户端配置
	config = clientv3.Config {
		Endpoints: []string{"localhost:2379", "localhost:22379", "localhost:32379"},
		DialTimeout: 5 * time.Second,
	}
	// 生成一个客户端
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